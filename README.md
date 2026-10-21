# formcast

formcast turns blank PDF forms into field templates. It detects form fields with
`commonforms`, then uses an LLM agent to name, type and structure them. The agent
never sees the page image: it works from the text and geometry of a page map.
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

# 1. The agent builds a template (tool calling is required).
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
python eval/agent_eval.py --rescore eval/results/agent_<...>.json                # re-score, no LLM
```

Results go to `eval/results/` (json and md per run). The saved templates of the
committed runs are archived in `eval/results/templates.tar.gz`; extract it with
`tar -xzf eval/results/templates.tar.gz` from the repo root before `--rescore`.
Step logs and overlays are local-only.

## Spec

Design spec: [docs/superpowers/specs/2026-10-20-form-projection-design.md](docs/superpowers/specs/2026-10-20-form-projection-design.md)
(results of the first eval in section 14.3).
Fill design: [docs/superpowers/specs/2026-10-21-formcast-fill-design.md](docs/superpowers/specs/2026-10-21-formcast-fill-design.md).
Plan: [docs/superpowers/plans/2026-10-20-formcast-agent-layer.md](docs/superpowers/plans/2026-10-20-formcast-agent-layer.md)
