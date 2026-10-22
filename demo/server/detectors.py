"""The field detectors, loaded once per server, before a document needs them.

The library's stock detectors (``load_default_detectors``) are lazy: each loads its weights on its
first ``detect`` call, and on a fresh clone that call downloads them from Hugging Face, which can take
minutes. A form-map build runs under ``PYMUPDF_LOCK``, so if a build made that first call, page images,
matches and fills for every document would wait for the download. ``DetectorWarmup`` makes that first
call instead, on its own thread when the server starts (``python -m demo.server``), holding no lock: it
runs each detector on a small blank image and touches no MuPDF. A build that needs detection waits for
the warm-up before it takes ``PYMUPDF_LOCK`` and then uses the same detector objects, loaded by then.

One warm-up at a time: callers that arrive while one runs share it. A failed warm-up is logged and
not kept: the builds waiting on it fail with its error, and the next build that needs detection tries
again (still outside the lock).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

from PIL import Image

from formcast.pagemap.detect import Detector, load_default_detectors

log = logging.getLogger("demo.server")

WARMUP_IMAGE_SIZE = (64, 64)  # pixels; enough to make each detector load and run once


class DetectorWarmup:
    """Loads ``load()``'s detectors once, off the MuPDF lock. ``detectors`` is the dict builds pass on."""

    def __init__(self, load: Callable[[], dict[str, Detector]] = load_default_detectors) -> None:
        self.detectors = load()  # cheap: the stock detectors load their weights on the first detect
        self._run: Future[None] | None = None  # the warm-up running or done; None before one, or after one failed
        self._lock = threading.Lock()

    def start(self) -> None:
        """Warm the detectors up on a background thread, unless a warm-up is running or done."""
        future, mine = self._claim()
        if mine:
            threading.Thread(target=self._warm, args=(future,), name="detector-warmup", daemon=True).start()

    def ready(self) -> dict[str, Detector]:
        """``detectors``, loaded: waits for the warm-up under way, or runs one here if none is.

        Raises the error of a failed warm-up; the next call tries again.
        """
        future, mine = self._claim()
        if mine:
            self._warm(future)
        future.result()
        return self.detectors

    def _claim(self) -> tuple[Future[None], bool]:
        """The warm-up under way or done, and False; or a new one for the caller to run, and True."""
        with self._lock:
            if self._run is not None:
                return self._run, False
            self._run = Future()
            return self._run, True

    def _warm(self, future: Future[None]) -> None:
        names = ", ".join(self.detectors) or "none"
        log.info("Loading the field detectors (%s). The first run downloads their weights from Hugging Face, which can take a few minutes.", names)
        started = time.monotonic()
        try:
            blank = Image.new("RGB", WARMUP_IMAGE_SIZE, "white")
            for name, detector in self.detectors.items():
                log.info("Loading detector %s...", name)
                detector.detect(blank)
        except BaseException as exc:  # handed to whoever waits; dropped first, so the next caller tries again
            with self._lock:
                if self._run is future:
                    self._run = None
            log.warning(
                "Could not load the field detectors: %s: %s. PDFs without form fields cannot be read until they "
                "load; the server tries again when one is opened.", type(exc).__name__, exc,
            )
            future.set_exception(exc)
        else:
            log.info("Field detectors ready (%.1f s).", time.monotonic() - started)
            future.set_result(None)
