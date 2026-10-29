# PDF Extraction Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an extraction template from 3-10 sample PDFs with an AI model (fields seeded three ways, readers
proposed by the model and proved by code against the model's own reading of each sample), edit it without the
model, and optionally ask the model about a run's failing documents.

**Architecture:** Plan 1 (`docs/superpowers/plans/2026-10-28-pdf-extraction-core.md`, on `main`) gave
`formcast.extract`: the `ExtractionTemplate`, `extract()`, routing, `run_batch`, `Results` and export, with no
model call. This plan adds the model-facing setup beside it. `read_samples` reads the samples and finds the
blanks; `ask.py` holds the four model calls (fields from lines, which destination fields are on the form, reader
proposals, the model's reading of one document), all built on `formcast.data.ai._ask`; `readers.py` turns proposals
into plan 1 strategies by code, keeping only what exists on the samples; `proof.py` runs the readers on the samples
and compares with the model's stored reading, so an edit (`edit.py`) is re-proved with no model call;
`setup.py` orchestrates the stages. `aipass.py` is the opt-in model pass over a finished run.

**Tech Stack:** Python 3.12, PyMuPDF, pydantic 2, jsonschema (existing); the `formcast.llm` providers; pytest.

**Not in this plan (the spec leaves it to the builder):** seeding readers from a saved fill template of the same
form ("one template, both directions"). The model proposes readers from the samples instead; a fill template's
widget names reach it through the samples' form fields anyway.

**Spec:** `docs/superpowers/specs/2026-10-28-pdf-extraction-design.md` (sections *Making the template from samples*,
*Answers from a fixed set*, *Running* (Fallback), *Settled after review*). Mockup:
`docs/superpowers/specs/2026-10-28-pdf-extraction-mockup.html`. This is plan 2 of 3; plan 3 (the workbench) builds
on its interfaces.

## Global Constraints

- Model calls happen only in `build_setup`, `add_line` and `ai_pass`. `prove`, `apply_edit`, `resolve`,
  `learn_maps`, `sample_marks`, `extract()` and `run_batch` never call a model. "An AI call per document" stays an
  anti-goal: `ai_pass` is off unless called.
- Never a silent wrong value. A model proposal that names a widget, box, label or phrase no sample has is dropped
  with a warning, never kept. Values from `ai_pass` are always `unsure` with strategy `"ai"`.
- Every model call goes through `formcast.data.ai._ask` (import the private name; do not copy it), so a reply is
  one JSON object checked by a function that raises `ValueError`/`FormcastError`/`pydantic.ValidationError`, retried
  once, then `FormcastError("the model's answer could not be used: ...")`.
- Prompts are module-level `UPPER_CASE = """\` constants in `src/formcast/extract/prompts.py`; every new module
  that holds model-facing text is added to `MODEL_FACING` in `tests/agent/test_prompts.py`. Prompt examples are
  invented and generic (the guard rejects the corpus's labels, property names and widget names).
- Page images go through `formcast.pagepass.marks.render_marked_page` (at most `MAX_IMAGE_PAGES = 12` per call);
  page text through `formcast.pagemap.summarize.summarize_page`; both only when `vision=True` for images.
- Field detection runs only on samples, only through `build_form_map`/`detect_on_page`, and tests always inject
  `detectors={}` and `DetectPolicy(digital=(), scanned=())`.
- The library stays free of HTTP and storage: samples are passed as bytes, results returned as pydantic models.
- Reuse: `learn_layout`, `page_fit`, `form_info_for`, `derive_schema`, `flatten_schema`, `concrete_paths`,
  `formcast.data.mapping.Transform` and its `map` transform (case-insensitive, `args={"values": {...}}`).
- Tests use scripted fake providers (the `Scripted` pattern of `tests/data/test_ai.py:18`: pop a reply string per
  call, record `messages`, return `LLMResponse(text, [], Usage(10, 5), {})`). A real model only in tests marked
  `llm` (deselected by default).
- Code style as plan 1: module docstring explaining the module, `from __future__ import annotations`,
  `extra="forbid"` pydantic models, `#:` comments on constants, `Raises:` sections.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A model proposal that names a widget, candidate box, label or phrase present on no sample: dropped with a
  warning naming the path and what was dropped; never a reader. Test in Task 4.
- A sample set mixing fillable and flattened copies of one form: every widget field also gets a box read at the
  widget's place, so the flattened copies read too, and the proof agrees on both kinds. Test in Task 6.
- The model's reading differs from the reader's only in form ("84-2219037" vs "842219037", "1,000" vs 1000,
  "Trust" vs "trust"): counted as agreeing; a real difference ("Ana" vs "Ben") is `disagree`. Test in Task 1.
- An unreadable or encrypted PDF among the samples: `build_setup` raises `FormcastError` naming the file before any
  model call. Test in Task 6.
- A destination schema with a repeated group and fields this form lacks: every item path of an absent group is in
  `absent`, and the proof counts no absent field toward ready or total. Test in Task 6.

---

## File structure

```
src/formcast/extract/
  model.py      + SetupInfo, SampleRecord; ExtractionTemplate.setup / .samples
  read.py       + a choice on a boolean path reads true/false (lone checkbox)
  proof.py      prove(), same_value(), learn_maps(), sample_marks()        (no model)
  edit.py       Edit ops and apply_edit()                                   (no model)
  samples.py    SamplePdf, SampleDoc, read_samples(), sample_view(), sample_images()
  readers.py    ReaderProposal, BoxRef, OptionProposal, resolve()           (no model)
  prompts.py    FIELDS_SYSTEM, ON_FORM_SYSTEM, READERS_SYSTEM, VALUES_SYSTEM
  ask.py        fields_from_lines(), fields_on_form(), propose_readers(), read_values()
  setup.py      SetupSpec, StageEvent, STAGES, SetupResult, build_setup(), add_line()
  aipass.py     ai_pass(), AiAnswer, AI file; Results applies ai.jsonl
  batch.py      Results reads ai.jsonl (Task 7)
  __main__.py   + `ai` subcommand (Task 7)
tests/extract/
  test_proof.py, test_edit.py, test_samples.py, test_readers.py, test_ask.py, test_setup.py, test_aipass.py
  fakes.py      Scripted provider and a corpus-answering fake model (Task 5)
tests/llm/test_extract_setup.py   one real-model build over the corpus (marked llm)
```

---

### Task 1: Samples' readings kept on the template, and the proof

**Files:**
- Modify: `src/formcast/extract/model.py`, `src/formcast/extract/__init__.py`
- Create: `src/formcast/extract/proof.py`
- Test: `tests/extract/test_proof.py`, `tests/extract/test_model.py`

**Interfaces:**
- Consumes: `ExtractionTemplate`, `FieldResult`, `concrete_paths` (model.py); `extract` (read.py); `DocText`,
  `label_key` (page.py); `page_fit` (layout.py); `flatten_schema`, `SchemaPath` (formcast.schema).
- Produces:
  - `SetupInfo(start: Literal["ai","lines","destination"], lines: list[str] = [], line_paths: dict[str, list[str]] = {}, destination: str | None = None, detect: Literal["auto","all","manual","off"] = "auto", sample_how: str = "")`
  - `SampleRecord(file: str, sha256: str, opinion: dict[str, Any])`: `opinion` maps every concrete path the
    model was asked about to the value it read there (`None`: not on this sample).
  - `ExtractionTemplate.setup: SetupInfo | None = None` and `ExtractionTemplate.samples: list[SampleRecord] = []`.
  - `Outcome = Literal["agree", "disagree", "missing", "failed_check", "absent"]`
  - `SampleProof(file: str, outcome: Outcome, read: FieldResult, opinion: Any = None)`
  - `FieldProof(path: str, ready: bool, absent: bool, samples: list[SampleProof])` with property
    `count -> tuple[int, int]` = (samples whose outcome is `agree` or `failed_check`, samples not `missing`).
  - `Proof(fields: list[FieldProof], ready: int, total: int)` (`total` excludes absent fields).
  - `same_value(read: Any, opinion: Any) -> bool`
  - `prove(template: ExtractionTemplate, pdfs: list[bytes]) -> Proof`
  - `learn_maps(template: ExtractionTemplate, proof: Proof) -> ExtractionTemplate`
  - `Mark(path: str, kind: Literal["read","box","label","option"], page: int, rect: Rect, answer: str | None = None)`
  - `sample_marks(template: ExtractionTemplate, pdf: bytes) -> list[Mark]`

- [ ] **Step 1: Write the failing tests** in `tests/extract/test_proof.py` (use `hand_template()`, `clients`,
  `make_form` from `tests/extract/corpus.py` and `truth` from `tests/extract/test_read.py`):

```python
def _with_samples(template, pdfs, opinions):
    records = [SampleRecord(file=f"s{i}.pdf", sha256=hashlib.sha256(p).hexdigest(), opinion=o)
               for i, (p, o) in enumerate(zip(pdfs, opinions))]
    return template.model_copy(update={"samples": records})

def test_same_value_ignores_form_but_not_content():
    assert same_value("84-2219037", "842219037") and same_value("Trust", "trust")
    assert same_value("1,000", 1000) and same_value(1000.0, "1000") and same_value(None, None)
    assert same_value(True, True) and not same_value(True, False)
    assert not same_value("Ana Ruiz", "Ben Ruiz") and not same_value("x", None)

def test_readers_that_match_the_models_reading_are_ready_on_both_kinds():
    people = clients(4, seed=7)
    pdfs = [make_form(p, kind=k) for p, k in zip(people, ("fillable", "flattened", "fillable", "flattened"))]
    t = _with_samples(hand_template(), pdfs, [truth(p) for p in people])   # truth: concrete path -> value
    proof = prove(t, pdfs)
    tax = next(f for f in proof.fields if f.path == "investor.tax_id")
    assert tax.ready and [s.outcome for s in tax.samples] == ["agree"] * 4 and tax.count == (4, 4)
    assert proof.ready == proof.total

def test_a_reader_that_reads_another_value_disagrees_and_is_not_ready():
    p = clients(1, seed=7)[0]
    pdf = make_form(p, kind="fillable")
    wrong = {**truth(p), "investor.legal_name": "Someone Else"}
    f = next(f for f in prove(_with_samples(hand_template(), [pdf], [wrong]), [pdf]).fields
             if f.path == "investor.legal_name")
    assert (f.ready, f.samples[0].outcome) == (False, "disagree")

def test_blank_on_both_sides_is_missing_and_a_reader_that_misses_a_value_disagrees():
    p = dataclasses.replace(clients(1, seed=7)[0], street="")
    pdf = make_form(p, kind="flattened")
    proof = prove(_with_samples(hand_template(), [pdf], [truth(p)]), [pdf])
    street = next(f for f in proof.fields if f.path == "address.street")
    assert street.samples[0].outcome == "missing" and not street.ready     # nothing agreed anywhere
    missed = {**truth(p), "address.street": "1 Main Street"}
    street = next(f for f in prove(_with_samples(hand_template(), [pdf], [missed]), [pdf]).fields
                  if f.path == "address.street")
    assert street.samples[0].outcome == "disagree"

def test_a_failed_check_the_model_agrees_with_does_not_block_ready():
    p = dataclasses.replace(clients(1, seed=7)[0], tax_id="8422-19037")
    pdf = make_form(p, kind="fillable")
    f = next(f for f in prove(_with_samples(hand_template(), [pdf], [truth(p)]), [pdf]).fields
             if f.path == "investor.tax_id")
    assert (f.samples[0].outcome, f.ready) == ("failed_check", True)

def test_absent_paths_are_absent_and_left_out_of_the_total():
    p = clients(1, seed=7)[0]; pdf = make_form(p, kind="fillable")
    t = _with_samples(hand_template(), [pdf], [truth(p)])
    t = t.model_copy(update={"absent": ["wire.bank"], "readers": {k: v for k, v in t.readers.items() if k != "wire.bank"}})
    proof = prove(t, [pdf])
    bank = next(f for f in proof.fields if f.path == "wire.bank")
    assert bank.absent and not bank.ready and bank.samples[0].outcome == "absent"
    assert proof.total == len([f for f in proof.fields if not f.absent])

def test_prove_refuses_pdfs_that_are_not_the_templates_samples():
    p = clients(2, seed=7); pdfs = [make_form(x, kind="fillable") for x in p]
    t = _with_samples(hand_template(), pdfs[:1], [truth(p[0])])
    with pytest.raises(FormcastError, match="sample"):
        prove(t, pdfs[1:])

def test_learn_maps_adds_a_wording_the_model_answered_with_an_allowed_answer():
    p = dataclasses.replace(clients(1, seed=7)[0], country="United States of America")
    pdf = make_form(p, kind="fillable")
    t = _with_samples(hand_template(), [pdf], [{**truth(p), "investor.country": "US"}])
    before = next(f for f in prove(t, [pdf]).fields if f.path == "investor.country")
    assert before.samples[0].outcome in ("disagree", "failed_check")
    learned = learn_maps(t, prove(t, [pdf]))
    after = next(f for f in prove(learned, [pdf]).fields if f.path == "investor.country")
    assert after.samples[0].outcome == "agree" and after.samples[0].read.value == "US"

def test_sample_marks_give_the_read_place_and_the_editable_boxes():
    p = clients(1, seed=7)[0]; pdf = make_form(p, kind="flattened")
    marks = sample_marks(hand_template(), pdf)
    kinds = {(m.path, m.kind) for m in marks}
    assert ("investor.tax_id", "read") in kinds and ("investor.tax_id", "box") in kinds
    assert ("investor.tax_id", "label") in kinds
    assert {m.answer for m in marks if m.path == "investor.type" and m.kind == "option"} == set(INVESTOR_TYPES)
```

  Also in `tests/extract/test_model.py`: a template with `setup` and `samples` round-trips through
  `model_dump_json(by_alias=True)` / `model_validate_json`, and a template without them still validates (both
  default empty), and `samples[*].opinion` keys that are not concrete paths are refused (validator message names
  the path). If `truth()` returns cleaned values keyed by concrete path already, use it as is; otherwise build the
  opinion dict in the test from the client the same way.

- [ ] **Step 2: Run them to verify they fail** — `/Users/landonthornton/Documents/Projects/RLMayonnaise/.venv/bin/python -m pytest tests/extract/test_proof.py tests/extract/test_model.py -q`; expected: import errors for `proof` and the new model names.

- [ ] **Step 3: Implement.** In `model.py` add `SetupInfo`, `SampleRecord` and the two fields (extend the
  existing validator: opinion keys must be concrete paths). In `proof.py`:
  - `same_value`: `None` equals only `None`; bools compare as bools; when both sides parse as numbers (strings with
    thousands commas in groups of three, as `read._coerced` allows) compare as floats; otherwise compare
    `label_key(str(a)) == label_key(str(b))`.
  - `prove`: refuse with `FormcastError("these PDFs are not the template's samples ...")` when the count or any
    sha256 differs from `template.samples`. `extract()` each PDF; per concrete path and sample decide the outcome:
    path in `absent` -> `absent`; read `found`/`unsure` -> `agree` if `same_value(read.value, opinion)` or
    `same_value(read.raw, opinion)` else `disagree`; read `failed_check` -> `failed_check` when the opinion is not
    `None` and `same_value(read.raw, opinion)`, else `disagree`; read `missing`/`absent` -> `missing` when the opinion
    is `None`, else `disagree`. A path the model was not asked about (absent from `opinion`) counts the opinion as
    `None`. `ready` = not absent, no `disagree`, and at least one `agree` or `failed_check`.
  - `learn_maps`: for each path whose schema has `enum`, every sample where the opinion is one of the enum's answers
    and the raw text is not, add `raw -> opinion` to the reader's `map` transform (append a
    `Transform(op="map", args={"values": {...}})` as the reader's first clean step when it has none); return a copy.
  - `sample_marks`: open the PDF once (`DocText`), compute each page's `page_fit` when the template's layout has
    lines on it; per path: the read place of its `extract` result (`kind="read"`), each `BoxRead` moved by the
    offset (`"box"`), each anchor label found (`"label"`, the first place), each `ChoiceRead` option's drawn box
    moved by the offset, or its widget's rect (`"option"`, `answer` = the option key).
  - Add the public names to `extract/__init__.py`'s `_LAZY` table.

- [ ] **Step 4: Run the tests** — same command; expected: all pass. Then `pytest tests/extract -q`: all pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: templates keep the samples' readings; proof compares the readers with them"`

---

### Task 2: Edits to a template, with no model

**Files:**
- Create: `src/formcast/extract/edit.py`
- Modify: `src/formcast/extract/__init__.py`
- Test: `tests/extract/test_edit.py`

**Interfaces:**
- Consumes: `ExtractionTemplate`, `Reader`, strategies, `concrete_paths` (model.py); `Transform`.
- Produces (pydantic models, `extra="forbid"`, discriminated by `op`):
  - `SetField(op="set_field", path, title: str | None = None, description: str | None = None, type: Literal["text","number","integer","date","yes/no"] | None = None, required: bool | None = None, pattern: str | None = None, enum: list[str] | None = None, minimum: float | None = None, maximum: float | None = None)` — `pattern=""` or `enum=[]` clears it.
  - `SetBox(op="set_box", path, page: int, rect: Rect)` — replaces the path's `BoxRead`, or inserts one right after
    its `WidgetRead` (first when it has none).
  - `SetOptionBox(op="set_option_box", path, answer: str, page: int, rect: Rect)` — sets the drawn box of one answer
    in the path's page-rect `ChoiceRead` (adds that `ChoiceRead` after the widget one when missing).
  - `SetLabels(op="set_labels", path, labels: list[str], side: Literal["right","below"] | None = None)` — replaces
    the labels (and side) of the path's `AnchorRead`, or appends one; `labels=[]` removes it.
  - `SetClean(op="set_clean", path, clean: list[Transform])`
  - `MarkAbsent(op="mark_absent", path)`, `Restore(op="restore", path)`, `RemoveField(op="remove_field", path)`
  - `Edit = Annotated[SetField | SetBox | SetOptionBox | SetLabels | SetClean | MarkAbsent | Restore | RemoveField, Field(discriminator="op")]`
  - `apply_edit(template: ExtractionTemplate, edit: Edit) -> ExtractionTemplate` (returns a validated copy).

- [ ] **Step 1: Write the failing tests** (`hand_template()` as the base):

```python
def test_set_field_edits_the_schema_of_the_path_and_type_date_means_string_with_format():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.dob", title="Born", type="date", required=True))
    prop = t.schema_["properties"]["investor"]["properties"]["dob"]
    assert (prop["title"], prop["type"], prop["format"]) == ("Born", "string", "date")
    assert "dob" in t.schema_["properties"]["investor"]["required"]

def test_an_item_path_edits_the_items_schema_once():
    t = apply_edit(hand_template(), SetField(op="set_field", path="owners[2].percent", type="number", maximum=100))
    item = t.schema_["properties"]["owners"]["items"]["properties"]["percent"]
    assert (item["type"], item["maximum"]) == ("number", 100)

def test_clearing_a_pattern_and_an_enum():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.tax_id", pattern=""))
    assert "pattern" not in t.schema_["properties"]["investor"]["properties"]["tax_id"]

def test_set_box_replaces_the_box_and_keeps_the_order():
    t = apply_edit(hand_template(), SetBox(op="set_box", path="investor.tax_id", page=0, rect=(100, 100, 200, 114)))
    kinds = [s.kind for s in t.readers["investor.tax_id"].strategies]
    assert kinds == ["widget", "box", "anchor"] and t.readers["investor.tax_id"].strategies[1].rect == (100, 100, 200, 114)
    t = apply_edit(hand_template(), SetBox(op="set_box", path="wire.bank", page=0, rect=(1, 2, 3, 4)))
    assert [s.kind for s in t.readers["wire.bank"].strategies] == ["box", "phrase"]

def test_set_labels_replaces_or_removes_the_label_read():
    t = apply_edit(hand_template(), SetLabels(op="set_labels", path="investor.tax_id", labels=["TIN"], side="below"))
    a = t.readers["investor.tax_id"].strategies[-1]
    assert (a.labels, a.side) == (["TIN"], "below")
    t = apply_edit(t, SetLabels(op="set_labels", path="investor.tax_id", labels=[]))
    assert [s.kind for s in t.readers["investor.tax_id"].strategies] == ["widget", "box"]

def test_mark_absent_drops_the_reader_and_restore_brings_back_an_empty_field():
    t = apply_edit(hand_template(), MarkAbsent(op="mark_absent", path="wire.bank"))
    assert "wire.bank" in t.absent and "wire.bank" not in t.readers
    t = apply_edit(t, Restore(op="restore", path="wire.bank"))
    assert "wire.bank" not in t.absent

def test_remove_field_removes_every_item_path_and_its_readings():
    t = hand_template().model_copy(update={"samples": [SampleRecord(file="a", sha256="0" * 64,
        opinion={"owners[0].name": "Ana", "investor.tax_id": "1"})]})
    t = apply_edit(t, RemoveField(op="remove_field", path="owners[1].name"))
    assert not any(p.startswith("owners[") and p.endswith(".name") for p in t.readers)
    assert t.samples[0].opinion == {"investor.tax_id": "1"}

def test_an_unknown_path_is_refused():
    with pytest.raises(FormcastError, match="no field"):
        apply_edit(hand_template(), SetField(op="set_field", path="nope", title="x"))
```

- [ ] **Step 2: Run them to verify they fail** — `pytest tests/extract/test_edit.py -q`; expected: ImportError.

- [ ] **Step 3: Implement `edit.py`.** Locate the property schema of a concrete path by walking `properties`, and
  `items` for `[n]` segments (so any item path edits the items schema). `RemoveField` deletes the property, its
  name from its parent's `required`, every concrete path under it from `readers`, `absent` and every sample's
  `opinion`, and an object left with no properties (its parent's entry too). `Restore` only takes the path out of
  `absent` (the field then has no reader until the person gives it one). Raise `FormcastError("no field <path>")`
  for a path that is not one of `concrete_paths(schema)`, and for `SetOptionBox` an answer not in the path's enum
  or choice options.

- [ ] **Step 4: Run** `pytest tests/extract/test_edit.py tests/extract -q`; expected: pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: edits to a template's fields and readers, with no model"`

---

### Task 3: Reading the samples, and what the model sees of them

**Files:**
- Create: `src/formcast/extract/samples.py`
- Test: `tests/extract/test_samples.py`

**Interfaces:**
- Consumes: `build_form_map`, `detect_on_page`, `looks_like_prose`, `PageProgress`, `DetectPolicy` (formcast.pagemap.build);
  `open_pdf`, `PDF_LOCK` (formcast.pagemap.extract); `summarize_page`; `render_marked_page`; `candidate_fields`
  (formcast.template.model); `DocText`.
- Produces:
  - `SamplePdf(file: str, pdf: bytes)` (frozen dataclass)
  - `SampleDoc(file: str, pdf: bytes, form_map: FormMap, values: dict[str, str | bool])` (dataclass; `values` =
    every widget's current value by name, from `DocText.widget_value`, empty and off values left out)
  - `Detect = Literal["auto", "all", "manual", "off"]`
  - `read_samples(samples: list[SamplePdf], *, detect: Detect = "auto", pages: dict[int, list[int]] | None = None, ocr: Literal["auto","never"] = "auto", detectors=None, policy: DetectPolicy | None = None, on_page: Callable[[int, PageProgress], None] | None = None) -> list[SampleDoc]`
  - `detected_pages(doc: SampleDoc) -> list[int]`
  - `sample_view(doc: SampleDoc, index: int, *, max_chars: int = SAMPLE_CHARS) -> str` with `SAMPLE_CHARS = 20_000`
  - `sample_images(doc: SampleDoc, *, max_pages: int = MAX_IMAGE_PAGES) -> list[tuple[int, bytes]]`

- [ ] **Step 1: Write the failing tests:**

```python
NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))

def test_a_fillable_sample_keeps_its_widget_values_and_runs_no_detection():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="fillable"))], **NO_DETECTORS)
    assert doc.values["tax_id"] == p.tax_id and detected_pages(doc) == []

def test_detection_modes_choose_the_pages(monkeypatch):
    seen = []
    real = samples_mod.detect_on_page
    monkeypatch.setattr(samples_mod, "detect_on_page", lambda page, pm, **kw: seen.append(pm.index) or real(page, pm, **kw))
    pdf = make_form(clients(1, seed=3)[0], kind="flattened")       # page 0 a form, page 1 prose
    read_samples([SamplePdf("a.pdf", pdf)], detect="auto", **NO_DETECTORS); assert seen == [0]
    seen.clear(); read_samples([SamplePdf("a.pdf", pdf)], detect="all", **NO_DETECTORS); assert seen == [0, 1]
    seen.clear(); read_samples([SamplePdf("a.pdf", pdf)], detect="manual", pages={0: [1]}, **NO_DETECTORS); assert seen == [1]
    seen.clear(); read_samples([SamplePdf("a.pdf", pdf)], detect="off", **NO_DETECTORS); assert seen == []

def test_an_unreadable_sample_is_refused_by_name():
    with pytest.raises(FormcastError, match="b.pdf"):
        read_samples([SamplePdf("b.pdf", b"not a pdf")], **NO_DETECTORS)

def test_the_models_view_names_the_sample_its_kind_and_its_form_field_values():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="fillable"))], **NO_DETECTORS)
    view = sample_view(doc, 2)
    assert view.startswith("SAMPLE 2 (a.pdf, fillable") and f'tax_id = "{p.tax_id}"' in view
    assert "PAGE 1" in view and len(sample_view(doc, 2, max_chars=500)) <= 500

def test_sample_images_render_png_pages():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1)[0], kind="flattened"))], **NO_DETECTORS)
    images = sample_images(doc, max_pages=1)
    assert len(images) == 1 and images[0][1][:8] == b"\x89PNG\r\n\x1a\n"
```

  (`make_form`'s page 1 is the agreement prose; if `looks_like_prose` does not call it prose, adjust the test to
  build a page that is, rather than changing the rule.)

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement.** `read_samples` builds each form map with `build_form_map(pdf, detect=False, ocr=ocr,
  on_progress=...)` (a `FormcastError` re-raised as `FormcastError(f"{file}: {message}")`), then runs
  `detect_on_page` (under `PDF_LOCK`, the document opened with `open_pdf`) on the pages the mode picks: `auto` =
  pages that are not `fillable` and not `looks_like_prose`; `all` = every page not `fillable`; `manual` = `pages[i]`
  (page numbers outside the document ignored); `off` = none. `detected_pages` returns the pages the build detected
  on (keep them on the `SampleDoc` as a private list). `sample_view`: a header line
  `SAMPLE {index} ({file}, fillable|flattened, {n} pages)`, then `PAGE {n}` and `summarize_page(...)` per page, then
  `FORM FIELD VALUES:` with one `name = "value"` line per widget value (booleans as `true`/`false`), cut to
  `max_chars`. `sample_images` renders the pages with the most boxes first (widgets plus candidates, then page
  order) with `render_marked_page(pdf, n, candidate_fields(page))`, at most `max_pages`.

- [ ] **Step 4: Run** `pytest tests/extract/test_samples.py -q`; expected: pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: read the samples and find their blanks; what the model sees of a sample"`

---

### Task 4: From the model's proposals to readers, by code

**Files:**
- Create: `src/formcast/extract/readers.py`
- Modify: `src/formcast/extract/read.py` (lone checkbox)
- Test: `tests/extract/test_readers.py`, `tests/extract/test_read.py`

**Interfaces:**
- Consumes: `SampleDoc` (Task 3); strategies, `Reader`, `Layout`, `concrete_paths`; `page_fit`; `DocText`.
- Produces:
  - `BoxRef(sample: int, id: str)` — a candidate (`"c0_3"`) or widget name on that sample's form map.
  - `OptionProposal(widget: str | None = None, state: str | None = None, box: BoxRef | None = None)`
  - `ReaderProposal(path: str, widgets: list[str] = [], box: BoxRef | None = None, labels: list[str] = [], side: Literal["right","below"] = "right", max_lines: int = 1, max_width: float = 300.0, phrases: list[str] = [], stop: Literal["comma","sentence","line","digits"] = "comma", options: dict[str, OptionProposal] = {}, clean: list[Transform] = [])`
  - `resolve(proposals: list[ReaderProposal], samples: list[SampleDoc], layout: Layout, schema: dict) -> tuple[dict[str, Reader], list[str]]` — readers by path, and warnings.

- [ ] **Step 1: Write the failing tests** (samples from `read_samples` over corpus copies, `NO_DETECTORS`; layout
  from `learn_layout` of their PDFs):

```python
def test_widgets_get_a_box_at_their_place_and_strategies_keep_the_specs_order(two_kinds):
    samples, layout = two_kinds                     # [fillable, flattened] of two clients
    readers, warnings = resolve([ReaderProposal(path="investor.tax_id", widgets=["tax_id"], labels=["Taxpayer ID"])],
                                samples, layout, hand_template().schema_)
    kinds = [s.kind for s in readers["investor.tax_id"].strategies]
    assert kinds == ["widget", "box", "anchor"] and warnings == []
    # hand_template() with these readers (others dropped) and this layout reads the flattened sample by its box
    t = hand_template().model_copy(update={"readers": readers, "layout": layout})
    got = {f.path: f for f in extract(t, samples[1].pdf).fields}["investor.tax_id"]
    assert (got.status, got.strategy) == ("found", "box")

def test_names_labels_phrases_and_boxes_no_sample_has_are_dropped_with_a_warning(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve([ReaderProposal(path="investor.tax_id", widgets=["tax_id", "ghost"],
        labels=["Nowhere label"], phrases=["never written"], box=BoxRef(sample=0, id="c9_9"))], samples, layout, SCHEMA)
    assert [s.kind for s in readers["investor.tax_id"].strategies] == ["widget", "box"]   # the box of tax_id
    assert any("ghost" in w for w in warnings) and any("Nowhere label" in w for w in warnings)
    assert any("never written" in w for w in warnings) and any("c9_9" in w for w in warnings)

def test_a_path_with_nothing_usable_gets_no_reader_and_a_warning(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve([ReaderProposal(path="wire.bank", labels=["Nowhere"])], samples, layout, SCHEMA)
    assert "wire.bank" not in readers and any("wire.bank" in w and "no way to read" in w for w in warnings)

def test_a_proposal_for_a_path_not_in_the_schema_is_ignored_with_a_warning(two_kinds): ...
    # ReaderProposal(path="made.up", widgets=["tax_id"]) -> no reader for it, a warning naming "made.up"

def test_choice_options_from_widgets_also_get_their_drawn_boxes(two_kinds):
    samples, layout = two_kinds
    options = {v: OptionProposal(widget=f"type_{v}") for v in INVESTOR_TYPES}
    readers, _ = resolve([ReaderProposal(path="investor.type", options=options)], samples, layout, SCHEMA)
    widget_read, box_read = readers["investor.type"].strategies
    assert widget_read.options["trust"].widget == "type_trust" and box_read.options["trust"].rect is not None

def test_a_box_from_a_shifted_sample_is_stored_in_the_layouts_place(): ...   # sample made with shift=SHIFT
```

  In `tests/extract/test_read.py` add: a boolean path read by a `ChoiceRead` with one option gives `True` when
  its box is marked and `False` when not (both `found`), on a fillable checkbox widget and on a drawn box.

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement.** For each proposal whose path is a concrete path (else a warning): keep widget names
  some sample's form map has (exact name); for each kept widget add one `BoxRead` at its rect on the first sample
  that has it, moved into the layout's place by subtracting that sample page's `page_fit(layout, DocText(pdf),
  page).offset`; a `box` ref resolves to the candidate (or widget) of that id on that sample, moved the same way;
  keep labels that `DocText.find_label` finds on at least one sample and phrases that `after_phrase` finds on at
  least one; options likewise (a widget option also gives a page-rect `ChoiceMark` from the widget's rect, moved).
  Build strategies in the spec's order: `WidgetRead(names)`, one `BoxRead` (the box ref's, else the first widget's),
  `AnchorRead(labels, page of the first label found, side, max_width, max_lines)`, `PhraseRead`; for options
  `ChoiceRead(widget marks)`, then `ChoiceRead(rect marks)`. Every dropped item is a warning
  `"<path>: <what> '<value>' is on no sample; left out"`. A path left with no strategies gets no reader and the
  warning `"<path>: no way to read it was found on the samples"`. In `read.py`, a `ChoiceRead` on a path whose
  schema type is `boolean` and that has exactly one option gives `True` when it is marked and `False` when not
  (update the module docstring's *Strategies in order* paragraph).

- [ ] **Step 4: Run** `pytest tests/extract -q`; expected: pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: proposals become readers by code, keeping only what the samples have; a lone box reads yes or no"`

---

### Task 5: The four model calls

**Files:**
- Create: `src/formcast/extract/prompts.py`, `src/formcast/extract/ask.py`, `tests/extract/fakes.py`
- Modify: `tests/agent/test_prompts.py` (add both new modules to `MODEL_FACING`)
- Test: `tests/extract/test_ask.py`

**Interfaces:**
- Consumes: `_ask` from `formcast.data.ai`; `_schema_lines` from `formcast.data.ai`; `Message`, `TextPart`,
  `ImagePart`, `LLMConfig`, `Provider`, `Usage` (formcast.llm); `SampleDoc`, `sample_view`, `sample_images`
  (Task 3); `ReaderProposal` (Task 4); `flatten_schema`, `concrete_paths`.
- Produces (each returns its usage):
  - `FieldsAnswer(schema: dict, line_paths: dict[str, list[str]], usage: Usage)` from
    `fields_from_lines(lines: list[str], samples: list[SampleDoc], llm, *, existing: list[str] = [], vision: bool = True) -> FieldsAnswer`
    — `line_paths` keyed by the line text, values schema paths in `flatten_schema` form (`owners[].name`).
  - `fields_on_form(schema: dict, samples: list[SampleDoc], llm, *, vision: bool = True) -> tuple[list[str], Usage]`
    — the leaf schema paths (`flatten_schema` form) this form has.
  - `propose_readers(schema: dict, paths: list[str], samples: list[SampleDoc], llm, *, vision: bool = True, max_parallel: int = 4) -> tuple[list[ReaderProposal], Usage]`
    — `paths` concrete; sent in chunks of `READER_CHUNK = 30`, chunks in parallel.
  - `read_values(schema: dict, paths: list[str], doc: SampleDoc, index: int, llm, *, vision: bool = True) -> tuple[dict[str, Any], Usage]`
    — every asked path as a key, `None` where the model found nothing.
- `tests/extract/fakes.py`: `Scripted(*answers)` as in `tests/data/test_ai.py`, and `CorpusModel(clients_by_file)`
  — a provider that answers by the system prompt (`messages[0].content` compared with the prompt constants, as
  `tests/demo/test_server_oneshot.py`'s `Model` does): `FIELDS_SYSTEM` -> the corpus schema's fields for the lines;
  `ON_FORM_SYSTEM` -> the corpus schema's leaf paths that are in the asked schema; `READERS_SYSTEM` -> proposals
  made from `hand_template()`'s readers for the asked paths (widget names, labels, phrases, options); `VALUES_SYSTEM`
  -> the truth for the sample named in the user text (`SAMPLE n (file, ...`), restricted to the asked paths;
  `DERIVE_SYSTEM` (formcast.data.ai) -> `{"schema": <corpus schema>}`. It records calls by kind and is thread-safe.

- [ ] **Step 1: Write the failing tests** (`Scripted` replies as JSON strings):

```python
def test_fields_from_lines_sends_the_lines_and_the_sample_and_returns_a_checked_schema(sample_docs):
    reply = {"schema": {"type": "object", "properties": {"wire": {"type": "object", "properties": {
        "bank": {"type": "string", "title": "Bank", "description": "The sending bank."}}}}},
        "line_paths": {"wire details": ["wire.bank"]}}
    llm = Scripted(json.dumps(reply))
    got = fields_from_lines(["wire details"], sample_docs, llm)
    assert got.line_paths == {"wire details": ["wire.bank"]} and got.usage.input_tokens == 10
    text = " ".join(p.text for p in llm.calls[0][1].content if isinstance(p, TextPart))
    assert "1. wire details" in text and "SAMPLE 1" in text
    assert any(isinstance(p, ImagePart) for p in llm.calls[0][1].content)

def test_a_line_path_that_is_not_in_the_schema_or_a_name_that_exists_is_asked_again(sample_docs):
    bad = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["b"]}}
    good = {"schema": {"type": "object", "properties": {"b": {"type": "string"}}}, "line_paths": {"x": ["b"]}}
    llm = Scripted(json.dumps(bad), json.dumps(good))
    assert fields_from_lines(["x"], sample_docs, llm).line_paths == {"x": ["b"]}
    assert "can't be used" in str(llm.calls[1][-1].content)
    clash = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["a"]}}
    with pytest.raises(FormcastError, match="could not be used"):
        fields_from_lines(["x"], sample_docs, Scripted(json.dumps(clash), json.dumps(clash)), existing=["a"])

def test_fields_on_form_keeps_only_paths_of_the_schema(sample_docs): ...
    # reply {"on_form": ["investor.tax_id", "made.up"]} is retried; {"on_form": ["investor.tax_id"]} returns it
def test_propose_readers_chunks_paths_and_merges_the_answers(sample_docs): ...
    # 31 asked paths -> 2 calls (30 + 1); the proposals of both come back; usage is the sum (20, 10)
def test_read_values_fills_unasked_keys_with_none_and_drops_unknown_ones(sample_docs): ...
    # asked ["a", "b"], reply {"values": {"a": 1, "zzz": 2}} -> {"a": 1, "b": None}; only that sample's view is sent
def test_vision_false_sends_no_images(sample_docs): ...
    # every call's user content has no ImagePart
```

  And in `tests/agent/test_prompts.py` the existing guard tests now cover both modules (no new test function).

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Write the prompts and the calls.** Each prompt says what the model gets (numbered samples as in
  `sample_view`, page images marked with box ids, the asked paths as `_schema_lines` renders them) and the one JSON
  object to answer with:
  - `FIELDS_SYSTEM`: turn each numbered line into one or more defined fields (JSON Schema properties with `title`,
    `description` (a plain-language definition), `type`, and checks as keywords: `pattern`, `enum`, `format`
    (`date`, `email`), `minimum`/`maximum`, `required`); repeated rows as arrays of objects with `maxItems`; snake_case
    names, nested by topic; never reuse an existing name; answer `{"schema": {...}, "line_paths": {line: [paths]}}`.
  - `ON_FORM_SYSTEM`: given a destination's fields, say which this form asks for; answer `{"on_form": [paths]}`.
  - `READERS_SYSTEM`: for each path, how to read its answer on copies of this form: the form field names that hold
    it (`widgets`), the box it sits in on a sample (`box`: `{"sample": n, "id": "c0_3"}`), the printed label next to it
    and on which side (`labels`, several wordings when samples differ; specific, not generic), or for an answer inside
    running text the phrase before it and where it stops (`phrases`, `stop`); for a fixed set of answers, each answer
    and the form field (with its on-state) or box that marks it (`options`); and `clean` steps from the transforms
    list (`transforms_ref()` from `formcast.data.mapping`), including a `map` from each wording seen on the samples
    to an allowed answer. Give a label for every field that has one, so copies without form fields still read.
    Answer `{"readers": [ReaderProposal...]}`.
  - `VALUES_SYSTEM`: read this one document's answer to each path as it is on the page (dates as YYYY-MM-DD, numbers
    as numbers, a fixed-set answer as one of its allowed answers, `null` when the document has no answer); answer
    `{"values": {path: value}}`.
  Each call builds `[Message("system", PROMPT), Message("user", parts)]` with, for `vision=True`, the
  `sample_images` of the first sample with widgets and of the first without (each image after a
  `TextPart("Sample n, page m:")`), then one `TextPart` with the samples' views (`sample_view`, budget
  `TEXT_BUDGET = 60_000` split evenly) and the asked paths; `read_values` sends only its own document. Each `check`
  validates with pydantic models (`ReaderProposal`, etc.) and the rules in the tests: schema parses with
  `flatten_schema`, line paths exist in it, no new top-level name equals one of `existing`, on-form paths are leaf
  paths of the asked schema, proposal paths are asked paths. `propose_readers` runs chunks in a
  `ThreadPoolExecutor(max_parallel)` and sums usage.

- [ ] **Step 4: Run** `pytest tests/extract/test_ask.py tests/agent/test_prompts.py -q`; expected: pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: the model calls for setup (fields from lines, on-form fields, readers, one document's values)"`

---

### Task 6: Building a template from samples, in stages

**Files:**
- Create: `src/formcast/extract/setup.py`, `tests/llm/test_extract_setup.py`
- Modify: `src/formcast/extract/__init__.py`, `src/formcast/extract/__init__.py` docstring (the package no longer
  says "no model call" without the exception), `README.md` (the extraction section: building a template)
- Test: `tests/extract/test_setup.py`

**Interfaces:**
- Consumes: Tasks 1-5; `derive_schema` (formcast.data.ai); `learn_layout`; `form_info_for`; `SessionCancelled`.
- Produces:
  - `STAGES: tuple[tuple[str, str], ...] = (("read", "Read the sample"), ("detect", "Find the blanks"), ("fields", "Design the fields"), ("readers", "Find each field on the sample"), ("check", "Check the reads"))`
  - `FIELDS_LABELS = {"ai": "Design the fields", "lines": "Turn the lines into fields", "destination": "Match the destination's fields"}`
  - `StageEvent(key: str, status: Literal["running","done","skipped"], detail: str = "")` (frozen dataclass)
  - `SetupSpec(name: str, template_id: str | None = None, start: Literal["ai","lines","destination"] = "ai", lines: list[str] = [], destination: dict | None = None, destination_name: str | None = None, detect: Detect = "auto", pages: dict[int, list[int]] = {}, ocr: Literal["auto","never"] = "auto", vision: bool = True, sample_how: str = "")`
  - `SetupResult(template: ExtractionTemplate, proof: Proof, samples: list[SampleDoc], usage: Usage, warnings: list[str])` (dataclass)
  - `build_setup(samples: list[SamplePdf], spec: SetupSpec, llm: LLMConfig | Provider, *, detectors=None, policy=None, on_stage: Callable[[StageEvent], None] | None = None, on_page: Callable[[int, PageProgress], None] | None = None, max_parallel: int = 4) -> SetupResult`
  - `add_line(template: ExtractionTemplate, line: str, samples: list[SampleDoc], llm, *, vision: bool = True) -> SetupResult`

- [ ] **Step 1: Write the failing tests** (`CorpusModel` from Task 5; samples are five corpus copies: three
  fillable and two flattened, one flattened shifted by `SHIFT`):

```python
def test_ai_start_builds_a_ready_template_that_reads_the_corpus_with_no_wrong_value(corpus_dir, five_samples):
    model = CorpusModel(five_samples.clients)
    events = []
    result = build_setup(five_samples.pdfs, SetupSpec(name="Client info"), model, on_stage=events.append, **NO_DETECTORS)
    assert [e.key for e in events if e.status == "done"] == ["read", "detect", "fields", "readers", "check"]
    assert result.proof.ready == result.proof.total and result.warnings == []
    assert result.template.template_id == "client_info" and result.template.setup.start == "ai"
    assert len(result.template.samples) == 5
    # every corpus folder document: no found value differs from truth(client), as
    # tests/extract/test_read.py::test_the_corpus_reads_with_no_wrong_found_value checks the hand template
    # (move its per-document check into a helper in tests/extract/corpus.py and call it from both tests)
    # corpus_clients(): the clients the corpus_dir fixture wrote, by file (tests/extract/conftest.py; expose them
    # there if it does not); the W-9 copies have no client and must have no found value at all
    for f, client in corpus_clients(corpus_dir):
        assert_no_wrong_found(extract(result.template, f), client)

def test_lines_start_records_which_paths_each_line_became(five_samples): ...
    # SetupSpec(start="lines", lines=["tax ID", "wire details"]): setup.line_paths has both lines, schema has
    # exactly the paths CorpusModel gave for them, readers only for those paths.

def test_destination_start_marks_the_fields_this_form_lacks_absent(five_samples):
    dest = {"type": "object", "properties": {**CORPUS_SCHEMA["properties"], "subscription": {"type": "object",
            "properties": {"amount": {"type": "number"}}}, "signers": {"type": "array", "maxItems": 2,
            "items": {"type": "object", "properties": {"name": {"type": "string"}}}}}}
    r = build_setup(five_samples.pdfs, SetupSpec(name="x", start="destination", destination=dest, destination_name="sub"),
                    CorpusModel(five_samples.clients), **NO_DETECTORS)
    assert {"subscription.amount", "signers[0].name", "signers[1].name"} <= set(r.template.absent)
    assert r.proof.total == len([f for f in r.proof.fields if not f.absent])

def test_an_unreadable_sample_fails_before_any_model_call(five_samples):
    model = CorpusModel(five_samples.clients)
    with pytest.raises(FormcastError, match="broken.pdf"):
        build_setup([*five_samples.pdfs[:2], SamplePdf("broken.pdf", b"%PDF-1.7 nope")], SetupSpec(name="x"), model, **NO_DETECTORS)
    assert model.calls == {}

def test_detect_off_skips_the_detect_stage(five_samples): ...      # StageEvent("detect", "skipped")
def test_a_cancel_from_on_stage_stops_the_build(five_samples): ...  # on_stage raises SessionCancelled at "fields"
def test_more_than_twenty_samples_or_none_are_refused(): ...

def test_add_line_adds_fields_their_readers_and_their_readings_then_reproves(five_samples):
    r = build_setup(five_samples.pdfs, SetupSpec(name="x", start="lines", lines=["tax ID"]), model, **NO_DETECTORS)
    added = add_line(r.template, "wire details", r.samples, model)
    assert {"wire.bank", "wire.account"} <= set(added.template.readers)
    assert all("wire.bank" in s.opinion for s in added.template.samples)
    assert added.template.setup.line_paths["wire details"] and added.proof.ready == added.proof.total
```

  `tests/llm/test_extract_setup.py` (marked `llm`, uses the `.env` model config the other `llm` tests use): build
  from five corpus samples with the AI start and real detectors off; assert no wrong found value over the corpus
  folder and print the ready count and usage.

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement `build_setup`.** Refuse 0 or more than 20 samples (`FormcastError`). Split
  `read_samples` (Task 3) into an internal `_read` (form maps with `detect=False`, widget values) and `_detect`
  (detection by mode), `read_samples` calling both, so the two stages are real: stage `read` is `_read` of every
  sample, stage `detect` is `_detect` (status `skipped` for `off`). Stage `fields` (label from `FIELDS_LABELS[spec.start]`): `ai` -> `derive_schema(doc.pdf, llm,
  form_map=doc.form_map, vision=spec.vision)` on the sample with the most widgets plus candidates (the first of
  equals); `lines` -> `fields_from_lines`; `destination` -> `fields_on_form(spec.destination, ...)`, the schema is the
  destination's and `absent` every concrete path whose `flatten_schema` path is not on the form. Stage `readers`:
  `layout = learn_layout(pdfs)`, `form = form_info_for(form map of the first sample with widgets, else the first)`,
  `propose_readers` for every concrete path not absent, `resolve`. Stage `check`: `read_values` per sample in a
  `ThreadPoolExecutor(max_parallel)` (only paths not absent), the `SampleRecord`s (sha256 of each PDF), `prove`,
  `learn_maps`, `prove` again; detail `"{ready} of {total} fields ready"`. `template_id` defaults to the name in
  lower snake case (letters and digits). `on_stage` is called with `running` before and `done` after each stage;
  an exception it raises (such as `SessionCancelled`) propagates. `add_line`: `fields_from_lines([line], samples,
  llm, existing=top-level names)`, merge the new properties into the schema, `propose_readers` and `resolve` for the
  new concrete paths, `read_values` for them on each sample (merged into each `SampleRecord.opinion`), record
  `line_paths[line]` (and append the line to `setup.lines`), then `prove`, `learn_maps`, `prove`.

- [ ] **Step 4: Run** `pytest tests/extract -q` then the full suite; expected: pass (the `llm` test deselected).

- [ ] **Step 5: Commit** — `git commit -m "Extract: build a template from samples in stages; add a field from a line"`

---

### Task 7: The AI pass over a run's failing documents

**Files:**
- Create: `src/formcast/extract/aipass.py`
- Modify: `src/formcast/extract/batch.py` (`Results` applies `ai.jsonl`), `src/formcast/extract/__main__.py`
  (`ai` subcommand), `README.md`
- Test: `tests/extract/test_aipass.py`, `tests/extract/test_cli.py`

**Interfaces:**
- Consumes: `Results`, `ReviewDecision`, `run_batch` (batch.py); `read_values` (Task 5); `read_samples` (one
  document, `detect="off"`); `concrete_paths`.
- Produces:
  - `AI = "ai.jsonl"` (in batch.py beside `RESULTS`); `AiAnswer(doc_id: str, path: str, value: Any, at: str)`
  - `AI_REASON = "read by the AI; check it"`
  - `required_paths(schema: dict) -> set[str]` — concrete paths named in their parent's `required`.
  - `AiPassSummary(documents: int, answered: int, usage: Usage)`
  - `ai_pass(templates: list[ExtractionTemplate], out_dir, llm, *, max_parallel: int = 4, vision: bool = True, on_progress: Callable[[int, int], None] | None = None) -> AiPassSummary`
  - `Results.effective` applies, before review decisions, each `AiAnswer` with a value to a field that is
    `missing` or `failed_check`: `status="unsure"`, `strategy="ai"`, `value`, `raw=str(value)`, reasons
    `[AI_REASON]`; `Results.decide` checks an `accept` against the field with AI answers applied.
  - CLI: `python -m formcast.extract ai OUT --template T.json [--template ...]`, model config from
    `LLMConfig.from_env("FORMCAST_EXTRACT")`; exit 2 with one line when it is not set.

- [ ] **Step 1: Write the failing tests:**

```python
def test_documents_with_failing_required_fields_are_asked_about_only_those_fields(tmp_path, corpus_dir):
    t = hand_template()
    run_batch([t], sorted(corpus_dir.glob("*.pdf")), tmp_path, workers=1)
    failing = {d.doc_id for d, f in Results.load(tmp_path).problems() if f.path in required_paths(t.schema_)
               and f.status in ("missing", "failed_check")}
    model = Scripted(*[json.dumps({"values": {"investor.tax_id": "12-3456789"}})] * len(failing))
    summary = ai_pass([t], tmp_path, model, vision=False)
    assert summary.documents == len(failing) and summary.answered == len(failing)
    doc = next(d for d in Results.load(tmp_path).documents() if d.doc_id in failing)
    f = next(f for f in Results.load(tmp_path).effective(doc).fields if f.path == "investor.tax_id")
    assert (f.status, f.strategy, f.value, f.reasons) == ("unsure", "ai", "12-3456789", [AI_REASON])

def test_a_second_pass_asks_nothing_again(tmp_path, corpus_dir): ...       # model with no script raises if called
def test_a_review_decision_wins_over_the_ai_answer(tmp_path, corpus_dir): ...
def test_the_ai_answer_stays_a_problem_until_reviewed(tmp_path, corpus_dir): ...  # summary().with_problems counts it
def test_the_cli_ai_command_needs_a_model_config(tmp_path): ...              # exit 2, one line naming FORMCAST_EXTRACT_
```

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement.** `ai_pass` loads `Results`, checks the templates (`check_templates`), and for each read
  document (no `error`) with required paths that are `missing` or `failed_check` after decisions and have no
  `AiAnswer` yet: opens the document's `file` with `read_samples([SamplePdf(file, bytes)], detect="off")`, calls
  `read_values(template.schema_, those_paths, doc, 1, llm, vision=vision)`, and appends one `AiAnswer` per asked path
  (value `None` included, so a later pass skips it) to `ai.jsonl`, flushed per document; documents in parallel
  (`max_parallel` threads), the file written by this thread only. A model failure on one document is skipped and
  counted (do not stop the pass). `Results` loads `ai.jsonl` as it loads `reviews.jsonl` (torn last line ignored,
  latest per doc and path wins). README: the AI pass, its cost, and that its answers are always reviewed.

- [ ] **Step 4: Run** `pytest tests/extract -q` then the full suite; expected: pass.

- [ ] **Step 5: Commit** — `git commit -m "Extract: an opt-in AI pass over a run's failing documents, its answers always reviewed"`
