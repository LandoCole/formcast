import datetime
import json

import pytest

from formcast import transforms
from formcast.errors import FormcastError
from formcast.transforms import (
    Transform,
    apply_chain,
    list_transforms,
    register_transform,
    validate_chain,
)


def T(op: str, **args) -> Transform:
    return Transform(op=op, args=args)


@pytest.fixture
def clean_registry():
    snapshot = dict(transforms._REGISTRY)
    yield
    transforms._REGISTRY.clear()
    transforms._REGISTRY.update(snapshot)


# --- brief examples -------------------------------------------------------


def test_date_part_month_padded():
    assert apply_chain(["2024-03-07"], [T("date_part", part="month", pad=2)]) == "03"


def test_join_then_upper():
    assert apply_chain(["Ada", "Lovelace"], [T("join", sep=" "), T("upper")]) == "ADA LOVELACE"


def test_split_index():
    assert apply_chain(["123-45-6789"], [T("split", sep="-", index=2)]) == "6789"


def test_number_format_default():
    assert apply_chain([1234.5], [T("number_format")]) == "1,234.50"


def test_equals_true():
    assert apply_chain(["married"], [T("equals", value="married")]) is True


def test_contains_list():
    assert apply_chain([["email", "sms"]], [T("contains", value="email")]) is True


def test_validate_unknown_op():
    assert validate_chain([Transform(op="nope")]) == ["unknown transform 'nope'"]


def test_validate_bad_args():
    errs = validate_chain([T("date_part", part="week")])
    assert len(errs) == 1
    assert "date_part" in errs[0] and "week" in errs[0]


def test_register_custom_transform(clean_registry):
    register_transform(
        "repeat",
        lambda value, n: None if value is None else value * n,
        {
            "type": "object",
            "properties": {"n": {"type": "integer", "minimum": 1}},
            "required": ["n"],
            "additionalProperties": False,
        },
    )
    assert validate_chain([T("repeat", n=2)]) == []
    assert apply_chain(["ab"], [T("repeat", n=2)]) == "abab"
    assert validate_chain([T("repeat", n=0)]) != []
    assert "repeat" in [t["name"] for t in list_transforms()]


def test_register_duplicate_rejected(clean_registry):
    with pytest.raises(FormcastError, match="upper"):
        register_transform("upper", lambda v: v, {"type": "object"})


def test_register_replace_and_bad_schema(clean_registry):
    register_transform("upper", lambda v: "X", {"type": "object"}, replace=True)
    assert apply_chain(["a"], [T("upper")]) == "X"
    with pytest.raises(FormcastError):
        register_transform("broken", lambda v: v, {"type": "not-a-type"})


def test_custom_transform_exception_is_wrapped(clean_registry):
    def boom(value):
        raise RuntimeError("kaboom")

    register_transform("boom", boom, {"type": "object"})
    with pytest.raises(FormcastError, match="boom.*kaboom"):
        apply_chain(["a"], [T("boom")])


# --- chain semantics ------------------------------------------------------


def test_transform_model_defaults_not_shared():
    a, b = Transform(op="upper"), Transform(op="upper")
    a.args["x"] = 1
    assert b.args == {}


def test_transform_model_roundtrips_json():
    t = T("split", sep="-", index=0)
    assert Transform.model_validate(json.loads(t.model_dump_json())) == t


def test_empty_chain_unwraps_single_value():
    assert apply_chain(["x"], []) == "x"


def test_empty_chain_multiple_values_returns_list():
    assert apply_chain(["x", "y"], []) == ["x", "y"]


def test_no_values_gives_none():
    assert apply_chain([], [T("upper")]) is None


def test_non_join_op_maps_over_multiple_values():
    assert apply_chain(["a", "b"], [T("upper")]) == ["A", "B"]


def test_join_after_map_is_applied_on_list():
    assert apply_chain(["a", "b"], [T("upper"), T("join", sep="+")]) == "A+B"


def test_join_of_single_list_value():
    assert apply_chain([["a", "b"]], [T("join", sep=", ")]) == "a, b"


def test_join_skips_missing_and_empty_parts():
    assert apply_chain(["Ada", None, "", "Lovelace"], [T("join", sep=" ")]) == "Ada Lovelace"


def test_join_all_missing_is_none():
    assert apply_chain([None, ""], [T("join", sep=" ")]) is None


def test_join_stringifies_numbers():
    assert apply_chain([1, 2], [T("join", sep="/")]) == "1/2"


def test_join_not_first_after_scalar():
    # a scalar is joined as a one-element list
    assert apply_chain(["a"], [T("upper"), T("join", sep="-")]) == "A"


def test_apply_chain_does_not_mutate_input():
    values = [["b", "a"]]
    apply_chain(values, [T("slice", start=0, end=1)])
    assert values == [["b", "a"]]


def test_apply_chain_invalid_chain_raises_before_running():
    with pytest.raises(FormcastError, match="unknown transform 'nope'"):
        apply_chain(["x"], [T("upper"), T("nope")])


def test_apply_chain_bad_args_raises():
    with pytest.raises(FormcastError, match="date_part"):
        apply_chain(["2024-03-07"], [T("date_part", part="week")])


def test_validate_chain_collects_all_errors():
    errs = validate_chain([T("nope"), T("split", sep="-"), T("upper")])
    assert errs[0] == "unknown transform 'nope'"
    assert len(errs) == 2 and "split" in errs[1]


def test_validate_rejects_extra_args():
    assert validate_chain([T("upper", bogus=1)]) != []


# --- built-in behavior ----------------------------------------------------


def run(op: Transform, value):
    return apply_chain([value], [op])


def test_split_out_of_range_is_empty_string():
    assert run(T("split", sep=" ", index=3), "Ada Lovelace") == ""
    assert run(T("split", sep=" ", index=-1), "Ada Lovelace") == "Lovelace"


def test_split_requires_nonempty_sep():
    assert validate_chain([T("split", sep="", index=0)]) != []


def test_slice():
    assert run(T("slice", start=1, end=3), "abcdef") == "bc"
    assert run(T("slice", start=2), "abcdef") == "cdef"
    assert run(T("slice", end=2), "abcdef") == "ab"
    assert run(T("slice", start=0, end=2), ["a", "b", "c"]) == ["a", "b"]


def test_char_at():
    assert run(T("char_at", index=1), "abc") == "b"
    assert run(T("char_at", index=-1), "abc") == "c"
    assert run(T("char_at", index=9), "abc") == ""


def test_case_and_strip():
    assert run(T("upper"), "ab") == "AB"
    assert run(T("lower"), "AB") == "ab"
    assert run(T("strip"), "  a b \n") == "a b"
    assert run(T("title"), "ada LOVELACE-king don't") == "Ada Lovelace-King Don't"


def test_string_ops_stringify_numbers():
    assert run(T("upper"), 12) == "12"


def test_string_ops_reject_containers():
    with pytest.raises(FormcastError, match="list"):
        run(T("upper"), ["a"])


def test_date_format():
    assert run(T("date_format", format="%m/%d/%Y"), "2024-03-07") == "03/07/2024"
    assert run(T("date_format", format="%d %b %Y"), datetime.date(2024, 3, 7)) == "07 Mar 2024"


@pytest.mark.parametrize(
    "part,pad,expected",
    [
        ("month", 0, "3"),
        ("month", 2, "03"),
        ("day", 2, "07"),
        ("day", 0, "7"),
        ("year", 0, "2024"),
        ("year2", 0, "24"),
        ("year", 6, "002024"),
    ],
)
def test_date_part(part, pad, expected):
    assert run(T("date_part", part=part, pad=pad), "2024-03-07") == expected


def test_date_part_year2_always_two_digits():
    assert run(T("date_part", part="year2"), datetime.date(2005, 1, 1)) == "05"


@pytest.mark.parametrize("bad", ["07/03/2024", "2024-3-7", "20240307", "2024-02-30", 20240307, " ", ["2024-03-07"]])
def test_date_ops_reject_bad_values(bad):
    with pytest.raises(FormcastError) as ei:
        run(T("date_part", part="day"), bad)
    assert repr(bad) in str(ei.value)
    with pytest.raises(FormcastError):
        run(T("date_format", format="%Y"), bad)


@pytest.mark.parametrize(
    "op",
    [T("date_format", format="%Y"), T("date_part", part="month", pad=2), T("number_format", decimals=2, prefix="$")],
)
def test_empty_string_passes_through_date_and_number_ops(op):
    assert run(op, "") == ""
    assert apply_chain([""], [op, T("default", value="N/A")]) == "N/A"


@pytest.mark.parametrize(
    "value,args,expected",
    [
        (1234.5, {}, "1,234.50"),
        (1234567, {"decimals": 0}, "1,234,567"),
        ("1234.5", {}, "1,234.50"),
        (0.125, {"decimals": 2}, "0.13"),  # half-up, not banker's
        (2.675, {"decimals": 2}, "2.68"),
        (-1234.5, {"prefix": "$"}, "-$1,234.50"),
        (-0.001, {}, "0.00"),
        (1234.5, {"thousands": ""}, "1234.50"),
        (1234.5, {"thousands": " ", "prefix": "~"}, "~1 234.50"),
        (12, {"decimals": 0}, "12"),
        (10**30, {"decimals": 0}, "1," + ",".join(["000"] * 10)),
    ],
)
def test_number_format(value, args, expected):
    assert run(T("number_format", **args), value) == expected


@pytest.mark.parametrize("bad", ["abc", " ", "1,234", "nan", "inf", "1_000", True, ["1"]])
def test_number_format_rejects_bad_values(bad):
    with pytest.raises(FormcastError) as ei:
        run(T("number_format"), bad)
    assert repr(bad) in str(ei.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (None, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("Y", True),
        ("true", True),
        ("no", False),
        ("False", False),
        ("0", False),
        ("", False),
        ("anything else", True),
        ([], False),
        (["a"], True),
    ],
)
def test_bool_to_check(value, expected):
    assert run(T("bool_to_check"), value) is expected


def test_equals_and_contains():
    assert run(T("equals", value="a"), "b") is False
    assert run(T("equals", value=3), 3) is True
    assert run(T("equals", value=None), None) is False
    assert run(T("contains", value="x"), ["a"]) is False
    assert run(T("contains", value="ell"), "hello") is True
    assert run(T("contains", value=1), "hello") is False
    assert run(T("contains", value="k"), {"k": 1}) is True


def test_contains_rejects_scalars():
    with pytest.raises(FormcastError):
        run(T("contains", value=1), 12)


def test_default_replaces_none_and_empty_only():
    assert run(T("default", value="N/A"), None) == "N/A"
    assert run(T("default", value="N/A"), "") == "N/A"
    assert run(T("default", value="N/A"), "x") == "x"
    assert run(T("default", value="N/A"), 0) == 0
    assert run(T("default", value="N/A"), False) is False


def test_prefix_suffix():
    assert run(T("prefix", value="#"), "42") == "#42"
    assert run(T("suffix", value=" kg"), 42) == "42 kg"
    # blank stays blank so decorations don't print on empty fields
    assert run(T("prefix", value="#"), "") == ""
    assert run(T("suffix", value="#"), "") == ""


# --- None handling --------------------------------------------------------

_NONE_ARGS = {
    "split": {"sep": "-", "index": 0},
    "join": {"sep": " "},
    "slice": {"start": 0, "end": 1},
    "char_at": {"index": 0},
    "date_format": {"format": "%Y"},
    "date_part": {"part": "month"},
    "equals": {"value": "x"},
    "contains": {"value": "x"},
    "default": {"value": "d"},
    "prefix": {"value": "p"},
    "suffix": {"value": "s"},
}
_NONE_EXPECTED = {
    "default": "d",
    "bool_to_check": False,
    "equals": False,
    "contains": False,
}


@pytest.mark.parametrize("name", [t["name"] for t in list_transforms()])
def test_every_builtin_handles_none(name):
    op = T(name, **_NONE_ARGS.get(name, {}))
    assert validate_chain([op]) == []
    expected = _NONE_EXPECTED.get(name)
    got = apply_chain([None], [op])
    assert got == expected and type(got) is type(expected)


def test_builtin_names_are_exact():
    assert {t["name"] for t in list_transforms()} == {
        "split", "join", "slice", "char_at", "upper", "lower", "title", "strip",
        "date_format", "date_part", "number_format", "bool_to_check", "equals",
        "contains", "default", "prefix", "suffix",
    }


# --- list_transforms ------------------------------------------------------


def test_list_transforms_shape():
    listing = list_transforms()
    assert listing, "no transforms listed"
    for entry in listing:
        assert set(entry) == {"name", "args_schema", "doc"}
        assert entry["doc"] and "\n" not in entry["doc"]
        assert len(entry["doc"]) <= 120
        assert entry["args_schema"]["type"] == "object"
    json.dumps(listing)  # must be serializable for the prompt


def test_list_transforms_is_a_copy():
    listing = list_transforms()
    listing[0]["args_schema"]["properties"] = "mangled"
    assert list_transforms()[0]["args_schema"].get("properties") != "mangled"
