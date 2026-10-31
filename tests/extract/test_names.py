"""Field names made from a category and a title."""

import pytest

from formcast.extract.names import derived_path, field_key, normalized
from formcast.schema import REASON_KEY


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("City, state, ZIP", "city_state_zip"),
        ("Date of birth", "date_of_birth"),
        ("1099 box", "f_1099_box"),
        ("Prénom", "prenom"),
        ("#", ""),
        ("名前", ""),
        ("  Taxpayer   ID  ", "taxpayer_id"),
    ],
)
def test_field_key_makes_a_key_from_a_title(text, key):
    assert field_key(text) == key


def test_derived_path_is_category_dot_title_and_the_category_may_be_blank():
    assert derived_path("Investor", "Taxpayer ID") == "investor.taxpayer_id"
    assert derived_path("", "Taxpayer ID") == "taxpayer_id"


def _text(title):
    return {"type": "string", "title": title}


def test_normalized_makes_keys_from_titles_and_says_where_each_field_went():
    schema = {
        "type": "object",
        "required": ["tin"],
        "properties": {
            "tin": _text("Taxpayer ID"),
            "inv": {
                "type": "object", "title": "Investor", "required": ["n"],
                "properties": {"n": {**_text("Legal name"), REASON_KEY: "the form says so"}, "d": _text("Date of birth")},
            },
            "own": {
                "type": "array", "title": "Owners", "maxItems": 4,
                "items": {"type": "object", "properties": {"n": _text("Owner name")}},
            },
        },
    }  # fmt: skip
    out, moved = normalized(schema)
    assert list(out["properties"]) == ["taxpayer_id", "investor", "owners"]
    assert out["required"] == ["taxpayer_id"]
    investor = out["properties"]["investor"]
    assert list(investor["properties"]) == ["legal_name", "date_of_birth"] and investor["required"] == ["legal_name"]
    assert investor["properties"]["legal_name"][REASON_KEY] == "the form says so"
    assert out["properties"]["owners"]["maxItems"] == 4
    assert moved == {
        "tin": "taxpayer_id", "inv.n": "investor.legal_name", "inv.d": "investor.date_of_birth",
        "own[].n": "owners[].owner_name",
    }  # fmt: skip
    assert "tin" in schema["properties"]  # the schema given is left alone


def test_two_fields_with_one_title_are_both_kept():
    out, moved = normalized({"type": "object", "properties": {"a": _text("Date"), "b": _text("Date")}})
    assert list(out["properties"]) == ["date", "date_2"]
    assert moved == {"a": "date", "b": "date_2"}


def test_a_field_with_no_key_in_its_title_keeps_its_own():
    out, _ = normalized({"type": "object", "properties": {"given_name": _text("名前"), "x": {"type": "string"}}})
    assert list(out["properties"]) == ["given_name", "x"]


def test_nothing_is_nested_deeper_than_a_category():
    schema = {
        "type": "object",
        "properties": {
            "holder": {
                "type": "object", "title": "Holder",
                "properties": {
                    "address": {
                        "type": "object", "required": ["street"],
                        "properties": {"street": _text("Street"), "city": _text("City")},
                    },
                },
            },
        },
    }  # fmt: skip
    out, moved = normalized(schema)
    holder = out["properties"]["holder"]
    assert list(holder["properties"]) == ["street", "city"] and holder["required"] == ["street"]
    assert moved == {"holder.address.street": "holder.street", "holder.address.city": "holder.city"}


def test_a_field_written_with_a_ref_is_named_too():
    schema = {
        "type": "object",
        "$defs": {"money": {"type": "number"}},
        "properties": {
            "a": {"$ref": "#/$defs/money", "title": "Amount due"},
            "g": {"type": "object", "title": "Fees", "properties": {"b": {"$ref": "#/$defs/money", "title": "Late fee"}}},
        },
    }  # fmt: skip
    out, moved = normalized(schema)
    assert moved == {"a": "amount_due", "g.b": "fees.late_fee"} and out["$defs"] == schema["$defs"]


def test_a_reserved_or_used_top_level_name_is_not_taken():
    out, _ = normalized(
        {"type": "object", "properties": {"a": _text("Document name"), "b": _text("Phone")}}, taken=["phone"]
    )
    assert list(out["properties"]) == ["document_name_2", "phone_2"]
