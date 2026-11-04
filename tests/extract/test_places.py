"""Field places: every place a datum is written, read and compared; fields that repeat each other made one; filled
form fields no field reads placed; the form's own read-only printing never taken for an answer."""

from __future__ import annotations

import copy
import hashlib

import pymupdf

from formcast.extract.layout import learn_layout
from formcast.extract.model import BoxRead, ExtractionTemplate, Reader, SampleRecord, WidgetRead
from formcast.extract.proof import prove
from formcast.extract.read import extract
from formcast.extract.readers import ReaderProposal, resolve
from formcast.extract.repeats import apply_answers, fold_repeats, merge_repeats, unread_widgets
from formcast.extract.samples import SamplePdf, read_samples
from formcast.pagemap.build import DetectPolicy
from formcast.template.model import form_info_for

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))
DATE = [{"op": "date", "args": {"formats": ["%m %d %y"]}}]


def _text_widget(page, name, value, rect, *, read_only=False):
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.field_value, w.rect = name, pymupdf.PDF_WIDGET_TYPE_TEXT, value, pymupdf.Rect(rect)
    if read_only:
        w.field_flags = 1
    page.add_widget(w)


def _package(*, copy_value="Ann Lee") -> bytes:
    """Page 0: Name "Ann Lee", a date in Month / Day / Year boxes with read-only "/" boxes between; page 1: the name
    again in a field of its own (NameAgain = ``copy_value``) and the date's boxes again; page 2: NameAgain twice."""
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page()
        page.insert_text((20, 85), "Name", fontsize=10)
        if n == 0:
            _text_widget(page, "Name", "Ann Lee", (72, 72, 200, 90))
        if n < 2:
            for name, value, x in (("Month", "05", 72), ("slash", "/", 94), ("Day", "16", 104), ("slash", "/", 126), ("Year", "17", 136)):
                _text_widget(page, name, value, (x, 120, x + 20, 138), read_only=name == "slash")
        if n == 1:
            _text_widget(page, "NameAgain", copy_value, (72, 72, 200, 90))
        if n == 2:
            _text_widget(page, "Native", "安", (72, 200, 200, 218))
    return doc.tobytes()


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "signed": {"type": "string", "format": "date"},
        "w9": {"type": "object", "properties": {"name": {"type": "string"}, "signed": {"type": "string", "format": "date"}}},
    },
}


def _samples(pdf: bytes):
    samples = read_samples([SamplePdf("p.pdf", pdf)], **NO_DETECTORS)
    return samples, learn_layout([s.pdf for s in samples])


def _template(samples, layout, readers, schema=SCHEMA, opinion=None):
    form = form_info_for(samples[0].form_map)
    records = [SampleRecord(file="p.pdf", sha256=hashlib.sha256(samples[0].pdf).hexdigest(), opinion=opinion)] if opinion else []
    return ExtractionTemplate(template_id="p", name="p", schema=schema, form=form, layout=layout, readers=readers, samples=records)


def test_the_forms_read_only_printing_is_not_an_answer():
    samples, layout = _samples(_package())
    assert "slash" not in samples[0].values and samples[0].values["Month"] == "05"
    proposal = ReaderProposal(path="signed", widgets=["Month", "Day", "slash"], clean=DATE)
    readers, warnings = resolve([proposal], samples, layout, SCHEMA)
    assert readers["signed"].strategies[0].names == ["Month", "Day"]
    assert warnings == ["signed: the form field 'slash' is read-only, the form's own printing; left out"]


def test_every_copy_of_a_split_date_is_a_place_and_a_page_bound_read_spans_copies_that_agree():
    samples, layout = _samples(_package())
    readers = {"signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"], page=0)], clean=DATE)}
    result = extract(_template(samples, layout, readers), samples[0].pdf)
    signed = next(f for f in result.fields if f.path == "signed")
    assert signed.status == "found" and signed.value == "2017-05-16"
    assert [(p.page, p.instance, p.value) for p in signed.places] == [(0, 1, "2017-05-16"), (1, 1, "2017-05-16")]
    x0, _, x1, _ = signed.places[0].rect
    assert (x0, x1) == (72, 156)  # the month's box to the year's: one place


def test_a_place_that_disagrees_makes_the_field_unsure_and_keeps_the_first_value():
    samples, layout = _samples(_package(copy_value="Ann Leigh"))
    readers = {"name": Reader(strategies=[WidgetRead(names=["Name"])], places=[WidgetRead(names=["NameAgain"])])}
    name = next(f for f in extract(_template(samples, layout, readers), samples[0].pdf).fields if f.path == "name")
    assert name.status == "unsure" and name.value == "Ann Lee"
    assert [(p.page, p.value) for p in name.places] == [(0, "Ann Lee"), (1, "Ann Leigh")]
    assert name.reasons == ["places disagree: page 1 reads 'Ann Lee'; page 2 reads 'Ann Leigh'"]


def test_places_that_agree_leave_the_field_found_and_count_instances_on_a_page():
    doc = pymupdf.open(stream=_package())
    _text_widget(doc[1], "NameAgain", "Ann Lee", (72, 300, 200, 318))  # a second widget of the same field, lower down
    samples, layout = _samples(doc.tobytes())
    readers = {"name": Reader(strategies=[WidgetRead(names=["Name"])], places=[WidgetRead(names=["NameAgain"])])}
    name = next(f for f in extract(_template(samples, layout, readers), samples[0].pdf).fields if f.path == "name")
    assert name.status == "found" and name.reasons == []
    assert [(p.page, p.instance) for p in name.places] == [(0, 1), (1, 1), (1, 2)]


def test_fields_that_read_the_same_form_field_are_merged_into_the_first():
    samples, layout = _samples(_package())
    schema = copy.deepcopy(SCHEMA)
    readers = {
        "signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"])], clean=DATE),
        "w9.signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"])], clean=DATE, places=[BoxRead(page=1, rect=(0, 0, 9, 9))]),
        "name": Reader(strategies=[WidgetRead(names=["Name"])]),
    }
    merged = merge_repeats(schema, readers, {"the date": ["w9.signed"]})
    assert set(merged.readers) == {"signed", "name"} and merged.readers["signed"].places == [BoxRead(page=1, rect=(0, 0, 9, 9))]
    assert "signed" not in merged.schema["properties"]["w9"]["properties"] and "name" in merged.schema["properties"]["w9"]["properties"]
    assert merged.line_paths == {"the date": ["signed"]}
    assert merged.warnings == ["w9.signed: reads the same form field as signed; merged into it (one field, every place listed)"]


def test_filled_form_fields_no_reader_reads_are_placed_as_the_model_answers():
    samples, _ = _samples(_package())
    readers = {"name": Reader(strategies=[WidgetRead(names=["Name"])]), "signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"])])}
    unread = unread_widgets(samples, readers)
    assert [(u.name, u.page, u.value) for u in unread] == [("NameAgain", 1, "Ann Lee"), ("Native", 2, "安")]  # no "/" boxes
    answers = [{"id": unread[0].id, "path": "name"}, {"id": unread[1].id, "new": {"name": "native_name", "title": "Native name"}}]
    schema = copy.deepcopy(SCHEMA)
    placed = apply_answers(schema, readers, unread, answers)
    assert placed.readers["name"].places == [WidgetRead(names=["NameAgain"])]
    assert placed.added == ["native_name"] and placed.schema["properties"]["native_name"]["title"] == "Native name"
    assert placed.readers["native_name"].strategies[0] == WidgetRead(names=["Native"])
    skipped = apply_answers(schema, readers, unread[:1], [{"id": unread[0].id, "skip": "a printed default"}])
    assert skipped.warnings == ["form field NameAgain (page 2) = 'Ann Lee': not read (a printed default)"]


def test_a_failed_field_written_in_a_ready_fields_place_is_folded_into_it():
    samples, layout = _samples(_package())
    readers = {
        "signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"])], clean=DATE),
        # the model read the W-9's date from its month box alone, on page 1: it fails
        "w9.signed": Reader(strategies=[WidgetRead(names=["Month"], page=1), BoxRead(page=1, rect=(72, 120, 92, 138))], clean=DATE),
        "name": Reader(strategies=[WidgetRead(names=["Name"])]),
    }
    opinion = {"signed": "2017-05-16", "w9.signed": "2017-05-16", "name": "Ann Lee", "w9.name": None}
    template = _template(samples, layout, readers, opinion=opinion)
    proof = prove(template, [samples[0].pdf])
    assert not next(f for f in proof.fields if f.path == "w9.signed").ready
    folded, warnings = fold_repeats(template, proof)
    assert "w9.signed" not in folded.readers and "signed" not in folded.schema_["properties"]["w9"]["properties"]
    assert warnings == ["w9.signed: the same answer, written in one of signed's places; folded into it (its reader read it wrongly)"]
    assert prove(folded, [samples[0].pdf]).ready == 2


def test_a_failed_field_with_the_same_words_elsewhere_is_not_folded():
    samples, layout = _samples(_package())
    readers = {
        "name": Reader(strategies=[WidgetRead(names=["Name"])]),
        "w9.name": Reader(strategies=[BoxRead(page=2, rect=(300, 300, 400, 318))]),  # nothing there: not the name's place
    }
    opinion = {"name": "Ann Lee", "w9.name": "Ann Lee"}
    template = _template(samples, layout, readers, opinion=opinion)
    assert fold_repeats(template, prove(template, [samples[0].pdf])) == (template, [])


def test_the_fill_template_made_from_it_writes_every_place():
    from formcast.extract.tofill import fill_template_for

    samples, layout = _samples(_package())
    readers = {
        "name": Reader(strategies=[WidgetRead(names=["Name"])], places=[WidgetRead(names=["NameAgain"])]),
        "signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"], page=0)], clean=DATE),
    }
    fill, _warnings = fill_template_for(_template(samples, layout, readers), samples[0].form_map, samples[0].pdf)
    by_widget = {(f.existing_widget, f.page): f.source.paths for f in fill.fields if f.source is not None}
    assert by_widget[("Name", 0)] == ["name"] and by_widget[("NameAgain", 1)] == ["name"]
    assert by_widget[("Month", 0)] == by_widget[("Month", 1)] == ["signed"]  # the date's copy on page 1 too
    assert ("slash", 0) not in by_widget  # the form's own "/" is left as it is


def test_a_read_kept_to_one_page_is_merged_when_the_answers_agree_on_every_page():
    samples, _ = _samples(_package())
    readers = {
        "signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"])], clean=DATE),
        "w9.signed": Reader(strategies=[WidgetRead(names=["Month", "Day", "Year"], page=1)], clean=DATE),
    }
    merged = merge_repeats(copy.deepcopy(SCHEMA), readers, {}, samples)
    assert set(merged.readers) == {"signed"}
    kept = merge_repeats(copy.deepcopy(SCHEMA), readers, {})  # no samples to tell: a page-bound read stays apart
    assert set(kept.readers) == {"signed", "w9.signed"}
