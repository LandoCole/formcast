"""The public API as the spec (section 8) shows it."""

import importlib
import subprocess
import sys
from pathlib import Path

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


def test_fill_exports():
    from formcast.render.fill import fill, to_smart_pdf
    from formcast.render.report import FillReport

    for name in ("fill", "to_smart_pdf", "FillReport"):
        assert name in formcast.__all__ and name in dir(formcast)
    assert formcast.fill is fill and formcast.to_smart_pdf is to_smart_pdf and formcast.FillReport is FillReport
    render = importlib.import_module("formcast.render")
    assert render.fill is fill and render.FillReport is FillReport


def test_match_exports_are_lazy_and_cover_scoring_many_templates(fillable_form_pdf):
    from formcast.match import PdfFeatures, Similarity, pdf_features, similarity

    for name in ("similarity", "Similarity", "pdf_features", "PdfFeatures", "fingerprint"):
        assert name in formcast.__all__ and name in dir(formcast)
    assert formcast.pdf_features is pdf_features and formcast.PdfFeatures is PdfFeatures
    assert formcast.similarity is similarity and formcast.Similarity is Similarity
    # spec section 8: read one PDF once and score it against many templates
    tpl = new_template(SCHEMA, build_form_map(fillable_form_pdf, detectors={}))
    feats = formcast.pdf_features(fillable_form_pdf)
    assert isinstance(feats, formcast.PdfFeatures)
    assert formcast.similarity(tpl, fillable_form_pdf, features=feats) == formcast.similarity(tpl, fillable_form_pdf)
    # lazy: importing formcast does not import formcast.match
    code = "import sys, formcast; assert 'formcast.match' not in sys.modules; formcast.pdf_features; assert 'formcast.match' in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1] / "src")
