"""formcast.data.mapping: a data mapping turns rows of a user's table into records of a form's schema."""

from __future__ import annotations

import pytest

from formcast.data import DataMapping, Table, apply_mapping, check_mapping, direct_mapping, records_from

SCHEMA = {
    "type": "object",
    "properties": {
        "applicant": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "dob": {"type": "string", "format": "date"},
                "address": {"type": "string"},
                "us_citizen": {"type": "boolean"},
                "children": {"type": "integer"},
                "status": {"type": "string", "enum": ["single", "married"]},
                "languages": {"type": "array", "items": {"type": "string"}},
            },
        },
        "employers": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "salary": {"type": "number"}}},
        },
        "office": {"type": "string"},
    },
}

ROW = {
    "Full Name": "Ann B Lee",
    "DOB": "03/14/1990",
    "Street": "412 Oak St",
    "City": "Menomonie",
    "ZIP": None,
    "Citizen?": "Y",
    "Kids": "2",
    "Marital": "MARRIED",
    "Langs": "English; Spanish",
    "Employer 1": "Dunn County",
    "Salary 1": "$41,200",
    "Employer 2": None,
}


def mapping(*rules) -> DataMapping:
    return DataMapping.model_validate({"rules": list(rules)})


def test_split_template_date_and_index_paths_make_a_nested_record():
    m = mapping(
        {"path": "applicant.first_name", "column": "Full Name", "transforms": [{"op": "split", "args": {"index": 0}}]},
        {"path": "applicant.last_name", "column": "Full Name", "transforms": [{"op": "split", "args": {"index": -1}}]},
        {"path": "applicant.dob", "column": "DOB", "transforms": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]},
        {"path": "applicant.address", "template": "{Street}, {City} {ZIP}"},
        {"path": "employers[0].name", "column": "Employer 1"},
        {"path": "employers[0].salary", "column": "Salary 1", "transforms": [{"op": "number"}]},
        {"path": "employers[1].name", "column": "Employer 2"},
        {"path": "office", "value": "HR"},
    )
    assert apply_mapping(m, SCHEMA, ROW) == {
        "applicant": {"first_name": "Ann", "last_name": "Lee", "dob": "1990-03-14", "address": "412 Oak St, Menomonie"},
        "employers": [{"name": "Dunn County", "salary": 41200}],
        "office": "HR",
    }


def test_values_are_coerced_to_the_schema_type_without_transforms():
    m = mapping(
        {"path": "applicant.us_citizen", "column": "Citizen?"},
        {"path": "applicant.children", "column": "Kids"},
        {"path": "applicant.status", "column": "Marital"},
        {"path": "applicant.languages", "column": "Langs", "transforms": [{"op": "list", "args": {"sep": ";"}}]},
    )
    assert apply_mapping(m, SCHEMA, ROW)["applicant"] == {
        "us_citizen": True, "children": 2, "status": "married", "languages": ["English", "Spanish"],
    }  # fmt: skip


def test_map_and_default_transforms():
    m = mapping(
        {"path": "applicant.status", "column": "Marital", "transforms": [{"op": "map", "args": {"values": {"married": "married", "s": "single"}}}]},
        {"path": "office", "column": "ZIP", "transforms": [{"op": "default", "args": {"value": "none given"}}]},
    )
    assert apply_mapping(m, SCHEMA, ROW) == {"applicant": {"status": "married"}, "office": "none given"}


def test_an_empty_value_sets_nothing_and_a_template_of_empty_cells_is_empty():
    m = mapping({"path": "office", "template": "{ZIP} {Employer 2}"}, {"path": "applicant.first_name", "column": "ZIP"})
    assert apply_mapping(m, SCHEMA, ROW) == {}


def test_a_transform_with_bad_arguments_leaves_the_value_alone():
    m = mapping({"path": "office", "column": "City", "transforms": [{"op": "regex", "args": {"pattern": "("}}]})
    assert apply_mapping(m, SCHEMA, ROW) == {"office": "Menomonie"}


def test_a_rule_needs_exactly_one_source():
    with pytest.raises(ValueError):
        mapping({"path": "office"})
    with pytest.raises(ValueError):
        mapping({"path": "office", "column": "City", "value": "x"})


def test_check_mapping_drops_what_cannot_apply_and_says_why():
    m = mapping(
        {"path": "office", "column": "City"},
        {"path": "applicant.nickname", "column": "City"},
        {"path": "employers[].name", "column": "Employer 1"},
        {"path": "applicant", "column": "City"},
        {"path": "applicant.first_name", "column": "Nope"},
        {"path": "applicant.last_name", "column": "Full Name", "transforms": [{"op": "frobnicate"}]},
    )
    checked, warnings = check_mapping(m, SCHEMA, list(ROW))
    assert [r.path for r in checked.rules] == ["office", "applicant.last_name"]
    assert checked.rules[1].transforms == []
    assert len(warnings) == 5
    assert any("needs its index" in w for w in warnings)
    assert any("no column 'Nope'" in w for w in warnings)
    assert "Full Name" not in checked.unmapped_columns and "DOB" in checked.unmapped_columns


def test_direct_mapping_uses_columns_named_by_schema_path():
    m = direct_mapping(SCHEMA, ["applicant.first_name", "employers[0].name", "Notes"])
    assert [r.path for r in m.rules] == ["applicant.first_name", "employers[0].name"]
    assert m.unmapped_columns == ["Notes"]
    t = Table(["applicant.first_name", "employers[0].name"], [{"applicant.first_name": "Ann", "employers[0].name": "X"}])
    assert records_from(direct_mapping(SCHEMA, t.columns), SCHEMA, t) == [{"applicant": {"first_name": "Ann"}, "employers": [{"name": "X"}]}]


def test_a_mapping_fits_a_table_with_every_column_it_reads_and_survives_a_round_trip():
    m = mapping({"path": "applicant.address", "template": "{Street}, {City}"}, {"path": "office", "value": "HR"})
    assert m.fits(["Street", "City", "Other"])
    assert not m.fits(["Street"])
    again = DataMapping.model_validate(m.dump())
    assert again == m
    assert "value" not in m.dump()["rules"][0]
