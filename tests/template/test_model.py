import json

import pytest

from formcast.pagemap.query import FormMapQuery
from pydantic import ValidationError

from formcast.pagemap.build import build_form_map
from formcast.template.model import (
    Column,
    Field,
    FormInfo,
    HistoryEntry,
    Source,
    Style,
    TableSegment,
    Template,
    new_template,
)
from formcast.transforms import Transform

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}


def _segment():
    return TableSegment(
        page=0,
        rect=(72, 300, 540, 500),
        row_top=310.0,
        row_height=18.0,
        max_rows=10,
        columns=[Column(key="item", x=(72, 200), transforms=[Transform(op="upper")], wrap=20)],
    )


def _template():
    return Template(
        template_id="abc123",
        schema=SCHEMA,
        form=FormInfo(
            fingerprint="fp", page_count=1, page_sizes=[(612.0, 792.0)], kinds=["digital"]
        ),
        fields=[
            Field(
                id="f1",
                type="text",
                label="Name",
                page=0,
                rect=(120, 86, 300, 100),
                source=Source(
                    paths=["name", "aliases"],
                    transforms=[Transform(op="upper")],
                    join=", ",
                    when=Transform(op="truthy"),
                ),
                style=Style(font_size=10, align="center", comb=8, multiline=True),
                confidence=0.9,
                origin="detector",
            ),
            Field(id="f2", type="table", segments=[_segment()]),
        ],
        history=[
            HistoryEntry(
                op={"op": "add_field"}, author="agent", at="2026-10-20T00:00:00+00:00", note="n"
            )
        ],
    )


def test_json_round_trip_equality():
    t = _template()
    assert Template.from_json(t.to_json()) == t


def test_schema_serialized_under_schema_key():
    data = json.loads(_template().to_json())
    assert data["schema"] == SCHEMA
    assert "schema_" not in data
    assert data["formcast_version"] == "1"


def test_schema_loads_from_schema_key_and_by_field_name():
    t = _template()
    assert Template.from_json(t.to_json()).schema_ == SCHEMA
    assert Template(**{**t.model_dump(), "schema_": SCHEMA}).schema_ == SCHEMA


@pytest.mark.parametrize("missing", ["page", "rect"])
def test_text_field_requires_page_and_rect(missing):
    kwargs = {"id": "f1", "type": "text", "page": 0, "rect": (0, 0, 10, 10)}
    del kwargs[missing]
    with pytest.raises(ValidationError):
        Field(**kwargs)


@pytest.mark.parametrize("kind", ["checkbox", "radio_option", "signature", "choice_mark"])
def test_every_non_table_type_requires_rect(kind):
    with pytest.raises(ValidationError):
        Field(id="f1", type=kind)


def test_table_without_segments_raises():
    with pytest.raises(ValidationError):
        Field(id="f1", type="table")
    assert Field(id="f1", type="table", segments=[_segment()]).page is None


def test_defaults_are_independent_instances():
    a = Field(id="f1", type="text", page=0, rect=(0, 0, 1, 1))
    b = Field(id="f2", type="text", page=0, rect=(0, 0, 1, 1))
    assert a.style == Style() and a.style is not b.style
    assert a.origin == "agent"
    assert a.segments == [] and a.segments is not b.segments


def test_template_requires_version_one():
    data = json.loads(_template().to_json())
    data["formcast_version"] = "2"
    with pytest.raises(ValidationError):
        Template.model_validate(data)


def test_new_template_on_fillable_form(fillable_form_pdf):
    fm = build_form_map(fillable_form_pdf)
    t = new_template(SCHEMA, fm)
    assert t.schema_ == SCHEMA
    assert t.form.fingerprint == fm.fingerprint
    assert t.form.page_count == 1
    assert t.form.page_sizes == [(612.0, 792.0)]
    assert t.form.kinds == ["fillable"]
    assert t.history == []
    assert [f.existing_widget for f in t.fields] == ["applicant_name", "agree"]
    widget_ids = {w["name"]: w["id"] for w in FormMapQuery(fm).widgets()}
    assert [f.id for f in t.fields] == [widget_ids["applicant_name"], widget_ids["agree"]]
    assert [f.type for f in t.fields] == ["text", "checkbox"]
    assert all(f.origin == "widget" and f.source is None and f.page == 0 for f in t.fields)
    assert t.fields[0].rect == pytest.approx((120, 86, 300, 100), abs=1.0)
    assert len(t.template_id) == 32 and int(t.template_id, 16) >= 0
    assert Template.from_json(t.to_json()) == t


def test_new_template_ids_are_unique(fillable_form_pdf):
    fm = build_form_map(fillable_form_pdf)
    assert new_template(SCHEMA, fm).template_id != new_template(SCHEMA, fm).template_id


def test_new_template_maps_widget_kinds_and_skips_buttons():
    from formcast.pagemap.model import FormMap, PageMap, Widget

    def w(name, kind, y):
        return Widget(name=name, kind=kind, rect=(10, y, 50, y + 10))

    page = PageMap(
        index=0, width=100, height=200, rotation=0, kind="fillable", text_source="pdf",
        words=[], lines=[], rules=[],
        widgets=[
            w("sig", "signature", 90),
            w("go", "button", 80),
            w("pick", "combo", 70),
            w("r1", "radio", 60),
            w("lst", "list", 50),
            w("b", "checkbox", 40),
        ],
    )
    page2 = page.model_copy(update={"index": 1, "widgets": [w("late", "text", 5)]})
    t = new_template(SCHEMA, FormMap(pages=[page, page2], fingerprint="x"))
    # ids are the widgets' form-map ids (w<page>_<index on the page>), in reading order
    assert [(f.id, f.existing_widget, f.type, f.page) for f in t.fields] == [
        ("w0_5", "b", "checkbox", 0),
        ("w0_4", "lst", "text", 0),
        ("w0_3", "r1", "radio_option", 0),
        ("w0_2", "pick", "text", 0),
        ("w0_0", "sig", "signature", 0),
        ("w1_0", "late", "text", 1),
    ]


def _cand_page():
    from formcast.pagemap.model import Candidate, PageMap, TextLine

    lines = [TextLine(id=0, text="Full name:", rect=(10, 20, 60, 30), word_ids=[], font_size=10)]
    cands = [
        Candidate(id="c0_0", kind="text", rect=(65, 18, 200, 32), conf=0.9, detector="d",
                  label_line_id=0, label_relation="left"),
        Candidate(id="c0_1", kind="choice", rect=(10, 50, 20, 60), conf=0.4, detector="d"),
        Candidate(id="c0_2", kind="signature", rect=(10, 80, 200, 95), conf=0.7, detector="d"),
    ]
    return PageMap(index=0, width=300, height=200, rotation=0, kind="digital", text_source="pdf",
                   words=[], lines=lines, rules=[], widgets=[], candidates=cands)


def test_new_template_seeds_one_field_per_detector_candidate_when_asked():
    from formcast.pagemap.model import FormMap

    fm = FormMap(pages=[_cand_page()], fingerprint="x")
    assert new_template(SCHEMA, fm).fields == []
    t = new_template(SCHEMA, fm, candidates=True)
    assert [(f.id, f.type, f.origin, f.confidence) for f in t.fields] == [
        ("c0_0", "text", "detector", 0.9),
        ("c0_1", "checkbox", "detector", 0.4),
        ("c0_2", "signature", "detector", 0.7),
    ]
    assert t.fields[0].label == "Full name:" and t.fields[1].label == ""
    assert t.fields[0].rect == (65, 18, 200, 32) and all(f.source is None for f in t.fields)


# ------------------------------------------------------------------ style validation


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -3])
def test_style_rejects_bad_font_size(bad):
    with pytest.raises(ValidationError):
        Style(font_size=bad)
    with pytest.raises(ValidationError):
        Style(min_font_size=bad)


def test_style_rejects_min_above_size():
    with pytest.raises(ValidationError):
        Style(font_size=8, min_font_size=9)
    assert Style(font_size="auto", min_font_size=9).min_font_size == 9


def test_template_with_nan_style_does_not_load():
    d = json.loads(_template().to_json())
    d["fields"][0]["style"]["font_size"] = float("nan")
    with pytest.raises(ValidationError):
        Template.model_validate(d)


def test_widget_field_types_table():
    from formcast.template.model import WIDGET_FIELD_TYPES

    assert WIDGET_FIELD_TYPES == {
        "text": frozenset({"text", "signature"}),
        "combo": frozenset({"text"}),
        "list": frozenset({"text"}),
        "checkbox": frozenset({"checkbox", "radio_option"}),
        "radio": frozenset({"radio_option", "checkbox"}),
        "signature": frozenset({"signature"}),
        "button": frozenset(),
    }
