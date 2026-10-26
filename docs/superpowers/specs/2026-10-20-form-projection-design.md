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

- Build a template for a form the system has never seen, using only text and geometry pulled from the PDF. No vision model is required. (Since 2026-10-23 page images are on by default and this text-only path is `vision=False`; see the [vision page mapping design](2026-10-23-vision-page-mapping-design.md).)
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
- **No vision model assumed.** Form understanding must work from extracted text and geometry. (Replaced on 2026-10-23 by the [vision page mapping design](2026-10-23-vision-page-mapping-design.md), §5: `build_template` and `revise` send page images by default, and `vision=False` keeps this constraint.)
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
  - a table column may link existing plain fields as its cells (`Column.fields`, one id per row): the cell keeps that field's box and widget, and the field takes no source of its own (added 2026-10-21, see §14.3 addendum)
  - an agent's `remove_field` on a seeded field (widget or detector origin) needs another field or table cell on the same box (IoU ≥ 0.6); a seeded box that is not a blank stays unmapped; human edits are not restricted (added 2026-10-21)
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
- `fill` and text fitting: superseded by `docs/superpowers/specs/2026-10-21-formcast-fill-design.md` (fields-only output, no flatten mode).

### 7.9 Fill report
Superseded by `docs/superpowers/specs/2026-10-21-formcast-fill-design.md` (fields-only output, no flatten mode).

### 7.10 Fingerprint and similarity (`formcast.match`)
- `fingerprint(pdf)`: a stable id for "this exact blank form", based on page count, sizes and normalized text and layout. It is not a file hash, because re-saved PDFs differ byte-for-byte. *Layout* is a coarse per-page ink map (a 32×44 grid of a low-resolution grayscale render without annotations, one bit per cell), so image-only scans, which have no text layer, are still told apart (`formcast/match.py`).
- `similarity(template, pdf) -> float` and a short explanation, so an app can decide between "reuse", "adapt with `start_from`" and "build new".

The core only computes these. The app decides what to do.

## 8. Public API (sketch)

```python
from formcast import LLMConfig, build_template, revise, fill, preview, to_smart_pdf, apply, ops, fingerprint, similarity, rebind
from formcast import StepEvent, SessionCancelled, pdf_features, PdfFeatures

llm = LLMConfig(base_url=..., api_key=..., model=..., api_format="responses")
result = build_template(schema, "intake.pdf", llm)  # SessionResult
tpl = result.template
pngs = preview(tpl, "intake.pdf")                     # human reviews
tpl, warnings = apply(tpl, ops.Move("f_dob_month", dx=4), author="human")  # context derived from tpl
tpl = revise(tpl, "intake.pdf", "Phone goes on the line under Address", llm, region=(0, [72, 300, 540, 330])).template
pdf_bytes, report = fill(tpl, "intake.pdf", record)
tpl.model_dump_json()                                 # app stores this

# (as built, Plan 3B)
sim = similarity(tpl, "intake_v2.pdf")                # Similarity: score 0-1, parts, plain-words explanation
feats = pdf_features("intake_v2.pdf")                 # PdfFeatures: read one PDF once, score it against many templates
best = max(stored, key=lambda t: similarity(t, "intake_v2.pdf", features=feats).score)
tpl = rebind(tpl, "intake_v2.pdf")                    # same page count and sizes; takes this PDF's fingerprint, kinds, signature
def on_step(event: StepEvent): ...                    # step number, summary, counts, token use; raise SessionCancelled to stop
result = build_template(schema, "intake.pdf", llm, on_step=on_step)   # also accepted by revise()
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

### 14.3 Plan 1 results: agent layer vs detector baseline (2026-10-21)

**Read this first.**
- **The held-out evaluation is incomplete.** The Anthropic account hit its API usage limit during the one held-out Haiku run (HTTP 400, "You will regain access on 2026-11-01"). Of 9 held-out sessions, one finished (`ics211`, as published). Three were cut off mid-session (both `ds11` variants, `ics211` fake scan). Five never made a call. One retry hit the same limit. There is no Luna held-out run either (OpenAI credits were already exhausted). No code or prompt changed after the dev rounds. The held-out agent numbers are one session, which says nothing about generalization.
- **Plan 2 added a fill-correctness metric, `fill_acc`.** The saved templates were re-scored with it (no model calls); see "Fill correctness (Plan 2)" below. On dev Haiku round3-gen scores fill_acc 0.63 overall (0.98 fillable, 0.61 digital, 0.52 scanned) against path 0.70.
- **The right floor for the agent is the seeded template, not the bare detector.** Since commit 7078332 a new template starts with one field per widget and per detector candidate. The agent edits that seed. So "agent recall vs bare detector" overstates what the agent adds. Every detection table below has a "seeded, no agent steps" row: the untouched seed scored without calling a model (`eval/agent_eval.py --seed-only`). Against that floor, **the agent's edits lower recall@.5 in every group on dev** (see failure category 1). What the agent adds is the schema mapping, which the seed does not have (path 0).

**How to read the tables.**
- *fillable*: fillable forms as published (the template starts from the widgets; GT is the widgets, so the seed has recall 1.00 by construction).
- *digital*: flat digital forms as published.
- *scanned*: fake-scan variants of the same forms (plus the native scan on held-out).
- *seeded* is the untouched seed; *bare det.* is the detector alone (FFDNet-L for fillable and digital, FFDetr for scanned).
- All figures are micro-averaged over GT fields. Precision is at IoU .3.
- *path* is the share of GT fields with a schema path whose matched template field has that path. *joint* is path and transforms both right. *exact path set* is the matched field's source paths equal to the GT path plus also-paths. These three are over fields with a schema path.
- All numbers were re-scored with the final scorer (multi-line rule restricted, see the caveats), from the saved templates; no model was called.
- Templates archived in `eval/results/templates.tar.gz` (extract from the repo root with `tar -xzf eval/results/templates.tar.gz`; `--rescore` reads them); step logs are local-only.

#### Dev split (12 forms, 24 sessions per full round)

Haiku 5.5, final code (round3-gen, 24 sessions, 10.8M input tokens), against the seed and the bare detector:

| group | sessions | recall@.5 | recall@.3 | precision@.3 | path | joint | exact path set | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|---|---|
| fillable, seeded (no agent) | 7 | 1.00 | 1.00 | 1.00 | 0 | 0 | 0 | 0 | 0 |
| fillable, Haiku (bare det.) | 7 | 0.78 (0.81) | 0.99 (0.99) | 0.99 (0.98) | 1.00 | 0.95 | 0.99 | 9.0 | 174K |
| digital, seeded | 5 | 0.72 | 0.74 | 0.95 | 0 | 0 | 0 | 0 | 0 |
| digital, Haiku | 5 | 0.63 (0.72) | 0.74 (0.74) | 0.84 (0.92) | 0.71 | 0.66 | 0.68 | 28.6 | 770K |
| scanned, seeded | 12 | 0.46 | 0.66 | 0.89 | 0 | 0 | 0 | 0 | 0 |
| scanned, Haiku | 12 | 0.41 (0.45) | 0.64 (0.65) | 0.91 (0.89) | 0.56 | 0.50 | 0.54 | 22.8 | 477K |
| **all, seeded** | 24 | 0.65 | 0.76 | 0.93 | 0 | 0 | 0 | 0 | 0 |
| **all, Haiku** | 24 | 0.55 (0.60) | 0.74 (0.75) | 0.91 (0.92) | 0.70 | 0.64 | 0.68 | 20.0 | 450K |

Luna 6 (gpt-6-luna), round 1 (24 sessions, earlier code: before seeding, `fm.grid`, the finish check and the prompt changes):

| group | sessions | recall@.5 (bare det.) | recall@.3 (bare det.) | precision@.3 (bare det.) | path | joint | exact path set | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|---|---|
| fillable | 7 | 0.76 (0.81) | 0.97 (0.99) | 0.99 (0.98) | 0.79 | 0.73 | 0.79 | 9.4 | 174K |
| digital | 5 | 0.44 (0.72) | 0.58 (0.74) | 0.71 (0.92) | 0.59 | 0.51 | 0.58 | 16.8 | 372K |
| scanned | 12 | 0.32 (0.45) | 0.57 (0.65) | 0.71 (0.89) | 0.56 | 0.48 | 0.55 | 18.4 | 337K |
| **all** | 24 | 0.45 (0.60) | 0.66 (0.75) | 0.78 (0.92) | 0.62 | 0.54 | 0.61 | 15.5 | 297K |

Luna 6, round 2 (later code; partial: the OpenAI credits ran out after 14 of 24 sessions, and the 10 failed sessions are excluded). The round-1 line on the same 14 sessions is the fair comparison:

| run (14 paired sessions) | recall@.5 | recall@.3 | precision@.3 | path | joint | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|
| Luna round 1 | 0.38 | 0.66 | 0.81 | 0.55 | 0.48 | 13.1 | 233K |
| Luna round 2 | 0.44 | 0.69 | 0.88 | 0.68 | 0.66 | 18.2 | 312K |

Luna has not been run on the final code (round 3 and the generalization fixes); this is blocked on OpenAI credits. Haiku's progression on the same dev set: round 1 path 0.52, round 2 0.61, round 3 0.66, round3-gen 0.70. Haiku's run-to-run variance is about plus or minus 0.1 path per form, so differences under about 0.05 are noise.

#### Held-out split (5 forms, 9 sessions planned)

Forms: `ds11` and `ics211` (fillable), `pacific_app` and `uwosh_health` (flat), `dagsboro_permit` (native scan). Fake-scan variants exist for the four non-scanned forms.

Agent, Haiku 5.5 (completed sessions only; one session), with the seed on the same pages:

| group | sessions | recall@.5 | recall@.3 | precision@.3 | path | joint | exact path set | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|---|---|
| fillable (`ics211` only), Haiku (bare det.) | 1 | 1.00 (0.94) | 1.00 (0.94) | 1.00 (0.96) | 1.00 | 0.94 | 1.00 | 6.0 | 132K |
| fillable (`ics211` only), seeded | 1 | 1.00 | 1.00 | 1.00 | 0 | 0 | 0 | 0 | 0 |
| digital, Haiku | 0 | not run | | | | | | | |
| scanned, Haiku | 0 | not run | | | | | | | |

Session status (the failures are the usage cap, not model behavior): `ics211` as published finished (6 steps). `ds11` as published, `ds11` fake scan and `ics211` fake scan were cut off after 9, 12 and 11 steps (partial templates exist but are excluded from the totals). `pacific_app` (both), `uwosh_health` (both) and `dagsboro_permit` never made a successful call. The results md/json list these sessions but leave them out of the totals.

Held-out pages without any agent (complete; all 9 session inputs):

| group | sessions | seeded recall@.5 | seeded recall@.3 | seeded precision@.3 | bare det. recall@.5 | bare det. recall@.3 | bare det. precision@.3 |
|---|---|---|---|---|---|---|---|
| fillable | 2 | 1.00 | 1.00 | 1.00 | 0.89 | 0.91 | 0.73 |
| digital | 2 | 0.66 | 0.75 | 0.93 | 0.66 | 0.75 | 0.93 |
| scanned | 5 | 0.48 | 0.60 | 0.83 | 0.45 | 0.56 | 0.83 |
| all | 9 | 0.63 | 0.72 | 0.90 | 0.59 | 0.68 | 0.83 |

For comparison, on dev the seed scores recall@.5 of 1.00 / 0.72 / 0.46 (fillable / digital / scanned) and the bare detector 0.81 / 0.72 / 0.45. The held-out set is about as hard as dev: digital a little harder (0.66 against 0.72), scans equal.

#### Against the v1 finish line (§6)

The bar: with both reference models, on the held-out set, (a) recall@.5 of at least 0.90 on fillable and digital and at least 0.80 on scanned; (b) at least 0.90 of detected fields mapped to the correct path and transforms; (c) every success-list mapping type shown working on at least one held-out form. Because the held-out run is incomplete and Luna has no held-out run, **none of (a)–(c) can be declared met on the held-out set.** On dev (Haiku, final code):

| bar | dev, Haiku | untouched seed | status |
|---|---|---|---|
| (a) recall@.5 fillable ≥ 0.90 | 0.78 | 1.00 (widgets are the GT, so this is not a detection result) | agent below the bar and below its seed |
| (a) recall@.5 digital ≥ 0.90 | 0.63 | 0.72 | not met; agent below its seed |
| (a) recall@.5 scanned ≥ 0.80 | 0.41 | 0.46 | not met; agent below its seed |
| (b) path ≥ 0.90 | fillable 1.00, digital 0.71, scanned 0.56, all 0.70 | 0 | met on fillable only |
| (b) path and transforms (joint) ≥ 0.90 | fillable 0.95, digital 0.66, scanned 0.50, all 0.64 | 0 | met on fillable only |
| (c) every mapping type on a held-out form | not assessed | | open |

- **Recall.** The bar is out of reach for the detectors in use, and the agent does not close the gap: its recall@.5 is lower than the seed's it started from (0.55 against 0.65 overall) and lower than the bare detector's (0.60). It is not a loss of fields. Recall@.3 is 0.74 against the seed's 0.76, and 0.99 on fillable forms; the loss at IoU .5 is box geometry from the agent's edits (failure category 1).
- **Mapping is the agent's contribution.** The seed has no schema paths. On fillable forms the agent reaches path 1.00 and joint 0.95. On digital and scanned forms mapping is held back by detector misses, early stopping and long-form behavior.
- Both reference models are required by the bar. Luna's last dev numbers (partial round 2: path 0.68, joint 0.66) were on earlier code.
- §6 called these numbers "a starting bar to revisit after the first eval run". This run suggests revisiting (a) per kind (the seed and the bare detector alone are at 0.46 and 0.45 on scans), and scoring the agent against the seed rather than the bare detector.

#### Generalization (dev vs held-out)

There is no valid dev/held-out gap to report. One completed held-out session (`ics211`, fillable) is consistent with dev (path 1.00 against 1.00, joint 0.94 against 0.95), but a fillable form seeded from its own widgets is the easiest case, and one session is not an estimate. Detector and seed difficulty are comparable across splits (above), which suggests the held-out forms are not unusually hard or easy, but that is not evidence about the agent. The dev set was used for tuning across four full Haiku rounds, so dev numbers are optimistic by an unknown amount. A reviewer subagent read the prompts and helpers against `forms/gt` before the run (allowed by the brief); its changes only neutralized form-specific wording (`.superpowers/sdd/2026-10-20-formcast-agent-layer/task-15-genreview.md`).

#### Fill correctness (Plan 2)

*fill_acc* answers a question path and joint don't: when the template is used, does the record's data show up in the right box? `fill()` writes K distinctive probe records (`eval/probe.py`; 2 to 33 records per form, 226 fills over the 24 round3-gen sessions) into the variant's PDF, and every GT box that has a schema path is checked for what its widget then shows. A text box is checked once per record, a table cell once per record and row, a choice box once across all records (right state in every record). fill_acc is correct units over all units, so it also counts GT boxes the template doesn't cover (those score wrong) and weighs table cells by how many records fill them. **That makes it a different weighting from path:** path counts each GT box once, fill_acc counts a 10-row table column ten times per record. Compare the two within a row, not as a like-for-like difference. No model was called: every figure below re-scores the saved templates (`--rescore`), and the results md/json files gained a `fill` column and `fill` totals in place. Nothing else in them changed (checked by diffing every row's other fields against the committed files).

Sanity check: the untouched seed scores fill_acc 0.00 (dev: 24 sessions, 16,098 units; held-out: 9 sessions, 10,848 units), as it must, since the seed has no schema paths. Rows are in `agent_seed_{dev,heldout}_2026-10-21_seed-fill.{json,md}`; the earlier `..._seed` files predate the metric and are unchanged.

fill_acc by group, next to path and joint, over the same sessions (completed sessions only; the held-out json rows of the 8 errored sessions still carry per-row `fill` values from their partial templates, which the totals exclude):

| group | sessions | fill: text | fill: choice | fill: cell | **fill_acc** | path | joint |
|---|---|---|---|---|---|---|---|
| Haiku round3-gen, fillable | 7 | 0.97 | 1.00 | 0.98 | **0.98** | 1.00 | 0.95 |
| Haiku round3-gen, digital | 5 | 0.70 | 0.90 | 0.48 | **0.61** | 0.71 | 0.66 |
| Haiku round3-gen, scanned | 12 | 0.62 | 0.63 | 0.38 | **0.52** | 0.56 | 0.50 |
| **Haiku round3-gen, all** | 24 | 0.71 | 0.78 | 0.52 | **0.63** | 0.70 | 0.64 |
| Luna round 1, fillable | 7 | 0.98 | 0.86 | 0.72 | **0.87** | 0.79 | 0.73 |
| Luna round 1, digital | 5 | 0.71 | 0.87 | 0.26 | **0.52** | 0.59 | 0.51 |
| Luna round 1, scanned | 12 | 0.75 | 0.78 | 0.28 | **0.55** | 0.56 | 0.48 |
| **Luna round 1, all** | 24 | 0.78 | 0.83 | 0.36 | **0.60** | 0.62 | 0.54 |
| Luna round 1, the 14 sessions of round 2 | 14 | 0.54 | 0.82 | 0.47 | **0.50** | 0.55 | 0.48 |
| Luna round 2 (same 14) | 14 | 0.59 | 0.72 | 0.58 | **0.59** | 0.68 | 0.66 |
| Haiku held-out, `ics211` as published only | 1 | 1.00 | 1.00 | 1.00 | **1.00** | 1.00 | 0.94 |
| Seed, dev (no agent) | 24 | 0 | 0 | 0 | **0.00** | 0 | 0 |
| Seed, held-out (all 9 session inputs) | 9 | 0 | 0 | 0 | **0.00** | 0 | 0 |

Haiku's fill_acc progression on the dev set, all sessions: round 1 0.47, round 2 0.54, round 3 0.63, round3-gen 0.63 (path 0.52, 0.61, 0.66, 0.70). The held-out run still has one completed session, so there is no held-out fill figure that says anything about generalization; the `ics211` result (720 of 720 units, 680 of them table cells) matches its dev-level behavior on a fillable form seeded from its own widgets, the easy case.

**What fill_acc says that path does not.**
- *Fillable forms: path is a fair predictor.* fill_acc 0.98 against path 1.00 and joint 0.95. The agent's mapping on fillable forms works end to end.
- *Digital and scanned: fill_acc is below path on Haiku, about equal on Luna's scans.* The all-sessions Haiku figure is 0.63 against path 0.70, and table cells carry the gap (cell 0.52, text 0.71).
- *Table-heavy forms.* Haiku round3-gen, path and joint against fill_acc (cell = fill_acc over table-cell units only):

| form, variant | path | joint | fill_acc | cell |
|---|---|---|---|---|
| `f8949` as published | 1.00 | 1.00 | 1.00 | 1.00 |
| `f8949` fake scan | 0.88 | 0.88 | 0.91 | 0.91 |
| `i9` as published | 1.00 | 0.98 | 0.93 | 0.90 |
| `i9` fake scan | 0.69 | 0.53 | 0.64 | 0.63 |
| `grandview_app` as published | 0.74 | 0.66 | 0.51 | 0.54 |
| `grandview_app` fake scan | 0.38 | 0.32 | 0.24 | 0.17 |
| `cms1500` as published | 0.44 | 0.39 | 0.40 | 0.39 |
| `cms1500` fake scan | 0.06 | 0.05 | 0.02 | 0.00 |
| `ics211` as published (held-out) | 1.00 | 0.94 | 1.00 | 1.00 |

  The gap is small on `f8949`, `cms1500` and `ics211`, 0.05 on `i9` as published, and large only on `grandview_app` (0.23 as published). Across the 10 of 24 sessions with a table, path is 0.62 and fill_acc 0.49. Where the gap is large, the failing units are mostly not the loophole. A diagnostic re-run (offline, one-off script, not committed) splits each failed unit into "no written widget matched the GT box" and "a widget matched but shows the wrong text". `grandview_app` as published fails mostly the first way: employment-history phone, start date and end date (half of their units), several education columns, `applicant.ssn` and `available_from`; and the second way on `starting_title`, `supervisor_name`, `school` and `major` (every unit). `cms1500` fails on service-line dates, supplemental info, NPI and qualifier (no widget matched) and on `charges`, `units` and `emergency_indicator` (wrong text). `i9` as published fails on `list_a_documents[].title`, half of `preparers[].last_name` and `first_name`, `reverifications[].document_title` and `employee.foreign_passport_number` (wrong text); the block-style caveat below (B3/B4) is a candidate cause for the table ones, and that was not isolated.

**The table-per-column loophole, measured.** The caveat said a table built as one segment per column scores as correct on path but flows rows wrongly at fill time. The shape is a segment with one column and more than one row. Across all 207 archived templates (539 table fields) it occurs in exactly one session: Haiku round 1, `i9` as published (22 such segments). That session scored path 0.50, joint 0.44 and fill_acc 0.43; its table-cell units were almost all wrong (28 of 280), but the form-level gap was small (path 0.50 against fill_acc 0.43, about 0.07). In round3-gen the single-column tables are all block-style (one row per segment, one segment per record), which fills right. **Result: one session in 207 had the shape, so the loophole did not inflate the archive's mapping scores to a measurable degree.** No template from round 2 on contains it. The larger path-to-fill_acc gaps above come from boxes with no matching widget and from wrong text, not from this shape. fill_acc remains the metric that would catch the shape if a later agent change reintroduced it.

**Fill errors and warnings** (round3-gen, 226 fills):
- `fill_errors` (a `fill()` call that raised): none in any results file. Sessions that scored badly did so by writing little or the wrong values, not by crashing.
- Top report warning codes, summed over the 226 fills (counts are warnings, not distinct boxes): `unmapped_data` 2,471 (record data with no mapped field; `csudh_health` fake scan 660, `cms1500` fake scan 490, `grandview_app` fake scan 405, `cms1500` as published 273); `shrunk` 1,824 (text fitted below its font size; `csudh_health` fake scan, `f8949` fake scan, `cms1500`, `columbus_medhist`); `font_substituted` 1,516 (info; the IRS forms' own fonts drawn in Helvetica); `adopted` 1,300 (info; existing widgets reused); `list_joined` 897 (`columbus_medhist`); `missing` 585 (probe slots with no value); `overflow` 584 (text that doesn't fit at the minimum size; `cms1500` as published 464); `rows_dropped` 379 (probe rows beyond a table's rows; `grandview_app`, `menomonie_app`); `fingerprint_mismatch` 226 (expected: every archived template predates commit e2afa94, which folded a coarse ink map into the fingerprint, so none matches its PDF's current fingerprint; the eval fills with `check_fingerprint=False`); `transform_error` 29; `not_an_option` 20; `not_writable` 10; `shared_field_conflict` 8; `truncated` 7.
- `unmapped_data` is the code to watch: it is how a record's data fails to reach a form (fill reports it rather than dropping the value silently).
- `rows_dropped_ok` (probe rows beyond the GT table's length must be reported as dropped, by exactly that excess): 5 of 8 checks pass on round3-gen. `f8949` fake scan and both `grandview_app` variants fail: no mapped table on the path, or the template's table capacity differs from the GT row count so the dropped count is not the excess.

**Caveats specific to fill_acc** (details in the rulings file, `docs/superpowers/plans/2026-10-21-formcast-fill-rulings.md`):
- *Plain numeric boxes accept any number formatting* (Ruling R). `f8949`'s money columns carry no number format in GT while templates add one; scoring exact text there made fill_acc read 0.58 for a correct template. It can no longer see a wrong number format on a box whose GT has no `number_format`.
- *Provenance is loose by design* (Ruling S). A box whose GT has transforms is right if its text derives from its own value; the check that the text isn't another path's value applies only to a fragment (a substring) of the own value, because roughly 50 date cells per record share a 71-year pool and a right year often recurs in another date. A wrong slice is still caught; a coincidence that fully matches is not.
- *Block-style tables use per-column row ordinals* (B3/B4). GT for a block-style table (`i9` preparers and reverifications) numbers rows per column. The `i9` `reverifications[]` entry has one sparse, misaligned GT box, which the scorer counts wrong whatever the template does. This costs `i9` a few cells at most.
- *Free-text choice values* (Ruling P). 25 boxes (`columbus_medhist` conditions, `grandview_app` software lists) are checkboxes whose value is the label text; the probe takes those texts as the path's options, so a right box is on in one record and off in the next. Choice units where the expected state never changes would not discriminate; the count is `static_choice` and it is 0 everywhere.
- *`#xx` on-states (fixed).* A checkbox on-state whose name needs `#xx` escaping (`/Yes#205`, the name "Yes 5") used to be left unticked by the writer with no warning. The final review fix decodes on-states and writes them correctly (radio kids were never affected), and a box that still can't be turned on is reported `not_shown`. No checkbox or radio in the corpus has such an on-state, so fill_acc was unaffected either way.
- *A `default` transform on a table column scores wrong on empty rows.* A column with `default` (writing, say, "N/A") fills every slot past the record's rows with that text; fill_acc expects those cells empty and counts them wrong, even though the filled form may be what the template author wanted.
- *Duplicate overlapping widgets are invisible to fill_acc.* Two template fields over one box, or a duplicate widget, would score as one right box if either shows the value.
- *Fingerprints.* All archived Plan 1 templates predate commit e2afa94 (2026-10-21), which folded a coarse ink map into the fingerprint. Their stored fingerprints equal the pre-e2afa94 fingerprint of the original PDFs (checked on `wadoc_auth`, `ndguard`, `menomonie_app` and `f8949`: flat, fillable and digital alike), so all 226 fills report `fingerprint_mismatch` and the eval fills with `check_fingerprint=False`. A template built before e2afa94 needs `check_fingerprint=False` or a rebuild. The fingerprint check itself is not exercised by this eval (Ruling M).
- *Dev numbers are optimistic* in the same way the detection numbers are (four rounds tuned against dev).

#### Browser models (FFDNet-S/L ONNX, single pass) vs the library's FFDNet-L

The browser demo runs the published ONNX exports of FFDNet-S and FFDNet-L, one pass with no test-time augmentation; the library's FFDNet-L is the `.pt` model with augmentation. Run on the 12 dev forms (`eval/results/baseline_detection_2026-10-21_onnx.md`, bare detectors, no merge), micro-averaged over GT fields, recall@.5 / recall@.3 / precision@.3:

| form kind, variant | library FFDNet-L (.pt, augmented) | FFDNet-L ONNX | FFDNet-S ONNX |
|---|---|---|---|
| fillable, as published (641 GT) | 0.81 / 0.99 / 0.98 | 0.79 / 0.98 / 0.99 | 0.79 / 0.97 / 0.99 |
| fillable, fake scan | 0.40 / 0.44 / 0.99 | 0.37 / 0.41 / 1.00 | 0.38 / 0.56 / 0.98 |
| flat, as published (812 GT) | 0.72 / 0.74 / 0.92 | 0.66 / 0.68 / 0.95 | 0.66 / 0.69 / 0.95 |
| flat, fake scan | 0.32 / 0.42 / 0.86 | 0.25 / 0.33 / 0.86 | 0.27 / 0.27 / 0.95 |

For the demo: on pages as published S and L single-pass are within 0.01 of each other at IoU .3 on fillable forms and flat forms alike, and both trail the library's augmented L by 0.01 to 0.02 (fillable) and 0.05 to 0.06 (flat) in recall@.3, so the demo can ship the 38 MB S model and let the visitor opt in to L. Neither browser model is reliable on a fake scan (recall@.3 0.27 to 0.56; S is lower than L on flat scans, higher on fillable ones), which is the case the library hands to FFDetr, so the demo should say that scanned pages need the Python pipeline. The ONNX path is `FFDNetOnnxDetector` (`src/formcast/pagemap/detect.py`); its graph facts are Ruling A in `docs/superpowers/plans/2026-10-21-formcast-plan3-rulings.md`.

#### Known scoring caveats

- **Multi-line rule.** A plain text field counts as multi-line if it has `style.multiline` or is at least 1.8 times the page's median GT text-box height. It then also covers unmatched GT text boxes at least 70% inside it, provided at least two such boxes are stacked in one column inside it (horizontal overlap of 50% or more). The field counts once for precision and gets path credit through the GT line box that carries the path. The first version credited any single tall box at any IoU (for example six extra matches at IoU .36–.47 on one fake scan); that is fixed and every result file here was re-scored from the saved templates. Effect on Haiku round3-gen: recall@.5 0.56 → 0.55, as-published path 0.84 → 0.83. The rule can still be gamed by a very large multi-line field over a stacked column; none was seen.
- **Table-per-column loophole.** Cells are scored by rectangle and column path only, so a table with one segment per column (one column, several rows) would score as correct on path while flowing rows wrongly at fill time. Measured with fill_acc (see "Fill correctness (Plan 2)"): the shape appears in one of the 207 archived templates (Haiku round 1, `i9` as published) and in none from round 2 on, so path and joint were not inflated by it in the final runs. Path and joint on table-heavy forms are still optimistic by the gap shown in the per-form table there (up to 0.23 on `grandview_app`), for other reasons: boxes with no matching widget and wrong text.
- **Fillable false positives.** On fillable forms as published, GT is the widgets. Boxes the agent adds on real blanks that have no widget (for example printed checkbox groups) count as false positives, so fillable precision understates the agent.
- **GT conventions the agent cannot infer:** one box versus two for from/to dates; whether office-use blanks are included (widgets include them, one flat form excludes them); 12 pt widget cells inside 24 pt table rows.
- **Fake scans.** The seeded degradation shifts boxes by about 4 pt, so small checkboxes fail IoU .5. The seed and the bare detector see the same images.
- **Noise.** Haiku varies by about plus or minus 0.1 path per form between runs on the same code.
- **Harness.** A session that ended in an error is now listed separately and left out of the totals' detection and mapping figures (earlier files counted such sessions' untouched templates as zero-path rows; they are re-scored). The held-out baseline run reused the stamp 2026-10-21 and briefly overwrote the dev baseline file; dev was restored and the held-out baseline is `baseline_detection_2026-10-21_heldout.{json,md}`. Held-out runs and re-scores take `--baseline-stamp 2026-10-21_heldout`.

#### Top remaining failure categories

1. **Agent edits lose recall relative to the seed it started from.** The seed already has a box for every widget and detector candidate, and the agent's table-building and removals replace correct boxes with coarser or shifted ones. The same pattern shows in every group on dev (recall@.5 0.78 against 1.00 fillable, 0.63 against 0.72 digital, 0.41 against 0.46 scanned). Per session, recall@.5 seed → agent (Haiku round3-gen): `f8949` as published 1.00 → 0.35 (recall@.3 stays 1.00: tables with 24 pt rows replace 12 pt widget cells); `cms1500` as published 0.50 → 0.21; `f8949` fake scan 0.42 → 0.15; `i9` fake scan 0.92 → 0.78 (recall@.3 0.95 → 0.82). Mapping gains (path 0 → 1.00 on fillable forms) pay for some of this, but a guard that keeps correct seeded boxes unless a table op replaces them with matching geometry is the first thing to try. Any such change alters agent behavior and so breaks the held-out freeze (tag `plan1-heldout-frozen`); decide that before rerunning held-out sessions.
2. **The detector misses most boxes on some scanned pages** (`cms1500` fake scan: about 3% recall@.5 for the seed and the agent alike; `fw9` fake scan about 48%). Scanned pages have no vector rules, so the agent has nothing to place the missed boxes against. The fix is image-based line and box detection in the page map; that is new feature work.
3. **Drawn input boxes are not a page-map signal.** A rectangle's short vertical edges are dropped, so a 9 pt box drawn as a rectangle appears only as two rules, and the agent puts fields on the label row above it. A `boxes` signal from rectangle drawing items would address this.
4. **Haiku stops early or gives up on long, OCR-noisy forms** and uses 0.6–1.2M input tokens per long session, because the first message (about 15–17K tokens) is resent each step. Prompt caching or a leaner first message is the cost lever.
5. **Ambiguous GT conventions** (see the caveats).
6. **Template-model limits.** Records printed as multi-line blocks are representable only as one table per block line with one segment per record. This works (path 1.00 on the I-9) but is clumsy.

#### Spend accounting

The committed result files total about 57.9M Haiku input tokens (57.1M on dev, 0.8M on held-out) and 21.4M Luna input tokens, all on dev. The controller's per-round cap was about 25M input tokens, at most three full rounds. Measured against it:
- Round 2 plus its two targeted checks was about 32M input tokens across both models (Haiku 18.2M, Luna 14.3M), over the 25M cap.
- Haiku ran four full rounds (round 1, 2, 3 and the post-generalization round3-gen at 10.8M each), one more than the cap, plus four targeted checks of 4–5M each.
- The Anthropic usage limit that cut the held-out run was most likely consumed by this dev spend. The earlier note that no round neared 25M was true only when each round is counted without its targeted checks.

For Plan 2: check the budget inside the per-round cap before launching extra checks or rounds, and reserve the held-out budget (both models, with a retry allowance) before any dev iteration starts.

#### Pending, and how to complete the evaluation

Both providers are out of budget (OpenAI credits; Anthropic usage cap until 2026-11-01 00:00 UTC). After topping up or the cap resets, from the repo root, with **no code changes** (use a new `--stamp` so nothing is overwritten, and `--baseline-stamp 2026-10-21_heldout` for held-out runs):

```
# 1. Finish the Haiku held-out run: sessions that failed or were cut off (one rerun each)
.venv/bin/python eval/agent_eval.py --split heldout --model-env ANTHROPIC_EVAL --forms ds11,pacific_app,uwosh_health,dagsboro_permit --variant both --tag heldout-rerun --stamp <date> --baseline-stamp 2026-10-21_heldout
.venv/bin/python eval/agent_eval.py --split heldout --model-env ANTHROPIC_EVAL --forms ics211 --variant fake_scan --tag heldout-rerun-ics211 --stamp <date> --baseline-stamp 2026-10-21_heldout

# 2. Luna 6 held-out (its first and only held-out run)
.venv/bin/python eval/agent_eval.py --split heldout --model-env OPENAI_EVAL --variant both --tag heldout --stamp <date> --baseline-stamp 2026-10-21_heldout

# 3. Luna round 3 on dev, on the final code
.venv/bin/python eval/agent_eval.py --split dev --model-env OPENAI_EVAL --variant both --tag round3-gen --stamp <date>
```

No-model commands that regenerate this section's seed rows and re-score saved runs (a `--seed-only` or `--rescore` run now also computes `fill`; the seed rows with it are the `seed-fill` tag):

```
.venv/bin/python eval/agent_eval.py --split dev --seed-only --variant both --tag seed --stamp <date>
.venv/bin/python eval/agent_eval.py --split heldout --seed-only --variant both --tag seed --stamp <date> --baseline-stamp 2026-10-21_heldout
.venv/bin/python eval/agent_eval.py --rescore eval/results/agent_<...>.json [--baseline-stamp 2026-10-21_heldout]
```

Whether a rerun of the cut-off sessions still counts as "the" one-time held-out run is the controller's call; it was cut off by the spending limit, not by any change to the code or prompts. Update this section with the results when they exist.

#### Addendum (2026-10-21): agent edits no longer discard seeded boxes (failure category 1)

**Status: measured on dev with Haiku 5.5 (two full rounds; see "Live dev results" at the end of this addendum).** Recall now matches or beats the seed in every group, but path and joint regress against round3-gen in both rounds, so the "done" bar (agent recall ≥ seed recall, path and joint not regressing) is **half met**. The offline replay below was written first, while both providers were blocked; it predicted the recall result and missed the mapping regression.

**Diagnosis (Haiku round3-gen, dev, IoU .5).** The seed matched 324 GT boxes that the agent's final templates no longer matched:
- **222: seeded fields removed and replaced by coarser table cells.** A table cell always spans the whole row. On `f8949`, each 24 pt row has a 12 pt widget per column in its lower half, so every cell matches its widget with IoU 0.50 (recall@.3 stays 1.00, recall@.5 drops to 0.35). Similar on `cms1500` service lines and the `f8949` fake scan.
- **92: seeded fields removed with nothing replacing them.** Three patterns: detector fields whose seeded label (the nearest text line) was body text, so the agent took real blanks for printed text; a rejected `add_table` (bad column key) followed in the same step by the removals of the cells it was to replace; and widget checkboxes removed on a fillable form.
- **10: seeded fields moved or resized off their box.**

Across all sessions the agent removed 511 detector boxes and 257 widget boxes that match GT (IoU .3), and 103 detector false positives. Many of the true-box removals were replaced by cells with the same geometry and lost nothing (`i9` as published: 74 widget removals, no recall change). An "is the box empty?" test does not separate good removals from bad ones: 85 of the 103 false-positive removals were empty boxes too. So removal of a detected box stays a judgment the agent makes, but it has to make it explicitly.

**Changes (all generic: geometry and field origin only, no form names, labels or layouts).**
- *Linked cells* (`template/model.py`). `Column.fields` links existing plain fields as a column's cells, one id per row (`None` or rows past the list use the computed cell). The linked field keeps its exact rect, type and `existing_widget`; the column supplies its value. `table_cells(template, table)` and `linked_cells(template)` expand a table for the scorer, the ops and later the fill. Ops validate links: the field exists, is plain, is on the segment's page with its center inside the segment rect, has no source of its own, and is linked once. A linked field refuses `set_source` and `remove_field` until it is unlinked.
- *Removal guard* (`template/ops.py`). An agent's `remove_field` on a seeded field passes only when another field or table cell covers the same box with IoU ≥ 0.6 (a duplicate, or a cell that really replaces it). (The first live round also let the agent remove a detected box with `not_a_blank=True`; that escape was dropped after round 1, see below.) The refusal names the closest box and its IoU, says that the seeded label may be wrong, and points to linking or to move/resize. Human edits are not restricted.
- *Step-report flag.* When a table's cells hold seeded fields but no cell matches their box (IoU < 0.6), `add_table` and the other table ops warn. The warning names the fields and the IoU range and says to link them via `fm.grid(ids)['column_fields']`. A refused removal shows in the report like any rejected op, so a rejected table followed by removals now leaves the fields in place.
- `fm.grid` returns `column_fields`: per column, the box id in each row (None where the row has no box).
- *Prompt* (steps 1 and 5): link the fields a table holds instead of removing them; a seeded label is only the nearest text line; removing a seeded field needs a replacement, and a non-blank seeded box stays unmapped; to fix a box, move or resize it. The scorer (`eval/score.py`) counts a linked field once, as its cell, with the field's box and type. Coverage leaves linked fields out of "fields without a source".

**Offline projection** (`eval/replay_histories.py`, results in `eval/results/replay_anthropic_eval_dev_2026-10-21_round3-gen.json`; no model calls). Each saved history is re-applied on the seed under the new ops:
- *as run*: the saved template (reproduces the published round3-gen numbers).
- *guard*: refused ops are dropped and never retried; the old histories carry no `not_a_blank`.
- *guard + link*: then every computed cell that holds a kept, unmapped seeded field is linked to it.
- *link instead*: also skips each removal of a seeded field that sits in an existing table's cell and links it. This is the new instruction, followed every time.

| all 24 dev sessions | recall@.5 | recall@.3 | precision@.3 | path | joint | exact path set |
|---|---|---|---|---|---|---|
| seeded (no agent) | 0.65 | 0.76 | 0.93 | 0 | 0 | 0 |
| Haiku round3-gen, as run | 0.55 | 0.74 | 0.91 | 0.70 | 0.64 | 0.68 |
| guard | 0.65 | 0.78 | 0.80 | 0.69 | 0.64 | 0.68 |
| guard + link | 0.65 | 0.78 | 0.89 | 0.69 | 0.64 | 0.68 |
| link instead | 0.66 | 0.78 | 0.89 | 0.69 | 0.64 | 0.68 |

By group (recall@.5, seed → as run → link instead): fillable 1.00 → 0.78 → 1.00, digital 0.72 → 0.63 → 0.75, scanned 0.46 → 0.41 → 0.46. Per session: `f8949` as published 1.00 → 0.35 → 1.00 (154 widgets linked), `cms1500` as published 0.50 → 0.21 → 0.51, `i9` fake scan 0.92 → 0.78 → 0.92, `f8949` fake scan 0.42 → 0.15 → 0.39.

What the projection does not show:
- **Precision.** 0.91 → 0.89, from boxes the agent judged not to be blanks; a replay cannot pass `not_a_blank`. A live agent that uses it should recover part of this.
- **Path and joint.** −0.004 each (9 path and 10 joint of 2,394 path GT boxes). Where the agent had redrawn a blank as a new box and removed the seeded one, the kept unmapped seed box now wins the one-to-one match. The prompt now says to move or resize instead.
- **`f8949` fake scan (0.39 against 0.42).** The detector's 20 pt boxes on 12 pt blanks sit just above IoU .5 (0.54–0.57), and the table cells that replace them just below (0.48–0.49). Raising the 0.6 cover threshold to catch this would be tuning to the scorer's threshold, so it was left.
- **Agent behavior.** How the agent responds to refusals (retry, link, or `not_a_blank`), and what that costs in steps and tokens.

**To rerun it (dev only):**

```
.venv/bin/python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --variant both --tag recall-guard --stamp <date>
.venv/bin/python eval/replay_histories.py eval/results/agent_anthropic_eval_dev_<date>_recall-guard.json --formmaps eval/results/logs/<date>_recall-guard/inputs
```

The seed floor is unchanged by this work (`new_template` is untouched; the replay re-scores the seed from the saved form maps at 0.65 / 0.76 / 0.93, as in `agent_seed_dev_2026-10-21_seed`). Compare the new run against that seed row and against round3-gen.

**Freeze.** This changes agent behavior (ops validation, prompt, `fm.grid`). The held-out reruns listed above must run from tag `plan1-heldout-frozen` (for example from a worktree checked out at the tag), not from this code, and before this lands on the branch they are run from. The held-out split was not run or read for this work.

**Live dev results (Haiku 5.5, 2026-10-21, after the Anthropic limit was raised).** Two full dev rounds, the budget for this work. Round 1 (`recall-guard`, code at ea65013, 11.2M input tokens) ran with the `not_a_blank` escape. Round 2 (`recall-guard2b`, code at 6552696, 12.4M input tokens) ran without it. An earlier attempt at round 2 (`recall-guard2`) was cut off after one session, when form-map preparation stalled on GPU out-of-memory errors; it is not used.

| all 24 dev sessions | recall@.5 | recall@.3 | precision@.3 | path | joint | exact path set | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|---|
| seeded (no agent) | 0.65 | 0.76 | 0.93 | 0 | 0 | 0 | 0 | 0 |
| round3-gen (before) | 0.55 | 0.74 | 0.91 | 0.70 | 0.64 | 0.68 | 20.0 | 450K |
| round 1 (guard + `not_a_blank`) | 0.64 | 0.75 | 0.91 | 0.62 | 0.58 | 0.61 | 20.2 | 468K |
| round 2 (guard, no escape) | **0.66** | 0.78 | 0.92 | 0.62 | 0.55 | 0.61 | 22.8 | 517K |

By group, round 2 recall@.5 against the seed: fillable 1.00 = 1.00, digital 0.73 against 0.72, scanned 0.47 against 0.46 (round3-gen: 0.78, 0.63, 0.41). Path / joint by group (round3-gen → round 2): fillable 1.00 / 0.95 → 0.96 / 0.89, digital 0.71 / 0.66 → 0.57 / 0.51, scanned 0.56 / 0.50 → 0.50 / 0.42. Per session the targeted losses are gone: `f8949` as published 1.00 (was 0.35; the agent linked the 154 widget cells), `cms1500` as published 0.51 (was 0.21), `i9` fake scan 0.92 (was 0.78), `f8949` fake scan 0.42 (was 0.15).

What happened between the rounds:
- **Round 1: the agent used `not_a_blank` as a rubber stamp.** It rarely hit the guard. Instead it passed `not_a_blank=True` up front on 191 seeded boxes, and 115 of those were real blanks (all 22 on one fake scan, whose recall@.5 fell 0.89 → 0.50). Only 76 were false positives. The agent's "not a blank" judgment was wrong more often than right, while keeping an unmapped box costs nothing at fill time (it fills nothing). So the escape was removed, and the prompt now says to leave non-blank seeded boxes unmapped.
- **Round 2.** Recall reached the seed and precision went up slightly, 0.91 → 0.92. The remaining agent edits now gain recall over the seed on several forms, for example `ndguard` fake scan 0.64 → 0.76.

**The mapping regression is real, not noise.** It is about 0.08 path in both rounds, while Haiku's per-form spread is about ±0.1 but averages out over 24 sessions. Counting path GT (2,394 boxes) by outcome, round3-gen → round 2:
- matched with the right path: 1,664 → 1,487;
- matched to an unmapped box: 59 → 156;
- table GT unmatched: 367 → 457;
- plain GT unmatched: 277 → 246.

The causes, in order of size:
1. **The agent redraws a blank next to the seeded box instead of mapping the seeded box.** 81 GT boxes in round 2 (43 in round3-gen) match a kept, unmapped detected box while their path sits on another box. That other box is usually one the agent added a few points off. Before the guard the agent removed the seeded box, so its own mapped box won the match. Now the seeded box stays, sits closer to the blank, and wins as an unmapped box. The prompt already says to map or move the seeded field rather than replace it; Haiku mostly does not.
2. **Paths mapped nowhere.** 54 matched-unmapped GT boxes whose path the agent never mapped (15 in round3-gen), on top of the unmatched counts.
3. **Step budget on long forms.** `grandview_app` as published used all 40 steps on page 0 in round 1 (path 0.38) and again ran long in round 2 (0.49), against 0.58–0.74 in earlier rounds. It never built the employment-history tables (unmatched table GT +51). Steps per session rose 20.0 → 22.8.
4. **One convention flip on a fillable form.** On `columbus_medhist` both new rounds mapped each family-history condition only to the relatives text widget and left the family checkbox unmapped (−20 path). round3-gen had mapped both.

**Next step (not run: it would be a third round).** Warn when an op adds or maps an agent field that overlaps an unmapped seeded field (IoU ≥ 0.3), naming the seeded id and saying to map or move that field instead. Optionally, `add_field` could refuse a box that overlaps an unmapped seeded box of the same type. This targets cause 1 directly and is generic (geometry and origin only). A dev round would then show whether path returns to about 0.70 with recall held at the seed.

**Round 3: defer to the seeded placement** (`defer-placement`, code at 866942f, 9.9M input tokens). Per the user, the agent no longer competes with the detector on placement. The saved runs back this up: of the agent's moves and resizes of seeded boxes in round3-gen and both recall-guard rounds, 13 were closer to the GT box than the seed and 57 further away. 34 fell below IoU .5 and none rose above it. Without the page image the agent does not place boxes better than the detector.
- The prompt says the seeded boxes come from the form's widgets and a field detector trained on many forms, whose placement is better than the agent's; the agent maps them and adds a field only where no seeded box sits on or next to a blank.
- An agent's `move` and `resize` of seeded fields are refused. Removal is still refused, except for a duplicate, a replacing table cell, or a detected box split into parts that cover at least 70% of it.
- A boolean path on a text field warns when a checkbox or radio box sits in the same row. (It never fired in this round; see below.)

| all 24 dev sessions | recall@.5 | recall@.3 | precision@.3 | path | joint | exact path set | steps/session | input tok/session |
|---|---|---|---|---|---|---|---|---|
| seeded (no agent) | 0.65 | 0.76 | 0.93 | 0 | 0 | 0 | 0 | 0 |
| round3-gen (before) | 0.55 | 0.74 | 0.91 | 0.70 | 0.64 | 0.68 | 20.0 | 450K |
| recall-guard round 2 | 0.66 | 0.78 | 0.92 | 0.62 | 0.55 | 0.61 | 22.8 | 517K |
| round 3, defer placement | **0.66** | 0.78 | **0.93** | 0.63 | 0.55 | 0.62 | **18.0** | **411K** |

- **By group** (recall@.5 / path / joint): fillable 1.00 / 0.96 / 0.81, digital 0.74 / 0.66 / 0.62, scanned 0.47 / 0.48 / 0.40. Recall stays at or above the seed in every group, and precision is back at the seed's 0.93.
- **Fill** (`fill_acc`, from the rescored file; 16,098 units, no model calls): fillable 0.93, digital 0.58, scanned 0.45, overall **0.58** (round3-gen 0.63).
- **Digital path** recovers from 0.57 to 0.66 (round3-gen 0.71). Sessions are shorter: 18 steps and 411K input tokens on average, against 23 and 517K.
- **Mapping is still below round3-gen** overall: path 0.63 against 0.70, joint 0.55 against 0.64.

Counting path GT by outcome, round 2 → round 3: right path 1,487 → 1,517; on the wrong box 48 → 16; GT matched to an unmapped seeded box while its path sits elsewhere 156 → 161. The remaining loss is mapping, not placement:
- **One path, several boxes.** 45 of those GT boxes are checkboxes whose path the agent put only on the text box in the same row. The path is a list (for example "relatives with the condition"): GT writes it into the text box (join) and also ticks the checkbox when the list is not empty (bool_to_check). round3-gen mapped both; the recall-guard rounds map only the text box. The boolean warning cannot catch this, since the path is not a boolean.
- **Haiku variance in transforms.** For example, `f8949` as published lost date_format on its two date columns in this round (joint 1.00 → 0.74), with the same geometry and paths.
- **Long scanned forms**: `menomonie_app`, `grandview_app` and `i9` fake scans, path 0.26–0.62.

Next generic lever (not run): step 8 of the prompt could say that one path may fill several boxes, for example a list written into a text box and a checkbox marked when that list is non-empty, and that a checkbox beside a mapped text box usually takes the same path with bool_to_check. Mapping, not placement, is now where the agent loses points.

**Freeze.** Unchanged: the held-out reruns must run from tag `plan1-heldout-frozen`; the held-out split was not run or read for this work.
