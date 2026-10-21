# Form Projection Engine — Design

Date: 2026-10-20
Status: ready for user approval

## 1. Intent

The old project in this folder (`rlm_lab`) let an agent explore a document by writing and running code, so it could answer questions *out of* it. This project does the reverse: an agent explores a form by writing and running code, so data can be projected *into* it.

The problem is real and recurring. Forms come in from doctors' offices, the IRS, vendors and so on. Some are fillable PDFs with real form fields. Many are not: they're digital PDFs with only printed lines and boxes, or they're scans. Today a person has to work out where each piece of data goes. We want an AI to build that mapping, a **template**, so that:

1. A person reviews and corrects it rather than building it by hand.
2. It can be saved and rerun on new data instantly, with no model call.
3. It can be revised later, by a person or by asking the AI, when a mistake turns up.

This is a **library**, not an application. It turns *(data schema + blank form)* into *template*, and *(template + blank form + data)* into *filled PDF + report*. Storage, workflows, clients, dates and which record goes with which form all belong to the application built on top. The repo ships a small demo application to show and test the library, kept clearly separate from the core.

## 2. Goals

- Build a template for a form the system has never seen, using only text and geometry pulled from the PDF. No vision model is required.
- Handle all three kinds of form:
  1. **Fillable**: the PDF already has form fields. Map schema fields onto them.
  2. **Digital flat**: real text, but blanks are only drawn lines and boxes. Add fields.
  3. **Scanned**: the page is an image. Run OCR plus line and box detection, then add fields.
- Accept a standard JSON Schema describing the data:
  - plain values: string, integer, number, boolean, date (`format: date`), enum
  - 1D arrays
  - 2D arrays (lists of rows), which fill tables
- Support the non-trivial mappings (this is how the user defines success):
  - **one value into several boxes**: a date split into MM / DD / YYYY, or one character per box
  - **several values into one field**: first + last name joined into "Full name"
  - **text transforms**: split by delimiter, join, slice, case, number and date formatting
  - **booleans**: tick a checkbox
  - **enums**: pick one box in a group
  - **multi-select**: tick several boxes
  - **tables**: rows of data into a table area on the form, filled accurately
- Produce a **coverage/fill report** with every run.
- Make human review and editing first-class: typed edit operations, validation, version history and undo, and an AI "revise" path that uses the same operations.
- Work with **any model provider** through an OpenAI-compatible endpoint, supporting both the **Chat Completions** and **Responses** formats, chosen by the user.
- Be a clean, installable Python package that is also meant to be cloned and modified.

## 3. Non-goals (v1)

- Storing templates, schemas or data. The library returns plain JSON and the app persists it.
- Choosing which record fills which form (clients, time ranges and so on).
- A hosted service or HTTP API.
- A full PDF editor. The template JSON is the source of truth, and PDFs are only written when rendering.
- Cryptographic digital signatures. Signature fields are placed and can take text or an image, nothing more.
- Requiring a vision model. An optional vision check is left as a future extension point, not built in v1.
- Learning from past corrections across forms. The edit history is recorded so this is possible later (phase 2), but v1 does not use it.

## 4. Anti-goals

These would count as failure even if tests pass:

- **Overfitting to test forms.** No code that checks for a specific form's name, label text or layout to bump a score. The fix for a failing form must be a general improvement to the tools, the page map or the prompts. Evaluation uses a held-out set (§11) to catch this.
- **Spaghetti.** v1 is the foundation. Clear module boundaries, a typed template model, edit operations as data, and no shortcuts that make the next form type harder.
- **Silent failure.** Never drop data, truncate rows or skip a field without saying so in the report.

## 5. Constraints

- Python ≥ 3.10.
- **No vision model assumed.** Form understanding must work from extracted text and geometry.
- Any OpenAI-compatible provider: base URL + API key + model name + API format (`chat_completions` | `responses`).
- **Frontier models only.** Small and local models are out of scope: the tool surface and reasoning demands are too much for them. The endpoint is still pluggable, so a custom provider works if its model is capable enough. Reference models for testing: **OpenAI Luna 6** (Responses format) and **Anthropic Haiku 5.5** (`claude-haiku-5-5`, Chat Completions-compatible endpoint). API keys come from the user's `ontoo 2/backend/.env` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), copied into this project's git-ignored `.env` at build time.
- Runs entirely on the user's machine.
- **Licensing:** open source, non-commercial. AGPL dependencies (PyMuPDF, ultralytics) are acceptable, so the package itself will be AGPL-compatible.

## 6. Decisions

### Made by the user
- Python package that is also a clone-and-edit repo. No hosted or HTTP service.
- Support both Chat Completions and Responses formats, user-selectable.
- Drop DSPy. We own the agent loop and the session state.
- An agent that writes and runs code (the RLM idea) is the core technique, over a text/geometry representation of the form.
- Human review is required, and both human edits and AI-prompted edits are supported.
- Data is a JSON Schema plus instances of that shape. Flat values plus 1D and 2D arrays for v1.
- The template ties schema and form together, and the library returns it for the app to store.
- Tables are in v1, unless they prove much harder than expected. In that case the builder reports back rather than shipping something shaky.
- One fill report returned alongside the PDF.
- Test with real public forms plus generated ones, plus a sample from the CommonForms dataset.
- Demo/review UI in vanilla HTML/JS. No frameworks.
- Use CommonForms detection models instead of hand-writing blank and box detection. The agent's role is meaning and corrections on top of detections.
- PyMuPDF (AGPL) is acceptable. The project is open source and non-commercial.
- Target frontier models (Luna 6, Haiku 5.5). Small or local models are out of scope.

### Made by Claude (the user can override)
- **Working package name `formcast`**, in `src/formcast/`. Rename freely.
- **Template is declarative JSON**, not saved Python. The agent's code is how the template gets built, not what gets saved.
- **Transforms are a fixed, named, chainable set** stored as data in the template, extensible by registering Python functions by name.
- **One set of edit operations used by both humans and the agent.**
- **Coordinates:** PDF points, origin top-left, per page. This is the unrotated page space, with page rotation handled at extraction and render time.
- **Old `rlm_lab` code:** this folder is *not* a git repo, so nothing is in history. Move everything from the old project into `legacy/` untouched and start a fresh `pyproject.toml` for `formcast`. That means `rlm_lab/`, `src/rlm_lab/`, `app.py`, `sitecustomize.py`, the old `README.md`, `pyproject.toml`, `gutenburg books/`, `runs/` and the old tests. Delete the `.dspy_cache` and `.pytest_cache` caches. Recommend `git init` before building. `.env` gets git-ignored.
- **Nested data:** "flat" means no deep structure is *required*, but nested objects are supported. Schema paths use dots (`applicant.dob`). Table rows may be objects (columns keyed by property) or plain lists (columns keyed by index).
- **Tables can continue across pages.** A table field is a list of *segments*, each with its own page, rect, column positions, row top, row height and max rows. Fill flows rows through the segments in order, and only rows left over after the last segment count as dropped. Cells are single-line with auto-shrink in v1. The column model has an optional `wrap` flag (max lines per cell) so wrapping can be added later without changing the template format.
- **Corpus size for v1:** about 12 dev forms and about 6 held-out forms, covering all three kinds, with at least 3 that contain tables. Most ground truth comes from stripped fillable forms and the generator. Hand-written schemas and mappings only for the real forms.
- **v1 finish line:** with both reference models, on the held-out set, (a) at least 90% field detection recall at IoU ≥ 0.5 on fillable and digital-flat forms, and at least 80% on scanned; (b) at least 90% of detected fields mapped to the correct schema path and transforms; (c) every success-list mapping type (date split, join, split, checkbox, enum, multi-select, table) shown working on at least one held-out form. The numbers are a starting bar to revisit after the first eval run, not a reason to tune against the held-out set.
- **Scanned-form support is an optional extra** (`pip install formcast[ocr]`) so the core install stays light.

## 7. Architecture

```
            ┌──────────────── core library (formcast) ────────────────┐
blank PDF ─▶│ pagemap* ──▶  agent session (LLM + sandbox) ──▶ Template │──▶ JSON (app stores)
schema    ─▶│   ▲              │  uses: edit ops, validate, report     │
            │   └ *text+widgets (PyMuPDF), OCR (extra), CommonForms detector │
            │            preview render (PNG with boxes)               │──▶ for human review
data      ─▶│ fill(template, blank PDF, data) ──▶ filled PDF + report  │
            └──────────────────────────────────────────────────────────┘
            demo/ (separate): CSV/SQLite data, template folder, review UI
```

### 7.1 Page map (`formcast.pagemap`)
A deterministic step turning any PDF into a **page map**: a text-and-geometry model of each page.

**Detection uses CommonForms models (decided 2026-10-20 after a spike, see §14).** We don't hand-write blank and box detection. A learned detector proposes candidate fields, and PyMuPDF supplies text and vector geometry around them.

- **Words and lines of text** with bounding boxes, font size and, where available, bold/italic. Comes from PyMuPDF when the page has a usable text layer, otherwise from OCR (Tesseract via `pytesseract`, or docTR, builder's choice, in the `[ocr]` extra). **"Digital" doesn't guarantee a text layer.** The official CMS-1500 is a born-digital PDF whose labels are drawn as vector outlines, so it has only 11 extractable words on a full form. Decide per page, by checking whether text extraction covers the visible labels, and fall back to OCR when it doesn't.
- **Existing form widgets** (fillable PDFs): name, type, rect, options, current value, tooltip. When a page already has widgets, detection is skipped for it.
- **Detected candidate fields** from the CommonForms models:
  - text box, choice button (checkbox or radio) and signature, each with a confidence score
  - converted to PDF points using the crop box, the same way commonforms does it
- **Detector choice by page kind** (both are configurable):
  - **digital pages:** FFDNet-L, which was much more accurate there
  - **scanned pages:** FFDetr, because FFDNet-L sometimes collapses on scan artifacts (it did on the W-9 and the 8949, but not on the simpler forms)
  - optionally run both detectors and merge the results (de-duplicate by IoU), since each finds things the other misses
  - **deskew** scanned pages before detection
- **Wrapper:** we call the detector models directly through a thin wrapper of our own (`formcast.pagemap.detect`). We don't use `commonforms.prepare_form`, which writes its own PDF and had a single-page bug with FFDetr. Model weights download from Hugging Face on first use and are cached.
- **Light vector geometry** (digital pages only), used to *refine* detections, not as a second detector:
  - horizontal rules near a detection, for snapping a box onto its printed line
  - grid lines, for turning detected cells into table structure
- **Label↔candidate links:** nearest text to the left, above or inside each candidate, plus the text line it sits on. This is what the agent mostly reasons over.
- **Form kind detection:** fillable / digital flat / scanned, per page.
- **Query helpers** exposed to the agent's code, for example:
  - `words(page)` and `find_text(pattern)`
  - `right_of(item)`, `below(item)` and `near(item, radius)`
  - `candidates(page, kind=None, min_conf=...)` and `rules(page)`
  - `region(page, rect)`
- **Compact text rendering** of a page or region for the model's context. A summarized listing, not raw dumps.

**The agent's job given detections:**
- confirm or reject each candidate
- add the fields the detector missed (it misses checkboxes most often, plus signature and date lines that original form authors never made fillable)
- **reinterpret** detections: an underline in front of an option (`______DD Form 214`) is a checkbox to mark with an X, even though the detector calls it a text box
- **split one detected line using the small sub-labels under it** (Street / City / State / Zip under one long Address line)
- **ignore pre-printed content** that looks like a blank but is already filled in
- **"circle one" choices** (`AD/NG/Res (circle one)`): v1 renders an ellipse or X mark around or next to the chosen option text, as a `choice_mark` field type. If that turns out awkward, report the choice as unfillable rather than guess.
- split or merge boxes (date parts, comb fields)
- give each field a label and a schema source
- group choices
- build tables from detected cells

The page map is where most of the generality lives. Improvements here help every form.

### 7.2 Template model (`formcast.template`)
Pydantic models, serialized as JSON. Sketch, with exact fields left to the builder:

```json
{
  "formcast_version": "1",
  "template_id": "uuid",
  "schema": { "...the JSON Schema, embedded..." },
  "form": { "fingerprint": "...", "page_count": 2, "page_sizes": [[612, 792], [612, 792]], "kind": "digital_flat" },
  "fields": [
    {
      "id": "f_dob_month",
      "page": 0,
      "rect": [312.0, 140.5, 340.0, 156.0],
      "type": "text",            // text | checkbox | radio_option | signature | table
      "label": "Date of birth (MM)",
      "source": { "path": "applicant.dob", "transforms": [{"op": "date_part", "part": "month", "pad": 2}] },
      "existing_widget": null,   // name of the PDF's own widget, when mapping a fillable form
      "style": { "font_size": "auto", "min_font_size": 6, "align": "left", "comb": null }
    },
    {
      "id": "t_medications",
      "type": "table",
      "source": { "path": "medications" },
      "segments": [
        { "page": 1, "rect": [...], "row_top": 210.0, "row_height": 18.0, "max_rows": 8,
          "columns": [ { "key": "name", "x": [..,..] }, { "key": "dose", "x": [..,..], "transforms": [] } ] },
        { "page": 2, "rect": [...], "row_top": 90.0, "row_height": 18.0, "max_rows": 20, "columns": [...] }
      ]
    }
  ],
  "unmapped_schema_paths": ["applicant.middle_name"],
  "unfilled_blanks": [ { "page": 0, "rect": [...], "nearby_label": "Employer" } ],
  "history": [ { "op": {...}, "author": "agent|human", "at": "...", "note": "..." } ]
}
```

- **Several values into one field:** `source` can take a list of paths plus a `join`.
- **Pick one / multi-select:** each option box is its own `radio_option` or `checkbox` field, with a `when` condition such as `equals: "married"` or `contains: "email"`.
- **Comb fields** (one character per box): either a `comb` style on one field, or N fields with `char_at` transforms. Builder's choice, but one approach consistently.

### 7.3 Transforms (`formcast.transforms`)
- **Named, pure, chainable functions:**
  - `split` (by delimiter, take index), `join`, `slice`, `char_at`
  - `upper`, `lower`, `title`, `strip`
  - `date_format` and `date_part`
  - `number_format` (decimals, thousands separator, currency)
  - `bool_to_check`, `equals`, `contains`
  - `default`, `prefix` and `suffix`
- **Validated when the template is loaded:** unknown transforms and bad arguments are errors.
- **User extension:** `register_transform(name, fn, arg_schema)`. A template that refers to a transform the runtime doesn't have fails clearly when loaded.

### 7.4 Edit operations (`formcast.template.ops`)
Edits are **data**, applied by one function:

- **The operations:**
  - `add_field` and `remove_field`
  - `move` (set rect or offset) and `resize`
  - `set_label` and `set_type`
  - `set_source` (path + transforms) and `set_style`
  - `add_table`, `set_table_columns` and `set_table_rows`
  - `link_widget` (fillable forms)
- **Validation on every apply:**
  - rect is on the page
  - the schema path exists and its type suits the field type
  - no serious overlap with other fields, which is a warning, not an error
  - transform chain is valid
- **Bookkeeping:** every applied op is appended to `history` with author and note. Undo or redo means replaying history. `diff(a, b)` between two template versions.
- **Shared by humans and the agent:** a human UI moving a box calls `apply(template, Move(...), author="human")`. The agent's code calls the same ops.

### 7.5 Agent session (`formcast.agent`)
We own the loop. A **session** holds:
- page map
- schema
- working template
- step log
- token usage
- a persistent sandbox worker

**Loop:**
1. **System prompt** covers the task, the page-map and query-helper API, the edit-op API, the transforms list and the rules: work generally, report what you can't place, call `finish` when done.
2. **First user message** carries a compact summary:
   - form kind
   - page count
   - the schema as paths, types and descriptions
   - a page-map overview
3. **Model replies with code** via a `run_python` tool call.
4. **Code runs in the sandbox worker**, where the page map, query helpers and edit-op functions are preloaded and Python variables persist between steps (RLM-style). Edit-op calls in the sandbox are **recorded and sent to the parent**. The parent applies and validates them against the authoritative template, then returns the results.
5. **Parent sends back a step report:**
   - stdout (truncated)
   - ops applied
   - ops rejected, and why
   - validation warnings
   - **current coverage:** unmapped schema paths, unfilled candidate blanks, low-confidence fields
6. **Repeat until done:** the loop ends when the model calls `finish(summary)` or a step/token budget runs out. The result is the template plus a session log.

Context stays small. Earlier step outputs are summarized or dropped, and the current state is always restated.

**Entry points:**
- `build_template(schema, pdf, llm, *, start_from=None, budget=...)`. With `start_from`, the agent begins from an existing template. Use it to adapt to a new revision of a known form.
- `revise(template, pdf, instruction, llm, *, region=None)`. `region` is a page + rect from a reviewer's highlight. It uses the same loop, starting from the given template, and the edits are logged with author `agent`.

### 7.6 Sandbox (`formcast.sandbox`)
- **Isolation:** a separate Python process per session with a per-step timeout and a memory limit where the OS allows. The working directory is temporary, and access to the page map is read-only.
- **No network, no file writes:** on a best-effort basis by restricting builtins and imports. This keeps a *confused* model from doing harm. It is **not** a security boundary against a hostile one, and the docs say so plainly.
- **Swappable backend** so a user can drop in a container or another isolation backend.

### 7.7 Providers (`formcast.llm`)
- `LLMConfig(base_url, api_key, model, api_format="chat_completions"|"responses", extra_headers, temperature, ...)`.
- A small `Provider` interface: send the message list plus tool definitions, then return the text, tool calls and usage. Two built-in implementations, one per format.
- **Tool calling is required.** That's reasonable for frontier models, and it keeps the loop simple.
- Plain HTTP via `httpx` (or the `openai` SDK, builder's call) so any compatible endpoint works. Retries and timeouts can be configured.

### 7.8 Rendering (`formcast.render`)
- `preview(template, pdf, data=None) -> list[PNG]`: each page with field boxes, ids and labels drawn on, optionally filled with sample or real data. This is what reviewers look at.
- `to_smart_pdf(template, pdf) -> bytes`: the blank PDF with real AcroForm fields added at the template's positions, so it opens as a fillable form in any PDF viewer.
- `fill(template, pdf, data, *, mode="flatten"|"fields") -> (bytes, FillReport)`:
  - validates `data` against the embedded schema
  - checks the PDF fingerprint (a mismatch is an error unless overridden)
  - runs transforms, then writes the text, check marks and table rows
  - `flatten` draws the content onto the page; `fields` fills form fields and keeps the result editable
  - for fillable forms, it sets the existing widget values
- **Text fitting:** auto-shrink to `min_font_size`. If the text still doesn't fit, it's truncated with a warning in the report, never silently.

### 7.9 Fill report
JSON, returned with every fill:
- **Per schema path:**
  - `mapped` (bool)
  - `fields` (ids)
  - `rendered` (the final text or check state per field)
  - `warnings` (truncated, shrunk, overflowed rows, failed validation, missing value)
- **Tables:** rows provided, rows written, rows dropped. Any rows dropped is also a top-level warning.
- **Form-side gaps:** blanks or fields on the form that got no data.

### 7.10 Fingerprint and similarity (`formcast.match`)
- `fingerprint(pdf)`: a stable id for "this exact blank form", based on page count, sizes and normalized text and layout. It is not a file hash, because re-saved PDFs differ byte-for-byte.
- `similarity(template, pdf) -> float` and a short explanation, so an app can decide between "reuse", "adapt with `start_from`" and "build new".

The core only computes these. The app decides what to do.

## 8. Public API (sketch)

```python
from formcast import LLMConfig, build_template, revise, fill, preview, to_smart_pdf, apply, ops, fingerprint, similarity

llm = LLMConfig(base_url=..., api_key=..., model=..., api_format="responses")
tpl = build_template(schema, "intake.pdf", llm)
pngs = preview(tpl, "intake.pdf")                     # human reviews
tpl = apply(tpl, ops.Move("f_dob_month", dx=4), author="human")
tpl = revise(tpl, "intake.pdf", "Phone goes on the line under Address", llm, region=(0, [72, 300, 540, 330]))
pdf_bytes, report = fill(tpl, "intake.pdf", record)
tpl.model_dump_json()                                 # app stores this
```

## 9. Repo layout (suggested)

```
src/formcast/   pagemap/ template/ transforms/ agent/ sandbox/ llm/ render/ match/
demo/           sample data (CSV + SQLite), schemas, template store folder, review UI
forms/          test corpus: dev/ and heldout/, each with PDFs + ground truth
eval/           scoring harness
tests/          unit tests (offline, no model)
legacy/         old rlm_lab, untouched
docs/
```

## 10. Demo application (`demo/`, not core)
Shows the full loop and gives humans a way to review. Minimum:

- **Data:** a few demo schemas and records, as CSV plus a tiny SQLite loader.
- **Template store:** a folder of template JSON files.
- **Review UI:**
  - pick a form and schema, then build a template
  - see page previews with boxes, plus a field list showing each field's label, source path, transforms and warnings
  - edit fields: move, resize, relabel, change source, add, delete
  - highlight an area and type an instruction to call `revise`
  - view history and undo
  - pick a record, fill the form, download the PDF and view the report

**Tech (user's decision): vanilla HTML, CSS and JavaScript. No React, no frameworks, no build step.** A small local Python server lives in `demo/` (stdlib `http.server` or a minimal ASGI app, builder's choice). It serves the static files plus a JSON API wrapping the library calls. Field boxes are absolutely positioned elements over the page preview images, so drag and resize work with plain pointer events. Coordinates convert between screen pixels and PDF points in one place.

## 11. Test corpus and evaluation
- **CommonForms test split (detection only):** the dataset (Apache-2.0) is page *images* with ground-truth field boxes, not PDFs, and has no schemas. Use a fixed sample from the **test** split (the detectors weren't trained on it) to score detection, plus the agent's add/remove corrections, as if the pages were scans. It can't score schema mapping end to end. Pull pages by reading single parquet row groups remotely, not whole 400 MB shards.
- **Real public forms:** US government forms (for example IRS W-9, W-4, and SSA or USCIS forms; public domain), plus printable intake or registration forms with redistributable licenses.
- **Turning fillable forms into ground truth:** take real fillable forms and **strip their widgets** to get a digital flat form. The original widget rects and names become ground truth. **Rasterize** that (with mild skew, noise and blur) to get a scanned version with the same ground truth. This gives realistic forms in all three kinds without hand labeling.
- **Generated forms:** a generator (for example `reportlab`) that produces varied forms from a spec, with exact ground truth. It must vary layouts:
  - label left, label above, and label inside the box
  - underscores vs drawn rules
  - comb boxes and date part boxes
  - checkbox groups and tables with and without grids
- **Ground-truth schema mapping:** for each corpus form, a schema and the expected path→field mapping. For stripped real forms, written by hand once.
- **Dev vs held-out:** develop only against `forms/dev/`. Score against `forms/heldout/` at milestones and never tune on it. Report both.
- **Metrics:**
  - field detection precision and recall at an IoU threshold
  - mapping accuracy (right schema path and transforms)
  - fill correctness (rendered text per field matches expected)
  - table row accuracy
  - steps and tokens per form
- **Tests:**
  - unit tests for pagemap, transforms, ops, render and fill run offline
  - agent-loop tests use a scripted fake provider
  - real-model evals are a separate opt-in command

## 12. Left to the builder
- Exact pydantic field names, module internals, and comb-field representation (pick one).
- OCR engine choice for the `[ocr]` extra.
- HTTP client choice, retry policy, and default budgets (steps, tokens, timeouts).
- System prompt wording and page-map summary format. Iterate using the dev set only.
- Fingerprint algorithm details.

## 13. Open questions for the user
None. Q1 (licensing) was answered: AGPL is acceptable.

## 14. Spike results: CommonForms detection (2026-10-20, throwaway)

**Pages tested:**
- 5 pages from IRS W-9, W-4 and 8822: widgets stripped, then rendered (flat) and rendered with rotation, blur and noise (fake scan)
- 40 pages from the CommonForms **test** split, one page per document

**Matching:** greedy IoU matching against ground truth, on CPU (Apple Silicon).

| Detector | Pages | Recall @IoU .5 | Precision @.5 | Recall @.3 | Speed |
|---|---|---|---|---|---|
| FFDNet-L | IRS flat | 0.98 | 0.92 | 0.98 | 1.5 s/page |
| FFDNet-L | IRS fake scan | **0.35** | 0.97 | 0.36 | |
| FFDNet-L | CommonForms test | 0.79 | 0.89 | 0.83 | |
| FFDetr | IRS flat | 0.82 | 0.76 | 0.91 | 0.18 s/page |
| FFDetr | IRS fake scan | 0.76 | 0.72 | 0.88 | |
| FFDetr | CommonForms test | 0.57 | 0.72 | 0.65 | |

**Findings:**
- **Digital pages:** FFDNet-L is very good on clean digital pages. When it finds a field, it gets the type (text, checkbox, signature) right 96–100% of the time.
- **Scans:** FFDNet-L is fragile on scan-like images. A 1° rotation, mild noise, or a JPEG re-scan took the W-9 from 23 detections to 0. Median filtering plus binarizing recovered about half. FFDetr held up much better on scans, which is why the design picks the detector by page kind and deskews scans.
- **Main misses:** checkboxes (about a third missed on the CommonForms sample). Also signature and date lines that weren't fillable in the original PDF, so they're missing from ground truth *and* from the predictions. The agent must add those from text cues.
- **Box height:** detected box heights often differ from the form author's widget boxes, which drags down IoU. Recall at IoU .3 is meaningfully higher. Snapping boxes to printed lines (§7.1) should help.
- **Licensing:** the dataset is Apache-2.0. FFDNet runs on `ultralytics` (AGPL, acceptable to the user). FFDetr runs on `rf-detr`.
- **Caveat:** the sample is small and the scans are synthetic. Treat the numbers as directional.

### 14.2 Spike 2: real PDFs, outside training data (2026-10-20, throwaway)

Six real government forms, simple to very hard.
- **Fillable forms:** widgets stripped and used as ground truth.
- **Flat forms:** no ground truth, so reviewed by eye on overlays.
- **Training data:** the 8949 (rev. Dec 2025) and Coleman (rev. Jun 2025) revisions postdate the CommonForms paper (Sept 2025), so they're definitely outside its training data. The others probably are.

| Form | Kind | FFDNet-L, digital | FFDNet-L, fake scan | FFDetr, digital | FFDetr, fake scan |
|---|---|---|---|---|---|
| Coleman County public info request (1 p) | fillable, simple | recall 1.00, prec .88 | .86 / .95 | .91 / .87 | 1.00 / .88 |
| USCIS I-9 (4 pp, 130 fields) | fillable, medium-hard | .95 / .96 | .93 / .98 | .95 / .91 | .96 / .91 |
| IRS 8949 (2 pp, 202 fields, big table) | fillable, hard | .99 / 1.00 | **.02** / 1.00 | .94 / .95 | .97 / .97 |
| WA DOC release authorization | flat, simple | all 27 blanks found (by eye) | all 27 | 25 + 1 sig | 24 + 2 sig |
| ND Guard release of information | flat, medium | ~all blanks found (by eye); see notes | 15 (collapsed) | 31 | 34 |
| CMS-1500 claim (blank page) | flat, very dense, SAMPLE watermark | 157 found, most real fields | 150 | 43 | 19 |

Recall and precision at IoU .3. FFDetr's boxes are looser, so its recall at IoU .5 is lower: 0.21 on the 8949 even though the detections were right.

**Takeaways:**
- **Clean digital pages:** detection is close to solved on these. The 8949's table of 11 rows × 8 columns plus its checkboxes came back essentially perfect. Detected boxes sit just above the printed line, where text should go.
- **FFDNet-L's scan fragility depends on the form.** It was fine on simple forms and collapsed on the dense IRS forms. FFDetr was stable on every scan. This confirms the per-kind detector choice, plus the optional merge.
- **The agent layer is clearly needed, for concrete reasons seen on these forms:**
  - turning `______DD Form 214` underlines into checkboxes
  - splitting one Address line into Street/City/State/Zip using the sub-labels
  - "circle one" options
  - signature and relationship lines the detector skipped
  - CMS-1500 fields hidden under the SAMPLE watermark
  - partially detected table rows (CMS-1500 rows 4–6)
- **Text layers can't be trusted on "digital" PDFs.** The CMS-1500 needs OCR for its labels (see §7.1).
- **Speed** on CPU: FFDNet-L about 1.5 s/page, FFDetr about 0.2 s/page.
