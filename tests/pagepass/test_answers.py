from __future__ import annotations

import json

from formcast.pagepass.answers import TableSpec, apply_ops, mapping_ops, parse_answer, table_ops
from formcast.template.model import Field, FormInfo, Template

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "ok": {"type": "boolean"},
        "rows": {
            "type": "array",
            "items": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}},
        },
    },
}


def _f(id, rect, page=0, type="text"):
    return Field(id=id, type=type, page=page, rect=rect, origin="detector")


def _template() -> Template:
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=2, page_sizes=[(612, 792), (612, 792)], kinds=["digital"] * 2),
        fields=[
            _f("w0_1", (100, 100, 300, 120)),  # name
            _f("w0_2", (100, 140, 112, 152)),  # ok
            _f("w0_3", (100, 200, 200, 220)),  # row 0, a
            _f("w0_4", (210, 200, 310, 220)),  # row 0, b
            _f("w0_5", (100, 230, 200, 250)),  # row 1, a
            _f("w0_6", (210, 230, 310, 250)),  # row 1, b
            _f("w1_1", (100, 100, 200, 120), page=1),  # continued row 0, a
            _f("w1_2", (210, 100, 300, 120), page=1),  # continued row 0, b
        ],
    )


def _mapping(fid, path, **source):
    return {"id": fid, "source": {"paths": [path], **source}}


def _table(rows, source_path="rows", columns=("a", "b")):
    return {"source_path": source_path, "columns": [{"key": k, "transforms": []} for k in columns], "rows": rows}


def test_parse_answer_takes_the_outermost_json_object():
    assert parse_answer('Sure:\n{"mappings": [], "tables": []}\nDone') == {"mappings": [], "tables": []}
    assert parse_answer('{"mappings": [') is None
    assert parse_answer("no json") is None
    assert parse_answer('{"mappings": [{"id": "x"}]}') == {"mappings": [{"id": "x"}], "tables": []}
    assert parse_answer("{}") == {"mappings": [], "tables": []}


def test_an_answer_cut_off_at_the_output_limit_keeps_its_complete_items():
    cut = '{"mappings": [{"id": "w0_1", "source": {"paths": ["name"]}}, {"id": "w0_2", "source": {"pa'
    assert parse_answer(cut) == {"mappings": [{"id": "w0_1", "source": {"paths": ["name"]}}], "tables": []}
    cut = '{"mappings": [], "tables": [{"source_path": "rows", "rows": [{"a": "w0_3"}]}, {"source_path": "ro'
    assert parse_answer(cut)["tables"] == [{"source_path": "rows", "rows": [{"a": "w0_3"}]}]
    assert parse_answer('{"mappings": [{"id": "w0_1", "sou') is None  # nothing complete: unparsed


def test_choice_on_a_text_box_sets_type_checkbox_first():
    when = {"op": "equals", "args": {"value": True}}
    ops, specs = mapping_ops(_template(), {"mappings": [_mapping("w0_2", "ok", when=when)]}, 0)
    assert ops == [
        {"op": "set_type", "id": "w0_2", "type": "checkbox"},
        {"op": "set_source", "id": "w0_2", "source": {"paths": ["ok"], "when": when}},
    ]
    assert specs == []
    ops, _ = mapping_ops(_template(), {"mappings": [_mapping("w0_2", "ok", transforms=[{"op": "bool_to_check"}])]}, 0)
    assert [o["op"] for o in ops] == ["set_type", "set_source"]


def test_plain_mapping_is_just_set_source():
    ops, _ = mapping_ops(_template(), {"mappings": [_mapping("w0_1", "name")]}, 0)
    assert ops == [{"op": "set_source", "id": "w0_1", "source": {"paths": ["name"]}}]


def test_mapping_with_unknown_or_other_page_id_is_dropped():
    answer = {
        "mappings": [
            _mapping("w9_9", "name"),  # unknown
            _mapping("w1_1", "name"),  # on page 1, not 0
            {"id": "w0_1", "source": {"paths": []}},  # no paths
            {"id": "w0_1"},
            "junk",
        ]
    }
    assert mapping_ops(_template(), answer, 0)[0] == []


def test_bare_marks_from_the_image_resolve_to_the_page_box_ids():
    # The model sometimes answers with the mark drawn on the image ("3", or 3) instead of the id.
    answer = {
        "mappings": [_mapping("1", "name"), _mapping(3, "rows[].a")],
        "tables": [_table([{"a": "3", "b": 4}, {"a": "w0_5", "b": " 6 "}])],
    }
    ops, specs = mapping_ops(_template(), answer, 0)
    assert [o["id"] for o in ops] == ["w0_1"]  # mark 3 is a table cell, so it is not also mapped
    assert specs[0].rows == [{"a": "w0_3", "b": "w0_4"}, {"a": "w0_5", "b": "w0_6"}]
    assert mapping_ops(_template(), {"mappings": [_mapping("1", "name")]}, 1)[0][0]["id"] == "w1_1"
    assert mapping_ops(_template(), {"mappings": [_mapping(True, "name"), _mapping("9", "name")]}, 0)[0] == []


def test_box_in_a_table_is_not_also_mapped():
    answer = {
        "mappings": [_mapping("w0_3", "rows[].a"), _mapping("w0_1", "name")],
        "tables": [_table([{"a": "w0_3", "b": "w0_4"}])],
    }
    ops, specs = mapping_ops(_template(), answer, 0)
    assert [o["id"] for o in ops] == ["w0_1"]
    assert len(specs) == 1


def test_table_rows_drop_unknown_and_reused_ids():
    answer = {
        "tables": [
            _table([{"a": "w0_3", "b": "nope"}, {"a": "w1_1"}, {}, "junk"]),
            _table([{"a": "w0_3", "b": "w0_4"}, {"b": "w0_6"}]),  # w0_3 is used by the first table
            _table([{"a": "w9_9"}]),  # nothing left: dropped
        ]
    }
    _, specs = mapping_ops(_template(), answer, 0)
    assert [s.rows for s in specs] == [[{"a": "w0_3"}], [{"b": "w0_4"}, {"b": "w0_6"}]]
    assert all(s.page == 0 and s.source_path == "rows" for s in specs)


def test_table_column_transforms_come_from_the_columns():
    table = _table([{"a": "w0_3"}])
    table["columns"][0]["transforms"] = [{"op": "upper"}]
    _, specs = mapping_ops(_template(), {"tables": [table]}, 0)
    assert specs[0].transforms == {"a": [{"op": "upper"}], "b": []}


def test_table_segment_geometry_from_linked_boxes():
    spec = TableSpec(0, "rows", {"a": [{"op": "upper"}]}, [{"a": "w0_3", "b": "w0_4"}, {"a": "w0_5", "b": "w0_6"}])
    (op,) = [o for o in table_ops(_template(), [spec]) if o["op"] == "add_table"]
    (seg,) = op["segments"]
    assert op["source_path"] == "rows"
    assert seg["page"] == 0
    assert seg["rect"] == [99, 199, 311, 251]
    assert seg["row_top"] == 199
    assert seg["row_height"] == 26
    assert seg["max_rows"] == 2
    assert seg["columns"] == [
        {"key": "a", "x": [100, 200], "transforms": [{"op": "upper"}], "fields": ["w0_3", "w0_5"]},
        {"key": "b", "x": [210, 310], "transforms": [], "fields": ["w0_4", "w0_6"]},
    ]


def test_column_with_a_missing_cell_and_a_narrow_box():
    t = _template()
    t.fields[2].rect = (100, 200, 101, 220)  # one point wide
    spec = TableSpec(0, "rows", {}, [{"a": "w0_3", "b": "w0_4"}, {"b": "w0_6"}])
    (op,) = table_ops(t, [spec])[-1:]
    a, b = op["segments"][0]["columns"]
    assert a["x"] == [100, 104] and a["fields"] == ["w0_3", None]
    assert b["fields"] == ["w0_4", "w0_6"]


def test_linked_boxes_with_a_source_are_cleared_first():
    t = _template()
    t, applied, rejected = apply_ops(t, [{"op": "set_source", "id": "w0_3", "source": {"paths": ["name"]}}], 0, None)
    assert (applied, rejected) == (1, [])
    spec = TableSpec(0, "rows", {}, [{"a": "w0_3", "b": "w0_4"}])
    ops = table_ops(t, [spec])
    assert [o["op"] for o in ops] == ["set_source", "add_table"]
    assert ops[0] == {"op": "set_source", "id": "w0_3", "source": None}


def test_continuing_table_across_pages_becomes_one_add_table_with_two_segments():
    specs = [
        TableSpec(1, "rows", {}, [{"a": "w1_1", "b": "w1_2"}]),
        TableSpec(0, "rows", {}, [{"a": "w0_3", "b": "w0_4"}, {"a": "w0_5", "b": "w0_6"}]),
    ]
    ops = table_ops(_template(), specs)
    assert [o["op"] for o in ops] == ["add_table"]
    assert [s["page"] for s in ops[0]["segments"]] == [0, 1]
    assert [s["max_rows"] for s in ops[0]["segments"]] == [2, 1]


def test_block_tables_with_disjoint_keys_stay_separate():
    specs = [
        TableSpec(0, "rows", {}, [{"a": "w0_3"}, {"a": "w0_5"}]),
        TableSpec(0, "rows", {}, [{"b": "w0_4"}, {"b": "w0_6"}]),
    ]
    assert [o["op"] for o in table_ops(_template(), specs)] == ["add_table", "add_table"]


def test_specs_on_one_page_or_with_other_source_paths_are_not_merged():
    same_page = [TableSpec(0, "rows", {}, [{"a": "w0_3"}]), TableSpec(0, "rows", {}, [{"a": "w0_5"}])]
    assert len(table_ops(_template(), same_page)) == 2
    other_path = [TableSpec(0, "rows", {}, [{"a": "w0_3"}]), TableSpec(1, "other", {}, [{"a": "w1_1"}])]
    assert len(table_ops(_template(), other_path)) == 2


def test_blocks_continuing_across_pages_merge_line_by_line():
    specs = [
        TableSpec(0, "rows", {}, [{"a": "w0_3"}]),
        TableSpec(0, "rows", {}, [{"b": "w0_4"}]),
        TableSpec(1, "rows", {}, [{"a": "w1_1"}]),
        TableSpec(1, "rows", {}, [{"b": "w1_2"}]),
    ]
    ops = table_ops(_template(), specs)
    assert [[(s["page"], [c["key"] for c in s["columns"]]) for s in o["segments"]] for o in ops] == [
        [(0, ["a"]), (1, ["a"])],
        [(0, ["b"]), (1, ["b"])],
    ]


def test_apply_ops_records_rejections_and_keeps_going():
    t = _template()
    ops = [
        {"op": "set_source", "id": "w0_1", "source": {"paths": ["no.such.path"]}},
        {"op": "set_source", "id": "w0_1", "source": {"paths": ["name"]}},
    ]
    t2, applied, rejected = apply_ops(t, ops, 0, None)
    assert applied == 1
    assert len(rejected) == 1 and rejected[0]["op"] is ops[0] and "no.such.path" in rejected[0]["reason"]
    assert next(f for f in t2.fields if f.id == "w0_1").source.paths == ["name"]
    assert [(h.author, h.note) for h in t2.history] == [("agent", "page pass p0")]
    assert apply_ops(t, ops[1:], None, None)[0].history[0].note == "page pass"


def test_end_to_end_answer_applies_and_scores():
    text = json.dumps({
        "mappings": [_mapping("w0_1", "name"), _mapping("w0_2", "ok", when={"op": "equals", "args": {"value": True}})],
        "tables": [_table([{"a": "w0_3", "b": "w0_4"}, {"a": "w0_5", "b": "w0_6"}])],
    })
    t = _template()
    ops, specs = mapping_ops(t, parse_answer(text), 0)
    t, applied, rejected = apply_ops(t, ops, 0, None)
    t, applied2, rejected2 = apply_ops(t, table_ops(t, specs), 0, None)
    assert (applied, rejected, applied2, rejected2) == (3, [], 1, [])
    by_id = {f.id: f for f in t.fields}
    assert by_id["w0_1"].source.paths == ["name"]
    assert by_id["w0_2"].type == "checkbox" and by_id["w0_2"].source.paths == ["ok"]
    (table,) = [f for f in t.fields if f.type == "table"]
    assert table.source.paths == ["rows"]
    assert [c.fields for c in table.segments[0].columns] == [["w0_3", "w0_5"], ["w0_4", "w0_6"]]


def test_malformed_values_never_raise_and_good_parts_still_apply():
    answer = {
        "mappings": [
            _mapping("w0_1", "name"),
            {"id": "w0_2", "source": "ok"},
            {"id": "w0_2", "source": {"paths": "ok"}},
            {"id": "w0_2", "source": {"paths": ["ok"], "transforms": "bool_to_check"}},
            {"id": ["w0_2"], "source": {"paths": ["ok"]}},
            5,
        ],
        "tables": [
            {"source_path": "rows", "columns": "a", "rows": "w0_3"},
            {"source_path": "rows", "columns": [{"key": "a", "transforms": "upper"}, 7, {"key": ["b"]}],
             "rows": [{"a": "w0_3", "b": ["w0_4"]}, "junk", 3, {"a": 5.5}]},
            {"source_path": ["rows"], "columns": None, "rows": None},
            "junk",
        ],
    }  # fmt: skip
    t = _template()
    ops, specs = mapping_ops(t, answer, 0)
    assert [o["op"] for o in ops] == ["set_source", "set_source"]
    assert [o["id"] for o in ops] == ["w0_1", "w0_2"]
    assert [(s.source_path, s.transforms, s.rows) for s in specs] == [("rows", {"a": []}, [{"a": "w0_3"}])]
    t, applied, rejected = apply_ops(t, ops + table_ops(t, specs), 0, None)
    assert applied == 2  # the name mapping and the table; the scalar transforms are a recorded rejection
    assert [r["op"]["id"] for r in rejected] == ["w0_2"]
    assert parse_answer('{"mappings": "x", "tables": {"a": 1}}') == {"mappings": [], "tables": []}
    assert mapping_ops(_template(), {"mappings": 3, "tables": "x"}, 0) == ([], [])
