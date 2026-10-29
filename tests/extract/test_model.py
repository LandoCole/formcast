"""The extraction template and result models: shape, round trip, and the checks a template must pass."""

import pytest
from pydantic import ValidationError

from formcast.data.mapping import Transform
from formcast.extract.model import (
    DEFAULT_ITEMS,
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    DocumentResult,
    ExtractionTemplate,
    FieldResult,
    Layout,
    PhraseRead,
    Reader,
    StaticLine,
    WidgetRead,
    concrete_paths,
)
from formcast.template.model import FormInfo

SCHEMA = {
    "type": "object",
    "properties": {
        "investor": {
            "type": "object",
            "properties": {
                "tax_id": {"type": "string"},
                "dob": {"type": "string", "format": "date"},
                "kind": {"type": "string", "enum": ["individual", "joint"]},
                "ack": {"type": "string"},
            },
        },
        "owners": {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    },
}
FORM = FormInfo(fingerprint="abc", page_count=1, page_sizes=[(612.0, 792.0)], kinds=["text"])


def _template(readers, absent=()):
    return ExtractionTemplate(
        template_id="t1",
        name="Client information",
        schema=SCHEMA,
        form=FORM,
        layout=Layout(lines=[StaticLine(page=0, text="taxpayer id", rect=(10, 10, 80, 20))], page_sizes=[(612.0, 792.0)]),
        readers=readers,
        absent=list(absent),
    )


def test_a_template_round_trips_through_json():
    t = _template(
        {
            "investor.tax_id": Reader(
                strategies=[
                    WidgetRead(names=["tin", "TaxID"]),
                    AnchorRead(labels=["Taxpayer ID", "TIN"], page=0, side="below", max_lines=2),
                    PhraseRead(phrases=["my tax id is"], stop="digits"),
                ],
                clean=[Transform(op="digits")],
            ),
            "investor.dob": Reader(strategies=[BoxRead(page=0, rect=(100, 100, 200, 120))]),
            "investor.kind": Reader(
                strategies=[
                    ChoiceRead(
                        options={
                            "individual": ChoiceMark(widget="kind_individual"),
                            "joint": ChoiceMark(page=0, rect=(5, 5, 15, 15)),
                        }
                    )
                ]
            ),
        },
        absent=["investor.ack"],
    )
    text = t.model_dump_json(by_alias=True)
    assert '"schema"' in text and "schema_" not in text
    assert ExtractionTemplate.model_validate_json(text) == t
    assert t.formcast_extract_version == "1"


def test_strategies_are_told_apart_by_kind():
    r = Reader.model_validate({"strategies": [{"kind": "widget", "names": ["a"]}, {"kind": "phrase", "phrases": ["x"]}]})
    assert isinstance(r.strategies[0], WidgetRead)
    assert r.strategies[1].stop == "comma"
    assert r.clean == []
    with pytest.raises(ValidationError):
        Reader.model_validate({"strategies": [{"kind": "psychic"}]})


def test_a_strategy_needs_at_least_one_name_label_or_phrase():
    with pytest.raises(ValidationError):
        WidgetRead(names=[])
    with pytest.raises(ValidationError):
        AnchorRead(labels=[])
    with pytest.raises(ValidationError):
        PhraseRead(phrases=[])


def test_models_refuse_unknown_keys():
    with pytest.raises(ValidationError):
        WidgetRead(names=["a"], colour="red")


def test_concrete_paths_expand_array_items_up_to_max_items():
    schema = {
        "type": "object",
        "properties": {
            "investor": {"type": "object", "properties": {"tax_id": {"type": "string"}}},
            "owners": {
                "type": "array",
                "maxItems": 2,
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        },
    }
    assert concrete_paths(schema) == ["investor.tax_id", "owners[0].name", "owners[1].name"]


def test_an_array_without_max_items_gets_four_items():
    schema = {
        "type": "object",
        "properties": {
            "refs": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}, "phone": {"type": "string"}}},
            }
        },
    }
    paths = concrete_paths(schema)
    assert DEFAULT_ITEMS == 4
    assert paths[0] == "refs[0].name" and paths[1] == "refs[0].phone"
    assert len(paths) == 8 and paths[-1] == "refs[3].phone"


def test_items_come_in_order_with_all_their_fields_and_groups_can_nest():
    item = {"type": "object", "properties": {"z": {"type": "string"}}}
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "o": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "string"},
                        "k": {"type": "array", "maxItems": 2, "items": item},
                        "d": {"type": "string"},
                    },
                },
            },
            "b": {"type": "string"},
        },
    }
    assert concrete_paths(schema) == [
        "a", "o[0].n", "o[0].k[0].z", "o[0].k[1].z", "o[0].d", "o[1].n", "o[1].k[0].z", "o[1].k[1].z", "o[1].d", "b",
    ]  # fmt: skip


def test_a_list_of_plain_values_is_one_path():
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}, "n": {"type": "integer"}},
    }
    assert concrete_paths(schema) == ["tags", "n"]


def test_a_reader_for_a_path_not_in_the_schema_is_refused():
    with pytest.raises(ValueError, match=r"investor\.nope"):
        _template({"investor.nope": Reader(strategies=[WidgetRead(names=["x"])])})


def test_a_reader_for_an_item_past_max_items_is_refused():
    with pytest.raises(ValueError, match=r"owners\[2\]\.name"):
        _template({"owners[2].name": Reader(strategies=[WidgetRead(names=["x"])])})


def test_an_absent_path_must_be_a_schema_leaf():
    _template({}, absent=["investor.ack", "owners[1].name"])
    with pytest.raises(ValueError, match=r"investor\.nope"):
        _template({}, absent=["investor.nope"])
    with pytest.raises(ValueError, match="investor"):
        _template({}, absent=["investor"])


def test_a_choice_mark_takes_a_widget_or_a_page_and_rect_not_both():
    ChoiceMark(widget="w")
    ChoiceMark(page=0, rect=(1, 2, 3, 4))
    bad_marks = (
        {},
        {"widget": "w", "page": 0, "rect": (1, 2, 3, 4)},
        {"page": 0},
        {"rect": (1, 2, 3, 4)},
        {"widget": "w", "page": 0},
    )
    for bad in bad_marks:
        with pytest.raises(ValidationError):
            ChoiceMark(**bad)


def _result(*fields):
    return DocumentResult(
        doc_id="d1",
        file="a.pdf",
        template_id="t1",
        matched="fingerprint",
        fields=list(fields),
        extracted_at="2026-10-28T00:00:00Z",
    )


def test_document_result_record_nests_the_values_it_has():
    result = _result(
        FieldResult(path="investor.tax_id", status="found", raw="84-2219037", value="84-2219037"),
        FieldResult(path="owners[1].name", status="found", raw="Ben", value="Ben"),
        FieldResult(path="investor.dob", status="missing", raw=None),
    )
    assert result.record() == {"investor": {"tax_id": "84-2219037"}, "owners": [{"name": "Ben"}]}


def test_record_keeps_unsure_and_failed_check_values_and_leaves_out_the_rest():
    result = _result(
        FieldResult(path="a", status="unsure", raw="x", value="x", reasons=["label twice"]),
        FieldResult(path="b", status="failed_check", raw="9", value="9", reasons=["not a date"]),
        FieldResult(path="c", status="absent", raw=None),
        FieldResult(path="d", status="not_on_form", raw=None),
        FieldResult(path="e", status="found", raw="", value=None),
    )
    assert result.record() == {"a": "x", "b": "9"}


def test_a_failed_document_has_no_fields_and_an_error():
    r = DocumentResult(
        doc_id="d", file="x.pdf", template_id=None, matched="none", fields=[], error="unreadable: encrypted", extracted_at="t"
    )
    assert r.record() == {}
    assert DocumentResult.model_validate_json(r.model_dump_json()) == r
