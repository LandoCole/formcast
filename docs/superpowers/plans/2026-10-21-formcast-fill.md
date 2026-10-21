# formcast Plan 2: Fill, Smart PDF, Fill Report, Fill-Correctness Metric — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fill(template, pdf, data)` writes one data record into a PDF as real, editable form fields and returns a structured `FillReport`; `to_smart_pdf` adds empty fields; the eval harness scores what actually lands in each ground-truth box (`fill_acc`), re-run over the saved Plan 1 templates with no model calls.

**Architecture:** Fill is four pure steps plus one PDF step. (1) **Resolve** each field's value from the record (missing rules, transforms, `when`, table rows → cells). (2) **Fit** text to its box (shrink, wrap, comb, overflow). (3) **Bind** each target to a widget: a named existing widget (radio and checkbox kids picked by rect), an adopted existing widget under it, or a new one. (4) **Write** the widgets with PyMuPDF, generating appearance streams. (5) **Report** everything. The eval generates distinctive probe records from each schema, fills the template, reads the widgets back, and checks each GT box's content.

**Tech Stack:** Python ≥ 3.11, PyMuPDF (installed: 2.0 in `.venv`), pydantic 2, jsonschema, pytest. Reuse the repo `.venv` at `/Users/landonthornton/Documents/Projects/RLMayonnaise/.venv` (worktrees have none). Run tests with `<repo>/.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-10-21-formcast-fill-design.md` (the "fill design", approved 2026-10-21, with all §0 defaults accepted). Its parent is `docs/superpowers/specs/2026-10-20-form-projection-design.md` (the "main spec"). Read the fill design in full, and main spec §7.2, §7.8–7.10 and §14.3, before starting.

## Global Constraints

- **Output keeps real form fields. There is no flatten mode** (user decision). Don't add a `mode` parameter.
- **No model or API calls in any Plan 2 task.** Everything is offline. Eval re-scores saved templates only.
- **No form-specific code.** Nothing branches on a form's id, name, label text or layout. Every reviewer checks this. Per-form *data* files (`forms/probes/<id>.json`, GT `value` labels) are allowed. Per-form *code* is not.
- Coordinates everywhere: PDF points, origin top-left, unrotated crop-box space, `(x0, y0, x1, y1)`. PyMuPDF widget rects use the same space (verified in Plan 1; the `preview` helper `_to_pixels` shows the rotation handling).
- **Never silent:** a value present in the record that doesn't end up visible in a widget must produce a report warning (fill design §2 anti-goals).
- **Missing** = absent key, `None`, `""`, whitespace-only string, or empty list. `0`, `0.0` and `False` are values, not missing.
- **Over-long text:** keep the full value in the field, at `min_font_size`, and report `overflow`. Cut only where a max length forces it (comb, `MaxLen`), reported as `truncated`.
- **Validation:** lenient by default (`invalid` warnings), `strict=True` raises `FormcastError`.
- Widgets the template doesn't use are left byte-for-byte untouched (value, appearance, flags).
- The Plan 1 held-out freeze (tag `plan1-heldout-frozen`) pins agent code only. Don't change `src/formcast/agent/` or prompts in this plan.
- Secrets: none are needed. Don't load `.env`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the implementing model's name, per Plan 1 ruling).

## Review Focus

1. **`0`, `0.0` and `False` are values, not missing.** A record with `{"count": 0, "agree": False}` renders "0" in the text field and leaves the agree box *off without* a `missing` warning. Pinned in Task 2 (`test_zero_and_false_are_values`).
2. **Refilling a filled PDF.** `fill(template, fill(template, blank, rec_a)[0], rec_b)` passes the fingerprint check and leaves no trace of `rec_a`, including fields that `rec_b` lacks, which are cleared. Pinned in Task 6 (`test_refill_clears_previous_values`).
3. **Rotated and cropped pages.** A field on a `/Rotate 90` page, and on a page whose cropbox is offset from its mediabox, gets its widget at the template rect (read back with PyMuPDF to within 0.5 pt). Pinned in Task 5 (`test_created_widget_rect_on_rotated_page`, `test_created_widget_rect_with_offset_cropbox`).
4. **Two template fields bound to one shared text field** (PDF text widgets with the same name share one value, as on the I-9). Writing different text to both mustn't silently keep only the last: report `shared_field_conflict`. Pinned in Task 6 (`test_shared_text_field_conflict_reported`).
5. **Malformed table data**: rows that are `None`, scalars where objects are expected, or a non-list where a list is expected. No crash. The bad rows are skipped with `invalid` warnings, and good rows still fill. Pinned in Task 2 (`test_malformed_rows_reported_not_raised`).

---

## File Structure

```
src/formcast/
  schema.py                 # + validate_data_located(); validate_data uses it
  transforms.py             # + public to_text() (the existing _text, renamed with a docstring)
  template/model.py         # Style validation (finite, > 0, min <= size); WIDGET_FIELD_TYPES table
  template/ops.py           # OpContext.widget_kinds; set_type/link_widget kind check
  render/
    __init__.py             # unchanged exports + fill, to_smart_pdf, FillReport
    report.py               # NEW: FillWarning, FieldReport, CellReport, TableReport, PathReport, FillReport, SEVERITY
    values.py               # NEW: lookup, is_missing, resolve(): fields -> Resolved, tables -> cells
    fit.py                  # NEW: fit_text(), unencodable_chars()
    binding.py              # NEW: ExistingWidget, Target, Binding, bind()
    write.py                # NEW: read_existing(), write_text(), write_choice(), write_choice_mark()
    fill.py                 # NEW: fill(), to_smart_pdf()
    preview.py              # + data= keyword
  __init__.py               # lazy exports fill, to_smart_pdf, FillReport
eval/
  score.py                  # GTField.value; load_gt reads it
  probe.py                  # NEW: probe_records()
  gt_choice_values.py       # NEW: drafts GT choice `value` labels
  fill_score.py             # NEW: score_fill()
  agent_eval.py             # fill column in rows/totals/markdown; --rescore and --seed-only compute it
forms/probes/<id>.json      # NEW: per-form probe overrides (only for paths the generator can't satisfy)
forms/gt/*.json             # + "value" on choice entries
tests/render/conftest.py    # NEW: make_pdf() synthetic PDF builder
tests/render/test_values.py, test_fit.py, test_binding.py, test_write.py, test_fill.py, test_report.py
tests/template/test_model.py, test_ops.py   # additions
tests/eval/test_probe.py, test_fill_score.py, test_gt_files.py  # additions
docs/superpowers/specs/2026-10-20-form-projection-design.md     # §8 sketch fix, §7.8/7.9 pointer, §14.3 fill results
README.md                   # fill usage
```

---

### Task 1: Template and ops hardening (Plan 1 parked items)

**Files:**
- Modify: `src/formcast/template/model.py` (`Style`, new `WIDGET_FIELD_TYPES`)
- Modify: `src/formcast/template/ops.py` (`OpContext`, `_set_type`, `_link_widget`)
- Modify: `docs/superpowers/specs/2026-10-20-form-projection-design.md` §8 and §7.8/§7.9
- Test: `tests/template/test_model.py`, `tests/template/test_ops.py`

**Interfaces:**
- Produces: `WIDGET_FIELD_TYPES: dict[str, frozenset[str]]` in `formcast.template.model`, mapping each widget kind to the field types it can back: `text → {text, signature}`, `combo → {text}`, `list → {text}`, `checkbox → {checkbox, radio_option}`, `radio → {radio_option, checkbox}`, `signature → {signature}`, `button → {}`. Task 4 uses it for `type_mismatch` and adoption.
- Produces: `OpContext.widget_kinds: dict[str, list[str]] | None` (widget name → kinds of every widget with that name). `from_template(template, form_map)` fills it from `form_map`.

- [ ] **Step 1: Write failing tests**

```python
# tests/template/test_model.py
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -3])
def test_style_rejects_bad_font_size(bad):
    with pytest.raises(pydantic.ValidationError):
        Style(font_size=bad)
    with pytest.raises(pydantic.ValidationError):
        Style(min_font_size=bad)

def test_style_rejects_min_above_size():
    with pytest.raises(pydantic.ValidationError):
        Style(font_size=8, min_font_size=9)
    assert Style(font_size="auto", min_font_size=9).min_font_size == 9

def test_template_with_nan_style_does_not_load():
    d = json.loads(_template_with_one_text_field().to_json())
    d["fields"][0]["style"]["font_size"] = float("nan")
    with pytest.raises(pydantic.ValidationError):
        Template.model_validate(d)

# tests/template/test_ops.py
def test_set_style_nan_rejected():
    with pytest.raises((OpError, pydantic.ValidationError)):
        apply(t, {"op": "set_style", "id": "f1", "style": {"font_size": float("nan")}}, author="agent", context=CTX)

def test_set_type_rejects_kind_mismatch_with_widget_kinds():
    ctx = CTX.model_copy(update={"widget_kinds": {"rg": ["radio"]}})
    # field f1 is radio_option linked to "rg"
    with pytest.raises(OpError, match="radio"):
        apply(t_radio, SetType(id="f1", type="text"), author="human", context=ctx)
    apply(t_radio, SetType(id="f1", type="checkbox"), author="human", context=ctx)  # allowed

def test_link_widget_rejects_kind_mismatch():
    ctx = CTX.model_copy(update={"widget_names": ["sig"], "widget_kinds": {"sig": ["signature"]}})
    with pytest.raises(OpError):
        apply(t_checkbox, LinkWidget(id="f1", widget="sig"), author="human", context=ctx)

def test_set_type_without_widget_kinds_still_allowed():
    apply(t_radio, SetType(id="f1", type="text"), author="human", context=CTX)
```

- [ ] **Step 2: Run them and confirm they fail.** `.venv/bin/python -m pytest tests/template -k "style or kind or nan" -v` → FAIL.
- [ ] **Step 3: Implement.** In `Style`, constrain `font_size` (when numeric) and `min_font_size` to finite values `> 0` (`pydantic.Field(gt=0, allow_inf_nan=False)` or a validator), plus a model validator for `min_font_size <= font_size` when `font_size` is numeric. Add `WIDGET_FIELD_TYPES`. Add `widget_kinds` to `OpContext` and fill it in `from_template`. In `_set_type` (after the existing checks) and `_link_widget`, when `f.existing_widget` and `ctx.widget_kinds` know the name: allowed types are the union of `WIDGET_FIELD_TYPES[k]` over its kinds; otherwise `run.fail(f"field {id!r} is linked to widget {name!r} ({kinds}); a {type} field can't use it — allowed: {...}")`.
- [ ] **Step 4: Fix the main spec.**
  - §8: `result = build_template(schema, "intake.pdf", llm)`, then `tpl = result.template`, and `pdf_bytes, report = fill(tpl, "intake.pdf", record)` (no `mode`). Replace `revise(...)`'s assignment the same way if it returns `SessionResult` (check `formcast/agent/api.py`).
  - §7.8/§7.9: one line each: "Superseded by `docs/superpowers/specs/2026-10-21-formcast-fill-design.md` (fields-only output, no flatten mode)." Leave `preview` as is.
- [ ] **Step 5: Run `tests/template` and the full offline suite.** `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 6: Commit** `fix(template): reject NaN/non-positive font sizes; check widget kind on set_type/link_widget; spec §8 sketch`.

---

### Task 2: Fill report model and value resolution

**Files:**
- Create: `src/formcast/render/report.py`, `src/formcast/render/values.py`
- Modify: `src/formcast/schema.py` (add `validate_data_located`), `src/formcast/transforms.py` (public `to_text`)
- Test: `tests/render/test_report.py`, `tests/render/test_values.py`

**Interfaces:**
- Consumes: `Template`, `Field`, `Source`, `TableSegment` (model); `apply_chain`, `Transform` (transforms); `parse_path`, `flatten_schema`, `SchemaPath` (schema).
- Produces (`formcast.render.report`):
  - `Severity = Literal["warning", "info"]`.
  - `WarningCode = Literal["missing", "invalid", "transform_error", "unmapped_data", "shrunk", "overflow", "truncated", "unencodable", "rows_dropped", "widget_not_found", "type_mismatch", "not_writable", "not_an_option", "shared_field_conflict", "adopted", "font_substituted", "list_joined", "unknown_keys", "fingerprint_mismatch"]`.
  - `SEVERITY: dict[WarningCode, Severity]`. Info: `shrunk, adopted, font_substituted, list_joined, unknown_keys`. Every other code is a warning.
  - `FillWarning(BaseModel)`: `code: WarningCode, severity: Severity, message: str, path: str | None = None, field: str | None = None, page: int | None = None, rect: Rect | None = None`, and a classmethod `make(code, message, **kw)` that fills `severity` from `SEVERITY`.
  - `FieldReport`: `id, type, page: int | None, paths: list[str], binding: Literal["named","adopted","created","none"] = "none", widget: str | None = None, widget_rect: Rect | None = None, value: str | bool | None = None, font_size: float | None = None, warnings: list[FillWarning] = []`.
  - `CellReport`: `row: int, key: str, segment: int, page: int, rect: Rect`, plus the same `binding, widget, widget_rect, value, font_size, warnings` as `FieldReport`.
  - `TableReport`: `id, path, rows_provided: int, rows_written: int, rows_dropped: int, rows_per_segment: list[int], cells: list[CellReport], warnings: list[FillWarning] = []`.
  - `PathReport`: `path, mapped: bool, has_value: bool, fields: list[str], rendered: dict[str, str | bool | None], warnings: list[FillWarning]`. Table column cells are keyed `"<table id>[<row>]"` in `rendered`.
  - `FillReport`: `template_id, fingerprint_ok: bool, strict: bool, fields: list[FieldReport], tables: list[TableReport], paths: dict[str, PathReport], gaps: list[str], warnings: list[FillWarning], counts: dict[str, int]`, with `to_json()` and `from_json()` like `Template`. `counts` holds `fields_written`, `fields_empty`, `cells_written`, and one entry per warning code present.
- Produces (`formcast.render.values`):
  - `is_missing(v) -> bool` (per Global Constraints) and `lookup(data, path: str) -> Any`, which follows `parse_path` segments through dicts and returns `None` for anything absent. A table column path isn't looked up this way.
  - `@dataclass Resolved`: `field_id: str, kind: Literal["text", "choice"], paths: list[str], has_value: bool, text: str | None, on: bool | None, warnings: list[FillWarning]`.
  - `@dataclass CellTarget`: `table_id, segment: int, row: int, key: str, page: int, rect: Rect, text: str | None, warnings: list[FillWarning]`.
  - `@dataclass TableResolved`: `field_id, path, rows_provided: int, cells: list[CellTarget], rows_per_segment: list[int], rows_dropped: int, warnings: list[FillWarning]`.
  - `resolve(template, data: dict) -> tuple[dict[str, Resolved], dict[str, TableResolved], list[FillWarning]]`. The last element is the record-level warnings: `invalid` per located validation error mapped to its schema path, `unmapped_data` per schema path that has a value but no mapped field, and `unknown_keys`. Unmapped template fields get no `Resolved` entry.
  - `schema_path_of(location: str) -> str`: `"medications[2].dose"` → `"medications[].dose"`.
- Produces (`formcast.schema`): `validate_data_located(schema, data) -> list[tuple[str, str]]` (location, message). `validate_data` becomes a thin wrapper with unchanged output.
- Produces (`formcast.transforms`): `to_text(value) -> str` (the existing `_text`, made public; keep `_text` as an alias).

Value rules (fill design §5.2, §5.6):
- **Text field:** gather one value per source path (missing → `None`), `apply_chain(values, transforms)`, then `source.join` if set. A list result renders `", ".join(to_text)` with `list_joined`. A `None` or missing result means `text=None` with a `missing` warning. A `FormcastError` means `text=None` with a `transform_error` warning carrying the message.
- **Choice field:** `x = apply_chain(values, transforms)`; `on = bool(apply_chain([x], [when]))` when `when` is set, else `bool_to_check(x)`. All source values missing → `on=False` with a `missing` warning (on the field, `has_value=False`).
- **Table:** rows = `lookup(data, source.paths[0])`. Missing or empty → no cells and a `missing` warning. Not a list → an `invalid` warning and no cells. A row that is `None`, or a scalar where the schema's items are objects → that row's cells are left empty with one `invalid` warning per bad row, and it still takes a row slot (row indexes stay aligned with the data). Cell value = `row[key]` (object rows), or `row[int(key)]` (list rows, where `key` is the index as a string), passed through the column transforms. A missing cell gives `text=None` with **no** warning. Rows flow through segments in order. Segment k holds `max_rows` rows at `row_top + r*row_height`. The cell rect is `(col.x[0], y, col.x[1], y + row_height)`. Leftover rows count in `rows_dropped`, with one `rows_dropped` warning on the table.

- [ ] **Step 1: Write failing tests** (`tests/render/test_values.py`, schema like `tests/template/test_ops.py::SCHEMA` plus `count: integer`, `agree: boolean`, `dob: string/date`):

```python
def test_missing_values():  # absent, None, "", "  ", []
    for v in (None, "", "   ", []):
        assert is_missing(v)
    assert lookup({"a": {"b": 1}}, "a.b") == 1 and lookup({}, "a.b") is None

def test_zero_and_false_are_values():
    fields, _, _ = resolve(tpl_count_agree, {"count": 0, "agree": False})
    assert fields["f_count"].text == "0" and not fields["f_count"].warnings
    assert fields["f_agree"].on is False and fields["f_agree"].has_value and not fields["f_agree"].warnings

def test_empty_string_is_missing_for_text_and_choice():
    fields, _, _ = resolve(tpl_name_married, {"name": "", "married": ""})
    assert fields["f_name"].text is None and [w.code for w in fields["f_name"].warnings] == ["missing"]
    assert fields["f_married"].on is False

def test_multiselect_is_one_list_value():  # contains("email") on ["email","phone"]
    fields, _, _ = resolve(tpl_contact_boxes, {"contact": ["email", "phone"]})
    assert fields["f_email"].on and fields["f_phone"].on and not fields["f_fax"].on

def test_unjoined_list_on_text_field_joins_with_comma():
    fields, _, _ = resolve(tpl_contact_text, {"contact": ["email", "phone"]})
    assert fields["f_contact"].text == "email, phone"
    assert [w.code for w in fields["f_contact"].warnings] == ["list_joined"]

def test_join_two_paths_skips_missing():  # first + last with join " "
    assert resolve(tpl_fullname, {"first": "Ada", "last": ""})[0]["f_full"].text == "Ada"

def test_transform_error_reported_not_raised():
    fields, _, _ = resolve(tpl_dob_formatted, {"dob": "next Tuesday"})
    assert fields["f_dob"].text is None and fields["f_dob"].warnings[0].code == "transform_error"

def test_table_flows_across_segments_and_drops_rows():
    # segment0 max_rows=2 (page 0), segment1 max_rows=1 (page 1); 4 rows
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": [{"drug": f"D{i}"} for i in range(4)]})
    t = tables["t_meds"]
    assert (t.rows_provided, t.rows_per_segment, t.rows_dropped) == (4, [2, 1], 1)
    assert [(c.page, c.row, c.text) for c in t.cells if c.key == "drug"] == [(0, 0, "D0"), (0, 1, "D1"), (1, 2, "D2")]
    assert t.cells[0].rect == (72.0, 200.0, 250.0, 218.0)  # x from column, y from row_top/row_height
    assert [w.code for w in t.warnings] == ["rows_dropped"]

def test_malformed_rows_reported_not_raised():
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": [None, "x", {"drug": "ok"}]})
    t = tables["t_meds"]
    assert [c.text for c in t.cells if c.key == "drug"] == [None, None, "ok"]
    assert sum(w.code == "invalid" for w in t.warnings) == 2
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": "oops"})
    assert tables["t_meds"].cells == [] and tables["t_meds"].warnings[0].code == "invalid"

def test_record_level_warnings():
    _, _, rec = resolve(tpl_name_only, {"name": "A", "count": "seven", "address": {"city": "X"}, "zzz": 1})
    codes = {(w.code, w.path) for w in rec}
    assert ("invalid", "count") in codes and ("unmapped_data", "address.city") in codes
    assert any(w.code == "unknown_keys" for w in rec)

def test_schema_path_of():
    assert schema_path_of("medications[2].dose") == "medications[].dose"
```

`tests/render/test_report.py`: `FillWarning.make("overflow", "x").severity == "warning"`; `make("shrunk", ...)` is `"info"`; `SEVERITY` covers every `WarningCode`; `FillReport.from_json(r.to_json()) == r`.

- [ ] **Step 2: Run them and confirm they fail.** `.venv/bin/python -m pytest tests/render/test_values.py tests/render/test_report.py -v` → FAIL (import errors).
- [ ] **Step 3: Implement** `report.py`, `values.py`, `validate_data_located` and `to_text`, to the interfaces and rules above.
- [ ] **Step 4: Run the tests and the full suite.** All pass. `tests/test_schema.py` must still pass unchanged.
- [ ] **Step 5: Commit** `feat(render): fill report model and value resolution (missing rules, tables -> cells)`.

---

### Task 3: Text fitting

**Files:**
- Create: `src/formcast/render/fit.py`
- Test: `tests/render/test_fit.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Fit`: `text: str` (what goes in the field: the full value, or cut at `max_len`), `font_size: float`, `lines: int`, `status: Literal["ok", "shrunk", "overflow"]`, `truncated: bool`, `unencodable: str` (the distinct offending characters, in order of first appearance).
  - `fit_text(text, rect, *, font_size: float | Literal["auto"], min_font_size: float, fontname: str = "helv", multiline: bool = False, max_lines: int | None = None, max_len: int | None = None, comb: int | None = None) -> Fit`.
  - `unencodable_chars(text) -> str`: characters that can't be encoded in `cp1252`.
  - Constants: `AUTO_MAX_SIZE = 12.0`, `SHRINK_STEP = 0.5`, `PAD = 2.0` (horizontal padding per side), `LINE_HEIGHT = 1.15` (× font size).

Rules (fill design §5.5):
- **Start size:** a numeric `font_size`, or for `"auto"`, `min(AUTO_MAX_SIZE, (rect height − 2) / LINE_HEIGHT)`. Never start below `min_font_size`.
- **Single line:** shrink by `SHRINK_STEP` while `pymupdf.get_text_length(text, fontname, size) > width − 2*PAD` and `size > min_font_size`. `status="shrunk"` if the final size is below the start, and `"overflow"` if it still doesn't fit at `min_font_size`, in which case the full text is kept.
- **Multi-line** (`multiline=True`, or `max_lines` given): greedy word wrap at the width, breaking an over-long word by characters. Shrink until `lines * size * LINE_HEIGHT <= height − 2` and, if set, `lines <= max_lines`. Otherwise `"overflow"`.
- **Comb** (`comb=n`): `max_len = n`. The size fits one character in `width / n` and the height.
- **`max_len`:** a longer text is cut to `max_len` characters, with `truncated=True`. This is applied before fitting.

- [ ] **Step 1: Write failing tests**

```python
R = (0, 0, 100, 14)
def test_short_text_auto_fits_at_start_size():
    f = fit_text("Ada", R, font_size="auto", min_font_size=6)
    assert f.status == "ok" and f.font_size == pytest.approx(min(12.0, 12 / 1.15), abs=0.01)

def test_long_text_shrinks():
    f = fit_text("Alexandra Wilhelmina Featherstonehaugh", R, font_size="auto", min_font_size=4)
    assert f.status == "shrunk" and 4 <= f.font_size < 10
    assert pymupdf.get_text_length(f.text, "helv", f.font_size) <= 96

def test_overflow_keeps_full_text():
    s = "x" * 200
    f = fit_text(s, R, font_size="auto", min_font_size=6)
    assert f.status == "overflow" and f.text == s and f.font_size == 6 and not f.truncated

def test_numeric_font_size_is_start():
    assert fit_text("Ada", R, font_size=8, min_font_size=6).font_size == 8

def test_multiline_wraps_then_shrinks():
    f = fit_text("word " * 40, (0, 0, 100, 60), font_size="auto", min_font_size=4, multiline=True)
    assert f.lines > 1 and f.status in ("ok", "shrunk")

def test_max_lines_limits_wrap():
    f = fit_text("word " * 40, (0, 0, 100, 60), font_size="auto", min_font_size=6, max_lines=2)
    assert f.lines <= 2 or f.status == "overflow"

def test_comb_truncates_to_n():
    f = fit_text("1234567890", (0, 0, 90, 14), font_size="auto", min_font_size=6, comb=9)
    assert f.text == "123456789" and f.truncated

def test_unencodable():
    assert unencodable_chars("Nguyễn 王") == "ễ王"
    assert fit_text("Nguyễn", R, font_size="auto", min_font_size=6).unencodable == "ễ"
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `fit.py`.**
- [ ] **Step 4: Run them and confirm they pass.**
- [ ] **Step 5: Commit** `feat(render): text fitting (shrink, wrap, comb, overflow, unencodable)`.

---

### Task 4: Widget binding

Pure logic over widget descriptors. No PDF in this task's tests.

**Files:**
- Create: `src/formcast/render/binding.py`
- Test: `tests/render/test_binding.py`

**Interfaces:**
- Consumes: `WIDGET_FIELD_TYPES` (Task 1), `FillWarning` (Task 2), `iou`, `area` (geometry).
- Produces:
  - `@dataclass(frozen=True) ExistingWidget`: `page: int, index: int` (position in `page.widgets()` order), `xref: int, name: str, kind: str` (pagemap kinds: text, checkbox, radio, combo, list, signature, button), `rect: Rect, on_state: str | None, max_len: int | None, options: tuple[str, ...]`.
  - `@dataclass(frozen=True) Target`: `key: str` (the field id, or `f"{table_id}[{row}].{col_key}"` for a cell), `field_id: str, field_type: str` (template type; cells are `"text"`), `page: int, rect: Rect, existing_widget: str | None`.
  - `@dataclass Binding`: `key: str, how: Literal["named", "adopted", "created", "none"], widget: ExistingWidget | None, new_name: str | None, warnings: list[FillWarning]`.
  - `bind(targets: list[Target], existing: list[ExistingWidget]) -> dict[str, Binding]`.
  - `cell_key(table_id, row, col_key) -> str` and `widget_name_for(key) -> str` (`"t_meds[2].drug"` → `"t_meds_r2_drug"`, field ids unchanged).

Rules (fill design §5.3), in this order:
1. **Named:** targets with `existing_widget` take the widgets on `target.page` with that name. With exactly one, use it. With several, use the one with the highest IoU with `target.rect` (ties broken by centre distance). If none has IoU > 0, or there's no same-named widget on that page, the result is `how="none"` with `widget_not_found`. If the widget kind doesn't back the field type (`WIDGET_FIELD_TYPES`), the result is `how="none"` with `type_mismatch`.
2. **Adopted:** for each still-unbound target without `existing_widget` (except `choice_mark`, which is always created), consider existing widgets that (a) no named binding claimed, (b) no earlier adoption claimed, (c) are backed for the target's type per `WIDGET_FIELD_TYPES`, and (d) have their centre inside `target.rect`. Pick the highest IoU, giving `how="adopted"` with an info `adopted` warning naming the widget. Process targets in page, top, left order so the result is deterministic.
3. **Created:** everything else gets `how="created"`, `new_name=widget_name_for(key)`, with `_2`, `_3`… appended while the name collides with any existing widget name or an earlier new name.
- A bound widget of kind `signature` gets the `not_writable` warning: "the form's own signature field can only be signed in a PDF viewer; value not written" (fill design §0 item 4 extension, my call: a /Sig field can't hold typed text). Record this as a ruling.

- [ ] **Step 1: Write failing tests**

```python
def test_named_single_widget_used_regardless_of_rect()
def test_radio_kid_picked_by_rect():
    kids = [ExistingWidget(0, 0, 10, "group_1", "radio", (475, 76, 486, 83), "Yes_5", None, ()),
            ExistingWidget(0, 1, 11, "group_1", "radio", (505, 76, 516, 83), "No", None, ())]
    b = bind([Target("f_no", "f_no", "radio_option", 0, (504, 75, 517, 84), "group_1")], kids)
    assert b["f_no"].how == "named" and b["f_no"].widget.on_state == "No"
def test_named_widget_not_found_when_no_kid_overlaps()      # warning code widget_not_found
def test_named_widget_on_other_page_not_found()
def test_type_mismatch():  # checkbox field named to a text widget -> how none, type_mismatch
def test_adopts_widget_under_table_cell():
    # cell 24pt tall, widget 12pt inside it -> adopted, info warning "adopted"
def test_adoption_skips_claimed_and_incompatible_widgets()
def test_choice_mark_never_adopts()
def test_created_names_unique():
    # existing widget named "f1" -> target f1 is created as "f1_2"; cells -> "t_meds_r0_drug"
def test_signature_widget_not_writable()
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `binding.py`.**
- [ ] **Step 4: Run them and confirm they pass.**
- [ ] **Step 5: Commit** `feat(render): bind fields to named, adopted or new widgets`.

---

### Task 5: Writing widgets with PyMuPDF (spike first)

**Files:**
- Create: `src/formcast/render/write.py`, `tests/render/conftest.py`
- Test: `tests/render/test_write.py`

**Interfaces:**
- Consumes: `ExistingWidget`, `Binding` (Task 4); `Fit` (Task 3); `Style` (model).
- Produces:
  - `read_existing(doc: pymupdf.Document) -> list[ExistingWidget]`, which reads every widget (page, index, xref, name, kind via the same mapping as `pagemap.extract._extract_widgets`, rect, `on_state()` for buttons, `text_maxlen or None`, choice values).
  - `write_text(page, binding, *, text: str | None, fit: Fit | None, style: Style) -> tuple[str, Rect, list[FillWarning]]`, which returns the widget name, its rect as read back, and warnings (`font_substituted`, `not_an_option`). `text=None` clears the widget. A created widget is a text widget at the target rect with `text_font="Helv"`, `text_fontsize=fit.font_size`, quadding from `style.align` (left 0 / center 1 / right 2), the multiline flag when `fit.lines > 1` or `style.multiline`, and the comb flag plus `text_maxlen` when `style.comb`. For a named or adopted combo/list widget whose value isn't in its options, it adds `not_an_option` (value still written).
  - `write_choice(page, binding, *, on: bool, style_circle: bool) -> tuple[str, Rect, list[FillWarning]]`. Existing widgets are set to `widget.on_state()` or `"Off"`. Created widgets are checkboxes, circle style when `style_circle` (used for `radio_option`) if PyMuPDF supports it, else the default check (ruling).
  - `write_choice_mark(page, binding, *, on: bool) -> tuple[str, Rect, list[FillWarning]]`. This creates a checkbox widget over the option rect whose "on" appearance is a stroked ellipse inset 1 pt in the rect, and whose "off" appearance is empty.
- Produces (test helper, `tests/render/conftest.py`): `make_pdf(*, pages=1, rotate=0, cropbox=None, widgets=(), text=()) -> bytes`. `widgets` entries are dicts `{page, type: "text"|"checkbox"|"radio"|"combo"|"signature", name, rect, on_state?, options?, maxlen?}`. Radio kids sharing a name are built so that `read_existing` sees the group with distinct on-states. If PyMuPDF can't create radio kids correctly, which the planning spike showed it can't (on-states came out as "False"/"True"), build radio groups with low-level xref edits in the helper, or use `forms/dev/menomonie_app.pdf` (radio `group_1`, on-states `Yes_5`/`No`) in a `slow`-free test.

- [ ] **Step 1: Spike (throwaway, scratchpad, no commit).** Verify against the installed PyMuPDF:
  - (a) a created text widget keeps `text_fontsize`, quadding, the multiline and comb flags and `text_maxlen` after `doc.tobytes()` and reopen.
  - (b) setting an existing radio kid to its `on_state()` turns its siblings off. The planning spike on menomonie `group_1` showed `[('Off','Yes_5'), ('No','No')]` after setting the second kid.
  - (c) what `update()` does to existing widgets whose DA font isn't base-14 (`HelveticaLTStd-Bold` on f8949/fw4/fw9, `Arial` on ds11): kept, or replaced by Helv? That decides when to emit `font_substituted`.
  - (d) whether a custom `/AP /N` dict (on: ellipse stream, `/Off`: empty) on a checkbox survives save and reopen when written *after* the last `update()`, and whether PyMuPDF regenerates it on reopen.
  - (e) whether the checkbox circle style is settable.

  Write the findings, one line each, as rulings in `docs/superpowers/plans/2026-10-21-formcast-fill-rulings.md`. If (d) fails, `write_choice_mark` falls back to a `pymupdf` circle annotation (`page.add_circle_annot(inset rect)`, drawn only when on) and returns the annot's rect, and the binding stays `"created"` with the name recorded as `None`. Record this fallback as a ruling, because Task 9's scorer then reads circle annots for `choice_mark`.
- [ ] **Step 2: Write failing tests**

```python
def test_created_text_widget_round_trip(make_pdf):  # value, size, font, quadding, comb + maxlen survive reopen
def test_clear_named_text_widget(make_pdf)          # text=None -> field_value "" after reopen
def test_existing_checkbox_uses_its_on_state(make_pdf)  # on_state "Yes_5" not "Yes"
def test_radio_kid_on_turns_sibling_off()            # menomonie_app.pdf group_1, via read_existing + bind
def test_choice_mark_on_and_off_appearance(make_pdf)  # on: widget/annot present with ellipse; off: nothing visible (render pixels in rect ~white)
def test_created_widget_rect_on_rotated_page(make_pdf):
    pdf = make_pdf(rotate=90)
    # created at template rect (100, 200, 250, 216) -> read back within 0.5 pt
def test_created_widget_rect_with_offset_cropbox(make_pdf)  # cropbox (36, 36, 576, 756)
def test_combo_value_not_an_option(make_pdf)
def test_untouched_widgets_unchanged(make_pdf):
    # a widget no binding refers to keeps identical xref object source (doc.xref_object) after writes
```

- [ ] **Step 3: Run them and confirm they fail.**
- [ ] **Step 4: Implement `write.py` and `conftest.make_pdf`** per the spike's rulings. Generate appearances via `widget.update()` for every touched widget. Don't set `NeedAppearances`.
- [ ] **Step 5: Run them and confirm they pass.**
- [ ] **Step 6: Commit** `feat(render): write text, checkbox and circle-mark widgets; rulings from PyMuPDF spike`.

---

### Task 6: `fill`, `to_smart_pdf`, `preview(data=)` and public API

**Files:**
- Create: `src/formcast/render/fill.py`
- Modify: `src/formcast/render/preview.py`, `src/formcast/render/__init__.py`, `src/formcast/__init__.py`, `README.md`
- Test: `tests/render/test_fill.py`, `tests/test_public_api.py`, `tests/render/test_preview.py`

**Interfaces:**
- Consumes: `resolve` (Task 2), `fit_text` (Task 3), `bind`/`Target`/`cell_key` (Task 4), `read_existing`/`write_*` (Task 5), `fingerprint_document` (`formcast.match`), `open_pdf`, `validate_data_located`.
- Produces:
  - `fill(template: Template, pdf: str | Path | bytes, data: dict, *, check_fingerprint: bool = True, strict: bool = False) -> tuple[bytes, FillReport]`.
  - `to_smart_pdf(template, pdf, *, check_fingerprint: bool = True) -> bytes`.
  - `preview(template, pdf, *, data: dict | None = None, dpi=110, show_candidates=None)`. With `data`, it renders `fill(template, pdf, data, check_fingerprint=False)[0]` under the boxes.
  - `formcast.fill`, `formcast.to_smart_pdf`, `formcast.FillReport` (lazy exports, added to `__all__`).

Orchestration (fill design §4):
1. `open_pdf`. Raise `FormcastError("page count mismatch: template has N pages, PDF has M")` on mismatch.
2. Fingerprint: on a mismatch, raise `FormcastError("fingerprint mismatch ...")` when `check_fingerprint`, else add the warning `fingerprint_mismatch` and `fingerprint_ok=False`.
3. With `strict` and any located validation error, raise `FormcastError("data does not match the schema: " + first 5 messages joined by "; ")`.
4. `resolve`.
5. Build `Target`s for every field with `page`/`rect` (mapped or not, so `to_smart_pdf` covers unmapped fields too) and every table cell slot (all `max_rows` × columns per segment, for `to_smart_pdf`). For `fill`, also include cells beyond the data rows: they're bound and created but left empty.
6. `bind`.
7. Write. **`fill`**: every bound target: text targets get `fit_text` with `Style` (cells use the table field's style; `max_lines = column.wrap`) and `fontname` from the widget font per the Task 5 ruling, plus `max_len` from an existing widget. Choice targets go through `write_choice`/`write_choice_mark`. Unmapped fields with a named or adopted widget are **not touched**. Unmapped created fields are created empty. **`to_smart_pdf`**: only `how == "created"` targets are written (empty). Named and adopted widgets are left untouched.
8. `shared_field_conflict`: after writing, group text targets by bound widget name. If two targets wrote different non-empty text to one name, add the warning to the later one (page/top order), naming both fields.
9. Assemble `FillReport`:
   - `fields`/`tables` from resolution, binding and fit, with `shrunk`/`overflow`/`truncated`/`unencodable` warnings from the `Fit`.
   - `paths`: every path from `flatten_schema`, with `rendered` per field or cell.
   - `gaps`: ids of fields with no source plus mapped fields or tables that wrote nothing.
   - `warnings`: every warning, flattened and sorted by (page, y0, x0), with `missing` collapsed to one per path.
   - `counts`.
10. Return `doc.tobytes(garbage=3, deflate=True)`.

- [ ] **Step 1: Write failing tests** (synthetic PDFs from `make_pdf`, templates built in the test with `Field`/`TableSegment`; fingerprint from `formcast.match.fingerprint` of the same bytes):

```python
def test_fill_flat_form_end_to_end(make_pdf):
    out, rep = fill(tpl, pdf, {"name": "Ada Lovelace", "married": True, "status": "married",
                               "contact": ["email"], "medications": [{"drug": "A", "dose": "1"}]})
    values = {w.field_name: w.field_value for p in pymupdf.open(stream=out) for w in p.widgets()}
    assert values["f_name"] == "Ada Lovelace" and values["t_meds_r0_drug"] == "A"
    assert rep.paths["name"].rendered == {"f_name": "Ada Lovelace"} and rep.fingerprint_ok

def test_fill_fillable_sets_named_widgets_and_keeps_others()   # unused widget xref object identical
def test_fingerprint_mismatch_raises_unless_overridden()
def test_page_count_mismatch_raises()
def test_strict_raises_on_invalid_data()
def test_lenient_reports_invalid_and_fills_rest()
def test_unmapped_data_reported()
def test_rows_dropped_top_level_warning()
def test_overflow_kept_whole_and_reported()
def test_refill_clears_previous_values(make_pdf):
    first, _ = fill(tpl, pdf, {"name": "Ada", "married": True})
    second, rep = fill(tpl, first, {"married": False})
    values = {w.field_name: w.field_value for p in pymupdf.open(stream=second) for w in p.widgets()}
    assert values["f_name"] in ("", None) and values["f_married"] == "Off"
def test_shared_text_field_conflict_reported(make_pdf)   # two text widgets named "dup"; fields f1/f2 named to them
def test_table_over_fillable_cells_adopts_widgets(make_pdf)  # no new widgets created; report binding "adopted"
def test_to_smart_pdf_adds_empty_fields_only(make_pdf)  # created widgets empty incl. every table cell slot; existing untouched
def test_warnings_sorted_and_missing_collapsed()
@pytest.mark.slow
def test_corpus_fill_smoke():  # forms/dev/menomonie_app.pdf: new_template(schema, build_form_map(pdf)) with group_1 kids mapped -> fill runs, radio kid on, no exceptions
```

`tests/test_public_api.py`: `formcast.fill`, `formcast.to_smart_pdf` and `formcast.FillReport` resolve and appear in `__all__`. `tests/render/test_preview.py`: `preview(tpl, pdf, data=rec)` returns one PNG per page, and it differs from the no-data preview.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement** `fill.py`, the `preview` keyword and the exports.
- [ ] **Step 4: README:** add a "Filling" section with `fill`/`to_smart_pdf` usage, a sample of the report's warnings list, the no-flatten note (flatten outside formcast, for example by printing to PDF) and the Latin-only font note.
- [ ] **Step 5: Run the full offline suite.** All pass.
- [ ] **Step 6: Commit** `feat: fill() and to_smart_pdf() with FillReport; preview(data=)`.

---

### Task 7: Probe records

**Files:**
- Create: `eval/probe.py`, `forms/probes/<id>.json` (only for forms that need overrides)
- Test: `tests/eval/test_probe.py`

**Interfaces:**
- Consumes: `flatten_schema`, `SchemaPath`, `validate_data`; `score.GTField`, `score.load_gt`.
- Produces:
  - `probe_records(schema: dict, gt: list[GTField], *, overrides: dict | None = None, seed: str = "") -> list[dict]`
  - `table_rows_in_gt(gt) -> dict[str, int]` (table path → number of GT rows, per Task 9's row ordering)
  - `load_overrides(form_id) -> dict | None` (reads `forms/probes/<id>.json`: `{"<path>": [value_for_record_0, value_for_record_1, ...] or value}`)
  - `TOKEN_LEN = 6`

Rules (fill design §6.1):
- **K** = max(2, largest option count among enum paths and multi-select item enums).
- **Strings:** uppercase tokens of `TOKEN_LEN` letters from a RNG seeded with `seed`, where no 3-letter substring repeats across any two tokens of the K records, honouring `maxLength` (shorter tokens if needed).
- **`format: date`:** distinct ISO dates. Each date path gets its own day (1–28), month and year (1950–2020), distinct from every other date path where possible, and different per record.
- **Numbers/integers:** distinct values in `[minimum, maximum]`, at least 3 digits when the range allows.
- **Booleans:** `k % 2 == 0`.
- **Enums:** option `k mod n`.
- **Multi-select:** options `{k mod n, (k+1) mod n}`.
- **Other formats** (`email`, …): a token-based conforming value (`token.lower()@probe.test`).
- **`pattern`:** take the override, or fail loudly with `ValueError("<form>: no probe value for pattern path <path>; add forms/probes/<id>.json")`.
- **Tables:** `table_rows_in_gt[path] + 1` rows, capped at `maxItems`, every cell a fresh token or value per its column type.
- Every record must pass `validate_data`. Otherwise raise with the messages.

- [ ] **Step 1: Write failing tests**

```python
def test_k_covers_largest_enum():  # enum of 5 -> 5 records, every option appears once
def test_tokens_have_unique_trigrams():  # across all string values of all records
def test_dates_distinct_parts():
def test_table_rows_gt_plus_one_capped_by_maxitems()
def test_records_validate_for_every_corpus_schema():
    for form in manifest_forms():  # all 17, dev + heldout; schemas from forms/schemas
        for rec in probe_records(schema, load_gt(form, pdf), overrides=load_overrides(form["id"]), seed=form["id"]):
            assert validate_data(schema, rec) == []
def test_pattern_without_override_raises()
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `probe.py`,** then add the override files for whichever corpus `pattern` paths the corpus test reports (17 pattern keywords exist across `forms/schemas`). Override values must keep the "distinctive" property: different digits per record.
- [ ] **Step 4: Run them and confirm they pass.**
- [ ] **Step 5: Commit** `eval: probe records from schemas (+ per-form pattern overrides)`.

---

### Task 8: GT choice `value` labels

**Files:**
- Modify: `eval/score.py` (`GTField.value: Any = None`; `_gt_from_entry` reads `"value"`; module docstring documents it)
- Create: `eval/gt_choice_values.py`
- Modify: `forms/gt/*.json` (all 17, held-out included; user-approved 2026-10-21, labels only)
- Test: `tests/eval/test_gt_files.py`, `tests/eval/test_score.py`

**Interfaces:**
- Produces: `GTField.value`, the option a choice box stands for, or a boolean for yes/no box pairs on a boolean path. Absent on `bool_to_check`-style boxes for boolean paths (implied `true`).
- Produces: `python eval/gt_choice_values.py --form <id> [--write-draft]`. For each choice GT entry with a path and no `value`, it proposes a value by matching (case- and punctuation-insensitive token overlap) the nearest label text (the entry's `label`, else the text line nearest the box from `build_form_map` words) and, on fillable forms, the widget's on-state name, against the path's enum or item-enum options, and `true`/`false` for boolean paths. It writes `forms/gt/drafts/<id>.values.json` with `{entry key: {"proposed": v, "evidence": "..."}}` for review. The script is generic and has no per-form branches.

- [ ] **Step 1: Write failing tests**

```python
# tests/eval/test_gt_files.py
def test_choice_entries_have_valid_value():
    for form, g in all_gt_entries():           # dev + heldout
        if g.type != "choice" or not g.path:
            continue
        sp = schema_path(form, g.path)
        if sp.type == "boolean":
            assert g.value in (None, True, False)
        else:
            assert g.value is not None, (form, g.label)
            assert g.value in options_of(sp)    # enum, or item enum for multi-select
# tests/eval/test_score.py
def test_load_gt_reads_value()                 # fields entry and widget_paths entry
```

- [ ] **Step 2: Run them and confirm `test_choice_entries_have_valid_value` fails** (no labels yet).
- [ ] **Step 3: Implement the `GTField.value` plumbing and `gt_choice_values.py`.** Run it for every form.
- [ ] **Step 4: Label.** Dispatch three labeling subagents (dev forms split in two, plus held-out). Each reviews its forms' drafts against the PDF text and the schema options, writes the confirmed `value` into `forms/gt/<id>.json`, and lists any box it can't decide, which the controller rules on. Held-out labels are written once and never used to change code or prompts. Delete `forms/gt/drafts/` afterwards (git-ignored if kept locally).
- [ ] **Step 5: Run `tests/eval`.** All pass.
- [ ] **Step 6: Commit** `eval: GT choice-box value labels (dev + heldout) and drafting tool`.

---

### Task 9: `fill_acc` scorer and harness integration

**Files:**
- Create: `eval/fill_score.py`
- Modify: `eval/agent_eval.py` (`score_row`, `totals`, `render_markdown`, `rescore_main`, `seed_main`)
- Test: `tests/eval/test_fill_score.py`, `tests/eval/test_agent_eval.py`

**Interfaces:**
- Consumes: `fill`, `FillReport` (Task 6); `probe_records`, `load_overrides` (Task 7); `GTField.value` (Task 8); `score._greedy_match`, `score.multiline_flags`/`_multiline_cover` (reuse; don't copy); `agent_eval.fake_scan_pdf`.
- Produces:
  - `@dataclass Observed`: `page: int, rect: Rect, name: str | None, text: str | None, on: bool | None, kind: Literal["text", "choice"]`.
  - `read_written(pdf_bytes: bytes, report: FillReport) -> list[Observed]`: only widgets that the report says a template field or cell wrote (by name and rect), with values read from the PDF; plus `choice_mark` circle annots if Task 5 ruled the annotation fallback.
  - `gt_rows(gt: list[GTField]) -> dict[int, int]`: GT index → row index for table-column boxes. Group by column path, order by page, then y0, then x0. Boxes whose tops are within 2 pt share a row.
  - `score_fill(gt, template, pdf: str | Path | bytes, records: list[dict]) -> dict` returning `{"units", "correct", "fill_acc", "text": {"units", "correct"}, "choice": {...}, "cell": {...}, "rows_dropped_ok": bool | None, "fill_errors": [str]}`. It runs `fill(..., check_fingerprint=False)` per record. An exception from `fill` makes every unit of that record wrong, with the message in `fill_errors`.
  - Row field `row["fill"]` (that dict) and totals `fill_acc` per group, micro-averaged over units. Markdown gets a `fill` column after `joint`.

Correctness per unit (fill design §6.3–6.5):
- **Text box with no GT transforms:** `norm(observed) == norm(expected)`, where `norm` = casefold and collapse whitespace. For joins (`also_paths`), the multiset of tokens equals the union, in any order.
- **Text box with GT transforms:** non-empty. Every alphanumeric token of the observed text is either a substring of the path's value (casefold) or, for dates, equal to the day, month or year (with or without zero padding, 2- or 4-digit year) of the path's date. No token of length ≥ 3 is a substring of another path's value in the same record.
- **Choice box:** one unit across all records; correct iff for every record `observed.on == expected_on`. `expected_on` = record value `== g.value` (enum or boolean), `g.value in` value (multi-select), or `bool(value)` (boolean with no `value`).
- **Table cell box:** row index `i = gt_rows[idx]`. Expected = row i's value for the column. Records with fewer rows expect empty; an empty expected and an empty observed count as correct.
- **Matching:** observed widgets are matched to GT boxes per record with `_greedy_match` at IoU ≥ 0.3 (same page), then the multi-line rule for unmatched text GT boxes. Unmatched GT box → wrong.
- `rows_dropped_ok`: for every table whose probe rows exceed GT rows, the report's `rows_dropped` equals the excess. `None` when no table had an excess.

- [ ] **Step 1: Write failing tests** (synthetic: a generated one-page PDF with text lines, GT boxes built in the test, schema with a string, a date split into three boxes, an enum with 3 options, a 3-row × 2-column table):

```python
def test_correct_template_scores_one()
def test_seeded_template_scores_zero()          # fields without paths; also no choice box earns credit
def test_always_on_checkbox_scores_zero_for_that_box()
def test_table_per_column_loophole_caught():
    # one segment per column, rows flow down column 1 then column 2
    assert score.score_mapping(gt, loophole_tpl)["path"] == 1.0
    assert score_fill(gt, loophole_tpl, pdf, recs)["cell"]["correct"] < score_fill(gt, good_tpl, pdf, recs)["cell"]["correct"]
def test_date_part_provenance_accepts_any_format()
def test_wrong_path_token_fails_provenance()
def test_rows_dropped_ok()
def test_fill_exception_counts_record_wrong()
# tests/eval/test_agent_eval.py
def test_totals_include_fill_acc()
def test_markdown_has_fill_column()
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `fill_score.py`.** Wire `score_row` to compute `row["fill"]` using the variant's PDF: the original for `as_published`, `fake_scan_pdf(form_id, original)` for `fake_scan` (cached per form within a run). Wire `totals` and `render_markdown`. `--rescore` and `--seed-only` compute it too. Add a NOTES line explaining `fill`.
- [ ] **Step 4: Run them and confirm they pass,** then run the full offline suite.
- [ ] **Step 5: Commit** `eval: fill_acc metric (probe fills scored per GT box), closes the table-per-column loophole`.

---

### Task 10: Re-score the archive and write up results

**Files:**
- Modify: `eval/results/agent_*.json` and `.md` (rescored in place), `docs/superpowers/specs/2026-10-20-form-projection-design.md` §14.3, `docs/superpowers/plans/2026-10-21-formcast-fill-rulings.md`

- [ ] **Step 1: Extract the templates.** `tar -xzf eval/results/templates.tar.gz` from the repo root (the files are git-ignored).
- [ ] **Step 2: Re-score every committed results file** (no model calls). Run, from the repo root:

```bash
for f in eval/results/agent_*_dev_*.json; do .venv/bin/python eval/agent_eval.py --rescore "$f"; done
for f in eval/results/agent_*_heldout_*.json; do .venv/bin/python eval/agent_eval.py --rescore "$f" --baseline-stamp 2026-10-21_heldout; done
```

  Expected: each prints `re-scored N of M rows`, and the md files gain a `fill` column. Then regenerate the seed rows: `--seed-only --split dev --variant both --tag seed-fill`, and the same for heldout with `--baseline-stamp 2026-10-21_heldout`. Seed `fill_acc` must be 0.00 (the sanity check from fill design §6.6). If it isn't, stop and debug.
- [ ] **Step 3: Write it up.** In main spec §14.3, add a "Fill correctness (Plan 2)" subsection:
  - a table of `fill_acc` (text / choice / cell / all) next to `path` and `joint` for each group: Haiku round3-gen, Luna rounds 1–2, held-out Haiku (completed sessions only), seed
  - the per-form gap between `path` and `fill_acc` on table-heavy forms (f8949, ics211, grandview_app, i9, cms1500), which is how much the table loophole inflated mapping scores
  - top `fill_errors` and top warning codes from the reports
  - the corresponding "Known scoring caveats" bullet updated from "only a fill-correctness metric catches it" to the measured result
- [ ] **Step 4: Viewer check (manual, by the user; the controller asks once).** The controller writes three files to `eval/results/viewer_check/` (git-ignored): a filled fillable form (`menomonie_app`), a filled flat form (`wadoc_auth`) and a smart PDF (`ndguard`). The user opens them in macOS Preview and one other viewer, checks that values show, fields are editable, circle marks look right, and print-to-PDF keeps the values. Record the result, or "not checked", in the rulings file.
- [ ] **Step 5: Run the whole offline suite one last time.** `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 6: Commit** `eval: fill_acc over saved Plan 1 templates; §14.3 fill results`.

---

## Self-review notes

- **Spec coverage:**
  - fill design §3 API → Task 6
  - §4 pipeline → Task 6
  - §5.1 types → Tasks 4–5
  - §5.2 values → Task 2
  - §5.3 binding → Task 4, and Task 6 for shared-field conflicts
  - §5.4 validation → Tasks 2 and 6
  - §5.5 fitting → Task 3
  - §5.6 tables → Tasks 2 and 6
  - §5.7 report → Tasks 2 and 6
  - §5.8 preview → Task 6
  - §6 metric → Tasks 7–10
  - §7 parked items → Task 1 (NaN, §8, set_type), Task 4 (radio kids), Task 2 (multi-select, `""`)
  - §10 testing → each task, plus the Task 10 viewer check
- **Additions beyond the fill design, as rulings for the controller:**
  - `not_writable` for the form's own signature widgets
  - `not_an_option` for combo values
  - `shared_field_conflict`
  These follow the fill design's "never silent" anti-goal.
