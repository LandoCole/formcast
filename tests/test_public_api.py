"""The public API as the spec (section 8) shows it."""

import pytest

import formcast
from formcast import (
    OpContext,
    OpError,
    SessionBudget,
    SessionResult,
    Template,
    apply,
    build_form_map,
    coverage,
    flatten_schema,
    ops,
)
from formcast.template.model import Field, FormInfo, new_template

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "dob": {"type": "string"}}}


def _tpl():
    return Template(
        template_id="t1",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=1, page_sizes=[(612.0, 792.0)], kinds=["digital"]),
        fields=[Field(id="f1", type="text", page=0, rect=(100, 100, 200, 120))],
    )


def test_spec_section_8_human_edit():
    tpl = _tpl()
    tpl, warnings = apply(tpl, ops.Move("f1", dx=4), author="human")
    assert warnings == []
    assert tpl.fields[0].rect == (104, 100, 204, 120)
    assert tpl.history[-1].author == "human" and tpl.history[-1].op == {"op": "move", "id": "f1", "dx": 4.0}
    assert Template.from_json(tpl.to_json()) == tpl


def test_apply_without_context_validates_against_the_template():
    tpl = _tpl()
    tpl, _ = apply(tpl, ops.SetSource("f1", source={"paths": ["dob"]}), author="human")
    assert tpl.fields[0].source.paths == ["dob"]
    with pytest.raises(OpError, match="unknown schema path 'dobb'"):
        apply(tpl, {"op": "set_source", "id": "f1", "source": {"paths": ["dobb"]}}, author="human")
    with pytest.raises(OpError, match="page 1 does not exist"):
        apply(tpl, ops.AddField(type="text", page=1, rect=(10, 10, 50, 30)), author="human")


def test_positional_id_and_keyword_id_are_the_same_op():
    assert ops.Move("f1", dx=4) == ops.Move(id="f1", dx=4)
    assert ops.LinkWidget("f1", widget=None) == ops.LinkWidget(id="f1", widget=None)
    with pytest.raises(TypeError, match="id"):
        ops.Move("f1", id="f2", dx=4)


def test_op_context_from_template(fillable_form_pdf):
    fm = build_form_map(fillable_form_pdf, detectors={})
    tpl = new_template(SCHEMA, fm)
    ctx = OpContext.from_template(tpl)
    assert [sp.path for sp in ctx.schema_paths] == [sp.path for sp in flatten_schema(SCHEMA)]
    assert ctx.page_sizes == tpl.form.page_sizes
    assert ctx.widget_names is None
    assert OpContext.from_template(tpl, fm).widget_names == ["applicant_name", "agree"]
    linked = tpl.fields[0].id
    with pytest.raises(OpError, match="unknown widget 'nope'"):
        apply(tpl, ops.LinkWidget(linked, widget="nope"), author="human", context=OpContext.from_template(tpl, fm))


def test_package_exports():
    for name in ("OpContext", "ops", "flatten_schema", "coverage", "SessionBudget", "SessionResult"):
        assert name in formcast.__all__ and name in dir(formcast)
    assert ops is formcast.template.ops
    assert callable(coverage) and SessionBudget().max_steps > 0 and SessionResult.__name__ == "SessionResult"
