"""The extraction workbench's managers: batches of PDFs, setups, saved extractions and runs.

This module is where they live; ``Api`` holds one of each (``api.batches``, ``api.setups``, ``api.extractions``,
``api.runs``), and ``demo/server/extract_api.py`` is the HTTP face over them. Everything they write goes under
``<data_dir>/local/`` (``batches/``, ``extractions/``, ``runs/``), except the review decisions of a command line
run, which ``Results.decide`` keeps in that run's own folder.

**Batches.** A batch is the PDFs one extraction starts from, held in memory by id (``Batches``, ``Batch``). It is
filled in one of two ways:

    uploads   ``Batches.add(batch, [(name, bytes)])``, any number of times (the page sends a large batch in several
              requests). A ``.pdf`` is stored as ``<root>/<batch id>/files/<index>.pdf``; a ``.zip`` is opened and
              each entry whose name ends ``.pdf`` (in any folder, any case) is stored the same way. Other files and
              other entries (and encrypted entries) are counted in ``skipped``; folder entries and macOS metadata
              (``__MACOSX/``, ``._name``) are ignored. A client's file name is only ever shown, never used as a
              path: it is cut to its last component, so ``../evil.pdf`` is shown as ``evil.pdf`` and stored by its
              number like every other file.
    a folder  ``Batches.from_folder(folder)``: every ``*.pdf`` under a folder on the server's machine, recursively,
              sorted by path, referenced where it is (nothing is copied or written). The walk stops past
              :data:`MAX_BATCH_FILES` PDFs (refused), and a file whose ``stat`` fails is left out.

An upload is all or nothing: every zip of the request is checked before anything is written, and the request is
refused (``FormcastError``, nothing written) when the uncompressed PDFs a zip declares pass :data:`MAX_ZIP_BYTES`
or the batch would pass :data:`MAX_BATCH_FILES`. A zip entry cannot unpack to more than it declares (``zipfile``
stops at the declared size), so the declared sizes bound what is written. A zip that fails while it is unpacked
(a bad checksum) takes back the files of its request already written.

**The scan.** Each change to a batch's files (re)starts its scan (``formcast.extract.scan.scan``) on a daemon thread,
over all its files; from :data:`FIRST_PAGE_ONLY_FROM` files on, it reads first pages only. ``summary()["scan"]``
says how it is going (``done`` of ``total``) and, once ``done``, the layout groups, largest first. A newer scan
replaces a running one: each scan carries the batch's scan generation, and one that finds the generation moved on
stops at its next document and drops what it found. A scan that fails unexpectedly ends ``error`` with a generic
message; the traceback goes to the server log. The scan reads each document under ``PYMUPDF_LOCK`` and lets go of
it between documents, so a page image asked for during a scan of thousands waits for one document, not the scan.

**Samples.** ``Batches.pick(batch, n, how, picked)`` chooses the documents a template is built from: ``spread`` or
``first`` take ``n`` (3 to 20) of the scan's largest group with ``formcast.extract.scan.sample``, once the scan is
done (``ScanNotDone`` before); ``pick`` takes the person's own choice as given.

**Setups.** A setup is an extraction template being made: the template, its samples as read
(``formcast.extract.samples.SampleDoc``), their numbers in the batch they came from, the template's proof on them
and the build's warnings (``Setup``). ``Setups`` keeps the last :data:`SETUP_LIMIT` in memory, the least recently
used dropped first, so after a restart every setup id is unknown. A setup changes in three ways, none of which calls
a model except the line job's ``add_line``:

    an edit      ``Setup.edit(edit)``: ``apply_edit`` (given the sample PDFs, so a box drawn on a sample is moved into
                 the layout's place), then ``prove``
    a wording    ``Setup.learn(path)``: the wordings the proof suggests for one path (``FieldProof.suggested``) put into
                 its reader's map (``learn_maps`` given a proof of that path only), then ``prove``
    a line       ``Setup.start_line()`` before the ``extract-line`` job starts, ``Setup.end_line(result)`` when it ends:
                 the job's ``add_line`` result replaces the template and proof (``None``, for a job that failed or was
                 cancelled, leaves them)

A setup takes one change at a time: while a line job runs, an edit, a wording or another line job raises
``SetupBusy``; edits wait for one another (each takes the setup's lock while it proves).

**Saved extraction templates.** ``ExtractionStore`` keeps each saved template as ``<folder>/<name>.json`` with its
sample PDFs beside it, as ``<folder>/assets/<name>/samples/<i>.pdf`` in the template's order (``<folder>`` is
``<data>/local/extractions``), so a saved template reopens as a setup and is proven again on the very samples its
readings are of. Names are the fill store's (``store.check_name``: ``ValueError`` for one that is not plain); a stored
file that is not a valid template, or whose samples are missing, raises ``StoredTemplateError``. Files are written as
``store._write`` writes them (a temporary file and ``os.replace``), the samples before the template.

**Runs.** A run is saved extraction templates read over a batch with ``formcast.extract.batch.run_batch`` (``Run``,
``Runs``). A workbench run is a folder ``<data>/local/runs/<id>/`` holding the ``run_batch`` files (``run.json``,
``results.jsonl``, and ``reviews.jsonl`` and ``ai.jsonl`` once there are decisions and AI answers) and three of its
own: :data:`RUN_META` (``{name, extraction, batch, started}``; ``name`` is the batch's), :data:`RUN_TEMPLATES` (the
templates as they were when the run was made, so a run is read with the templates its results are of, whatever is
saved later) and :data:`RUN_NAMES` (each document's shown name, by ``doc_id``: an uploaded file is stored by its
number, so its ``doc_id`` is ``"3.pdf"``). Its ``root`` is the batch's folder (``Batch.root``), so a document's
``doc_id`` is the same each time the batch is read. ``Runs.for_batch`` gives the run of a batch and extraction
whose templates have the same sha256, so a cancelled run resumes where it stopped (no document read twice);
otherwise a new one. ``Runs.get`` reads a run's folder the first time it is asked for, so runs outlive the server
(their batches do not: batches are in memory, so the same files read after a restart are a new batch and a new run).

A command line run (``python -m formcast.extract run``) is opened by its folder (``Runs.open_folder``): each
template its ``run.json`` names must be a saved extraction with the same ``template_id`` and sha256. It is held in
memory under a new id and read where it is; nothing is copied. Its documents are named by their ``doc_id`` (their
path in that folder).

A run's results (``formcast.extract.batch.Results``) are loaded once, by the first request that needs them, and kept
in memory: one pass over ``results.jsonl`` counts every document and notes which still have problems, so a page of
problems (``Run.problems``) or of documents set aside (``Run.set_aside``) parses only its own documents, a page image
finds its document by its line (``Run.document``), and a decision (``Run.decide``) updates the counts and lists in
place, reading nothing again. Before each use the kept results catch up with the run's files (``Results.refresh``):
with nothing changed that is four ``stat`` calls; while a job is writing it reads only the lines appended since.
``Run.summary()`` (the run as the API shows it: the ``RunSummary``, each field's counts with its title from the
schema, the seconds spent) is made from those counts and kept until they change, so ``Runs.list`` costs a ``stat``
per file of each run once its results are loaded. An export and the dataset sent to fill are passes over every
document and load the results anew, holding no lock. A run takes one job at a time (``Run.claim``, ``RunBusy``) and
one decision at a time. ``Run.export`` names each document (the
export's ``name`` column) and gives an uploaded one's shown name as its ``file``, never the server's copy of it.
``Run.table`` (sent to fill) holds only ``found`` values: whatever review has not settled is a gap.

Threads: ``Batches`` has one lock for its table. Each ``Batch`` has a lock for its state (files, counts, scan) and a
second one that lets one upload at a time write; the state lock is never held while files are written or read.
Lock order: ``PYMUPDF_LOCK`` first, a batch's state lock second. ``Setups`` and ``ExtractionStore`` have one lock
each for their table or folder. A ``Setup``'s lock is taken before ``PYMUPDF_LOCK``, never while holding it: an edit
proves holding the setup's lock, and the library takes ``PYMUPDF_LOCK`` (its own ``PDF_LOCK``) for each MuPDF call
it makes (a document opened, a page read or rendered), so a page image asked for meanwhile waits for one call, not
the whole proof. ``Runs`` has one lock for its table (held while a run's folder is made or read); each ``Run`` has
one for its job flag, its kept results and summary, and its decisions: every use of the kept results takes it. A run's job reads documents with no lock of the
server's: the library takes ``PYMUPDF_LOCK`` for each MuPDF call it makes in this process.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Literal

from demo.server.documents import PYMUPDF_LOCK
from demo.server.store import NAME_RE, StoredTemplateError, _write, check_name
from formcast.data.table import MAX_ROWS, Table
from formcast.errors import FormcastError
from formcast.extract.batch import PROBLEMS, RUN, Results, ReviewDecision, RunSummary, template_sha256
from formcast.extract.edit import Edit, apply_edit
from formcast.extract.export import ExportFormat, _node, _template_columns, _title, export  # _title: as the ``fields`` part
from formcast.extract.levels import REASON_KEY, Level, required_paths
from formcast.extract.model import DocumentResult, ExtractionTemplate, FieldResult, concrete_paths
from formcast.extract.proof import Mark, Proof, learn_maps, prove, sample_marks
from formcast.extract.samples import SampleDoc, SamplePdf
from formcast.extract.scan import ScanReport, sample, scan
from formcast.extract.setup import MAX_SAMPLES, SetupResult
from formcast.schema import parse_path

log = logging.getLogger("demo.server")

MAX_ZIP_BYTES = 2 * 2**30  #: the most uncompressed bytes of PDFs one zip may declare
MAX_BATCH_FILES = 200_000  #: the most PDFs one batch may hold
FIRST_PAGE_ONLY_FROM = 2_000  #: from this many files on, the scan reads first pages only
PAGE_LIMIT = 200  #: the most entries one page of a list (files, documents) may ask for
PAGE_DEFAULT = 50  #: the entries a page of a list gives when it does not say
MIN_SAMPLE = 3  #: the fewest documents a sample picked by ``spread`` or ``first`` may ask for
DEFAULT_NAME = "Uploaded files"  #: a batch's name when it is given none
SCAN_PAUSE = 0.001  #: seconds the scan leaves ``PYMUPDF_LOCK`` free between documents
SCAN_FAILED = "The scan stopped because of an unexpected error. The server log has the details."
SETUP_LIMIT = 8  #: the setups kept in memory; the least recently used is dropped first
RUN_META = "run-meta.json"  #: a workbench run's own record, ``{name, extraction, batch, started}``
RUN_TEMPLATES = "templates.json"  #: the templates a workbench run reads with, as they were when it was made
RUN_NAMES = "names.json"  #: the name shown for each document of a workbench run, by ``doc_id``
RUN_ID = re.compile(r"[0-9a-f]{32}")  #: a run's id, and the name of its folder
#: The counts of each field a run shows, in this order (``Results.field_counts`` gives these and more).
FIELD_COUNTS = ("found", "missing", "failed_check", "unsure", "reviewed", "not_on_form")
#: The summary of a run that has not begun reading.
NOT_STARTED = RunSummary(total=0, complete=0, with_problems=0, no_template=0, unreadable=0, internal_errors=0, seconds=0.0)


class ScanNotDone(FormcastError):
    """The batch is still being scanned, so there is no layout to take a sample from yet."""


class SetupBusy(FormcastError):
    """A field is being added to the setup from a line; it takes no other change until that job ends."""


class _Superseded(Exception):
    """A newer scan of the batch has started: this one stops and keeps nothing."""


@dataclass(frozen=True)
class BatchFile:
    """One PDF of a batch: its number, the name shown for it (never a path), where it is, and its size in bytes."""

    index: int
    name: str
    path: Path
    size: int


@dataclass
class ScanState:
    """How a batch's scan is going: ``scanning`` (``done`` of ``total``), ``done`` (with the report) or ``error``."""

    phase: Literal["scanning", "done", "error"] = "done"
    done: int = 0
    total: int = 0
    error: str | None = None
    report: ScanReport | None = None


@dataclass(eq=False)
class Batch:
    """The PDFs one extraction starts from. Build with ``Batches.create`` or ``Batches.from_folder``.

    ``folder`` is the folder on the server a batch was opened from (``None`` for an uploaded one); ``files_dir``
    is where an uploaded batch's files are stored. Read the state with ``summary``, ``page``, ``file`` and
    ``group_of``: they take the batch's lock.
    """

    id: str
    name: str
    files_dir: Path
    folder: Path | None = None
    files: list[BatchFile] = field(default_factory=list)
    skipped: int = 0
    bytes: int = 0
    scan: ScanState = field(default_factory=ScanState)
    _groups: dict[int, int] = field(default_factory=dict, repr=False)  # file index -> scan group id
    _generation: int = field(default=0, repr=False)  # bumped by each scan started; an older scan stops
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)  # the state above
    _adding: threading.Lock = field(default_factory=threading.Lock, repr=False)  # one upload writes at a time

    def summary(self) -> dict:
        """The batch as the API shows it: ``{id, name, count, bytes, skipped, folder, scan}``."""
        with self._lock:
            state = self.scan
            groups = state.report.groups if state.report is not None else []
            return {
                "id": self.id,
                "name": self.name,
                "count": len(self.files),
                "bytes": self.bytes,
                "skipped": self.skipped,
                "folder": self.folder is not None,
                "scan": {
                    "phase": state.phase,
                    "done": state.done,
                    "total": state.total,
                    "error": state.error,
                    "groups": [
                        {"group": g.id, "size": len(g.files), "fillable": g.fillable, "flattened": g.flattened, "closeness": g.closeness}
                        for g in groups
                    ],
                },
            }

    def page(self, offset: int, limit: int) -> tuple[int, list[BatchFile]]:
        """``(number of files, the files from offset, at most limit of them)``, in index order."""
        with self._lock:
            return len(self.files), self.files[offset : offset + limit]

    def all_files(self) -> list[BatchFile]:
        """Every file of the batch, in index order (for a run, which reads them all)."""
        with self._lock:
            return list(self.files)

    def root(self) -> Path:
        """The folder the batch's files are under: the folder it was opened from, or where its uploads are stored."""
        return self.folder if self.folder is not None else self.files_dir

    def file(self, index: int) -> BatchFile:
        """The file numbered ``index``.

        Raises:
            KeyError: the batch has no such file.
        """
        with self._lock:
            if not 0 <= index < len(self.files):
                raise KeyError(str(index))
            return self.files[index]

    def group_of(self, index: int) -> int | None:
        """The scan group of file ``index``: ``None`` until the scan is done, or when the file could not be read."""
        with self._lock:
            return self._groups.get(index)


@dataclass
class _Pdf:
    """One PDF an upload will store: given as bytes, or as an entry of the zip ``source``."""

    name: str
    source: str  # the uploaded file's name, for an error message
    data: bytes | None = None
    archive: zipfile.ZipFile | None = None
    entry: zipfile.ZipInfo | None = None


class Batches:
    """The batches the workbench has open, by id; uploaded files go under ``root`` (``<data>/local/batches``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._batches: dict[str, Batch] = {}
        self._lock = threading.Lock()

    def create(self, name: str | None = None) -> Batch:
        """A new, empty batch for uploads. Nothing is written until files are added."""
        batch_id = uuid.uuid4().hex
        return self._keep(Batch(batch_id, _label(name or "", DEFAULT_NAME), self.root / batch_id / "files"))

    def from_folder(self, folder: Path, name: str | None = None) -> Batch:
        """A batch of every ``*.pdf`` under ``folder`` (recursively, sorted by path), referenced in place, its scan started.

        The walk stops at the first PDF past :data:`MAX_BATCH_FILES`; a file whose ``stat`` fails (gone since the
        walk found it, or not readable) is left out, as is anything that is not a plain file.

        Raises:
            FormcastError: ``folder`` is not a folder or cannot be read, holds no PDF, or holds more than
                :data:`MAX_BATCH_FILES`.
        """
        try:
            is_dir = folder.is_dir()
        except (OSError, ValueError):
            is_dir = False
        if not is_dir:
            raise FormcastError("There is no folder at that path on the server.")
        found: list[tuple[Path, int]] = []
        try:
            for p in folder.rglob("*"):
                if p.suffix.lower() != ".pdf":
                    continue
                try:
                    st = p.stat()
                except (OSError, ValueError):
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                found.append((p, st.st_size))
                if len(found) > MAX_BATCH_FILES:
                    raise FormcastError(f"That folder holds more than {MAX_BATCH_FILES:,} PDF files, the most a batch may hold.")
        except OSError:
            raise FormcastError("That folder can't be read on the server.") from None
        if not found:
            raise FormcastError("That folder holds no PDF files.")
        found.sort()
        batch_id = uuid.uuid4().hex
        batch = Batch(batch_id, _label(name or folder.name, "Server folder"), self.root / batch_id / "files", folder=folder)
        batch.files = [BatchFile(i, _shown(p.name), p, size) for i, (p, size) in enumerate(found)]
        batch.bytes = sum(f.size for f in batch.files)
        self._keep(batch)
        self.start_scan(batch)
        return batch

    def get(self, batch_id: str) -> Batch:
        """The batch ``batch_id``. Raises ``KeyError`` when there is none (or the server restarted since)."""
        with self._lock:
            return self._batches[batch_id]

    def _keep(self, batch: Batch) -> Batch:
        with self._lock:
            self._batches[batch.id] = batch
        return batch

    # ------------------------------------------------------------ uploads

    def add(self, batch: Batch, uploads: list[tuple[str, bytes]]) -> None:
        """Store the PDFs of ``uploads`` (PDFs and zips of PDFs; see the module docstring) and start the scan again.

        Raises:
            FormcastError: the batch was opened from a folder; a zip cannot be opened or unpacked, or declares more
                than :data:`MAX_ZIP_BYTES` of PDFs; or the batch would hold more than :data:`MAX_BATCH_FILES`.
                Nothing of the request is kept.
        """
        if batch.folder is not None:
            raise FormcastError("A batch opened from a folder is that folder; start a new batch to upload files.")
        with batch._adding:
            pdfs, skipped = _plan(uploads)
            try:
                with batch._lock:
                    start = len(batch.files)
                if start + len(pdfs) > MAX_BATCH_FILES:
                    raise FormcastError(f"A batch holds at most {MAX_BATCH_FILES:,} PDF files, and these would take it past that.")
                new = _store(pdfs, batch.files_dir, start)
            finally:
                for archive in {id(p.archive): p.archive for p in pdfs if p.archive is not None}.values():
                    archive.close()
            with batch._lock:
                batch.files.extend(new)
                batch.skipped += skipped
                batch.bytes += sum(f.size for f in new)
        if new:
            self.start_scan(batch)

    # ------------------------------------------------------------ the scan

    def start_scan(self, batch: Batch) -> None:
        """Scan all of ``batch``'s files on a daemon thread, replacing any scan of it still running."""
        with batch._lock:
            batch._generation += 1
            generation = batch._generation
            paths = [f.path for f in batch.files]
            batch.scan = ScanState(phase="scanning" if paths else "done", total=len(paths))
            batch._groups = {}
        if paths:
            threading.Thread(target=self._scan, args=(batch, generation, paths), name=f"scan-{batch.id[:8]}", daemon=True).start()

    def _scan(self, batch: Batch, generation: int, paths: list[Path]) -> None:
        def step(done: int, total: int) -> None:
            with batch._lock:
                if batch._generation != generation:
                    raise _Superseded
                batch.scan.done = done
            PYMUPDF_LOCK.release()  # between documents, a page image (or anything else) may have MuPDF
            try:
                time.sleep(SCAN_PAUSE)
            finally:
                PYMUPDF_LOCK.acquire()

        try:
            with PYMUPDF_LOCK:
                report = scan(paths, first_page_only=len(paths) >= FIRST_PAGE_ONLY_FROM, on_progress=step)
        except _Superseded:
            return
        except Exception:
            log.exception("the scan of batch %s failed", batch.id)
            with batch._lock:
                if batch._generation == generation:
                    batch.scan = ScanState(phase="error", done=batch.scan.done, total=len(paths), error=SCAN_FAILED)
            return
        index_of = {str(p): i for i, p in enumerate(paths)}
        groups = {index_of[f]: g.id for g in report.groups for f in g.files}
        with batch._lock:
            if batch._generation == generation:
                batch.scan = ScanState(phase="done", done=len(paths), total=len(paths), report=report)
                batch._groups = groups

    # ------------------------------------------------------------ samples

    def pick(self, batch: Batch, n: int, how: str, picked: list[int] | None) -> list[int]:
        """The indexes of the documents to build a template from (see the module docstring).

        Raises:
            ScanNotDone: ``spread`` or ``first`` while the batch is being scanned.
            FormcastError: an unknown ``how``; ``n`` not from 3 to 20; ``picked`` empty, longer than 20, with an
                index twice or one that is not a file of the batch; or no document of the batch could be read.
        """
        with batch._lock:
            count, state = len(batch.files), batch.scan
            paths = {str(f.path): f.index for f in batch.files} if how in ("spread", "first") else {}
        if how == "pick":
            if not isinstance(picked, list) or not 1 <= len(picked) <= MAX_SAMPLES:
                raise FormcastError(f"Pick 1 to {MAX_SAMPLES} documents of the batch.")
            for i in picked:
                if type(i) is not int or not 0 <= i < count:
                    raise FormcastError(f"{i!r} is not a document of this batch (it has {count}).")
            if len(set(picked)) != len(picked):
                raise FormcastError("Pick each document once.")
            return list(picked)
        if how not in ("spread", "first"):
            raise FormcastError('how must be "spread", "first" or "pick".')
        if type(n) is not int or not MIN_SAMPLE <= n <= MAX_SAMPLES:
            raise FormcastError(f"A sample is {MIN_SAMPLE} to {MAX_SAMPLES} documents.")
        if state.phase == "scanning":
            raise ScanNotDone("The batch is still being scanned; pick the sample once the scan is done.")
        main = state.report.main if state.report is not None else None
        if main is None:
            raise FormcastError("None of this batch's documents could be read, so there is no layout to take a sample from.")
        return [paths[p] for p in sample(main, n, how)]


def _plan(uploads: list[tuple[str, bytes]]) -> tuple[list[_Pdf], int]:
    """The PDFs to store from ``uploads`` and how many files or entries are skipped; nothing is written.

    Raises:
        FormcastError: a zip cannot be opened, or declares more than :data:`MAX_ZIP_BYTES` of PDFs.
    """
    pdfs: list[_Pdf] = []
    skipped = 0
    for name, data in uploads:
        kind = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if kind == "pdf":
            pdfs.append(_Pdf(name, name, data=data))
        elif kind == "zip":
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
                entries = archive.infolist()
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError):
                raise FormcastError(f"{name} is not a zip file that can be opened.") from None
            declared = 0
            for entry in entries:
                parts = entry.filename.replace("\\", "/").split("/")
                if entry.is_dir() or "__MACOSX" in parts or parts[-1].startswith("._"):
                    continue
                if not parts[-1].lower().endswith(".pdf") or entry.flag_bits & 0x1:  # 0x1: the entry is encrypted
                    skipped += 1
                    continue
                declared += entry.file_size
                pdfs.append(_Pdf(_shown(parts[-1]), name, archive=archive, entry=entry))
            if declared > MAX_ZIP_BYTES:
                raise FormcastError(f"{name} would unpack to more than {_size(MAX_ZIP_BYTES)} of PDFs, the most one zip may hold; split it into smaller zips.")
        else:
            skipped += 1
    return pdfs, skipped


def _store(pdfs: list[_Pdf], files_dir: Path, start: int) -> list[BatchFile]:
    """Write ``pdfs`` as ``files_dir/<start + i>.pdf``; on any failure remove what was written and raise.

    Raises:
        FormcastError: a zip entry cannot be unpacked (a bad checksum, an unsupported compression).
    """
    written: list[BatchFile] = []
    current: _Pdf | None = None
    try:
        for offset, current in enumerate(pdfs):
            path = files_dir / f"{start + offset}.pdf"
            if current.data is not None:
                _write(path, current.data)
                size = len(current.data)
            else:
                with current.archive.open(current.entry) as src:
                    size = _write_stream(path, src)
            written.append(BatchFile(start + offset, current.name, path, size))
    except BaseException as exc:
        for f in written:
            f.path.unlink(missing_ok=True)
        if isinstance(exc, (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError)) and current is not None:
            raise FormcastError(f"{current.source} could not be unpacked: it is damaged or uses a compression this server can't read.") from None
        raise
    return written


def _write_stream(path: Path, src: IO[bytes]) -> int:
    """Copy ``src`` to ``path`` through a temporary file and ``os.replace`` (as ``store._write``); the bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(src, f, 2**20)
            size = f.tell()
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return size


def _shown(name: str) -> str:
    """A file name to show: its last path component, as ``_label`` tidies it. Never a path."""
    return _label(name.replace("\\", "/").rsplit("/", 1)[-1], "document.pdf")


def _label(text: str, default: str) -> str:
    """``text`` with printable characters only, trimmed, at most 120 of them; ``default`` when nothing is left."""
    return "".join(c for c in text if c.isprintable()).strip()[:120] or default


def _size(n: int) -> str:
    for unit, scale in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if n >= scale:
            return f"{n / scale:.3g} {unit}"
    return f"{n} bytes"


# ------------------------------------------------------------------ setups


@dataclass(eq=False)
class Setup:
    """An extraction template being made, with its samples as read (see the module docstring).

    ``batch_id`` is the batch the samples came from and ``indexes`` their file numbers in it, in the template's
    order; both are ``None`` for a setup reopened from a saved extraction template. Read it with ``summary``,
    ``snapshot``, ``sample`` and ``marks``, and change it with ``edit``, ``learn`` and a line job: they take its lock.
    """

    id: str
    batch_id: str | None
    template: ExtractionTemplate
    samples: list[SampleDoc]
    indexes: list[int] | None
    proof: Proof
    warnings: list[str]
    _line: bool = field(default=False, repr=False)  # an extract-line job is running on this setup
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def summary(self) -> dict:
        """The setup as the API shows it: ``{id, batch, template, proof, warnings, samples: [{index, name, kind, pages}]}``.

        A sample's ``index`` is its file's number in the batch (``None`` for a reopened setup), its ``kind``
        ``fillable`` (it has form fields) or ``flattened``, and ``pages`` its pages' ``[width, height]`` in points.
        """
        with self._lock:
            return {
                "id": self.id,
                "batch": self.batch_id,
                "template": self.template.model_dump(mode="json", by_alias=True),
                "proof": self.proof.model_dump(mode="json"),
                "warnings": list(self.warnings),
                "samples": [
                    {
                        "index": self.indexes[n] if self.indexes is not None else None,
                        "name": doc.file,
                        "kind": "fillable" if any(p.widgets for p in doc.form_map.pages) else "flattened",
                        "pages": [[p.width, p.height] for p in doc.form_map.pages],
                    }
                    for n, doc in enumerate(self.samples)
                ],
            }

    def snapshot(self) -> tuple[ExtractionTemplate, list[SampleDoc]]:
        """The template and the samples as they are now."""
        with self._lock:
            return self.template, list(self.samples)

    def sample(self, n: int) -> SampleDoc:
        """The template's sample ``n`` (from 0, in its order).

        Raises:
            KeyError: the template has no such sample.
        """
        with self._lock:
            if not 0 <= n < len(self.samples):
                raise KeyError(str(n))
            return self.samples[n]

    def marks(self, n: int) -> list[Mark]:
        """``sample_marks`` of sample ``n``: the places on its pages each path is read from, in that page's frame.

        Raises:
            KeyError: the template has no such sample.
        """
        with self._lock:
            template, doc = self.template, self.sample(n)
        return sample_marks(template, doc.pdf)

    def edit(self, edit: Edit) -> None:
        """Make ``edit`` (``apply_edit`` with the sample PDFs) and prove the template again. No model is called.

        Raises:
            SetupBusy: a line job is running on the setup.
            FormcastError: the library refused the edit (``apply_edit``'s message: a path that is not a field, a
                sample the template does not have...).
            pydantic.ValidationError: the edited template is not valid.
        """
        with self._lock:
            self._free()
            pdfs = [doc.pdf for doc in self.samples]
            template = apply_edit(self.template, edit, pdfs=pdfs)
            self.template, self.proof = template, prove(template, pdfs)

    def learn(self, path: str) -> None:
        """Put the wordings the proof suggests for ``path`` into its reader's map and prove again. No model is called.

        Raises:
            SetupBusy: a line job is running on the setup.
            FormcastError: the proof suggests no wording for ``path`` (or it is not a field).
        """
        with self._lock:
            self._free()
            field_proof = next((f for f in self.proof.fields if f.path == path and f.suggested), None)
            if field_proof is None:
                raise FormcastError(f"No wordings are suggested for {path}.")
            template = learn_maps(self.template, self.proof.model_copy(update={"fields": [field_proof]}))
            self.template, self.proof = template, prove(template, [doc.pdf for doc in self.samples])

    def start_line(self) -> tuple[ExtractionTemplate, list[SampleDoc]]:
        """Mark a line job as running on the setup; the template and samples it starts from.

        Raises:
            SetupBusy: a line job is already running on it.
        """
        with self._lock:
            self._free()
            self._line = True
            return self.template, list(self.samples)

    def end_line(self, result: SetupResult | None) -> None:
        """The line job has ended: its result's template and proof replace the setup's (``None`` leaves them)."""
        with self._lock:
            self._line = False
            if result is not None:
                self.template, self.proof = result.template, result.proof
                self.warnings = list(dict.fromkeys([*self.warnings, *result.warnings]))

    def _free(self) -> None:
        if self._line:
            raise SetupBusy("A field is being added to this setup; wait for it to finish, or cancel it.")


class Setups:
    """The setups in progress, by id, in memory: at most ``limit``, the least recently used dropped first."""

    def __init__(self, limit: int = SETUP_LIMIT) -> None:
        self.limit = limit
        self._setups: OrderedDict[str, Setup] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, setup: Setup) -> Setup:
        """Keep ``setup``, dropping the least recently used past the limit."""
        with self._lock:
            self._setups[setup.id] = setup
            while len(self._setups) > self.limit:
                self._setups.popitem(last=False)
        return setup

    def get(self, setup_id: str) -> Setup:
        """The setup ``setup_id``, now the most recently used. Raises ``KeyError`` when there is none (or it was dropped)."""
        with self._lock:
            setup = self._setups[setup_id]
            self._setups.move_to_end(setup_id)
            return setup


# ------------------------------------------------------------------ saved extraction templates


class ExtractionStore:
    """Saved extraction templates with their sample PDFs, in ``folder`` (``<data>/local/extractions``).

    Every method raises ``ValueError("bad template name")`` for a name ``check_name`` refuses, before touching a file.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        return self.folder / f"{check_name(name)}.json"

    def _samples_dir(self, name: str) -> Path:
        return self.folder / "assets" / check_name(name) / "samples"

    def list(self) -> list[dict]:
        """``[{name, template_id, fields, samples, updated}]`` by name: ``fields`` the paths not marked absent,
        ``samples`` how many it was built from, ``updated`` ISO 8601 UTC. A file that is not a template is left out."""
        out = []
        for path in sorted(self.folder.glob("*.json")):
            if not NAME_RE.fullmatch(path.stem):
                continue
            try:
                template = ExtractionTemplate.model_validate_json(path.read_bytes())
                updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except (OSError, ValueError, FormcastError):
                continue
            absent = set(template.absent)
            out.append({
                "name": path.stem,
                "template_id": template.template_id,
                "fields": sum(p not in absent for p in concrete_paths(template.schema_)),
                "samples": len(template.samples),
                "updated": updated.isoformat(timespec="seconds"),
            })  # fmt: skip
        return out

    def get(self, name: str) -> ExtractionTemplate:
        """The template saved as ``name``.

        Raises:
            ValueError: a bad name.
            KeyError: none is saved under it.
            StoredTemplateError: the file is not a valid extraction template.
        """
        path = self._path(name)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise KeyError(name) from None
        try:
            return ExtractionTemplate.model_validate_json(data)
        except (ValueError, FormcastError) as exc:
            raise StoredTemplateError(f"stored extraction template {name!r} is not a valid template") from exc

    def samples(self, name: str) -> list[SamplePdf]:
        """The sample PDFs saved with ``name``, in its template's order, each named as its template's sample is.

        Raises:
            ValueError, KeyError: as ``get``.
            StoredTemplateError: as ``get``, or a sample PDF is missing.
        """
        template = self.get(name)
        folder = self._samples_dir(name)
        out = []
        for n, record in enumerate(template.samples):
            try:
                out.append(SamplePdf(record.file, (folder / f"{n}.pdf").read_bytes()))
            except FileNotFoundError:
                raise StoredTemplateError(f"the sample PDFs of stored extraction template {name!r} are missing") from None
        return out

    def put(self, name: str, template: ExtractionTemplate, samples: list[SamplePdf]) -> None:
        """Save ``template`` as ``name`` with ``samples`` (its sample PDFs, in its order), replacing any saved there.

        Raises:
            ValueError: a bad name.
        """
        path, folder = self._path(name), self._samples_dir(name)
        with self._lock:
            for n, sample_pdf in enumerate(samples):
                _write(folder / f"{n}.pdf", sample_pdf.pdf)
            for old in folder.glob("*.pdf"):  # a template saved here before from more samples
                if not (old.stem.isdigit() and int(old.stem) < len(samples)):
                    old.unlink(missing_ok=True)
            _write(path, template.model_dump_json(by_alias=True, indent=1).encode("utf-8"))

    def delete(self, name: str) -> None:
        """Delete the template saved as ``name`` and its samples.

        Raises:
            ValueError: a bad name.
            KeyError: none is saved under it.
        """
        path, assets = self._path(name), self._samples_dir(name).parent
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                raise KeyError(name) from None
            shutil.rmtree(assets, ignore_errors=True)


# ------------------------------------------------------------------ runs


class RunBusy(FormcastError):
    """A job is already running on this run; a run takes one at a time."""


class RunNotStarted(FormcastError):
    """The run's job has not begun reading yet, so the run has no results yet."""


@dataclass(eq=False)
class Run:
    """A run of saved extraction templates over a batch, in ``out_dir`` (see the module docstring).

    ``batch`` is the id of the batch a workbench run reads (``None`` for a command line run), and ``folder`` is true
    for a command line run opened by its folder. Read it with ``summary``, ``problems``, ``set_aside``, ``document``,
    ``name_of``, ``table`` and ``export``; ``decide`` records a review decision. The first of them to need the
    results loads them, and the run keeps them (see the module docstring).
    """

    id: str
    name: str
    extraction: str
    out_dir: Path
    templates: list[ExtractionTemplate]
    started: str = ""
    batch: str | None = None
    folder: bool = False
    _names: dict[str, str] | None = field(default=None, repr=False)  # doc_id -> shown name, read from RUN_NAMES once
    _titles: dict[str, str] | None = field(default=None, repr=False)  # concrete path -> title
    _reasons: dict[str, dict[str, str]] | None = field(default=None, repr=False)  # template_id -> path -> reason
    _results: Results | None = field(default=None, repr=False)  # loaded once, then kept up with (Results.refresh)
    _cached: tuple[int, dict] | None = field(default=None, repr=False)  # (the results' version, the summary)
    _busy: bool = field(default=False, repr=False)  # a job is running on the run
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _loaded(self) -> Results:
        """The run's results as its files hold them now, with the decisions and the AI's answers: loaded the first
        time, then caught up with what was written since (``Results.refresh``). The caller holds the run's lock.

        Raises:
            RunNotStarted: the run has not begun reading (it has no ``run.json`` yet).
            FormcastError: a line of its files before the last is not a record.
        """
        if self._results is None:
            if not (self.out_dir / RUN).is_file():
                raise RunNotStarted("This run has not read any document yet.")
            self._results = Results.load(self.out_dir)
        else:
            self._results.refresh()
        return self._results

    def _fresh(self) -> Results:
        """The run's results loaded anew, for a pass over all of them (an export, a dataset) that holds no lock.

        Raises:
            RunNotStarted, FormcastError: as ``_loaded``.
        """
        if not (self.out_dir / RUN).is_file():
            raise RunNotStarted("This run has not read any document yet.")
        return Results.load(self.out_dir)

    def paths(self) -> list[str]:
        """The concrete paths the run's templates read (not marked absent), the first template's first, each once."""
        out: dict[str, None] = {}
        for template in self.templates:
            absent = set(template.absent)
            out.update((p, None) for p in concrete_paths(template.schema_) if p not in absent)
        return list(out)

    def titles(self) -> dict[str, str]:
        """Each concrete path's title in its template's schema, as the export's ``fields`` part gives it (the path
        itself when the schema has none)."""
        if self._titles is None:
            titles: dict[str, str] = {}
            for template in self.templates:
                for _, path, schema_path in _template_columns(template):
                    titles.setdefault(path, _title(template.schema_, schema_path) or path)
            self._titles = titles
        return self._titles

    def reasons(self) -> dict[str, dict[str, str]]:
        """Each template's reason for the level of each concrete path (``template_id`` -> path -> the line under
        ``REASON_KEY`` on the field's property in its schema); a path with none is left out."""
        if self._reasons is None:
            self._reasons = {
                template.template_id: {
                    path: reason
                    for _, path, schema_path in _template_columns(template)
                    if isinstance(reason := _node(template.schema_, parse_path(schema_path.path)).get(REASON_KEY), str)
                    and reason
                }
                for template in self.templates
            }
        return self._reasons

    def levels(self) -> dict[str, tuple[bool, str]]:
        """Each concrete path's level and why, from the run's templates: ``(required, reason)``. A path in several
        templates takes the first that requires it (else the first that has it); the reason is that template's."""
        out: dict[str, tuple[bool, str]] = {}
        reasons = self.reasons()
        for template in self.templates:
            required = required_paths(template.schema_)
            for path in concrete_paths(template.schema_):
                if path not in out or (path in required and not out[path][0]):
                    out[path] = (path in required, reasons[template.template_id].get(path, ""))
        return out

    def summary(self) -> dict:
        """The run as the API shows it: ``{id, name, extraction, summary, fields: [{path, title, required,
        required_reason, found, missing, failed_check, unsure, reviewed, not_on_form}], seconds}``. It is made from
        the kept results' counts, again only when they have changed since. A field is ``required`` as the results
        count it (every field of a run made before the levels were kept), or, before the run has read anything, as
        its templates say."""
        with self._lock:
            try:
                results = self._loaded()
            except RunNotStarted:
                key, summary, counts = -1, NOT_STARTED, {}
            else:
                key = results.version
                if self._cached is not None and self._cached[0] == key:
                    return self._cached[1]
                summary, counts = results.summary(), results.field_counts()
        titles, levels = self.titles(), self.levels()
        out = {
            "id": self.id,
            "name": self.name,
            "extraction": self.extraction,
            "summary": summary.model_dump(),
            "fields": [
                {
                    "path": p,
                    "title": titles.get(p, p),
                    "required": bool(counts[p]["required"]) if p in counts else levels[p][0],
                    "required_reason": levels[p][1],
                    **{k: counts.get(p, {}).get(k, 0) for k in FIELD_COUNTS},
                }
                for p in self.paths()
            ],
            "seconds": summary.seconds,
        }
        with self._lock:
            self._cached = (key, out)
        return out

    def problems(
        self, offset: int, limit: int, path: str | None, status: str | None, level: Level | None = None
    ) -> tuple[int, list[tuple[DocumentResult, list[tuple[FieldResult, bool, str]]]]]:
        """``Results.page``: how many documents still have problems (matching ``path``, ``status`` and ``level``),
        and ``limit`` of them from ``offset`` with their problem fields, each with whether it is required in its
        document's template and that template's reason for it (``""`` when it gives none). Only the page's
        documents are parsed.

        Raises:
            RunNotStarted: the run has not begun reading.
            FormcastError: as ``Results.page`` (a status that is not a problem's, a level that is not a level).
        """
        reasons = self.reasons()
        with self._lock:
            results = self._loaded()
            total, page = results.page(offset, limit, path=path, status=status, level=level)
            return total, [
                (
                    doc,
                    [
                        (
                            f,
                            results.is_required(doc.template_id, f.path),
                            reasons.get(doc.template_id or "", {}).get(f.path, ""),
                        )
                        for f in fields
                    ],
                )
                for doc, fields in page
            ]

    def set_aside(self, offset: int, limit: int) -> tuple[int, list[dict]]:
        """``Results.set_aside_page``: how many documents are set aside, and the rows of ``limit`` of them from ``offset``.

        Raises:
            RunNotStarted: the run has not begun reading.
        """
        with self._lock:
            return self._loaded().set_aside_page(offset, limit)

    def document(self, doc_id: str) -> DocumentResult:
        """The document ``doc_id`` of the run, as it was read (only its own line is parsed).

        Raises:
            KeyError: the run has no such document (or has not begun reading).
        """
        with self._lock:
            try:
                return self._loaded().document(doc_id)
            except RunNotStarted:
                raise KeyError(doc_id) from None

    def name_of(self, doc_id: str) -> str:
        """The name shown for a document: its file's name in the batch, or (a command line run's) its ``doc_id``."""
        return self.names().get(doc_id) or (doc_id if self.folder else _shown(doc_id))

    def names(self) -> dict[str, str]:
        """The shown name of each document by ``doc_id`` (from ``RUN_NAMES``; none for a command line run)."""
        if self._names is None:
            try:
                self._names = {} if self.folder else json.loads((self.out_dir / RUN_NAMES).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._names = {}
        return self._names

    def set_names(self, names: dict[str, str]) -> None:
        """Keep the shown name of each document (``doc_id`` -> name) in the run's folder, as ``RUN_NAMES``."""
        _write(self.out_dir / RUN_NAMES, json.dumps(names, ensure_ascii=False).encode("utf-8"))
        self._names = dict(names)

    def decide(self, decision: ReviewDecision) -> None:
        """Record ``decision`` (``Results.decide``) on the kept results, which count it at once (nothing is read
        again); the run takes one decision at a time.

        Raises:
            RunNotStarted: the run has not begun reading.
            FormcastError: the library refused it (no such document or field, nothing to accept).
        """
        with self._lock:
            self._loaded().decide(decision)

    def table(self) -> tuple[Table, int]:
        """The documents read, as a table for the fill flow, and how many of its rows still have a problem.

        A column per path (``paths``) and a row per document, at most ``MAX_ROWS`` rows (``truncated`` when there were
        more). A cell holds the field's value only when it is ``found``, with the AI's answers and the decisions
        applied (a value typed or accepted in review is found; an AI answer not yet accepted is ``unsure``): a field
        ``missing``, ``failed_check`` or ``unsure`` is left empty, so the fill reports it as a gap instead of filling
        in a value nobody trusted. A row counts as having a problem only for a required field's (an optional blank
        leaves the document complete).

        Raises:
            RunNotStarted: the run has not begun reading.
        """
        paths = self.paths()
        results = self._fresh()
        rows: list[dict] = []
        with_problems = 0
        truncated = False
        for doc in results.documents():
            if doc.error is not None:
                continue  # set aside: not a row
            if len(rows) == MAX_ROWS:
                truncated = True
                break
            fields = {f.path: f for f in results.effective(doc).fields}
            rows.append({p: fields[p].value if p in fields and fields[p].status == "found" else None for p in paths})
            with_problems += any(
                f.status in PROBLEMS and results.is_required(doc.template_id, f.path) for f in fields.values()
            )
        table = Table(columns=paths, rows=rows, kind="json", name=f"{self.name} results", truncated=truncated)
        return table, with_problems

    def export(self, out: Path, format: ExportFormat, stored: Path) -> list[Path]:
        """``formcast.extract.export`` of the run into ``out``, with a ``name`` column (``names``); a document kept
        under ``stored`` (the batches' uploads) has its shown name as its ``file``, not where the server keeps it.

        Raises:
            RunNotStarted: the run has not begun reading.
            FormcastError: as ``export`` (a missing extra, a column clash), or a template is not the run's.
        """
        results = self._fresh()
        results.check_templates(self.templates)
        shown = _ShownFiles(results, self, stored.resolve())
        return export(shown, self.templates, out, format=format, names=self.names())

    def read_count(self, doc_ids: Iterable[str]) -> int:
        """How many of ``doc_ids`` the run has read already (0 before it begins)."""
        with self._lock:
            try:
                done = set(self._loaded().doc_ids())
            except RunNotStarted:
                return 0
        return sum(doc_id in done for doc_id in doc_ids)

    def claim(self) -> None:
        """Mark a job as running on the run.

        Raises:
            RunBusy: one already is.
        """
        with self._lock:
            if self._busy:
                raise RunBusy("This run is being read already; wait for that job to finish, or cancel it.")
            self._busy = True

    def release(self) -> None:
        """The run's job has ended (or never started)."""
        with self._lock:
            self._busy = False


class _ShownFiles:
    """A run's results as its export reads them (``rows``, ``status_rows``, ``set_aside``), with the ``file`` of a
    document under ``stored`` replaced by its shown name: an uploaded file's path is the server's numbered copy."""

    def __init__(self, results: Results, run: Run, stored: Path) -> None:
        self._results, self._run, self._stored = results, run, stored

    def rows(self) -> Iterator[dict]:
        return self._shown(self._results.rows())

    def status_rows(self) -> Iterator[dict]:
        return self._shown(self._results.status_rows())

    def set_aside(self) -> Iterator[dict]:
        return self._shown(self._results.set_aside())

    def _shown(self, rows: Iterable[dict]) -> Iterator[dict]:
        for row in rows:
            if Path(row["file"]).is_relative_to(self._stored):
                row = {**row, "file": self._run.name_of(row["doc_id"])}
            yield row


class Runs:
    """The runs: the workbench's, one folder each under ``root`` (``<data>/local/runs``) and read lazily, and the
    command line runs opened by their folders, held in memory only (see the module docstring)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, name: str, extraction: str, batch: str, templates: list[ExtractionTemplate]) -> Run:
        """A new run of ``templates`` (the saved extraction ``extraction``) over the batch ``batch``, its folder written."""
        with self._lock:
            return self._create(name, extraction, batch, templates)

    def for_batch(self, name: str, extraction: str, batch: str, templates: list[ExtractionTemplate]) -> tuple[Run, bool]:
        """The run to read ``batch`` with: the run of that batch and extraction whose templates are ``templates`` (by
        their sha256), which then resumes, else a new one (as ``create``); and whether it is new."""
        shas = [template_sha256(t) for t in templates]
        with self._lock:
            for run in self._stored():
                same = (run.batch, run.extraction) == (batch, extraction)
                if same and [template_sha256(t) for t in run.templates] == shas:
                    return run, False
            return self._create(name, extraction, batch, templates), True

    def discard(self, run: Run) -> None:
        """Forget a run made for a job that never started, and remove its folder."""
        with self._lock:
            self._runs.pop(run.id, None)
        if not run.folder and run.out_dir.parent == self.root:
            shutil.rmtree(run.out_dir, ignore_errors=True)

    def get(self, run_id: str) -> Run:
        """The run ``run_id``: one already in memory, else the one in ``<root>/<run_id>/``.

        Raises:
            KeyError: there is no such run (a command line run is unknown after a restart until it is opened again).
        """
        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
            run = self._load(self.root / run_id) if RUN_ID.fullmatch(run_id) else None
            if run is None:
                raise KeyError(run_id)
            self._runs[run_id] = run
            return run

    def list(self) -> list[dict]:
        """``[{id, name, extraction, started, summary}]`` of every run, the newest first."""
        with self._lock:
            runs = {run.id: run for run in self._stored()} | self._runs
        listed = [
            {"id": r.id, "name": r.name, "extraction": r.extraction, "started": r.started, "summary": r.summary()["summary"]}
            for r in runs.values()
        ]
        return sorted(listed, key=lambda entry: entry["started"], reverse=True)

    def open_folder(self, folder: Path, extractions: ExtractionStore) -> Run:
        """The command line run in ``folder`` (``python -m formcast.extract run``), registered under a new id with
        nothing copied; opening the same folder again gives the same run.

        Each template its ``run.json`` names must be a saved extraction (by ``template_id``) with the same sha256:
        the results are of that template, and review and export need its schema.

        Raises:
            FormcastError: ``folder`` holds no run that can be read; a template of the run is not saved, or the saved
                one differs from it (the message names its id, never the folder).
        """
        try:
            folder = folder.resolve()
            results = Results.load(folder)
            used = [(t["template_id"], t.get("sha256")) for t in results.run["templates"]]
            started = str(results.run.get("started", ""))
        except (FormcastError, OSError, ValueError, KeyError, TypeError):  # their messages name the folder
            raise FormcastError(
                "There is no run in that folder that can be read (a run's folder holds its run.json and results.jsonl)."
            ) from None
        with self._lock:
            for run in self._runs.values():
                if run.folder and run.out_dir == folder:
                    return run
        saved = extractions.list()
        names, templates = [], []
        for template_id, sha in used:
            same_id = [entry["name"] for entry in saved if entry["template_id"] == template_id]
            if not same_id:
                raise FormcastError(f"The run reads with the template {template_id!r}, which is not a saved extraction here.")
            for name in same_id:
                template = extractions.get(name)
                if template_sha256(template) == sha:
                    names.append(name)
                    templates.append(template)
                    break
            else:
                raise FormcastError(
                    f"The run's template {template_id!r} differs from the one saved here ({', '.join(same_id)}): it was "
                    "changed after the run, so the run can't be shown with it."
                )
        if not templates:
            raise FormcastError("The run names no template.")
        run = Run(uuid.uuid4().hex, _label(folder.name, "Command line run"), names[0], folder, templates, started, folder=True)
        with self._lock:
            self._runs[run.id] = run
        return run

    def _create(self, name: str, extraction: str, batch: str, templates: list[ExtractionTemplate]) -> Run:
        """``create`` with the lock held."""
        run_id = uuid.uuid4().hex
        run = Run(run_id, name, extraction, self.root / run_id, list(templates), _now(), batch)
        dumped = [t.model_dump(mode="json", by_alias=True) for t in templates]
        meta = {"name": name, "extraction": extraction, "batch": batch, "started": run.started}
        _write(run.out_dir / RUN_TEMPLATES, json.dumps(dumped, ensure_ascii=False).encode("utf-8"))
        _write(run.out_dir / RUN_META, json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
        self._runs[run_id] = run
        return run

    def _stored(self) -> list[Run]:
        """Every run in ``root``, kept in memory once read; the caller holds the lock."""
        out = []
        for folder in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not RUN_ID.fullmatch(folder.name):
                continue
            run = self._runs.get(folder.name) or self._load(folder)
            if run is not None:
                self._runs[run.id] = run
                out.append(run)
        return out

    def _load(self, folder: Path) -> Run | None:
        """The run in ``folder``, from its ``RUN_META`` and ``RUN_TEMPLATES``; ``None`` when it has none or they are bad."""
        try:
            meta = json.loads((folder / RUN_META).read_text(encoding="utf-8"))
            raw = json.loads((folder / RUN_TEMPLATES).read_text(encoding="utf-8"))
            templates = [ExtractionTemplate.model_validate(t) for t in raw]
            return Run(folder.name, meta["name"], meta["extraction"], folder, templates, meta["started"], meta["batch"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("the run in %s can't be read; leaving it out", folder.name)
            return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
