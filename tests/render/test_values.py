from formcast.render.values import is_missing, lookup, resolve, schema_path_of
from formcast.schema import validate_data, validate_data_located
from formcast.template.model import Column, Field, FormInfo, Source, TableSegment, Template
from formcast.transforms import Transform, to_text

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "first": {"type": "string"},
        "last": {"type": "string"},
        "count": {"type": "integer"},
        "agree": {"type": "boolean"},
        "dob": {"type": "string", "format": "date"},
        "married": {"type": "boolean"},
        "status": {"enum": ["single", "married"]},
        "contact": {"type": "array", "items": {"enum": ["email", "phone", "fax"]}},
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


def _tpl(*fields: Field) -> Template:
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=2, page_sizes=SIZES, kinds=["digital"] * 2),
        fields=list(fields),
    )


def _text(fid, *paths, transforms=(), join=None, type="text"):
    return Field(id=fid, type=type, page=0, rect=(100, 100, 200, 120),
                 source=Source(paths=list(paths), transforms=list(transforms), join=join))  # fmt: skip


def _check(fid, *paths, when=None, transforms=(), type="checkbox"):
    return Field(id=fid, type=type, page=0, rect=(100, 100, 120, 120),
                 source=Source(paths=list(paths), transforms=list(transforms), when=when))  # fmt: skip


def _contains(v):
    return Transform(op="contains", args={"value": v})


def _meds_table(segments=((0, 2), (1, 1))):
    segs = [
        TableSegment(page=page, rect=(72, 190, 540, 400), row_top=200, row_height=18, max_rows=n,
                     columns=[Column(key="drug", x=(72, 250)), Column(key="dose", x=(250, 540))])  # fmt: skip
        for page, n in segments
    ]
    return Field(id="t_meds", type="table", source=Source(paths=["medications"]), segments=segs)


tpl_count_agree = _tpl(_text("f_count", "count"), _check("f_agree", "agree"))
tpl_name_married = _tpl(_text("f_name", "name"), _check("f_married", "married"))
tpl_contact_boxes = _tpl(
    _check("f_email", "contact", when=_contains("email")),
    _check("f_phone", "contact", when=_contains("phone")),
    _check("f_fax", "contact", when=_contains("fax")),
)
tpl_contact_text = _tpl(_text("f_contact", "contact"))
tpl_fullname = _tpl(_text("f_full", "first", "last", join=" "))
tpl_dob_formatted = _tpl(_text("f_dob", "dob", transforms=[Transform(op="date_format", args={"format": "%m/%d/%Y"})]))
tpl_meds_two_segments = _tpl(_meds_table())
tpl_name_only = _tpl(_text("f_name", "name"))


def test_missing_values():
    for v in (None, "", "   ", []):
        assert is_missing(v)
    for v in (0, 0.0, False, "0", "x", [None], {}):
        assert not is_missing(v)
    assert lookup({"a": {"b": 1}}, "a.b") == 1 and lookup({}, "a.b") is None
    assert lookup({"a": 5}, "a.b") is None and lookup({"a b": 2}, '["a b"]') == 2
    assert lookup({"a": [1]}, "a[]") is None and lookup({}, "") is None


def test_zero_and_false_are_values():
    fields, _, _ = resolve(tpl_count_agree, {"count": 0, "agree": False})
    assert fields["f_count"].text == "0" and not fields["f_count"].warnings
    assert fields["f_agree"].on is False and fields["f_agree"].has_value and not fields["f_agree"].warnings


def test_empty_string_is_missing_for_text_and_choice():
    fields, _, _ = resolve(tpl_name_married, {"name": "", "married": ""})
    assert fields["f_name"].text is None and [w.code for w in fields["f_name"].warnings] == ["missing"]
    assert not fields["f_name"].has_value
    assert fields["f_married"].on is False
    assert [w.code for w in fields["f_married"].warnings] == ["missing"] and not fields["f_married"].has_value


def test_resolved_shape_and_unmapped_fields_skipped():
    unmapped = Field(id="f_free", type="text", page=0, rect=(1, 1, 50, 20))
    fields, tables, _ = resolve(_tpl(_text("f_name", "name"), _check("f_agree", "agree"), unmapped),
                                {"name": "Ada", "agree": True})  # fmt: skip
    assert set(fields) == {"f_name", "f_agree"} and tables == {}
    n, a = fields["f_name"], fields["f_agree"]
    assert (n.kind, n.paths, n.has_value, n.text, n.on) == ("text", ["name"], True, "Ada", None)
    assert (a.kind, a.has_value, a.text, a.on) == ("choice", True, None, True)
    sig = resolve(_tpl(_text("f_sig", "name", type="signature")), {"name": "Ada"})[0]["f_sig"]
    assert sig.kind == "text" and sig.text == "Ada"


def test_checkbox_without_when_uses_bool_to_check():
    t = _tpl(_check("f_a", "status"), _check("f_b", "status", type="choice_mark"))
    fields, _, _ = resolve(t, {"status": "no"})
    assert fields["f_a"].on is False and fields["f_a"].has_value
    fields, _, _ = resolve(t, {"status": "married"})
    assert fields["f_a"].on and fields["f_b"].on


def test_choice_when_applies_after_transforms_and_errors_reported():
    t = _tpl(_check("f_a", "dob", transforms=[Transform(op="date_part", args={"part": "year"})],
                    when=Transform(op="equals", args={"value": "1990"})))  # fmt: skip
    assert resolve(t, {"dob": "1990-05-01"})[0]["f_a"].on is True
    assert resolve(t, {"dob": "1991-05-01"})[0]["f_a"].on is False
    r = resolve(t, {"dob": "nope"})[0]["f_a"]
    assert r.on is False and r.has_value and [w.code for w in r.warnings] == ["transform_error"]


def test_multiselect_is_one_list_value():
    fields, _, _ = resolve(tpl_contact_boxes, {"contact": ["email", "phone"]})
    assert fields["f_email"].on and fields["f_phone"].on and not fields["f_fax"].on
    fields, _, _ = resolve(tpl_contact_boxes, {"contact": []})
    assert not fields["f_email"].on and [w.code for w in fields["f_email"].warnings] == ["missing"]


def test_unjoined_list_on_text_field_joins_with_comma():
    fields, _, _ = resolve(tpl_contact_text, {"contact": ["email", "phone"]})
    assert fields["f_contact"].text == "email, phone"
    assert [w.code for w in fields["f_contact"].warnings] == ["list_joined"]


def test_join_two_paths_skips_missing():
    assert resolve(tpl_fullname, {"first": "Ada", "last": ""})[0]["f_full"].text == "Ada"
    both = resolve(tpl_fullname, {"first": "Ada", "last": "Lovelace"})[0]["f_full"]
    assert both.text == "Ada Lovelace" and not both.warnings
    none = resolve(tpl_fullname, {"first": "", "last": " "})[0]["f_full"]
    assert none.text is None and [w.code for w in none.warnings] == ["missing"]


def test_transform_error_reported_not_raised():
    fields, _, _ = resolve(tpl_dob_formatted, {"dob": "next Tuesday"})
    assert fields["f_dob"].text is None and fields["f_dob"].warnings[0].code == "transform_error"
    assert "next Tuesday" in fields["f_dob"].warnings[0].message
    assert resolve(tpl_dob_formatted, {"dob": "1990-05-01"})[0]["f_dob"].text == "05/01/1990"


def test_table_flows_across_segments_and_drops_rows():
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": [{"drug": f"D{i}"} for i in range(4)]})
    t = tables["t_meds"]
    assert (t.rows_provided, t.rows_per_segment, t.rows_dropped) == (4, [2, 1], 1)
    assert [(c.page, c.row, c.text) for c in t.cells if c.key == "drug"] == [(0, 0, "D0"), (0, 1, "D1"), (1, 2, "D2")]
    assert t.cells[0].rect == (72.0, 200.0, 250.0, 218.0)
    assert [w.code for w in t.warnings] == ["rows_dropped"]
    # cells for every column of every written row; missing cell value: empty, no warning
    assert len(t.cells) == 6 and all(not c.warnings for c in t.cells)
    dose = next(c for c in t.cells if c.key == "dose" and c.row == 1)
    assert dose.text is None and dose.rect == (250.0, 218.0, 540.0, 236.0)
    assert (dose.table_id, dose.segment) == ("t_meds", 0)


def test_table_missing_and_empty():
    for data in ({}, {"medications": []}, {"medications": None}):
        _, tables, _ = resolve(tpl_meds_two_segments, data)
        t = tables["t_meds"]
        assert t.cells == [] and t.rows_provided == 0 and t.rows_dropped == 0
        assert [w.code for w in t.warnings] == ["missing"]


def test_malformed_rows_reported_not_raised():
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": [None, "x", {"drug": "ok"}]})
    t = tables["t_meds"]
    assert [c.text for c in t.cells if c.key == "drug"] == [None, None, "ok"]
    assert sum(w.code == "invalid" for w in t.warnings) == 2
    assert [c.row for c in t.cells if c.key == "drug"] == [0, 1, 2]
    _, tables, _ = resolve(tpl_meds_two_segments, {"medications": "oops"})
    assert tables["t_meds"].cells == [] and tables["t_meds"].warnings[0].code == "invalid"


def test_table_list_rows_cell_transforms_and_cell_errors():
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "array"}}}}
    seg = TableSegment(page=0, rect=(0, 0, 300, 300), row_top=10, row_height=10, max_rows=5, columns=[
        Column(key="0", x=(0, 100)),
        Column(key="1", x=(100, 200), transforms=[Transform(op="upper", args={})]),
        Column(key="5", x=(200, 300)),
    ])  # fmt: skip
    tpl = Template(template_id="t", schema=schema, form=FormInfo(fingerprint="f", page_count=1,
                   page_sizes=[(612.0, 792.0)], kinds=["digital"]),
                   fields=[Field(id="t", type="table", source=Source(paths=["rows"]), segments=[seg])])  # fmt: skip
    _, tables, _ = resolve(tpl, {"rows": [["a", "b"], {"0": "x"}]})
    t = tables["t"]
    assert [(c.row, c.key, c.text) for c in t.cells] == [
        (0, "0", "a"), (0, "1", "B"), (0, "5", None), (1, "0", None), (1, "1", None), (1, "5", None)]  # fmt: skip
    assert [w.code for w in t.warnings] == ["invalid"]

    # a failing column transform lands on the cell, not the table
    bad = _tpl(Field(id="t2", type="table", source=Source(paths=["medications"]), segments=[
        TableSegment(page=0, rect=(0, 0, 300, 300), row_top=10, row_height=10, max_rows=5,
                     columns=[Column(key="drug", x=(0, 100), transforms=[Transform(op="date_format", args={"format": "%Y"})])])]))  # fmt: skip
    _, tables, _ = resolve(bad, {"medications": [{"drug": "zzz"}]})
    cell = tables["t2"].cells[0]
    assert cell.text is None and [w.code for w in cell.warnings] == ["transform_error"] and tables["t2"].warnings == []


def test_record_level_warnings():
    _, _, rec = resolve(tpl_name_only, {"name": "A", "count": "seven", "address": {"city": "X"}, "zzz": 1})
    codes = {(w.code, w.path) for w in rec}
    assert ("invalid", "count") in codes and ("unmapped_data", "address.city") in codes
    assert any(w.code == "unknown_keys" for w in rec)
    assert not any(w.code == "unmapped_data" and w.path in ("name", "address") for w in rec)
    unk = next(w for w in rec if w.code == "unknown_keys")
    assert "zzz" in unk.message and unk.severity == "info"


def test_record_level_clean_and_table_columns():
    tpl = _tpl(_text("f_name", "name"), _meds_table())
    _, _, rec = resolve(tpl, {"name": "A", "medications": [{"drug": "d"}]})
    assert rec == []
    # a column the table doesn't cover, and invalid rows map to schema paths
    one_col = _tpl(Field(id="t", type="table", source=Source(paths=["medications"]), segments=[
        TableSegment(page=0, rect=(0, 0, 300, 300), row_top=10, row_height=10, max_rows=5,
                     columns=[Column(key="drug", x=(0, 100))])]))  # fmt: skip
    _, _, rec = resolve(one_col, {"medications": [{"drug": "d", "dose": "5mg", "extra": 1}, 7]})
    codes = {(w.code, w.path) for w in rec}
    assert ("unmapped_data", "medications[].dose") in codes and ("invalid", "medications[]") in codes
    assert not any(w.path == "medications[].drug" for w in rec)
    assert any(w.code == "unknown_keys" and "medications[].extra" in w.message for w in rec)
    # an unmapped table is reported once, at the table path
    _, _, rec = resolve(tpl_name_only, {"medications": [{"drug": "d", "dose": "x"}]})
    assert [w.path for w in rec if w.code == "unmapped_data"] == ["medications"]


def test_unknown_keys_not_reported_when_schema_forbids_extras():
    schema = {**SCHEMA, "additionalProperties": False}
    tpl = Template(template_id="t", schema=schema, form=FormInfo(fingerprint="f", page_count=1,
                   page_sizes=[(612.0, 792.0)], kinds=["digital"]), fields=[_text("f_name", "name")])  # fmt: skip
    _, _, rec = resolve(tpl, {"name": "A", "zzz": 1})
    assert [w.code for w in rec] == ["invalid"] and rec[0].path is None


def test_schema_path_of():
    assert schema_path_of("medications[2].dose") == "medications[].dose"
    assert schema_path_of("a[0][12].b") == "a[][].b"
    assert schema_path_of('["a[3]"][1]') == '["a[3]"][]'
    assert schema_path_of("name") == "name" and schema_path_of("") == ""


def test_validate_data_located_matches_validate_data():
    data = {"count": "x", "medications": [{"drug": 1}]}
    located = validate_data_located(SCHEMA, data)
    assert [loc for loc, _ in located] == ["count", "medications[0].drug"]
    assert validate_data(SCHEMA, data) == [f"{loc}: {msg}" for loc, msg in located]
    assert validate_data_located(SCHEMA, []) == [("", "[] is not of type 'object'")]


def test_to_text_public():
    assert to_text(5) == "5" and to_text("a") == "a"


def test_path_shaped_column_keys_resolve_and_count_as_covered():
    schema = {"type": "object", "properties": {"visit": {"type": "object", "properties": {"meds": {
        "type": "array", "items": {"type": "object", "properties": {
            "drug": {"type": "string"},
            "dose mg": {"type": "string"},
            "sched": {"type": "object", "properties": {"time": {"type": "string"}}},
        }}}}}}}  # fmt: skip
    seg = TableSegment(page=0, rect=(0, 0, 400, 300), row_top=10, row_height=10, max_rows=5, columns=[
        Column(key="drug", x=(0, 100)),
        Column(key='["dose mg"]', x=(100, 200)),
        Column(key="sched.time", x=(200, 300)),
    ])  # fmt: skip
    tpl = Template(template_id="t", schema=schema, form=FormInfo(fingerprint="f", page_count=1,
                   page_sizes=[(612.0, 792.0)], kinds=["digital"]),
                   fields=[Field(id="t", type="table", source=Source(paths=["visit.meds"]), segments=[seg])])  # fmt: skip
    rows = [{"drug": "A", "dose mg": "5", "sched": {"time": "am"}}, {"drug": "B", "sched": "oops"}]
    _, tables, rec = resolve(tpl, {"visit": {"meds": rows}})
    t = tables["t"]
    assert [(c.row, c.key, c.text) for c in t.cells] == [
        (0, "drug", "A"), (0, '["dose mg"]', "5"), (0, "sched.time", "am"),
        (1, "drug", "B"), (1, '["dose mg"]', None), (1, "sched.time", None)]  # fmt: skip
    assert not any(w.code == "unmapped_data" for w in rec)


def test_missing_message_distinguishes_transform_result_from_absent_value():
    split = Transform(op="split", args={"sep": "-", "index": 3})  # "" for "Ada": present value, empty result
    r = resolve(_tpl(_text("f_a", "name", transforms=[split])), {"name": "Ada"})[0]["f_a"]
    assert r.has_value and r.text is None
    assert [w.code for w in r.warnings] == ["missing"] and "transforms" in r.warnings[0].message
    absent = resolve(tpl_name_only, {})[0]["f_name"]
    assert [w.code for w in absent.warnings] == ["missing"] and absent.warnings[0].message.startswith("no value for")
