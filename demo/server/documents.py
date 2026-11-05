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
holding no lock, until the detectors its pages need are loaded (FFDNet for digital pages, FFDetr for
scans); a fully fillable PDF does not wait. (3) It builds the form map with the warm-up's detectors. The
library takes ``PYMUPDF_LOCK`` for each MuPDF step of the build and lets it go while a detector or
Tesseract runs (``formcast.pagemap.build``). Without a warm-up (``Documents()``, as in the tests) the
library loads its stock detectors inside the build, outside the lock too.

Threads: ``Documents`` has one lock for its table; each ``Doc`` has its own locks for the form
map and for filling its image cache. MuPDF is not safe to call from several threads at once
(PyMuPDF runs it single-threaded), so all MuPDF work in the server happens under the
process-wide ``PYMUPDF_LOCK``: opening, rendering, form-map builds, feature computation, and (in
``api.py``) fills, smart PDFs and rebinds. ``PYMUPDF_LOCK`` is the library's ``formcast.pagemap.extract.PDF_LOCK``,
which the library itself takes to render the marked page images of an AI job (``jobs.py``), so those renders
and the server's MuPDF work exclude each other. Every render also opens its own PyMuPDF document from
the bytes. Lock order: a ``Doc``'s own lock first, ``PYMUPDF_LOCK`` second; code that holds
``PYMUPDF_LOCK`` must not call ``form_map()``, ``features()`` or ``match_features()``.

What blocks: a form-map build holds ``PYMUPDF_LOCK`` only for its MuPDF steps (a page's text, a render,
tens of milliseconds each), never while a detector runs (about two seconds a page) or Tesseract does. So
while it runs, the other MuPDF steps in the server (page images, fills, smart PDFs and rebinds, for any
document, and opening another PDF) wait at most for one such step: the page shows its PDF while its blanks
are still being found. Matching a document whose features are computed does not wait, nor do requests that
only read memory (capabilities, templates, records, job status).

Progress. ``Doc.progress()`` says how the build is going, for the page to show while it waits: the phase
(``features``, ``detectors`` while the weights load, ``pages``, ``finishing``, then ``done`` or ``error``), the
page being read and its step (``text``, ``ocr``, ``detect`` with the detectors that run), and per page its
state and what it found (boxes, widgets, whether detection was skipped as prose or left to the person).

Detection by hand. A document opened with ``detect="manual"`` builds its form map with no detection at all
(text, widgets and OCR only), which is quick even for a long agreement. ``Doc.detect_page(n)`` then runs
detection on page ``n`` alone and puts its boxes in the form map, so the jobs that read it later see them. It
also takes a page a ``"auto"`` build skipped as prose.

Words. ``page_words`` lists a page's words where they sit, line by line, for the page view's text layer (text a person
can select and copy over the page image). A ``Doc`` takes them from its form map once built (OCR words included),
else reads the page's text itself (``Doc.page_words``); a sample or a run's document passes its own.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future

import pymupdf

from demo.server.detectors import DetectorWarmup
from formcast.errors import FormcastError
from formcast.match import PdfFeatures, pdf_features
from formcast.pagemap.build import NOTE_PROSE, PageProgress, build_form_map, detect_on_page, needs_detection
from formcast.pagemap.detect import DetectPolicy, Detector
from formcast.pagemap.extract import PDF_LOCK, extract_page, is_image_dominated, open_pdf, page_size, render_matrix
from formcast.geometry import Rect, contained_fraction
from formcast.pagemap.model import FormMap, PageMap, TextLine
from formcast.pagemap.ocr import ocr_available, ocr_page

log = logging.getLogger("demo.server")

# One at a time, server-wide. Re-entrant, so code holding it may call other code that takes it. It is the
# library's own MuPDF lock, so the marked page images an AI job renders also wait for (and block) this server's work.
PYMUPDF_LOCK = PDF_LOCK

MAX_DOCUMENTS = 20
MIN_DPI, MAX_DPI = 36, 200
_IMAGES_PER_DOC = 24  # rendered pages kept per document (least recently used dropped)
_WORDS_PER_DOC = 24  # pages of words read before the form map, kept per document
# Every part of PdfFeatures that similarity may read; Doc.match_features computes them all.
_FEATURE_PARTS = ("fingerprint", "fingerprint_blind", "page_sizes", "signature", "phrases")
DETECT_MODES = ("auto", "manual")
_BUILD_ERROR = "The form could not be read. The server log has the details."


def clamp_dpi(dpi: int) -> int:
    return max(MIN_DPI, min(MAX_DPI, int(dpi)))


def page_png(pdf: bytes, n: int, dpi: int) -> bytes:
    """PNG of page ``n`` (0-based) of the PDF ``pdf`` at ``dpi`` (clamped to 36-200, and lowered for a page too large to
    render at it: ``render_dpi``), with the page's /Rotate applied.

    Opens the PDF from its bytes and renders under ``PYMUPDF_LOCK``. For a PDF held as bytes with no ``Doc``
    (a sample of a batch, a document of a run); ``Doc.page_png`` adds its cache.

    Raises:
        IndexError: ``n`` is not a page of the PDF (MuPDF raises it for a page past the last).
    """
    if n < 0:  # MuPDF counts a negative page from the end
        raise IndexError(n)
    with PYMUPDF_LOCK:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            page = doc[n]
            return page.get_pixmap(matrix=render_matrix(page, clamp_dpi(dpi)), alpha=False).tobytes("png")
        finally:
            doc.close()


def page_words(pdf: bytes, n: int, page: PageMap | None = None) -> dict:
    """The words of page ``n`` (0-based) where they sit, line by line in reading order, for the page view's text layer:
    ``{page, width, height, rotation, source, lines: [[{text, rect}]]}``, each rect in unrotated page points (as the
    form map's) and ``source`` how they were read (``pdf``, ``ocr``, ``pdf+ocr``, or ``none`` for a page with no words).

    From ``page``, the page's form map, when given. Otherwise read from the PDF here: its text, or Tesseract's when the
    page is a scan and Tesseract is installed (Tesseract runs outside ``PYMUPDF_LOCK``; ``ocr_page`` locks its render).

    Raises:
        IndexError: ``n`` is not a page of the PDF.
    """
    if page is None:
        if n < 0:  # MuPDF counts a negative page from the end
            raise IndexError(n)
        with PYMUPDF_LOCK:
            doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            with PYMUPDF_LOCK:
                mu = doc[n]
                page = extract_page(mu)
            if page.kind == "scanned" and ocr_available():
                words, lines = ocr_page(mu)
                page = page.model_copy(update={"words": words, "lines": lines, "text_source": "ocr"})
        finally:
            with PYMUPDF_LOCK:
                doc.close()
    lines = [
        [{"text": page.words[i].text, "rect": [round(v, 2) for v in page.words[i].rect]} for i in line.word_ids]
        for line in _reading_order(page)
    ]
    lines = [line for line in lines if line]
    return {
        "page": n,
        "width": page.width,
        "height": page.height,
        "rotation": page.rotation,
        "source": page.text_source if lines else "none",
        "lines": lines,
    }


def _reading_order(page: PageMap) -> list[TextLine]:
    """The page's lines as they read: in the order of the page's own text, with each line written in a form field
    (which MuPDF reads after all of the page's text) put after the last of them that reads before it (a running
    footer the page's text lists first doesn't pull it to the top)."""
    def in_field(line: TextLine) -> bool:
        return any(contained_fraction(line.rect, w.rect) > 0.5 for w in page.widgets)

    out = [line for line in page.lines if not in_field(line)]
    filled = sorted((line for line in page.lines if in_field(line)), key=lambda line: (line.rect[1] + line.rect[3], line.rect[0]))
    for line in filled:
        at = max((k + 1 for k, other in enumerate(out) if _reads_before(other.rect, line.rect)), default=0)
        out.insert(at, line)
    return out


def _reads_before(a: Rect, b: Rect) -> bool:
    """True when the line at ``a`` reads before the line at ``b``: on the same row (they overlap by half the shorter
    one's height) and to its left, or on a row above."""
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    if overlap >= min(a[3] - a[1], b[3] - b[1]) / 2:
        return a[0] < b[0]
    return a[1] + a[3] < b[1] + b[3]


class Doc:
    """One open PDF. Build with ``Documents.add``."""

    def __init__(
        self, name: str, pdf_bytes: bytes, *, detectors: DetectorWarmup | None = None, detect: str = "auto",
        max_pages: int | None = None,
    ) -> None:  # fmt: skip
        if detect not in DETECT_MODES:
            raise ValueError(f"detect must be one of {DETECT_MODES}, not {detect!r}")
        # open_pdf rejects encrypted, corrupt and empty PDFs, and one past max_pages, with FormcastError.
        with PYMUPDF_LOCK:
            pdf = open_pdf(pdf_bytes, max_pages=max_pages)
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
        self.detect = detect
        self._detectors = detectors
        self._progress_lock = threading.Lock()
        self._progress: dict = self._fresh_progress()
        self._detect_lock = threading.Lock()  # one page detection at a time per document
        self._map: Future[FormMap] | None = None  # the build in progress or done; None before one starts
        self._map_lock = threading.Lock()
        self._features: PdfFeatures | None = None
        self._features_lock = threading.Lock()
        self._features_complete = False  # every part computed, so scoring them needs no MuPDF
        self._images: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._image_lock = threading.Lock()
        self._words: OrderedDict[int, dict] = OrderedDict()  # pages of words read before the form map was built
        self._words_lock = threading.Lock()

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "page_count": self.page_count,
            "page_sizes": [list(s) for s in self.page_sizes],
            "rotations": self.rotations,
            "kinds": self.kinds,
            "detect": self.detect,
        }

    # ---- progress

    def _fresh_progress(self) -> dict:
        return {
            "phase": "queued",
            "detect": self.detect,
            "page_count": self.page_count,
            "pages_done": 0,
            "current": None,
            "pages": [{"state": "pending", "kind": kind, "boxes": 0, "widgets": 0, "skipped": None, "ocr": False, "seconds": None}
                      for kind in self.kinds],
            "started": time.time(),
            "ended": None,
            "error": None,
        }  # fmt: skip

    def progress(self) -> dict:
        """How the form-map build is going (see the module docstring); a copy, safe to send."""
        with self._progress_lock:
            out = dict(self._progress)
            out["pages"] = [dict(p) for p in out["pages"]]
            out["current"] = dict(out["current"]) if out["current"] else None
            out["elapsed"] = round((out["ended"] or time.time()) - out["started"], 1)
        out["boxes"] = sum(p["boxes"] for p in out["pages"])
        return out

    def _phase(self, phase: str, error: str | None = None) -> None:
        with self._progress_lock:
            self._progress["phase"] = phase
            if phase in ("done", "error"):
                self._progress["ended"] = time.time()
                self._progress["current"] = None
                self._progress["error"] = error

    def _on_page(self, event: PageProgress) -> None:
        """The library's word on a page (``build_form_map(on_progress=...)``), kept for ``progress()``."""
        with self._progress_lock:
            entry = self._progress["pages"][event.index]
            if event.step == "done":
                self._record_page(entry, event.page)
                entry["seconds"] = round(time.time() - entry.pop("_since", time.time()), 2)
                self._progress["pages_done"] = event.index + 1
                self._progress["current"] = None
                return
            if entry["state"] == "pending":
                entry["state"], entry["_since"] = "working", time.time()
            entry["ocr"] = entry["ocr"] or event.step == "ocr"
            self._progress["current"] = {"page": event.index, "step": event.step, "detectors": list(event.detectors)}

    @staticmethod
    def _record_page(entry: dict, page: PageMap | None) -> None:
        entry["state"] = "done"
        if page is None:
            return
        entry["kind"] = page.kind
        entry["boxes"] = len(page.candidates)
        entry["widgets"] = len(page.widgets)
        entry["skipped"] = ("prose" if NOTE_PROSE in page.notes else "manual") if needs_detection(page) else None

    def _finish_progress(self, form_map: FormMap) -> None:
        """Fill in every page from the finished map (a builder that reported nothing, as in the tests, included)."""
        with self._progress_lock:
            for entry, page in zip(self._progress["pages"], getattr(form_map, "pages", None) or [None] * self.page_count):
                entry.pop("_since", None)
                self._record_page(entry, page)
            self._progress["pages_done"] = self.page_count

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
        with self._progress_lock:
            self._progress = self._fresh_progress()  # a build after a failed one starts over
        try:
            self._phase("features")
            self._prime_features()
            self._phase("detectors")
            detectors = self._detectors_for_build()  # may wait for the weights, holding no lock
            self._phase("pages")
            # The library takes PYMUPDF_LOCK for each MuPDF step, not while it detects. The prose of a long
            # agreement is not searched for blanks (formcast.pagemap.build).
            result = builder(
                self.bytes, detectors=detectors, skip_prose=True, detect=self.detect == "auto", on_progress=self._on_page
            )
            self._finish_progress(result)
        except BaseException as exc:  # handed to whoever waits; a background thread has nobody else to tell
            self._phase("error", str(exc) if isinstance(exc, FormcastError) else _BUILD_ERROR)
            future.set_exception(exc)
        else:
            self._phase("done")
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
        if self.detect == "manual" or all(kind == "fillable" for kind in self.kinds):
            return self._detectors.detectors  # nothing to detect, so no need to wait for the weights
        return self._detectors.ready(self.needed_detectors())

    def needed_detectors(self, kinds: list[str] | None = None) -> list[str]:
        """The detectors pages of these kinds (default: every page's) run: a digital PDF needs no FFDetr."""
        kinds = self.kinds if kinds is None else kinds
        policy = DetectPolicy()
        names = [*(policy.digital if "digital" in kinds else ()), *(policy.scanned if "scanned" in kinds else ())]
        return list(dict.fromkeys(names))

    def detect_page(self, n: int) -> tuple[PageMap, bool]:
        """Run detection on page ``n`` (0-based) alone, once, and keep its boxes in the form map.

        Returns the page's map and whether detection ran now: False for a page already searched (by the
        build, or an earlier call) and for a fillable page.

        Raises:
            IndexError: ``n`` is not a page of this document.
        """
        if not 0 <= n < self.page_count:
            raise IndexError(n)
        form_map = self.form_map()  # waits for the build; takes PYMUPDF_LOCK itself
        with self._detect_lock:
            page = form_map.pages[n]
            if not needs_detection(page):
                return page, False
            with self._progress_lock:
                self._progress["current"] = {"page": n, "step": "detect", "detectors": []}
            detectors = self._detectors.ready(self.needed_detectors([page.kind])) if self._detectors is not None else None
            started = time.time()
            try:
                with PYMUPDF_LOCK:
                    pdf = open_pdf(self.bytes)
                    pdf_page = pdf[n]
                try:
                    page = detect_on_page(pdf_page, page, detectors)  # takes PYMUPDF_LOCK for its MuPDF steps only
                finally:
                    with PYMUPDF_LOCK:
                        pdf.close()
            finally:
                with self._progress_lock:
                    self._progress["current"] = None
            form_map.pages[n] = page  # the jobs that read the form map from now on see these boxes
            with self._progress_lock:
                entry = self._progress["pages"][n]
                self._record_page(entry, page)
                entry["seconds"] = round(time.time() - started, 2)
            return page, True

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
            png = page_png(self.bytes, n, key[1])
            self._images[key] = png
            while len(self._images) > _IMAGES_PER_DOC:
                self._images.popitem(last=False)
            return png


    def page_words(self, n: int) -> dict:
        """The words of page ``n`` (0-based), as ``page_words`` lists them: from the form map once it is built (OCR
        words included); before, read from the page (and kept), so the text is there while the form is still read.

        Raises:
            IndexError: ``n`` is not a page of this document.
        """
        if not 0 <= n < self.page_count:
            raise IndexError(n)
        built = self._map
        if built is not None and built.done() and built.exception() is None:
            return page_words(self.bytes, n, built.result().pages[n])
        with self._words_lock:  # only so that identical concurrent requests read once
            words = self._words.get(n)
            if words is None:
                words = self._words[n] = page_words(self.bytes, n)
                while len(self._words) > _WORDS_PER_DOC:
                    self._words.popitem(last=False)
            return words


class Documents:
    def __init__(self, limit: int = MAX_DOCUMENTS, *, detectors: DetectorWarmup | None = None) -> None:
        """``detectors``: the server's warm-up, whose detectors every build uses; ``None`` leaves them to the library."""
        self.limit = limit
        self.detectors = detectors
        self._docs: OrderedDict[str, Doc] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, pdf_bytes: bytes, name: str, *, detect: str = "auto", max_pages: int | None = None) -> Doc:
        """Open ``pdf_bytes``, keep it and start building its form map. Raises ``FormcastError`` for a PDF that
        cannot be used, or that has more than ``max_pages`` pages. ``detect="manual"`` runs no detection in the build
        (see ``Doc.detect_page``)."""
        doc = Doc(name, pdf_bytes, detectors=self.detectors, detect=detect, max_pages=max_pages)  # validated outside the lock: opening can take a moment
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
