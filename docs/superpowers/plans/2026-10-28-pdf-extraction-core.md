# PDF Extraction Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A library package `formcast.extract` that reads filled PDFs of one layout into structured, checked
values with no model call, at batch scale, and exports them; driven by a hand-written extraction template.

**Architecture:** An `ExtractionTemplate` (schema with checks, a learned layout, and per-path readers with
ordered strategies) is applied to each PDF by `extract()`, which opens the PDF once, reads form-field values and
words, tries each path's strategies, cleans with the existing data-mapping transforms, and checks the assembled
record with JSON Schema. A batch runner routes each PDF to a template (blind fingerprint, else layout score),
runs `extract()` in a process pool, appends results to a JSON-lines file it can resume, and keeps review
decisions apart. `scan()` groups a batch by layout before any template exists. Export writes values, statuses
and field definitions as xlsx, Parquet or CSV.

**Tech Stack:** Python 3.12, PyMuPDF, pydantic 2, jsonschema (all existing); openpyxl (existing `xlsx` extra);
pyarrow (new `parquet` extra); pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-pdf-extraction-design.md` (mockup:
`docs/superpowers/specs/2026-10-28-pdf-extraction-mockup.html`). This is plan 1 of 3; plans 2 (building a
template from samples with AI) and 3 (the workbench) follow and are outlined at the end.

## Global Constraints

- No model call anywhere in this plan's code. "An AI call per document as the normal path" is an anti-goal.
- Never a silent wrong value: every value ends `found`, `unsure`, `failed_check`, `missing`, `absent` or
  `not_on_form`; a failed check is a status, never a dropped or altered value. Raw and cleaned values are both kept.
- Field detection never runs during `extract()`, `scan()` or a batch run.
- The library stays free of HTTP and of any store beyond files the caller names (`out_dir`).
- `pyarrow` only as the optional extra `parquet = ["pyarrow>=15"]`; Parquet export raises `FormcastError`
  naming the extra when it is missing. xlsx uses the existing `xlsx` extra the same way.
- Repeated groups: arrays of objects with `maxItems` (no `maxItems`: 4); readers are keyed by concrete paths
  (`owners[0].name`); export columns flatten items as `owners_1_name`, other paths keep their dotted path.
- Reuse, don't copy: transforms from `formcast.data.mapping` (`Transform`, `TRANSFORMS`, `_run`, `_coerce`),
  checks via `formcast.schema.validate_data_located`, fingerprints and signatures from `formcast.match`.
- Code style as in `src/formcast/data/`: a module docstring that explains the module, `from __future__ import
  annotations`, pydantic models, `FormcastError` for bad input.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A fillable PDF whose widget value is also painted on the page (its appearance stream): the widget strategy
  must win and the value must not be read twice or joined with itself. Test in Task 5.
- A label that appears twice on a page (a section header and the field): the anchor read is `unsure`, never a
  silent pick. Test in Task 5.
- A filled copy with an extra page attached at the end, or a page rotated with `/Rotate 90`: routing still
  matches and box/anchor reads still land (rects are unrotated crop-box points, as everywhere in formcast).
  Tests in Tasks 4 and 5.
- An encrypted, corrupt or empty file in the batch folder: recorded as `unreadable` with the reason, the run
  continues. Test in Task 7.
- A run killed midway (a truncated last line in `results.jsonl`): the resume skips the torn line, re-reads that
  document, and never duplicates a document. Test in Task 7.

---

## File structure

```
src/formcast/extract/
  __init__.py      public names (lazy, as formcast/__init__.py does)
  model.py         ExtractionTemplate, strategies, Layout, FieldResult, DocumentResult  (Task 1)
  page.py          DocText: one open PDF's widget values and words; label, box, mark and phrase lookups (Task 3)
  layout.py        learn_layout, layout_score; matches()                                (Task 4)
  read.py          extract(template, pdf) -> DocumentResult                             (Task 5)
  scan.py          scan(paths) -> ScanReport                                            (Task 6)
  batch.py         route, run_batch, Results (load, reviews, rows)                      (Task 7)
  export.py        export(results, out, format)                                         (Task 8)
  __main__.py      python -m formcast.extract scan | run | export                       (Task 9)
src/formcast/match.py   + public signature_similarity(a, b)                             (Task 4)
pyproject.toml          + parquet extra                                                 (Task 8)
tests/extract/
  corpus.py        generated client-information forms: fillable, flattened, shifted, rotated, other (Task 2)
  test_model.py, test_page.py, test_layout.py, test_read.py, test_scan.py, test_batch.py, test_export.py,
  test_cli.py, test_corpus.py
README.md               + "Extraction" section                                           (Task 9)
```

---

### Task 1: The extraction template and result models

**Files:**
- Create: `src/formcast/extract/__init__.py`, `src/formcast/extract/model.py`
- Test: `tests/extract/test_model.py`

**Interfaces:**
- Produces (all pydantic `BaseModel`, `extra="forbid"`):
  - Strategies, a discriminated union on `kind`:
    - `WidgetRead(kind="widget", names: list[str])`: a form field's value; the first name present wins (versions
      of a form may rename a field).
    - `BoxRead(kind="box", page: int, rect: Rect)`: words inside a rect; used only on a matching layout.
    - `AnchorRead(kind="anchor", labels: list[str], page: int | None = None, side: Literal["right", "below", "inside"] = "right", max_width: float = 300.0, max_lines: int = 1)`:
      `labels` are alternative wordings of one label ("Taxpayer ID", "TIN", "EIN / SSN").
    - `PhraseRead(kind="phrase", phrases: list[str], page: int | None = None, stop: Literal["comma", "sentence", "line", "digits"] = "comma")`.
    - Each of `names`, `labels`, `phrases` has at least one entry (validated).
    - `ChoiceRead(kind="choice", options: dict[str, ChoiceMark])` where `ChoiceMark = {widget: str | None, page: int | None, rect: Rect | None}` (exactly one of `widget` or `page`+`rect`).
    - `Strategy = Annotated[WidgetRead | BoxRead | AnchorRead | PhraseRead | ChoiceRead, Field(discriminator="kind")]`.
  - `Reader(strategies: list[Strategy], clean: list[Transform] = [])` (`Transform` from `formcast.data.mapping`).
  - `StaticLine(page: int, text: str, rect: Rect)`; `Layout(lines: list[StaticLine], page_sizes: list[tuple[float, float]])`.
  - `ExtractionTemplate(formcast_extract_version: Literal["1"] = "1", template_id: str, name: str, schema_: dict (alias "schema"), form: FormInfo, layout: Layout, readers: dict[str, Reader], absent: list[str] = [], fill_template: str | None = None)`.
    `absent` lists schema paths marked "not on this form".
  - `Status = Literal["found", "unsure", "failed_check", "missing", "absent", "not_on_form"]`.
  - `FieldResult(path: str, status: Status, raw: str | None, value: Any = None, strategy: str | None = None, page: int | None = None, rect: Rect | None = None, reasons: list[str] = [])`.
  - `DocumentResult(doc_id: str, file: str, template_id: str | None, matched: Literal["fingerprint", "layout", "none"], fields: list[FieldResult], error: str | None = None, extracted_at: str)`;
    `.record() -> dict` (the cleaned values of `found`/`unsure`/`failed_check` fields as a nested record, via `formcast.data.mapping._set`).
  - `concrete_paths(schema: dict) -> list[str]`: every leaf path with array items expanded to `[0]..[maxItems-1]` (no `maxItems`: `DEFAULT_ITEMS = 4`).
- Validation: `ExtractionTemplate` rejects a reader key not in `concrete_paths(schema)` and an `absent` path that is not a schema leaf, with `ValueError` naming it.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_template_round_trips_through_json(): ...
    # build a template with one of each strategy kind; Template.model_validate_json(t.model_dump_json(by_alias=True)) == t
def test_concrete_paths_expand_array_items_up_to_max_items():
    schema = {"type": "object", "properties": {
        "investor": {"type": "object", "properties": {"tax_id": {"type": "string"}}},
        "owners": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {"name": {"type": "string"}}}}}}
    assert concrete_paths(schema) == ["investor.tax_id", "owners[0].name", "owners[1].name"]
def test_an_array_without_max_items_gets_four_items(): ...
def test_a_reader_for_a_path_not_in_the_schema_is_refused():  # pytest.raises(ValueError, match="investor.nope")
def test_a_choice_mark_takes_a_widget_or_a_page_and_rect_not_both(): ...
def test_document_result_record_nests_the_values_it_has():
    # fields investor.tax_id found "84-2219037", owners[1].name found "Ben", investor.dob missing;
    # lists are compacted as apply_mapping compacts them, so a record looks the same whichever way it was made
    assert result.record() == {"investor": {"tax_id": "84-2219037"}, "owners": [{"name": "Ben"}]}
```

- [ ] **Step 2: Run them to see them fail**: `pytest tests/extract/test_model.py -v` → FAIL (no module).
- [ ] **Step 3: Implement `model.py` and `__init__.py`** as in the Interfaces block. `record()` uses `_set` then `_compact_lists` from `formcast.data.mapping`.
- [ ] **Step 4: Run them to see them pass**: `pytest tests/extract/test_model.py -v` → PASS.
- [ ] **Step 5: Commit**: `git add src/formcast/extract tests/extract/test_model.py && git commit -m "Extract: template and result models"`

---

### Task 2: A generated test corpus of client-information forms

**Files:**
- Create: `tests/extract/__init__.py` (empty), `tests/extract/corpus.py`, `tests/extract/conftest.py`
- Test: `tests/extract/test_corpus.py`

**Interfaces:**
- Produces, in `tests/extract/corpus.py`:
  - `Client` dataclass: `legal_name, tax_id, dob ("MM/DD/YYYY"), investor_type ("individual"|"joint"|"trust"|"llc"), country (as written, e.g. "United States"), street, city_state_zip, owners: list[tuple[str, str]] (name, percent), bank, account`.
  - `clients(n: int, seed: int = 0) -> list[Client]`: deterministic.
  - `make_form(client: Client | None, *, kind: Literal["fillable", "flattened"], shift: float = 0.0, rotate: int = 0, extra_page: bool = False, painted: bool = False) -> bytes`:
    two Letter pages. Page 0: title "Client Information Form", labels "Investor legal name:", "Taxpayer ID:",
    "Date of birth or formation:", "Country:", "Investor type:" with four checkboxes (Individual, Joint, Trust,
    LLC), "Mailing address" with two lines below, "Beneficial owners (25% or more)" with 4 rows (name, %), then
    a paragraph "Funds will be sent from {bank}, ABA 114000093, account {account}, in the name of the investor
    named above." Page 1: a prose page (40 sentences of agreement text) ending with "Signature". Fillable: one
    named widget per answer (`investor_name`, `tax_id`, `dob`, `country`, `type_individual`/`type_joint`/
    `type_trust`/`type_llc` checkboxes, `address_1`, `address_2`, `owner_{i}_name`, `owner_{i}_pct` for i in
    1..4); the wire paragraph is page text in both kinds. Flattened: the same answers drawn as text at the
    widget positions, checkboxes as drawn squares with an "X" inside when on. `shift` moves page 0's content
    down by that many points; `rotate` sets `/Rotate`; `extra_page` appends a blank page; `painted` (fillable
    only) also draws each widget's value as page text under the widget. `client=None` makes the blank form.
    `labels: dict[str, str] = {}` replaces label wordings (e.g. `{"Taxpayer ID:": "TIN:"}`), for copies that word
    a label differently.
  - `other_form() -> bytes`: a different two-page layout (use `demo/data/forms/fw9.pdf` bytes).
  - `hand_template() -> ExtractionTemplate`: the template a person would make for this form, readers in the
    spec's order (widget, box, anchor; phrase for bank/account; choice for investor type), `country` cleaned
    with `map` {"United States": "US", "USA": "US", "U.S.": "US", "Canada": "CA"}, schema checks: `tax_id`
    `pattern "^\\d{2}-\\d{7}$|^\\d{3}-\\d{2}-\\d{4}$"` and required; `investor.type` enum; `owners` maxItems 4;
    `dob` format date with clean `date`. Until Task 4 exists its `layout` is `Layout(lines=[], page_sizes=[])`
    and its `form` is `form_info_for` of the blank form's `build_form_map(..., detect=False)`; Task 5 sets
    `layout` with `learn_layout`.
- `conftest.py` fixture `corpus_dir(tmp_path) -> Path`: writes 20 filled forms (12 fillable, 6 flattened, 1
  shifted flattened, 1 rotated fillable) plus 2 `other_form()` copies as `client_00001.pdf`... and returns the folder.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fillable_form_carries_its_answers_in_named_widgets():
    pdf = make_form(clients(1)[0], kind="fillable")
    # open with pymupdf: widget "tax_id".field_value == client.tax_id; "type_trust" on when investor_type == "trust"
def test_a_flattened_form_has_no_widgets_and_its_answers_are_page_text(): ...
def test_shift_moves_the_first_page_content_down(): ...  # the "Taxpayer ID:" word's y0 grows by the shift
def test_the_blank_form_has_the_labels_and_no_answers(): ...
```

- [ ] **Step 2: Run them to see them fail**: `pytest tests/extract/test_corpus.py -v` → FAIL.
- [ ] **Step 3: Implement `corpus.py` and `conftest.py`** with PyMuPDF (`insert_text`, `draw_rect`, `pymupdf.Widget`).
- [ ] **Step 4: Run them to see them pass** → PASS.
- [ ] **Step 5: Commit**: `git commit -m "Extract tests: generated client-information corpus"`

---

### Task 3: Reading one PDF: widget values, words, labels, boxes, marks and phrases

**Files:**
- Create: `src/formcast/extract/page.py`
- Test: `tests/extract/test_page.py`

**Interfaces:**
- Consumes: `formcast.pagemap.extract.open_pdf`, `extract_page` (words and lines).
- Produces also: `label_key(text: str) -> str`: NFKC, casefolded, letters and digits only
  (`"Taxpayer I.D.:"` and `"TAXPAYER ID"` both give `"taxpayerid"`). Labels and phrases are compared by key, a
  run of consecutive words matching when their keys joined equal the label's key.
- Produces: `class DocText` (opened once per document; close with `close()` or use as a context manager):
  - `DocText(pdf: str | Path | bytes)`; `.page_count: int`; `.has_values: bool` (any widget with a non-empty value).
  - `.widget_value(name: str) -> str | bool | None`: text widgets' value (`None` when empty); checkboxes and
    radio buttons `True`/`False` (on = `field_value` equals the widget's `on_state()`, else not `"Off"`/empty).
  - `.words(page: int) -> list[Word]` (from `extract_page`, cached per page).
  - `.find_label(labels: list[str], page: int | None) -> list[tuple[int, Rect]]`: every place any of the labels
    occurs (by `label_key`), across all its wordings. Several hits are returned, not resolved.
  - `.text_right_of(page: int, rect: Rect, max_width: float) -> tuple[str, Rect] | None`: words on the same
    row (vertical overlap at least 50% of the word's height) starting after `rect`'s right edge, until a gap of
    more than 3 em, a word ending with ":", or `max_width`.
  - `.text_below(page: int, rect: Rect, max_lines: int) -> tuple[str, Rect] | None`: the next `max_lines`
    lines whose left edge is within the label's x-span, stopping at a line ending with ":".
  - `.text_in(page: int, rect: Rect) -> str | None`: words whose centre lies in `rect`, in reading order.
  - `.marked(page: int, rect: Rect) -> bool | None`: a drawn checkbox's mark. True when a glyph among "X x ✓ ✔ ☒ ✕"
    lies in the rect or the ink share of its inner 70% (rendered at 144 dpi, grayscale < 128) is at least
    `MARK_INK = 0.06`; `None` when the rect is off the page.
  - `.after_phrase(phrases: list[str], page: int | None, stop: str) -> list[tuple[int, str, Rect]]`: every
    occurrence of any of the phrases in a page's text (words joined in reading order), with the text after it up to the stop
    (`comma`: next ","; `sentence`: next ". "; `line`: end of the line; `digits`: the run of digits and spaces).

- [ ] **Step 1: Write the failing tests** (on `make_form` output)

```python
def test_widget_values_and_checkbox_states_read_exactly(): ...
def test_a_label_is_found_without_regard_to_case_spacing_or_punctuation():
    # find_label(["taxpayer i.d."], 0) == one hit on page 0 (the form says "Taxpayer ID:")
def test_any_of_a_labels_wordings_finds_it():
    # on a copy whose label reads "TIN:", find_label(["Taxpayer ID", "TIN"], 0) == one hit
def test_a_label_found_twice_returns_both_places(): ...  # a form with "Taxpayer ID:" in a header and the row
def test_text_right_of_a_label_stops_at_the_next_label():
    # on a flattened form: text_right_of(rect of "Taxpayer ID:") == ("84-2219037", ...), not "84-2219037 Date of birth..."
def test_text_below_reads_the_address_lines(): ...
def test_a_drawn_mark_is_seen_and_an_empty_box_is_not(): ...
def test_after_phrase_reads_the_account_digits():
    # after_phrase("account", 0, "digits")[0][1].replace(" ", "") == client.account
def test_words_on_a_rotated_page_are_in_unrotated_points(): ...  # same rect for "Taxpayer ID:" with rotate=90 and 0
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `page.py`.**
- [ ] **Step 4: Run them to see them pass** → PASS.
- [ ] **Step 5: Commit**: `git commit -m "Extract: read a PDF's widget values, labels, boxes, marks and phrases"`

---

### Task 4: The layout: learned from samples, scored on a document

**Files:**
- Create: `src/formcast/extract/layout.py`
- Modify: `src/formcast/match.py` (public `signature_similarity`)
- Test: `tests/extract/test_layout.py`, `tests/match/test_similarity.py` (one test)

**Interfaces:**
- Produces:
  - `learn_layout(samples: list[bytes], *, blank: bytes | None = None) -> Layout`: the static lines. With a
    blank form, its lines (normalized text, rect) are the layout. Without one, a line is static when its
    normalized text occurs, within `LINE_TOLERANCE = 6.0` points of the same place, on at least all but one of
    the samples (all of them when there are fewer than 4). Page sizes from the first sample.
  - `layout_score(layout: Layout, doc: DocText) -> float`: the share of the layout's static lines found on the
    document within tolerance, after a per-page vertical offset (the median offset of matching lines, so a
    shifted copy scores as high as an aligned one). Pages beyond the layout's are ignored.
  - `layout_offset(layout: Layout, doc: DocText, page: int) -> float`: that per-page offset, for box reads.
  - `LAYOUT_MATCH = 0.9`: the score at which a document is "this layout" (calibrated in Task 5's corpus test).
  - In `formcast.match`: `signature_similarity(a: FormSignature, b: FormSignature) -> float`, the mean of
    `_text_part` (or the ink part alone when a side has no text) and `_ink_part`, 0 to 1. `similarity` keeps
    its behaviour (refactor only if it reuses this).

- [ ] **Step 1: Write the failing tests**

```python
def test_answers_are_not_static_lines_and_labels_are():
    layout = learn_layout([make_form(c, kind="flattened") for c in clients(5)])
    texts = {line.text for line in layout.lines}
    assert "taxpayer id" in texts and not any(c.tax_id in t for c in clients(5) for t in texts)
def test_a_blank_form_gives_the_layout_directly(): ...
def test_a_filled_copy_of_either_kind_scores_above_the_match_line():  # fillable, flattened, shifted, rotated, extra page: all >= LAYOUT_MATCH
def test_another_form_scores_well_below_it():  # other_form() < 0.3
def test_the_offset_of_a_shifted_copy_is_its_shift():  # layout_offset == pytest.approx(12.0, abs=1.0) for shift=12
def test_signature_similarity_is_one_for_the_same_form_and_low_for_another(): ...  # in tests/match
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `layout.py` and `signature_similarity`.**
- [ ] **Step 4: Run them to see them pass** → PASS (and `pytest tests/match -q` still passes).
- [ ] **Step 5: Commit**: `git commit -m "Extract: learn a layout from samples and score documents against it"`

---

### Task 5: `extract()`: strategies in order, cleaning, checks and statuses

**Files:**
- Create: `src/formcast/extract/read.py`
- Modify: `tests/extract/corpus.py` (`hand_template` now sets `layout` with `learn_layout`)
- Test: `tests/extract/test_read.py`

**Interfaces:**
- Consumes: `ExtractionTemplate`, `FieldResult`, `DocumentResult` (Task 1); `DocText` (Task 3); `layout_score`,
  `layout_offset`, `LAYOUT_MATCH` (Task 4); `_run`, `_coerce` from `formcast.data.mapping`;
  `validate_data_located`, `flatten_schema`, `resolve` from `formcast.schema`.
- Produces: `extract(template: ExtractionTemplate, pdf: str | Path | bytes, *, doc_id: str | None = None, file: str = "") -> DocumentResult`.
  `doc_id` defaults to the blind fingerprint. Never raises for a bad document: an unopenable PDF gives
  `matched="none"`, no fields, `error` set (the batch runner reads that as `unreadable`).

Rules `extract()` applies (the spec's *Running*, *Settled after review* 2 and 3, *Answers from a fixed set*):
1. Layout match: `matched="fingerprint"` when the document's blind fingerprint equals `template.form.fingerprint`;
   else `"layout"` when `layout_score >= LAYOUT_MATCH`; else `"none"` (reads still run).
2. Per concrete path, strategies in order. `box` runs only when matched (rect moved by `layout_offset` on a
   layout match). The first strategy giving a non-empty raw value is kept as the candidate; if it fails a check
   and a later strategy gives a value that passes, the later one wins. `choice` gives the single marked option's
   key, `", ".join` of several when more than one is marked (a check fails: "N boxes checked"), nothing when none.
3. `clean` transforms run on the raw text (`_run`), then `_coerce` to the path's schema type; raw is kept.
4. Checks: `validate_data_located(template.schema_, record)` on the record of all candidates; each error is
   attached to its path (a `required` error to the missing child's path) as a reason; such a field is
   `failed_check`, or `missing` when it had no value.
5. `unsure`: an `anchor` or `phrase` value that passes its checks but whose labels/phrases matched more than one
   place in all (two wordings found once each count as two places), or any `anchor`/`phrase` value on a document with `matched="none"`. `widget`, `box` and `choice`
   values that pass are `found`.
6. Repeated items: an item whose every field is missing makes those fields `absent` (not `missing`), and the
   item is left out of the record.
7. Paths in `template.absent` are `not_on_form` and never read.

- [ ] **Step 1: Write the failing tests** (corpus forms + `hand_template()`)

```python
def test_a_fillable_copy_reads_every_field_from_its_widgets():
    # every field found, strategy "widget" (bank/account "phrase"), values == the client's (dob cleaned to ISO, country "US")
def test_a_flattened_copy_reads_by_box_on_a_matching_layout(): ...  # strategy "box", matched "layout"
def test_a_shifted_flattened_copy_still_reads_by_box_after_the_offset(): ...
def test_a_painted_fillable_copy_reads_each_value_once(): ...  # Review Focus: raw == "84-2219037", not doubled
def test_a_bad_tax_id_fails_its_check_and_keeps_its_value():
    # client.tax_id = "8422-19037": status "failed_check", raw "8422-19037", reasons mention the pattern
def test_a_missing_required_field_is_missing_with_a_reason(): ...
def test_two_checked_type_boxes_fail_the_enum_check(): ...  # value "individual, joint", "2 boxes checked"
def test_a_label_found_twice_makes_the_anchor_read_unsure(): ...  # Review Focus
def test_a_copy_that_words_the_label_differently_reads_by_its_other_wording():
    # a flattened copy with "TIN:" instead of "Taxpayer ID:" and labels ["Taxpayer ID", "TIN"]: found, strategy "anchor"
def test_an_unknown_country_wording_fails_the_allowed_answers_check(): ...  # "Untied States" -> failed_check
def test_empty_owner_rows_are_absent_not_missing(): ...  # an individual with no owners
def test_a_rotated_copy_and_a_copy_with_an_extra_page_read_like_the_original(): ...  # Review Focus
def test_a_path_marked_absent_is_not_on_form(): ...
def test_an_unopenable_pdf_gives_an_error_result_and_does_not_raise(): ...
def test_the_corpus_reads_with_no_wrong_found_value():
    # every corpus form: for each path, status "found" implies value == ground truth (from the Client); report
    # the share found per path; record LAYOUT_MATCH's margin: min score of filled copies, max of other forms
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `read.py`.** Keep `extract` a short loop over paths calling one `_read_path(...) -> FieldResult` per path; statuses decided in one place.
- [ ] **Step 4: Run them to see them pass** → PASS. If `LAYOUT_MATCH` sits within 0.03 of either margin, move it to their midpoint and note the measured margins in `layout.py`'s comment.
- [ ] **Step 5: Commit**: `git commit -m "Extract: read a document with a template: strategies, cleaning, checks, statuses"`

---

### Task 6: `scan()`: group a batch by layout before any template exists

**Files:**
- Create: `src/formcast/extract/scan.py`
- Test: `tests/extract/test_scan.py`

**Interfaces:**
- Consumes: `formcast.match.pdf_features` (blind fingerprint, page sizes, signature), `signature_similarity` (Task 4), `DocText.has_values` (Task 3).
- Produces:
  - `ScanGroup(id: int, files: list[str], fillable: int, flattened: int, page_count: int)`.
  - `ScanReport(groups: list[ScanGroup], unreadable: list[tuple[str, str]], seconds: float)`; groups largest first; `.main -> ScanGroup | None`.
  - `scan(paths: Iterable[str | Path], *, same: float = SCAN_SAME, first_page_only: bool = False) -> ScanReport`.
    Same blind fingerprint: one group at once. Otherwise a document joins the first group whose first member
    has the same page count and sizes (within `SIZE_TOLERANCE`) and `signature_similarity >= same`; else it
    starts a group. `SCAN_SAME = 0.8` until the corpus test calibrates it. `fillable` counts documents with
    widget values. Unreadable files are listed with the reason and skipped.
  - `sample(group: ScanGroup, n: int, how: Literal["spread", "first"] = "spread") -> list[str]`: `spread`
    takes evenly spaced members, fillable and flattened in proportion (at least one of each when both exist).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_corpus_scans_to_one_main_group_and_the_other_form_apart(corpus_dir):
    report = scan(sorted(corpus_dir.glob("*.pdf")))
    assert len(report.main.files) == 20 and report.main.fillable == 13 and report.main.flattened == 7
    assert sorted(len(g.files) for g in report.groups[1:]) == [2]
def test_a_corrupt_file_is_listed_as_unreadable_and_the_scan_goes_on(tmp_path): ...
def test_a_spread_sample_mixes_fillable_and_flattened(corpus_dir): ...
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `scan.py`.** Record in a comment the measured similarity range of same-layout pairs and of cross-layout pairs on the corpus; set `SCAN_SAME` between them.
- [ ] **Step 4: Run them to see them pass** → PASS.
- [ ] **Step 5: Commit**: `git commit -m "Extract: scan a batch into layout groups and pick a sample"`

---

### Task 7: Batch runs: routing, resumable results, review decisions

**Files:**
- Create: `src/formcast/extract/batch.py`
- Test: `tests/extract/test_batch.py`

**Interfaces:**
- Consumes: `extract` (Task 5), `layout_score`/`LAYOUT_MATCH` (Task 4), `DocText`, models (Task 1).
- Produces:
  - `route(templates: list[ExtractionTemplate], pdf: bytes) -> tuple[ExtractionTemplate | None, ExtractionTemplate | None, float]`:
    (the template to use, the closest template, its best score). The template to use is the one whose
    `form.fingerprint` equals the PDF's blind fingerprint (score 1.0), else the best `layout_score` at or above
    `LAYOUT_MATCH`, else `None`. A no-template result records the closest template and score in
    `DocumentResult.error` as `"no template fits (closest: NAME, 0.62)"`, which export reads for `set_aside`.
  - `run_batch(templates: list[ExtractionTemplate], paths: Iterable[str | Path], out_dir: str | Path, *, root: str | Path | None = None, workers: int = 0, on_progress: Callable[[int, int], None] | None = None) -> RunSummary`.
    Writes `out_dir/run.json` (templates' ids and names, started, root) and appends one `DocumentResult` per line
    to `out_dir/results.jsonl`, flushing each line. `doc_id` is the file's path relative to `root` (default:
    their common parent). On start it loads `results.jsonl`, ignores a torn last line, and skips documents
    already there. A document no template fits is written with `matched="none"`, `template_id=None` and no
    fields; an unreadable one with `error`. `workers=0` means `os.cpu_count()`; workers use
    `concurrent.futures.ProcessPoolExecutor` with the templates sent once per worker (initializer).
    `on_progress(done, total)` after each document.
  - `RunSummary(total: int, complete: int, with_problems: int, no_template: int, unreadable: int, seconds: float)`.
  - `ReviewDecision(doc_id: str, path: str, decision: Literal["value", "accept", "not_in_document"], value: Any = None, at: str)`.
  - `class Results` over an `out_dir`: `Results.load(out_dir)`; `.documents() -> Iterator[DocumentResult]`;
    `.problems(*, path: str | None = None, status: Status | None = None) -> Iterator[tuple[DocumentResult, FieldResult]]`
    (statuses `missing`, `failed_check`, `unsure`, minus those with a decision); `.decide(decision: ReviewDecision)`
    appends to `out_dir/reviews.jsonl`; `.effective(doc: DocumentResult) -> DocumentResult` applies the latest
    decision per path (`value` sets value and status `found` with strategy `"review"`; `accept` sets `found`;
    `not_in_document` sets `absent`); `.summary() -> RunSummary`; `.rows() -> Iterator[dict]` (effective values,
    columns flattened per Global Constraints, plus `file`, `template`, `matched`, `extracted_at`) and
    `.status_rows() -> Iterator[dict]` (the same columns holding statuses).
- Re-running `run_batch` over the same `out_dir` keeps `reviews.jsonl`; decisions apply by `doc_id` and path.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_batch_routes_each_form_and_sets_the_others_aside(corpus_dir, tmp_path):
    summary = run_batch([hand_template()], sorted(corpus_dir.glob("*.pdf")), tmp_path / "out", workers=2)
    assert summary.total == 22 and summary.no_template == 2
def test_a_killed_run_resumes_without_duplicates(corpus_dir, tmp_path):
    # run on 10 files; truncate the last line of results.jsonl mid-way; run on all 22
    # every doc_id appears once; the torn document is present and complete
def test_an_unreadable_file_is_recorded_and_the_run_goes_on(tmp_path): ...  # Review Focus
def test_review_decisions_apply_and_survive_a_rerun(corpus_dir, tmp_path):
    # decide value for a failed tax_id; effective() shows it found with strategy "review"; rerun_batch; still applied
def test_rows_flatten_repeated_items_and_keep_dotted_paths(): ...  # "owners_1_name", "investor.tax_id"
def test_failed_documents_are_not_rows_and_carry_their_closest_template(): ...  # the 2 other forms: no rows; error names the closest
def test_problems_filter_by_path_and_status(): ...
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `batch.py`.**
- [ ] **Step 4: Run them to see them pass** → PASS.
- [ ] **Step 5: Commit**: `git commit -m "Extract: batch runs with routing, resume and review decisions"`

---

### Task 8: Export: xlsx, Parquet and CSV with the field definitions

**Files:**
- Create: `src/formcast/extract/export.py`
- Modify: `pyproject.toml` (`parquet = ["pyarrow>=15"]` under optional dependencies)
- Test: `tests/extract/test_export.py`

**Interfaces:**
- Consumes: `Results.rows()`, `Results.status_rows()` (Task 7); the template's schema (`flatten_schema`, for definitions).
- Produces: `export(results: Results, templates: list[ExtractionTemplate], out: str | Path, *, format: Literal["xlsx", "parquet", "csv"]) -> list[Path]`.
  - `xlsx`: one workbook `out` with sheets `values`, `status`, `fields`.
  - `parquet` and `csv`: a folder `out` with `values.*`, `status.*`, `fields.*`.
  - Every format also has `set_aside` (a sheet, or `set_aside.*`): one row per failed document with `file`,
    `reason` (`no template fits` or the unreadable error), `closest_template` and `closest_score` (the best
    `layout_score` routing saw; `route` returns it alongside its answer for this). Failed documents are never
    rows of `values` or `status`.
  - `fields` rows: `column, path, title, definition (description), type, checks` (`required`, `pattern`, `enum`
    values, `format`, ranges, as text).
  - A missing extra raises `FormcastError("Parquet export needs the parquet extra: pip install 'formcast[parquet]'")`
    (and the same wording for `xlsx`).

- [ ] **Step 1: Write the failing tests** (on a small finished run from Task 7's helpers)

```python
def test_xlsx_has_values_status_and_fields_sheets(tmp_path): ...  # openpyxl: header row, 20 value rows
def test_parquet_round_trips_values(tmp_path):  # pytest.importorskip("pyarrow"); pyarrow.parquet.read_table(...).num_rows == 20
def test_csv_writes_three_files(tmp_path): ...
def test_a_missing_extra_names_it(monkeypatch, tmp_path): ...  # block the pyarrow import; match="parquet extra"
```

- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `export.py` and add the extra.**
- [ ] **Step 4: Run them to see them pass** → PASS.
- [ ] **Step 5: Commit**: `git commit -m "Extract: export results as xlsx, Parquet or CSV with field definitions"`

---

### Task 9: Command line and README

**Files:**
- Create: `src/formcast/extract/__main__.py`
- Modify: `src/formcast/__init__.py` (lazy exports `ExtractionTemplate`, `extract`, `scan`, `run_batch`), `README.md` (an "Extraction" section after "One-shot")
- Test: `tests/extract/test_cli.py`

**Interfaces:**
- Produces: `python -m formcast.extract`
  - `scan DIR [--first-page]`: prints groups (size, fillable, flattened, pages, three example files) and unreadable files.
  - `run TEMPLATE.json [TEMPLATE.json ...] DIR --out OUT [--workers N]`: progress on stderr every 100
    documents and at the end the summary (`total, complete, with problems, no template, unreadable, seconds`).
  - `export OUT --format xlsx|parquet|csv --to PATH`.
  - Exit code 0, or 2 with a one-line message for bad arguments or a `FormcastError`.
- README: what a template holds, the statuses, the CLI, the throughput measured in Task 5's corpus run, and
  that plan 2 adds building templates from samples.

- [ ] **Step 1: Write the failing tests**: `subprocess.run([sys.executable, "-m", "formcast.extract", "scan", corpus_dir])` prints the main group's 20; `run` then `export --format csv` writes `values.csv` with 22 rows; a missing template file exits 2 with its name.
- [ ] **Step 2: Run them to see them fail** → FAIL.
- [ ] **Step 3: Implement `__main__.py` (argparse), the exports and the README section.**
- [ ] **Step 4: Run the whole suite**: `pytest -q` → all pass (the existing 1,840 plus these).
- [ ] **Step 5: Commit**: `git commit -m "Extract: command line and README"`

---

## Plans 2 and 3 (outline, written after this one lands)

**Plan 2: building a template from samples with AI** (`formcast.extract.setup`): read the sample (`detect=False`
plus detection on pages without widgets, prose skipped, per the Extract modal's setting); seed fields from the
three starts (`derive_schema` for AI suggests; a new prompt turning the person's lines into defined fields; a
destination schema with "on this form" marked by the model); propose readers per field from the samples' form
maps and page images; the proof run (the readers by code, and the model's own reading as a second opinion) giving
per field and sample `agree | disagree | missing | failed_check`; the country-style `map` proposed from the
samples' wordings; `learn_layout` from the samples. Output: an `ExtractionTemplate` plus per-sample results.
Also the optional AI pass per document (off by default): after a run, documents whose required fields are
`missing` or `failed_check` go to `extract_records` with their page text and images, limited to those fields;
its values are written with strategy `"ai"` and status `unsure`, so review sees them.
Prompts pass the model-facing text guard (`tests/agent/test_prompts.py`).

**Plan 3: the workbench**: the Fill or Extract choice; the Extract modal (bulk upload of files, a folder or a
zip, a server-side folder scan with `scan()`, the sample count and picking, the three starts, detection and
reading settings); the template-build job with stages; the setup screen (fields, sample page with readers drawn,
field panel with per-sample results, edits that re-prove); saving extraction templates in the store; the run job
with per-document progress; the review view over `Results` (paged problems, decisions); export downloads; and
"Send to fill" turning results into a `Dataset` (direct mapping when paths match the destination, else a saved
`DataMapping`). The mockup is the reference.
