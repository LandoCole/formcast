# PDF extraction: read filled forms into structured data

Date: 2026-10-28. Status: reviewed by a builder; awaiting the owner's approval.

## Intent

formcast fills PDFs from data (projection). This adds the reverse: reading data out of filled documents
(ingestion), at scale, into a structured table that the existing fill features can use.

The real case: a firm holds a large set (on the order of 100k) of client information forms that clients
completed digitally. Their answers have to end up on a manager's subscription document. Today that is
retyping. With this, a person maps one layout of the client form once, from a few samples, and the
answers of every form of that layout are read by code, checked, exported (xlsx or Parquet in the
workbench demo) and then fill the subscription document through the existing one-shot and fill flows.

The general idea: **map once, extract from all.** Most documents in a batch share a set layout, so the
expensive, judgment-heavy work (deciding what the fields are and where each answer sits) happens once,
with AI and a person, and the per-document work is cheap, deterministic code.

## Goals

1. Build an **extraction template** for one layout from a few (3-10) sample documents, with no more effort
   than building a fill template today.
2. Three ways to decide what to extract, on **one screen that behaves the same** whichever is used:
   - **AI suggests:** the AI proposes the fields it finds on the samples.
   - **I'll list them:** the person types what they need in plain words, one item per line; the AI turns each
     line into one or more defined fields and finds them on the samples.
   - **The destination:** the person picks a saved (fill) template, e.g. the subscription document; its
     schema is the checklist, and fields the source form never asks for are flagged "not on this form".
   The three only seed the field list. After that the list, the sample page and the field panel are the same,
   and the person can mix (start from the destination, then add a line of their own).
3. **The person never chooses how a value is read.** For each field the system picks a reader (form-field
   value, text next to a label, a box, a checkbox group, text after a phrase) and proves it on the samples.
   A mixed document (some answers in form fields, some in page text) just has fields with different readers.
4. Every field has a **name** (schema path), a plain-language **definition**, a **type** and **checks**
   (required, format, allowed values). The definition guides the AI, tells a reviewer what the field means,
   and documents the export.
5. **Checkboxes are first class:** a group of boxes becomes one field with a fixed set of answers (each answer
   tied to its box); a lone box becomes yes/no.
6. **Run at scale by code:** no model call per document in the normal case. Fast enough that 100k
   few-page digital forms are hours on one machine, not days.
7. **Show what was missed, and where:** for every document and field, a status (found, missing, failed a check,
   unsure). A review list of the problems, by document, where a person enters the value, confirms it is not in
   the document, or accepts it.
8. **Export** the results (xlsx and Parquet; CSV is free) with the field definitions, and **hand them to fill**:
   the extracted records become a dataset the workbench's existing one-shot and fill flows take.

## Non-goals (this version)

- Handwriting and scanned paper. The forms are digitally completed. Scanned pages are not refused (the form
  map already OCRs them) but their accuracy is not a goal.
- Rules across fields ("a trust needs a trustee name", "commitment at least the fund minimum"). Designed for
  (see *Validation*), not built.
- Learning a layout with no samples, or extracting from wholly free-form documents with no shared layout.
  Free text in a document is handled only as a field's reader ("text after this phrase") or, failing that,
  by the AI fallback.
- Judgment questions with no field to read ("is this a venture capital fund?" when only the strategy text implies
  it): they need the model to read each document. Out of scope; re-evaluate later.
- Computed fields (an answer worked out from other extracted fields, e.g. `venture_capital` from `fund.type`):
  designed for with the rules across fields, not built.
- Enhancing normalization beyond the existing map and `enum` check (see *Answers from a fixed set*).
- Open-ended tables (any number of rows); repeated groups have a fixed maximum (see *Settled after review*).
- A bulk command line fill; filling starts from the results through the existing flows.
- A production ingestion service (queues, auth, multi-user review). The workbench stays a local, single-user
  demo; the library and a command line runner are the scale path.

## Anti-goals (would make this a failure even if it works)

- **Silent wrong values.** A value that was read from the wrong place and looks plausible is worse than a
  missing one. Every reader result passes the field's checks or goes to review; "unsure" is a status, not a guess.
- **An AI call per document** as the normal path. It would make 100k forms slow, costly and unrepeatable.
- **A second, different editor.** Extraction setup reuses the workbench editor's layout and controls
  (fields left, page in the middle on the dot grid, the selected item right), in its design language.
- **The person picking field-versus-text per field**, or writing regexes, to get a normal form working.
- Re-reading every PDF to change a check: checks run on stored values.

## Constraints

- Builds on what exists: the form map (`build_form_map`: text, widgets, rules, labels linked to boxes;
  `detect=False` for a fast read; `detect_on_page` for one page), templates (`Template`, `Field` with page/rect,
  `existing_widget`, `Source` with `when` for choices), JSON Schema with `flatten_schema` paths,
  `derive_schema`, `extract_records`, `DataMapping` and its transforms, `similarity` matching and
  fingerprints, the workbench server and page (jobs with stages, reading progress), the template store.
- The library stays free of storage and HTTP, as now; the workbench reaches storage through its store interfaces.
- Measured: reading a PDF with detection off costs about 0.1 s a page (a 90-page PDF in about 9 s, including
  the fingerprint). Extraction reads with detection off.
- New dependency only as an optional extra: Parquet export needs `pyarrow` (a `parquet` extra, like `xlsx`).

## Decided in conversation

- Readers: **the current value of a form field, and label-relative text**, are the core. Exact boxes are a
  shortcut for the same version of the form; regex **checks and cleans** values, it does not find them.
- The reader order for a field: form-field value, then the exact box when the document is the samples'
  version (same fingerprint), then the label-relative area, then the AI on that one document or review.
- The three starting methods (AI, user, destination) on one screen, as mocked up (see *The setup screen*).
- Fields have names and definitions; checkboxes are in scope.
- Validation in two layers: checks on one field run during extraction; rules across fields are a separate,
  later step over the stored table.
- Output for the demo: xlsx or Parquet.

## The design

### Extraction template

A saved, versioned artifact per layout, stored beside fill templates:

- `schema`: a JSON Schema. Each property has `title`, `description` (the definition), `type`, and checks as
  JSON Schema keywords (`required`, `pattern`, `enum`, `format`, `minimum`...).
- `form`: the form info of the samples' layout (fingerprint, page sizes, signature), as `FormInfo` now, so
  `similarity` can route documents to it.
- `readers`: one per schema path (not per box), each with an ordered list of strategies:
  - `widget`: a form field by name (and kind); exact. For a checkbox group, one widget per answer.
  - `box`: page + rect; text inside it (PDF text layer; OCR only if the page needs it). Used when the
    document's fingerprint matches.
  - `anchor`: page (or "any page"), label text (matched tolerantly: case, spacing, punctuation), the side the
    value sits on (right, below, inside), and how far it reaches (to the next label, rule or box edge, or a
    size). Survives shifts and reflow.
  - `after_phrase`: text following a phrase in running text, up to a stop (end of sentence, comma, a number
    run); for answers inside paragraphs ("account 9930 4410").
  - `choice_group`: answers -> boxes (each a widget, or a page rect for a drawn box); reads which is marked.
  - `clean`: transforms from the existing set (`digits`, `date`, `strip`, `map`...) applied to the raw text.
- `samples`: the sample documents' ids and, per field, what each reader read on each (kept for re-testing).

**One template, both directions (builder's call how far to take it).** A fill template already says "this box
is `investor.tax_id`". The setup can seed readers from a saved fill template of the same form: a field with a
single source path and no transforms (or a checkbox whose `when` names one answer) reads straight back. A field
whose value was joined from several paths or reformatted cannot be inverted and gets its own reader. The
extraction template is its own artifact, linked to the fill template by name when there is one.

### Making the template from samples (setup)

1. The person gives 3-10 samples of one layout. Each is read with `detect=False`, plus detection on the pages
   without widgets so the model has boxes to point at (cheap at this count).
2. The field list is seeded by the chosen start:
   - AI: `derive_schema` over the samples (it already designs schemas from pages).
   - User: each typed line goes to the model with the samples' pages; it answers defined fields
     (name, definition, type, checks) for that line, possibly several (a line "wire details" -> bank, ABA,
     account).
   - Destination: the saved template's schema; the model marks which paths this form has, and the rest are
     "not on this form".
3. For each field the model proposes readers from the samples' form maps (it sees widgets with names and
   values, text lines with positions, labels linked to boxes, page images).
4. **Proof on the samples:** the readers run by code on every sample; independently, the model reads each
   sample's values (a second opinion, like `extract_records` on the page text and images). Per field and
   sample: agree (found), disagree (shown to the person), missing in both (likely not on that sample), or
   fails a check. A field is ready when its readers agree with the model on the samples where it is present.
5. The person fixes what is not ready on the setup screen (below): moves or redraws a box, picks the right
   label, edits the definition or checks, or marks a field "not on this form". Each fix re-runs the proof.
6. Save. The template keeps the samples' results so a later edit can be re-proved.

### The way in: Fill or Extract, then the Extract modal

The workbench opens on a choice: **Fill a PDF** (the existing start screen, unchanged) or **Extract from
PDFs**, which opens the Extract modal. Everything before the sample is read is set there, in one sheet in the
workbench's modal style:

1. **Your documents (bulk upload).** Drop PDFs or a `.zip`, choose files or a folder, or type a folder on the
   server's machine and *Scan* it (the path for large batches: nothing is uploaded through the browser). Once
   added, a summary: how many PDFs, their size, and a quick sort by layout (fillable and flattened copies of
   the main layout, and the documents that look different, set aside for a second template later), drawn as
   a strip of cells with the sample documents marked.
2. **The sample.** How many documents (3 to 20, default 5) and how they are picked: *spread across the batch*
   (the default: fillable and flattened, early and late), *the first ones*, or *I'll pick them* (a list of
   the batch's files with checkboxes).
3. **What to extract.** *AI suggests* / *I'll list them* (a text area, one item per line) / *Destination* (a
   saved template picked from a list). This is where *Start from* lives; the setup screen shows the choice
   and a *Change the setup* button that reopens this modal.
4. **Detection and reading** (folded): find the blanks automatically skipping prose pages (default),
   automatically on every page, on the pages the person picks on the samples, or not at all (form fields and
   page text only); scanned pages OCR'd when needed or left out; the *unsure* rule (on by default, see
   *Settled after review*); the AI per document (off by default).

*Read the sample →* starts the template build as a job with stages (read the sample, find the blanks, design
the fields / turn the lines into fields / match the destination's fields, find each field on the sample, check
the reads) in the workbench's job panel; when it ends, the setup screen opens. Without documents, the modal
says so and starts nothing.

### The setup screen

The workbench editor's layout and design language (paper and ink, serif body, uppercase mono labels, outline
buttons, the page on the dot grid), with an extraction mode:

- **Toolbar:** the batch's name, size and sample count (it reopens the Fill-or-Extract choice); *Set up* /
  *Review*; *Run on all*. Under it, a strip naming the start, sample and detection chosen, with *Change the setup*.
- **Left, fields:** every field with its status across the samples (● every sample, ◐ some, ○ not on this
  form) and count (5/5). An add box at the bottom ("Add a field: wire instructions, bank and account") in every
  mode. Clicking a field selects its spot on the page.
- **Middle, a sample page:** boxes drawn as the editor draws them (dashed unlinked, solid linked, the selected
  one heavy with its path tag), ‹ › through the samples and their pages. A label-relative reader shows its
  label and its read area; a checkbox group shows its boxes. Boxes move and resize as in the editor.
- **Right, the field:** name, definition, type, read from (in words: "Form field investor_name",
  "Next to the label Taxpayer ID:", "Page text, after account", "Checkbox group, 4 boxes"), answers (a choice
  field's answers, each with its box), checks (required, format, allowed values, exactly one box), and *On the
  samples*: what was read on each, ● or × with the reason ("doesn't match the format", "missing",
  "2 boxes checked").
- With *I'll list them*, the top strip is a text area of lines; with *Destination*, it names the saved
  template and how many of its fields this form lacks.

A clickable HTML mock-up of this screen and of the review view, built on the workbench's own stylesheets,
is [`2026-10-28-pdf-extraction-mockup.html`](2026-10-28-pdf-extraction-mockup.html) (open it from a local
server at the repository root, e.g. `python -m http.server`, so its links to `demo/web/` resolve). An earlier
sketch was reviewed in the conversation (fields
`investor.legal_name` from a widget, `investor.tax_id` next to a label with a bad sample and a missing one,
`investor.type` as a checkbox group, `wire.account_number` from paragraph text, `subscription.amount` not on
the form).

### Running (extraction)

- Library: `extract(template, pdf) -> Extraction` reads one document: per path the raw text, the cleaned
  value, the reader that produced it, its place (page, rect), and a status (`found`, `missing`, `failed_check`
  with the check, `unsure`); plus the document's id, fingerprint and whether it matched the template's
  layout. No model call.
- Routing: the blind fingerprint (fillable copies) or the layout fingerprint (flattened copies, see *Settled
  after review*) picks each document's template, with `similarity` as the tie-breaker; below the calibrated
  threshold the document is `no_template` (set aside, listed, not guessed). One template per batch is the first case;
  several (versions of the form) is the same code with more candidates.
- Fallback (optional, off by default, a setting per run): documents with missing or failed required fields
  go to the model (`extract_records` with the page text and images, restricted to the failing fields), and
  the model's values are marked `from_ai` so they are reviewed, not trusted.
- Scale: a runner over a folder (or a list) of PDFs with a process pool, resumable (skips documents already in
  the results), writing results as it goes. A command line entry point for bulk (shape is the builder's call)
  and the workbench for demo-sized batches.

### Results and review

- Results are a table: one row per document, one column per field value, plus per-field status columns and
  document columns (file, template, matched, extracted at). Stored by the runner as Parquet (or JSON lines
  without `pyarrow`), so checks can re-run and review edits apply without re-reading PDFs.
- Review list: problems by document, filterable by field and status. Opening one shows the document page
  with the field's spot (or the area searched) highlighted; the person types the value, accepts what was read,
  or marks it "not in this document". Review decisions are stored apart from the raw reads (when, the value,
  the decision), so re-running extraction keeps them.
- Summary: per field, how many documents found / missing / failed / reviewed; per document, how many problems.

### Answers from a fixed set (normalization)

A field with a fixed set of answers (`enum`, e.g. country `US`, `CA`, `KY`) may say the same thing many ways
("United States", "USA", "U.S."). This round uses the base layer that exists:

- The field's `clean` step includes a `map` transform (wording -> answer), and the `enum` check.
- At setup, the model proposes the map from the wordings it sees on the samples; the person checks it on the
  field panel.
- At run time code applies it. A wording not in the map fails the "one of the allowed answers" check and goes
  to review; the raw wording is kept beside the cleaned value, so a map changed later re-runs over the stored
  results without reading the PDFs.

Not this round (re-evaluate later): the model mapping unknown wordings (once per distinct wording), and review
answers extending the map automatically.

### Validation

- On one field, during extraction: type, `required`, `pattern`, `enum`, `format` (date, email), ranges.
  A failed check is a status, never a dropped or altered value.
- Across fields: a later step over the stored table, its rules in the template (JSON Schema `if/then` covers
  "a trust needs a trustee name"). Not built in this version.

### Export and handing to fill

- Export: xlsx (openpyxl, existing `xlsx` extra) and Parquet (`pyarrow`, new `parquet` extra) with values,
  statuses, and the field definitions (path, title, definition, type, checks).
- To fill: the results become a workbench dataset (`Datasets`), so *Your data* and the one-shot data stages
  take them. When the extraction schema's paths are the destination's (the destination start), the direct
  mapping applies with no model; otherwise a `DataMapping` from source paths to destination paths is written
  once by `map_table` (columns are the source paths) and saved with the destination template for reuse.

### Workbench demo, server

The shape (routes are the builder's call): create an extraction template from uploaded samples (a job with
stages, as the one-shot job: read samples, seed fields, propose readers, prove), edit it (field and reader
edits, re-prove), save it in the store, run it on an uploaded zip of PDFs or a server-side folder (a job with
progress per document, as the form-reading progress), list and review results, export, and send results to fill.

## Testing

- Library: readers on generated PDFs (fillable, flattened, label moved, wrapped answer, checkbox groups with
  widgets and drawn marks, phrase in a paragraph); proof agreement; statuses and checks; routing by fingerprint
  and similarity; resumable runner.
- A small corpus: a client-information-style form in a fillable and a flattened version with N filled copies
  and ground truth (generated with the existing fill), plus a few shifted copies. Accuracy and speed reported
  as in `eval/`.
- Workbench: the setup screen in each start mode; review edits; export files open; results to fill.

## Settled after review

A builder read this design cold and asked four questions only the owner could answer. The answers:

1. **The forms are a mix of fillable and flattened.** Both reading paths are built from the start.
   - Why it matters: `fingerprint_document(..., ignore_field_values=True)` makes a filled *fillable* copy match
     its blank, but a *flattened* copy's answers are page text, so every filled copy has its own fingerprint, and
     `similarity` caps any non-fingerprint match at 0.94 with a text score that drops as answers vary.
   - So: a **layout fingerprint** for flattened documents, made from what the samples share (the static text
     lines and their positions, page sizes, rules), not from the whole page; answers (text that differs across
     samples) are left out. A blank copy, when the person has one, gives the static text directly and is
     optional. Routing uses the existing fingerprint for fillable copies and the layout fingerprint for
     flattened ones, with its threshold calibrated on filled copies in the test corpus.
   - The exact-box reader runs when the layout fingerprint matches (flattened) or the blind fingerprint matches
     (fillable). The template stores both.
2. **Repeated answers: a few groups, each up to a fixed number of items.** Several owners, signers, beneficial
   owners: the schema has arrays with `maxItems` (as `derive_schema` makes them); readers are per item
   (`owners[0].name` ... `owners[3].name`), the setup screen shows a group as one field with its items, and
   the export flattens items to columns (`owners_1_name`, `owners_2_name`). An item whose fields are all missing
   is an absent item, not missing fields. Open-ended tables are not in this version.
3. **"Unsure" covers the weaker reads.** Form-field values and exact boxes (on a matching layout) are trusted
   when they pass their checks. A label-relative or after-phrase read is `unsure` when its label or phrase
   matched more than one place on the page, or the document's layout did not match the samples'; otherwise
   it is trusted when it passes its checks. Each reader reports which strategy produced the value and how many
   places its anchor matched, so the rule can be tuned after the corpus run.
4. **At scale: review in the workbench, fill from the export.** The workbench opens a bulk results file
   (from the command line runner) with paged review of the problems, reading each document's PDF from the
   run's folder on the server's machine. Filling the subscription documents starts from the results a batch at
   a time through the existing flows (`Datasets` keeps tables of up to 10,000 rows today; the builder decides
   whether to page or raise that). A bulk command line fill is not in this version. A record with unresolved
   required-field problems is filled with its gaps flagged in the fill report, as fill does for missing
   values today.

Minor, settled by the author: the HTML mock-up next to this spec shows the intended layout and behaviour;
where it and the written description differ, the written description wins, and details beyond both are the
builder's call.

## Calls made here (check these)

- The extraction template is its own artifact, linked to a fill template, not a fill template with extra keys.
- Readers are per schema path, ordered strategies, chosen by the model and proved by code on the samples.
- The AI fallback per document is off by default and its values are always reviewed.
- Results are stored by the runner (Parquet, or JSON lines) so checks and review never need the PDFs again.
- Rules across fields: designed for, not built. Computed fields are grouped with them.
