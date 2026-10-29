"""Batch runs: route each PDF of a folder to its template, read it, keep the results in a file a killed run resumes.

``run_batch(templates, paths, out_dir)`` reads many documents (meant for ~100k client forms) and writes only under
``out_dir``:

    run.json        the templates' ids and names, when the run started, its ``root``, and the seconds spent so far
    results.jsonl   one DocumentResult per line, appended and flushed as each document is done
    reviews.jsonl   one ReviewDecision per line, appended by ``Results.decide``; a run never touches it

**Routing** (:func:`route`). A document goes to the first template whose ``form.fingerprint`` is the document's
fingerprint or its blind fingerprint (field values ignored, as ``extract`` counts a fingerprint match, Ruling R2),
with score 1.0; else to the template with the best :func:`~formcast.extract.layout.layout_score`, when that reaches
:data:`~formcast.extract.layout.LAYOUT_MATCH` (the first of equal scores); else to none. Filled fillable copies
usually route by layout too: answers reach the page text, so their blind fingerprint is not the blank's (R14). A
document no template fits is written with ``matched="none"``, ``template_id=None``, no fields and the error
``"no template fits (closest: NAME, 0.62)"``: the closest template's name and its best score to two places.
:func:`parse_no_template` reads the two back, and :meth:`Results.set_aside` gives them as columns, so export needs
no pattern of its own. A document that cannot be read (missing, encrypted, corrupt, empty) is written with its
``error`` ("cannot open PDF", "encrypted PDF", "cannot read PDF: ...") and the run goes on; a fault of the code or
a template gives "internal error: ..." (R18), also recorded, never raised.

**One open per document.** A worker opens each document once, as a ``DocText`` and a ``PdfFeatures``, routes it on
them and hands both, with the match routing found, to ``read._extract_open``, the body of ``extract``. The PDF is
not opened, fingerprinted or layout-scored a second time; ``extract()`` itself is unchanged.

**Workers.** ``workers=0`` is ``os.cpu_count()``; with ``workers=1`` documents are read in this process (no crash
isolation: a file that crashes MuPDF ends the run). Otherwise a ``ProcessPoolExecutor`` of the platform's default
start method (spawn on macOS) gets the templates once per worker, as JSON through its initializer (plain data, no
pickling of pydantic models), and returns each result already as its JSON line, which this process writes as it is.
At most four documents per worker are in flight, so memory stays flat however long the list. When a worker process
dies (MuPDF crashing on a file), every document then in flight is read again alone, one at a time in a one-worker
pool; the one that kills that worker too is recorded as unreadable with ``"cannot read PDF: the reader crashed"``
and no fields, and the run goes on in a new pool (Ruling R22). So a resume never meets the crashing file again.
A document that hangs still stalls its worker: there is no per-document timeout.

**Document ids.** ``doc_id`` is the file's path relative to ``root``, with ``/`` separators (``"sub/a.pdf"``);
``root`` defaults to the common parent of the paths, is stored in ``run.json``, and a resumed run keeps it (a
different ``root``, or a path outside it, is refused). ``file`` is the resolved absolute path. A path given twice
is read once.

**Resuming.** On start the run loads ``results.jsonl`` and skips every document already there, read or set aside
(a run is not re-entered with a second template; a set-aside document stays set aside). The last line is torn when
it lacks its newline or does not parse (the run was killed while writing it): the file is cut back to the end of the
last whole line before anything is appended, since appending after the partial line would glue the next record to
it, and that document is read again. So no document is ever in the file twice. A bad line before good ones is not a
torn write, and is refused. ``out_dir`` must not be used by two runs at once. A resume with other templates than
``run.json`` names is refused: use a new ``out_dir`` (with just the set-aside documents, as the spec says).

**Results** (:class:`Results`) over a run's ``out_dir``: ``load`` indexes ``results.jsonl`` (the offset of each
document's line, the last line for a ``doc_id`` winning) without keeping the results in memory, ignores a torn last
line, and never writes (a run may be appending). ``documents()`` parses them one at a time in ``doc_id`` order, so
the order is the same however many workers wrote them. Review decisions are kept apart in ``reviews.jsonl`` and
apply by ``doc_id`` and path, the latest one per path winning, so a rerun keeps them. ``rows()`` and
``status_rows()`` flatten paths to columns as the Global Constraints say (:func:`column_name`: ``owners[0].name``
-> ``owners_1_name``; ``investor.tax_id`` stays), after ``doc_id``, ``file``, ``template``, ``matched`` and
``extracted_at``; failed documents are never rows. :class:`RunSummary` counts the whole ``out_dir``: ``complete``
(read, no problem left), ``with_problems`` (``missing``, ``failed_check`` or ``unsure`` with no decision),
``no_template`` and ``unreadable`` (every other error, internal errors included), which add up to ``total``.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from formcast.errors import FormcastError
from formcast.extract.layout import LAYOUT_MATCH, layout_score
from formcast.extract.model import _HAS_VALUE, DocumentResult, ExtractionTemplate, FieldResult, Status
from formcast.extract.page import DocText
from formcast.extract.read import _extract_open, _failed, error_text, now
from formcast.match import PdfFeatures

RUN = "run.json"
RESULTS = "results.jsonl"
REVIEWS = "reviews.jsonl"
#: The statuses review settles.
PROBLEMS: tuple[Status, ...] = ("missing", "failed_check", "unsure")
#: The start of the error of a document no template fits.
NO_TEMPLATE = "no template fits"
_NO_TEMPLATE = re.compile(r"no template fits \(closest: (.*), (\d+\.\d+)\)", re.DOTALL)
#: The error of a document whose reading killed the worker process, read alone as well (Ruling R22).
CRASHED = "cannot read PDF: the reader crashed"
#: Documents handed to each worker ahead of the one it is reading.
_AHEAD = 4
_ITEM = re.compile(r"\[(\d+)\](\.)?")
_DOC_COLUMNS = ("doc_id", "file", "template", "matched", "extracted_at")


class RunSummary(BaseModel):
    """How a run's documents ended; ``complete + with_problems + no_template + unreadable == total``."""

    total: int
    complete: int
    with_problems: int
    no_template: int
    unreadable: int
    seconds: float


class ReviewDecision(BaseModel):
    """A person's answer to one problem: the value typed, the read accepted, or the field not in this document."""

    doc_id: str
    path: str
    decision: Literal["value", "accept", "not_in_document"]
    value: Any = None
    at: str = Field(default_factory=now)

    @model_validator(mode="after")
    def _value_with_value(self) -> ReviewDecision:
        if self.decision == "value" and self.value is None:
            raise ValueError("a 'value' decision needs a value")
        return self


# ------------------------------------------------------------------ routing


def route(
    templates: list[ExtractionTemplate], pdf: bytes | str | Path
) -> tuple[ExtractionTemplate | None, ExtractionTemplate | None, float]:
    """The template to read ``pdf`` with (``None`` when none fits), the closest template and its score; see the module.

    Raises:
        FormcastError: "cannot open PDF" or "encrypted PDF" when the PDF cannot be opened.
    """
    try:
        data = bytes(pdf) if isinstance(pdf, (bytes, bytearray, memoryview)) else Path(pdf).read_bytes()
    except OSError as exc:
        raise FormcastError("cannot open PDF") from exc
    with DocText(data) as doc:
        use, closest, score, _ = _route(templates, doc, PdfFeatures(data))
    return use, closest, score


def _route(
    templates: list[ExtractionTemplate], doc: DocText, features: PdfFeatures
) -> tuple[ExtractionTemplate | None, ExtractionTemplate | None, float, str]:
    """:func:`route` on an open document, with how it matched (``"fingerprint"``, ``"layout"`` or ``"none"``)."""
    for template in templates:
        if features.matches_fingerprint(template.form.fingerprint):
            return template, template, 1.0, "fingerprint"
    best: ExtractionTemplate | None = None
    score = 0.0
    for template in templates:
        s = layout_score(template.layout, doc)
        if best is None or s > score:
            best, score = template, s
    if best is not None and score >= LAYOUT_MATCH:
        return best, best, score, "layout"
    return None, best, score, "none"


def no_template_reason(closest: ExtractionTemplate | None, score: float) -> str:
    """``"no template fits (closest: NAME, 0.62)"``; just ``"no template fits"`` when there was no template at all."""
    return f"{NO_TEMPLATE} (closest: {closest.name}, {score:.2f})" if closest else NO_TEMPLATE


def parse_no_template(error: str | None) -> tuple[str, float] | None:
    """The closest template's name and score from a no-template error; ``None`` for any other error."""
    m = _NO_TEMPLATE.fullmatch(error or "")
    return (m.group(1), float(m.group(2))) if m else None


# ------------------------------------------------------------- reading one PDF


def _read_one(templates: list[ExtractionTemplate], file: str, doc_id: str) -> DocumentResult:
    """Route and read one file, opening it once. Never raises for a bad document."""
    at = now()
    try:
        data = Path(file).read_bytes()
    except OSError:
        return _failed(None, "cannot open PDF", b"", doc_id, file, at)
    try:
        doc = DocText(data)
    except FormcastError as exc:
        return _failed(None, str(exc), data, doc_id, file, at)
    try:
        features = PdfFeatures(data)
        try:
            use, closest, score, matched = _route(templates, doc, features)
        except Exception as exc:  # MuPDF failing on a page, or a fault of the code
            return _failed(None, error_text(exc), data, doc_id, file, at)
        if use is None:
            return _failed(None, no_template_reason(closest, score), data, doc_id, file, at)
        return _extract_open(use, doc, features, data, matched=matched, doc_id=doc_id, file=file, at=at)
    finally:
        doc.close()


def _line(templates: list[ExtractionTemplate], file: str, doc_id: str) -> str:
    """One document's result as its line of ``results.jsonl`` (without the newline); always a line, never raises."""
    try:
        return _read_one(templates, file, doc_id).model_dump_json()
    except Exception as exc:  # the document is recorded whatever broke
        return _failed(None, f"internal error: {type(exc).__name__}: {exc}", b"", doc_id, file, now()).model_dump_json()


_TEMPLATES: list[ExtractionTemplate] = []  # a worker process's templates, set once by _start_worker


def _start_worker(templates_json: str) -> None:
    global _TEMPLATES
    _TEMPLATES = [ExtractionTemplate.model_validate(t) for t in json.loads(templates_json)]


def _work(file: str, doc_id: str) -> str:
    return _line(_TEMPLATES, file, doc_id)


def _pool(workers: int, payload: str) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=workers, initializer=_start_worker, initargs=(payload,))


def _read_all(templates: list[ExtractionTemplate], todo: list[tuple[str, str]], workers: int) -> Iterator[str]:
    """Each ``(doc_id, file)`` of ``todo`` read, as result lines, in the order they finish.

    When a worker process dies, every document in flight is read again on its own (:func:`_read_alone`) and the
    rest of ``todo`` goes on in a new pool.
    """
    workers = workers or os.cpu_count() or 1
    if workers == 1:
        for doc_id, file in todo:
            yield _line(templates, file, doc_id)
        return
    payload = json.dumps([t.model_dump(mode="json", by_alias=True) for t in templates])
    queue = deque(todo)
    while queue:  # one pool until the queue is done or the pool breaks
        count = min(workers, len(queue))
        pool = _pool(count, payload)
        pending: dict[Future[str], tuple[str, str]] = {}  # future -> (doc_id, file)
        broken = False
        try:
            while not broken:
                while len(pending) < count * _AHEAD and queue:
                    item = queue.popleft()
                    pending[pool.submit(_work, item[1], item[0])] = item
                if not pending:
                    return
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    if isinstance(future.exception(), BrokenProcessPool):
                        broken = True  # kept in pending: one of these documents killed its worker
                        continue
                    del pending[future]
                    yield future.result()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        # every document still pending was in flight when the pool broke (a finished one was yielded above)
        yield from _read_alone(payload, list(pending.values()))


def _read_alone(payload: str, suspects: list[tuple[str, str]]) -> Iterator[str]:
    """Each suspect read alone in a one-worker pool; one that kills that worker too is recorded as crashed.

    A new one-worker pool is started after each crash, so every suspect is read on its own by a live worker.
    """
    pool: ProcessPoolExecutor | None = None
    try:
        for doc_id, file in suspects:
            pool = pool or _pool(1, payload)
            try:
                line = pool.submit(_work, file, doc_id).result()
            except BrokenProcessPool:
                pool.shutdown(wait=True)
                pool = None
                line = _failed(None, CRASHED, b"", doc_id, file, now()).model_dump_json()
            yield line
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


# ------------------------------------------------------------------- the run


def run_batch(
    templates: list[ExtractionTemplate],
    paths: Iterable[str | Path],
    out_dir: str | Path,
    *,
    root: str | Path | None = None,
    workers: int = 0,
    on_progress: Callable[[int, int], None] | None = None,
) -> RunSummary:
    """Route and read every PDF of ``paths`` into ``out_dir``, skipping the ones already there (see the module).

    ``on_progress(done, total)`` is called after each document read; ``done`` counts the documents of ``paths``
    already in the results from an earlier run. Returns the summary of the whole ``out_dir``, with ``seconds`` this
    call's.

    Raises:
        FormcastError: no templates; ``out_dir`` holds a run with other templates or another root; a path outside
            the run's root; a line of ``results.jsonl`` before its last that is not a record.
    """
    started = time.perf_counter()
    if not templates:
        raise FormcastError("a batch run needs at least one template")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = [Path(p).resolve() for p in paths]
    base = _start_run(out, templates, root, files)

    docs: dict[str, str] = {}  # doc_id -> file, in the order given, each once
    for f in files:
        try:
            docs.setdefault(f.relative_to(base).as_posix(), str(f))
        except ValueError:
            raise FormcastError(f"{f} is not under the run's root {base}") from None
    done: set[str] = set()
    end = _whole_lines(out / RESULTS, lambda offset, length, record: done.add(record["doc_id"]))
    _cut(out / RESULTS, end)
    todo = [(doc_id, file) for doc_id, file in docs.items() if doc_id not in done]

    count = len(docs) - len(todo)
    try:
        with (out / RESULTS).open("a", encoding="utf-8") as sink:
            for line in _read_all(templates, todo, workers):
                sink.write(line + "\n")
                sink.flush()
                count += 1
                if on_progress is not None:
                    on_progress(count, len(docs))
    finally:
        _add_seconds(out, time.perf_counter() - started)
    summary = Results.load(out).summary()
    return summary.model_copy(update={"seconds": time.perf_counter() - started})


def _start_run(out: Path, templates: list[ExtractionTemplate], root: str | Path | None, files: list[Path]) -> Path:
    """Write ``run.json`` for a new run, or check a resumed one against it; the run's root."""
    names = [{"template_id": t.template_id, "name": t.name} for t in templates]
    given = Path(root).resolve() if root is not None else None
    if (out / RUN).is_file():
        run = json.loads((out / RUN).read_text(encoding="utf-8"))
        if [t["template_id"] for t in run["templates"]] != [t["template_id"] for t in names]:
            ids = ", ".join(t["template_id"] for t in run["templates"])
            raise FormcastError(f"{out} holds a run with other templates ({ids}); use a new out_dir")
        if given is not None and str(given) != run["root"]:
            raise FormcastError(f"{out} holds a run whose root is {run['root']}, not {given}")
        return Path(run["root"])
    if given is None:
        given = Path(os.path.commonpath([f.parent for f in files])) if files else Path.cwd().resolve()
    run = {"templates": names, "started": now(), "root": str(given), "seconds": 0.0}
    _write_json(out / RUN, run)
    return given


def _add_seconds(out: Path, seconds: float) -> None:
    run = json.loads((out / RUN).read_text(encoding="utf-8"))
    run["seconds"] = run.get("seconds", 0.0) + seconds
    _write_json(out / RUN, run)


def _write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` whole or not at all (a killed run never leaves half a ``run.json``)."""
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(part, path)


def _whole_lines(path: Path, keep: Callable[[int, int, dict], None]) -> int:
    """Call ``keep(offset, length, record)`` for each whole line of a JSON-lines file; where the whole lines end.

    The last line is torn when it has no newline or does not parse as a record: it is left out, and the returned
    offset is where it starts. A missing file has no lines.

    Raises:
        FormcastError: a line that is not a record with whole lines after it.
    """
    if not path.is_file():
        return 0
    offset = end = 0
    bad: int | None = None  # the number of a line that is not a record, allowed only as the last one
    with path.open("rb") as f:
        for number, line in enumerate(f, start=1):
            if bad is not None:
                raise FormcastError(f"{path}: line {bad} is not a JSON record")
            start, offset = offset, offset + len(line)
            if not line.endswith(b"\n"):
                break  # cut off mid-write: the last line
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if not isinstance(record, dict):
                bad = number
                continue
            keep(start, len(line), record)
            end = offset
    return end


def _cut(path: Path, end: int) -> None:
    """Drop a torn last line, so the next line appended starts on a line of its own."""
    if path.is_file() and path.stat().st_size > end:
        os.truncate(path, end)


# ------------------------------------------------------------------ results


def column_name(path: str) -> str:
    """The export column of a path: items numbered from 1 and joined with ``_`` (``owners[0].name`` ->
    ``owners_1_name``), every other path as it is (``investor.tax_id``)."""
    return _ITEM.sub(lambda m: f"_{int(m.group(1)) + 1}" + ("_" if m.group(2) else ""), path)


def _decided(field: FieldResult, decision: ReviewDecision) -> FieldResult:
    if decision.decision == "value":
        update: dict[str, Any] = {"status": "found", "value": decision.value, "strategy": "review", "reasons": []}
    elif decision.decision == "accept":
        update = {"status": "found", "reasons": []}
    else:
        update = {"status": "absent", "value": None, "reasons": []}
    return field.model_copy(update=update)


class Results:
    """A run's results and review decisions, read from its ``out_dir`` (see the module). Make one with :meth:`load`."""

    def __init__(self, out_dir: Path, run: dict, index: dict[str, tuple[int, int]]) -> None:
        self.out_dir = out_dir
        self.run = run  # run.json: templates, started, root, seconds
        self._index = index  # doc_id -> (offset, length) of its line in results.jsonl
        self._decisions: dict[str, dict[str, ReviewDecision]] = {}  # doc_id -> path -> the latest decision
        _whole_lines(out_dir / REVIEWS, lambda offset, length, rec: self._remember(ReviewDecision.model_validate(rec)))

    @classmethod
    def load(cls, out_dir: str | Path) -> Results:
        """The run in ``out_dir``; a torn last line of its files is ignored.

        Raises:
            FormcastError: ``out_dir`` has no ``run.json``, or a line before the last of a file is not a record.
        """
        out = Path(out_dir)
        if not (out / RUN).is_file():
            raise FormcastError(f"no run in {out}: it has no {RUN}")
        run = json.loads((out / RUN).read_text(encoding="utf-8"))
        index: dict[str, tuple[int, int]] = {}

        def keep(offset: int, length: int, record: dict) -> None:
            index[record["doc_id"]] = (offset, length)

        _whole_lines(out / RESULTS, keep)
        return cls(out, run, index)

    def _remember(self, decision: ReviewDecision) -> None:
        self._decisions.setdefault(decision.doc_id, {})[decision.path] = decision

    def _document(self, doc_id: str) -> DocumentResult:
        offset, length = self._index[doc_id]
        with (self.out_dir / RESULTS).open("rb") as f:
            f.seek(offset)
            return DocumentResult.model_validate_json(f.read(length))

    def documents(self) -> Iterator[DocumentResult]:
        """Every document of the run as it was read (decisions not applied), in ``doc_id`` order."""
        if not self._index:
            return
        with (self.out_dir / RESULTS).open("rb") as f:
            for doc_id in sorted(self._index):
                offset, length = self._index[doc_id]
                f.seek(offset)
                yield DocumentResult.model_validate_json(f.read(length))

    def effective(self, doc: DocumentResult) -> DocumentResult:
        """``doc`` with the latest decision on each of its paths applied: a typed value is ``found`` by ``"review"``,
        an accepted read ``found``, a field not in the document ``absent``."""
        decided = self._decisions.get(doc.doc_id)
        if not decided:
            return doc
        fields = [_decided(f, decided[f.path]) if f.path in decided else f for f in doc.fields]
        return doc.model_copy(update={"fields": fields})

    def problems(
        self, *, path: str | None = None, status: Status | None = None
    ) -> Iterator[tuple[DocumentResult, FieldResult]]:
        """Each field still ``missing``, ``failed_check`` or ``unsure`` (no decision on it), with its document.

        Raises:
            FormcastError: ``status`` is not one of those three.
        """
        if status is not None and status not in PROBLEMS:
            raise FormcastError(f"{status!r} is not a problem status (one of {', '.join(PROBLEMS)})")
        for doc in self.documents():
            doc = self.effective(doc)
            for f in doc.fields:
                if f.status in PROBLEMS and (path is None or f.path == path) and (status is None or f.status == status):
                    yield doc, f

    def decide(self, decision: ReviewDecision) -> None:
        """Record ``decision`` in ``reviews.jsonl``; it applies at once and in every later load.

        Raises:
            FormcastError: no such document in the run, no such field in it, or an ``accept`` of a field that has
                no value read.
        """
        if decision.doc_id not in self._index:
            raise FormcastError(f"no document {decision.doc_id!r} in this run")
        field = next((f for f in self._document(decision.doc_id).fields if f.path == decision.path), None)
        if field is None:
            raise FormcastError(f"{decision.doc_id}: no field {decision.path!r}")
        if decision.decision == "accept" and field.value is None:
            raise FormcastError(f"{decision.doc_id}: {decision.path}: nothing to accept, no value was read")
        reviews = self.out_dir / REVIEWS
        _cut(reviews, _whole_lines(reviews, lambda offset, length, record: None))
        with reviews.open("a", encoding="utf-8") as sink:
            sink.write(decision.model_dump_json() + "\n")
            sink.flush()
        self._remember(decision)

    def summary(self) -> RunSummary:
        """The counts of the run, with decisions applied; ``seconds`` is the time its runs have spent so far."""
        counts = {"complete": 0, "with_problems": 0, "no_template": 0, "unreadable": 0}
        for doc in self.documents():
            if doc.error is not None:
                counts["no_template" if doc.error.startswith(NO_TEMPLATE) else "unreadable"] += 1
            elif any(f.status in PROBLEMS for f in self.effective(doc).fields):
                counts["with_problems"] += 1
            else:
                counts["complete"] += 1
        return RunSummary(total=sum(counts.values()), seconds=self.run.get("seconds", 0.0), **counts)

    def _rows(self, cell: Callable[[FieldResult], Any]) -> Iterator[dict]:
        for doc in self.documents():
            if doc.error is not None:
                continue  # a failed document is never a row
            doc = self.effective(doc)
            row: dict[str, Any] = dict(
                zip(_DOC_COLUMNS, (doc.doc_id, doc.file, doc.template_id, doc.matched, doc.extracted_at))
            )
            for f in doc.fields:
                row[column_name(f.path)] = cell(f)
            yield row

    def rows(self) -> Iterator[dict]:
        """One row per document read: the document columns, then each field's value with decisions applied."""
        return self._rows(lambda f: f.value if f.status in _HAS_VALUE else None)

    def status_rows(self) -> Iterator[dict]:
        """The same rows and columns as :meth:`rows`, each field's cell holding its status."""
        return self._rows(lambda f: f.status)

    def set_aside(self) -> Iterator[dict]:
        """One row per failed document: ``doc_id``, ``file``, ``reason`` (``"no template fits"`` or the error),
        ``closest_template`` and ``closest_score`` (``None`` for an unreadable document)."""
        for doc in self.documents():
            if doc.error is None:
                continue
            closest = parse_no_template(doc.error)
            reason = NO_TEMPLATE if doc.error.startswith(NO_TEMPLATE) else doc.error
            yield {
                "doc_id": doc.doc_id, "file": doc.file, "reason": reason,
                "closest_template": closest[0] if closest else None, "closest_score": closest[1] if closest else None,
            }  # fmt: skip
