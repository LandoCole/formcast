"""Reading the sample PDFs and what a model sees of them, with no detector model loaded."""

from __future__ import annotations

import re

import pytest

import formcast.extract.samples as samples_mod
from formcast.errors import FormcastError, SessionCancelled
from formcast.extract.samples import SamplePdf, detected_pages, read_samples, sample_images, sample_view
from formcast.pagemap.build import DetectPolicy
from tests.extract.corpus import clients, make_form

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))


def test_a_fillable_sample_keeps_its_widget_values_and_runs_no_detection():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="fillable"))], **NO_DETECTORS)
    assert doc.values["tax_id"] == p.tax_id and detected_pages(doc) == []


def test_detection_modes_choose_the_pages(monkeypatch):
    seen = []
    real = samples_mod.detect_on_page
    monkeypatch.setattr(
        samples_mod, "detect_on_page", lambda page, pm, **kw: seen.append(pm.index) or real(page, pm, **kw)
    )
    pdf = make_form(clients(1, seed=3)[0], kind="flattened")  # page 0 a form, page 1 prose
    (doc,) = read_samples([SamplePdf("a.pdf", pdf)], detect="auto", **NO_DETECTORS)
    assert seen == [0] and detected_pages(doc) == [0]
    seen.clear()
    read_samples([SamplePdf("a.pdf", pdf)], detect="all", **NO_DETECTORS)
    assert seen == [0, 1]
    seen.clear()
    (doc,) = read_samples([SamplePdf("a.pdf", pdf)], detect="manual", pages={0: [1, 7]}, **NO_DETECTORS)
    assert seen == [1] and detected_pages(doc) == [1]
    seen.clear()
    (doc,) = read_samples([SamplePdf("a.pdf", pdf)], detect="off", **NO_DETECTORS)
    assert seen == [] and detected_pages(doc) == []


def test_an_unreadable_sample_is_refused_by_name():
    with pytest.raises(FormcastError, match="b.pdf: cannot open PDF"):
        read_samples([SamplePdf("b.pdf", b"not a pdf")], **NO_DETECTORS)


def test_a_cancel_while_the_samples_are_read_or_searched_comes_out_as_the_cancel_it_is(monkeypatch):
    pdf = make_form(clients(1, seed=3)[0], kind="flattened")

    def cancel(*args, **kwargs):
        raise SessionCancelled("cancelled by the person")

    with pytest.raises(SessionCancelled) as read:
        read_samples([SamplePdf("a.pdf", pdf)], on_page=cancel, **NO_DETECTORS)
    assert str(read.value) == "cancelled by the person"  # not "a.pdf: ...", an ordinary error of the sample
    monkeypatch.setattr(samples_mod, "detect_on_page", cancel)
    with pytest.raises(SessionCancelled) as detect:
        read_samples([SamplePdf("a.pdf", pdf)], detect="all", **NO_DETECTORS)
    assert str(detect.value) == "cancelled by the person"


def test_an_unknown_detection_mode_is_refused():
    with pytest.raises(ValueError, match="detect"):
        read_samples([], detect="some", **NO_DETECTORS)  # type: ignore[arg-type]


def test_empty_and_off_form_fields_are_left_out_of_the_values():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("blank.pdf", make_form(None, kind="fillable"))], **NO_DETECTORS)
    assert doc.values == {} and p.tax_id not in str(doc.values)


def test_page_progress_is_reported_with_the_samples_position():
    seen = []
    pdfs = [SamplePdf(f"{n}.pdf", make_form(clients(1)[0], kind="flattened")) for n in "ab"]
    read_samples(pdfs, on_page=lambda i, event: seen.append((i, event.index, event.step)), **NO_DETECTORS)
    assert (1, 0, "done") in seen and (0, 1, "done") in seen


def test_the_models_view_names_the_sample_its_kind_and_its_form_field_values():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="fillable"))], **NO_DETECTORS)
    view = sample_view(doc, 2)
    assert view.startswith("SAMPLE 2 (a.pdf, fillable") and f'tax_id = "{p.tax_id}"' in view
    assert "PAGE 1" in view and len(sample_view(doc, 2, max_chars=500)) <= 500


def test_a_flattened_sample_is_named_as_such_and_has_no_field_values():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1)[0], kind="flattened"))], **NO_DETECTORS)
    view = sample_view(doc, 1)
    assert view.startswith("SAMPLE 1 (a.pdf, flattened, 2 pages)") and "FORM FIELD VALUES" not in view


def test_sample_images_render_png_pages():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1)[0], kind="flattened"))], **NO_DETECTORS)
    images = sample_images(doc, max_pages=1)
    assert len(images) == 1 and images[0][1][:8] == b"\x89PNG\r\n\x1a\n"


def test_sample_images_put_the_pages_with_the_most_boxes_first():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1)[0], kind="fillable"))], **NO_DETECTORS)
    assert [n for n, _ in sample_images(doc)] == [0, 1]
    assert [n for n, _ in sample_images(doc, max_pages=1)] == [0]


def _sample_with_values(values: dict[str, str | bool], pdf: bytes) -> samples_mod.SampleDoc:
    (doc,) = read_samples([SamplePdf("a.pdf", pdf)], **NO_DETECTORS)
    doc.values = values
    return doc


def test_a_flattened_samples_view_holds_its_answers_as_page_text():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="flattened"))], **NO_DETECTORS)
    view = sample_view(doc, 1)
    assert "PAGE 0 TEXT:" in view and "PAGE 1 TEXT:" in view
    line = next(ln for ln in view.splitlines() if p.tax_id in ln)
    assert re.fullmatch(rf"\[\d+,\d+,\d+,\d+\] {re.escape(p.tax_id)}", line)


def test_the_form_field_values_come_before_the_pages_and_a_fillable_page_has_no_text_section():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1, seed=3)[0], kind="fillable"))], **NO_DETECTORS)
    view = sample_view(doc, 1)
    assert view.index("FORM FIELD VALUES:") < view.index("PAGE 0") and "PAGE 0 TEXT:" not in view
    assert "PAGE 1 TEXT:" in view  # the agreement page has no form fields


def test_the_header_and_the_values_survive_a_small_budget_whole():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="fillable"))], **NO_DETECTORS)
    full = sample_view(doc, 1)
    values = full[full.index("FORM FIELD VALUES:") : full.index("PAGE 0")]
    for limit in (len(values) + 60, len(values) + 400, 1500):
        view = sample_view(doc, 1, max_chars=limit)
        assert values in view and len(view) <= limit


def test_a_long_page_cannot_push_the_values_out_and_the_view_stays_within_its_budget():
    p = clients(1, seed=3)[0]
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(p, kind="flattened"))], **NO_DETECTORS)
    doc.values = {"tax_id": p.tax_id, "notes": "x"}
    view = sample_view(doc, 1, max_chars=900)
    assert f'tax_id = "{p.tax_id}"' in view and len(view) <= 900
    assert len(sample_view(doc, 1, max_chars=400)) <= 400


def test_the_last_pages_text_goes_before_the_first_pages():
    (doc,) = read_samples([SamplePdf("a.pdf", make_form(clients(1, seed=3)[0], kind="flattened"))], **NO_DETECTORS)
    full = sample_view(doc, 1)
    cut = sample_view(doc, 1, max_chars=len(full) - 100)
    first, last = cut.split("PAGE 1\n")
    assert "dropped]" not in first and "dropped]" in last


def test_a_value_with_a_quote_or_a_newline_stays_on_one_line():
    doc = _sample_with_values({"a": 'He said "hi"', "b": "two\nlines", "c": True}, make_form(None, kind="fillable"))
    lines = sample_view(doc, 1).splitlines()
    assert 'a = "He said \\"hi\\""' in lines and 'b = "two\\nlines"' in lines and "c = true" in lines


def test_each_samples_form_map_is_built_holding_the_pdf_lock(monkeypatch):
    from formcast.pagemap.extract import PDF_LOCK

    owned: list[bool] = []
    real = samples_mod.build_form_map

    def watching(pdf, **kw):
        owned.append(PDF_LOCK._is_owned())
        return real(pdf, **kw)

    monkeypatch.setattr(samples_mod, "build_form_map", watching)
    pdfs = [SamplePdf(f"{n}.pdf", make_form(c, kind="flattened")) for n, c in enumerate(clients(2))]
    read_samples(pdfs, detect="off")
    assert owned == [True, True] and not PDF_LOCK._is_owned()
