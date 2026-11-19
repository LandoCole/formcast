"""The document queue, held in memory only: nothing is written to disk, so quitting the app forgets it all.

One worker thread converts the documents one at a time, in the order they were added. ``reset`` forgets every
document; the one being converted stops after its current page and its result is thrown away.
"""

from __future__ import annotations

import io
import itertools
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePath

from formcast_lite.convert import Cancelled, ConvertError, PageResult, convert, page_count
from formcast_lite.detector import Detector


@dataclass
class Page:
    state: str = "pending"  # pending, working, boxes, empty, prose, fillable
    fields: int = 0
    seconds: float | None = None


@dataclass
class Doc:
    id: str
    name: str
    size: int
    skip_prose: bool
    data: bytes | None
    status: str = "queued"  # queued, converting, done, failed
    pages: list[Page] = field(default_factory=list)
    error: str | None = None
    output: bytes | None = None
    added: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None

    @property
    def out_name(self) -> str:
        stem = PurePath(self.name).stem or "document"
        return f"{stem}-fillable.pdf"

    def summary(self) -> dict:
        now = time.time()
        return {
            "id": self.id,
            "name": self.name,
            "out_name": self.out_name,
            "size": self.size,
            "skip_prose": self.skip_prose,
            "status": self.status,
            "error": self.error,
            "pages": [{"state": p.state, "fields": p.fields, "seconds": p.seconds} for p in self.pages],
            "fields": sum(p.fields for p in self.pages if p.state == "boxes"),
            "elapsed": round(((self.finished or now) - self.started), 1) if self.started else None,
            "output_size": len(self.output) if self.output else None,
        }


class Queue:
    def __init__(self, detector: Detector):
        self.detector = detector
        self.docs: dict[str, Doc] = {}
        self.pending: deque[str] = deque()
        self.generation = 0
        self.detector_state = "loading"
        self.detector_error: str | None = None
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        threading.Thread(target=self._warm_up, daemon=True, name="detector-load").start()
        threading.Thread(target=self._work, daemon=True, name="converter").start()

    def _warm_up(self) -> None:
        try:
            self.detector.load()
            self.detector_state = "ready"
        except Exception as exc:  # a missing model or a failed download
            self.detector_state = "error"
            self.detector_error = str(exc) or type(exc).__name__

    # ------------------------------------------------------------ API

    def add(self, name: str, data: bytes, skip_prose: bool) -> Doc:
        doc = Doc(id=str(next(self._ids)), name=name or "document.pdf", size=len(data), skip_prose=skip_prose, data=data)
        try:
            doc.pages = [Page() for _ in range(page_count(data))]
        except ConvertError as exc:
            doc.status, doc.error, doc.data = "failed", str(exc), None
        with self._cond:
            self.docs[doc.id] = doc
            if doc.status == "queued":
                self.pending.append(doc.id)
                self._cond.notify()
        return doc

    def reset(self) -> None:
        with self._cond:
            self.generation += 1
            self.docs.clear()
            self.pending.clear()

    def get(self, doc_id: str) -> Doc | None:
        with self._cond:
            return self.docs.get(doc_id)

    def state(self) -> dict:
        with self._cond:
            docs = [d.summary() for d in self.docs.values()]
        return {"detector": self.detector_state, "detector_error": self.detector_error, "docs": docs}

    def zip_done(self) -> bytes | None:
        with self._cond:
            done = [d for d in self.docs.values() if d.status == "done" and d.output]
        if not done:
            return None
        buf = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # PDFs are compressed already
            for d in done:
                name, n = d.out_name, 1
                while name.lower() in used:
                    n += 1
                    name = f"{PurePath(d.out_name).stem} ({n}).pdf"
                used.add(name.lower())
                zf.writestr(name, d.output)
        return buf.getvalue()

    # ------------------------------------------------------------ worker

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self.pending:
                    self._cond.wait()
                doc = self.docs[self.pending.popleft()]
                generation = self.generation
                doc.status, doc.started = "converting", time.time()
                data, doc.data = doc.data, None

            def live() -> bool:
                return self.generation == generation

            def on_page(index: int, result: PageResult | None) -> None:
                with self._cond:
                    if not live():
                        return
                    page = doc.pages[index]
                    if result is None:
                        page.state = "working"
                    else:
                        page.state, page.fields, page.seconds = result.state, result.fields, result.seconds

            try:
                output = convert(data, self.detector, skip_prose=doc.skip_prose, on_page=on_page, cancelled=lambda: not live())
                status, error = "done", None
            except Cancelled:
                continue
            except ConvertError as exc:
                output, status, error = None, "failed", str(exc)
            except Exception as exc:  # keep the worker alive whatever one PDF does
                output, status, error = None, "failed", f"conversion failed: {exc or type(exc).__name__}"
            finally:
                del data
            with self._cond:
                if live():
                    doc.output, doc.status, doc.error, doc.finished = output, status, error, time.time()
