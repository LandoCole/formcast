"""PDFs the workbench has open: uploaded or picked, held in memory by id.

Each ``Doc`` keeps the PDF bytes, a light summary (page count, page sizes, page kinds) taken when
it is added, its form map (built on first use, once, by ``form_map()``) and the page images
rendered so far. Nothing is written to disk. ``Documents`` keeps the 20 most recently used and
drops the rest; a request that holds a ``Doc`` keeps it alive after eviction.

Threads: ``Documents`` has one lock for its table; each ``Doc`` has its own locks for the form
map and for filling its image cache. MuPDF is not safe to call from several threads at once
(PyMuPDF runs it single-threaded), so all MuPDF work in the server happens under the
process-wide ``PYMUPDF_LOCK``: opening, rendering, and (Tasks 6 and 7) fills and form-map
builds. Every render also opens its own PyMuPDF document from the bytes.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

import pymupdf

from formcast.pagemap.build import build_form_map
from formcast.pagemap.extract import is_image_dominated, open_pdf, page_size
from formcast.pagemap.model import FormMap

# One at a time, server-wide. Re-entrant, so code holding it may call other code that takes it.
PYMUPDF_LOCK = threading.RLock()

MAX_DOCUMENTS = 20
MIN_DPI, MAX_DPI = 36, 200
_IMAGES_PER_DOC = 24  # rendered pages kept per document (least recently used dropped)


def clamp_dpi(dpi: int) -> int:
    return max(MIN_DPI, min(MAX_DPI, int(dpi)))


class Doc:
    """One open PDF. Build with ``Documents.add``."""

    def __init__(self, name: str, pdf_bytes: bytes) -> None:
        # open_pdf rejects encrypted, corrupt and empty PDFs with FormcastError.
        with PYMUPDF_LOCK:
            pdf = open_pdf(pdf_bytes)
            try:
                self.page_count: int = pdf.page_count
                self.page_sizes: list[tuple[float, float]] = [page_size(p) for p in pdf]
                # The same rule build_form_map applies, without detecting or reading any text.
                self.kinds: list[str] = [
                    "fillable" if p.first_widget else ("scanned" if is_image_dominated(p) else "digital") for p in pdf
                ]
            finally:
                pdf.close()
        self.id: str = uuid.uuid4().hex
        self.name = name
        self.bytes = pdf_bytes
        self._form_map: FormMap | None = None
        self._map_lock = threading.Lock()
        self._images: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._image_lock = threading.Lock()

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "page_count": self.page_count,
            "page_sizes": [list(s) for s in self.page_sizes],
            "kinds": self.kinds,
        }

    def form_map(self) -> FormMap:
        """The form map, built on the first call (this can take seconds) and kept.

        Concurrent first calls build it once; a failed build is not kept, so the next call tries again.
        """
        with self._map_lock:
            if self._form_map is None:
                self._form_map = build_form_map(self.bytes)
            return self._form_map

    def page_png(self, n: int, dpi: int = 110) -> bytes:
        """PNG of page ``n`` (0-based) at ``dpi`` (clamped to 36-200), with the page's /Rotate applied.

        Raises:
            IndexError: ``n`` is not a page of this document.
        """
        if not 0 <= n < self.page_count:
            raise IndexError(n)
        key = (n, clamp_dpi(dpi))
        with self._image_lock:  # only so that identical concurrent requests render once; MuPDF is locked below
            png = self._images.get(key)
            if png is not None:
                self._images.move_to_end(key)
                return png
            with PYMUPDF_LOCK:
                pdf = pymupdf.open(stream=self.bytes, filetype="pdf")
                try:
                    png = pdf[n].get_pixmap(dpi=key[1], alpha=False).tobytes("png")
                finally:
                    pdf.close()
            self._images[key] = png
            while len(self._images) > _IMAGES_PER_DOC:
                self._images.popitem(last=False)
            return png


class Documents:
    def __init__(self, limit: int = MAX_DOCUMENTS) -> None:
        self.limit = limit
        self._docs: OrderedDict[str, Doc] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, pdf_bytes: bytes, name: str) -> Doc:
        """Open ``pdf_bytes`` and keep it. Raises ``FormcastError`` for a PDF that cannot be used."""
        doc = Doc(name, pdf_bytes)  # validated outside the lock: opening can take a moment
        with self._lock:
            self._docs[doc.id] = doc
            while len(self._docs) > self.limit:
                self._docs.popitem(last=False)
        return doc

    def get(self, doc_id: str) -> Doc:
        """The document, marked as just used. Raises ``KeyError`` if unknown or evicted."""
        with self._lock:
            doc = self._docs[doc_id]
            self._docs.move_to_end(doc_id)
            return doc

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)
