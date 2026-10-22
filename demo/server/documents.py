"""PDFs the workbench has open: uploaded or picked, held in memory by id.

Each ``Doc`` keeps the PDF bytes, a light summary (page count, page sizes, page kinds) taken when
it is added, its form map, its match features (``features()``, computed on first use and kept) and
the page images rendered so far. Nothing is written to disk. ``Documents`` keeps the 20 most recently used and
drops the rest; a request that holds a ``Doc`` keeps it alive after eviction.

The form map. Building it can take a while: seconds of detection and OCR, and on the very first run a
download of the detector weights from Hugging Face. So ``Documents.add`` starts the build on a
background thread (``Doc.start_form_map``) as soon as the document is open, while the person is still
looking at the match prompt, and ``form_map()`` (seed, apply with a document, AI jobs, rebind) waits for
that build instead of starting its own. It is built once and kept. A failed build is not kept: the
first ``form_map()`` call after it raises its error, and the next call builds again.

Threads: ``Documents`` has one lock for its table; each ``Doc`` has its own locks for the form
map and for filling its image cache. MuPDF is not safe to call from several threads at once
(PyMuPDF runs it single-threaded), so all MuPDF work in the server happens under the
process-wide ``PYMUPDF_LOCK``: opening, rendering, form-map builds, feature computation, and (in
``api.py``) fills, smart PDFs and rebinds. Every render also opens its own PyMuPDF document from
the bytes. Lock order: a ``Doc``'s own lock first, ``PYMUPDF_LOCK`` second; code that holds
``PYMUPDF_LOCK`` must not call ``form_map()`` or ``features()``.

What blocks: a form-map build holds ``PYMUPDF_LOCK`` from start to end (the detector download included,
since the library loads the weights inside the build). While a document's first build runs, every other
MuPDF step in the server waits for it: page images, matches, fills, smart PDFs and rebinds, for any
document. Requests that only read memory (capabilities, templates, records, job status) do not wait.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future

import pymupdf

from formcast.match import PdfFeatures, pdf_features
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
                self.rotations: list[int] = [int(p.rotation) for p in pdf]  # /Rotate, as page_png shows it
                # The same rule build_form_map applies, without detecting or reading any text.
                self.kinds: list[str] = [
                    "fillable" if p.first_widget else ("scanned" if is_image_dominated(p) else "digital") for p in pdf
                ]
            finally:
                pdf.close()
        self.id: str = uuid.uuid4().hex
        self.name = name
        self.bytes = pdf_bytes
        self._map: Future[FormMap] | None = None  # the build in progress or done; None before one starts
        self._map_lock = threading.Lock()
        self._features: PdfFeatures | None = None
        self._features_lock = threading.Lock()
        self._images: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._image_lock = threading.Lock()

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "page_count": self.page_count,
            "page_sizes": [list(s) for s in self.page_sizes],
            "rotations": self.rotations,
            "kinds": self.kinds,
        }

    def start_form_map(self) -> None:
        """Start building the form map on a background thread, unless a build is running or done."""
        future, mine = self._claim_build()
        if mine:
            builder = build_form_map  # looked up now, by the caller, not later on the thread
            threading.Thread(target=self._build, args=(future, builder), name=f"form-map-{self.id[:8]}", daemon=True).start()

    def form_map(self) -> FormMap:
        """The form map: waits for the build under way, or builds it here if none was started; kept once built.

        Concurrent calls share one build. A failed build is not kept: the calls that come for it raise
        its error, and the next call builds again.
        """
        future, mine = self._claim_build()
        if mine:
            self._build(future, build_form_map)
        try:
            return future.result()
        except BaseException:
            with self._map_lock:
                if self._map is future:
                    self._map = None
            raise

    def _claim_build(self) -> tuple[Future[FormMap], bool]:
        """The build under way or done, and False; or a new one for the caller to run, and True."""
        with self._map_lock:
            if self._map is not None:
                return self._map, False
            self._map = Future()
            return self._map, True

    def _build(self, future: Future[FormMap], builder) -> None:
        try:
            with PYMUPDF_LOCK:
                result = builder(self.bytes)
        except BaseException as exc:  # handed to whoever waits; a background thread has nobody else to tell
            future.set_exception(exc)
        else:
            future.set_result(result)

    def features(self) -> PdfFeatures:
        """What ``formcast.similarity`` reads from this PDF, kept: each part is computed on first use and then reused.

        A request that scores this PDF against many templates passes the result to each call. Parts
        compute MuPDF work lazily, so use them under ``PYMUPDF_LOCK``.
        """
        with self._features_lock:
            if self._features is None:
                with PYMUPDF_LOCK:
                    self._features = pdf_features(self.bytes)
            return self._features

    def has_fingerprint(self, expected: str | None) -> bool:
        """Whether a template with this fingerprint was made for this PDF (a filled copy of it counts, as in ``fill``).

        Computes only the fingerprint(s), not the signature or text.
        """
        if expected is None:
            return False
        features = self.features()
        with PYMUPDF_LOCK:
            return features.matches_fingerprint(expected)

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
        """Open ``pdf_bytes``, keep it and start building its form map. Raises ``FormcastError`` for a PDF that cannot be used."""
        doc = Doc(name, pdf_bytes)  # validated outside the lock: opening can take a moment
        with self._lock:
            self._docs[doc.id] = doc
            while len(self._docs) > self.limit:
                self._docs.popitem(last=False)
        doc.start_form_map()  # in the background, so seed and apply find it built or under way
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
