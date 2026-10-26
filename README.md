# formcast

formcast turns blank PDF forms into field templates. It detects form fields with
`commonforms`, then uses an LLM to name, type and structure them: a page pass maps
each page from a marked image of it, and an agent finishes the work from the page
images and the text and geometry of a page map (`vision=False` keeps it text-only).
The result is a JSON template of placed fields, each mapped to a path of your
JSON Schema.

## Install

Python 3.11 or later. From a checkout:

```sh
pip install -e .            # core
pip install -e ".[ocr]"     # plus OCR for scanned pages (pytesseract)
```

OCR also needs the Tesseract binary on `PATH` (`brew install tesseract`,
`apt install tesseract-ocr`). Without it, scanned pages keep their (usually
empty) PDF text and the page map notes that OCR was skipped. The CommonForms
detector weights are downloaded on first use.

## Usage

```python
from pathlib import Path

from formcast import LLMConfig, Template, apply, build_template, ops, preview, revise

schema = {"type": "object", "properties": {"name": {"type": "string"}, "dob": {"type": "string"}}}
llm = LLMConfig(
    base_url="https://api.openai.com/v1",  # any OpenAI-compatible endpoint
    api_key="...",
    model="...",
    api_format="responses",                # or "chat_completions" (the default)
)

# 1. The agent builds a template (tool calling is required, and image input unless vision=False).
result = build_template(schema, "intake.pdf", llm)
tpl = result.template                      # also: result.finished, .summary, .steps, .usage, .unplaceable

# 2. A person reviews the previews (one PNG per page, field boxes and ids drawn on).
for i, png in enumerate(preview(tpl, "intake.pdf")):
    Path(f"page{i}.png").write_bytes(png)

# 3. Human edits are ops too; each one is validated and recorded in tpl.history.
tpl, warnings = apply(tpl, ops.Move("f3", dx=4), author="human")

# 4. Or ask the agent to fix something, optionally pointing at a region (page, rect).
tpl = revise(tpl, "intake.pdf", "Phone goes on the line under Address", llm,
             region=(0, (72, 300, 540, 330))).template

# 5. The template JSON is the artifact to store.
Path("intake.template.json").write_text(tpl.to_json())
tpl = Template.from_json(Path("intake.template.json").read_text())
```

- `LLMConfig` fields: `base_url`, `api_key`, `model`, `api_format`
  (`"chat_completions"` or `"responses"`), and optionally `temperature`
  (`None` omits it), `max_output_tokens`, `timeout_s`, `max_retries`,
  `extra_headers`, `reasoning_effort` and `max_tokens_param`.
  `LLMConfig.from_env("PREFIX")` reads `PREFIX_BASE_URL`, `PREFIX_API_KEY`,
  `PREFIX_MODEL` and `PREFIX_API_FORMAT`. Its repr hides the key.
- `build_template` and `revise` take a `budget=SessionBudget(...)` (steps, tokens,
  wall clock). `build_template(..., start_from=old_tpl)` adapts an existing template
  to a new revision of a form. `fingerprint(pdf)` identifies a blank form across re-saves.
- `apply` without `context` validates against the template's own schema and page
  sizes. Pass `context=OpContext.from_template(tpl, form_map)` to also check widget
  names. Ops are plain data, so `{"op": "move", "id": "f3", "dx": 4}` works too.
- **What the model sees.** The page-map summary given to the model includes the
  PDF's file name (from a path; bytes have none). Pass the PDF as bytes, or rename
  the file, if the name itself is sensitive.

### Page images

By default (`vision=True`) the model also looks at the pages. The endpoint's model must
accept image input.

- **Page pass.** For a fresh template, `build_template` first runs a page pass
  (`formcast.map_pages`): one vision call per page, which maps the seeded boxes from a
  marked image of the page. The pass maps boxes only. It never adds any.
- **Agent.** The agent then starts from the page pass's template, checks it, and finishes
  the work. Its first message carries one marked image per page that has boxes.
  `start_from` skips the page pass but still sends images, and so does `revise`.
- **Result.** `result.page_pass` holds the pass's per-page outcomes, and `result.usage`
  includes its tokens. `on_step` is not called during the pass. Once the pass is done it
  gets one event for it (`step=0`, summary `"page pass"`). Raising `SessionCancelled` there
  stops the run with the pass's template. On a dense form the pass can take minutes.
- **`agent=False`** stops after the page pass. The result has summary `"page pass only"`
  and no steps.
- **`vision=False`** sends no images and runs no page pass: the agent works from the
  text page map alone, as before. Use it for a model without image input: with images on,
  such a model's endpoint rejects the first call, and the error suggests `vision=False`.
- **Cost.** The page pass is one call per page with boxes, up to 32,000 output tokens and a
  300 s timeout each, run up to four pages at a time. `SessionBudget`'s token caps count the
  agent's tokens only. The agent's images are resent with its first message every turn, about
  1.6k input tokens per page per step on Haiku 5.5.
- **Privacy.** Images go to the model endpoint only. They are never written to step reports,
  results, logs or the template.

How much the images help, on the eval corpus with Haiku 5.5 (dev, all 24 sessions per pipeline;
[vision design, section 11](docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md#11-results-2026-10-24)):

| Pipeline | Path, all boxes | Path, reachable boxes | Fill | Cost |
|---|---|---|---|---|
| `vision=False` (text-only agent) | 0.630 | 0.793 | 0.538 | $1.64 |
| `agent=False` (page pass only) | 0.739 | 0.947 | 0.573 | $0.15 |
| default (page pass, then agent) | 0.791 | 0.955 | 0.662 | $1.46 |

The gains are largest on scans (fill 0.41 to 0.63 on the fake scans). The one held-out run of the
default pipeline scored 0.804 path and 0.695 fill. "Reachable" boxes are the ones some seeded box
overlaps, which is all a mapping step can reach.

## Filling

A template fills the form it was built for with one data record. No model is
called.

```python
from formcast import fill, to_smart_pdf

pdf_bytes, report = fill(tpl, "intake.pdf", record)   # record: a dict matching tpl.schema
Path("filled.pdf").write_bytes(pdf_bytes)

for w in report.warnings:                              # a checklist, sorted by page and position
    print(w.severity, w.code, w.path or w.field, w.message)

smart = to_smart_pdf(tpl, "intake.pdf")                # same form, empty fields to fill by hand
```

Templates made in the browser demo have no fingerprint (`tpl.form.fingerprint is None`), so a checked fill refuses them. Call `tpl = formcast.rebind(tpl, "intake.pdf")` first: it checks the page count and sizes, then stamps this PDF's fingerprint, page kinds and signature onto a copy and leaves the fields alone.

Templates built before 2026-10-21 (before the ink-map fingerprint) no longer match their PDF's fingerprint: fill them with `check_fingerprint=False`, or rebuild them.

`report.warnings` is a flat list sorted by page and position. Record-level warnings
come first. For example (rects omitted):

```json
{"code": "invalid", "severity": "warning", "message": "invalid data at dob: '31/01/1990' is not a 'date'", "path": "dob"}
{"code": "unmapped_data", "severity": "warning", "message": "the record has a value at 'notes' but no template field maps it", "path": "notes"}
{"code": "rows_dropped", "severity": "warning", "message": "1 of 3 rows don't fit the table's 1 segment(s) and were dropped", "path": "medications", "field": "t_meds", "page": 0}
{"code": "shrunk", "severity": "info", "message": "text shrunk to 6 pt to fit", "path": "name", "field": "f_name", "page": 0}
{"code": "missing", "severity": "warning", "message": "no value for phone", "path": "phone", "field": "f_phone", "page": 0}
```

The report also has:

- `report.fields` and `report.tables`: per box, the widget each value went to (a
  form widget it `named` or `adopted`, or one it `created`), the value and the
  font size.
- `report.paths`: per schema path, what was rendered where.
- `report.gaps`: boxes that came out empty.
- `report.counts`.
- `report.to_json()`.

- **The output keeps real form fields; there is no flatten mode.** Every value
  sits in an editable AcroForm field with its own appearance stream. For a flat
  PDF, flatten it outside formcast, for example by printing to PDF.
- **Fill writes into the form's own widgets.** A field with a name uses that
  widget. A field or table cell drawn over an unused widget adopts it. Every
  other box gets a new widget. Widgets the template doesn't use are left as
  they were.
- **Refilling a filled PDF works.** Fields whose data is missing are cleared, so
  nothing from an earlier record is left behind. The fingerprint check ignores
  filled-in values, so a filled copy still matches its template. The exception
  is a blank form whose text fields already carry default values: its filled
  copies no longer match, so refill those with `check_fingerprint=False`.
- **Nothing is dropped silently.** A record value that no box ends up showing
  (say an enum value no checkbox stands for) is reported as `not_shown`, unless
  another warning already explains it. `False` and `0` are exempt: an unticked
  box shows them. A template that uses an unknown transform raises
  `FormcastError`.
- **Validation is lenient by default.** Bad data is reported as `invalid` and
  the rest is filled. `strict=True` raises `FormcastError` instead.
  `check_fingerprint=False` fills a PDF whose fingerprint differs from the
  template's, and records `fingerprint_mismatch`.
- **Text that doesn't fit is kept whole.** It is shrunk to the field's minimum
  font size and reported as `overflow`, so on paper the end is cut off. Only a
  character limit (a comb box, or a field's own maximum length) cuts the value,
  and that is reported as `truncated`.
- **Only Latin text renders reliably.** Form fields use the standard PDF fonts,
  which cover Western European characters. Other scripts are stored in the
  field correctly, but may show as blanks in some viewers. They are reported as
  `unencodable`.
- `preview(tpl, "intake.pdf", data=record)` draws the template's boxes over the
  filled PDF.

## The sandbox is not a security boundary

The agent's Python runs in a separate worker process (`formcast.sandbox`) with a
per-step timeout, a memory limit where the OS enforces one (not macOS), a
restricted set of builtins (no `open`, `exec`, `eval`, `compile`, `input`, ...)
and an import allowlist. Its edit ops are only recorded there; the parent process
validates and applies them. This keeps a *confused* model from doing damage by
accident. It does **not** contain a *hostile* model: known escape routes, such as
attribute walks to real modules and builtins, are left open and listed in
`src/formcast/sandbox/worker.py`. The worker runs as your user with your
filesystem and network. Use a model and endpoint you trust, or run formcast inside
a container or VM if you need real isolation.

## Development

```sh
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -e ".[ocr,dev]"
.venv/bin/pytest -q
```

Tests marked `slow` (model downloads) and `llm` (real model calls) are skipped by
default; run them with `-m slow` / `-m llm`. Credentials for the `llm` tests and the
eval come from the git-ignored `.env` as `*_EVAL_*` variables, for example
`ANTHROPIC_EVAL_BASE_URL`, `ANTHROPIC_EVAL_API_KEY`, `ANTHROPIC_EVAL_MODEL` and
`ANTHROPIC_EVAL_API_FORMAT` (likewise `OPENAI_EVAL_*`). Only those prefixes are read.

## Evaluation

The corpus is in `forms/` (`dev` for tuning, `heldout` scored only in final runs;
ground truth in `forms/gt`). The scripts are in `eval/`:

```sh
python eval/baseline_detection.py --split dev          # detectors alone, no LLM
python eval/agent_eval.py --split dev --seed-only      # the untouched seeded template, no LLM
python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --variant both   # agent runs (paid)
python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL --pipeline pages+agent --max-usd 3   # with page images
python eval/agent_eval.py --rescore eval/results/agent_<...>.json                # re-score, no LLM
```

`--pipeline` picks what builds each template: `agent` (the default; text only, as `vision=False`),
`pages` (the page pass only, `agent=False`) or `pages+agent` (the library's default). The pipeline is
appended to the run name. `--max-usd` stops starting sessions once the finished ones cost more than
that (prices are built in for Haiku 5.5). Results go to `eval/results/` (json and md per run). The
saved templates of the committed runs up to 2026-10-21 are archived in `eval/results/templates.tar.gz`
(the page-image runs' templates are local-only); extract it with `tar -xzf eval/results/templates.tar.gz`
from the repo root before `--rescore`.
Step logs and overlays are local-only.

## Demo

A browser demo in [demo/web/](demo/web/) detects form fields, links them to a JSON Schema, and fills the
form from a record. It runs entirely in the page: vanilla HTML and ES modules, no build step, with
pdf.js, pdf-lib and onnxruntime-web vendored in `demo/web/vendor/`. The detector is FFDNet-S, fetched
from Hugging Face on first use and cached by the browser. It is a review tool, not the whole pipeline:
there is no AI in the browser, and scanned pages are not supported there (the page has no OCR, so
scans get boxes but no labels). The agent's work is shown through templates it already built: on a
sample form, Auto map pairs the agent's mapping with the boxes the detector found, by position. Those
templates come from an earlier, text-only eval run (`2026-10-21_defer-placement`), before the page pass;
the page's Results table is the current code with page images, with a line for the same code run
text-only. Fill shows the filled PDF on the page before it is downloaded.

To open it locally, serve `demo/web` and visit the showcase:

```sh
cd demo/web && python -m http.server 8000   # then open http://localhost:8000/showcase/
```

The [Workbench](#workbench) below serves the same files from a local Python server and adds the
library's agent, form matching and server-side fills.

Tests:

```sh
node --test "tests/web/*.test.js"                      # JS ports, checked against Python fixtures
.venv/bin/python -m pytest tests/web tests/demo        # fixtures, fills read back with PyMuPDF, browser tests
.venv/bin/python -m pytest -m slow tests/demo tests/pagemap/test_detect_onnx.py   # runs FFDNet-S (downloads it once)
```

The browser tests use Playwright, which comes with the `dev` extra
(`pip install -e '.[ocr,dev]'`), and Chromium (`playwright install chromium`). They skip
themselves when either is missing. The `slow` set needs both too: it runs FFDNet-S in the page
as well as in Python.

Every JS port of library logic is checked against fixtures generated by running the Python library.
After changing the library, regenerate them, and use `--check` to see whether they are stale:

```sh
.venv/bin/python demo/tools/export_conformance.py          # rewrite tests/web/fixtures
.venv/bin/python demo/tools/export_conformance.py --check  # exit 1 if any fixture is stale
```

The showcase's sample forms and its results table are built from committed files:

```sh
.venv/bin/python demo/tools/build_showcase.py samples   # sample bundles (W-9, W-4, I-9); runs FFDNet-S
.venv/bin/python demo/tools/build_showcase.py results   # results.json from eval/results/
```

The showcase page (`demo/web/showcase/`) is also published at landonthornton.com/formcast/. That copy is
made by `tools/sync_formcast.py` in the portfolio repo: edit the page here, then run
`python3 tools/sync_formcast.py <path to this checkout>` from the portfolio repo's root. The sync copies
`demo/web/showcase/`, `core/`, `engines/browser/` and `vendor/`, and swaps in the site's licensed font (never
committed here), so keep the page on EB Garamond. It fails if the font link, the stylesheet and script tags,
or the "Landon Thornton" link in `showcase/index.html` change; update the script when they do.

## Workbench

The workbench (`demo/server/` and `demo/web/workbench/`) is the browser demo's review screen backed by a
small Python server that calls the library: detect and seed a template, match a PDF against stored
templates, build, adapt or revise with the agent, edit by hand with undo, save, and fill a record into
the PDF. The server uses only the standard library. It is a local, single-user tool and not a product.
**Run it on your own machine only: the agent's sandbox is not a security boundary** (see above), and the
server holds your model API key. It binds `127.0.0.1`, and it refuses requests whose `Origin` or `Host`
is not its own, so another web page open in your browser cannot drive it or spend your API credits.

```sh
pip install -e '.[ocr,dev]'      # `dev` is only for the tests
python -m demo.server            # then open http://127.0.0.1:8765/workbench/
```

Run it from the repository root. Without the install it still starts from a clone, because
`python -m demo.server` adds `src/` to the path. Flags: `--host`, `--port` and `--data-dir` (a folder
with `forms/`, `schemas/`, `records/`, `records.sqlite` and `templates/`; default `demo/data`). Binding a wildcard
address (`0.0.0.0`) does not make a usable URL: bind your machine's own IP with `--host` if you want
to reach it from another machine, and know that anyone who can reach that address can use it.

When it starts, the server loads the field detectors (CommonForms FFDNet-L and FFDetr) on a
background thread and logs its progress. The first time, this downloads their weights from Hugging Face,
which can take a few minutes; after that they load from the cache in seconds. Pages are served and PDFs
match while it runs. Reading a PDF that needs detection (one with pages that have no form fields) waits
for the load; a fully fillable PDF does not. If the download fails (no network), the server logs it and
tries again when such a PDF is opened.

Settings come from the git-ignored `.env` and the environment. `FORMCAST_DEMO_BASE_URL`,
`FORMCAST_DEMO_API_KEY` and `FORMCAST_DEMO_MODEL` turn the AI features on, and `FORMCAST_DEMO_API_FORMAT`
(`chat_completions`, the default, or `responses`) picks the API. The server calls the library with its
defaults, so the model must accept image input: a build runs the page pass first, and build, adapt and
revise send page images. There is no setting for `vision=False` yet. `FORMCAST_DEMO_HOST`,
`FORMCAST_DEMO_PORT` and `FORMCAST_DEMO_DATA_DIR` set the rest. With the three AI values missing, the
AI controls are hidden; hand editing, matching, saving and filling still work. The key stays on the
server and no response contains it.

The flow:

1. **Start.** Pick a demo form or drop a PDF, then pick a schema. The server starts reading the PDF
   (its form map: text, widgets and detected boxes) in the background as soon as it opens. While a
   reading runs, other PDF work on the server (page images, filling) waits for it; matching does not.
2. **Match.** The PDF is scored against every stored template (`formcast.similarity`). The scores come
   from the PDF's fingerprint, text and layout, which the server reads first, before the form map, so
   the match prompt appears at once even while detection runs. The server
   offers *Reuse* at 0.95 or above, *Adapt* at 0.60 or above (an agent job that starts from the stored
   template), or *Build new*. The thresholds are in `demo/server/settings.py` and were calibrated on the
   dev forms only.
3. **Edit or revise.** Edit by hand, or draw an area and type an instruction for the agent. Undo and
   redo cover both.
4. **Save** to the template store. Saves go to `local/templates/` in the data folder
   (`demo/data/local/templates/`, git-ignored), never into the shipped `demo/data/templates/`. A saved
   template hides a shipped one with the same name; the shipped ones cannot be deleted.
5. **Fill.** Pick a record and fill. If the stored template was made for a different version of the PDF,
   the server answers 409 and the page offers *Rebind* (`formcast.rebind`: the same pages, this PDF's
   fingerprint) or *Fill anyway*. Templates made in the browser have no fingerprint and fill with a note.

AI work runs as a background job with a step-by-step panel and a Cancel button; cancel takes effect
between steps. A build's panel starts with "Page pass" once the whole pass is done (on a dense form that
can take minutes), then the agent's steps; a cancel during the pass takes effect when it ends. If you
close the panel, the finished result is offered as *Load result* until you load or discard it.

Where the app layer ends: the library knows nothing about a store, records or HTTP. The server
reaches them through two small interfaces, so you can swap them for your own database.
`TemplateStore` (`demo/server/store.py`: `list`, `get`, `put`, `delete`; the default keeps one JSON
file per template and raises `StoredTemplateError` for a file that is not a valid template) and
`RecordSource` (`demo/server/records.py`: `schemas`, `list`, `get`; the defaults read CSV files and a
SQLite file, and raise `RecordSourceError` when a source cannot be read at all). To use your own,
pass them to `make_server(settings, store=..., records=...)` (`demo/server/server.py`); the
`python -m demo.server` command has no flag for this, so write a short script:

```python
from demo.server.detectors import DetectorWarmup
from demo.server.server import make_server
from demo.server.settings import Settings

httpd = make_server(Settings.from_env(), store=MyTemplateStore(), records=MyRecordSource(), detectors=DetectorWarmup())
httpd.detectors.start()  # load the detectors in the background; without `detectors`, the first reading loads them
httpd.serve_forever()
```

(`DetectorWarmup` is in `demo/server/detectors.py`.)

Settings are one object, built in `Settings.from_env`. Odd records (a bad JSON cell, NULLs, extra columns) come back with
what could be read, and the fill report says what is wrong.

Tests:

```sh
.venv/bin/python -m pytest tests/demo                   # server, stores, jobs, and the browser flows
.venv/bin/python -m pytest -m slow tests/match tests/demo
```

The browser flows use Playwright, which comes with the `dev` extra, and Chromium
(`playwright install chromium`); they skip themselves when either is missing. Agent flows run against
the scripted fake provider, so no test calls a model. See [Demo](#demo) for the browser-only version and
the JS tests.

## Spec

Design spec: [docs/superpowers/specs/2026-10-20-form-projection-design.md](docs/superpowers/specs/2026-10-20-form-projection-design.md)
(results of the first eval in section 14.3).
Review apps (browser demo and workbench): [docs/superpowers/specs/2026-10-21-formcast-review-apps-design.md](docs/superpowers/specs/2026-10-21-formcast-review-apps-design.md).
Fill design: [docs/superpowers/specs/2026-10-21-formcast-fill-design.md](docs/superpowers/specs/2026-10-21-formcast-fill-design.md).
Vision page mapping (the page pass and page images, with results in section 11):
[docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md](docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md).
Plan: [docs/superpowers/plans/2026-10-20-formcast-agent-layer.md](docs/superpowers/plans/2026-10-20-formcast-agent-layer.md)
