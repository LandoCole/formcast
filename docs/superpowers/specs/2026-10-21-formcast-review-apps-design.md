# formcast Plan 3 — Review apps: browser demo and local workbench

Date: 2026-10-21
Status: approved for planning (the user asked to go straight to plans and implementation without reviewing this first)

Main spec: `2026-10-20-form-projection-design.md` (§7.10 similarity, §10 demo app). Fill spec: `2026-10-21-formcast-fill-design.md`.

## 1. Intent

formcast can already build a template with an AI agent, edit it with typed operations, fill it and write a fill report. Nobody can *see* any of that without writing Python. Plan 3 adds the human-facing layer, in two forms that share one set of UI parts:

1. **A browser demo for the user's portfolio site** (landonthornton.com, alongside the Document classifier and Distribution waterfall demos). No server and no AI: the CommonForms detector runs in the visitor's browser, the visitor connects the boxes it finds to fields of a data schema by hand, fills the form from a sample record and downloads the PDF. The page explains that in the full library an AI agent gets tools to correct the detector and does the connecting automatically, and it shows the real benchmark numbers as they stand.
2. **A local review app (the "workbench") in the formcast repo**, for developers who clone the repo as a starting point for their own app. It runs the real library behind a small Python server: build a template with the AI, edit it by hand, ask the AI to revise a highlighted area, check whether a form has been seen before, fill records from a CSV or SQLite file, and download the PDF with its fill report.

Plan 3 also adds the library pieces those flows need: `similarity()` and `rebind()` for template reuse, step progress from agent sessions, and browser-template support in the template model.

## 2. Goals

- One set of vanilla-JS UI parts (page view with boxes, schema list, inspector, history, fill report) used by two page layouts:
  - **Portfolio page: layout C, "guided steps".** The page stays on the left; a right-hand panel walks through *Detect → Link → Fill*. Below the tool, the write-up sections in the site's label/body columns.
  - **Local app: layout B, "workbench".** Full width: schema on the left, the page in the middle, selected box and history on the right; a toolbar with Build with AI, Undo, Redo, add box and Fill.
  - The mockups the user picked are in `.superpowers/brainstorm/80517-1792635385/content/layout.html` (options C and B).
- **Two engines behind one interface.** The UI parts talk to an engine object. The **browser engine** detects, edits and fills entirely in the browser. The **server engine** calls the local Python server, which runs the library and adds the AI actions. Controls for actions an engine can't do are hidden.
- **Human edits everywhere, including with the AI engine.** Every change, by hand or by the AI, is an edit operation recorded in the template's history with its author. Hand edits and AI revisions can alternate freely. Undo and redo work across both.
- **One template format.** A template made by hand in the browser demo loads in the workbench and in Python, and the reverse.
- **Portfolio page looks like the user's site** and fits the existing project-sheet pattern (topbar, `.sec` label/body columns on the 24px dot grid, Flexoki neutrals, Michelangelus and IBM Plex Mono, light and dark themes).
- **The portfolio page loads fast and works on phones** (the user's stated wince: "if the model takes forever to load or breaks on mobile").
- **The write-up shows real numbers as they stand**, including the weak ones, generated from the committed eval result files.
- **The workbench is a readable starting point** that shows where the library ends and the app begins.

## 3. Non-goals

- AI in the browser demo. No LLM, no API key, no server calls besides fetching static files and the model weights.
- Automatic schema-field suggestions in the browser demo (fuzzy label matching and the like). In the demo the human connects fields; automatic mapping is the agent's job and the page says so.
- Scanned forms in the browser demo. FFDetr has no ONNX export and FFDNet is fragile on scans (main spec §14). A page without a text layer gets a plain notice that the full library handles scans with a different detector and OCR.
- Creating tables by hand in the browser demo. Tables in a loaded template (for example the agent's) are displayed and filled, not built.
- Logins, multiple users, deployment or hosting of the workbench. It runs on the developer's own machine, bound to 127.0.0.1.
- A security boundary for the agent's code (unchanged from the main spec: the sandbox is not one, and the README says so).
- Phone layout for the workbench. It must not break on a narrow window, but it is a desktop tool.
- Frameworks, bundlers or a build step for the web code.

## 4. Anti-goals

These count as failure even if tests pass:

- **The JS port drifting from Python.** Anything the browser engine re-implements (transforms, edit operations, schema flattening, value resolution) is checked against fixtures generated by running the Python code (§7.4). Python stays the reference.
- **A page that freezes, shows a blank area, or downloads 40–100 MB before the visitor asked for anything.**
- **Numbers on the page that don't come from committed result files**, or that drop the caveats (dev numbers were tuned on; the held-out run is incomplete).
- **Form-specific code** anywhere, including sample-specific branches in the demo.
- **API keys reaching the browser** in the workbench.
- **Spaghetti.** Small modules with one job each; layouts are thin; engines are swappable.

## 5. Constraints

- Vanilla HTML, CSS and ES modules. No build step. Third-party browser libraries are vendored at pinned versions with their licence files (§8.1).
- The portfolio site is static on Vercel. The demo must work as static files under `/formcast/`.
- Node 20+ is available for running JS unit tests (`node --test`, no npm dependencies). Python Playwright (with Chromium) runs page tests, as on the portfolio site.
- Library changes keep the existing public API working and the 1,035 existing tests passing.
- The held-out split is never read or used for anything in this plan.
- No real-model (paid) calls are needed to build or test Plan 3. Agent flows are tested with the existing scripted fake provider.

## 6. Decisions

### Made by the user
- Both a browser demo for the portfolio and a full local app in the repo.
- One shared set of UI parts with two engines.
- The agent engine also allows human edits.
- Layout C for the portfolio, layout B for the local app.
- Visitors to the demo can drop in their own PDF and paste their own JSON Schema; sample schemas are the default.
- The local app is for developers who clone the repo as a starting point.
- Show real numbers as they stand, plus benchmark results.
- Subagent-driven development for both parts, no review of the spec or plans first.

### Made by Claude (the user can override)
- **FFDNet-S is the default browser model** (38 MB ONNX), FFDNet-L (102 MB) is opt-in on desktop. Measured accuracy of both on the dev corpus goes on the page (§10.4).
- **Model weights load from the author's Hugging Face repos** (`jbarrow/FFDNet-S-cpu`, `jbarrow/FFDNet-L-cpu`), which allow cross-origin requests. We don't re-host or modify them: the weights carry no stated licence, so linking avoids redistributing them. No quantized copies.
- **Sample forms open with boxes detected ahead of time** by the same FFDNet-S model, shipped as small JSON, labelled as such, with a "Run the detector live" button. The model downloads only on that button or when the visitor drops in their own PDF.
- **Detection runs in a Web Worker** with onnxruntime-web, WebGPU when available, WebAssembly otherwise.
- **The browser engine re-implements a subset in JS** rather than running Python in the browser (Pyodide would add a large download and PyMuPDF-in-WASM risk, against the load-time wince). The subset is held to Python by generated conformance fixtures.
- **The workbench server is stateless for edits.** The browser keeps the template and its undo stack; the server validates and applies each op with the library (`POST /api/apply`). The server keeps only uploaded documents, their form maps and running AI jobs.
- **Stdlib `http.server.ThreadingHTTPServer`** for the workbench server. Zero extra dependencies and easy to read; AI jobs run in threads and the browser polls their progress.
- **Code lives under `demo/`** (`demo/web/` for the browser code, `demo/server/` for the Python app), matching main spec §9. A sync script in the portfolio repo copies the browser demo into the site, as it does for the classifier.
- **Browser templates have no fingerprint** (`FormInfo.fingerprint = None`), because the fingerprint depends on MuPDF's rendering and text extraction. `rebind()` stamps one later (§9.2).
- **Sample forms are US federal forms (public domain) from the dev corpus, with their form fields stripped**, the same way the eval turns a fillable form into a flat one. Default three: IRS W-9, IRS W-4, USCIS I-9. Stripping makes the detector do real work on first paint, and the form's original fields give ground truth, so a sample can show "the form's own fields" faintly for comparison and a live count of how many the detector found. State and local forms are not used (redistribution status unclear). The sample manifest records each source URL and revision.
- **"Load the AI agent's template"** opens the same form *as published* (with its fields) together with the archived agent template for that exact file, because the archived runs were on the published files. The page says so: on the fillable original the agent maps the form's own fields; on flat and scanned forms it maps detector boxes (the results tables cover those).
- **Agent templates and headline numbers come from the latest code's dev run (`defer-placement`, Haiku 5.5).** Its 24 templates were never archived: they exist only in the worktree `.claude/worktrees/determined-kowalevski-a12545/eval/results/templates/2026-10-21_defer-placement/` (branch merged into `main`). Copy them into the archive only after `--rescore` reproduces the committed `defer-placement` results file's detection and mapping figures exactly; then the rescore also adds `fill`. If they don't reproduce, use `round3-gen` for both the templates and the headline, and say so. Beside the headline: round3-gen, the seed, the bare detector, Luna 6's partial rounds, and the held-out status stated plainly (§10.3).
- **The browser runs the single-pass ONNX model, not the library's detector configuration.** The library's `FFDNetDetector` loads the `.pt` weights and predicts with test-time augmentation (`augment=True`, NMS IoU 0.1); the published `-cpu` ONNX files take a fixed 1216 px input and run one pass. Porting augmentation would triple phone inference time. So: add a single-pass ONNX path in Python (an `FFDNetOnnxDetector` in `formcast.pagemap.detect`, same output type, opt-in, not the library default), and use it for the precomputed sample boxes, the parity test, and the S-vs-L measurement. The page states that the browser runs the single-pass ONNX export and the library uses the full model with augmentation, and §10.4 measures all three.
- **Phones: live detection is off by default.** A crashed tab is the failure the user named, the ONNX input size is fixed, and we don't re-export weights. On phones (coarse pointer and narrow viewport, or `navigator.deviceMemory` < 4 where reported) samples work fully with their precomputed boxes; a dropped flat PDF shows a note that live detection runs on desktop, with a "Try anyway" button that loads FFDNet-S after warning about the 38 MB download and possible slowness. A dropped fillable PDF works on phones (its own fields are used, no model needed).
- **Portfolio work happens in a separate git worktree** of the portfolio repo, on a new branch `formcast-demo` cut from `main` (`git worktree add ../landon-portfolio-formcast -b formcast-demo main`), so the user's checkout (currently on the unpushed `docrooter-demo` branch) is untouched. Edits are kept additive so a later merge with `docrooter-demo` is small. The formcast Work entry goes first in the Work list and in `check_site.py`'s `EXPECTED_PROJECTS` (newest project, live demo); moving it is a one-line change the user can make.

## 7. Architecture

```
demo/
  web/                      static, no build step
    core/                   UI parts and pure logic (ES modules; pure ones also run in Node)
    engines/browser/        detection worker, PDF loading, seed building, fill writer
    engines/server/         thin HTTP client for the workbench server
    vendor/                 pdf.js, pdf-lib, onnxruntime-web (pinned, with licences)
    showcase/               portfolio page (layout C) + samples/ + results.json
    workbench/              local app page (layout B)
  server/                   Python app layer for the workbench
  data/                     demo schemas, records (CSV + SQLite), forms, templates/
  tools/                    build scripts: conformance fixtures, showcase samples, results.json
tests/web/                  node --test suites + conformance fixtures
tests/demo/                 pytest for the server and app layer; Playwright page tests
```

### 7.1 The engine interface

One JS object per engine. Every method is async. Capability flags tell the UI what to show.

| method | browser engine | server engine |
|---|---|---|
| `open(pdfBytes or sampleId)` → document (page count, sizes, kinds, page renderer) | pdf.js | upload; server renders PNGs |
| `seed(doc, schema)` → template | JS seed (§8.3) | library `new_template(..., candidates=True)` |
| `detect(doc, page, {model})` → candidates | onnxruntime-web worker | not offered (server seeds with detections) |
| `apply(template, op, author)` → `{template, warnings}` or `{error}` | JS ops (§8.4) | `POST /api/apply` (library `apply`) |
| `fill(template, doc, record)` → `{pdfBytes, report}` | pdf-lib (§8.5) | library `fill` |
| `smartPdf(template, doc)` → bytes | pdf-lib | library `to_smart_pdf` |
| `build(doc, schema, {startFrom})` → job | — | agent `build_template` |
| `revise(template, doc, instruction, region)` → job | — | agent `revise` |
| `matches(doc)` → `[{name, score, explanation}]` | — | library `similarity` over the store |
| `templates` / `records` stores | download / upload JSON | `demo/server` stores |

`capabilities`: `{detectLive, ai, matches, store, records, scanned}`.

### 7.2 Shared UI parts (`demo/web/core/`)

Each part is a module exporting a small class or `mount(el, opts)` function. Parts talk through a tiny event bus or explicit callbacks, never by reaching into each other's DOM.

- `geometry.js`: the only place that converts between PDF points (origin top-left, unrotated crop-box space, as in the library) and screen pixels, including page rotation. Also IoU and rect helpers.
- `pageview.js`: renders a page image (canvas from pdf.js, or a PNG from the server) with field boxes as absolutely positioned elements. Select, drag to move, corner handles to resize, draw a new box, keyboard nudge (arrow 1 pt, shift+arrow 10 pt), Delete. Optional area-select mode for AI revise (drag on empty space → region callback). Boxes are styled by state with line styles only, no accent colours: linked = solid ink, not linked = dashed graphite, selected = 2 px ink with handles, AI-added = small "AI" mark. Tables draw their segment, row lines and column lines. Each box shows a small tag with its schema path.
- `schemalist.js`: the flattened schema as a tree with type and placement status (placed, partly placed, not placed). Drag a field onto a box to link it (desktop). Clicking a field highlights the boxes that use it.
- `inspector.js`: the selected box's label, type, data field (a select of schema paths), transforms (a chain editor: add, remove and reorder transforms with their arguments, from the transform catalogue), style (font size auto or fixed, alignment, comb), and warnings. Tables show segments and columns. On touch screens linking happens here (tap a box, pick a field).
- `history.js`: the template's history entries (author, op summary, note) and undo/redo buttons.
- `report.js`: renders a fill report: counts by warning code, then per field and per path rows.
- `store.js` (browser): download and load template JSON; remember the last template per sample in `localStorage` (wrapped in try/catch; the page works without it).
- `undo.js`: an undo/redo stack of immutable template snapshots. Undo restores the previous snapshot, which also drops the matching history entry. This is session state in the browser for both engines.

### 7.3 Layouts

- `showcase/index.html` + `showcase.js` (layout C): site topbar and intro section; the tool (page left, sticky on desktop; steps panel right); write-up sections. Below 760 px the page goes on top and the panel below it.
- `workbench/index.html` + `workbench.js` (layout B): toolbar; three columns; a start screen for picking a PDF and schema; the similarity prompt; job progress panel for AI runs; fill panel with record picker and report.

Both are thin: they create an engine, mount the parts, and wire their events.

### 7.4 Conformance fixtures

`demo/tools/export_conformance.py` runs the real Python code over a hand-written list of cases and writes JSON fixtures to `tests/web/fixtures/`:

- **transforms**: every transform in the catalogue, normal and edge inputs (empty, None, wrong type, bad arguments), with Python's output or error.
- **schema**: `flatten_schema` on schemas covering nesting, arrays of objects and scalars, enums, formats, `$ref` and `$defs`, plus the placeholder-record cases (§8.6, generated from the JS rules but validated by Python's `jsonschema`).
- **ops**: for each op in the browser subset, a starting template, the op, the author, and Python's resulting template (or error message class) and warnings.
- **values**: templates plus records, with the value Python's fill resolves for every slot (after transforms, `when`, joins and table flow) and the report entries that resolution produces (`missing`, `transform_error`, `unmapped_data`, `rows_dropped`, `not_shown`, ...).

`tests/web/*.test.js` load the fixtures and require the JS output to equal Python's. A pytest test regenerates the fixtures in memory and fails if the committed files are stale, and another runs `node --test tests/web` (skipped with a clear message if `node` is missing). A second direction is also tested: a Node script builds templates the way the browser engine does (seed, then a scripted list of ops), and pytest validates them with `Template.model_validate` and checks that `replay` of their history on the seed reproduces them.

## 8. Browser engine (`demo/web/engines/browser/`)

### 8.1 Vendored libraries
- **pdf.js** (`pdfjs-dist`, ES module build plus its worker): page rendering, text content for labels, AcroForm widget annotations.
- **pdf-lib**: writing form fields and values into the PDF.
- **onnxruntime-web**: running FFDNet, WebGPU or WASM.

Pin exact versions released at least two weeks ago, record them with their licences in `demo/web/vendor/README.md`, and copy the files (including WASM binaries) into `vendor/`. Nothing loads from a CDN at runtime except the model weights.

### 8.2 Detection
- `detect-worker.js` runs in a Web Worker: fetches the ONNX file from Hugging Face (`https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx`, L likewise), caches it with the Cache API, reports download progress, creates an inference session (`webgpu` then `wasm` execution providers), and answers detect requests.
- Pre- and post-processing must match the new Python `FFDNetOnnxDetector` (§6): render at the same DPI as the library, letterbox to 1216, confidence 0.3, one pass, the NMS commonforms applies to its ONNX path, the three classes mapped to `text`, `choice`, `signature`, boxes converted to PDF points through the crop box. **Inspect the ONNX graph first** (input name and shape, output layout, whether NMS is inside) with a short Python spike using `onnxruntime`, and record what was found in the plan's rulings.
- **Parity test**: on at least three dev pages, boxes from the JS pipeline (run in Playwright's Chromium with the vendored onnxruntime-web, or in Node with `onnxruntime-node` if it installs offline) must match `FFDNetOnnxDetector` on the same ONNX file: at least 95% of boxes paired at IoU ≥ 0.9, and counts within 5%.
- The page reports what ran: model, execution provider, seconds per page.

### 8.3 Opening a PDF and seeding
- Open with pdf.js. For each page: size and rotation; widgets from `getAnnotations()` (form fields: name, type, rect, options); a page kind: `fillable` if it has widgets, `digital` if it has a usable text layer, else `scanned` (show the notice; detection is still allowed but labelled unreliable).
- **Seed** like `new_template(..., candidates=True)`: one field per widget (`w<page>_<i>`, reading order, `origin: "widget"`, `existing_widget` = the widget's name) and one per detection (`c<page>_<n>`, `origin: "detector"`, confidence). The seed label is the nearest text line to the left of or above the box (simple heuristic from pdf.js text items; it does not need to match the Python page map exactly). Pages with widgets skip detection, as in the library.
- Sample forms ship as a bundle: the stripped PDF, precomputed detections (FFDNet-S single pass, from `FFDNetOnnxDetector`), the form's original field rects (for the faint comparison overlay and the found-count), the sample schema, a sample record, and the published PDF with its archived agent template.

### 8.4 Edit operations (JS subset)
Port of `formcast.template.ops` for: `add_field`, `remove_field`, `move`, `resize`, `set_label`, `set_type`, `set_source`, `set_style`. Same op JSON as Python (`op_models.py`), same validation rules and messages as far as the fixtures cover them (rect on page, schema path exists and suits the field type, transform chain valid, overlap warnings, the linked-cell rules when a template has tables). Human edits are never restricted by the agent-only guards (removal guard, placement deference), as in Python. Each applied op is appended to `history` with author `"human"`, an ISO timestamp and note. Table ops are not in the browser subset; a table field can still take `set_source`, `set_label` and `remove_field`.

### 8.5 Fill (pdf-lib)
- **Value resolution** is a port of `formcast/render/values.py` and the slot logic in `fill.py` (sources, joins, transforms, `when`, `bool_to_check`, table flow through segments, linked cells), held to Python by the `values` fixtures.
- **Writing** follows the fill spec's outcome, fields-only: a text form field per text or signature slot at its rect (Helvetica, auto-shrink between the style's size and min size, comb when set), a checkbox per checkbox/radio_option slot, and for `choice_mark` a checkbox whose on appearance is an ellipse around the option text. Existing widgets named in `existing_widget` are filled in place by name. Table cells are text fields named after the table, row and column.
- **Report**: the same JSON shape as Python's `FillReport` for the codes the browser can produce (`missing`, `transform_error`, `unmapped_data`, `list_joined`, `unknown_keys`, `shrunk`, `overflow`, `truncated`, `rows_dropped`, `widget_not_found`, `type_mismatch`, `not_an_option`, `not_shown`, `unencodable`). Codes that need MuPDF (`font_substituted`, `adopted`) are omitted, and so is `invalid` (the browser carries no JSON Schema validator; record errors in a pasted record surface as `missing` or `transform_error`). The report says it came from the browser writer.
- Coordinates go through `geometry.js` into PDF user space (bottom-left origin, media box offset, page rotation). A test fills a rotated-page fixture and a page with a crop box offset and checks the field rects by reading them back with pdf.js (and, in pytest, with PyMuPDF).

### 8.6 Schemas and records
- `schema.js` flattens a JSON Schema (the same dialect formcast accepts, including `$ref`/`$defs`) into paths with types, formats, enums and descriptions, matching `flatten_schema` via fixtures.
- **Pasted schemas**: parse and flatten; on error, show the message under the text box and keep the previous schema.
- **Placeholder records** for a pasted schema: type-driven values (strings get "Sample <title or last path part>", or a fitting fake value for common formats and names such as `date`, `email`, phone, ZIP; numbers and integers a small value in range; booleans `true`; enums the first option; arrays two rows). The record is shown as editable JSON in the Fill step. The sample schemas come with hand-written fictional records.

## 9. Library additions (`src/formcast/`)

### 9.1 Form signature and `similarity`
- New optional `FormInfo.signature: FormSignature | None`: per page, the coarse ink map (`ink_bits`, hex) and a MinHash sketch (64 values) of the page's normalized word 3-shingles. Computed by `build_form_map` (stored on `FormMap`) and copied into templates by `new_template`. Old templates without it stay valid.
- `similarity(template, pdf) -> Similarity` (pydantic: `score` 0–1, `parts` dict, `explanation` str).
  - Same fingerprint → 1.0, explanation says "same blank form".
  - Otherwise a weighted mean of: page count and size agreement; text similarity (MinHash Jaccard); ink similarity (1 − Hamming distance over aligned pages). The explanation names each part in plain words, for example "same page count and sizes; 92% of the text matches; layout 88% the same".
  - A template without a signature (older, or made in the browser) falls back to its fields' labels against the PDF's text plus page geometry, and the explanation says so.
- The library only scores. The workbench decides: by default reuse at ≥ 0.95, adapt at ≥ 0.60, else build new (config in `demo/server/settings.py`, calibrated on the dev corpus, never the held-out set). Calibration cases: a re-saved copy, a filled copy (`ignore_field_values`), the fake-scan variant of the same form, and different forms.

### 9.2 `rebind` and fingerprint-less templates
- `FormInfo.fingerprint` becomes `str | None` (default required in Python-built templates stays a string; `None` means "not computed", as in browser templates).
- `fill` and `to_smart_pdf` with `check_fingerprint=True` on a template whose fingerprint is `None` raise `FormcastError` with a message that says to call `rebind()` or pass `check_fingerprint=False`.
- `rebind(template, pdf) -> Template`: checks page count and page sizes match (error otherwise), then returns a copy with this PDF's form info (fingerprint, kinds, signature). It does not change fields. `build_template(start_from=...)` already does the equivalent internally; refactor it to use `rebind`'s form-info helper without changing behaviour.

### 9.3 Session progress and cancel
- `build_template` and `revise` take `on_step: Callable[[StepEvent], None] | None`. A `StepEvent` carries the step number, a short summary (ops applied and rejected counts, coverage counts, the model's finish summary on the last step) and token usage so far. Default `None` changes nothing.
- Cancel: `on_step` may raise `formcast.errors.SessionCancelled`; the session stops cleanly (sandbox closed) and returns its current template with `finished=False` and the summary "cancelled".

### 9.4 Exports
`similarity`, `Similarity`, `rebind`, `StepEvent` and `SessionCancelled` join the lazy public exports.

## 10. Portfolio page (`demo/web/showcase/`)

### 10.1 Structure (top to bottom)
1. **Topbar**: "Landon Thornton" (links to `/` in the synced copy), "Work · Document AI".
2. **Intro** (`.sec first`): label "Interactive tool", h1 "formcast", lead (one sentence: a detector finds the blanks, you connect them to your data; in the full library an AI agent does the connecting), meta line (for example "CommonForms FFDNet in your browser · JSON Schema in, fillable PDF out · Your file stays in your browser").
3. **The tool** (layout C):
   - Left: the page figure with boxes, caption with model, timing and counts, and a key (linked, not linked, selected).
   - Right: steps panel.
     - **Detect**: sample picker (W-9, W-4, I-9, fields stripped) or drop a PDF; model choice (S default, L on desktop); "Run the detector live" with progress; "Show the form's own fields" toggle with "found N of M" for samples; a notice for scanned pages. "Load the AI agent's template" (opens the published form with the agent's template; origin note: built by Haiku 5.5 in the dev eval, with that session's scores).
     - **Link**: schema source (sample schema, or paste your own JSON Schema); the schema list with placement status; the inspector for the selected box; undo, redo, add box.
     - **Fill**: the record as editable JSON (sample or placeholder), Fill, download the filled PDF, download a fillable empty PDF (smart PDF), download or load the template JSON, and the fill report.
4. **How it works**: the pipeline in plain words (page map from text, geometry and the detector; the agent writes and runs Python against query helpers and edit ops; the template is JSON; fill writes real form fields). A small diagram in the site's drafting style.
5. **What the AI agent adds**: what the browser demo leaves to you (linking, fixing misses, splitting lines, "circle one" choices) and how the agent does it with tools, without vision.
6. **Results**: tables and short notes generated from `results.json` (§10.3).
7. **Limits**: the failure categories from main spec §14.3 in plain words (mapping is where the agent loses points now; scans are weak; held-out incomplete).
8. **Credits and code**: CommonForms (paper and models, by Joe Barrow; dataset Apache-2.0; the model weights load from the author's Hugging Face page), the vendored libraries, a link to the formcast repo (left as a pending `data-todo` link until a public remote exists).
9. **Footer** like the other demo pages.

Copy follows the site's copy rules (see the portfolio's `CLAUDE.md` and `tools/check_site.py`: plain, calm, no hype, no accent colours).

### 10.2 Load time and phones
- First paint shows sample 1 with its precomputed boxes; no model, pdf-lib or onnxruntime download happens until needed. pdf.js loads at start (needed to show the page); onnxruntime-web and the model load on "Run live" or a dropped PDF; pdf-lib loads on the first fill.
- Download progress in MB; the worker keeps the main thread responsive; the model is cached for the next visit.
- If anything in detection fails (no WebGPU and WASM fails, out of memory, offline), samples keep working end to end and the drop-a-PDF path shows a plain message with what failed. No blank areas, no stuck spinners: every async step has a visible state and a timeout.
- Phones: page on top, panel below; tap a box, then pick a field in the inspector; touch-sized handles (at least 24 px hit area); no horizontal scroll at 375 px; live detection off by default with "Try anyway" (§6); the model choice is hidden and S is used.
- Performance budget (checked by Playwright with throttling where possible): sample page interactive within 2 s on a simulated mid-range phone, excluding web fonts; no request to huggingface.co before the visitor asks.

### 10.3 Results (`results.json`)
`demo/tools/build_showcase.py` reads committed files in `eval/results/` and writes `demo/web/showcase/results.json`, so every number on the page traces to a file. It includes:
- **Detection baselines** (bare detectors, dev and held-out, by form kind; recall at IoU .5 and .3, precision).
- **Browser model accuracy**: FFDNet-S against FFDNet-L on the dev corpus (new run, §10.4).
- **Agent results on dev**, by kind and overall: seed (no agent), Haiku 5.5 `defer-placement` (current code), Haiku round3-gen (best mapping so far), Luna 6 round 1 and the partial round 2. Columns: recall@.5, precision@.3, path, joint, fill_acc, steps and input tokens per session. If a run's results file has no `fill` column yet, re-score it with `eval/agent_eval.py --rescore` (no model calls) first and commit the updated file.
- **Held-out status**, stated plainly: one of nine Haiku sessions completed; no Luna held-out run; why (usage limits), and that no code changed.
- Caveats the page must show next to the numbers: dev results were tuned on over several rounds; Haiku varies about ±0.1 path per form between runs.

### 10.4 New measurement: FFDNet-S vs FFDNet-L
Extend `eval/baseline_detection.py` with a `--detectors` option (default unchanged) and run it on the dev split (as published and fake scan) for three setups: FFDNet-L as the library runs it (`.pt`, augmentation), FFDNet-L ONNX single pass, and FFDNet-S ONNX single pass. Commit the results with a new stamp and add a short paragraph to main spec §14.3. Dev split only. The page shows all three, so the cost of running in the browser is visible.

### 10.5 Portfolio integration (in a worktree of the portfolio repo `landon-thornton-site/landon-portfolio`, branch `formcast-demo` from `main`, §6)
- `tools/sync_formcast.py <path to formcast checkout>`: copies `demo/web/showcase/`, `core/`, `engines/browser/` and `vendor/` into `formcast/` with the same three swaps the classifier sync makes (site fonts for EB Garamond, Michelangelus `@font-face`, name link to `/`), and a "copied, edit it there" stamp. The formcast repo's own page uses EB Garamond (the Michelangelus licence covers the site only).
- `tools/test_page.py` gains a `/formcast/` test: loads without console errors; sample 1 shows boxes; linking a field by the inspector updates the tag; Fill produces a PDF download; no horizontal scroll at 375 px; no request to huggingface.co on load; Michelangelus loads.
- A Work entry on the home page (title, one-paragraph description, link to `/formcast/`, a figure in the site's sketch-to-build style if `tools/make_figure.py` supports it). The copy is a draft; list it under the portfolio `CLAUDE.md`'s open items for the user to edit.
- `CLAUDE.md` in the portfolio gets a `/formcast/` item matching the classifier's (what it is, how to sync, don't edit by hand).
- All of this on a new branch in the portfolio repo, committed, not pushed or deployed. `check_site.py` and `test_page.py --all` must pass.

## 11. Workbench (`demo/server/`, `demo/web/workbench/`)

### 11.1 App layer (deliberately simple, each piece behind a small interface)
- `settings.py`: reads `.env` (LLM base URL, key, model, API format; data folder; similarity thresholds; host/port, default `127.0.0.1:8765`). The key never leaves the server; `GET /api/capabilities` reports only whether AI is configured.
- `store.py`: `TemplateStore` protocol (`list`, `get`, `put`, `delete`) with `FolderTemplateStore` over `demo/data/templates/*.json`. Comment: replace with your database.
- `records.py`: `RecordSource` protocol (`schemas()`, `list(schema)`, `get(schema, id)`) with `CsvRecords` (dotted column names; JSON in a cell for arrays) and `SqliteRecords` (one table per schema; array fields as JSON columns or child tables, builder's choice, documented).
- `documents.py`: uploaded or picked PDFs held in memory by id, each with its form map (built once, in a thread, cached) and rendered page PNGs.
- `jobs.py`: AI jobs in threads (`build`, `adapt`, `revise`), each with status, step events from `on_step`, result template or error, and cancel.
- `api.py`: the JSON API; `server.py`: `ThreadingHTTPServer` that serves `demo/web/` and routes `/api/*`. Start with `python -m demo.server` (documented in the README).

### 11.2 API
| method and path | does |
|---|---|
| `GET /api/capabilities` | engine flags; AI configured or not |
| `GET /api/forms` | demo forms in `demo/data/forms/` |
| `POST /api/documents` | upload a PDF or pick a demo form → document id, page count, sizes, kinds |
| `GET /api/documents/{id}/pages/{n}.png?dpi=` | page image |
| `GET /api/documents/{id}/matches` | `similarity` against every stored template, sorted, with the suggested action |
| `POST /api/seed` | `{doc, schema}` → seeded template (widgets and detections) |
| `POST /api/apply` | `{template, op, author}` → `{template, warnings}` or `{error}` |
| `POST /api/rebind` | `{template, doc}` → rebound template |
| `POST /api/jobs` | `{kind: build|adapt|revise, doc, schema or template, instruction, region}` → job id |
| `GET /api/jobs/{id}` | status, steps so far, result |
| `POST /api/jobs/{id}/cancel` | cancel |
| `POST /api/fill` | `{template, doc, record}` → filled PDF (base64) and report |
| `POST /api/smart-pdf` | `{template, doc}` → fillable empty PDF |
| `GET/PUT/DELETE /api/templates[/{name}]` | template store |
| `GET /api/schemas`, `GET /api/records?schema=`, `GET /api/records/{schema}/{id}` | record source |

Errors are JSON `{error, detail}` with 4xx for bad input and 5xx for library failures; uploads capped (50 MB).

### 11.3 Flow
1. Start screen: pick a demo form or upload a PDF, pick a schema.
2. Matches: if a stored template scores at or above the adapt threshold, offer *Reuse* (load it; rebind if the fingerprint differs), *Adapt* (`build_template(start_from=...)` job) or *Build new*.
3. Build new: start from the seed (by hand) or press Build with AI (a job; the progress panel shows each step's summary; cancel works; the result loads into the workbench with its history).
4. Edit on the workbench: any hand edit (`POST /api/apply`, author human); select an area and type an instruction to revise (a job, author agent); undo and redo across both.
5. Save to the store; pick a record; fill; download the PDF; read the report; download the smart PDF.

### 11.4 Demo data
Two or three schemas matched to demo forms from the dev corpus (reusing `forms/schemas/` where they fit), five or so fictional records each in CSV and in SQLite, and one stored template per demo form (an archived agent template, rebound) so the match prompt has something to find.

## 12. Testing

- `node --test tests/web`: conformance (transforms, schema, ops, values), geometry (rotation, crop box), undo stack, placeholder records, report shape. Run from pytest as well.
- Detection parity (§8.2), marked `slow`.
- pytest for library additions: signature, similarity calibration cases, rebind, fingerprint `None` handling, `on_step` and cancel with the scripted fake provider.
- pytest for the server: every API route with an in-process server on an ephemeral port, the fake provider for jobs, stores and record sources on temp folders.
- Playwright page tests (`tests/demo/test_pages.py`, marked so they can be skipped without Chromium): showcase (as in §10.5, plus pasting a schema, loading the agent template, a dropped PDF with the model stubbed by a fixture worker so the test needs no download; one `slow` test with the real model), workbench (start screen → seed → hand edit → undo → fake-provider build job → fill → report).
- Existing tests keep passing.

## 13. Left to the builder

- Module internals, file names inside the folders above, CSS details within the site's rules.
- Exact pinned versions of the vendored libraries.
- The ONNX pre/post-processing details (from the graph inspection).
- Similarity weights and MinHash details, calibrated on dev.
- SQLite layout for array fields.
- Placeholder-record value table.
- Whether `tools/make_figure.py` can make the Work entry figure; if not, a plain screenshot of the tool in the site's figure frame.

## 14. Risks to watch

- **Mobile memory** with FFDNet-S at 1216 px in WASM. Handled by keeping live detection off on phones by default (§6).
- **pdf-lib appearance generation** for comb fields and the `choice_mark` ellipse. Spike before building on it; if a custom appearance won't hold, record a ruling and draw the ellipse as a plain vector when on.
- **Archived templates and fingerprints**: archived templates predate the ink-map fingerprint (fill spec caveats), so the demo loads them with `check_fingerprint` off (browser) or rebinds them (workbench).
- **Sample licensing**: see §6; the manifest records each source.
