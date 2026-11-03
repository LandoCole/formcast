"""The field detectors, loaded once per server, before a document needs them.

The library's stock detectors (``load_default_detectors``) are lazy: each loads its weights on its
first ``detect`` call, and on a fresh clone that call downloads them from Hugging Face, which can take
minutes. Even with the weights on disk, loading them is the slowest part of opening a PDF: importing
torch and ultralytics and building the two models takes about 10 s on a laptop, and longer on Windows,
where every DLL that loads is scanned. ``DetectorWarmup`` makes that first call instead, on its own
thread when the server starts (``python -m demo.server``) and again whenever the page opens
(``GET /api/capabilities``) if it is not running or done, holding no lock: it runs each detector on a
small blank image and touches no MuPDF. A build that needs detection waits only for the detectors its
pages run (``ready(names)``: FFDNet for digital pages, FFDetr for scans), which load in that order, and
then uses the same detector objects, loaded by then.

One load per detector: callers that arrive while one loads share it (the library's detectors also
guard their own first load, so a ``detect`` that comes first cannot load the model twice). A failed
load is logged and not kept: the callers waiting on it fail with its error, and the next one that
needs that detector tries again (still outside the lock). ``status()`` says how it is going, for the
page's progress pane.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future

from PIL import Image

from formcast.pagemap.detect import Detector, load_default_detectors

log = logging.getLogger("demo.server")

WARMUP_IMAGE_SIZE = (64, 64)  # pixels; enough to make each detector load and run once


class DetectorWarmup:
    """Loads ``load()``'s detectors once, off the MuPDF lock. ``detectors`` is the dict builds pass on."""

    def __init__(self, load: Callable[[], dict[str, Detector]] = load_default_detectors) -> None:
        self.detectors = load()  # cheap: the stock detectors load their weights on the first detect
        # per detector, its load running or done; absent before one, or after one failed
        self._loads: dict[str, Future[None]] = {}
        self._seconds: dict[str, float] = {}  # how long each finished load took
        self._errors: dict[str, str] = {}  # the last failed load's error, until one succeeds
        self._lock = threading.Lock()

    def start(self) -> None:
        """Load every detector on a background thread, unless each is loading or loaded already."""
        _, mine = self._claim(self.detectors)
        if mine:
            threading.Thread(target=self._warm, args=(mine,), name="detector-warmup", daemon=True).start()

    def ready(self, names: Iterable[str] | None = None) -> dict[str, Detector]:
        """``detectors``, with ``names`` (default: all; a name it doesn't know means all) loaded: waits for
        the loads under way, or runs them here if none is.

        Raises the error of a failed load; the next call tries again.
        """
        wanted = [n for n in (names or ()) if n in self.detectors] or list(self.detectors)
        loads, mine = self._claim(wanted)
        if mine:
            self._warm(mine)
        for future in loads.values():
            future.result()
        return self.detectors

    def status(self) -> dict:
        """``{state, detectors: [{name, state, seconds?, error?}]}``; ``state`` is ``idle``, ``loading``, ``ready`` or ``error``."""
        with self._lock:
            rows = []
            for name in self.detectors:
                future = self._loads.get(name)
                if future is not None and future.done():
                    rows.append({"name": name, "state": "ready", "seconds": round(self._seconds.get(name, 0.0), 1)})
                elif future is not None:
                    rows.append({"name": name, "state": "loading"})
                elif name in self._errors:
                    rows.append({"name": name, "state": "error", "error": self._errors[name]})
                else:
                    rows.append({"name": name, "state": "idle"})
        states = {r["state"] for r in rows}
        state = next((s for s in ("loading", "error", "idle") if s in states), "ready")
        return {"state": state, "detectors": rows}

    def _claim(self, names: Iterable[str]) -> tuple[dict[str, Future[None]], dict[str, Future[None]]]:
        """The loads of ``names`` (running, done or new), and the new ones, for the caller to run."""
        loads: dict[str, Future[None]] = {}
        mine: dict[str, Future[None]] = {}
        with self._lock:
            for name in names:
                future = self._loads.get(name)
                if future is None:
                    future = self._loads[name] = mine[name] = Future()
                loads[name] = future
        return loads, mine

    def _warm(self, mine: dict[str, Future[None]]) -> None:
        log.info("Loading the field detectors (%s). The first run downloads their weights from Hugging Face, which can take a few minutes.", ", ".join(mine))
        started = time.monotonic()
        blank = Image.new("RGB", WARMUP_IMAGE_SIZE, "white")
        failed = False
        for name, future in mine.items():  # in the detectors' order: FFDNet, which digital pages need, first
            log.info("Loading detector %s...", name)
            began = time.monotonic()
            try:
                self.detectors[name].detect(blank)
            except BaseException as exc:  # handed to whoever waits; dropped first, so the next caller tries again
                failed = True
                with self._lock:
                    if self._loads.get(name) is future:
                        del self._loads[name]
                    self._errors[name] = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Could not load the field detectors: %s: %s. PDFs without form fields cannot be read until they "
                    "load; the server tries again when one is opened.", type(exc).__name__, exc,
                )  # fmt: skip
                future.set_exception(exc)
            else:
                with self._lock:
                    self._seconds[name] = time.monotonic() - began
                    self._errors.pop(name, None)
                log.info("Detector %s ready (%.1f s).", name, time.monotonic() - began)
                future.set_result(None)
        if not failed:
            log.info("Field detectors ready (%.1f s).", time.monotonic() - started)
