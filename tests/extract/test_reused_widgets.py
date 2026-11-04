"""Form fields a package reuses: one name on several pages holding different answers, and one answer split over
several fields (a date's month, day and year)."""

from __future__ import annotations

import pymupdf

from formcast.extract.layout import learn_layout
from formcast.extract.model import ExtractionTemplate, WidgetRead
from formcast.extract.read import extract
from formcast.extract.readers import ReaderProposal, resolve
from formcast.extract.samples import SamplePdf, read_samples, reused_values
from formcast.extract.setup import _reused_note
from formcast.pagemap.build import DetectPolicy
from formcast.template.model import form_info_for

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))
SCHEMA = {
    "type": "object",
    "properties": {
        "subscriber": {"type": "object", "properties": {"name": {"type": "string"}, "signed": {"type": "string", "format": "date"}}},
        "worksheet": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
}


def _rename(doc, xref, name):
    """Rename the field of widget ``xref``: the widget itself when it carries the /T, else its parent."""
    kind, parent = doc.xref_get_key(xref, "Parent")
    target = int(parent.split()[0]) if doc.xref_get_key(xref, "T")[0] == "null" and kind == "xref" else xref
    doc.xref_set_key(target, "T", f"({name})")


def _package() -> bytes:
    """Page 0: "Name" = "Ann" and Month/Day/Year = 05/16/17; page 1: another field also named "Name", "Bo"."""
    doc = pymupdf.open()
    for n, value in enumerate(("Ann", "Bo")):
        page = doc.new_page()
        page.insert_text((20, 85), "Name", fontsize=10)
        fields = [(f"Name{n}", value, (72, 72, 200, 90))]
        if n == 0:
            fields += [("Month", "05", (72, 120, 92, 138)), ("Day", "16", (100, 120, 120, 138)), ("Year", "17", (128, 120, 148, 138))]
        for name, v, rect in fields:
            w = pymupdf.Widget()
            w.field_name, w.field_type, w.field_value, w.rect = name, pymupdf.PDF_WIDGET_TYPE_TEXT, v, pymupdf.Rect(rect)
            page.add_widget(w)
    for page in doc:
        for w in page.widgets():
            if w.field_name.startswith("Name"):
                _rename(doc, w.xref, "Name")  # two fields, one name
    return doc.tobytes()


def _samples():
    samples = read_samples([SamplePdf("p.pdf", _package())], **NO_DETECTORS)
    return samples, learn_layout([s.pdf for s in samples])


def test_the_sample_values_list_a_reused_name_again_for_the_page_whose_value_differs():
    samples, _ = _samples()
    values = samples[0].values
    assert values["Name"] == "Ann" and values["Name (page 1, id w1_0)"] == "Bo"
    assert reused_values(values) == {"Name": [("first page", "Ann"), ("page 1, id w1_0", "Bo")]}
    note = _reused_note(samples[0])
    assert "its own properties" in note and '- Name: first page = "Ann"; page 1, id w1_0 = "Bo"' in note


def test_a_widget_named_by_its_id_is_read_on_its_page_and_split_fields_are_joined():
    samples, layout = _samples()
    proposals = [
        ReaderProposal(path="subscriber.name", widgets=["Name"]),
        ReaderProposal(path="worksheet.name", widgets=["w1_0"]),
        ReaderProposal(path="subscriber.signed", widgets=["Month", "Day", "Year"], clean=[{"op": "date", "args": {"formats": ["%m %d %y"]}}]),
    ]
    readers, warnings = resolve(proposals, samples, layout, SCHEMA)
    assert warnings == []
    worksheet = readers["worksheet.name"].strategies[0]
    assert isinstance(worksheet, WidgetRead) and (worksheet.names, worksheet.page) == (["Name"], 1)
    form = form_info_for(samples[0].form_map)
    t = ExtractionTemplate(template_id="p", name="p", schema=SCHEMA, form=form, layout=layout, readers=readers)
    got = {f.path: f.value for f in extract(t, samples[0].pdf).fields}
    assert got == {"subscriber.name": "Ann", "subscriber.signed": "2017-05-16", "worksheet.name": "Bo"}


def test_a_reused_name_given_by_name_takes_the_page_of_the_fields_label():
    samples, layout = _samples()
    page_1 = [ReaderProposal(path="worksheet.name", widgets=["Name"], box={"sample": 0, "id": "w1_0"})]
    readers, _ = resolve(page_1, samples, layout, SCHEMA)
    assert readers["worksheet.name"].strategies[0].page == 1


def _blank(pdf: bytes) -> bytes:
    """The same form with every text field emptied."""
    doc = pymupdf.open(stream=pdf)
    for page in doc:
        for w in page.widgets():
            w.field_value = ""
            w.update()
    return doc.tobytes()


def test_a_blank_copy_filled_through_the_extraction_template_shows_what_the_sample_did():
    from formcast import fill
    from formcast.extract.tofill import fill_template_for
    from formcast.pagemap.build import build_form_map

    samples, layout = _samples()
    proposals = [
        ReaderProposal(path="subscriber.name", widgets=["w0_0"]),
        ReaderProposal(path="worksheet.name", widgets=["w1_0"]),
        ReaderProposal(path="subscriber.signed", widgets=["Month", "Day", "Year"], clean=[{"op": "date", "args": {"formats": ["%m %d %y"]}}]),
    ]
    readers, _ = resolve(proposals, samples, layout, SCHEMA)
    form = form_info_for(samples[0].form_map)
    t = ExtractionTemplate(template_id="p", name="p", schema=SCHEMA, form=form, layout=layout, readers=readers)
    record = extract(t, samples[0].pdf).record()
    blank = _blank(samples[0].pdf)
    fill_tpl, warnings = fill_template_for(t, build_form_map(blank, **NO_DETECTORS, ocr="never"), samples[0].pdf)
    assert warnings == []
    by_widget = {f.id: (f.source.paths, [tr.args for tr in f.source.transforms]) for f in fill_tpl.fields if f.source}
    assert by_widget["w0_1"] == (["subscriber.signed"], [{"format": "%m"}])  # each part of the split date its part
    out, _ = fill(fill_tpl, blank, record)
    shown = {(p.number, w.field_name): w.field_value for p in pymupdf.open(stream=out) for w in p.widgets()}
    assert shown == {(0, "Name"): "Ann", (0, "Month"): "05", (0, "Day"): "16", (0, "Year"): "17", (1, "Name"): "Bo"}
