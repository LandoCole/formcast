"""``rebind``: stamp a PDF's form info onto a template. Dev forms only; form maps are built without
detectors or OCR, so no model loads."""

from pathlib import Path

import pymupdf
import pytest

import formcast
from formcast import FormcastError, fill, rebind
from formcast.pagemap.build import DetectPolicy, build_form_map
from formcast.template.model import FormInfo, Template, form_info_for, new_template

ROOT = Path(__file__).resolve().parents[2]
FW4 = ROOT / "forms" / "dev" / "fw4.pdf"
MEDHIST = ROOT / "forms" / "dev" / "columbus_medhist.pdf"  # two letter pages, as is HEALTH
HEALTH = ROOT / "forms" / "dev" / "csudh_health.pdf"
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}
NO_DETECT = DetectPolicy(digital=(), scanned=())


def form_map(pdf):
    return build_form_map(pdf, detectors={}, policy=NO_DETECT, ocr="never")


def browser_style(pdf) -> Template:
    """A template as the browser makes it: fields, but no fingerprint, kinds or signature."""
    t = new_template(SCHEMA, form_map(pdf))
    t.form = FormInfo(fingerprint=None, page_count=t.form.page_count, page_sizes=t.form.page_sizes, kinds=[])
    return t


def edited_bytes(edit) -> bytes:
    doc = pymupdf.open(FW4)
    edit(doc)
    return doc.tobytes()


def test_form_info_for_matches_what_new_template_records():
    fm = form_map(FW4)
    info = form_info_for(fm)
    assert info == new_template(SCHEMA, fm).form
    assert info.fingerprint == fm.fingerprint and info.signature == fm.signature
    assert info.signature is not None


def test_rebind_stamps_fingerprint_kinds_and_signature_and_keeps_fields():
    fm = form_map(FW4)
    tpl = browser_style(FW4)
    assert tpl.form.fingerprint is None and tpl.form.signature is None
    out = rebind(tpl, FW4, form_map=fm)
    assert out.form == form_info_for(fm)
    assert out.form.fingerprint and out.form.kinds and out.form.signature is not None
    assert out.fields and out.fields == tpl.fields
    assert out.history == tpl.history and out.template_id == tpl.template_id and out.schema_ == tpl.schema_


def test_rebind_builds_the_form_map_from_a_path_or_bytes_when_not_given():
    tpl = browser_style(FW4)
    expected = form_info_for(form_map(FW4))
    assert rebind(tpl, FW4).form == expected
    assert rebind(tpl, FW4.read_bytes()).form == expected


def test_rebind_does_not_mutate_the_input():
    tpl = browser_style(FW4)
    before = tpl.model_dump_json()
    out = rebind(tpl, FW4)
    assert tpl.model_dump_json() == before
    assert out is not tpl and out.fields is not tpl.fields
    assert out.fields[0] is not tpl.fields[0] and out.form is not tpl.form


def test_a_checked_fill_works_after_rebind_and_refuses_before():
    tpl = browser_style(FW4)
    with pytest.raises(FormcastError, match=r"formcast\.rebind"):
        fill(tpl, FW4, {}, check_fingerprint=True)
    pdf_bytes, report = fill(rebind(tpl, FW4), FW4, {}, check_fingerprint=True)
    assert pdf_bytes.startswith(b"%PDF")
    assert not [w for w in report.warnings if w.code == "fingerprint_mismatch"]


def test_page_count_mismatch_raises_with_both_counts():
    tpl = browser_style(FW4)
    n = tpl.form.page_count
    assert n >= 2
    one_page = edited_bytes(lambda d: d.select([0]))
    with pytest.raises(FormcastError) as e:
        rebind(tpl, one_page)
    assert str(e.value) == f"cannot rebind: the template has {n} pages, this PDF has 1"


def test_page_size_mismatch_raises_naming_the_page():
    tpl = browser_style(FW4)
    w, h = tpl.form.page_sizes[1]
    bigger = edited_bytes(lambda d: d[1].set_mediabox(pymupdf.Rect(0, 0, w + 20, h)))
    with pytest.raises(FormcastError) as e:
        rebind(tpl, bigger)
    assert str(e.value) == f"cannot rebind: page 2 is {w:g}x{h:g} pt in the template and {w + 20:g}x{h:g} pt in this PDF"


def test_a_page_size_within_one_point_is_accepted():
    tpl = browser_style(FW4)
    w, h = tpl.form.page_sizes[0]
    nudged = edited_bytes(lambda d: d[0].set_mediabox(pymupdf.Rect(0, 0, w + 0.5, h - 0.5)))
    assert rebind(tpl, nudged).form.fingerprint


def test_rebind_onto_a_different_form_with_the_same_shape_stamps_that_forms_info():
    # rebind checks page count and sizes only; telling forms apart is similarity's job.
    tpl = browser_style(MEDHIST)
    out = rebind(tpl, HEALTH)
    assert out.form.fingerprint == form_map(HEALTH).fingerprint != form_map(MEDHIST).fingerprint
    assert out.fields == tpl.fields


def test_rebind_is_a_lazy_public_export():
    assert formcast.rebind is rebind
    assert "rebind" in formcast.__all__
