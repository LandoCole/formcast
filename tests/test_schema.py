import pytest

from formcast.errors import FormcastError
from formcast.schema import (
    ARRAY_ITEM,
    SchemaPath,
    flatten_schema,
    format_path,
    parse_path,
    resolve,
    validate_data,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "applicant": {
            "type": "object",
            "description": "The person applying",
            "properties": {
                "first name": {"type": "string"},
                "dob": {"type": "string", "format": "date", "description": "Date of birth"},
                "sex": {"type": "string", "enum": ["F", "M", "X"]},
                "age": {"type": "integer"},
                "height": {"type": "number"},
                "smoker": {"type": "boolean"},
            },
        },
        "aliases": {"type": "array", "items": {"type": "string"}},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "dose": {"type": "number"},
                },
            },
        },
    },
}


def by_path(paths):
    return {p.path: p for p in paths}


def test_nested_object_date():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["applicant.dob"].type == "date"
    assert paths["applicant.dob"].description == "Date of birth"
    assert paths["applicant"].type == "object"
    assert paths["applicant"].description == "The person applying"


def test_integer_number_boolean_distinct():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["applicant.age"].type == "integer"
    assert paths["applicant.height"].type == "number"
    assert paths["applicant.smoker"].type == "boolean"


def test_array_of_objects_emits_table_and_columns():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["medications"].type == "array"
    assert paths["medications"].item_type == "object"
    assert paths["medications"].in_table is None
    assert paths["medications[].name"].type == "string"
    assert paths["medications[].name"].in_table == "medications"
    assert paths["medications[].dose"].in_table == "medications"


def test_array_of_strings():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["aliases"].type == "array"
    assert paths["aliases"].item_type == "string"
    assert "aliases[]" not in paths


def test_enum_carries_values():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["applicant.sex"].type == "enum"
    assert paths["applicant.sex"].enum == ["F", "M", "X"]
    assert paths["applicant.age"].enum is None


def test_property_with_space_is_quoted_and_resolvable():
    paths = flatten_schema(SCHEMA)
    assert 'applicant["first name"]' in by_path(paths)
    assert resolve(paths, 'applicant["first name"]').type == "string"


def test_missing_description_is_empty_string():
    paths = by_path(flatten_schema(SCHEMA))
    assert paths["aliases"].description == ""


def test_resolve_missing_raises_keyerror():
    with pytest.raises(KeyError):
        resolve(flatten_schema(SCHEMA), "nope")


def test_schema_path_serializes_to_dict():
    p = SchemaPath(path="a", type="string")
    assert p.model_dump() == {
        "path": "a",
        "type": "string",
        "description": "",
        "enum": None,
        "item_type": None,
        "in_table": None,
    }


def test_validate_data_reports_wrong_type():
    errors = validate_data(SCHEMA, {"applicant": {"dob": 5}})
    assert len(errors) == 1
    assert "applicant.dob" in errors[0]
    assert "5 is not of type 'string'" in errors[0]


def test_validate_data_valid_is_empty():
    data = {
        "applicant": {"dob": "2000-01-31", "sex": "F"},
        "medications": [{"name": "x", "dose": 1.5}],
    }
    assert validate_data(SCHEMA, data) == []


def test_validate_data_checks_date_format_and_array_index():
    errors = validate_data(SCHEMA, {"applicant": {"dob": "nope"}, "medications": [{"dose": "x"}]})
    assert any("applicant.dob" in e for e in errors)
    assert any("medications[0].dose" in e for e in errors)


def test_validate_data_root_error_has_no_prefix():
    errors = validate_data(SCHEMA, [])
    assert errors == ["[] is not of type 'object'"]


# ---- refs, nullable, unions ----


def test_local_refs_are_resolved():
    schema = {
        "type": "object",
        "$defs": {
            "Addr": {
                "type": "object",
                "properties": {"zip": {"type": "string"}},
            }
        },
        "properties": {
            "home": {"$ref": "#/$defs/Addr", "description": "Home address"},
            "past": {"type": "array", "items": {"$ref": "#/$defs/Addr"}},
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["home.zip"].type == "string"
    assert paths["home"].description == "Home address"
    assert paths["past[].zip"].in_table == "past"


def test_remote_ref_raises():
    schema = {"type": "object", "properties": {"a": {"$ref": "http://example.com/x.json"}}}
    with pytest.raises(FormcastError):
        flatten_schema(schema)


def test_dangling_ref_raises():
    schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}}
    with pytest.raises(FormcastError):
        flatten_schema(schema)


def test_recursive_ref_terminates():
    schema = {
        "type": "object",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "child": {"$ref": "#/$defs/Node"}},
            }
        },
        "properties": {"root": {"$ref": "#/$defs/Node"}},
    }
    paths = by_path(flatten_schema(schema))
    assert paths["root.name"].type == "string"
    assert paths["root.child"].type == "object"
    assert "root.child.name" not in paths


def test_nullable_type_list_unwrapped():
    schema = {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}
    assert by_path(flatten_schema(schema))["a"].type == "string"


def test_nullable_anyof_unwrapped():
    schema = {
        "type": "object",
        "properties": {
            "a": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "description": "d"},
            "b": {"oneOf": [{"type": "null"}, {"type": "integer"}]},
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["a"].type == "date"
    assert paths["a"].description == "d"
    assert paths["b"].type == "integer"


def test_multi_branch_union_uses_first_branch():
    schema = {
        "type": "object",
        "properties": {"a": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
    }
    assert by_path(flatten_schema(schema))["a"].type == "integer"


def test_nullable_array_of_objects_unwrapped():
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "anyOf": [
                    {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "string"}}}},
                    {"type": "null"},
                ]
            }
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["rows"].type == "array"
    assert paths["rows[].x"].in_table == "rows"


def test_allof_merges_properties():
    schema = {
        "type": "object",
        "properties": {
            "p": {
                "allOf": [
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                    {"properties": {"b": {"type": "integer"}}},
                ]
            }
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["p.a"].type == "string"
    assert paths["p.b"].type == "integer"


def test_nested_table_structures():
    schema = {
        "type": "object",
        "properties": {
            "visits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "when": {"type": "string", "format": "date"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "doc": {"type": "object", "properties": {"name": {"type": "string"}}},
                        "meds": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"n": {"type": "string"}}},
                        },
                    },
                },
            }
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["visits[].when"].in_table == "visits"
    assert paths["visits[].tags"].item_type == "string"
    assert paths["visits[].doc.name"].in_table == "visits"
    assert paths["visits[].meds"].in_table == "visits"
    assert paths["visits[].meds[].n"].in_table == "visits[].meds"


def test_enum_items_and_non_string_enum():
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"enum": ["a", "b"]}},
            "level": {"type": "integer", "enum": [1, 2]},
            "flag": {"enum": [True, "x"]},
        },
    }
    paths = by_path(flatten_schema(schema))
    assert paths["tags"].item_type == "enum"
    assert paths["tags"].enum == ["a", "b"]
    assert paths["level"].type == "enum"
    assert paths["level"].enum == ["1", "2"]
    assert paths["flag"].enum == ["true", "x"]


def test_missing_type_is_inferred():
    schema = {
        "properties": {
            "o": {"properties": {"x": {}}},
            "l": {"items": {"type": "string"}},
        }
    }
    paths = by_path(flatten_schema(schema))
    assert paths["o"].type == "object"
    assert paths["o.x"].type == "string"
    assert paths["l"].type == "array"


def test_array_of_arrays():
    schema = {
        "type": "object",
        "properties": {"grid": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}},
    }
    paths = by_path(flatten_schema(schema))
    assert paths["grid"].item_type == "array"
    assert paths["grid[]"].type == "array"
    assert paths["grid[]"].item_type == "integer"


def test_empty_schema_gives_no_paths():
    assert flatten_schema({"type": "object"}) == []


def test_non_object_root_raises():
    with pytest.raises(FormcastError):
        flatten_schema({"type": "array", "items": {"type": "string"}})


# ---- path syntax ----


@pytest.mark.parametrize(
    "path, segments",
    [
        ("a", ["a"]),
        ("a.b", ["a", "b"]),
        ("a[].b", ["a", ARRAY_ITEM, "b"]),
        ('applicant["first name"]', ["applicant", "first name"]),
        ('["first name"].x', ["first name", "x"]),
        ('a["x.y"][].b', ["a", "x.y", ARRAY_ITEM, "b"]),
        ('a["q\\"uote"]', ["a", 'q"uote']),
        ('a["b\\\\c"]', ["a", "b\\c"]),
        ('a["[]"]', ["a", "[]"]),
        ("a[][]", ["a", ARRAY_ITEM, ARRAY_ITEM]),
    ],
)
def test_parse_and_format_round_trip(path, segments):
    parsed = parse_path(path)
    assert parsed == segments
    assert format_path(parsed) == path


def test_property_named_like_array_marker_is_distinguishable():
    parsed = parse_path('a["[]"]')
    assert parsed[1] is not ARRAY_ITEM
    assert parse_path("a[]")[1] is ARRAY_ITEM


@pytest.mark.parametrize("name", ["a.b", "a[b", 'a"b', "a b", "", "a]b", "back\\slash", "tab\tx"])
def test_awkward_property_names_flatten_and_round_trip(name):
    schema = {"type": "object", "properties": {name: {"properties": {"x": {"type": "string"}}}}}
    paths = flatten_schema(schema)
    inner = [p for p in paths if p.path != paths[0].path][0]
    assert parse_path(inner.path) == [name, "x"]
    assert resolve(paths, format_path([name])).type == "object"


@pytest.mark.parametrize("bad", ["", "a.", ".a", "a..b", 'a["x', "a[x]", 'a["x"', 'a["x"]b', "a["])
def test_parse_path_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_path(bad)


# ---- fix round 1 ----

_REUSE_DEFS = {
    "A": {"type": "object", "properties": {"q": {"type": "string"}}},
    "B": {"type": "object", "properties": {"x": {"$ref": "#/$defs/A"}}},
}


@pytest.mark.parametrize(
    "combo",
    [
        {"allOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}]},
        {"oneOf": [{"$ref": "#/$defs/B"}, {"$ref": "#/$defs/A"}]},
    ],
)
def test_ref_reused_by_sibling_branch_is_not_recursion(combo):
    schema = {"type": "object", "$defs": _REUSE_DEFS, "properties": {"p": combo}}
    paths = by_path(flatten_schema(schema))
    assert paths["p.x"].type == "object"
    assert paths["p.x.q"].type == "string"


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "object", "properties": {"a": {"type": "nonsense"}}},
        {"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}},
        {"type": "object", "properties": {"a": {"$ref": "http://example.com/x.json"}}},
    ],
    ids=["invalid-schema", "dangling-ref", "remote-ref"],
)
def test_validate_data_bad_schema_raises_formcast_error(bad):
    with pytest.raises(FormcastError):
        validate_data(bad, {"a": 1})


@pytest.mark.parametrize(
    "wrap",
    [
        lambda r: {"allOf": [r]},
        lambda r: {"oneOf": [r, {"type": "null"}]},
        lambda r: {"anyOf": [{"type": "null"}, r]},
    ],
    ids=["allOf", "oneOf-nullable", "anyOf-nullable"],
)
def test_cycle_reached_only_through_union_branches_terminates(wrap):
    ref = {"$ref": "#/$defs/B"}
    schema = {
        "type": "object",
        "$defs": {"B": {"type": "object", "properties": {"x": wrap(ref)}}},
        "properties": {"p": wrap(ref)},
    }
    assert [p.path for p in flatten_schema(schema)] == ["p", "p.x"]


def test_cycle_through_array_items_union_terminates():
    ref = {"$ref": "#/$defs/B"}
    schema = {
        "type": "object",
        "$defs": {"B": {"type": "object", "properties": {"kids": {"type": "array", "items": {"allOf": [ref]}}}}},
        "properties": {"p": {"oneOf": [ref, {"type": "null"}]}},
    }
    paths = [p.path for p in flatten_schema(schema)]
    assert paths[:2] == ["p", "p.kids"]
