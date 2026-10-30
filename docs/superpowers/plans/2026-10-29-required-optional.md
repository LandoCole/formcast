# Required and Optional Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make required/optional a first-class property of extraction fields and fill boxes: the agent classifies each
with a reason, the person can change it, extraction review splits required from optional problems, and the fill
report puts required empty boxes first.

**Architecture:** Extraction keeps the level in JSON Schema's `required` (plus an `x-required-reason` annotation);
`run_batch` records each template's required paths in run.json and `Results` counts problems by level. Fill gains
`required`, `required_reason`, `required_by` on `Field` and a `set_required` op (Python and the browser JS mirror);
the page pass classifies boxes; the fill report gains `required_gaps`. Extraction tasks 1-4 first, fill tasks 5-8.

**Tech Stack:** Python 3.12, pydantic v2, jsonschema, PyMuPDF; vanilla JS (ES modules) in demo/web; pytest,
node --test, Playwright.

**Spec:** docs/superpowers/specs/2026-10-29-required-optional-design.md

Run tests with the repo's venv: `/Users/landonthornton/Documents/Projects/RLMayonnaise/.venv/bin/python -m pytest`
(below written `pytest`), node tests with `node --test tests/web/*.test.js` (the glob; a bare directory errors on this
Node).

## Global Constraints

- Two levels only, Required and Optional; no conditional rules. Either/or fields and fields needed only under a
  condition are Optional, with the rule in the reason.
- The person's choice wins over the agent's.
- A blank optional field is never a required problem; a document is complete when it has no required problem.
- Exact copy: "Set by you"; "from the destination's schema"; section switch "Required · N" and "Optional · M";
  Optional note "Optional fields: blanks and doubtful answers on fields a copy may leave empty. They never stop a
  document from being complete."; stats tiles Documents / Complete / Required problems / Optional only / Set aside /
  Run time; fill report lines "Required boxes left empty: …" and "Other empty boxes: …"; counts tile
  "required left empty".
- Fill is unchanged for a person who never uses required: a template with no required boxes fills and reports as
  today except the gap line's wording ("Other empty boxes: …" in place of "No data field: …").
- Python and the browser JS fill engine stay in lockstep (identical field dumps; conformance cases for the new op).
- Model-facing prompt text is generic: never words from the test forms (no "SSN", "EIN", "W-9", "LLC", "tax
  classification"); tests/agent/test_prompts.py's MODEL_FACING guard must keep passing. Do not tune prompts on
  forms/extract/fw9.
- Old data loads unchanged: templates without the new keys, run.json without `"required"` (every field then counts
  as required, as before).
- Never add the subscription PDFs (forms/subdocs) to the repo or demo/data/forms.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A run made before this change (run.json without `"required"`): counts, pages and the review are exactly as before
   (every problem is a required problem). Test in Task 1.
2. A run of two templates where one path is required in one and optional in the other: each document's level comes
   from its own template. Test in Task 1.
3. A decision that settles a document's last required problem moves it to complete (and to optional only when it
   still has optional problems), in place, with no reload. Test in Task 1.
4. A template made in the browser engine (JS) with a `set_required` loads and replays in Python with an identical
   dump. Test in Task 5.
5. A required box that has no data link at all (unmapped) is a required gap, not hidden. Test in Task 6.

---

### Task 1: Extraction levels in the run and in Results

**Files:**
- Modify: `src/formcast/extract/batch.py` (`_start_run` ~495, `RunSummary` ~164, `Results` load/_tally/_set_problems/
  _with_problems/page/problems/field_counts/summary, `_kind` ~993, `_matches` ~1002, module docstring)
- Modify: `src/formcast/extract/aipass.py` only if `required_paths` moves (builder's call; keep the public name
  importable from `formcast.extract`)
- Test: `tests/extract/test_batch.py`

**Interfaces:**
- Consumes: `formcast.extract.aipass.required_paths(schema: dict) -> set[str]` (concrete paths).
- Produces:
  - run.json key `"required": {"<template_id>": ["<concrete path>", ...]}` (sorted), written by `_start_run`.
  - `Results.is_required(template_id: str | None, path: str) -> bool` — True when run.json has no `"required"` key;
    otherwise path in that template's list (False for an unknown template).
  - `RunSummary.optional_only: int = 0` (complete documents with an optional problem) and
    `RunSummary.with_optional: int = 0` (documents, of any kind, with an optional problem). Defaults keep old JSON valid.
  - `Results.page(offset, limit, *, path=None, status=None, level: Literal["required", "optional"] | None = None)` and
    `Results.problems(..., level=None)`: with a level, only documents with a problem of that level are listed and only
    that level's problem fields come with them.
  - `Results.field_counts()` rows gain `"required": bool` (True when the path is required in any template that has it).
  - `PROBLEMS` unchanged; a problem's level is `"required"` or `"optional"`.

- [ ] **Step 1: Write the failing tests** in `tests/extract/test_batch.py` (build runs with the corpus helpers the file
  already uses; make a template whose schema has `investor.legal_name` required and `investor.dob` optional, or
  adjust `hand_template`'s copy):
  - `test_run_json_records_each_templates_required_paths`: after `run_batch`, `run.json["required"][tid]` equals
    `sorted(required_paths(template.schema_))`.
  - `test_a_blank_optional_field_leaves_a_document_complete_with_an_optional_problem`: a copy whose only gap is an
    optional field → `summary().complete == 1`, `with_problems == 0`, `optional_only == 1`, `with_optional == 1`;
    `page(0, 50, level="required")` total 0; `page(0, 50, level="optional")` lists it with only the optional field.
  - `test_a_missing_required_field_makes_a_document_with_problems`: `with_problems == 1`; `level="required"` page
    lists only required fields; `level=None` lists both levels (as today).
  - `test_a_run_json_without_required_counts_every_field_as_required` (Review Focus 1): delete the key from run.json,
    `Results.load` → the optional blank counts as with_problems, `is_required(tid, any) is True`.
  - `test_each_document_takes_its_level_from_its_own_template` (Review Focus 2): two templates (different ids/layouts
    — reuse the file's two-template routing setup), one path required in one, optional in the other.
  - `test_settling_the_last_required_problem_makes_the_document_complete_in_place` (Review Focus 3): decide the
    required problem with a value; no reload (count `_read_all` as the existing G1 tests do); summary moves to
    complete/optional_only; the required list no longer has it; the optional list still does.
  - `test_field_counts_say_whether_a_path_is_required`.

- [ ] **Step 2: Run them to verify they fail**

  Run: `pytest tests/extract/test_batch.py -k "required or optional or own_template or settling_the_last" -v`
  Expected: FAIL (no `"required"` key, no `level` argument).

- [ ] **Step 3: Implement.** `_start_run` adds the key (and on resume leaves run.json as it is). `Results` loads it;
  `_tally` reads each document's `template_id` (record or effective doc) and splits problems by level: `_kinds`
  counts `with_problems` only for required problems; `optional_only`/`with_optional` are counted alongside. The kept
  per-document problems become `(path, status, level)` and the per-filter lists key on `(path, status, level)`;
  `_matches` gains `level`. `page`/`problems` filter fields by level. Update the module docstring (statuses
  unchanged; what a problem and complete mean now; the run.json key; old runs).

- [ ] **Step 4: Run the extraction tests**

  Run: `pytest tests/extract -q`
  Expected: PASS (existing tests unchanged in meaning: their templates' fields that were problems stay problems
  when required; fix only tests whose fixtures make an optional field blank and assert with_problems — say which in
  the report).

- [ ] **Step 5: Commit** — `git commit -m "Extraction runs count problems by level: a blank optional field leaves a document complete"`

### Task 2: The agent classifies extraction fields; the person's change is recorded

**Files:**
- Modify: `src/formcast/data/ai.py` (`REQUIRED_RULES` constant; `derive_schema(..., classify_required: bool = False)`)
- Modify: `src/formcast/extract/prompts.py` (FIELDS_SYSTEM uses `REQUIRED_RULES`)
- Modify: `src/formcast/extract/setup.py` (`_fields`: `classify_required=True` for the ai start; destination reasons)
- Modify: `src/formcast/extract/edit.py` (`SetField.required_reason`, "Set by you")
- Test: `tests/extract/test_edit.py`, `tests/extract/test_setup.py` (or the file holding `_fields` tests),
  `tests/data/test_ai.py` (or wherever derive_schema is tested), `tests/agent/test_prompts.py` (must stay green)

**Interfaces:**
- Produces:
  - `formcast.data.ai.REQUIRED_RULES: str` — the classification rules of the spec (*How the agent classifies* 1-4),
    the either/or and conditional rule, and the instruction to put a one-line `"x-required-reason"` on every
    property, quoting the form's wording when there is one. Generic wording only (see Global Constraints).
  - `derive_schema(pdf, llm, *, form_map=None, vision=True, instructions="", classify_required: bool = False)`:
    with True the system prompt is `DERIVE_SYSTEM` plus `REQUIRED_RULES`; with False the prompt is byte-identical to
    today's.
  - `REASON_KEY = "x-required-reason"` (one constant, importable from `formcast.extract.edit` or a shared module; the
    server and UI read the same key).
  - `SetField.required_reason: str | None = None`; applying a `set_field` with `required` set and `required_reason`
    None writes `"Set by you"`; with `required_reason` given writes it.

- [ ] **Step 1: Write the failing tests**
  - `test_derive_schema_asks_for_required_levels_only_when_asked`: with a `Scripted` llm (tests/extract/fakes.py),
    capture the system prompt: `classify_required=True` → contains `REQUIRED_RULES`; False → equals `DERIVE_SYSTEM`.
  - `test_fields_prompt_carries_the_required_rules`: `REQUIRED_RULES in FIELDS_SYSTEM` (or the rendered prompt).
  - `test_the_ai_start_classifies_required`: `build_setup` with `start="ai"` calls derive_schema with
    `classify_required=True` (Scripted reply with `required` and reasons → the template's schema keeps both).
  - `test_destination_fields_keep_their_levels_with_the_destination_reason`: a destination schema with `required`
    and no reasons → every property gets `"x-required-reason": "from the destination's schema"`; one that has a
    reason keeps it.
  - `test_a_persons_required_change_is_set_by_you` and `test_a_required_change_with_a_reason_keeps_it` (edit.py).

- [ ] **Step 2: Run them to verify they fail** — `pytest tests/extract/test_edit.py tests/extract/test_setup.py -k required -v` (and the derive test). Expected: FAIL.

- [ ] **Step 3: Implement.** Keep `DERIVE_SYSTEM` unchanged; append the rules only on request. The FIELDS reply
  format line mentions `x-required-reason` on each property. `CorpusModel`/`Scripted` fakes may need reasons in
  their canned schemas only if a test asserts them.

- [ ] **Step 4: Run** `pytest tests/extract tests/agent/test_prompts.py tests/data -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "The agent classifies extraction fields as required or optional with a reason; a person's change is set by you"`

### Task 3: Server: levels in the run summary, the problems route and to-data

**Files:**
- Modify: `demo/server/extraction.py` (`Run.summary` ~889, `Run.problems` ~919, `Run.table` ~982, a `Run.levels()`)
- Modify: `demo/server/extract_api.py` (`run_problems` ~765 and its doc comment ~75-84, `_page_query`)
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Consumes: Task 1's `Results.page(level=)`, `Results.is_required`, `RunSummary.optional_only/with_optional`,
  `field_counts()["required"]`; Task 2's `REASON_KEY`.
- Produces:
  - `GET /api/runs/{r}/problems?level=required|optional` (absent = both; another value → 400 `bad_request`); each
    problem dict gains `"required": bool` and `"required_reason": str` (the field's reason from its template's
    schema, `""` when none).
  - `GET /api/runs/{r}`: `summary` includes `optional_only` and `with_optional`; each `fields` entry gains
    `"required": bool` and `"required_reason": str`.
  - `Run.table()` counts a document as with problems only for required problems.

- [ ] **Step 1: Write the failing tests** (the file's existing run fixtures; a template with one optional field left
  blank on some copies):
  - `test_problems_by_level_list_only_that_levels_fields`, `test_a_bad_level_is_a_bad_request`,
  - `test_the_runs_fields_say_required_and_why`, `test_the_summary_counts_optional_only_documents`,
  - `test_to_data_counts_only_required_problems`.
- [ ] **Step 2: Run to verify they fail** — `pytest tests/demo/test_server_extract.py -k "level or required or optional" -v`.
- [ ] **Step 3: Implement.** Reasons are looked up per (template_id, generic path) from the run's templates (cache per
  run; templates do not change under a run).
- [ ] **Step 4: Run** `pytest tests/demo/test_server_extract.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "The run's problems and fields carry their level and reason"`

### Task 4: Review splits Required and Optional; setup shows the reason

**Files:**
- Modify: `demo/web/workbench/extract-review.js` (state `level`, `list()`, `renderDocs`, `renderStats`,
  `renderFieldTable`, `problemCard`, decide refresh), `demo/web/workbench/extract-model.js` (`runStats`,
  `fieldTable` with `required`, a `levelSwitch(summary)` helper or similar), `demo/web/workbench/index.html` (switch
  markup above the documents list, the Optional note), `demo/web/workbench/extract.css`
- Modify: `demo/web/workbench/extract-setup.js` (`inspectorParts` ~545: reason under the Required checkbox; field list
  marks required fields), `demo/web/engines/server/client.js` (`problems` passes `level`)
- Test: `tests/web/extract_model.test.js`, `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Consumes: Task 3's routes and fields.
- Produces: none for later tasks.

- [ ] **Step 1: Write the failing tests**
  - node: `runStats` gives tiles in the exact order and names of the Global Constraints (Required problems =
    `with_problems`, Optional only = `optional_only`); `fieldTable` rows carry `required` and list required paths
    first (folded rows: required when any item path is); the switch labels are `Required · N` and `Optional · M` with
    N = `with_problems`, M = `with_optional`.
  - Playwright `test_review_shows_required_problems_first_and_optional_ones_in_their_own_section`: a run where some
    copies miss only an optional field: the Required section (default) does not list them; the Optional section does,
    shows the Optional note and each card's reason; the status and field filters apply inside a section; deciding a
    required card updates both counts.
  - Playwright `test_the_setup_inspector_shows_the_required_reason_and_a_change_is_set_by_you`.
- [ ] **Step 2: Run to verify they fail** — `node --test tests/web/extract_model.test.js` and
  `pytest tests/demo/test_workbench_extract.py -k "required or optional" -v`.
- [ ] **Step 3: Implement.** Fill CSS must not leak (scope new selectors to the review/setup views, as P20 did). The
  By field table gets a Required column (a mark), and a row click still sets the field filter.
- [ ] **Step 4: Run** `node --test tests/web/*.test.js` and `pytest tests/demo/test_workbench_extract.py -q`. PASS.
- [ ] **Step 5: Commit** — `git commit -m "Review lists required problems first and optional ones in their own section"`

### Task 5: Fill fields carry a level: model, set_required op, browser mirror

**Files:**
- Modify: `src/formcast/template/model.py` (`Field` ~77), `src/formcast/template/op_models.py` (`SetRequired`,
  `OP_CLASSES`), `src/formcast/template/ops.py` (handler, `_HANDLERS`, exports)
- Modify: `demo/web/core/ops.js` (BROWSER_OPS, OP_NAMES order, OP_SPECS, HANDLERS, `addField` literal),
  `demo/web/engines/browser/seed.js` (`field` literal)
- Modify: `demo/tools/conformance_cases.py`; regenerate `tests/web/fixtures/ops.json` with
  `demo/tools/export_conformance.py`; regenerate any Field-dump fixtures whose tests compare exact dumps (use the
  repo's own generator scripts named in the map; never hand-edit generated files)
- Test: `tests/template/test_ops.py`, `tests/template/test_model.py`, `tests/web/ops.test.js`,
  `tests/web/test_browser_templates.py`

**Interfaces:**
- Produces:
  - `Field.required: bool = False`, `Field.required_reason: str = ""`,
    `Field.required_by: Literal["agent", "human"] | None = None`.
  - Op `{"op": "set_required", "id": str, "required": bool, "reason": str = ""}` (`SetRequired(_FieldOp)`, `id` and
    `required` positional in the sandbox like `set_label`). Sets the three attributes; `required_by` = "human" when
    the run's author is "human", else "agent".
  - Refusal: an agent `set_required` on a field whose `required_by == "human"` raises `OpError` with
    `"<id>: the person set whether this box is required; leave it as it is"`, template unchanged.
  - JS: `set_required` in BROWSER_OPS (the browser author is always human).

- [ ] **Step 1: Write the failing tests**
  - `test_set_required_sets_the_level_reason_and_author` (agent and human authors).
  - `test_an_agent_cannot_change_a_level_the_person_set` and `test_a_person_can_change_a_level_the_agent_set`.
  - `test_a_template_without_the_new_keys_loads_with_optional_defaults`.
  - Conformance cases `required/set`, `required/clear`, and an `add_field` case whose dump shows the three default
    keys; `tests/web/ops.test.js` passes them in JS.
  - Review Focus 4: in `tests/web/test_browser_templates.py` (+ its `build_templates.mjs`), a browser-made template
    with a `set_required` loads and replays in Python to an identical dump.
- [ ] **Step 2: Run to verify they fail** — `pytest tests/template -k required -v`; `node --test tests/web/ops.test.js`.
- [ ] **Step 3: Implement**, copying `SetLabel`/`_set_label` and their JS counterparts. The agent's sandbox exposes
  the op automatically (OP_CLASSES).
- [ ] **Step 4: Run** `pytest tests/template tests/web tests/agent tests/eval -q` and `node --test tests/web/*.test.js`.
  Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Fill fields carry required, its reason and who set it; set_required in Python and the browser"`

### Task 6: The fill report lists required empty boxes first

**Files:**
- Modify: `src/formcast/render/report.py` (`FillReport.required_gaps`), `src/formcast/render/assemble.py`
  (`build_report` ~198-243: required_gaps and the `required_left_empty` count)
- Modify: `demo/web/core/fill/report.js` (mirror, ~237-264), `demo/web/core/report.js` (~44 counts tile, ~115 gap lines)
- Test: `tests/render/test_report.py`, `tests/web/fill.test.js` (and `tests/web/fixtures/fill.json` if generated —
  regenerate with its generator)

**Interfaces:**
- Consumes: Task 5's `Field.required`.
- Produces: `FillReport.required_gaps: list[str] = []` (the gap ids whose field is required, in `gaps` order);
  `counts["required_left_empty"]` = its length (present only when > 0, like the warning-code counts).

- [ ] **Step 1: Write the failing tests**
  - `test_required_gaps_are_the_empty_required_boxes` (one required box with data, one required box with no data,
    one optional box with no data → `required_gaps == [the second]`, `gaps` has both empty ones).
  - Review Focus 5: `test_an_unmapped_required_box_is_a_required_gap`.
  - `test_a_template_with_no_required_boxes_reports_as_before` (`required_gaps == []`, no new count key).
  - JS: the same three through `fill.test.js` (the JS report must equal Python's for the conformance fills); and in
    `core/report.js`'s test (or panels test) the two lines' exact copy and the tile name.
- [ ] **Step 2: Run to verify they fail** — `pytest tests/render/test_report.py -k required -v`; node test.
- [ ] **Step 3: Implement.** The panel shows "Required boxes left empty: …" first (styled as a warning) and
  "Other empty boxes: …" after it; each line only when it has ids.
- [ ] **Step 4: Run** `pytest tests/render tests/web -q` and `node --test tests/web/*.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "The fill report puts required empty boxes first"`

### Task 7: The page pass and the agent classify fill boxes

**Files:**
- Modify: `src/formcast/pagepass/prompts.py` (SYSTEM_PROMPT: the rules, and the reply's `"required"` list),
  `src/formcast/pagepass/answers.py` (`mapping_ops`, `TableSpec`, `table_ops`)
- Modify: `src/formcast/agent/prompts.py` (SYSTEM_PROMPT: `set_required` exists; when to use it; never change a box
  the person set)
- Test: `tests/pagepass/test_answers.py` (or the file testing mapping_ops/table_ops), `tests/agent/test_prompts.py`

**Interfaces:**
- Consumes: Task 5's op; Task 2's `REQUIRED_RULES` (reuse the text; the page-pass prompt states it for boxes).
- Produces:
  - Reply format gains `"required": [{"id": "<box id>", "reason": "..."}]` (may be empty; ids outside the page's boxes
    are ignored like other unknown ids).
  - `mapping_ops` emits `{"op": "set_required", "id", "required": True, "reason"}` for each listed box that is not
    absorbed into a table on that page.
  - `TableSpec` gains `required_reason: str | None` (the reason of the first listed box among its rows); `table_ops`
    gives each `add_table` an explicit unique `id` and, when any merged spec has a reason, follows it with a
    `set_required` for that id.

- [ ] **Step 1: Write the failing tests**
  - `test_listed_boxes_become_required_with_their_reason`, `test_unlisted_boxes_stay_optional`,
    `test_a_listed_box_inside_a_table_makes_the_table_required_not_the_box`, `test_unknown_required_ids_are_ignored`.
  - the agent prompt names `set_required` (string check) and the MODEL_FACING guard stays green.
- [ ] **Step 2: Run to verify they fail** — `pytest tests/pagepass -k required -v`.
- [ ] **Step 3: Implement.** Keep the reply parser tolerant (a missing or malformed `"required"` is ignored).
- [ ] **Step 4: Run** `pytest tests/pagepass tests/agent -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "The page pass marks required boxes with a reason; a table is required when one of its boxes is"`

### Task 8: Fill UI: Required on the selected box

**Files:**
- Modify: `demo/web/core/inspector.js` (`#draw` ~174: a "Required" checkbox row and the reason under it, emitting
  `set_required` with reason "Set by you"), its CSS if needed
- Test: `tests/web/` inspector or panels node test; `tests/demo/test_workbench_*.py` Playwright (the Fill page)

**Interfaces:**
- Consumes: Task 5's op and Field keys; Task 6's report panel.

- [ ] **Step 1: Write the failing tests**
  - node: the inspector draws the checkbox checked for a required field, shows its reason, and emits
    `{"op": "set_required", "id", "required": false, "reason": "Set by you"}` on change.
  - Playwright `test_marking_a_box_required_puts_it_first_among_the_empty_boxes_after_a_fill`: open a demo form, mark
    a box with no data Required, fill, the report shows "Required boxes left empty: <id>" before "Other empty boxes".
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** (the checkbox pattern is the multiline checkbox in `#style`).
- [ ] **Step 4: Run** `node --test tests/web/*.test.js` and the Playwright file. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Fill: a Required checkbox on the selected box"`

### Final: whole-branch review, then the full suite

`pytest -q` (all) and `node --test tests/web/*.test.js` green before finishing.
