# formcast Plan 3B — Template reuse and the local workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local review app ("workbench", layout B) that developers clone as a starting point: build a template with the AI or by hand, edit and revise it, reuse templates for forms seen before, fill records from CSV or SQLite and download the PDF with its report; plus the library pieces it needs (`similarity`, `rebind`, session progress and cancel).

**Architecture:** Library additions in `src/formcast/` (form signature in the page map and template, `similarity`, `rebind`, `on_step`/`SessionCancelled`). A small stdlib Python app in `demo/server/` (settings, template store, record sources, documents, AI jobs, JSON API) serves `demo/web/` and wraps the library. A server engine (`demo/web/engines/server/`) implements Plan 3A's engine interface over HTTP; the workbench page (`demo/web/workbench/`) arranges Plan 3A's UI parts in three columns and adds AI, matches, store and records.

**Tech Stack:** Python 3.12 (formcast, stdlib `http.server`, `sqlite3`, `csv`, `threading`), vanilla ES modules, Plan 3A's core parts, Python Playwright.

**Spec:** `docs/superpowers/specs/2026-10-21-formcast-review-apps-design.md` (§7.1, §9, §11, §12). Plan 3A (`docs/superpowers/plans/2026-10-21-formcast-browser-demo.md`) must be complete first; its Interfaces blocks define the core parts used here.

## Global Constraints

- Everything in Plan 3A's Global Constraints applies (vanilla JS, no build step, no frameworks, Python is the reference, no paid model calls, no held-out use, no form-specific code, don't read `.env` from tests or print keys, commits end with the Co-Authored-By line, no push, branch `formcast-plan3` in `.claude/worktrees/plan3`).
- Server binds `127.0.0.1` by default (port 8765); never `0.0.0.0` unless the developer passes `--host`. API keys never leave the server; no response body contains them.
- No new runtime dependencies for the app layer (stdlib only). `python -m demo.server` starts it from the repo root.
- Uploads capped at 50 MB; JSON request bodies capped at 20 MB.
- The library only scores similarity; thresholds and actions live in `demo/server/settings.py` (defaults: reuse ≥ 0.95, adapt ≥ 0.60), calibrated on dev forms only.
- Old templates (no `signature`) and browser templates (`fingerprint: None`) must keep loading and filling (with `check_fingerprint=False` or after `rebind`).
- Agent flows in tests use the existing scripted fake provider (`tests/agent/` helpers); no network.
- The README says plainly: run the workbench on your own machine only; the agent's sandbox is not a security boundary.

## Review Focus

- **Concurrent requests** (a fill while a build job runs; two tabs): the threaded server must not share mutable per-request state; documents and jobs are guarded by locks. Owned by Task 5 (two simultaneous page-image requests and an apply during a running fake job).
- **A template whose PDF changed revision** (same layout, different footer text): `similarity` lands in the adapt band, not reuse, and `fill` with the old fingerprint refuses until rebind. Owned by Task 1 (calibration case: edit one text line on a dev PDF with PyMuPDF) and Task 6 (matches route).
- **Malformed records** (CSV row with a bad JSON array cell, SQLite NULLs, extra columns): the record source returns what it can and the fill report says what's wrong (`invalid`, `unknown_keys`), never a 500. Owned by Task 4.
- **A job cancelled or failing mid-run** (provider error, cancel): the job ends `cancelled`/`error` with its partial template available, the sandbox is closed, the UI leaves the progress state. Owned by Task 3 (library) and Task 7 (jobs).
- **Path traversal in template names and uploads** (`../`, absolute paths, odd unicode): rejected with 400. Owned by Task 4 (store) and Task 5 (documents).

---

## File structure

```
src/formcast/match.py              + FormSignature, compute_signature, similarity, Similarity (Task 1)
src/formcast/pagemap/model.py      FormMap.signature (Task 1)
src/formcast/pagemap/build.py      compute signature (Task 1)
src/formcast/template/model.py     FormInfo.signature; new_template copies it (Task 1)
src/formcast/template/rebind.py    rebind, form_info_for (Task 2)
src/formcast/agent/api.py          use form_info_for; on_step (Tasks 2–3)
src/formcast/agent/session.py      StepEvent, on_step, cancel (Task 3)
src/formcast/errors.py             SessionCancelled (Task 3)
src/formcast/__init__.py           exports (Tasks 1–3)
demo/__init__.py, demo/server/{__init__,__main__,settings,store,records,documents,jobs,api,server}.py  (Tasks 4–7)
demo/data/{forms/,schemas/,records/records.csv,records/records.sqlite,templates/}   (Task 4)
demo/tools/build_demo_data.py      (Task 4)
demo/web/core/session.js (Task 8)
demo/web/engines/server/client.js  (Task 9)
demo/web/workbench/{index.html,workbench.js,workbench.css}  (Task 10)
tests/match/test_similarity.py, tests/template/test_rebind.py, tests/agent/test_progress.py  (Tasks 1–3)
tests/demo/test_server_*.py, tests/demo/test_workbench_page.py, tests/web/server_client.test.js  (Tasks 4–9)
```

---

### Task 1: Form signature and `similarity`

**Files:**
- Modify: `src/formcast/match.py`, `src/formcast/pagemap/model.py`, `src/formcast/pagemap/build.py`, `src/formcast/template/model.py`, `src/formcast/__init__.py`
- Test: `tests/match/test_similarity.py`

**Interfaces:**
- Produces:
  - `class FormSignature(BaseModel)`: `ink: list[str]` (per page, `ink_bits` hex), `text: list[list[int]]` (per page, 64 MinHash values of normalized word 3-shingles; an empty page gives 64 × `2**32 - 1`), `version: Literal[1] = 1`.
  - `compute_signature(doc: pymupdf.Document) -> FormSignature` (text from the PDF's own text layer, same normalization as `fingerprint_document`; field values ignored as in `ignore_field_values=True`).
  - `FormMap.signature: FormSignature | None = None` (set by `build_form_map`); `FormInfo.signature: FormSignature | None = None` (copied by `new_template`).
  - `class Similarity(BaseModel)`: `score: float` (0–1), `parts: dict[str, float]` (keys `pages`, `text`, `ink`; `fingerprint` 1.0 when equal), `explanation: str`, `basis: Literal["fingerprint", "signature", "labels"]`.
  - `similarity(template: Template, pdf: str | Path | bytes) -> Similarity`.
  - MinHash: a fixed seed set of 64 hash functions (e.g. `(a*h + b) mod 2**61-1` over `int.from_bytes(blake2b(shingle, digest_size=8))`, with 64 fixed `(a, b)` pairs generated from `random.Random(1729)`), so signatures are stable across runs and machines.

- [ ] **Step 1: Failing tests** (dev forms only):

```python
def test_same_pdf_is_one(fw9_template): assert similarity(fw9_template, FW9).score == 1.0   # basis "fingerprint"
def test_filled_copy_is_reuse(fw9_template, fw9_filled_bytes): assert similarity(fw9_template, fw9_filled_bytes).score >= 0.95
def test_one_line_edit_is_adapt(fw9_template, fw9_edited_bytes):      # redact one text line, insert different text, re-save
    s = similarity(fw9_template, fw9_edited_bytes); assert 0.60 <= s.score < 0.95; assert "text" in s.explanation
def test_fake_scan_is_adapt_or_better(wadoc_template, wadoc_fake_scan_bytes): assert similarity(wadoc_template, wadoc_fake_scan_bytes).score >= 0.60
def test_different_forms_are_low(fw9_template):
    for other in [FW4, I9, WADOC, NDGUARD]: assert similarity(fw9_template, other).score < 0.60
def test_old_template_uses_labels(fw9_template):
    t = fw9_template.model_copy(deep=True); t.form.signature = None; t.form.fingerprint = "0" * 16
    s = similarity(t, FW9); assert s.basis == "labels"; assert "no stored signature" in s.explanation; assert s.score >= 0.60
def test_signature_round_trips_in_json(fw9_template):
    assert Template.from_json(fw9_template.to_json()).form.signature == fw9_template.form.signature
def test_explanation_is_plain(fw9_template, fw9_edited_bytes):
    e = similarity(fw9_template, fw9_edited_bytes).explanation
    assert "page" in e and "%" in e and "layout" in e
```

Fixtures build templates with `new_template(schema, build_form_map(pdf))` (cache the form maps at module scope; mark the module `slow` if detection makes it slow, but prefer `build_form_map(..., detect=False)` or the existing no-detector test policy if one exists).

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/match/test_similarity.py -v -m "slow or not slow"` — FAIL.
- [ ] **Step 3: Implement.** Weights and band edges are calibrated on these dev cases (start from pages 0.2, text 0.5, ink 0.3; pages part = 1.0 if page counts and sizes match within 1 pt, else the fraction of matching pages; when one side has no text layer, re-weight to pages + ink). Label basis: Jaccard containment of the template's field-label words in the PDF's words, combined with the pages part. Explanation format: "same page count and sizes; 92% of the text matches; layout 88% the same" (basis signature), or "same blank form" (fingerprint), or "template has no stored signature, so this compares its field labels and page sizes: …" (labels).
- [ ] **Step 4: Run** the tests and the full suite — PASS (existing template fixtures still validate: `signature` is optional).
- [ ] **Step 5: Commit** `git commit -m "match: form signature and similarity(template, pdf)"`

---

### Task 2: `rebind`

**Files:**
- Create: `src/formcast/template/rebind.py`
- Modify: `src/formcast/agent/api.py`, `src/formcast/__init__.py`
- Test: `tests/template/test_rebind.py`

**Interfaces:**
- Produces: `form_info_for(form_map: FormMap) -> FormInfo` (what `new_template` computes today; `new_template` calls it) and `rebind(template: Template, pdf: str | Path | bytes, *, form_map: FormMap | None = None) -> Template` — raises `FormcastError("cannot rebind: the template has N pages, this PDF has M")` or `("cannot rebind: page K is WxH pt in the template and wxh pt in this PDF")` (1 pt tolerance); returns a deep copy whose `form` is `form_info_for(...)`; fields and history unchanged. `build_template(start_from=...)` uses `form_info_for` (behaviour unchanged; existing agent tests prove it).

- [ ] **Step 1: Failing tests:** rebinding a browser-style template (`fingerprint=None`) onto its PDF sets the fingerprint, kinds and signature and leaves fields equal; afterwards `fill(..., check_fingerprint=True)` works; page-count and page-size mismatches raise with the exact messages; the input template is not mutated.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** the new tests and `tests/agent` — PASS.
- [ ] **Step 5: Commit** `git commit -m "template: rebind(template, pdf) stamps a PDF's form info"`

---

### Task 3: Session progress and cancel

**Files:**
- Modify: `src/formcast/agent/session.py`, `src/formcast/agent/api.py`, `src/formcast/errors.py`, `src/formcast/__init__.py`
- Test: `tests/agent/test_progress.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class StepEvent`: `step: int`, `ops_applied: int`, `ops_rejected: int`, `unmapped_paths: int`, `unfilled_blanks: int`, `input_tokens: int`, `output_tokens: int`, `finished: bool`, `summary: str` (the model's finish summary on the last event, else "").
  - `class SessionCancelled(FormcastError)`.
  - `build_template(..., on_step: Callable[[StepEvent], None] | None = None)` and `revise(..., on_step=None)`; `Session(..., on_step=None)`.
  - Raising `SessionCancelled` from `on_step` ends the session: sandbox closed, result `finished=False`, `summary="cancelled"`, template = current working template. Any other exception from `on_step` propagates after the sandbox is closed.

- [ ] **Step 1: Failing tests** with the scripted fake provider (3 `run_python` steps then `finish`): `on_step` called 4 times with steps 1–4, the last `finished=True` and the finish summary; token counts non-decreasing; a callback raising `SessionCancelled` on step 2 → result `finished is False`, `summary == "cancelled"`, the ops of step 1 present in the template history, and the sandbox worker process is gone (check via the session's sandbox handle or `psutil`-free: the client's `closed` flag); `on_step=None` gives identical results to today's (compare templates field by field).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `tests/agent` and the full suite — PASS.
- [ ] **Step 5: Commit** `git commit -m "agent: on_step progress events and cancel"`

---

### Task 4: App layer — settings, template store, record sources, demo data

**Files:**
- Create: `demo/__init__.py`, `demo/server/__init__.py`, `demo/server/settings.py`, `demo/server/store.py`, `demo/server/records.py`
- Create: `demo/tools/build_demo_data.py`, `demo/data/{forms/,schemas/,records/,templates/}`
- Test: `tests/demo/test_server_store_records.py`

**Interfaces:**
- Produces:
  - `@dataclass class Settings`: `host="127.0.0.1"`, `port=8765`, `data_dir: Path = demo/data`, `reuse_threshold=0.95`, `adapt_threshold=0.60`, `llm: LLMConfig | None`, `max_upload_bytes=50*2**20`, `max_json_bytes=20*2**20`; `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings` reads `FORMCAST_DEMO_*` (`BASE_URL`, `API_KEY`, `MODEL`, `API_FORMAT`, `HOST`, `PORT`, `DATA_DIR`) from the given mapping or from `.env` + `os.environ` (parse `.env` with a 10-line stdlib parser; never log values). `llm` is None unless base URL, key and model are all set.
  - `class TemplateStore(Protocol)`: `list() -> list[StoredTemplate]`, `get(name) -> Template`, `put(name, template) -> None`, `delete(name) -> None`; `StoredTemplate = {name, template_id, page_count, updated}`. `FolderTemplateStore(folder: Path)`; names match `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$` else `ValueError("bad template name")`; files `<name>.json`; writes are atomic (temp file + `os.replace`). Header comment: "Replace with your database: implement TemplateStore."
  - `class RecordSource(Protocol)`: `schemas() -> dict[str, dict]` (name → JSON Schema), `list(schema: str) -> list[RecordSummary]` (`{id, title}`), `get(schema: str, id: str) -> dict`. `CsvRecords(folder)` reads `<schema>.csv` beside `schemas/<schema>.json`: dotted column names build nested objects (`applicant.address.city`), a cell starting with `[` or `{` is JSON (bad JSON → the raw string is kept, so `fill` reports `invalid`), empty cell → key omitted; `id` column required. `SqliteRecords(db_path)`: one table per schema; array-valued paths stored as JSON text columns (documented in the module docstring); `NULL` → key omitted. `MultiRecords(*sources)` concatenates.
  - `build_demo_data.py`: copies two or three dev forms (default `fw9`, `wadoc_auth`, `menomonie_app` — dev split only) into `demo/data/forms/`, their schemas from `forms/schemas/` into `demo/data/schemas/`, writes 5 fictional records per schema to CSV and the same records to `records.sqlite`, and stores one rebound archived agent template per form (`defer-placement` or round3-gen per Plan 3A Ruling B) in `demo/data/templates/`.

- [ ] **Step 1: Failing tests:** settings from a dict (no `.env` read when a mapping is passed); `llm is None` when the key is missing; store put/get/list/delete round trip on `tmp_path`; names `../x`, `/etc/x`, `a/b`, `` reject with `ValueError`; a crash mid-write (monkeypatch `os.replace` to raise) leaves the old file intact; CSV nested columns and a JSON array cell parse; a bad JSON cell stays a string; SQLite NULL omitted; extra CSV columns appear as extra keys (fill later reports `unknown_keys`); generated demo data: each record validates against its schema with `jsonschema`, each stored template validates and `fill`s its form with `check_fingerprint=True`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/demo/test_server_store_records.py -v` — FAIL. **Step 3: Implement**, then run `python demo/tools/build_demo_data.py`. **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo: settings, template store, CSV/SQLite record sources, demo data"`

---

### Task 5: Server skeleton, documents and static files

**Files:**
- Create: `demo/server/documents.py`, `demo/server/api.py`, `demo/server/server.py`, `demo/server/__main__.py`
- Test: `tests/demo/test_server_documents.py`, `tests/demo/conftest.py` (+ `demo_server` fixture: `make_server(settings, provider_factory=None)` on port 0 in a thread, yields base URL)

**Interfaces:**
- Produces:
  - `class Documents`: `add(pdf_bytes: bytes, name: str) -> Doc` (validates with `formcast.pagemap.extract.open_pdf`; `FormcastError` → 400 `{error: "bad_pdf", detail}`), `get(doc_id) -> Doc`, thread-safe (`threading.Lock`), keeps at most 20 documents (least recently used evicted). `Doc`: `id` (uuid hex), `name`, `bytes`, `page_count`, `page_sizes`, `kinds` (from a light open), `form_map()` (built once on first call with `build_form_map`, guarded by a per-doc lock), `page_png(n, dpi) -> bytes` (cached per (n, dpi); dpi clamped 36–200).
  - `make_server(settings: Settings, *, provider_factory: Callable[[], Provider] | None = None, store: TemplateStore | None = None, records: RecordSource | None = None) -> ThreadingHTTPServer` — serves `demo/web/` at `/` (no directory listing; `..` rejected), and `/api/*` via `api.route(method, path, query, body) -> (status, headers, body)`. `provider_factory` lets tests inject the scripted fake provider; production builds providers from `settings.llm`.
  - `python -m demo.server [--host H] [--port P] [--data-dir D]` prints the URL (`http://127.0.0.1:8765/workbench/`).
  - Routes in this task: `GET /api/capabilities` → `{"ai": bool, "matches": true, "store": true, "records": true, "scanned": true, "detectLive": false}`; `GET /api/forms`; `POST /api/documents` (multipart `file`, or JSON `{"form": "<demo form name>"}`) → `{id, name, page_count, page_sizes, kinds}`; `GET /api/documents/{id}/pages/{n}.png?dpi=110`.
  - Error shape everywhere: `{"error": "<code>", "detail": "<plain message>"}`; 404 unknown route/doc, 400 bad input, 413 too large, 500 unexpected (detail without stack traces or secrets; the traceback goes to the server log).

- [ ] **Step 1: Failing tests:** capabilities `ai` false with no LLM settings and true with a fake provider factory; the body never contains the configured API key string (set a sentinel key); upload fw9 → page_count 6; upload a text file → 400 `bad_pdf`; upload > limit (monkeypatch the limit to 1 KB) → 413; pick demo form by name; `../../etc/passwd` as a demo form name → 400; page PNG returns `image/png` with correct size for dpi 72 (612×792 px for page 0); `GET /../pyproject.toml` → 404; two concurrent page requests (threads) both succeed; static `GET /workbench/` returns HTML once Task 10 exists (skip until then via `pytest.importorskip`-style check on the file).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo/server: threaded stdlib server, documents, page images, static files"`

---

### Task 6: Template, edit, match and fill routes

**Files:**
- Modify: `demo/server/api.py`
- Test: `tests/demo/test_server_routes.py`

**Interfaces:**
- Produces routes (spec §11.2):
  - `POST /api/seed {doc, schema}` → `{template}` (`new_template(schema, doc.form_map(), candidates=True)`).
  - `POST /api/apply {template, op, author: "human"|"agent", note?}` → `{template, warnings}`; `OpError` → 422 `{error: "op_rejected", detail}`; context = `OpContext.from_template(template, form_map)` when the template's fingerprint matches a loaded doc passed as optional `doc`, else `from_template(template)`.
  - `POST /api/rebind {template, doc}` → `{template}`; mismatch → 422 with `rebind`'s message.
  - `GET /api/documents/{id}/matches` → `[{name, template_id, score, basis, explanation, action: "reuse"|"adapt"|"new"}]` sorted by score, from every stored template, action by the settings thresholds.
  - `POST /api/fill {template, doc, record?, record_ref?: {schema, id}}` → `{pdf_base64, report}` (fill with `check_fingerprint=True` when the template has a fingerprint equal to the doc's, else `False` and the response adds `"fingerprint_note"`).
  - `POST /api/smart-pdf {template, doc}` → `{pdf_base64}`.
  - `GET /api/templates`, `GET/PUT/DELETE /api/templates/{name}`; `GET /api/schemas`, `GET /api/records?schema=`, `GET /api/records/{schema}/{id}`.

- [ ] **Step 1: Failing tests:** seed on fw9 returns widget fields `w0_*`; apply a `move` → moved rect and one history entry with author human; apply an invalid op → 422 with the library's message; rebind a browser-style template → fingerprint set; matches for the fw9 doc rank the stored fw9 template first with action `reuse`, and the one-line-edited fw9 (built in the test) gets `adapt`; fill with a stored record by `record_ref` returns a PDF (base64 decodes to `%PDF`) and a report whose `template_id` matches; a malformed record (bad JSON array cell from CSV) returns 200 with `invalid` in the report counts; templates CRUD; bad template name → 400.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo/server: seed, apply, rebind, matches, fill, store and record routes"`

---

### Task 7: AI jobs

**Files:**
- Create: `demo/server/jobs.py`
- Modify: `demo/server/api.py`
- Test: `tests/demo/test_server_jobs.py`

**Interfaces:**
- Produces:
  - `class Jobs`: `start(kind: Literal["build","adapt","revise"], *, doc: Doc, schema: dict | None, template: Template | None, instruction: str | None, region: tuple[int, Rect] | None, provider_factory) -> Job`; `get(job_id) -> Job`; `cancel(job_id)`. One thread per job; at most 2 running (a third start → 429 `{error: "busy"}`).
  - `Job` (JSON): `{id, kind, status: "running"|"done"|"cancelled"|"error", steps: [StepEvent as dict], template: <current or final template> | null, summary, error: str | null, started, ended}`. Cancel sets a flag; the job's `on_step` raises `SessionCancelled` at the next step.
  - Routes: `POST /api/jobs {kind, doc, schema?, template?, instruction?, region?: {page, rect}}` → `{id}` (400 if AI is not configured: `{error: "ai_not_configured"}`); `GET /api/jobs/{id}`; `POST /api/jobs/{id}/cancel`.
  - `adapt` = `build_template(schema=template.schema_, pdf, start_from=template)`; `revise` = `revise(template, pdf, instruction, region=region)`; both pass `on_step` and the doc's cached form map.

- [ ] **Step 1: Failing tests** (fake provider factory: scripted 3 steps + finish): build job reaches `done` with 4 step events and a template; revise job with a region appends history entries with author agent; cancel during step 2 (fake provider blocks on an `Event` the test controls) → `cancelled` with a template; provider raising → `error` with a plain message and no secret; AI not configured → 400; third concurrent job → 429; an `/api/apply` call while a job runs returns promptly (< 1 s).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo/server: build, adapt and revise jobs with progress and cancel"`

---

### Task 8: Core session controller (shared by both layouts)

Added by controller Ruling 26 after the Plan 3A final review: session logic (document, template, undo, apply, warnings) lives in `demo/web/showcase/showcase.js`, `app.apply` is not serialized (with the server engine's latency, two ops in flight start from the same base and the second silently drops the first), and the browser engine keeps widget kinds engine-wide (`openKinds`), so a dropped late `open()` overwrites them.

**Files:**
- Create: `demo/web/core/session.js`
- Modify: `demo/web/showcase/showcase.js` (and its step modules) to use it; `demo/web/engines/browser/engine.js` (widget kinds from the doc passed to `apply`, not engine state)
- Test: `tests/web/session.test.js` (Node, with a fake engine whose `apply` resolves after a delay), existing showcase Playwright tests stay green

**Interfaces:**
- Produces: `createSession({engine, bus}): Session` with `setDocument(doc, template, {source})`, `get doc()`, `get template()`, `apply(op, {author = "human", note}) : Promise<{template, warnings} | {error}>` — ops are queued and applied strictly in order, each against the template produced by the previous one; a failed op leaves the template unchanged and later queued ops still apply; `undo()`, `redo()` (through `UndoStack`, cancelling nothing in flight: they wait for the queue to drain), `replaceTemplate(template, {source, pushUndo = true})` (used for detector results and AI job results), events on the bus: `template {template, warnings, source}`, `error {message}`.
- Engine contract change: `engine.apply(template, op, author, {doc})` — the browser engine reads widget kinds from `doc` (stored on the BrowserDoc at open), never from engine-wide state.

- [ ] **Step 1: Failing tests:** two `apply` calls issued without awaiting, against a fake engine that resolves the first after 50 ms and the second after 5 ms → the final template contains both edits, in order; an op that errors between two good ops → template has the two good edits, `error` emitted once; `undo` issued while an op is in flight undoes that op after it lands; `replaceTemplate` pushes one undo snapshot; browser engine: opening doc A, then doc B, then applying an op with `{doc: A}` validates against A's widget kinds.
- [ ] **Step 2: Run** `node --test "tests/web/*.test.js"` — FAIL. **Step 3: Implement**, then move the showcase onto `createSession` (no behaviour change for visitors). **Step 4: Run** node tests and `.venv/bin/python -m pytest tests/demo -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo/web: core session controller with an ordered op queue; widget kinds per document"`

---

### Task 9: Server engine (JS)

**Files:**
- Create: `demo/web/engines/server/client.js`
- Test: `tests/web/server_client.test.js` (Node, with a stub `fetch`), `tests/demo/test_server_engine.py` (Playwright against the real server with the fake provider)

**Interfaces:**
- Consumes: Plan 3A `core/engine.js` (`assertEngine`, `CAPABILITIES`), `core/undo.js`.
- Produces: `createServerEngine({baseUrl = ""}): Promise<Engine>` — fetches capabilities; methods per spec §7.1: `open(fileOrFormName)` → `ServerDoc {id, pageCount, pages: [{index, width, height, rotation: 0, kind, imageUrl(dpi)}]}` (page images are already rotated by the server; `rotation` 0 for the page view, and the page `width/height` are the rendered orientation — document this in the module header), `seed(doc, schema)`, `apply(template, op, author)` (422 → throws `OpError` with the server's detail), `fill(template, doc, record | {schema, id})` → `{pdfBytes, report}`, `smartPdf`, `build(doc, schema, {startFrom})` / `revise(template, doc, instruction, region)` → `JobHandle {id, poll(): Promise<Job>, cancel(), done: Promise<Job>}` (polls every 1000 ms; stops on a final status), `matches(doc)`, `templates: {list, get, put, delete}`, `records: {schemas, list, get}`. All errors become `EngineError(code, detail)`.

- [ ] **Step 1: Failing tests:** Node: `apply` maps 422 to `OpError` with detail; `JobHandle.done` resolves after the stub returns `running`, `running`, `done`; `cancel` posts to the cancel route; `assertEngine` passes. Playwright (harness page `tests/web/harness/server-engine.html` served by the demo server): open fw9 → 6 pages; seed; apply a move; start a build with the fake provider and await `done`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "demo/web: server engine over the workbench API"`

---

### Task 10: Workbench page (layout B)

**Files:**
- Create: `demo/web/workbench/{index.html,workbench.js,workbench.css}`
- Test: `tests/demo/test_workbench_page.py`

**Interfaces:**
- Consumes: server engine (Task 9); Plan 3A parts `PageView` (with `regionSelect: true`), `SchemaList`, `Inspector`, `HistoryPanel`, `renderReport`, `UndoStack`, `createBus`, `checkTemplateFits`.
- Produces: the page in spec §7.3 / §11.3 and the mockup option B: toolbar (form name, Undo, Redo, + Box, Build with AI, Save, Fill →), left column schema (240 px), centre page on the panel-coloured dot grid with page navigation and zoom, right column inspector + history (300 px), plus three overlays: **start screen** (pick demo form or upload; pick schema from `/api/schemas` or paste JSON), **match prompt** (top match ≥ adapt threshold: "This looks like *<name>* (0.93): <explanation>" with Reuse / Adapt / Build new), **job panel** (step list from `steps`, Cancel, then "Load result"), and a **fill drawer** (record picker from `/api/records`, Fill, download PDF, download smart PDF, report). AI controls (Build with AI, area-select revise) are hidden when `capabilities.ai` is false, with a one-line note on how to configure `.env`. Same site styling tokens as the showcase (it is the same project), full width. Area-select: drag on empty page space → a small popover with an instruction textarea and Revise → `engine.revise(...)` job. Every applied op (hand or job result) goes through `UndoStack`; loading a job result pushes one snapshot so Undo returns to the pre-AI template.

- [ ] **Step 1: Failing Playwright tests** (server with fake provider and the demo data in a temp copy):
  - Start screen → pick fw9 + its schema → match prompt offers Reuse for the stored fw9 template → Reuse loads it (boxes with paths visible).
  - Build new by hand: seed shows detected/widget boxes; drag a box → history shows "you · move"; Undo restores; Redo re-applies.
  - Build with AI: job panel lists 4 steps, then Load result shows boxes with the "AI" mark and history entries "AI · …"; one Undo returns to the pre-AI template.
  - Area revise: drag a region on empty space → popover → Revise → job → result loads.
  - Cancel a running job (fake provider blocked) → status "cancelled", panel closes, template unchanged unless "Load partial result" is pressed.
  - Fill drawer: pick a record → Fill → a download starting `%PDF`; the report renders.
  - Save: PUT to the store; reload page → the template is listed in matches.
  - With AI not configured: Build with AI hidden, note shown.
  - Window 900 px wide: no overlapping columns (columns stack or the side columns collapse to drawers).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/demo/test_workbench_page.py -v` — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS, plus `node --test "tests/web/*.test.js"` and the full pytest suite.
- [ ] **Step 5: Commit** `git commit -m "workbench: three-column review app on the server engine"`

---

### Task 11: README, docs and final checks

**Files:**
- Modify: `README.md` (Workbench section: what it is and isn't, `python -m demo.server`, `.env` keys `FORMCAST_DEMO_BASE_URL/API_KEY/MODEL/API_FORMAT`, where the app layer ends and how to swap the store and record sources, "run it on your own machine only; the sandbox is not a security boundary"), `docs/superpowers/specs/2026-10-20-form-projection-design.md` §8 public API sketch (add `similarity`, `rebind`, `on_step`), `docs/superpowers/plans/2026-10-21-formcast-plan3-rulings.md` (closing summary for 3B)

- [ ] **Step 1: Run everything:** `.venv/bin/python -m pytest -q`; `node --test "tests/web/*.test.js"`; `.venv/bin/python -m pytest -q -m slow tests/match tests/demo`. Expected: all pass.
- [ ] **Step 2: Manual smoke** in the built-in browser: `python -m demo.server`, then the start screen → reuse flow → hand edit → fill → download. (Without a real API key the AI path is checked only by the fake-provider tests; say so in the rulings.)
- [ ] **Step 3: Commit** `git commit -m "docs: workbench how-to; public API sketch; Plan 3B rulings summary"`
