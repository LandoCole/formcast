# PDF Extraction Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The workbench opens on "What would you like to do?" (Fill a PDF / Extract from PDFs); Extract takes a
batch, builds a template from a sample with the AI, lets the person fix it on a setup screen, runs it on the
whole batch, reviews the problems, exports xlsx/Parquet/CSV and sends the results to fill.

**Architecture:** Server: a new module `demo/server/extraction.py` holds four managers (batches of uploaded or
server-side PDFs with their scan, setups in progress, saved extraction templates, runs) and
`demo/server/extract_api.py` registers their routes on the existing `Api`; long work runs as jobs through a
generalised `Jobs.start_task`. Web: plain ES modules beside the existing workbench modules (`extract.js` for the
choice and the Extract modal, `extract-setup.js` for the setup screen, `extract-review.js` for running, review,
export and send to fill), with their pure logic in `extract-model.js` (node-tested). The page's existing editor
parts are reused: `PageView` draws sample pages and boxes; the workbench's overlay, job panel and stylesheets are
reused; the extraction field list and inspector are new, styled with the existing `fc-sl-*` / `fc-ins-*` classes.

**Tech Stack:** Python 3.12 stdlib HTTP server (existing), `formcast.extract` (plans 1 and 2); plain ES modules,
no build step; Playwright (Python) and `node --test` for tests.

**Spec:** `docs/superpowers/specs/2026-10-28-pdf-extraction-design.md` (sections *The way in*, *The setup screen*,
*Results and review*, *Export and handing to fill*, *Workbench demo, server*, *Settled after review* 4). Mockup
(the reference for layout, ids, copy and behaviour; where it and the spec differ, the spec wins):
`docs/superpowers/specs/2026-10-28-pdf-extraction-mockup.html`. Needs plan 2
(`docs/superpowers/plans/2026-10-29-pdf-extraction-setup.md`) done.

## Global Constraints

- The Fill path is unchanged: choosing *Fill a PDF* opens today's `#start` screen as it is, and every existing
  workbench test still passes.
- No model call per document unless the person turns on *AI per document* for a run (then `ai_pass` after it).
- Server conventions (`demo/server/api.py`): errors as `{"error": code, "detail": plain sentence}` via `ApiError`;
  validate everything before starting a job thread; runtime ids are `uuid4().hex`; stored names use `NAME_RE` /
  `check_name` from `demo/server/store.py` and its atomic `_write`; never put provider errors or file-system paths
  of the server outside the data directory in a response (a run's own document paths are fine for a run opened
  from that folder); any MuPDF call made in the server process holds `PYMUPDF_LOCK`.
- Everything new on disk lives under `<data_dir>/local/` (already git-ignored): `batches/`, `extractions/`, `runs/`.
- Web conventions: no `innerHTML` (build with `createElement`, `replaceChildren`, `textContent`); new overlays are
  in `index.html` at load (the modal code captures `.overlay` elements once) and toggle only with `hidden`; one
  overlay open at a time; `mountX(app)` modules wired in `workbench.js`; a prose header comment per file; ids and
  copy as the mockup's.
- Page modules talk to the server only through the engine (`demo/web/engines/server/client.js`); the engine's
  extraction methods are listed in `core/engine.js`'s `OPTIONAL_METHODS`, and capability `extract` gates the
  *Extract from PDFs* choice (hidden when the server lacks it).
- Tests: server tests drive a real in-process server (`start_demo_server`) with the fake model `CorpusModel` from
  `tests/extract/fakes.py` (plan 2) as `provider_factory`, `extract_workers=1`, and the autouse `no_model_loads`
  fixture; page tests use the Playwright `bench` fixture style of `tests/demo/test_workbench_page.py` and assert no
  page errors. Corpus PDFs come from `tests/extract/corpus.py`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A zip with a `../` or absolute entry name, folders inside it, non-PDF entries, or more uncompressed bytes than
  `MAX_ZIP_BYTES`: names never become paths (files are stored by number), non-PDFs are skipped and counted, an
  over-limit zip is refused before writing anything. Test in Task 3.
- A batch of thousands of files: the files list and the review's documents are paged (`offset`, `limit` at most
  200); no response lists every file; the scan reports `done`/`total` while it runs. Tests in Tasks 3 and 5.
- A setup or run id the server no longer knows (it restarted): 404 `not_found` with a plain sentence, and the
  page returns to the choice with that sentence as a notice, never a stuck screen. Tests in Tasks 4 and 8.
- A second job while one runs (a run during a build, past `max_running`): 429 `busy`, shown as the job panel's
  message; nothing half-started. Test in Task 5.
- Send to fill with more than 10,000 result rows: the dataset keeps the first `MAX_ROWS`, `truncated` is true and
  the page says so. Test in Task 5.

---

## File structure

```
demo/server/
  extraction.py     Batches, Setups, ExtractionStore, Runs (managers; no HTTP)
  extract_api.py    add_extract_routes(api): the routes below
  jobs.py           + Jobs.start_task(kind, runner, stages)
  documents.py      page_png(pdf, n, dpi) as a module function (Doc.page_png uses it)
  api.py            + api.batches/.setups/.extractions/.runs, capability "extract", _uploads(req), routes registered
  settings.py       + folder_scan, extract_workers
src/formcast/extract/
  scan.py           + scan(on_progress=)
  batch.py          + run_batch(should_stop=), Results.field_counts(), Results.page()
demo/web/workbench/
  index.html        + #choose, #extract, #building overlays; the setup and review views; toolbar groups
  extract.css       the mockup's extraction classes, on the workbench tokens
  extract-model.js  pure logic (node-tested)
  extract.js        mountExtract(app): choice, Extract modal, build job
  extract-setup.js  mountSetup(app): fields, sample page, inspector, add a field, save
  extract-review.js mountReview(app): run, review, export, send to fill
  workbench.js      wiring; start.js: showStart({dataset, form})
demo/web/engines/server/client.js   + engine.extract methods
demo/web/core/engine.js             + "extract" in OPTIONAL_METHODS
tests/demo/test_server_extract.py, tests/demo/test_workbench_extract.py, tests/web/extract_model.test.js
```

## The HTTP shapes (shared by Tasks 3-9)

```
POST /api/batches                {name?} | {folder}           -> Batch
POST /api/batches/{b}/files      multipart, parts named file  -> Batch       (PDFs and .zip files)
GET  /api/batches/{b}                                         -> Batch
GET  /api/batches/{b}/files?offset=&limit=                    -> {total, files: [{index, name, size, group}]}
POST /api/batches/{b}/sample     {n, how: spread|first|pick, picked?: [index]} -> {samples: [{index, name, group}]}
GET  /api/batches/{b}/files/{i}/pages/{p}.png?dpi=            -> image/png
POST /api/jobs {kind:"extract-setup", batch, samples:[index], spec:{name, start, lines, destination (a fill template
     name), detect, pages, ocr, sample_how}}                  -> {id}; done job result {setup: Setup}
GET  /api/setups/{s}                                          -> Setup
POST /api/setups {extraction: name}                           -> Setup (a saved template reopened)
POST /api/setups/{s}/edit        {edit: Edit}                 -> Setup
GET  /api/setups/{s}/samples/{i}/marks                        -> {marks: [Mark]}
GET  /api/setups/{s}/samples/{i}/pages/{p}.png?dpi=           -> image/png
POST /api/jobs {kind:"extract-line", setup, line}             -> {id}; done job result {setup: Setup, paths}
GET  /api/extractions                                         -> {extractions: [{name, template_id, fields, samples, updated}]}
GET/DELETE /api/extractions/{name}                            -> {template} / {name}
PUT  /api/extractions/{name}     {setup}                      -> {name, template_id}
POST /api/jobs {kind:"extract-run", extraction: name, batch, ai: bool} -> {id}; result {run, done, total, summary}
GET  /api/runs                                                -> {runs: [{id, name, extraction, started, summary}]}
POST /api/runs                   {folder}                     -> Run (a command line run opened)
GET  /api/runs/{r}                                            -> Run
GET  /api/runs/{r}/problems?path=&status=&offset=&limit=      -> {total, documents: [{doc_id, name, problems: [Problem]}]}
GET  /api/runs/{r}/set-aside?offset=&limit=                   -> {total, documents: [{doc_id, name, reason, closest_template, closest_score}]}
POST /api/runs/{r}/decisions     {doc_id, path, decision, value?} -> Run
GET  /api/runs/{r}/pages.png?doc=&page=&dpi=                  -> image/png (a document of the run)
GET  /api/runs/{r}/export?format=xlsx|parquet|csv             -> file (.xlsx, or .zip for parquet/csv)
POST /api/runs/{r}/to-data                                    -> {dataset: Dataset.summary(), truncated}

Batch   = {id, name, count, bytes, skipped, folder: bool, scan: {phase: scanning|done|error, done, total, error,
           groups: [{group, size, fillable, flattened, closeness}]}}
Setup   = {id, batch, template, proof, warnings, samples: [{index, name, kind, pages: [[w, h]]}]}
Run     = {id, name, extraction, summary: RunSummary, fields: [{path, title, found, missing, failed_check, unsure,
           reviewed, not_on_form}], seconds}
Problem = {path, title, status, raw, value, reasons, page, rect}
```

---

### Task 1: Library hooks the workbench needs

**Files:**
- Modify: `src/formcast/extract/scan.py`, `src/formcast/extract/batch.py`
- Test: `tests/extract/test_scan.py`, `tests/extract/test_batch.py`

**Interfaces:**
- Produces:
  - `scan(paths, *, same=SCAN_SAME, first_page_only=False, on_progress: Callable[[int, int], None] | None = None) -> ScanReport` — called after each document as `(done, total)`.
  - `run_batch(..., should_stop: Callable[[], bool] | None = None)` — checked before each submit; when true the run
    stops submitting, lets in-flight documents finish and be written, and returns the summary (a resume carries on).
  - `Results.field_counts() -> dict[str, dict[str, int]]` — per concrete path, counts of `found`, `missing`,
    `failed_check`, `unsure`, `absent`, `not_on_form` after decisions and AI answers, plus `reviewed` (paths with a
    decision); failed documents not counted.
  - `Results.page(offset: int, limit: int, *, path: str | None = None, status: Status | None = None) -> tuple[int, list[tuple[DocumentResult, list[FieldResult]]]]`
    — the documents with problems (filtered as `problems`), in `doc_id` order, as `(total documents, this page)`.

- [ ] **Step 1: Write the failing tests:** `on_progress` sees `(1, n) ... (n, n)` for an n-file scan;
  `run_batch(workers=1, should_stop=lambda: calls >= 3)` stops after 3 documents written, `Results.summary().total
  == 3`, and a resume without `should_stop` finishes the rest with no document twice; with `workers=2` a stop
  leaves no torn line; `field_counts()` on the corpus run gives `investor.tax_id`'s counts summing to the read
  documents and `reviewed == 1` after one decision; `page(0, 2)` returns the first two problem documents and the
  total, `page(2, 2, status="missing")` the next two of the filtered set.
- [ ] **Step 2: Run them to verify they fail** (`pytest tests/extract/test_scan.py tests/extract/test_batch.py -q`).
- [ ] **Step 3: Implement** the four, documenting each in its module docstring's matching paragraph.
- [ ] **Step 4: Run** `pytest tests/extract -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Extract: scan progress, a stoppable run, per-field counts and paged problems"`

---

### Task 2: Server foundations: task jobs, settings, the capability, uploads of several files

**Files:**
- Modify: `demo/server/jobs.py`, `demo/server/settings.py`, `demo/server/api.py`, `demo/server/documents.py`
- Create: `demo/server/extraction.py` (empty managers' module docstring only, filled in Tasks 3-5),
  `demo/server/extract_api.py` (`add_extract_routes(api)` registering nothing yet)
- Test: `tests/demo/test_server_extract.py`, `tests/demo/test_server_documents.py` (capabilities),
  `tests/demo/test_server_jobs.py` (the `bad_kind` text)

**Interfaces:**
- Produces:
  - `Jobs.start_task(kind: str, runner: Callable[[Job], None], *, stages: Sequence[tuple[str, str]] = ()) -> Job`
    — same slot accounting as `start` (`JobsBusy` past `max_running`); `job.stages` from `stages` (all
    `pending`); the runner's return ends the job `done`; `SessionCancelled` ends it `cancelled`; `FormcastError`
    ends it `error` with its message (redacted); anything else the generic message, traceback to the log only; open
    stages closed as `_finish` does today.
  - `Settings.folder_scan: bool = True` (env `FORMCAST_DEMO_FOLDER_SCAN`, `parse_bool`), `Settings.extract_workers:
    int = 0` (env `FORMCAST_DEMO_EXTRACT_WORKERS`, a whole number ≥ 0).
  - `capabilities()["extract"] = True`.
  - `_uploads(req) -> list[tuple[str, bytes]]` — every multipart part named `file` (400 `bad_upload` when none).
  - `page_png(pdf: bytes, n: int, dpi: int) -> bytes` in `documents.py` (holds `PYMUPDF_LOCK`; `IndexError` for a page
    out of range); `Doc.page_png` calls it.
  - `api.batches`, `api.setups`, `api.extractions`, `api.runs` attributes (Tasks 3-5 make them real), and
    `add_extract_routes(self)` called at the end of `Api._add_routes` (before nothing; the new paths do not overlap).
  - `create_job` accepts kinds `extract-setup`, `extract-line`, `extract-run` (handled in `extract_api`, which
    `create_job` delegates to for these kinds); the `bad_kind` detail lists every kind.

- [ ] **Step 1: Write the failing tests:** a task job whose runner sets `job.stage("a", "done")` ends `done` with
  that stage; one raising `SessionCancelled` ends `cancelled`; one raising `FormcastError("no")` ends `error` with
  `"no"`; one raising `RuntimeError` ends `error` with the generic message and nothing of the exception text;
  `max_running` task jobs make the next `start_task` raise `JobsBusy`; `GET /api/capabilities` includes
  `"extract": true` (update the verbatim dict test); `Settings.from_env({"FORMCAST_DEMO_FOLDER_SCAN": "off",
  "FORMCAST_DEMO_EXTRACT_WORKERS": "2"})` gives `False, 2`, and `"-1"` raises `ValueError` without echoing it;
  `POST /api/jobs {"kind": "nope"}` gives 400 `bad_kind` naming the extract kinds.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/demo -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench server: task jobs, extraction settings and capability, several files per upload"`

---

### Task 3: Batches: upload, zip, server folder, scan, sample, pages

**Files:**
- Modify: `demo/server/extraction.py`, `demo/server/extract_api.py`
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Consumes: `scan`, `sample`, `ScanReport` (formcast.extract.scan, Task 1); `page_png`; `_uploads`.
- Produces in `extraction.py`:
  - `MAX_ZIP_BYTES = 2 * 2**30` (uncompressed, per zip), `MAX_BATCH_FILES = 200_000`, `FIRST_PAGE_ONLY_FROM = 2_000`
    (files; from this count the scan reads first pages only), `PAGE_LIMIT = 200`.
  - `BatchFile(index: int, name: str, path: Path, size: int)`; `Batch` with `id, name, files, skipped, folder,
    scan state`, `summary() -> dict` (the `Batch` shape above), `group_of(index) -> int | None`.
  - `Batches(root: Path)` (`root = <data>/local/batches`): `create(name) -> Batch`, `add(batch, uploads:
    list[tuple[str, bytes]]) -> None` (a `.pdf` is stored as `files/<index>.pdf`; a `.zip` is opened with
    `zipfile`, its entries ending `.pdf` (any folder, case-insensitive) stored the same way under their base name,
    other entries counted in `skipped`; refused with `FormcastError` before writing when the sum of `file_size`
    passes `MAX_ZIP_BYTES` or the batch would pass `MAX_BATCH_FILES`; anything else counted in `skipped`),
    `from_folder(folder: Path, name) -> Batch` (every `*.pdf` under it, recursively, sorted, referenced in place, not
    copied), `start_scan(batch)` (a thread; a new upload restarts it), `get(id) -> Batch` (`KeyError`),
    `pick(batch, n, how, picked) -> list[int]` (3 ≤ n ≤ 20 unless `picked`; `spread`/`first` over the largest scan
    group with `sample()`; `pick` returns `picked` as given, each a valid index).
- Routes: the batch routes of *The HTTP shapes*. `POST /api/batches {folder}`: 403 `not_allowed` when
  `settings.folder_scan` is false; 400 `bad_folder` when it is not an existing directory or holds no PDF. The
  multipart route's body limit is `settings.max_upload_bytes` (the page sends files in several requests).

- [ ] **Step 1: Write the failing tests** (HTTP against `start_demo_server`):
  - upload three corpus PDFs in one request and a zip of two more in a second: `count == 5`, the scan reaches
    `phase == "done"` with `done == total == 5` and one group of size 5 (wait with a polling helper as
    `test_server_jobs.wait_for`);
  - a zip with entries `../evil.pdf`, `/abs.pdf`, `sub/ok.pdf`, `notes.txt`: three PDFs stored as `files/<n>.pdf`
    under the batch folder (nothing written outside it; names shown are `evil.pdf`, `abs.pdf`, `ok.pdf`),
    `skipped == 1`;
  - a zip whose entries declare more than `MAX_ZIP_BYTES` (monkeypatch the constant small): 400 `bad_upload`,
    the batch unchanged;
  - `POST /api/batches {folder: tmp}` scans in place; with `folder_scan=False` 403 `not_allowed`; a missing folder
    400 `bad_folder`;
  - `GET .../files?offset=1&limit=2` gives `total == 5` and two files; `limit=500` gives 400 `bad_request`;
  - `POST .../sample {n: 3, how: "spread"}` returns three distinct indexes; `n: 2` gives 400; `how: "pick"` with
    `picked: [0, 4, 9]` gives 400 (9 is no file);
  - `GET .../files/0/pages/0.png` is a PNG; page 99 is 404; an unknown batch is 404 `not_found` "no such batch".
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement** the manager and routes.
- [ ] **Step 4: Run** `pytest tests/demo/test_server_extract.py -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench server: batches of PDFs from uploads, zips or a folder, scanned by layout"`

---

### Task 4: Setups and saved extraction templates

**Files:**
- Modify: `demo/server/extraction.py`, `demo/server/extract_api.py`
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Consumes: `build_setup`, `add_line`, `SetupSpec`, `STAGES`, `FIELDS_LABELS`, `SamplePdf`, `read_samples`,
  `prove`, `apply_edit`, `Edit`, `sample_marks` (plan 2); `api.store` (fill templates, for a destination's
  schema); `api.provider_factory`; the documents' detectors (`api.documents` has the warm-up the form reading
  uses; pass the same `detectors` the documents use, so tests' `no_model_loads` applies).
- Produces in `extraction.py`:
  - `Setup(id, batch_id, template: ExtractionTemplate, samples: list[SampleDoc], indexes: list[int], proof: Proof, warnings: list[str])`
    with `summary() -> dict` (the `Setup` shape); `Setups(limit=8)` in memory, LRU: `add`, `get` (`KeyError`).
  - `ExtractionStore(folder: Path)` (`folder = <data>/local/extractions`): `list()`, `get(name) ->
    ExtractionTemplate`, `samples(name) -> list[SamplePdf]`, `put(name, template, samples: list[SamplePdf])` (the
    template as `<name>.json`, samples as `assets/<name>/samples/<i>.pdf`, written with `_write`), `delete(name)`;
    names checked with `check_name` (400 `bad_name`); a bad stored file raises `StoredTemplateError`.
- Routes and jobs: the setup and extraction routes of *The HTTP shapes*.
  - `extract-setup` job: 400 `ai_not_configured` with no provider; 400 for a bad spec, unknown batch, or sample
    indexes not in the batch (fewer than 3 unless the batch has fewer files); 404 for an unknown destination
    template. Stages are plan 2's `STAGES` with the `fields` label from `FIELDS_LABELS[start]`; `on_stage` sets
    them and raises `SessionCancelled` when the job's cancel was asked; the result is `{setup: Setup.summary()}`.
  - `extract-line` job: `add_line` on the setup's template and samples; replaces the setup's template and proof.
  - `POST /api/setups/{s}/edit`: `apply_edit` then `prove` (no model); 400 `bad_edit` with the library's message.
  - `PUT /api/extractions/{name} {setup}`: the template renamed to `name` (its `name` field; `template_id` kept)
    and saved with the setup's sample PDFs. `POST /api/setups {extraction}` reopens one: `read_samples(detect="off")`
    of its stored samples, `prove`.

- [ ] **Step 1: Write the failing tests** (`CorpusModel` as `provider_factory`, five corpus PDFs uploaded):
  - an `extract-setup` job with `start: "ai"` ends `done`; its stages in order are the five keys, all `done`; the
    result's `setup.proof.ready == setup.proof.total`; `GET /api/setups/{id}` gives the same body;
  - `start: "destination", destination: "<a shipped fill template name>"`: absent paths are the destination's
    paths CorpusModel says are not on the form;
  - an edit `{"op": "set_labels", "path": "investor.tax_id", "labels": []}` returns a setup whose reader for that
    path has no anchor and whose proof is recomputed (no model call made: `CorpusModel.calls` unchanged);
  - `GET .../samples/0/marks` lists marks with `kind` and `rect`; `GET .../samples/0/pages/0.png` is a PNG;
  - an `extract-line` job with `line: "wire details"` adds `wire.bank`;
  - `PUT /api/extractions/Client%20info {setup}` then `GET /api/extractions` lists it with 5 samples; `POST
    /api/setups {extraction: "Client info"}` gives a setup with the same template and a fresh proof;
    `PUT /api/extractions/..%2Fx` gives 400 `bad_name`;
  - without AI configured the setup job is 400 `ai_not_configured`; an unknown setup id is 404 "no such setup";
  - cancelling during the `fields` stage (a `Gate` inside the fake) ends the job `cancelled`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/demo -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench server: build, edit, re-prove and save extraction templates"`

---

### Task 5: Runs: run on all, review, export, send to fill

**Files:**
- Modify: `demo/server/extraction.py`, `demo/server/extract_api.py`
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Consumes: `run_batch(should_stop=)`, `Results` (`load`, `summary`, `field_counts`, `page`, `decide`,
  `effective`, `documents`, `check_templates`), `ReviewDecision`, `export`, `ai_pass`, `concrete_paths`;
  `api.datasets.add_table`, `formcast.data.table.Table`, `MAX_ROWS`.
- Produces in `extraction.py`:
  - `Run(id, name, extraction: str, out_dir: Path, templates: list[ExtractionTemplate])` with `summary() -> dict`
    (the `Run` shape; titles from the schema); `Runs(root: Path)` (`root = <data>/local/runs`; each run is
    `runs/<id>/` holding `run-meta.json` `{name, extraction, batch, started}` and the `run_batch` files): `create`,
    `get(id)` (reads `run-meta.json` lazily; `KeyError`), `list()`, `open_folder(folder, extractions)` (a command
    line run: its `run.json` names template ids that must be saved extractions with the same sha256, else 400
    `bad_run` naming the id; registered in memory under a new id, not copied).
- Routes and jobs: the run routes of *The HTTP shapes*.
  - `extract-run` job: stages `("read", "Read every document")` and, when `ai`, `("ai", "Ask the AI about the
    failing documents")`; `run_batch(workers=settings.extract_workers, should_stop=lambda: job.cancel_requested,
    on_progress=...)` with the result `{run, done, total}` updated on progress and `summary` at the end; a cancel ends
    it `cancelled` with its run kept (a later run of the same batch and extraction resumes it: reuse the run whose
    meta matches). `ai` needs a provider (400 `ai_not_configured` before starting).
  - `GET /api/runs/{r}/problems`: `limit` 1-200 (default 50), statuses as `Results.problems` takes; each problem's
    `title` from the schema; `name` is the document's file name.
  - `GET /api/runs/{r}/set-aside`: `Results.set_aside()` paged the same way (`name` the file name).
  - `POST /api/runs/{r}/decisions`: a `ReviewDecision`; 400 `bad_decision` with the library's message.
  - `GET /api/runs/{r}/pages.png?doc=<doc_id>&page=n`: only a `doc_id` of the run (404 otherwise); the PDF read from
    the result's `file`.
  - `GET /api/runs/{r}/export`: `export(results, run.templates, tmp, format=...)`; xlsx sent as
    `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, Parquet and CSV folders zipped
    (`application/zip`); `Content-Disposition: attachment; filename="<name>-results.<ext>"`; Parquet without
    `pyarrow` is 400 `not_supported` with the library's message.
  - `POST /api/runs/{r}/to-data`: a `Table(columns=<the extraction's concrete paths not absent>, rows=<each read
    document's effective values by path, decisions and AI answers applied>, kind="json", name="<name> results")`, at
    most `MAX_ROWS` rows (`truncated` true when cut), added with `api.datasets.add_table`; the response is
    `{dataset: summary, truncated}`. Columns named by schema path let the fill flow's `map_dataset` choose the direct
    mapping when the destination has those paths, else a saved or AI mapping, as for any dataset.

- [ ] **Step 1: Write the failing tests** (a saved extraction from Task 4's flow; a batch of the 22 corpus folder
  PDFs):
  - an `extract-run` job ends `done` with `summary.total == 22` and `done == total`; `GET /api/runs` lists it;
    `GET /api/runs/{r}` gives per-field counts;
  - `GET .../problems?limit=2` gives `total` and at most two documents, each with its problems; `offset` past the end
    gives an empty page; `limit=0` and `limit=201` give 400;
  - `set-aside` lists the corpus folder's W-9 copies with reason "no template fits" and their closest score;
  - a decision `{"decision": "value", "value": "12-3456789"}` on one problem: the next `problems` call no longer has
    it and `summary.with_problems` drops by one when it was that document's only problem;
  - `pages.png?doc=<a doc_id>&page=0` is a PNG; `doc=../etc/passwd` is 404;
  - `export?format=xlsx` is a workbook with the `values` sheet (open with `openpyxl`); `format=csv` is a zip holding
    `values.csv`; `format=pdf` is 400;
  - `to-data` gives a dataset whose columns are the concrete paths and whose rows are the read documents; with
    `MAX_ROWS` monkeypatched to 5 it has 5 rows and `truncated: true`;
  - a second `extract-run` while two jobs hold the slots (a `Gate`) is 429 `busy`;
  - a run cancelled after three documents ends `cancelled`; starting it again resumes (22 documents, none twice);
  - `POST /api/runs {folder}` on a folder written by `python -m formcast.extract run` with the saved template's JSON
    opens it; with another template it is 400 `bad_run`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/demo -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench server: run an extraction on a batch, review it, export it, send it to fill"`

---

### Task 6: Page foundations: the engine's extraction methods, the pure logic, the choice

**Files:**
- Modify: `demo/web/engines/server/client.js`, `demo/web/core/engine.js`, `demo/web/workbench/index.html`,
  `demo/web/workbench/workbench.js`, `demo/web/workbench/start.js`, `demo/web/workbench/jobs.js`
- Create: `demo/web/workbench/extract-model.js`, `demo/web/workbench/extract.css`, `demo/web/workbench/extract.js`
- Test: `tests/web/extract_model.test.js`, `tests/web/server_client.test.js`, `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Produces:
  - `engine.extract` (present when capability `extract`): `createBatch({name}|{folder})`, `addFiles(batchId, files,
    {onProgress})` (sends `File`s in requests of at most 40 MiB each), `batch(id)`, `files(id, offset, limit)`,
    `pickSample(id, {n, how, picked})`, `pageUrl(batchId, index, page)`, `startSetup(body) -> JobHandle`,
    `setup(id)`, `reopen(name)`, `edit(id, edit)`, `marks(id, sample)`, `samplePageUrl(id, sample, page)`,
    `startLine(id, line) -> JobHandle`, `save(name, setupId)`, `list()`, `remove(name)`, `startRun({extraction,
    batch, ai}) -> JobHandle`, `runs()`, `openRun(folder)`, `run(id)`, `problems(id, {path, status, offset, limit})`,
    `decide(id, decision)`, `runPageUrl(id, docId, page)`, `exportUrl(id, format)`, `toData(id)`. Errors are
    `EngineError` as for every engine call.
  - `extract-model.js` (no DOM): `fieldState(fieldProof) -> {glyph, text, state}`: an absent field is
    `{glyph: "○", text: "not on this form", state: "absent"}`; otherwise `text` is `"<agree or failed_check
    samples>/<samples>"` and `state` is `ready` when `fieldProof.ready` else `weak`; `glyph` is `●` when every sample
    is `agree`/`failed_check`, `?` when not ready and a sample's read is `unsure`, `◐` when at least one sample is
    `agree`/`failed_check`, else `×`. So `[agree ×5]` -> `● 5/5 ready`; `[agree ×3, failed_check, missing]` (ready)
    -> `◐ 4/5 ready`; `[agree ×4, disagree]` -> `◐ 4/5 weak`; `[disagree ×5]` -> `× 0/5 weak`;
    `readFrom(reader, schemaProp) -> string[]` (one line per strategy: `"Form field tax_id"`, `"A box on page 1"`,
    `"Next to the label Taxpayer ID"`, `"Below the label Mailing address"`, `"Page text, after account"`,
    `"Checkbox group, 4 boxes"`); `checksOf(schemaProp, required) -> string[]` (`"Required"`, `"Format <pattern>"`,
    `"Is a date"`, `"One of a, b, c"`, `"Between 0 and 100"`); `outcomeLine(sampleProof) -> {glyph, text}`
    (`● value`, `× <first reason or "missing">`, `○ not on this form`); `groupFields(proof, schema) -> [{group,
    items: [{path, title, generic, items}]}]` (a repeated group's item paths folded under its generic path with
    `items` = maxItems, as the mockup's `owners[ ] · up to 4`); `problemLabel(status)` (`Missing`, `Fails a check`,
    `Unsure`); `batchLine(batch) -> string` (`"1,248 PDFs · 412 MB · 1,163 fillable or flattened copies of one
    layout, 85 set aside"`); `stripCells(batch, sampleIndexes, max = 120) -> [{state: sample|main|flat|other}]`.
  - `#choose` overlay (`.choice[data-go="fill"]`, `.choice[data-go="extract"]`), shown at load instead of `#start`;
    *Fill a PDF* opens `#start` unchanged; the toolbar's `#doc-name` opens `#choose`; `#start`'s Back (`#start-close`)
    returns to `#choose` when nothing is open. `body[data-mode="fill"|"extract"]` switches the toolbar groups and main
    views (extract views are filled in Tasks 7-9).
  - `start.js`: `showStart({dataset, form} = {})` — `dataset` (a `Dataset.summary()`) becomes *Your data* (shown by
    name with a *Clear* button; `Open` uses it instead of uploading), `form` preselects `#form-pick`.
  - `jobs.js`: export `showStages(el, job)` for the extraction build panel.

- [ ] **Step 1: Write the failing tests:** node tests for every `extract-model.js` function with the mockup's
  sample data shapes, including the four `fieldState` cases above, `readFrom` for each strategy kind, and
  `groupFields` folding `owners[0..3].name` into one row `owners[ ] · up to 4`; `server_client.test.js`: `engine.extract.addFiles` splits 3 files of 30 MiB
  into requests under 40 MiB and posts to `/api/batches/<id>/files`; Playwright: the workbench opens on
  `#choose` with both choices; *Fill a PDF* shows `#start` and the existing flow still works (run the existing
  `bench.upload` path from it); with a server whose capabilities lack `extract` (monkeypatch `Api.capabilities`)
  only *Fill a PDF* is shown; `#doc-name` reopens `#choose`.
- [ ] **Step 2: Run them to verify they fail** (`pytest tests/web/test_node_suite.py tests/demo/test_workbench_extract.py -q`).
- [ ] **Step 3: Implement.** Update the existing page tests that expect `#start` at load to click *Fill a PDF*
  first (one helper in `Bench.goto`, `fill=True` by default), not each test.
- [ ] **Step 4: Run** `pytest tests/web tests/demo -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench page: open on Fill or Extract; the engine's extraction calls"`

---

### Task 7: The Extract modal and building the template

**Files:**
- Modify: `demo/web/workbench/index.html`, `demo/web/workbench/extract.js`, `demo/web/workbench/extract.css`
- Test: `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Consumes: `engine.extract` (Task 6); `GET /api/templates` through the engine's existing template list call (the
  *Destination* list).
- Produces: `#extract` overlay (`.sheet.wide`) with the mockup's four groups and ids (`#drop` with a multiple file
  input and a folder input (`webkitdirectory`), `#folder` + `#scan`, `#batch` / `#batch-sum` / `#batch-strip` /
  `#batch-note`; `#n-sample` (3-20, default 5), `#sample-how`, `#picklist` (paged 200 at a time, a *More* button);
  `.seg [data-mode=ai|user|dest]` and `#mode-detail` (a textarea `#lines` for *I'll list them*, a select
  `#dest-pick` for *Destination*); `<details>` with `#detect`, `#ocr`, `#unsure` (checked, informational this
  round), `#ai-fallback`); `#extract-msg`; Back; `#read-sample`). `#building` overlay: `ol.stages#build-stages`,
  `#build-readout`, Cancel. `#unsure` is shown checked and disabled with the note "Always on in this version":
  plan 1's reader has no switch for the unsure rule, and turning it off would let weaker reads pass as found. On
  *Read the sample →*: no batch -> `#extract-msg` "Add the documents first."; else
  `pickSample`, `startSetup`, stages shown live with `showStages`; done -> `app.extract.openSetup(setup)` (Task 8
  provides it; until then the test checks the event `extract:setup` on the bus); error -> back to `#extract` with
  the message; Cancel -> cancel and back to `#extract`.

- [ ] **Step 1: Write the failing tests** (Playwright; `CorpusModel` server; five corpus PDFs on disk):
  `#extract` opens from `#choose`; dropping or choosing the five files shows `#batch-sum` with "5 PDFs" and five
  strip cells after the scan; `#n-sample` refuses 2 (`#extract-msg` says 3 to 20); *I'll pick them* lists the files
  with checkboxes; *Destination* lists the shipped fill templates; *Read the sample* with nothing added says so;
  with the batch, the building panel shows the five stages turning done and then the setup opens (`extract:setup`
  fired with `proof.ready == proof.total`); the folder path field scans a server folder (the tmp folder of
  corpus PDFs); with `folder_scan=False` the error detail shows in `#extract-msg`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/demo/test_workbench_extract.py -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench page: the Extract modal, the batch summary and building the template"`

---

### Task 8: The setup screen

**Files:**
- Modify: `demo/web/workbench/index.html`, `demo/web/workbench/extract.css`, `demo/web/workbench/workbench.js`
- Create: `demo/web/workbench/extract-setup.js`
- Test: `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Consumes: `engine.extract.setup/edit/marks/samplePageUrl/startLine/save/reopen`, `extract-model.js`, `PageView`
  (`demo/web/core/pageview.js`: a second instance on the setup view's page element, fed a template-shaped object
  `{fields: [{id, type: "text", page, rect, source: {paths: [path]}, origin: "human"}]}` built from the marks: one
  field per `box` and `option` mark (id `"<path>#box"` / `"<path>#opt:<answer>"`, movable), and per `read` and
  `label` mark (id `"<path>#read"` / `"<path>#label"`), whose moves are ignored).
- Produces: `app.extract.openSetup(setup)`; the setup view (`.view[data-only=setup]`): toolbar (`#doc-pick` with
  the batch name, size and sample count, opening `#choose`; `.seg [data-view=setup|review]`; *Save*; `#run`
  *Run on all →*), `#strip` (start, sample, detection, with the lines or destination per the mockup, and
  `#change` reopening `#extract` with the current choices), `#fields` (from `groupFields`, each row with
  `fieldState`; click selects), `#add` + `#add-go` (an `extract-line` job; the new field selected when done), the
  page bar (`#prev-s`, `#sample-no`, `#next-s`, `#sample-kind`, pages, zoom with fit width / fit page as the
  mockup), the page with boxes (selected field's marks heavy with its path tag), `#ins` (name, title, definition,
  type, *Read from* lines from `readFrom`, answers for a choice field, checks with *Required* toggle and pattern,
  labels editable as a comma list, *Not on this form* / *Bring back*, *Remove field*), `#per-sample` (one
  `outcomeLine` per sample; clicking one shows that sample). Every change is one `edit` call; the response's setup
  replaces the screen's state (fields, page, inspector, per-sample) with the selection kept. *Save* asks for a
  name in the existing `#save-box` style (default the setup's name) and calls `save`. A 404 on any setup call
  returns to `#choose` with the notice "This setup is no longer on the server; start again."

- [ ] **Step 1: Write the failing tests** (Playwright, after Task 7's flow): `#fields` lists the corpus fields with
  `● 5/5`; selecting `investor.tax_id` heavy-draws its box with the path tag and fills `#ins` with "Form field
  tax_id" and "Next to the label …"; dragging its box on a flattened sample sends one `set_box` edit and the proof
  updates; editing the definition persists after reselecting; *Not on this form* moves the field to `○` and the
  per-sample list says "not on this form"; *Add a field* with "phone number" adds a field (CorpusModel answers);
  ‹ › walks the five samples; *Save* stores it (`GET /api/extractions` lists it); after the server forgets the
  setup (clear `api.setups`), an edit returns to `#choose` with the notice; a setup built with *I'll list them*
  shows its lines in `#strip` and one field per line's paths; one built from a *Destination* names the template in
  `#strip` with "N of its fields are not on this form" and lists those fields as `○`; no page errors.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/demo/test_workbench_extract.py tests/demo/test_workbench_page.py -q`; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench page: the extraction setup screen"`

---

### Task 9: Run on all, review, export, send to fill

**Files:**
- Modify: `demo/web/workbench/index.html`, `demo/web/workbench/extract.css`, `demo/web/workbench/workbench.js`,
  `README.md` (the workbench section: extraction), `demo/README.md` if it exists
- Create: `demo/web/workbench/extract-review.js`
- Test: `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Consumes: `engine.extract.startRun/run/problems/decide/runPageUrl/exportUrl/toData/runs/openRun`;
  `showStart({dataset, form})` (Task 6); `extract-model.js`.
- Produces: *Run on all →* saves first when the setup is unsaved (the same name prompt), then starts
  `extract-run` with `#ai-fallback`'s setting, shown in the job panel with `done / total` under the stage; done ->
  the review view. Review (`.view[data-only=review]`): `.stats` (documents read, complete, with problems, set
  aside, time) from `Run.summary`, filters (field, problem, and *Set aside*, which lists `set-aside` documents with
  their reason and closest template instead of problems) re-querying `problems`, `#docs` (50 per page, *More*),
  `#docs-legend`, the selected document's page image (`runPageUrl`) with the problem's `rect` outlined, `#problems`
  cards (field title, status label, what was read, reasons) with *Use this value* (a text input), *Accept as read*
  (only when a value was read), *Not in this document*; each decision updates the card, the stats and the list.
  Toolbar in review: *Export xlsx*, *Export Parquet* (hidden when the server says Parquet is not available: a 400
  `not_supported` turns it into a notice), *Send to fill →* (`toData`, then `showStart({dataset, form: the
  extraction's destination when it has one})` with a notice when `truncated`). `#choose` lists saved extractions and
  past runs under *Extract from PDFs* ("Open a saved run") so a run can be reviewed again later, and the command
  line run folder can be opened by path there.

- [ ] **Step 1: Write the failing tests** (Playwright, after Task 8's flow, the batch being the 22 corpus folder
  PDFs): *Run on all* ends in the review view with stats summing to 22; filtering by *Missing* lists only missing
  problems; choosing a document shows its page; *Use this value* settles a problem and the count drops; *Export
  xlsx* downloads a file whose first sheet is `values`; *Send to fill* opens `#start` with the dataset named "...
  results" as *Your data*; reopening the page, *Open a saved run* lists the run and opens its review; no page errors.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.** README: how to extract in the workbench (the four steps and what each screen does).
- [ ] **Step 4: Run** the full suite (`pytest -q`) and the node suite; expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "Workbench page: run an extraction, review it, export it and send it to fill"`
