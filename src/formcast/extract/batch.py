"""Batch runs: route each PDF of a folder to its template, read it, keep the results in a file a killed run resumes.

``run_batch(templates, paths, out_dir)`` reads many documents (meant for ~100k client forms) and writes only under
``out_dir``:

    run.json        the templates' ids, names and sha256 (:func:`template_sha256`), when the run started, its
                    ``root``, the seconds spent so far, and ``required``: each template's required concrete paths
                    (:func:`~formcast.extract.levels.required_paths`), sorted, by ``template_id``
    results.jsonl   one DocumentResult per line, appended and flushed as each document is done
    reviews.jsonl   one ReviewDecision per line, appended by ``Results.decide``; a run never touches it
    ai.jsonl        one AiAnswer per line, appended by :func:`~formcast.extract.aipass.ai_pass`; a run never touches it

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

**Workers.** ``workers=0`` is ``os.cpu_count()``; with ``workers=1`` documents are read in this process: no crash
isolation (a file that crashes MuPDF ends the run, and every resume meets it again) and no timeout (a file that
hangs stalls the run). Otherwise a ``ProcessPoolExecutor`` gets the templates once per worker, as JSON through its
initializer (plain data, no pickling of pydantic models), and returns each result already as its JSON line, which this
process writes as it is. Every pool (the run's, and the pool of one a document is read alone in) starts its workers
by spawn on every platform, never by fork: a caller with threads (the workbench server) may hold a lock, MuPDF's
``PDF_LOCK`` among them, while a fork copies it held into the child, where nothing ever releases it.
At most four documents per worker are in flight, so memory stays flat however long the list. When a worker process
dies (MuPDF crashing on a file), every document then in flight is read again alone, one at a time in a one-worker
pool; the one that kills that worker too is recorded as unreadable with ``"cannot read PDF: the reader crashed"``
and no fields, and the run goes on in a new pool (Ruling R22). So a resume never meets the crashing file again.
A document that hangs is caught by ``timeout`` (seconds, 120 by default, Ruling F5): when one has been in flight
that long since it was submitted, the pool's workers are terminated, which breaks the pool, and what was in flight
is read alone as after a crash. Alone, a document has ``timeout`` seconds of its own (the worker is started before
the clock does); one that still takes longer is recorded as unreadable with ``"cannot read PDF: the reader hung"``
and no fields, and the run goes on. A document that waited behind a slow one may pass the timeout in the pool; it
is then simply read again alone.

**Stopping.** ``should_stop()`` is asked before each document is read (in this process) or submitted (to the pool),
and before each re-read alone. Once it is true nothing more is started: the documents already in flight finish and
are written, one whole line each, and the run returns the summary of what is in the file. The documents not started
are simply not there, so a run started again over the same ``out_dir`` reads exactly those: none is written twice.
A document in flight is still caught by ``timeout`` as always, and a document that was in flight when a worker died
but has not yet been re-read alone is left for the resume, not given a crashed or hung record.

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
``run.json`` names is refused: use a new ``out_dir`` (with just the set-aside documents, as the spec says). So is a
resume with a template of the same id whose content changed (its sha256 differs; the error names it): the results
already there were read with the old one. :meth:`Results.check_templates` makes the same check for an export.

**Results** (:class:`Results`) over a run's ``out_dir``: ``load`` indexes ``results.jsonl`` (the offset of each
document's line, the last line for a ``doc_id`` winning) without keeping the results in memory, ignores a torn last
line, and never writes (a run may be appending). ``documents()`` parses them one at a time in ``doc_id`` order, so
the order is the same however many workers wrote them. Review decisions are kept apart in ``reviews.jsonl`` and
apply by ``doc_id`` and path, the latest one per path winning, so a rerun keeps them. So are the AI pass's answers
in ``ai.jsonl`` (read the same way: a torn last line ignored, the latest answer per ``doc_id`` and path winning): an
answer with a value is put on a field that is ``missing`` or ``failed_check`` as ``unsure`` by strategy ``"ai"``,
with the reason :data:`AI_REASON`, before any review decision is applied, so a decision always wins over it, and an
``accept`` is checked against the field with the answer on it, and an ``accept`` of an AI answer is recorded as a
``value`` decision carrying the value the person saw, so it stands whatever ``ai.jsonl`` holds later. An answer
never replaces the read of a field that has a decision. An answer of ``None`` changes nothing: it only records
that the field was asked. An ``accept`` of a field that now has no value (the value it accepted is gone) leaves the
field as read, never ``found`` with no value. ``rows()`` and
``status_rows()`` flatten paths to columns as the Global Constraints say (:func:`column_name`: ``owners[0].name``
-> ``owners_1_name``; ``investor.tax_id`` stays), after ``doc_id``, ``file``, ``template``, ``matched`` and
``extracted_at``; failed documents are never rows.

**Problems and their levels.** A field still ``missing``, ``failed_check`` or ``unsure`` once the AI's answers and
the decisions are applied is a problem (the statuses themselves never change); its level is its field's in the
document's template, ``"required"`` or ``"optional"`` (:meth:`Results.is_required`), so a blank optional field is an
optional problem of status ``missing``. The levels are the template's, kept in ``run.json`` when the run starts (a
template cannot change under a run); a run made before they were kept has no ``"required"``, and every field of it
counts as required, as it always did. :class:`RunSummary` counts the whole ``out_dir``: ``complete`` (read, no
required problem left; it may have optional ones), ``with_problems`` (read, a required problem left),
``no_template``, ``internal_errors`` (an error starting "internal error:", a fault of the code or a template, not of
the file) and ``unreadable`` (every other error), which add up to ``total``; and apart from those, ``optional_only``
(the complete documents with an optional problem) and ``with_optional`` (every document with one).

**For a screen over a run** (meant for ~100k documents). The pass of ``load`` that indexes the lines also counts
them, so nothing below reads the whole file again. ``summary()`` is those counts. ``field_counts()`` gives, for each
concrete path (``owners[0].name`` is its own path), how many documents read ended ``found``, ``missing``,
``failed_check``, ``unsure``, ``absent`` and ``not_on_form`` after the AI's answers and the decisions are applied (so
it is what ``rows`` would show), ``reviewed``, how many of them have a decision on that path, and ``required``,
whether the path is required in any template that has it; a failed document has no fields and is counted nowhere.
The pass keeps, for each document that still has a problem, its problem paths, statuses and levels (a few bytes
each); ``page(offset, limit, path=..., status=..., level=...)`` is the documents that still have a problem (the
fields ``problems`` gives, filtered the same way: ``path`` may be ``owners[].name``, every item; ``level`` lists only
the documents with a problem of that level, with only that level's fields), in ``doc_id`` order, as ``(how many such
documents, the ones from offset on up to limit)``, each with its effective result and the problem fields that
matched. The sorted list of documents for each ``(path, status, level)`` filter is made from those the first time it
is asked for and kept, so a page parses only its own documents. ``set_aside_page`` pages the failed documents the
same way, and ``document(doc_id)`` parses one document by its line. A decision (``decide``), and every line
``refresh()`` reads, takes the documents it touches out of the counts and lists and puts them back as they now are
(a level is part of what is kept, so settling a document's last required problem makes it complete in place).
``refresh()`` catches up with the files: it reads only the lines appended since (a run, the AI pass or another
process writing), and reads everything again only when a file was rewritten (it shrank, or changed with nothing
added); with no file changed it reads nothing. A Results is not safe for threads; a server sharing one holds a lock
around each use.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import multiprocessing
import os
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from formcast.errors import FormcastError
from formcast.extract.layout import LAYOUT_MATCH, layout_score
from formcast.extract.levels import LEVELS, Level, required_paths
from formcast.extract.model import _HAS_VALUE, DocumentResult, ExtractionTemplate, FieldResult, Status
from formcast.extract.page import DocText
from formcast.extract.read import _extract_open, _failed, error_text, now
from formcast.match import PdfFeatures

RUN = "run.json"
RESULTS = "results.jsonl"
REVIEWS = "reviews.jsonl"
AI = "ai.jsonl"
#: Why a field the AI read is ``unsure``: its value is not settled until a person has looked at it.
AI_REASON = "read by the AI; check it"
#: The statuses review settles.
PROBLEMS: tuple[Status, ...] = ("missing", "failed_check", "unsure")
#: The start of the error of a document no template fits.
NO_TEMPLATE = "no template fits"
_NO_TEMPLATE = re.compile(r"no template fits \(closest: (.*), (\d+\.\d+)\)", re.DOTALL)
#: The start of the error of a document a fault of the code or of a template stopped (Ruling R18).
INTERNAL_ERROR = "internal error:"
#: The error of a document whose reading killed the worker process, read alone as well (Ruling R22).
CRASHED = "cannot read PDF: the reader crashed"
#: The error of a document that was still being read, alone, after the run's timeout (Ruling F5).
HUNG = "cannot read PDF: the reader hung"
#: Seconds a document may take in a worker before it is taken for hung.
TIMEOUT = 120.0
#: The statuses :meth:`Results.field_counts` counts.
_STATUSES: tuple[str, ...] = get_args(Status)
#: Documents handed to each worker ahead of the one it is reading.
_AHEAD = 4
_ITEM = re.compile(r"\[(\d+)\](\.)?")
_ITEM_NUMBER = re.compile(r"\[\d+\]")
_DOC_COLUMNS = ("doc_id", "file", "template", "matched", "extracted_at")
#: The files of a run's ``out_dir`` that :meth:`Results.refresh` watches.
_FILES = (RUN, RESULTS, REVIEWS, AI)
#: How a document can end, as :class:`RunSummary` counts them.
_KINDS = ("complete", "with_problems", "no_template", "unreadable", "internal_errors")
#: The documents :class:`RunSummary` counts by their optional problems, apart from how they ended.
_OPTIONAL = ("optional_only", "with_optional")
#: A document's problem as :class:`Results` keeps it: ``(path, status, level)``.
_Problem = tuple[str, str, Level]
#: A filter of the documents with problems: ``(path, status, level)``, ``None`` for any.
_Filter = tuple[str | None, str | None, Level | None]


class RunSummary(BaseModel):
    """How a run's documents ended; ``complete + with_problems + no_template + unreadable + internal_errors ==
    total``. ``optional_only`` counts the complete documents that still have an optional problem, ``with_optional``
    every document that has one (complete or with problems)."""

    total: int
    complete: int
    with_problems: int
    no_template: int
    unreadable: int
    internal_errors: int
    seconds: float
    optional_only: int = 0
    with_optional: int = 0


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


class AiAnswer(BaseModel):
    """What the AI pass read for one field of one document; ``value`` is ``None`` when the model found no answer."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    path: str
    value: Any = None
    at: str = Field(default_factory=now)


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
    """A pool of ``workers`` processes started by spawn (see the module, *Workers*), each given the templates."""
    return ProcessPoolExecutor(
        max_workers=workers, mp_context=multiprocessing.get_context("spawn"), initializer=_start_worker, initargs=(payload,)
    )


def _ping() -> None:
    """Nothing: run once in a new worker so its start-up is not charged to a document's timeout."""


def _kill_workers(pool: ProcessPoolExecutor) -> None:
    """Terminate the pool's worker processes; the pool then breaks, and its pending futures fail."""
    terminate = getattr(pool, "terminate_workers", None)  # Python 3.14+
    if terminate is not None:
        terminate()
        return
    for process in list((pool._processes or {}).values()):  # no public way before 3.14
        process.terminate()


def _read_all(
    templates: list[ExtractionTemplate],
    todo: list[tuple[str, str]],
    workers: int,
    timeout: float,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """Each ``(doc_id, file)`` of ``todo`` read, as result lines, in the order they finish.

    When a worker process dies, or a document has been in flight longer than ``timeout`` seconds (its workers are
    then terminated), every document in flight is read again on its own (:func:`_read_alone`) and the rest of
    ``todo`` goes on in a new pool. Once ``should_stop()`` is true no document is started, in the pool or alone: the
    ones in flight finish and are yielded, and the rest of ``todo`` is left unread.
    """
    workers = workers or os.cpu_count() or 1
    if workers == 1:
        for doc_id, file in todo:
            if should_stop is not None and should_stop():
                return
            yield _line(templates, file, doc_id)
        return
    payload = json.dumps([t.model_dump(mode="json", by_alias=True) for t in templates])
    queue = deque(todo)
    stopped = False  # once true, always true: nothing more is submitted or read alone
    while queue and not stopped:  # one pool until the queue is done or the pool breaks
        count = min(workers, len(queue))
        pool = _pool(count, payload)
        pending: dict[Future[str], tuple[str, str]] = {}  # future -> (doc_id, file)
        sent: dict[Future[str], float] = {}  # future -> when it was submitted
        suspects: list[tuple[str, str]] = []  # read alone once this pool is down
        killed = False  # a document ran past the timeout and the workers were terminated
        try:
            while not suspects:
                while not killed and len(pending) < count * _AHEAD and queue:
                    stopped = stopped or (should_stop is not None and should_stop())
                    if stopped:
                        break
                    item = queue.popleft()
                    try:
                        future = pool.submit(_work, item[1], item[0])
                    except BrokenProcessPool:  # it broke while this process was busy writing or reporting
                        suspects.append(item)  # never sent; read alone with the ones in flight
                        break
                    pending[future], sent[future] = item, time.monotonic()
                if suspects:
                    break
                if not pending:
                    if killed:
                        break  # everything in flight finished after all; the rest goes on in a new pool
                    return
                # wait for a result, or until the oldest document in flight runs past the timeout
                left = None if killed else max(0.0, min(sent[f] for f in pending) + timeout - time.monotonic())
                finished, _ = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
                if not finished:
                    _kill_workers(pool)  # the pool breaks: what is in flight is read alone, with its own timeout
                    killed = True
                    continue
                for future in finished:
                    del sent[future]
                    if isinstance(future.exception(), BrokenProcessPool):
                        suspects.append(pending.pop(future))  # one of these documents killed its worker
                        continue
                    del pending[future]
                    yield future.result()
        finally:
            if any(not f.done() for f in pending):  # left early (the caller stopped): a hung worker never ends
                _kill_workers(pool)
            pool.shutdown(wait=True, cancel_futures=True)
        # the pool broke: settle every future still pending (all are done now), each document once
        for future, item in pending.items():
            if not future.cancelled() and future.exception() is None:
                yield future.result()  # finished before the break, not yet written
            else:
                suspects.append(item)
        yield from _read_alone(payload, suspects, timeout, lambda: stopped or bool(should_stop and should_stop()))


def _read_alone(
    payload: str, suspects: list[tuple[str, str]], timeout: float, should_stop: Callable[[], bool] | None = None
) -> Iterator[str]:
    """Each suspect read alone in a one-worker pool; one that kills that worker too is recorded as crashed, one
    that takes longer than ``timeout`` seconds as hung (its worker is terminated). Once ``should_stop()`` is true
    the suspects not yet started are left unread (a resume reads them).

    A new one-worker pool is started after each crash or hang, and runs :func:`_ping` before the document, so every
    suspect is read on its own by a live worker and its timeout counts the reading, not the worker's start.
    """
    pool: ProcessPoolExecutor | None = None
    try:
        for doc_id, file in suspects:
            if should_stop is not None and should_stop():
                return
            try:
                if pool is None:
                    pool = _pool(1, payload)
                    pool.submit(_ping).result()
                line = pool.submit(_work, file, doc_id).result(timeout=timeout)
            except (BrokenProcessPool, TimeoutError) as exc:
                if pool is not None:
                    _kill_workers(pool)
                    pool.shutdown(wait=True)
                pool = None
                error = HUNG if isinstance(exc, TimeoutError) else CRASHED
                line = _failed(None, error, b"", doc_id, file, now()).model_dump_json()
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
    timeout: float = TIMEOUT,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunSummary:
    """Route and read every PDF of ``paths`` into ``out_dir``, skipping the ones already there (see the module).

    ``timeout`` is the seconds one document may take in a worker before it is taken for hung (see the module;
    ``workers=1`` reads in this process and has no timeout). ``on_progress(done, total)`` is called after each
    document read; ``done`` counts the documents of ``paths`` already in the results from an earlier run. Returns
    the summary of the whole ``out_dir``, with ``seconds`` this call's. ``should_stop()`` is asked before each
    document is started; once it is true the run starts no more, writes the ones in flight and returns (see the
    module, *Stopping*); run again over the same ``out_dir`` it reads the rest.

    Raises:
        FormcastError: no templates; a timeout that is not positive; ``out_dir`` holds a run with other templates,
            an edited template or another root; a path outside the run's root; a line of ``results.jsonl`` before
            its last that is not a record.
    """
    started = time.perf_counter()
    if not templates:
        raise FormcastError("a batch run needs at least one template")
    if not (timeout > 0 and math.isfinite(timeout)):
        raise FormcastError(f"the timeout must be a positive number of seconds, not {timeout!r}")
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
            for line in _read_all(templates, todo, workers, timeout, should_stop):
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
    names = [{"template_id": t.template_id, "name": t.name, "sha256": template_sha256(t)} for t in templates]
    given = Path(root).resolve() if root is not None else None
    if (out / RUN).is_file():
        run = json.loads((out / RUN).read_text(encoding="utf-8"))
        if [t["template_id"] for t in run["templates"]] != [t["template_id"] for t in names]:
            ids = ", ".join(t["template_id"] for t in run["templates"])
            raise FormcastError(f"{out} holds a run with other templates ({ids}); use a new out_dir")
        for had, now_ in zip(run["templates"], names):
            if had.get("sha256") != now_["sha256"]:
                raise FormcastError(
                    f"{out} holds a run whose template {had['template_id']!r} differs from this one (its sha256 in "
                    f"{RUN} is another); use a new out_dir"
                )
        if given is not None and str(given) != run["root"]:
            raise FormcastError(f"{out} holds a run whose root is {run['root']}, not {given}")
        return Path(run["root"])
    if given is None:
        given = Path(os.path.commonpath([f.parent for f in files])) if files else Path.cwd().resolve()
    required = {t.template_id: sorted(required_paths(t.schema_)) for t in templates}
    run = {"templates": names, "started": now(), "root": str(given), "seconds": 0.0, "required": required}
    _write_json(out / RUN, run)
    return given


def template_sha256(template: ExtractionTemplate) -> str:
    """The sha256 of the template's canonical JSON (``model_dump_json(by_alias=True)``), as ``run.json`` keeps it.

    ``setup`` and ``samples`` are left out: they never change a read, so a template written before they existed keeps
    its sha256 (its runs still resume and export), and editing only the samples' readings changes nothing.
    """
    dumped = template.model_dump_json(by_alias=True, exclude={"setup", "samples"})
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _add_seconds(out: Path, seconds: float) -> None:
    run = json.loads((out / RUN).read_text(encoding="utf-8"))
    run["seconds"] = run.get("seconds", 0.0) + seconds
    _write_json(out / RUN, run)


def _write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` whole or not at all (a killed run never leaves half a ``run.json``)."""
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(part, path)


def _whole_lines(path: Path, keep: Callable[[int, int, dict], None], start: int = 0) -> int:
    """Call ``keep(offset, length, record)`` for each whole line of a JSON-lines file from byte ``start`` (where a
    line starts); where the whole lines end.

    The last line is torn when it has no newline or does not parse as a record: it is left out, and the returned
    offset is where it starts. A missing file has no lines.

    Raises:
        FormcastError: a line that is not a record with whole lines after it.
    """
    if not path.is_file():
        return 0
    offset = end = start
    bad: int | None = None  # the number of a line that is not a record, allowed only as the last one
    with path.open("rb") as f:
        f.seek(start)
        for number, line in enumerate(f, start=1):
            if bad is not None:
                where = f"line {bad}" if start == 0 else f"line {bad} after byte {start}"
                raise FormcastError(f"{path}: {where} is not a JSON record")
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
        if field.value is None:  # nothing to accept now (the value it accepted is gone): the field stays as read
            return field
        update = {"status": "found", "reasons": []}
    else:
        update = {"status": "absent", "value": None, "reasons": []}
    return field.model_copy(update=update)


def _from_ai(field: FieldResult, answer: AiAnswer) -> FieldResult:
    """``field`` with the AI's answer on it: always ``unsure``, and no longer tied to where the failed read was."""
    update = {
        "status": "unsure", "strategy": "ai", "value": answer.value, "raw": str(answer.value),
        "page": None, "rect": None, "reasons": [AI_REASON],
    }  # fmt: skip
    return field.model_copy(update=update)


class Results:
    """A run's results and review decisions, read from its ``out_dir`` (see the module). Make one with :meth:`load`.

    Not safe for threads: a caller that shares one between threads holds its own lock around each use.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        #: Goes up each time what the results show changes (a read of the files, a decision); a cache keyed by it
        #: is stale once it moves.
        self.version = 0
        self._read_all()

    @classmethod
    def load(cls, out_dir: str | Path) -> Results:
        """The run in ``out_dir``; a torn last line of its files is ignored.

        Raises:
            FormcastError: ``out_dir`` has no ``run.json``, or a line before the last of a file is not a record.
        """
        out = Path(out_dir)
        if not (out / RUN).is_file():
            raise FormcastError(f"no run in {out}: it has no {RUN}")
        return cls(out)

    # ------------------------------------------------------------ reading the files

    def _read_all(self) -> None:
        """Read the run's files from the start: one pass over ``results.jsonl`` indexes and counts every document."""
        self._stats = {name: _stat(self.out_dir / name) for name in _FILES}  # before reading: a later line is seen later
        self.run = json.loads((self.out_dir / RUN).read_text(encoding="utf-8"))  # templates, started, root, seconds
        self._required = _levels(self.run)  # template_id -> its required paths; None: every field is required
        self._index: dict[str, tuple[int, int]] = {}  # doc_id -> (offset, length) of its line in results.jsonl
        self._decisions: dict[str, dict[str, ReviewDecision]] = {}  # doc_id -> path -> the latest decision
        self._ai: dict[str, dict[str, AiAnswer]] = {}  # doc_id -> path -> the latest AI answer
        self._ends = {RESULTS: 0, REVIEWS: 0, AI: 0}  # where the whole lines read so far end, per file
        self._kinds = dict.fromkeys(_KINDS, 0)  # how many documents ended each way, as RunSummary counts them
        self._optional = dict.fromkeys(_OPTIONAL, 0)  # the documents with optional problems, as RunSummary counts
        self._counts: dict[str, dict[str, int]] = {}  # path -> status (and "reviewed") -> documents
        self._problems: dict[str, tuple[_Problem, ...]] = {}  # doc_id -> its (path, status, level) still problems
        self._aside: list[str] = []  # the failed documents' doc_ids, sorted
        self._lists: dict[_Filter, list[str]] = {}  # (path, status, level) -> sorted doc_ids, made lazily
        for name in (REVIEWS, AI, RESULTS):  # the decisions and answers first: a document is counted as they leave it
            self._follow(name)
        self.version += 1

    def _follow(self, name: str) -> None:
        """Read the whole lines of ``name`` after the last one read, and count what they change."""
        if name == RESULTS:
            keep = self._keep_result
        elif name == REVIEWS:
            def keep(offset: int, length: int, record: dict) -> None:
                decision = ReviewDecision.model_validate(record)
                self._changing(decision.doc_id, lambda: self._remember(decision))
        else:
            def keep(offset: int, length: int, record: dict) -> None:
                answer = AiAnswer.model_validate(record)
                self._changing(answer.doc_id, lambda: self._remember_ai(answer))
        self._ends[name] = _whole_lines(self.out_dir / name, keep, start=self._ends[name])

    def refresh(self) -> bool:
        """Catch up with what was written to the run's files since they were read; whether anything changed.

        Nothing is read when no file's size or time changed. Lines appended to ``results.jsonl``, ``reviews.jsonl``
        or ``ai.jsonl`` (a run, the AI pass or another process writing) are read from where the last read ended, so
        a run being written is followed without reading it again. A file that shrank, or changed with nothing added
        after what was read, was rewritten: then every file is read again from the start.

        Raises:
            FormcastError: as :meth:`load`.
        """
        stats = {name: _stat(self.out_dir / name) for name in _FILES}
        if stats == self._stats:
            return False
        if stats[RUN] is None:
            raise FormcastError(f"no run in {self.out_dir}: it has no {RUN}")
        for name in (RESULTS, REVIEWS, AI):
            now, had, end = stats[name], self._stats[name], self._ends[name]
            size, had_size = (now[0] if now else 0), (had[0] if had else 0)
            if now != had and (size < end or (end > 0 and size == end == had_size)):
                self._read_all()
                return True
        if stats[RUN] != self._stats[RUN]:
            self.run = json.loads((self.out_dir / RUN).read_text(encoding="utf-8"))
            if _levels(self.run) != self._required:  # never by a run (its templates cannot change): counted afresh
                self._read_all()
                return True
        self._stats = stats
        for name in (RESULTS, REVIEWS, AI):
            self._follow(name)
        self.version += 1
        return True

    def _keep_result(self, offset: int, length: int, record: dict) -> None:
        doc_id = record["doc_id"]
        if doc_id in self._index:  # the last line for a doc_id wins
            self._tally(doc_id, -1)
        self._index[doc_id] = (offset, length)
        self._tally(doc_id, 1, record)

    def _changing(self, doc_id: str, change: Callable[[], None]) -> None:
        """Make ``change`` (a decision or an AI answer remembered) and count ``doc_id`` again as it leaves it."""
        known = doc_id in self._index
        if known:
            self._tally(doc_id, -1)
        change()
        if known:
            self._tally(doc_id, 1)

    def _tally(self, doc_id: str, sign: int, record: dict | None = None) -> None:
        """Add (``sign`` 1) or take back (-1) what ``doc_id`` counts for, as it is now (``record``: its line, parsed).

        Adding also sets its problems and whether it is set aside; taking back leaves those to the add that follows.
        """
        if record is None:
            record = json.loads(self._line(doc_id))
        if doc_id in self._decisions or doc_id in self._ai:
            doc = self.effective(DocumentResult.model_validate(record))
            error, template_id, fields = doc.error, doc.template_id, [(f.path, f.status) for f in doc.fields]
        else:  # as it was read: nothing changes it, so its line's statuses are its own
            error, template_id = record.get("error"), record.get("template_id")
            fields = [(f["path"], f["status"]) for f in record.get("fields") or ()]
        problems = tuple(
            (path, status, self._level(template_id, path)) for path, status in fields if status in PROBLEMS
        )
        required = any(level == "required" for _, _, level in problems)
        self._kinds[_kind(error, required)] += sign
        if error is None and any(level == "optional" for _, _, level in problems):
            self._optional["with_optional"] += sign
            if not required:
                self._optional["optional_only"] += sign
        decided = self._decisions.get(doc_id, {})
        for path, status in fields:
            row = self._counts.get(path)
            if row is None:
                row = self._counts[path] = dict.fromkeys((*_STATUSES, "reviewed"), 0)
            row[status] += sign
            if path in decided:
                row["reviewed"] += sign
        if sign > 0:
            self._set_problems(doc_id, problems)
            _place(self._aside, doc_id, error is not None)

    def _set_problems(self, doc_id: str, problems: tuple[_Problem, ...]) -> None:
        old = self._problems.get(doc_id, ())
        if old == problems:
            return
        if problems:
            self._problems[doc_id] = problems
        else:
            self._problems.pop(doc_id, None)
        for key, ids in self._lists.items():
            if _matches(old, *key) != _matches(problems, *key):
                _place(ids, doc_id, _matches(problems, *key))

    def _with_problems(self, path: str | None, status: str | None, level: Level | None) -> list[str]:
        """The doc_ids, sorted, of the documents with a problem field matching ``path``, ``status`` and ``level``;
        kept and updated in place once asked for."""
        key = (path, status, level)
        ids = self._lists.get(key)
        if ids is None:
            ids = self._lists[key] = sorted(
                doc_id for doc_id, problems in self._problems.items() if _matches(problems, *key)
            )
        return ids

    def is_required(self, template_id: str | None, path: str) -> bool:
        """Whether ``path`` is required in the template ``template_id``, as ``run.json`` keeps the levels: always in
        a run made before the levels were kept (it has no ``"required"``), never in a template the run does not
        have."""
        if self._required is None:
            return True
        return path in self._required.get(template_id or "", ())

    def _level(self, template_id: str | None, path: str) -> Level:
        return "required" if self.is_required(template_id, path) else "optional"

    # ------------------------------------------------------------ reading the results

    def check_templates(self, templates: list[ExtractionTemplate]) -> None:
        """Refuse templates other than the ones the run read with (by id and by the sha256 ``run.json`` keeps).

        Raises:
            FormcastError: a template that is not one of the run's, or whose content differs from the run's.
        """
        had = {t["template_id"]: t for t in self.run.get("templates", [])}
        for template in templates:
            kept = had.get(template.template_id)
            if kept is None:
                ids = ", ".join(had) or "none"
                raise FormcastError(f"{template.template_id!r} is not a template of this run (its templates: {ids})")
            if kept.get("sha256") != template_sha256(template):
                raise FormcastError(
                    f"template {template.template_id!r} differs from the one this run read with (its sha256 in "
                    f"{RUN} is another); export with the run's template"
                )

    def _remember(self, decision: ReviewDecision) -> None:
        self._decisions.setdefault(decision.doc_id, {})[decision.path] = decision

    def _remember_ai(self, answer: AiAnswer) -> None:
        self._ai.setdefault(answer.doc_id, {})[answer.path] = answer

    def ai_answers(self, doc_id: str) -> dict[str, AiAnswer]:
        """The AI pass's latest answer for each path it was asked about in ``doc_id`` (``None`` values included)."""
        return dict(self._ai.get(doc_id, {}))

    def doc_ids(self) -> list[str]:
        """The doc_id of every document of the run, sorted."""
        return sorted(self._index)

    def _line(self, doc_id: str) -> bytes:
        offset, length = self._index[doc_id]
        with (self.out_dir / RESULTS).open("rb") as f:
            f.seek(offset)
            return f.read(length)

    def document(self, doc_id: str) -> DocumentResult:
        """The document ``doc_id`` as it was read (decisions not applied); only its own line is parsed.

        Raises:
            KeyError: the run has no such document.
        """
        return DocumentResult.model_validate_json(self._line(doc_id))

    def documents(self) -> Iterator[DocumentResult]:
        """Every document of the run as it was read (decisions not applied), in ``doc_id`` order."""
        if not self._index:
            return
        with (self.out_dir / RESULTS).open("rb") as f:
            for doc_id in sorted(self._index):
                offset, length = self._index[doc_id]
                f.seek(offset)
                yield DocumentResult.model_validate_json(f.read(length))

    def _with_ai(self, doc: DocumentResult, decided: Iterable[str] = ()) -> DocumentResult:
        """``doc`` with each AI answer that has a value on its field, when that field is ``missing`` or
        ``failed_check`` and has no decision (``decided`` are the paths that have one)."""
        answers = self._ai.get(doc.doc_id)
        if not answers:
            return doc
        answers = {path: a for path, a in answers.items() if path not in set(decided)}
        fields = [
            _from_ai(f, answers[f.path])
            if f.path in answers and answers[f.path].value is not None and f.status in ("missing", "failed_check")
            else f
            for f in doc.fields
        ]
        return doc.model_copy(update={"fields": fields})

    def effective(self, doc: DocumentResult) -> DocumentResult:
        """``doc`` with the AI's answers (see the module) and then the latest decision on each of its paths applied:
        a typed value is ``found`` by ``"review"``, an accepted read ``found``, a field not in the document
        ``absent``."""
        decided = self._decisions.get(doc.doc_id)
        doc = self._with_ai(doc, decided or ())
        if not decided:
            return doc
        fields = [_decided(f, decided[f.path]) if f.path in decided else f for f in doc.fields]
        return doc.model_copy(update={"fields": fields})

    def _problem_documents(
        self, path: str | None, status: Status | None, level: Level | None, ids: Iterable[str] | None = None
    ) -> Iterator[tuple[DocumentResult, list[FieldResult]]]:
        """Each document (of ``ids``, else of all) with its fields still ``missing``, ``failed_check`` or ``unsure``
        that match the filters; a document with none is left out. The document is :meth:`effective`."""
        _check_filters(status, level)
        for doc_id in list(self._with_problems(path, status, level)) if ids is None else ids:
            doc = self.effective(self.document(doc_id))
            fields = [
                f
                for f in doc.fields
                if f.status in PROBLEMS
                and _path_is(f.path, path)
                and (status is None or f.status == status)
                and (level is None or self._level(doc.template_id, f.path) == level)
            ]
            if fields:
                yield doc, fields

    def problems(
        self, *, path: str | None = None, status: Status | None = None, level: Level | None = None
    ) -> Iterator[tuple[DocumentResult, FieldResult]]:
        """Each field still ``missing``, ``failed_check`` or ``unsure`` (no decision on it), with its document.
        ``path`` is a concrete path (``owners[0].name``), or a path with ``[]`` for every item of a repeated group
        (``owners[].name``); ``status`` one of the three; ``level`` ``"required"`` or ``"optional"``, the fields of
        that level only (``None``: both).

        Raises:
            FormcastError: ``status`` is not one of those three, or ``level`` not a level.
        """
        for doc, fields in self._problem_documents(path, status, level):
            for f in fields:
                yield doc, f

    def page(
        self,
        offset: int,
        limit: int,
        *,
        path: str | None = None,
        status: Status | None = None,
        level: Level | None = None,
    ) -> tuple[int, list[tuple[DocumentResult, list[FieldResult]]]]:
        """The documents with problems (filtered as :meth:`problems`) in ``doc_id`` order: how many there are, and
        ``limit`` of them from the ``offset``-th on, each with the problem fields that matched. With a ``level``,
        only the documents with a problem of that level are counted and listed, each with that level's problem
        fields only. Only the page's documents are parsed: which documents have problems is kept from the load (see
        the module).

        Raises:
            FormcastError: ``status`` is not a problem status, ``level`` not a level, or ``offset`` or ``limit`` is
                negative.
        """
        if offset < 0:
            raise FormcastError(f"the offset must not be negative, not {offset!r}")
        if limit < 0:
            raise FormcastError(f"the limit must not be negative, not {limit!r}")
        _check_filters(status, level)
        ids = self._with_problems(path, status, level)
        return len(ids), list(self._problem_documents(path, status, level, ids[offset : offset + limit]))

    def field_counts(self) -> dict[str, dict[str, int | bool]]:
        """For each path, how many documents read ended in each status (``found``, ``missing``, ``failed_check``,
        ``unsure``, ``absent``, ``not_on_form``) with the AI's answers and the decisions applied, ``reviewed``,
        how many have a decision on that path, and ``required``, whether the path is required in any template that
        has it (always, in a run made before the levels were kept). A failed document has no fields and is not
        counted."""
        required = None if self._required is None else set().union(*self._required.values())
        return {
            path: {**row, "required": required is None or path in required}
            for path, row in self._counts.items()
            if any(row.values())
        }

    def decide(self, decision: ReviewDecision) -> None:
        """Record ``decision`` in ``reviews.jsonl``; it applies at once and in every later load.

        Raises:
            FormcastError: no such document in the run, no such field in it, or an ``accept`` of a field that has
                no value read (an AI answer counts as a read value). An ``accept`` of an AI answer is recorded as a
                ``value`` decision carrying that value. As in :meth:`effective`, no AI answer is put on a path that
                already has a decision, so an ``accept`` over one never takes an AI value the person was not shown.
        """
        self.refresh()
        if decision.doc_id not in self._index:
            raise FormcastError(f"no document {decision.doc_id!r} in this run")
        read = self._with_ai(self.document(decision.doc_id), self._decisions.get(decision.doc_id) or ())
        field = next((f for f in read.fields if f.path == decision.path), None)
        if field is None:
            raise FormcastError(f"{decision.doc_id}: no field {decision.path!r}")
        if decision.decision == "accept" and field.value is None:
            raise FormcastError(f"{decision.doc_id}: {decision.path}: nothing to accept, no value was read")
        if decision.decision == "accept" and field.strategy == "ai":
            # the value the person saw is kept in the decision, so it stands whatever ai.jsonl holds later
            decision = ReviewDecision(
                doc_id=decision.doc_id, path=decision.path, decision="value", value=field.value, at=decision.at
            )
        reviews = self.out_dir / REVIEWS
        _cut(reviews, self._ends[REVIEWS])  # a torn last line goes before the decision
        line = (decision.model_dump_json() + "\n").encode("utf-8")
        with reviews.open("ab") as sink:
            sink.write(line)
            sink.flush()
        self._ends[REVIEWS] += len(line)
        self._stats[REVIEWS] = _stat(reviews)  # this write is read already: a refresh does not read it again
        self._changing(decision.doc_id, lambda: self._remember(decision))
        self.version += 1

    def summary(self) -> RunSummary:
        """The counts of the run, with decisions applied; ``seconds`` is the time its runs have spent so far."""
        return RunSummary(
            total=sum(self._kinds.values()), seconds=self.run.get("seconds", 0.0), **self._kinds, **self._optional
        )

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
            if doc.error is not None:
                yield _aside_row(doc)

    def set_aside_page(self, offset: int, limit: int) -> tuple[int, list[dict]]:
        """How many documents are set aside, and the :meth:`set_aside` rows of ``limit`` of them from the
        ``offset``-th on, in ``doc_id`` order; only those documents are parsed.

        Raises:
            FormcastError: ``offset`` or ``limit`` is negative.
        """
        if offset < 0 or limit < 0:
            raise FormcastError(f"the offset and the limit must not be negative, not {offset!r} and {limit!r}")
        return len(self._aside), [_aside_row(self.document(doc_id)) for doc_id in self._aside[offset : offset + limit]]


def _aside_row(doc: DocumentResult) -> dict:
    """A failed document's :meth:`Results.set_aside` row."""
    closest = parse_no_template(doc.error)
    reason = NO_TEMPLATE if (doc.error or "").startswith(NO_TEMPLATE) else doc.error
    return {
        "doc_id": doc.doc_id, "file": doc.file, "reason": reason,
        "closest_template": closest[0] if closest else None, "closest_score": closest[1] if closest else None,
    }  # fmt: skip


def _kind(error: str | None, required_problems: bool) -> str:
    """How a document ended, as :class:`RunSummary` counts it: read, it is complete unless a required field is a
    problem."""
    if error is None:
        return "with_problems" if required_problems else "complete"
    if error.startswith(NO_TEMPLATE):
        return "no_template"
    return "internal_errors" if error.startswith(INTERNAL_ERROR) else "unreadable"


def _levels(run: dict) -> dict[str, frozenset[str]] | None:
    """Each template's required paths, as ``run.json`` keeps them; ``None`` for a run made before it kept them."""
    required = run.get("required")
    return None if required is None else {template_id: frozenset(paths) for template_id, paths in required.items()}


def _check_filters(status: str | None, level: str | None) -> None:
    if status is not None and status not in PROBLEMS:
        raise FormcastError(f"{status!r} is not a problem status (one of {', '.join(PROBLEMS)})")
    if level is not None and level not in LEVELS:
        raise FormcastError(f"{level!r} is not a level (one of {', '.join(LEVELS)})")


def _matches(problems: tuple[_Problem, ...], path: str | None, status: str | None, level: str | None) -> bool:
    """Whether one of ``problems`` (``(path, status, level)``) is of ``path``, ``status`` and ``level`` (``None``:
    any)."""
    return any(
        _path_is(p, path) and (status is None or s == status) and (level is None or lv == level)
        for p, s, lv in problems
    )


def _path_is(concrete: str, path: str | None) -> bool:
    """Whether the concrete path is ``path`` (``None``: any): the same path, or, for a path with ``[]``
    (``owners[].name``), the path of any item of it."""
    return path is None or concrete == path or ("[]" in path and _ITEM_NUMBER.sub("[]", concrete) == path)


def _place(ids: list[str], doc_id: str, member: bool) -> None:
    """Put ``doc_id`` into the sorted list ``ids`` (``member``) or take it out, whichever it is not already."""
    i = bisect.bisect_left(ids, doc_id)
    there = i < len(ids) and ids[i] == doc_id
    if member and not there:
        ids.insert(i, doc_id)
    elif there and not member:
        del ids[i]


def _stat(path: Path) -> tuple[int, int] | None:
    """A file's size and modification time in nanoseconds; ``None`` when there is no file."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns
