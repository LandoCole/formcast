"""Templates made outside the library (e.g. the browser demo) carry ``form.fingerprint = None``."""

from __future__ import annotations

import json

import pytest

from formcast.errors import FormcastError
from formcast.render.fill import fill, to_smart_pdf
from formcast.template.model import Field, FormInfo, Source, Template

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}


@pytest.fixture
def blank_pdf(make_pdf):
    return make_pdf(text=[{"at": (72, 60), "text": "Intake form"}])


@pytest.fixture
def tpl_none() -> Template:
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint=None, page_count=1, page_sizes=[(612.0, 792.0)], kinds=["digital"]),
        fields=[Field(id="f_name", type="text", page=0, rect=(100, 100, 300, 116), source=Source(paths=["name"]))],
    )


def test_none_fingerprint_validates(tpl_none):
    d = json.loads(tpl_none.to_json())
    assert d["form"]["fingerprint"] is None
    assert Template.model_validate(d).form.fingerprint is None


def test_fill_refuses_none_fingerprint_when_checked(tpl_none, blank_pdf):
    with pytest.raises(FormcastError, match="rebind"):
        fill(tpl_none, blank_pdf, {})
    with pytest.raises(FormcastError, match="rebind"):
        to_smart_pdf(tpl_none, blank_pdf)


def test_fill_accepts_none_fingerprint_unchecked(tpl_none, blank_pdf):
    out, report = fill(tpl_none, blank_pdf, {"name": "Ada"}, check_fingerprint=False)
    assert "fingerprint_mismatch" not in report.counts and report.fingerprint_ok
    assert out
    assert to_smart_pdf(tpl_none, blank_pdf, check_fingerprint=False)
