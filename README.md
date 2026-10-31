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
  1.6k input tokens per page per step on Haiku 5.5. On a document of more than 12 pages with more
  than 12 pages of boxes, the agent gets images of the 12 pages the page pass left the most boxes
  unlinked on, and page-map text only for pages with boxes (`formcast.agent.api.LONG_DOCUMENT_PAGES`).
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
- `report.required_gaps`: the gaps whose box is marked required.
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

## One-shot: a PDF and your data

You don't need a schema or a template to start. Give formcast a blank PDF and the data you have (a CSV,
an Excel sheet, JSON records or plain text), and the model does the three things a person would:

```python
from pathlib import Path

from formcast import (
    build_form_map, build_template, derive_schema, extract_records, fill, map_table, read_table, records_from,
)

pdf = Path("application.pdf").read_bytes()
fm = build_form_map(pdf)                    # llm: an LLMConfig, as in Usage above

# 1. The model designs the form's data schema from its pages (one call, page images included).
schema = derive_schema(pdf, llm, form_map=fm).schema

# 2. It links the form's boxes to that schema: the page pass, then the agent checks it (agent=False: faster).
tpl = build_template(schema, pdf, llm, form_map=fm).template

# 3a. A table: the model writes a DataMapping from the column names and a few samples per column.
#     The mapping (not the model) then turns every row into a record, so the rows are never sent.
table = read_table(Path("applicants.xlsx").read_bytes(), "applicants.xlsx")    # .csv .tsv .xlsx .json
mapping = map_table(schema, table, llm).mapping
records = records_from(mapping, schema, table)

# 3b. Free text: the model reads it into records of the schema.
records = extract_records(schema, "Priya Raman, 19 Cedar Lane, Menomonie WI ...", llm).records

pdf_bytes, report = fill(tpl, pdf, records[0])
```

- **`derive_schema`** follows the form: nested objects for its sections, `format: date` for dates,
  booleans for yes/no questions, enums for choose-one options, arrays of objects (with `maxItems`) for
  tables and repeated blocks, and the printed label as each property's `description`. Signatures and
  office-use boxes are left out. `instructions="..."` passes your own words along.
- **A `DataMapping`** is plain JSON: one rule per schema path, taking a `column`, a `template` of
  `{Column}` placeholders, or a fixed `value`, then `transforms` (`split`, `date`, `map`, `boolean`,
  `number`, `list`, `regex`, `digits`, `default`, ...). Values are then coerced to the schema's types
  (`"Y"` to `true`, `"$41,200"` to `41200`, `"MARRIED"` to the enum's `"married"`). Store it next to the
  template and the next file with those columns fills with no model call (`mapping.fits(columns)`).
  `direct_mapping(schema, columns)` needs no model at all when the columns are named by schema path
  (`applicant.name`, `employers[0].salary`). `check_mapping` drops rules that can't apply and says why.
- **What the model sees.** `derive_schema`: the page images and page-map text. `map_table`: per column,
  its name, inferred type and up to five sample values, never the full rows. `extract_records`: the
  whole text. A reply that isn't the JSON asked for is asked for once more, then raises `FormcastError`.
- `profile(table)` and `data_schema(table)` describe the data's own shape (types, fill, samples).
  Excel needs `pip install -e ".[xlsx]"` (openpyxl).

Measured on the 3-page Menomonie job application with Haiku 5.5 (one run each, 2026-10-27):

| Step | Time | Tokens (in / out) |
|---|---|---|
| `derive_schema` (37 fields) | 10 s | 11k / 2.3k |
| `build_template`, page pass only (`agent=False`) | 12 s | 19k / 5k |
| `build_template`, page pass then agent (default) | 90-100 s | 300k / 20k |
| `map_table`, 22-column CSV | 8 s | 2.7k / 2k |
| `extract_records`, one paragraph | 3 s | 2.8k / 0.7k |
| refill from a saved mapping | instant | none |

On that form the page pass alone stacked the two repeated "Previous Employment" blocks onto one line;
the agent's check fixed them. That is why the agent runs by default.

**Only the PDF is required.** With no data and no schema, the model designs the schema and links the
boxes; data can come later. Without a model, `build_form_map` still detects the boxes.

### Long documents

A subscription package is mostly agreement text, with the forms in sections (a cover page, signature
pages, a questionnaire, annexes). Three things keep that affordable:

- `build_form_map(pdf, skip_prose=True)` runs no detection on pages that read as running text
  (`looks_like_prose`: most lines span the page, almost no "Label:" lines, few rules). It is off by
  default in the library and on in the workbench. On the eval corpus it skips no page with fields.
- `derive_schema` sends images of one page per section of forms (largest sections first), then of the
  pages with the most blanks, up to 12; full page-map text for pages with blanks and a short excerpt of
  every other page.
- After the page pass, the agent works from the 12 pages it left most unlinked (see *Cost* above).
- Or pick the pages yourself: `build_form_map(pdf, detect=False)` searches no page (text, widgets and OCR
  only), and `detect_on_page(page, page_map)` searches one page later; it also takes a page skipped as
  prose. `build_form_map(..., on_progress=fn)` reports each page's steps as it reads them.

Measured on a public 90-page fund subscription package (flat, no form fields; 30 pages of agreement
skipped as prose), Haiku 5.5, 2026-10-28:

| Step | Time | Tokens (in / out) |
|---|---|---|
| Page map, 60 pages detected | 198 s | none |
| Page map, detection off (`detect=False`) | 9 s | none |
| `detect_on_page`, one page | about 2 s | none |
| `derive_schema` (90-130 fields across runs) | 35-75 s | 57k / 8-17k |
| Page pass, 37 pages | about 50 s | 330-450k / 26-37k |
| Agent check | 2-3 min | 1.2-1.5M / 15-20k |
| `extract_records`, one paragraph | 10-12 s | 9-13k / 2-3k |

Without the agent check, the free-text investor filled 31 fields: the cover page, signature-page date and
fund choice, and the first questionnaire page complete. The agent check linked more boxes in one run
(212 against 195) and in another stopped at `SessionBudget`'s 1.5M input-token cap with its template
kept. On a long package the faster `agent=False` (the workbench's "one pass") is the better trade.
Known gaps: detection misses some signature-block lines (name and title under the signature), so
nothing fills them.

## Extraction: many filled PDFs to rows

The other direction: you have hundreds or thousands of *filled* PDFs of one form (fillable, or flattened with a
text layer; there is no OCR here) and want a table of checked values. An **extraction template** is made once
and then reads every document with no model call, in well under a second a document.

```python
from pathlib import Path

from formcast import ExtractionTemplate, run_batch, scan
from formcast.extract import extract
from formcast.extract.batch import Results
from formcast.extract.export import export

pdfs = sorted(Path("incoming").glob("*.pdf"))
report = scan(pdfs)                         # groups by layout; report.main is the biggest group
template = ExtractionTemplate.model_validate_json(Path("client.json").read_text())

doc = extract(template, pdfs[0])            # one document: doc.fields, doc.record()
summary = run_batch([template], pdfs, "out", workers=4)    # results.jsonl + run.json in out/; run it again to resume
export(Results.load("out"), [template], "extracted.xlsx", format="xlsx")
```

```sh
python -m formcast.extract scan incoming/ [--first-page]
python -m formcast.extract run client.json incoming/ --out out/ [--workers 4] [--timeout 120]
python -m formcast.extract export out/ --template client.json --format csv --to extracted/
python -m formcast.extract ai out/ --template client.json    # opt-in model pass over failing documents (below)
```

- **A template** is plain JSON: the JSON Schema the values must meet (with its checks: `pattern`, `enum`,
  `required`, ...), the form's learned layout (its fixed printed lines, used to recognise the layout again) and,
  for each schema path, a reader: ordered strategies (`widget` form field, `box` words inside a rectangle,
  `anchor` words next to a label, `phrase` words after a phrase in running text, `choice` check marks) and the
  `clean` transforms of the One-shot mapping above. The first strategy that finds something wins, except that
  when its value fails a check the next strategies are tried: a later value that passes replaces it, and is
  `unsure` when it differs from the first (two reads disagree, so it is never `found`). A repeated
  group is an array of objects with `maxItems` (4 without one), read item by item (`owners[0].name`). Write one
  by hand, or build one from sample documents with a model (below).
- **Statuses.** Every field of every document ends as `found`, `unsure` (two reads disagree, a label sits in
  more than one place, the printed lines around a box are not where the layout has them, or one answer was read
  for two fields; the reason is kept), `failed_check` (the value is kept as read and fails the schema),
  `missing` (nothing found), `absent` (an unused repeated item) or `not_on_form` (the template says this
  version lacks it). A value is never dropped or silently changed; raw and cleaned values are both kept.
- **Routing.** Each document goes to the template whose form it matches (the blind fingerprint, else the layout
  score). One that fits no template, or cannot be read (encrypted, corrupt, empty, a file that crashes the
  reader), is set aside with the reason and the run goes on. `scan` shows which layouts a folder holds before
  any template exists.
- **Runs resume.** `run` appends one line per document to `out/results.jsonl` as it goes. Run it again after a
  crash or with more files in the folder and it reads only the new ones; a torn last line is read again.
  `run.json` keeps each template's sha256: a resume with an edited template is refused (use a new `--out`).
  `--workers` defaults to one per CPU; progress goes to stderr every 100 documents, then the summary
  (total, complete, with problems, no template, unreadable, internal errors, seconds). `internal errors` counts
  documents a fault of the code or of a template stopped ("internal error: ..."), not bad files.
  `--timeout` (seconds, default 120) sets aside a document that takes longer as "the reader hung", and the run
  goes on. `--workers 1` (`workers=1`) reads in-process, with no crash isolation and no timeout: a file that
  crashes the reader ends the run, and every resume meets it again; one that hangs stalls the run. Use two or
  more workers for a folder you have not seen.
- **Export** writes `values` (one row per document read), `status` (the same cells holding the status),
  `fields` (each column's path, title, definition, type and checks) and `set_aside` (the failed documents) as
  one xlsx workbook, or as Parquet or CSV files in a folder. The export takes the templates again
  (`--template`) because `run.json` keeps only their ids and hashes, and refuses a template whose content
  differs from the run's; it refuses two paths that make one column. xlsx needs
  `pip install -e ".[xlsx]"`, Parquet `pip install 'formcast[parquet]'` (pyarrow). CSV values are written
  unaltered, so a value that starts with `=`, `+` or `-` is not escaped: treat the file as data, not as a
  spreadsheet to open blind.
- Exit code 0, or 2 with a one-line message for bad arguments, an invalid template, an unwritable `--out` or `--to`, or any `FormcastError`.

Measured on the generated test corpus (client-information forms, fillable and flattened) on this machine.
`extract()` is the median per document; `run_batch` is the best of 3 runs on 110 documents, five copies of the
22-file corpus (about 10 of them are the other form, set aside); `scan` is warm, in a single process:

| Step | Time |
|---|---|
| `extract()`, one 2-page document | 0.07-0.08 s |
| `extract()`, 6-page IRS W-9 | 0.48 s |
| `run_batch`, 110 documents, 1 / 2 / 4 workers | 8.8 / 14.9 / 22.8 documents/s |
| `scan`, per document | 82 ms (30 ms with `first_page_only`) |

About 60% of a batch document's time goes to the fingerprint tried first; routing by layout alone would roughly
double the speed. The corpus is simpler than real documents: measure yours before planning a large run.

### Building a template from samples

A template can be built from 1 to 20 filled samples of the form (3 to 10 is plenty) with a model. The model is
called only while the template is built; the finished template reads every document with no model call, and an
edit to it is proven again on the samples with no model call either.

```python
from formcast.extract import SamplePdf, SetupSpec, add_line, build_setup

samples = [SamplePdf(p.name, p.read_bytes()) for p in pdfs[:5]]
result = build_setup(samples, SetupSpec(name="Client info"), llm, on_stage=print)
print(f"{result.proof.ready} of {result.proof.total} fields ready", result.warnings)
more = add_line(result.template, "wire details", result.samples, llm)   # fields for one more line
Path("client.json").write_text(more.template.model_dump_json(by_alias=True, indent=2))
```

- **Three starts** (`SetupSpec.start`). `"ai"`: the model designs the fields from the sample with the most boxes
  (`derive_schema`). `"lines"`: you list what you want, one line each (`lines=["tax ID", "wire details"]`), and the
  model turns each line into the fields it asks for (a line may become several: bank and account); the template
  keeps which paths each line became. `"destination"`: the fields of a schema you already have (`destination=`):
  the model says which of them this form has, and the rest are marked "not on this form".
- **Stages**, each reported to `on_stage` as it starts and ends (`StageEvent(key, status, detail)`): `read` (each
  sample's form map and form-field values), `detect` (the field detectors find the blanks on pages without form
  fields; `detect="auto"` skips prose pages, `"all"`, `"manual"` with `pages`, or `"off"` to skip the stage),
  `fields` (the start above), `readers` (the model proposes how to read every field: form-field names, a box,
  printed labels, phrases in running text, check boxes; code keeps only what is on the samples and warns of the
  rest), `check` (the model reads each sample for a second opinion, and the readers are proven against it). An
  exception from `on_stage` (such as `SessionCancelled`) stops the build.
- **The proof.** A field is *ready* when, on every sample, its readers read what the model read there (or both
  find nothing) and at least one sample has an answer; a field no sample fills cannot be proven. The template
  keeps each sample's sha256 and the model's readings, so `prove(template, pdfs)` re-checks it after any edit
  (`apply_edit`) with no model call.
- `llm` is an `LLMConfig` (one provider is opened for the whole build and closed after) or a ready provider;
  calls of one stage run side by side (`max_parallel`, 4 by default). A sample that cannot be read is refused,
  by name, before any model call.

### The AI pass over a run's failing documents

A run leaves some documents with a required field `missing` or `failed_check`. The AI pass is an opt-in step
after the run (`run_batch` and `extract` still never call a model) that asks a model for those fields only:

```python
from formcast.extract import ai_pass

summary = ai_pass([template], "out", llm, max_parallel=4)   # llm: an LLMConfig or a ready provider
print(summary.documents, summary.answered, summary.failed, summary.usage)
```

```sh
export FORMCAST_EXTRACT_BASE_URL=... FORMCAST_EXTRACT_API_KEY=... FORMCAST_EXTRACT_MODEL=...
python -m formcast.extract ai out/ --template client.json
```

- **What it asks.** For each document that was read, whose template has a *required* field (named in its object's
  `required`; inside a repeated group, in the item's) that is still `missing` or `failed_check` after your review
  decisions, the model is shown the document (its text, and page images unless `vision=False`) and asked for those
  fields. Every other field of the document keeps what the template read, and a document whose required fields were
  read, or that was set aside, is never sent.
- **What it costs.** One model call per such document, so the cost follows the number of failing documents, not the
  size of the run; the summary gives the tokens spent. Documents are asked `max_parallel` at a time; one that the
  model cannot answer usably (after one retry) is counted in `failed` and asked again by the next pass. A second pass
  over a run asks nothing it already asked: an answer, even "not found", is kept.
- **Its answers are always reviewed.** They are stored apart, in `out/ai.jsonl`, and the run's own results are never
  changed. A field with an answer reads as `unsure` by strategy `ai` (reason "read by the AI; check it"), so it counts
  as a problem in the summary and the `status` export until a person accepts it, types another value or marks the
  field not in the document (`Results.decide`); a review decision always wins over the answer. The model's value is
  not checked against the schema: a wrong shape shows as `unsure` for you to see, never as `found`.
- Exit code 2 with one line when `FORMCAST_EXTRACT_BASE_URL`, `_API_KEY` or `_MODEL` is not set, `out/` is not a run,
  or a template is not the run's.

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
small Python server that calls the library. Its front door is the [one-shot flow](#one-shot-a-pdf-and-your-data):
give it a PDF and your data, and it designs the schema, links the boxes, maps the data and shows the
filled PDF. Behind that: match a PDF against stored templates, build, adapt or revise with the agent,
edit by hand with undo, save as a template, and fill records into the PDF, from the page or from code. The server uses only the standard library. It is a local, single-user tool and not a product.
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
`FORMCAST_DEMO_PORT` and `FORMCAST_DEMO_DATA_DIR` set the rest.
`FORMCAST_DEMO_ENABLED=false` (or `python -m demo.server --no-demo`) takes the demo data out of the app:
the demo forms, the shipped templates, and the demo schemas and records. The start screen then offers
your saved templates and your own PDFs (just *Choose a PDF* until you have saved one), matching scores
only your saved templates, and fills come from your data or a sample record. Saves still go to
`local/templates/` in the data folder. It is on by default. With the three AI values missing, the
AI controls are hidden; hand editing, matching, saving and filling still work. The key stays on the
server and no response contains it.

The flow:

1. **Start: the form, your data, and (folded away) the schema.** The form is a *saved template* (it
   keeps its PDF, boxes and schema: no detection, no AI), a demo form, or your own PDF. Your data is
   optional: a CSV, TSV, Excel or JSON file, or pasted text (records, a table, or plain prose). A demo form
   picks its own stored schema; your own PDF defaults to *the AI designs it from the form*. You also pick
   how the boxes are linked: by the AI with the agent checking its page pass (the default), by the page
   pass alone (faster), or by hand. The server starts reading the PDF (its form map: text, widgets and
   detected boxes) in the background as soon as it opens, and the start screen shows the reading as it
   goes: the page and step under way (reading text, OCR, finding the blanks with which detector), a cell
   per page (boxes found, searched and empty, skipped as prose, fillable), the counts and a rough time
   left. While a reading runs, other PDF work on the server (page images, filling) waits for it;
   matching does not.
   *I'll pick the form pages myself* reads the PDF with no detection, which takes seconds even for a
   90-page agreement. Turn to each page with a form and press *Find the blanks on this page*: the
   server searches that page alone (about 2 s) and its boxes join the template as one undo step. *Build
   with AI* then designs the schema from the pages you searched and links their boxes. The same button
   shows on any page an automatic reading skipped as prose.
2. **Match.** The PDF is scored against every stored template (`formcast.similarity`). The scores come
   from the PDF's fingerprint, text and layout, which the server reads first, before the form map, so
   the match prompt appears at once even while detection runs. The server
   offers *Reuse* at 0.95 or above, *Adapt* at 0.60 or above (an agent job that starts from the stored
   template), or *Build new*. The thresholds are in `demo/server/settings.py` and were calibrated on the
   dev forms only.
3. **One-shot mapping.** On *Build new* the seed goes on screen, and a `oneshot` job runs its stages:
   read the form, design the schema, link the boxes, read your data, map it. The panel lists each stage
   as it runs, the agent's steps, and the tokens used. The result loads as one undo step (back to the
   seed), and the fill drawer opens on your data's records with the first one filled. A saved template,
   or *Reuse*, runs only the data stages: columns named by schema path, or matching a mapping saved with
   the template, need no AI at all.
4. **Edit or revise.** Edit by hand, or draw an area and type an instruction for the agent. Undo and
   redo cover both.
5. **Fill.** *Your data* lists the mapped records: each shows as JSON you can edit before filling, under
   the data mapping that made it (form field ← column, template or value, with transforms; the columns
   left out; the AI's notes). *Add data* maps another file or text for the template on screen. *Data
   schema* downloads the schema as a JSON file. Stored records and a sample record are there too. If
   the stored template was made for a different version of the PDF, the server answers 409 and the page
   offers *Rebind* (`formcast.rebind`: the same pages, this PDF's fingerprint) or *Fill anyway*.
   Templates made in the browser have no fingerprint and fill with a note.
6. **Save** to the template store, with the PDF and, when the AI (or a saved mapping) mapped your data,
   that data mapping. Saves go to `local/templates/` in the data folder (`demo/data/local/templates/`,
   git-ignored; the PDF and mappings in `assets/<name>/`), never into the shipped `demo/data/templates/`.
   A saved template hides a shipped one with the same name; the shipped ones cannot be deleted.

Filling from code. A saved template takes data without the browser:

```sh
curl -X POST http://127.0.0.1:8765/api/templates/NAME/fill -H 'Content-Type: application/json' \
     -d '{"record": {"applicant": {"name": "Ann Lee"}}}' -o filled.pdf
curl -X POST http://127.0.0.1:8765/api/templates/NAME/fill -F file=@applicants.csv -o filled.zip
curl -X POST http://127.0.0.1:8765/api/templates/NAME/fill -H 'Content-Type: application/json' \
     -d '{"text": "Priya Raman, 19 Cedar Lane, ..."}' -o filled.pdf
curl http://127.0.0.1:8765/api/templates/NAME/schema -o NAME.schema.json   # what records must look like
```

The body is one `record`, a list of `records`, free `text` (read by the model), or a table `file`
(mapped by schema path, then by a saved mapping, then by the model). One record answers a PDF; several
answer a zip with one PDF per record and `reports.json`. `X-Formcast-Records` and `X-Formcast-Warnings`
carry the counts. The server's routes for the one-shot flow are `POST /api/data` (a file or `{"text"}`,
answering the data's columns, types and samples), the `oneshot` job kind of `POST /api/jobs`,
`POST /api/documents {"template": name}` (open a saved template's PDF), and `PUT /api/templates/{name}`
with `doc` and `mapping`. Reading a PDF: `GET /api/documents/{id}/progress` (never waits),
`POST /api/documents?detect=manual` (no detection) and `POST /api/documents/{id}/pages/{n}/detect`
(search page n, 0-based; answers the new fields for its boxes).

AI work runs as a background job with a step-by-step panel and a Cancel button; cancel takes effect
between steps. A build's panel starts with "Page pass" once the whole pass is done (on a dense form that
can take minutes), then the agent's steps; a cancel during the pass takes effect when it ends. If you
close the panel, the finished result is offered as *Load result* until you load or discard it.

Where the app layer ends: the library knows nothing about a store, records or HTTP. The server
reaches them through two small interfaces, so you can swap them for your own database.
`TemplateStore` (`demo/server/store.py`: `list`, `get`, `put`, `delete`, and optionally `get_file` and
`put_file` for the PDF and data mappings kept with a template; the default keeps one JSON
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

### Extracting in the workbench

The page opens on *What would you like to do?*: *Fill a PDF* is the flow above, and *Extract from PDFs* (shown
when the server can extract) is [extraction](#extraction-many-filled-pdfs-to-rows) in four steps. No model is
called per document unless you ask for it.

1. **The batch and the sample** (the Extract modal). Drop PDFs or zips (up to 50 MB a zip; choose the files or a
   folder for a bigger batch), or type a folder on the server's machine (`FORMCAST_DEMO_FOLDER_SCAN=false` turns
   that off). The batch is scanned by layout: its line says how many PDFs are copies of its main layout, fillable or
   flattened, and a strip marks the documents the sample would take. Pick 3 to 20 sample documents (spread across
   the batch, the first ones, or by hand), what to extract (*AI suggests*, *I'll list them*, one line per thing, or
   a saved fill template as the *Destination*), and how the blanks are found. *Read the sample* builds the template
   in a panel of stages; the model reads the samples here, and only here.
2. **Set up** (the setup screen). The fields on the left, each with its state across the samples (● every sample,
   ◐ some, ? unsure, ○ not on this form) and *Add a field* from a line of your words; a sample page in the middle
   with where each field is read (drag a box to move it); the selected field on the right: its name, title, type,
   definition, how it is read, its answers and checks, and its result on each sample. Every edit is proven again
   on the samples with no model call. *Save* stores the template with its samples.
3. **Run on all.** Saves the template first if it is not saved, then reads every document of the batch with it
   (`FORMCAST_DEMO_EXTRACT_WORKERS` processes; one per CPU by default), shown as documents read of the total. *AI
   per document* in the modal's settings also asks the model about the documents whose required fields fail; its
   answers are marked and still reviewed. A run cancelled keeps what it read, and *Run on all* again carries on.
4. **Review.** The run's numbers (documents, complete, with problems, set aside, run time), the documents that still
   have a problem (filter by field and by *Missing*, *Fails a check* or *Unsure*; *Set aside* lists the documents
   no template fits or that could not be read, with the closest template), the chosen document's page with the
   problem's place outlined, and a card per problem: *Use this value* (type it), *Accept as read* or *Not in this
   document*. Picking a card goes to its field's page and highlights it: where a missing field should be (from the
   template, when its label or box can be found on that document), or the doubtful value and what is wrong with it.
   *← Go back to setup* returns to the setup screen; *Go to review →* comes back, straight away if nothing changed,
   or after asking, since changes take effect only when every document is read again as a new run (decisions made
   stay with the earlier run). Decisions are kept apart from the reads, in the run's folder. *Export xlsx* and *Export Parquet*
   (`pip install 'formcast[parquet]'`) download the values, statuses, set-aside documents and field definitions,
   each document named as in the batch. *Send to fill →* opens the fill start screen with the run's results as
   *Your data* (the destination form picked when the template was made for one); a field still unsettled is left
   blank there, and the fill reports it as a gap.

The choice lists the saved runs (*Open a saved run*) and extraction templates, so a run can be reviewed again
later, and opens a command line run (`python -m formcast.extract run ... --out out/`) by its folder's path, when
its template is saved in the workbench. Batches, saved extraction templates and runs live under `local/` in the
data folder (`batches/`, `extractions/`, `runs/`); a setup being made is kept in memory until it is saved.

## Spec

Design spec: [docs/superpowers/specs/2026-10-20-form-projection-design.md](docs/superpowers/specs/2026-10-20-form-projection-design.md)
(results of the first eval in section 14.3).
Review apps (browser demo and workbench): [docs/superpowers/specs/2026-10-21-formcast-review-apps-design.md](docs/superpowers/specs/2026-10-21-formcast-review-apps-design.md).
Fill design: [docs/superpowers/specs/2026-10-21-formcast-fill-design.md](docs/superpowers/specs/2026-10-21-formcast-fill-design.md).
Vision page mapping (the page pass and page images, with results in section 11):
[docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md](docs/superpowers/specs/2026-10-23-vision-page-mapping-design.md).
Plan: [docs/superpowers/plans/2026-10-20-formcast-agent-layer.md](docs/superpowers/plans/2026-10-20-formcast-agent-layer.md)
