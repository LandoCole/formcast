import math

import pydantic
import pytest

from formcast.errors import OpError
from formcast.pagemap.build import build_form_map
from formcast.schema import flatten_schema
from formcast.template.model import Field, FormInfo, Template, linked_cells, new_template, table_cells
from formcast.template.ops import AddField, LinkWidget, Move, OpContext, SetType, apply, diff, replay

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "married": {"type": "boolean"},
        "status": {"enum": ["single", "married"]},
        "contact": {"type": "array", "items": {"enum": ["email", "phone"]}},
        "address": {"type": "object", "properties": {"city": {"type": "string"}}},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"drug": {"type": "string"}, "dose": {"type": "string"}},
            },
        },
    },
}
SIZES = [(612.0, 792.0), (612.0, 792.0)]
CTX = OpContext(schema_paths=flatten_schema(SCHEMA), page_sizes=SIZES)

SEGMENT = {
    "page": 0,
    "rect": [72, 300, 540, 500],
    "row_top": 320,
    "row_height": 18,
    "max_rows": 10,
    "columns": [{"key": "drug", "x": [72, 300]}, {"key": "dose", "x": [300, 540]}],
}


def _template(fields=()):
    return Template(
        template_id="t1",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=2, page_sizes=SIZES, kinds=["digital"] * 2),
        fields=list(fields),
    )


def _add(t, author="agent", **kw):
    op = {"op": "add_field", "type": "text", "page": 0, "rect": [100, 100, 200, 120], **kw}
    return apply(t, op, author=author, context=CTX)


def _field(t, fid):
    return next(f for f in t.fields if f.id == fid)


def _rejects(t, op, *needles):
    with pytest.raises(OpError) as exc:
        apply(t, op, author="agent", context=CTX)
    for needle in needles:
        assert needle in exc.value.reason, exc.value.reason
    return exc.value


# ------------------------------------------------------------------ basics


def test_add_then_move_shifts_rect_and_records_history():
    t1, _ = apply(
        _template(),
        AddField(type="text", page=0, rect=(100, 100, 200, 120)),
        author="human",
        note="first",
        context=CTX,
    )
    t2, warnings = apply(t1, Move(id="f1", dx=5), author="human", context=CTX)
    assert warnings == []
    assert _field(t2, "f1").rect == (105, 100, 205, 120)
    assert [h.author for h in t2.history] == ["human", "human"]
    assert [h.op["op"] for h in t2.history] == ["add_field", "move"]
    assert t2.history[0].note == "first"
    assert t2.history[0].op["id"] == "f1"  # generated id is recorded for replay
    assert t2.history[1].at.endswith("+00:00")


def test_apply_does_not_mutate_input():
    t0 = _template()
    t1, _ = _add(t0)
    apply(t1, {"op": "move", "id": "f1", "dy": 3}, author="agent", context=CTX)
    assert t0.fields == [] and t0.history == []
    assert _field(t1, "f1").rect == (100, 100, 200, 120) and len(t1.history) == 1


def test_plain_dict_op_works():
    t, warnings = _add(_template(), label="Name", source={"paths": ["name"]})
    f = _field(t, "f1")
    assert (f.type, f.label, f.source.paths, f.origin) == ("text", "Name", ["name"], "agent")
    assert warnings == []


def test_generated_id_follows_max_numeric_suffix():
    existing = Field(id="f7", type="text", page=0, rect=(0, 0, 10, 10))
    other = Field(id="name_box", type="text", page=1, rect=(0, 0, 10, 10))
    t, _ = _add(_template([existing, other]))
    assert t.fields[-1].id == "f8"


def test_explicit_id_accepted_and_duplicate_rejected():
    t, _ = _add(_template(), id="applicant")
    assert t.fields[0].id == "applicant"
    _rejects(t, {"op": "add_field", "id": "applicant", "type": "text", "page": 0, "rect": [0, 0, 50, 50]},
             "duplicate id 'applicant'")
    _rejects(t, {"op": "add_field", "id": "9bad", "type": "text", "page": 0, "rect": [0, 0, 50, 50]},
             "'9bad'")


def test_unknown_id_rejected():
    _rejects(_template(), {"op": "set_label", "id": "f9", "label": "x"}, "unknown field id 'f9'")


def test_unknown_op_and_bad_args_are_op_errors():
    _rejects(_template(), {"op": "mvoe", "id": "f1"}, "unknown op 'mvoe'", "move")
    _rejects(_template(), {"op": "move", "id": "f1", "rect": [1, 2]}, "rect", "[x0, y0, x1, y1]")
    _rejects(_template(), {"op": "set_label", "id": "f1", "label": "x", "colour": 1}, "colour")


def test_move_requires_rect_or_offset():
    t, _ = _add(_template())
    _rejects(t, {"op": "move", "id": "f1"}, "rect", "dx")
    _rejects(t, {"op": "move", "id": "f1", "rect": [0, 0, 9, 9], "dx": 1}, "not both")


# ------------------------------------------------------------------ geometry


def test_move_off_page_raises():
    t, _ = _add(_template())
    err = _rejects(t, {"op": "move", "id": "f1", "dx": 500}, "off page", "f1", "612")
    assert err.op == {"op": "move", "id": "f1", "dx": 500}


def test_rect_within_two_points_of_edge_is_allowed():
    t, _ = _add(_template(), rect=[-1.5, 0, 50, 793.5])
    assert t.fields[0].rect == (-1.5, 0, 50, 793.5)


def test_degenerate_rect_and_bad_page_raise():
    _rejects(_template(), {"op": "add_field", "type": "text", "page": 0, "rect": [10, 10, 11, 40]},
             "degenerate")
    _rejects(_template(), {"op": "add_field", "type": "text", "page": 2, "rect": [10, 10, 50, 40]},
             "page 2", "0..1")


def test_resize_sets_rect():
    t, _ = _add(_template())
    t, _ = apply(t, {"op": "resize", "id": "f1", "rect": [100, 100, 300, 140]}, author="agent", context=CTX)
    assert _field(t, "f1").rect == (100, 100, 300, 140)


def test_overlap_is_a_warning_not_an_error():
    t, _ = _add(_template())
    t, warnings = _add(t, rect=[105, 100, 205, 120])
    assert len(t.fields) == 2
    assert len(warnings) == 1 and "f2" in warnings[0] and "f1" in warnings[0] and "overlap" in warnings[0]
    _, other_page = _add(t, page=1)
    assert other_page == []


# ------------------------------------------------------------------ sources


def test_set_source_unknown_path_raises():
    t, _ = _add(_template())
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["nmae"]}},
             "unknown schema path 'nmae'", "name")


def test_set_source_and_unmap():
    t, _ = _add(_template())
    t, _ = apply(t, {"op": "set_source", "id": "f1", "source": {"paths": ["name"], "transforms": [{"op": "upper"}]}},
                 author="agent", context=CTX)
    assert _field(t, "f1").source.transforms[0].op == "upper"
    t, _ = apply(t, {"op": "set_source", "id": "f1", "source": None}, author="agent", context=CTX)
    assert _field(t, "f1").source is None


def test_container_and_column_paths_rejected_on_plain_fields():
    t, _ = _add(_template())
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["address"]}}, "object")
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["medications"]}}, "add_table")
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["medications[].dose"]}}, "medications")


def test_when_on_non_choice_field_raises():
    t, _ = _add(_template())
    _rejects(t, {"op": "set_source", "id": "f1",
                 "source": {"paths": ["status"], "when": {"op": "equals", "args": {"value": "single"}}}},
             "when", "text")


def test_set_type_away_from_choice_with_when_raises():
    t, _ = _add(_template(), type="checkbox", rect=[100, 100, 110, 110],
                source={"paths": ["status"], "when": {"op": "equals", "args": {"value": "single"}}})
    _rejects(t, {"op": "set_type", "id": "f1", "type": "text"}, "when")


def test_invalid_transform_chain_raises():
    t, _ = _add(_template())
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["name"], "transforms": [{"op": "shout"}]}},
             "unknown transform 'shout'")
    _rejects(t, {"op": "set_source", "id": "f1",
                 "source": {"paths": ["name"], "transforms": [{"op": "char_at", "args": {}}]}},
             "char_at")


def test_checkbox_source_type_warning():
    t, _ = _add(_template(), type="checkbox", rect=[100, 100, 110, 110])
    _, warnings = apply(t, {"op": "set_source", "id": "f1", "source": {"paths": ["name"]}},
                        author="agent", context=CTX)
    assert len(warnings) == 1 and "checkbox" in warnings[0] and "'name'" in warnings[0]
    for source in (
        {"paths": ["married"]},
        {"paths": ["contact"], "when": {"op": "contains", "args": {"value": "email"}}},
        {"paths": ["name"], "transforms": [{"op": "bool_to_check"}]},
        {"paths": ["name"], "when": {"op": "equals", "args": {"value": "x"}}},
    ):
        _, warnings = apply(t, {"op": "set_source", "id": "f1", "source": source}, author="agent", context=CTX)
        assert warnings == [], source


def test_set_type_to_table_raises():
    t, _ = _add(_template())
    _rejects(t, {"op": "set_type", "id": "f1", "type": "table"}, "add_table")
    _rejects(t, {"op": "add_field", "type": "table", "page": 0, "rect": [0, 0, 50, 50]}, "add_table")


def test_set_type_label_style():
    t, _ = _add(_template())
    t, _ = apply(t, {"op": "set_type", "id": "f1", "type": "signature"}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "set_label", "id": "f1", "label": "Sign here"}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "set_style", "id": "f1", "style": {"align": "center"}}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "set_style", "id": "f1", "style": {"font_size": 9}}, author="agent", context=CTX)
    f = _field(t, "f1")
    assert (f.type, f.label, f.style.align, f.style.font_size) == ("signature", "Sign here", "center", 9)


def test_remove_field():
    t, _ = _add(_template())
    t, _ = apply(t, {"op": "remove_field", "id": "f1"}, author="agent", context=CTX)
    assert t.fields == []
    _rejects(t, {"op": "remove_field", "id": "f1"}, "unknown field id")


# ------------------------------------------------------------------ tables


def _table(t=None):
    op = {"op": "add_table", "label": "Meds", "source_path": "medications", "segments": [SEGMENT]}
    return apply(t or _template(), op, author="agent", context=CTX)[0]


def test_add_table():
    t = _table()
    f = t.fields[0]
    assert (f.id, f.type, f.source.paths, f.page, f.rect) == ("f1", "table", ["medications"], None, None)
    assert [c.key for c in f.segments[0].columns] == ["drug", "dose"]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_table_rows_and_columns_are_rejected(bad):
    rows = {**SEGMENT, "row_top": bad}
    _rejects(_template(), {"op": "add_table", "source_path": "medications", "segments": [rows]},
             "row_top", repr(bad))
    cols = {**SEGMENT, "columns": [{"key": "drug", "x": [72, bad]}, {"key": "dose", "x": [300, 540]}]}
    _rejects(_template(), {"op": "add_table", "source_path": "medications", "segments": [cols]},
             "column 'drug'", repr(bad))
    for key in ("row_top", "row_height"):
        op = {"op": "set_table_rows", "id": "f1", "segment": 0, "row_top": 320, "row_height": 18, "max_rows": 10}
        _rejects(_table(), {**op, key: bad}, key, repr(bad), "non-finite")
    t = _table()
    assert Template.from_json(t.to_json()) == t


def test_add_table_on_string_path_raises():
    _rejects(_template(), {"op": "add_table", "source_path": "name", "segments": [SEGMENT]},
             "'name'", "string", "array")
    _rejects(_template(), {"op": "add_table", "source_path": "contact", "segments": [SEGMENT]},
             "contact[].drug")


def test_table_column_key_must_resolve():
    bad = {**SEGMENT, "columns": [{"key": "dosage", "x": [72, 300]}]}
    _rejects(_template(), {"op": "add_table", "source_path": "medications", "segments": [bad]},
             "medications[].dosage", "dose")


def test_table_rows_must_fit_segment():
    bad = {**SEGMENT, "max_rows": 20}
    _rejects(_template(), {"op": "add_table", "source_path": "medications", "segments": [bad]},
             "max_rows", "10")


def test_set_table_columns_rows_and_segment():
    t = _table()
    t, _ = apply(t, {"op": "set_table_columns", "id": "f1", "segment": 0,
                     "columns": [{"key": "drug", "x": [72, 540]}]}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "set_table_rows", "id": "f1", "segment": 0,
                     "row_top": 310, "row_height": 20, "max_rows": 9}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "add_table_segment", "id": "f1", "segment": {**SEGMENT, "page": 1}},
                 author="agent", context=CTX)
    f = t.fields[0]
    assert [c.key for c in f.segments[0].columns] == ["drug"]
    assert (f.segments[0].row_top, f.segments[0].row_height, f.segments[0].max_rows) == (310, 20, 9)
    assert [s.page for s in f.segments] == [0, 1]
    _rejects(t, {"op": "set_table_rows", "id": "f1", "segment": 2, "row_top": 310, "row_height": 20,
                 "max_rows": 9}, "segment 2", "0..1")


def test_table_ops_on_non_table_rejected():
    t, _ = _add(_table())
    _rejects(t, {"op": "set_table_columns", "id": "f2", "segment": 0, "columns": []}, "f2", "not a table")
    _rejects(t, {"op": "move", "id": "f2", "dx": 1, "segment": 0}, "f2", "segment=0", "table")
    _rejects(t, {"op": "resize", "id": "f2", "rect": [0, 0, 50, 50], "segment": 0}, "f2", "table")


def _two_segment_table():
    t = _table()
    return apply(t, {"op": "add_table_segment", "id": "f1", "segment": {**SEGMENT, "page": 1}},
                 author="agent", context=CTX)[0]


def test_move_table_by_offset_shifts_segments_rows_and_columns():
    t, _ = apply(_two_segment_table(), {"op": "move", "id": "f1", "dx": 10, "dy": -5}, author="human", context=CTX)
    for seg in t.fields[0].segments:
        assert seg.rect == (82, 295, 550, 495) and seg.row_top == 315
        assert [c.x for c in seg.columns] == [(82, 310), (310, 550)]
    t, _ = apply(_two_segment_table(), {"op": "move", "id": "f1", "dy": 4, "segment": 1}, author="human", context=CTX)
    assert [s.row_top for s in t.fields[0].segments] == [320, 324]
    _rejects(_table(), {"op": "move", "id": "f1", "dy": 400}, "segment 0 rect", "off page")


def test_move_table_segment_to_rect_carries_rows_and_columns():
    t, _ = apply(_two_segment_table(), {"op": "move", "id": "f1", "rect": [82, 310, 550, 510], "segment": 1},
                 author="human", context=CTX)
    first, second = t.fields[0].segments
    assert first.rect == (72, 300, 540, 500) and first.row_top == 320
    assert second.rect == (82, 310, 550, 510) and second.row_top == 330
    assert [c.x for c in second.columns] == [(82, 310), (310, 550)]


def test_table_rect_needs_segment():
    _rejects(_table(), {"op": "move", "id": "f1", "rect": [72, 300, 540, 500]}, "f1", "segment=")
    _rejects(_table(), {"op": "resize", "id": "f1", "rect": [72, 300, 540, 500]}, "f1", "segment=")
    _rejects(_table(), {"op": "resize", "id": "f1", "rect": [72, 300, 540, 500], "segment": 1},
             "no segment 1", "0..0")


def test_resize_table_segment_keeps_rows_and_columns_in_place():
    t, _ = apply(_table(), {"op": "resize", "id": "f1", "rect": [72, 300, 560, 520], "segment": 0},
                 author="human", context=CTX)
    seg = t.fields[0].segments[0]
    assert seg.rect == (72, 300, 560, 520) and seg.row_top == 320
    assert [c.x for c in seg.columns] == [(72, 300), (300, 540)]


def test_resize_table_too_small_names_what_no_longer_fits():
    _rejects(_table(), {"op": "resize", "id": "f1", "rect": [72, 300, 540, 400], "segment": 0},
             "segment 0", "10 rows", "at most 4 rows fit")
    _rejects(_table(), {"op": "resize", "id": "f1", "rect": [72, 300, 400, 500], "segment": 0},
             "column 'dose'", "[300, 540]")
    _rejects(_table(), {"op": "resize", "id": "f1", "rect": [72, 330, 540, 500], "segment": 0},
             "row_top 320")


def test_stale_source_path_does_not_block_unrelated_edits():
    stale = Field(id="c1", type="checkbox", page=0, rect=(100, 100, 110, 110), source={"paths": ["old_path"]})
    t, warnings = apply(_template([stale]), {"op": "move", "id": "c1", "dx": 1}, author="human", context=CTX)
    assert _field(t, "c1").rect == (101, 100, 111, 110)
    assert len(warnings) == 1 and "'old_path'" in warnings[0] and "not in the schema" in warnings[0]


# ------------------------------------------------------------------ widgets


def test_link_widget():
    ctx = OpContext(schema_paths=CTX.schema_paths, page_sizes=SIZES, widget_names=["w1", "w2"])
    t, _ = _add(_template())
    t, _ = _add(t, page=1)
    t, _ = apply(t, {"op": "link_widget", "id": "f1", "widget": "w1"}, author="agent", context=ctx)
    assert _field(t, "f1").existing_widget == "w1"
    with pytest.raises(OpError, match="already linked to field 'f1'"):
        apply(t, {"op": "link_widget", "id": "f2", "widget": "w1"}, author="agent", context=ctx)
    with pytest.raises(OpError, match="unknown widget 'w3'"):
        apply(t, {"op": "link_widget", "id": "f2", "widget": "w3"}, author="agent", context=ctx)


def test_radio_kids_may_share_their_group_widget(radio_form_pdf):
    fm = build_form_map(radio_form_pdf, detectors={})
    t = new_template(SCHEMA, fm)
    kids = [f for f in t.fields if f.type == "radio_option"]
    assert [f.existing_widget for f in kids] == ["sex", "sex"]
    ctx = OpContext(schema_paths=CTX.schema_paths, page_sizes=SIZES[:1],
                    widget_names=[w.name for p in fm.pages for w in p.widgets])
    t, _ = apply(t, {"op": "link_widget", "id": kids[1].id, "widget": None}, author="agent", context=ctx)
    t, _ = apply(t, {"op": "link_widget", "id": kids[1].id, "widget": "sex"}, author="agent", context=ctx)
    assert [_field(t, f.id).existing_widget for f in kids] == ["sex", "sex"]
    t, _ = apply(t, {"op": "add_field", "type": "radio_option", "page": 0, "rect": [280, 160, 290, 170]},
                 author="agent", context=ctx)
    t, _ = apply(t, {"op": "link_widget", "id": t.fields[-1].id, "widget": "sex"}, author="agent", context=ctx)
    for kind in ("text", "checkbox"):
        t, _ = apply(t, {"op": "add_field", "type": kind, "page": 0, "rect": [300, 160, 310, 170]},
                     author="agent", context=ctx)
        with pytest.raises(OpError, match=f"already linked to field {kids[0].id!r}"):
            apply(t, {"op": "link_widget", "id": t.fields[-1].id, "widget": "sex"}, author="agent", context=ctx)


def test_checkbox_kids_may_share_a_widget_but_not_with_text():
    ctx = OpContext(schema_paths=CTX.schema_paths, page_sizes=SIZES, widget_names=["box"])
    t, _ = _add(_template(), type="checkbox", rect=[100, 100, 110, 110])
    t, _ = _add(t, type="checkbox", rect=[120, 100, 130, 110])
    t, _ = _add(t, rect=[140, 100, 200, 110])
    for fid in ("f1", "f2"):
        t, _ = apply(t, {"op": "link_widget", "id": fid, "widget": "box"}, author="agent", context=ctx)
    with pytest.raises(OpError, match="already linked to field 'f1'"):
        apply(t, {"op": "link_widget", "id": "f3", "widget": "box"}, author="agent", context=ctx)


# ------------------------------------------------------------------ hardening


def test_set_style_nan_rejected():
    t, _ = _add(_template())
    with pytest.raises((OpError, pydantic.ValidationError)):
        apply(t, {"op": "set_style", "id": "f1", "style": {"font_size": float("nan")}}, author="agent", context=CTX)


def test_set_style_merge_that_breaks_min_above_size_rejected():
    t, _ = _add(_template(), style={"font_size": 8})
    with pytest.raises(OpError, match="min_font_size"):
        apply(t, {"op": "set_style", "id": "f1", "style": {"min_font_size": 9}}, author="agent", context=CTX)
    t, _ = apply(t, {"op": "set_style", "id": "f1", "style": {"min_font_size": 5}}, author="agent", context=CTX)
    assert _field(t, "f1").style.min_font_size == 5 and _field(t, "f1").style.font_size == 8


def _linked(kind, widget):
    t, _ = _add(_template(), type=kind, rect=[100, 100, 110, 110])
    return apply(t, {"op": "link_widget", "id": "f1", "widget": widget}, author="agent", context=CTX)[0]


def test_set_type_rejects_kind_mismatch_with_widget_kinds():
    ctx = CTX.model_copy(update={"widget_kinds": {"rg": ["radio"]}})
    t_radio = _linked("radio_option", "rg")
    with pytest.raises(OpError, match="radio"):
        apply(t_radio, SetType(id="f1", type="text"), author="human", context=ctx)
    apply(t_radio, SetType(id="f1", type="checkbox"), author="human", context=ctx)  # allowed


def test_link_widget_rejects_kind_mismatch():
    ctx = CTX.model_copy(update={"widget_names": ["sig"], "widget_kinds": {"sig": ["signature"]}})
    t_checkbox, _ = _add(_template(), type="checkbox", rect=[100, 100, 110, 110])
    with pytest.raises(OpError, match="signature"):
        apply(t_checkbox, LinkWidget(id="f1", widget="sig"), author="human", context=ctx)


def test_link_widget_accepts_matching_kind():
    ctx = CTX.model_copy(update={"widget_names": ["sig"], "widget_kinds": {"sig": ["signature"]}})
    t_sig, _ = _add(_template(), type="signature", rect=[100, 100, 200, 120])
    t, _ = apply(t_sig, LinkWidget(id="f1", widget="sig"), author="human", context=ctx)
    assert _field(t, "f1").existing_widget == "sig"


def test_set_type_without_widget_kinds_still_allowed():
    t_radio = _linked("radio_option", "rg")
    t, _ = apply(t_radio, SetType(id="f1", type="text"), author="human", context=CTX)
    assert _field(t, "f1").type == "text"


def test_from_template_fills_widget_kinds(radio_form_pdf):
    fm = build_form_map(radio_form_pdf, detectors={})
    ctx = OpContext.from_template(new_template(SCHEMA, fm), fm)
    assert ctx.widget_kinds["sex"] == ["radio", "radio"]
    assert OpContext.from_template(new_template(SCHEMA, fm)).widget_kinds is None


# ------------------------------------------------------------------ replay / diff


def test_replay_reproduces_final_template():
    base = _template([Field(id="f1", type="text", page=0, rect=(10, 10, 60, 30), origin="human")])
    t = base
    for op in (
        {"op": "add_field", "type": "checkbox", "page": 0, "rect": [72, 140, 82, 150]},
        {"op": "set_source", "id": "f2", "source": {"paths": ["married"]}},
        {"op": "move", "id": "f1", "dx": 2, "dy": -1},
        {"op": "set_style", "id": "f1", "style": {"font_size": 8}},
        {"op": "add_table", "source_path": "medications", "segments": [SEGMENT]},
        {"op": "move", "id": "f3", "dx": 3},
        {"op": "resize", "id": "f3", "rect": [75, 300, 543, 510], "segment": 0},
        {"op": "remove_field", "id": "f2"},
        {"op": "add_field", "type": "text", "page": 1, "rect": [72, 72, 200, 90]},
    ):
        t, _ = apply(t, op, author="agent", note="n", context=CTX)
    assert [f.id for f in t.fields] == ["f1", "f3", "f4"]
    assert replay(base, t.history, CTX) == t


def test_diff_reports_changes():
    t1, _ = _add(_template())
    t1, _ = _add(t1, page=1)
    t2, _ = apply(t1, {"op": "move", "id": "f1", "dx": 5}, author="human", context=CTX)
    t2, _ = _add(t2, page=1)
    t2, _ = apply(t2, {"op": "remove_field", "id": "f2"}, author="human", context=CTX)
    assert diff(t1, t2) == [
        {"id": "f1", "change": "changed", "keys": ["rect"]},
        {"id": "f2", "change": "removed"},
        {"id": "f3", "change": "added"},
    ]
    assert diff(t1, t1) == []


def test_a_table_warns_about_plain_fields_its_cells_cover():
    t, _ = _add(_template(), rect=[75, 321, 298, 337])  # f1: inside row 0's drug cell
    t, _ = _add(t, rect=[302, 340, 538, 356])  # f2: row 1's dose cell
    t, _ = _add(t, rect=[75, 520, 298, 536])  # f3: below the table
    op = {"op": "add_table", "label": "Meds", "source_path": "medications", "segments": [SEGMENT]}
    t, warnings = apply(t, op, author="agent", context=CTX)
    covering = [w for w in warnings if "covers" in w]
    assert len(covering) == 1
    assert "'f1'" in covering[0] and "'f2'" in covering[0] and "'f3'" not in covering[0]
    assert "remove_field" in covering[0]


def test_an_op_on_a_removed_field_says_it_was_removed():
    t, _ = _add(_template())
    t, _ = _add(t)
    t, _ = apply(t, {"op": "remove_field", "id": "f1"}, author="agent", context=CTX)
    with pytest.raises(OpError) as exc:
        apply(t, {"op": "set_label", "id": "f1", "label": "x"}, author="agent", context=CTX)
    assert "f1" in exc.value.reason and "already removed" in exc.value.reason


def test_an_unknown_field_id_suggests_close_ids():
    t, _ = _add(_template(), id="c0_12")
    with pytest.raises(OpError) as exc:
        apply(t, {"op": "set_label", "id": "c0_21", "label": "x"}, author="agent", context=CTX)
    assert "unknown field id 'c0_21'" in exc.value.reason and "c0_12" in exc.value.reason


def test_mapping_a_table_column_onto_a_plain_field_explains_the_table_to_build():
    t, _ = _add(_template(), rect=[72, 320, 300, 338])
    _rejects(t, {"op": "set_source", "id": "f1", "source": {"paths": ["medications[].drug"]}},
             "column of table 'medications'", "add_table(source_path='medications'",
             "{'key': 'drug', 'x': [72, 300], 'fields'", "'row_top': 320", "fm.grid", "column_fields",
             "If the records are printed")
    with pytest.raises(OpError) as exc:
        apply(t, {"op": "set_source", "id": "f1", "source": {"paths": ["medications[].drug"]}},
              author="agent", context=CTX)
    assert "'row_height': 18" not in exc.value.reason  # a box's height is not the row pitch


# ------------------------------------------------------------------ linked cells, seeded-field removals

# A 3-row grid of seeded widget boxes: each 18pt row has a 9pt box in its lower half per column,
# so a computed cell (the whole row) matches a box with IoU 0.5 only.
GRID_COLS = {"drug": (72.0, 300.0), "dose": (300.0, 540.0)}


def _seeded_grid(origin="widget"):
    fields = []
    for r in range(3):
        top = 320 + 18 * r
        for k, (x0, x1) in GRID_COLS.items():
            fid = f"w0_{len(fields)}" if origin == "widget" else f"c0_{len(fields)}"
            fields.append(
                Field(id=fid, type="text", page=0, rect=(x0, top + 9, x1, top + 18), origin=origin,
                      existing_widget=fid if origin == "widget" else None)
            )
    return _template(fields)


def _linked_segment(t, rows=3):
    ids = [f.id for f in t.fields if f.type != "table"]
    columns = [
        {"key": k, "x": list(x), "fields": ids[i::2][:rows]} for i, (k, x) in enumerate(GRID_COLS.items())
    ]
    return {**SEGMENT, "max_rows": 10, "columns": columns}


def _add_linked(t, **seg):
    op = {"op": "add_table", "source_path": "medications", "segments": [{**_linked_segment(t), **seg}]}
    return apply(t, op, author="agent", context=CTX)


def test_a_column_can_link_existing_fields_as_its_cells():
    t, warnings = _add_linked(_seeded_grid())
    table = _field(t, "f1")
    cells = table_cells(t, table)
    assert len(cells) == 10 * 2
    first = [c for c in cells if c.row == 0]
    assert [(c.column.key, c.field.id, c.rect) for c in first] == [
        ("drug", "w0_0", (72.0, 329.0, 300.0, 338.0)),
        ("dose", "w0_1", (300.0, 329.0, 540.0, 338.0)),
    ]
    # rows past the linked ids use the computed cell
    row5 = [c for c in cells if c.row == 5]
    assert all(c.field is None and c.rect[1] == 320 + 5 * 18 for c in row5)
    assert linked_cells(t)["w0_3"] == ("f1", 0, 1, "dose")
    assert len(t.fields) == 7 and _field(t, "w0_3").existing_widget == "w0_3"  # nothing removed
    assert not any("covers" in w or "Link those" in w for w in warnings)


@pytest.mark.parametrize(
    "fields, needle",
    [
        (["w0_0", "nope"], "unknown field id 'nope'"),
        (["w0_0", "w0_0"], "linked as a cell more than once"),
        (["w0_0"] * 11, "has 10 rows"),
    ],
)
def test_linked_cell_ids_are_validated(fields, needle):
    t = _seeded_grid()
    seg = {**SEGMENT, "columns": [{"key": "drug", "x": [72, 300], "fields": fields}]}
    _rejects(t, {"op": "add_table", "source_path": "medications", "segments": [seg]}, needle)


def test_a_linked_cell_must_sit_in_the_segment_and_have_no_source():
    t, _ = _add(_seeded_grid(), rect=[100, 600, 200, 620])  # f1, below the segment rect
    seg = {**SEGMENT, "columns": [{"key": "drug", "x": [72, 300], "fields": ["f1"]}]}
    _rejects(t, {"op": "add_table", "source_path": "medications", "segments": [seg]}, "not inside the segment rect")
    t, _ = apply(t, {"op": "set_source", "id": "w0_0", "source": {"paths": ["name"]}}, author="agent", context=CTX)
    seg = {**SEGMENT, "columns": [{"key": "drug", "x": [72, 300], "fields": ["w0_0"]}]}
    _rejects(t, {"op": "add_table", "source_path": "medications", "segments": [seg]},
             "has its own source", "set_source('w0_0', None)")


def test_a_linked_field_cannot_take_a_source_or_be_removed_until_unlinked():
    t, _ = _add_linked(_seeded_grid())
    _rejects(t, {"op": "set_source", "id": "w0_0", "source": {"paths": ["name"]}},
             "cell of table 'f1' segment 0 column 'drug' row 0")
    with pytest.raises(OpError, match="unlink it first"):
        apply(t, {"op": "remove_field", "id": "w0_0"}, author="human", context=CTX)
    cols = [{"key": "drug", "x": [72, 300], "fields": [None, "w0_2", "w0_4"]}]
    t, _ = apply(t, {"op": "set_table_columns", "id": "f1", "segment": 0, "columns": cols}, author="human", context=CTX)
    t, _ = apply(t, {"op": "remove_field", "id": "w0_0"}, author="human", context=CTX)
    assert "w0_0" not in linked_cells(t) and "w0_1" not in linked_cells(t)


def test_replay_reproduces_a_template_with_linked_cells():
    base = _seeded_grid()
    t, _ = _add_linked(base)
    t, _ = apply(t, {"op": "move", "id": "w0_0", "dy": 1}, author="human", context=CTX)
    assert replay(base, t.history, CTX).fields == t.fields


def test_an_agent_cannot_remove_a_widget_field_that_nothing_replaces():
    t = _seeded_grid()
    _rejects(t, {"op": "remove_field", "id": "w0_0"}, "the form's own widget 'w0_0'", "link it as a cell")
    t2, _ = apply(t, {"op": "remove_field", "id": "w0_0"}, author="human", context=CTX)  # people may
    assert "w0_0" not in [f.id for f in t2.fields]


def test_an_agent_removes_a_detected_box_only_when_another_box_replaces_it():
    t = _seeded_grid(origin="detector")
    _rejects(t, {"op": "remove_field", "id": "c0_0"}, "a detected box", "leave it with an empty source")
    # a duplicate on the same box (IoU >= 0.6) replaces it
    t3, _ = _add(t, rect=[73, 329, 300, 338])
    t3, _ = apply(t3, {"op": "remove_field", "id": "c0_0"}, author="agent", context=CTX)
    assert "c0_0" not in [f.id for f in t3.fields]
    # agent-made fields are not seeded evidence
    t4, _ = _add(t, rect=[100, 600, 200, 620])
    apply(t4, {"op": "remove_field", "id": "f1"}, author="agent", context=CTX)


def test_a_coarse_table_cell_does_not_replace_a_seeded_box_but_a_matching_one_does():
    t = _seeded_grid(origin="detector")
    coarse = {**SEGMENT, "columns": [{"key": "drug", "x": [72, 300]}, {"key": "dose", "x": [300, 540]}]}
    t1, warnings = apply(t, {"op": "add_table", "source_path": "medications", "segments": [coarse]},
                         author="agent", context=CTX)
    mismatch = [w for w in warnings if "Link those fields" in w]
    assert len(mismatch) == 1 and "'c0_0'" in mismatch[0] and "column_fields" in mismatch[0]
    _rejects(t1, {"op": "remove_field", "id": "c0_0"}, "closest box is 'f1', IoU 0.50")
    exact = {**coarse, "row_top": 329, "row_height": 9, "max_rows": 1}  # row 0's boxes exactly
    t2, warnings = apply(t, {"op": "add_table", "source_path": "medications", "segments": [exact]},
                         author="agent", context=CTX)
    assert any("covers" in w for w in warnings)
    apply(t2, {"op": "remove_field", "id": "c0_0"}, author="agent", context=CTX)  # its cell matches exactly


def test_an_agent_maps_seeded_boxes_where_they_are():
    t = _seeded_grid(origin="detector")
    _rejects(t, {"op": "move", "id": "c0_0", "dx": 2}, "placed by the field detector", "do not move")
    _rejects(_seeded_grid(), {"op": "resize", "id": "w0_0", "rect": [72, 329, 290, 338]},
             "the form's own widget", "do not resize")
    apply(t, {"op": "move", "id": "c0_0", "dx": 2}, author="human", context=CTX)  # people may
    t2, _ = _add(t, rect=[100, 600, 200, 620])
    apply(t2, {"op": "move", "id": "f1", "dx": 2}, author="agent", context=CTX)  # its own fields: yes


def test_a_detected_box_split_into_parts_can_be_removed():
    long_box = Field(id="c0_0", type="text", page=0, rect=(72, 320, 372, 338), origin="detector")
    t = _template([long_box])
    t, _ = _add(t, rect=[72, 320, 170, 338])
    _rejects(t, {"op": "remove_field", "id": "c0_0"}, "add the parts first")  # one part covers a third
    t, _ = _add(t, rect=[172, 320, 270, 338])
    t, _ = _add(t, rect=[272, 320, 372, 338])
    t, _ = apply(t, {"op": "remove_field", "id": "c0_0"}, author="agent", context=CTX)
    assert [f.id for f in t.fields] == ["f1", "f2", "f3"]


def test_a_boolean_on_a_text_box_beside_a_checkbox_warns():
    text = Field(id="c0_0", type="text", page=0, rect=(100, 200, 300, 218), origin="detector")
    box = Field(id="c0_1", type="checkbox", page=0, rect=(80, 202, 94, 216), origin="detector")
    far = Field(id="c0_2", type="checkbox", page=0, rect=(80, 402, 94, 416), origin="detector")
    t = _template([text, box, far])
    _, warnings = apply(t, {"op": "set_source", "id": "c0_0", "source": {"paths": ["married"]}},
                        author="agent", context=CTX)
    assert len(warnings) == 1 and "'c0_1'" in warnings[0] and "'c0_2'" not in warnings[0]
    _, warnings = apply(t, {"op": "set_source", "id": "c0_0", "source": {"paths": ["name"]}},
                        author="agent", context=CTX)
    assert warnings == []
