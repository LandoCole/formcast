# Category and Title Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An extraction field's name is derived from its Category and Title, so people and the AI give a readable title and an optional category, and nobody writes a schema name.

**Architecture:** A new module, `formcast.extract.names`, turns titles into keys and rewrites a schema so its keys match its titles. The AI's schema goes through it during setup, and `apply_edit` uses it when a person changes a Title or Category. A flag on `SetupInfo` says whether the Name still follows the Title; saving clears it. The document's own export columns get a `document_` prefix so a flat field called `name` or `file` no longer clashes.

**Tech Stack:** Python 3.12, pydantic, the workbench's plain JavaScript modules, pytest with Playwright, node:test.

**Spec:** this plan's Decisions section. It comes from the conversation of 2026-10-30, and there is no separate spec document.

## Decisions (the person's)

- The name is `category.title`. Category is optional, and flat data is preferred.
- The Name follows the Title until the template is saved, then it freezes. Changing a frozen Name is a deliberate action.
- The document's export columns are renamed: `name` becomes `document_name`, and the others follow.
- Extraction only. Fill does not change.
- Destination templates keep their names. Existing saved templates stay as they are.

## Global Constraints

- A key is lower-case snake_case, made from the title: accents dropped, every run of other characters becomes `_`, ends trimmed. "City, state, ZIP" gives `city_state_zip`, and "Date of birth" gives `date_of_birth`.
- A key that starts with a digit gets `f_` in front: "1099 box" gives `f_1099_box`.
- Document columns, in order: `document_id`, `document_name` (when names are given), `document_file`, `document_template`, `document_match`, `document_extracted_at`.
- Set-aside columns: `document_id`, `document_name` (when names are given), `document_file`, `reason`, `closest_template`, `closest_score`.
- `RESERVED_NAMES` remains the single list of those six names. A top-level field still cannot use one.
- Saved run results are unchanged on disk. `DocumentResult.doc_id` keeps its name, and only exported columns change.
- Fill's one-shot prompt (`DERIVE_SYSTEM`) and every Fill path stay byte-identical.
- Copy for a frozen name: the button says "Update name". The hint says "Changing the Name changes the export column."

## Review Focus

- A Title with nothing a key can be made from ("#", "名前"): the person's edit is refused with a plain message, and the AI's field keeps the key it was given.
- Two AI fields with the same title in one category: the second gets `_2`, and neither is lost.
- A Title or Category change on a required field: it keeps its level, reason, readers, samples' readings and line.
- A Title change on a repeated group's field: every item moves, and the selection stays on the same item.
- Reopening a saved template: Title edits leave the Name alone, and "Update name" appears only when the two differ.

---

### Task 1: Document columns

**Files:**
- Modify: `src/formcast/extract/model.py`, `src/formcast/extract/batch.py`, `src/formcast/extract/export.py`, `demo/server/extraction.py`, `demo/server/extract_api.py`
- Test: `tests/extract/test_export.py`, `tests/extract/test_batch.py`, `tests/demo/test_server_extract.py`, `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Produces: `_DOC_COLUMNS = ("document_id", "document_file", "document_template", "document_match", "document_extracted_at")`, `NAME_COLUMN = "document_name"`, `RESERVED_NAMES`.

- [ ] **Step 1:** Update the export tests to the new columns, and add `test_a_field_called_name_or_file_has_its_own_column`: a top-level field `name` and one called `file` both export beside `document_name` and `document_file`.
- [ ] **Step 2:** Run `pytest tests/extract/test_export.py -q`. Expected: FAIL on the old column names.
- [ ] **Step 3:** Rename the constants, the `Results._rows` and `_aside_row` keys, `_with_names`, and the server's uses of `row["doc_id"]` and `row["file"]`. `Results.rows()` now uses the new keys.
- [ ] **Step 4:** Change the clash tests to a field called `document_name`, and run the export, batch and server tests. Expected: PASS.

### Task 2: Names from titles

**Files:**
- Create: `src/formcast/extract/names.py`
- Modify: `src/formcast/extract/edit.py`, `src/formcast/extract/model.py`
- Test: `tests/extract/test_names.py`, `tests/extract/test_edit.py`

**Interfaces:**
- Produces: `field_key(text: str) -> str` (`""` when nothing is left); `derived_path(category: str, title: str) -> str`; `normalized(schema: dict) -> tuple[dict, dict[str, str]]`, the schema with keys derived from titles and the map of old generic path to new; `SetupInfo.name_follows_title: bool = False`; `SetCategory(path, category)`.

- [ ] **Step 1:** Write `test_names.py`: the Global Constraints examples, `field_key("#") == ""`, `field_key("Prénom") == "prenom"`, `derived_path("Investor", "Taxpayer ID") == "investor.taxpayer_id"`, `derived_path("", "Taxpayer ID") == "taxpayer_id"`.
- [ ] **Step 2:** Add normalizing tests: nesting deeper than one level is flattened into the top-level category, a title of "Date" twice gives `date` and `date_2`, a field with no title keeps its key, an object's `required` list follows its renamed fields, and a repeated group keeps its `items` and `maxItems`.
- [ ] **Step 3:** Add edit tests, with a template whose `setup.name_follows_title` is true:
  - `SetField(title=...)` moves the path, with level, reader, absent entry, reading and line.
  - A blank title, or one with no key, is refused.
  - A reserved or taken name is refused.
  - On a repeated group every item moves.
  - With the flag false, the Title changes and the path does not.
  - `SetCategory` moves a plain field to a category, creating it, and `""` moves it to the top level.
  - An emptied category goes, and a repeated group's field is refused.
- [ ] **Step 4:** Run `pytest tests/extract/test_names.py tests/extract/test_edit.py -q`. Expected: FAIL.
- [ ] **Step 5:** Implement them. `_rename_field` keeps its checks and becomes the one way a key changes. `_set_field` calls it when the flag is on. `_set_category` shares a path-rewrite helper with it.
- [ ] **Step 6:** Run the same tests. Expected: PASS.

### Task 3: The AI gives titles and categories

**Files:**
- Modify: `src/formcast/extract/prompts.py`, `src/formcast/data/ai.py`, `src/formcast/extract/setup.py`
- Test: `tests/extract/test_setup.py`, `tests/extract/test_ask.py`, `tests/data/test_ai.py`

**Interfaces:**
- Consumes: `normalized`.

- [ ] **Step 1:** Add tests: the AI and lines starts give keys derived from titles and map `line_paths`; `add_line` gives new fields derived keys, and a clash with an existing field gets `_2`; a destination start keeps every path and sets `name_follows_title` false; AI and lines starts set it true; the prompts ask for at most one level of objects and prefer flat fields.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Change the rule lines in `FIELDS_SYSTEM`, and add `flat_fields: bool = False` to `derive_schema`, adding its rule only when true. Call `normalized` in `_fields` for the AI and lines starts, and in `add_line`.
- [ ] **Step 4:** Run the extract and data tests. Expected: PASS.

### Task 4: Saving freezes the Name

**Files:**
- Modify: `demo/server/extract_api.py`, `demo/server/extraction.py`
- Test: `tests/demo/test_server_extract.py`

**Interfaces:**
- Produces: on the Setup summary, `renamed: {old path: new path}` from the last edit, and `name_updates: {path: derived path}` for frozen fields whose Name differs from their Category and Title.

- [ ] **Step 1:** Add tests: a Title edit returns `renamed`; a save stores `name_follows_title` false; a reopened setup's Title edit leaves the path and lists the field in `name_updates`; a `rename_field` to that name clears it.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement them. The save also clears the flag on the open setup's template, so edits after a save are frozen too.
- [ ] **Step 4:** Run `pytest tests/demo/test_server_extract.py -q`. Expected: PASS.

### Task 5: The setup panel

**Files:**
- Modify: `demo/web/workbench/extract-setup.js`, `demo/web/workbench/extract.css`
- Test: `tests/demo/test_workbench_extract.py`

**Interfaces:**
- Consumes: `renamed`, `name_updates`, `rename_field`, `set_category`.

- [ ] **Step 1:** Replace the rename tests with: Title and Category boxes above a read-only Name; a Title edit changes the Name and keeps the field selected, on the same item of a repeated group; a Category edit moves the field in the list; a refused Title puts the box back and says why; a repeated group's Category box is read-only; a reopened template shows "Update name" only when the Name differs, and the button updates it.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Build it. `change` accepts `select` as a function of the returned setup, so the selection can follow `renamed`. Category uses a `datalist` of the template's categories.
- [ ] **Step 4:** Run `pytest tests/demo/test_workbench_extract.py -q`. Expected: PASS.

### Task 6: Check it all

- [ ] **Step 1:** Run the full Python suite and `node --test tests/web/*.test.js`. Expected: PASS, apart from anything already known as flaky.
- [ ] **Step 2:** Run one fresh reviewer over the whole change on the most capable model, and fix what it finds.
- [ ] **Step 3:** Update the module docstrings that describe names and columns.
