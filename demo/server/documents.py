"""PDFs the workbench has open: uploaded or picked, held in memory by id.

Each ``Doc`` keeps the PDF bytes, a light summary (page count, page sizes, page kinds) taken when
it is added, its form map, its match features (``features()``, each part computed on first use and kept) and
the page images rendered so far. Nothing is written to disk. ``Documents`` keeps the 20 most recently used and
drops the rest; a request that holds a ``Doc`` keeps it alive after eviction.

The form map. Building it takes seconds of detection and OCR. So ``Documents.add`` starts the build on a
background thread (``Doc.start_form_map``) as soon as the document is open, while the person is still
looking at the match prompt, and ``form_map()`` (seed, apply with a document, AI jobs, rebind) waits for
that build instead of starting its own. It is built once and kept. A failed build is not kept: the
first ``form_map()`` call after it raises its error, and the next call builds again.

The build thread does three things in order. (1) It computes every part of the document's match features
(``match_features()``, under ``PYMUPDF_LOCK`` briefly), so the match prompt never waits for detection:
scoring complete features needs no MuPDF and no lock. (2) If a page needs detection and the documents were given
a ``DetectorWarmup`` (``demo/server/detectors.py``; ``python -m demo.server`` starts one), it waits,
holding no lock, until the detector weights are loaded; a fully fillable PDF does not wait. (3) It
builds the form map under ``PYMUPDF_LOCK`` with the warm-up's detectors. Without a warm-up (``Documents()``,
as in the tests) the library loads its stock detectors inside the build, so the first download happens
under the lock.

Threads: ``Documents`` has one lock for its table; each ``Doc`` has its own locks for the form
map and for filling its image cache. MuPDF is not safe to call from several threads at once
(PyMuPDF runs it single-threaded), so all MuPDF work in the server happens under the
process-wide ``PYMUPDF_LOCK``: opening, rendering, form-map builds, feature computation, and (in
``api.py``) fills, smart PDFs and rebinds. ``PYMUPDF_LOCK`` is the library's ``formcast.pagemap.extract.PDF_LOCK``,
which the library itself takes to render the marked page images of an AI job (``jobs.py``), so those renders
and the server's MuPDF work exclude each other. Every render also opens its own PyMuPDF document from
the bytes. Lock order: a ``Doc``'s own lock first, ``PYMUPDF_LOCK`` second; code that holds
``PYMUPDF_LOCK`` must not call ``form_map()``, ``features()`` or ``match_features()``.

What blocks: a form-map build holds ``PYMUPDF_LOCK`` from start to end (seconds of detection and OCR;
with a warm-up, never the weight download). While it runs, every other MuPDF step in the server waits for
it: page images, fills, smart PDFs and rebinds, for any document, and opening another PDF (its summary and
features). Matching a document whose features are computed does not wait, nor do requests that only read
memory (capabilities, templates, records, job status).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future

import pymupdf

from demo.server.detectors import DetectorWarmup
from formcast.match import PdfFeatures, pdf_features
from formcast.pagemap.build import build_form_map
from formcast.pagemap.detect import Detector
from formcast.pagemap.extract import PDF_LOCK, is_image_dominated, open_pdf, page_size
from formcast.pagemap.model import FormMap

log = logging.getLogger("demo.server")

# One at a time, server-wide. Re-entrant, so code holding it may call other code that takes it. It is the
# library's own MuPDF lock, so the marked page images an AI job renders also wait for (and block) this server's work.
PYMUPDF_LOCK = PDF_LOCK

MAX_DOCUMENTS = 20
MIN_DPI, MAX_DPI = 36, 200
_IMAGES_PER_DOC = 24  # rendered pages kept per document (least recently used dropped)
# Every part of PdfFeatures that similarity may read; Doc.match_features computes them all.
_FEATURE_PARTS = ("fingerprint", "fingerprint_blind", "page_sizes", "signature", "phrases")


def clamp_dpi(dpi: int) -> int:
    return max(MIN_DPI, min(MAX_DPI, int(dpi)))


class Doc:
    """One open PDF. Build with ``Documents.add``."""

    def __init__(self, name: str, pdf_bytes: bytes, *, detectors: DetectorWarmup | None = None) -> None:
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
        self._detectors = detectors
        self._map: Future[FormMap] | None = None  # the build in progress or done; None before one starts
        self._map_lock = threading.Lock()
        self._features: PdfFeatures | None = None
        self._features_lock = threading.Lock()
        self._features_complete = False  # every part computed, so scoring them needs no MuPDF
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
            self._prime_features()
            detectors = self._detectors_for_build()  # may wait for the weights, holding no lock
            with PYMUPDF_LOCK:
                # the prose of a long agreement is not searched for blanks (formcast.pagemap.build)
                result = builder(self.bytes, detectors=detectors, skip_prose=True)
        except BaseException as exc:  # handed to whoever waits; a background thread has nobody else to tell
            future.set_exception(exc)
        else:
            future.set_result(result)

    def _prime_features(self) -> None:
        """Compute the match features before the build takes ``PYMUPDF_LOCK``, so matching never waits for it."""
        try:
            self.match_features()
        except Exception:  # not the build's business: the request that needs the features meets the error
            log.debug("could not compute the match features of %s ahead of its form map", self.name, exc_info=True)

    def _detectors_for_build(self) -> dict[str, Detector] | None:
        """The detectors this document's build passes to the library: ``None`` lets the library load its own."""
        if self._detectors is None:
            return None
        if all(kind == "fillable" for kind in self.kinds):
            return self._detectors.detectors  # nothing to detect, so no need to wait for the weights
        return self._detectors.ready()

    def features(self) -> PdfFeatures:
        """What ``formcast.similarity`` reads from this PDF, kept: each part is computed on first use and then reused.

        Parts compute MuPDF work lazily, so use them under ``PYMUPDF_LOCK``, or use ``match_features``.
        """
        with self._features_lock:
            if self._features is None:
                with PYMUPDF_LOCK:
                    self._features = pdf_features(self.bytes)
            return self._features

    def match_features(self) -> PdfFeatures:
        """``features()`` with every part computed (under ``PYMUPDF_LOCK``, once), so using them needs no lock.

        A request that scores this PDF against many templates passes the result to each call, and does
        not wait for a form-map build. The build thread computes them before it builds (``_build``); a
        request that comes sooner computes them itself.
        """
        features = self.features()
        if not self._features_complete:
            with PYMUPDF_LOCK:  # two first calls at once: the second finds every part kept
                for part in _FEATURE_PARTS:
                    getattr(features, part)
            self._features_complete = True
        return features

    def has_fingerprint(self, expected: str | None) -> bool:
        """Whether a template with this fingerprint was made for this PDF (a filled copy of it counts, as in ``fill``).

        Computes only the fingerprint(s), not the signature or text, unless they are computed already.
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
    def __init__(self, limit: int = MAX_DOCUMENTS, *, detectors: DetectorWarmup | None = None) -> None:
        """``detectors``: the server's warm-up, whose detectors every build uses; ``None`` leaves them to the library."""
        self.limit = limit
        self.detectors = detectors
        self._docs: OrderedDict[str, Doc] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, pdf_bytes: bytes, name: str) -> Doc:
        """Open ``pdf_bytes``, keep it and start building its form map. Raises ``FormcastError`` for a PDF that cannot be used."""
        doc = Doc(name, pdf_bytes, detectors=self.detectors)  # validated outside the lock: opening can take a moment
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
