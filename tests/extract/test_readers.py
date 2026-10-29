"""resolve(): a model's reader proposals made into readers by code, keeping only what the samples have."""

from __future__ import annotations

import pytest

from formcast.extract.layout import learn_layout
from formcast.extract.model import AnchorRead, BoxRead, ChoiceRead, PhraseRead, WidgetRead
from formcast.extract.read import extract
from formcast.extract.readers import BoxRef, OptionProposal, ReaderProposal, resolve
from formcast.extract.samples import SamplePdf, read_samples
from formcast.pagemap.build import DetectPolicy
from formcast.pagemap.model import Candidate
from tests.extract.corpus import FIELD_RECTS, INVESTOR_TYPES, SHIFT, TYPE_BOXES, clients, hand_template, make_form

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))
SCHEMA = hand_template().schema_


@pytest.fixture(scope="module")
def two_kinds():
    """A fillable and a flattened sample of two clients, and the layout learned from them."""
    people = clients(2, seed=11)
    pdfs = [
        SamplePdf("a.pdf", make_form(people[0], kind="fillable")),
        SamplePdf("b.pdf", make_form(people[1], kind="flattened")),
    ]
    samples = read_samples(pdfs, **NO_DETECTORS)
    return samples, learn_layout([s.pdf for s in samples])


def near(a, b, tol=0.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_widgets_get_a_box_at_their_place_and_strategies_keep_the_specs_order(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve(
        [ReaderProposal(path="investor.tax_id", widgets=["tax_id"], labels=["Taxpayer ID"])],
        samples, layout, hand_template().schema_,
    )  # fmt: skip
    strategies = readers["investor.tax_id"].strategies
    assert [s.kind for s in strategies] == ["widget", "box", "anchor"] and warnings == []
    widget, box, anchor = strategies
    assert isinstance(widget, WidgetRead) and widget.names == ["tax_id"]
    assert isinstance(box, BoxRead) and box.page == 0 and near(box.rect, FIELD_RECTS["tax_id"])
    assert isinstance(anchor, AnchorRead) and anchor.labels == ["Taxpayer ID"] and anchor.page == 0
    # hand_template() with these readers (others dropped) and this layout reads the flattened sample by its box
    t = hand_template().model_copy(update={"readers": readers, "layout": layout})
    got = {f.path: f for f in extract(t, samples[1].pdf).fields}["investor.tax_id"]
    assert (got.status, got.strategy) == ("found", "box")


def test_names_labels_phrases_and_boxes_no_sample_has_are_dropped_with_a_warning(two_kinds):
    samples, layout = two_kinds
    proposal = ReaderProposal(
        path="investor.tax_id", widgets=["tax_id", "ghost"], labels=["Nowhere label"], phrases=["never written"],
        box=BoxRef(sample=0, id="c9_9"),
    )  # fmt: skip
    readers, warnings = resolve([proposal], samples, layout, SCHEMA)
    strategies = readers["investor.tax_id"].strategies
    assert [s.kind for s in strategies] == ["widget", "box"]  # the box of tax_id
    assert strategies[0].names == ["tax_id"] and near(strategies[1].rect, FIELD_RECTS["tax_id"])
    assert any("ghost" in w for w in warnings) and any("Nowhere label" in w for w in warnings)
    assert any("never written" in w for w in warnings) and any("c9_9" in w for w in warnings)
    assert "investor.tax_id: widget 'ghost' is on no sample; left out" in warnings


def test_a_box_ref_to_a_sample_there_is_not_is_dropped_with_a_warning(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve(
        [ReaderProposal(path="investor.tax_id", box=BoxRef(sample=-1, id="tax_id"))], samples, layout, SCHEMA
    )
    assert "investor.tax_id" not in readers and any("tax_id" in w and "left out" in w for w in warnings)


def test_a_path_with_nothing_usable_gets_no_reader_and_a_warning(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve([ReaderProposal(path="wire.bank", labels=["Nowhere"])], samples, layout, SCHEMA)
    assert "wire.bank" not in readers and any("wire.bank" in w and "no way to read" in w for w in warnings)
    assert "wire.bank: no way to read it was found on the samples" in warnings


def test_a_proposal_for_a_path_not_in_the_schema_is_ignored_with_a_warning(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve([ReaderProposal(path="made.up", widgets=["tax_id"])], samples, layout, SCHEMA)
    assert readers == {} and any("made.up" in w for w in warnings)


def test_a_second_proposal_for_one_path_is_ignored_with_a_warning(two_kinds):
    samples, layout = two_kinds
    proposals = [
        ReaderProposal(path="investor.tax_id", widgets=["tax_id"]),
        ReaderProposal(path="investor.tax_id", labels=["Taxpayer ID"]),
    ]
    readers, warnings = resolve(proposals, samples, layout, SCHEMA)
    assert [s.kind for s in readers["investor.tax_id"].strategies] == ["widget", "box"]
    assert any("investor.tax_id" in w and "more than once" in w for w in warnings)


def test_phrases_found_on_a_sample_are_kept_with_their_stop_and_page(two_kinds):
    samples, layout = two_kinds
    proposal = ReaderProposal(path="wire.account", phrases=["account"], stop="digits", clean=[{"op": "digits"}])
    readers, warnings = resolve([proposal], samples, layout, SCHEMA)
    (phrase,) = readers["wire.account"].strategies
    assert isinstance(phrase, PhraseRead) and (phrase.phrases, phrase.stop, phrase.page) == (["account"], "digits", 0)
    assert [t.op for t in readers["wire.account"].clean] == ["digits"] and warnings == []


def test_choice_options_from_widgets_also_get_their_drawn_boxes(two_kinds):
    samples, layout = two_kinds
    options = {v: OptionProposal(widget=f"type_{v}") for v in INVESTOR_TYPES}
    readers, _ = resolve([ReaderProposal(path="investor.type", options=options)], samples, layout, SCHEMA)
    widget_read, box_read = readers["investor.type"].strategies
    assert isinstance(widget_read, ChoiceRead) and isinstance(box_read, ChoiceRead)
    assert widget_read.options["trust"].widget == "type_trust" and box_read.options["trust"].rect is not None
    assert near(box_read.options["trust"].rect, TYPE_BOXES["trust"][1]) and box_read.options["trust"].page == 0
    # the readers read the flattened sample's drawn boxes
    t = hand_template().model_copy(update={"readers": readers, "layout": layout})
    got = {f.path: f for f in extract(t, samples[1].pdf).fields}["investor.type"]
    assert (got.status, got.value) == ("found", clients(2, seed=11)[1].investor_type)


def test_an_option_no_sample_has_is_dropped_with_a_warning(two_kinds):
    samples, layout = two_kinds
    options = {"trust": OptionProposal(widget="type_trust"), "estate": OptionProposal(widget="type_estate")}
    readers, warnings = resolve([ReaderProposal(path="investor.type", options=options)], samples, layout, SCHEMA)
    assert all(list(s.options) == ["trust"] for s in readers["investor.type"].strategies)
    assert "investor.type: widget 'type_estate' is on no sample; left out" in warnings


def test_a_box_from_a_shifted_sample_is_stored_in_the_layouts_place():
    client = clients(1, seed=12)[0]
    fillable, flattened = read_samples(
        [SamplePdf("a.pdf", make_form(client, kind="fillable", shift=SHIFT)),
         SamplePdf("b.pdf", make_form(client, kind="flattened", shift=SHIFT))],
        **NO_DETECTORS,
    )  # fmt: skip
    # a blank found on the flattened copy where its tax ID is written, pushed down with the page
    x0, y0, x1, y1 = FIELD_RECTS["tax_id"]
    blank = Candidate(id="c0_1", kind="text", rect=(x0, y0 + SHIFT, x1, y1 + SHIFT), conf=0.9, detector="test")
    page0 = flattened.form_map.pages[0].model_copy(update={"candidates": [blank]})
    flattened.form_map = flattened.form_map.model_copy(update={"pages": [page0, *flattened.form_map.pages[1:]]})
    layout = hand_template().layout  # learned from unshifted copies
    readers, warnings = resolve(
        [ReaderProposal(path="investor.tax_id", widgets=["tax_id"]),
         ReaderProposal(path="investor.dob", box=BoxRef(sample=1, id="c0_1"))],
        [fillable, flattened], layout, SCHEMA,
    )  # fmt: skip
    assert warnings == []
    assert near(readers["investor.tax_id"].strategies[1].rect, FIELD_RECTS["tax_id"])
    assert near(readers["investor.dob"].strategies[0].rect, FIELD_RECTS["tax_id"])
