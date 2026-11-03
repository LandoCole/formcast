# Fill mapping triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill gets a Mapping view: what the AI matched, the form fields with no data, the data not used, and the
actions to confirm, change, remove, connect and type a value, refilling the PDF on each change with no model call.

**Architecture:** The library's `Rule` carries two optional marks (`confirmed`, `manual`). The server turns an edited
mapping into records for a stored dataset (`POST /api/data/{id}/map`). The browser keeps the mapping it shows, derives
the three lists from it with a pure module, and renders them in the left column beside the schema list.

**Tech Stack:** Python 3 (pydantic), the workbench's standard-library server, vanilla ES modules, Playwright, node:test.

**Spec:** `docs/superpowers/specs/2026-11-03-fill-mapping-triage-design.md`

## Global Constraints

- No model call for any mapping change.
- `Rule.confirmed` and `Rule.manual` are absent from `dump()` unless true; existing saved mappings load unchanged.
- The server makes the records; the browser never applies a mapping itself.
- Free text data: the view says the AI read the text and offers no lists.
- Form fields are shown by their schema titles (path secondary); only schema leaves linked to at least one box count.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit only when the user asks.

## Review Focus

- A mapping for a list path (`owners[0].name`): matched and unmatched rows use the concrete path the template links.
- A column used by a template rule (`{Street}, {City}`) counts as used.
- An edit made while the previous re-map is still on its way: the later edit wins and nothing is lost.
- A typed value for record 2, then picking record 1: record 1 fills without it.
- Saving with only confirmations changed (no new matches): the mapping is still saved.

---

### Task 1: A match's marks in the library

**Files:** Modify `src/formcast/data/mapping.py` (`Rule`), `src/formcast/data/ai.py` (`map_table`'s `check`).
Test: `tests/data/test_mapping.py`, `tests/data/test_ai.py`.

**Produces:** `Rule.confirmed: bool | None = None`, `Rule.manual: bool | None = None`.

- [ ] Tests: `test_a_rules_marks_round_trip_and_are_absent_unless_set` (a rule with `confirmed=True, manual=True`
  dumps both; a plain rule dumps neither; `apply_mapping` gives the same record either way);
  `test_the_models_rules_carry_no_marks` (a model answer with `"confirmed": true, "manual": true` on a rule yields a
  rule with both `None`).
- [ ] Implement; run `pytest tests/data -q`.

### Task 2: Re-map a dataset with an edited mapping

**Files:** Modify `demo/server/api.py` (route and handler), `demo/server/oneshot.py` (a `remap(schema, dataset,
mapping, mode)` returning `DataResult`), `demo/web/engines/server/client.js` (`data.map(id, schema, mapping, mode)`).
Test: `tests/demo/test_server_oneshot.py`.

**Consumes:** Task 1. **Produces:** `POST /api/data/{id}/map` with `{schema, mapping, mode}` answering
`DataResult.to_json` (`mode` echoed, `warnings` from `check_mapping`).

- [ ] Tests: `test_an_edited_mapping_remakes_the_records_with_no_model_call` (CSV, mapping `name ← Ref` gives
  records `{"name": "1"}`, `{"name": "2"}`; the marks are kept in the answer's mapping);
  `test_remapping_refuses_free_text_an_unknown_dataset_and_a_bad_mapping` (400 `not_a_table`, 404, 400
  `bad_mapping`); `test_a_rule_for_a_path_the_schema_lacks_is_dropped_with_a_warning`.
- [ ] Implement; run `pytest tests/demo/test_server_oneshot.py -q`.

### Task 3: The triage model

**Files:** Create `demo/web/workbench/mapping-model.js`. Test: `tests/web/mapping_model.test.js`.

**Produces:**
- `triage({mapping, schema, linked, columns, record, mode})` → `{matched: Row[], missing: Row[], unused: Col[],
  counts: {matched, confirmed, missing, unused}}`. `Row = {path, title, required, rule?, origin?, value?}`;
  `Col = {name, samples}`. `linked` is the set of concrete paths the template's boxes use. Matched and missing
  rows are required first, then schema order; a rule for a path no box uses is left out of both lists.
- Edits, each returning a new mapping: `confirm(m, path, on = true)`, `confirmAll(m, paths)`, `change(m, path,
  column)` (manual, unconfirmed, no transforms), `remove(m, path)`, `connect(m, path, column)`, `fixed(m, path,
  value)`.
- `originOf(rule, mode)`: `"you"` when manual, else `"saved"`, `"by name"` (direct) or `"AI"`.

- [ ] Tests: the three lists and counts for a two-field schema with one rule and three columns; template rules use
  their placeholders' columns; list paths; each edit; origins.
- [ ] Implement; run `node --test tests/web/mapping_model.test.js`.

### Task 4: The Mapping view

**Files:** Create `demo/web/workbench/mapping.js` (`mountMapping(app)`). Modify `index.html` (the left column's
switch and the Mapping view; the fill drawer's mapping fold becomes a line with a *Review the mapping* button),
`workbench.js` (mount it; `app.showData` opens it), `fill.js` (typed values laid over the record; `app.refill`),
`save.js` (save the mapping when edited), `workbench.css`.

**Consumes:** Tasks 2 and 3.

- [ ] The switch (*Form fields* / *Mapping*) shows only when `app.data` is a table's; mapped data opens *Mapping*.
- [ ] Counts, *Confirm all*, *Undo*; the three sections with expandable rows and their actions as the spec says.
- [ ] Each change: the new mapping on screen at once (marked as waiting), `engine.data.map`, then `app.data`
  replaced and `app.refill()`; a later change supersedes an earlier answer.
- [ ] Typed values: kept per record index; `fill.js` lays them over the record before filling; *Every record*
  turns one into `fixed`.
- [ ] Picking a form field emits `pick` with its path (the page turns to its box).

### Task 5: Browser tests and the preview

**Files:** Create `tests/demo/test_workbench_mapping.py` (a two-box PDF, a scripted model matching one of two
fields from a three-column CSV).

- [ ] `test_the_mapping_view_opens_with_what_was_matched_and_what_is_left`;
  `test_confirming_changing_connecting_and_removing_refill_the_pdf`;
  `test_a_typed_value_fills_this_record_and_every_record_makes_it_a_match`;
  `test_picking_a_form_field_selects_its_box`; `test_save_keeps_the_edited_mapping`.
- [ ] Check it in the browser preview with the scripted model off: a real CSV on a demo form.

### Task 6: All tests and review

- [ ] `node --test "tests/web/*.test.js"`; the full Python suite in the background with `-o faulthandler_timeout=300`.
- [ ] A fresh review of the whole change against the spec; fix what it confirms.
