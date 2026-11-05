"""resolve(): a model's reader proposals made into readers by code, keeping only what the samples have."""

from __future__ import annotations

import pymupdf
import pytest

from formcast.extract.layout import learn_layout, without_answers
from formcast.extract.model import AnchorRead, BoxRead, ChoiceRead, PhraseRead, WidgetRead
from formcast.extract.read import extract
from formcast.extract.page import DocText
from formcast.extract.readers import TEXT_PAD, TEXT_REACH, BoxRef, OptionProposal, ReaderProposal, resolve, text_cell
from formcast.extract.samples import SamplePdf, read_samples, text_lines
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


def test_a_proposal_of_only_a_path_says_the_model_found_no_place_for_it(two_kinds):
    samples, layout = two_kinds
    readers, warnings = resolve([ReaderProposal(path="wire.bank")], samples, layout, SCHEMA)
    assert readers == {} and warnings == ["wire.bank: the model found no place for it on the samples"]


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


YES_BOX, NO_BOX = (300.0, 120.0, 312.0, 132.0), (350.0, 120.0, 362.0, 132.0)
YES_NO_SCHEMA = {"type": "object", "properties": {"us_person": {"type": "boolean"}}}


def _yes_no_form(kind: str, answer: bool | None) -> bytes:
    """A one-question form with a Yes box and a No box: check boxes ``us_yes``/``us_no``, or drawn squares."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Tax status questionnaire", fontsize=14)
    page.insert_text((72, 130), "Are you a US person?", fontsize=10)
    page.insert_text((315, 130), "Yes", fontsize=9)
    page.insert_text((365, 130), "No", fontsize=9)
    page.insert_text((72, 300), "Thank you for your business.", fontsize=10)
    for name, box, on in (("us_yes", YES_BOX, answer is True), ("us_no", NO_BOX, answer is False)):
        rect = pymupdf.Rect(*box)
        if kind == "fillable":
            w = pymupdf.Widget()
            w.field_name, w.field_type, w.rect, w.field_value = name, pymupdf.PDF_WIDGET_TYPE_CHECKBOX, rect, on
            page.add_widget(w)
        else:
            page.draw_rect(rect, width=0.75)
            if on:
                page.insert_text((rect.x0 + 2.5, rect.y1 - 2.5), "X", fontsize=10)
    return doc.tobytes()


@pytest.fixture(scope="module")
def yes_no():
    """Three samples of the yes/no form (fillable yes, flattened no, fillable no) and the layout learned from them."""
    pdfs = [_yes_no_form(k, a) for k, a in (("fillable", True), ("flattened", False), ("fillable", False))]
    samples = read_samples([SamplePdf(f"s{i}.pdf", p) for i, p in enumerate(pdfs)], **NO_DETECTORS)
    return samples, learn_layout(pdfs)


def _yes_no_template(readers, layout):
    return hand_template().model_copy(update={"schema_": YES_NO_SCHEMA, "readers": readers, "layout": layout})


def test_a_yes_no_choice_that_lost_an_answer_is_left_out_and_never_reads_an_unanswered_copy_as_found(yes_no):
    # the model misnames the Yes widget: a choice of the No box alone would read as a lone box, an unmarked one
    # reading True, so an unanswered copy would be found True
    samples, layout = yes_no
    options = {"true": OptionProposal(widget="us_yes_box"), "false": OptionProposal(widget="us_no")}
    readers, warnings = resolve([ReaderProposal(path="us_person", options=options)], samples, layout, YES_NO_SCHEMA)
    assert "us_person" not in readers
    assert "us_person: widget 'us_yes_box' is on no sample; left out" in warnings
    assert "us_person: an answer's box is on no sample; the choice is left out" in warnings
    t = _yes_no_template(readers, layout)
    for kind in ("fillable", "flattened"):
        got = extract(t, _yes_no_form(kind, None)).fields[0]
        assert got.status != "found", (kind, got)


def test_a_check_box_option_with_a_wrong_state_keeps_its_place_and_both_answers_read(yes_no):
    samples, layout = yes_no
    options = {"true": OptionProposal(widget="us_yes", state="On"), "false": OptionProposal(widget="us_no")}
    readers, warnings = resolve([ReaderProposal(path="us_person", options=options)], samples, layout, YES_NO_SCHEMA)
    assert warnings == []
    assert [sorted(s.options) for s in readers["us_person"].strategies] == [["false", "true"]] * 2
    t = _yes_no_template(readers, layout)
    for kind in ("fillable", "flattened"):
        for answer in (True, False):
            got = extract(t, _yes_no_form(kind, answer)).fields[0]
            assert (got.status, got.value) == ("found", answer), (kind, answer, got)
        assert extract(t, _yes_no_form(kind, None)).fields[0].status == "blank"


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


# ---------------------------------------------------------------- an answer named by its text line

TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "loan_amount": {"type": "string"},
        "early_payment": {"type": "string"},
        "late_payment": {"type": "string"},
        "address": {"type": "string"},
    },
}


def _statement(amount: str, early: str, late: str, street: str, city: str) -> bytes:
    """A flattened one-page statement printed as a designed page, not a form: a label with its answer far to its
    right, a table whose two columns hold one row's two answers, and an address over two lines."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for (x, y), text in {
        (40, 100): "Loan Amount", (190, 100): amount,
        (260, 160): "Years 1-7", (450, 160): "Years 8-30",
        (40, 180): "Principal & Interest", (260, 180): early, (450, 180): late,
        (40, 240): "Property", (110, 240): street, (110, 252): city,
        (40, 300): "Closing Costs Summary",
    }.items():  # fmt: skip
        page.insert_text((x, y), text, fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


def _line_id(sample, text: str) -> str:
    with DocText(sample.pdf) as pdf:
        return f"t0_{[t for t, _ in text_lines(sample, pdf, 0)].index(text)}"


def test_a_text_line_names_the_answer_and_its_box_widens_to_its_cell_only():
    (sample,) = read_samples([SamplePdf("a.pdf", _statement("$162,000", "$761.78", "$761.78", "456 Somewhere Ave", "Anytown, ST 12345"))], **NO_DETECTORS)
    layout = learn_layout([sample.pdf])
    early, late = (f"t0_{n}" for n in _row_ids(sample, "$761.78"))
    readers, warnings = resolve(
        [ReaderProposal(path="loan_amount", box=BoxRef(sample=0, id=_line_id(sample, "$162,000"))),
         ReaderProposal(path="early_payment", box=BoxRef(sample=0, id=early)),
         ReaderProposal(path="late_payment", box=BoxRef(sample=0, id=late)),
         ReaderProposal(path="address", box=BoxRef(sample=0, id=f"{_line_id(sample, '456 Somewhere Ave')}+{_line_id(sample, 'Anytown, ST 12345')}"))],
        [sample], layout, TABLE_SCHEMA,
    )  # fmt: skip
    assert warnings == []
    boxes = {path: r.strategies[0] for path, r in readers.items()}
    assert all(isinstance(b, BoxRead) for b in boxes.values())
    # each column's box stops halfway to the next column: the two answers of one row never share a box
    assert boxes["early_payment"].rect[2] <= boxes["late_payment"].rect[0]
    # a box never reaches back over its label
    with DocText(sample.pdf) as pdf:
        (label,) = [r for t, r in pdf.lines(0) if t == "Loan Amount"]
    assert boxes["loan_amount"].rect[0] > label[2]
    # and every box reads its own answer on the sample
    t = hand_template().model_copy(
        update={"schema_": TABLE_SCHEMA, "readers": readers, "layout": without_answers(layout, [(b.page, b.rect) for b in boxes.values()])}
    )
    got = {f.path: f.value for f in extract(t, sample.pdf).fields}
    assert got == {"loan_amount": "$162,000", "early_payment": "$761.78", "late_payment": "$761.78", "address": "456 Somewhere Ave Anytown, ST 12345"}


def _row_ids(sample, text: str) -> list[int]:
    with DocText(sample.pdf) as pdf:
        return [n for n, (t, _) in enumerate(text_lines(sample, pdf, 0)) if t == text]


def test_a_template_from_one_flattened_sample_reads_another_copys_longer_answers():
    first = _statement("$162,000", "$761.78", "$761.78", "456 Somewhere Ave", "Anytown, ST 12345")
    other = _statement("$1,450,000", "$12,737.91", "$9,100.00", "1200 Long Boulevard West", "Elsewhere, ST 54321")
    (sample,) = read_samples([SamplePdf("a.pdf", first)], **NO_DETECTORS)
    layout = learn_layout([sample.pdf])
    early, late = (f"t0_{n}" for n in _row_ids(sample, "$761.78"))
    readers, _ = resolve(
        [ReaderProposal(path="loan_amount", box=BoxRef(sample=0, id=_line_id(sample, "$162,000")), labels=["Loan Amount"], max_width=150),
         ReaderProposal(path="early_payment", box=BoxRef(sample=0, id=early)),
         ReaderProposal(path="late_payment", box=BoxRef(sample=0, id=late)),
         ReaderProposal(path="address", box=BoxRef(sample=0, id=f"{_line_id(sample, '456 Somewhere Ave')}+{_line_id(sample, 'Anytown, ST 12345')}"))],
        [sample], layout, TABLE_SCHEMA,
    )  # fmt: skip
    answers = [(s.page, s.rect) for r in readers.values() for s in r.strategies if isinstance(s, BoxRead)]
    t = hand_template().model_copy(update={"schema_": TABLE_SCHEMA, "readers": readers, "layout": without_answers(layout, answers)})
    got = {f.path: (f.status, f.value) for f in extract(t, other).fields}
    assert got == {
        "loan_amount": ("found", "$1,450,000"), "early_payment": ("found", "$12,737.91"), "late_payment": ("found", "$9,100.00"),
        "address": ("found", "1200 Long Boulevard West Elsewhere, ST 54321"),
    }  # fmt: skip


def test_a_text_line_id_no_sample_has_is_dropped_with_a_warning():
    (sample,) = read_samples([SamplePdf("a.pdf", _statement("$1", "$2", "$3", "x", "y"))], **NO_DETECTORS)
    for bad in ("t0_999", "t3_0", "t0_1+t1_2"):
        readers, warnings = resolve([ReaderProposal(path="loan_amount", box=BoxRef(sample=0, id=bad))], [sample], learn_layout([sample.pdf]), TABLE_SCHEMA)
        assert readers == {} and f"loan_amount: box '{bad}' is on no sample; left out" in warnings


def test_text_cell_reaches_halfway_to_its_row_neighbours_and_at_most_text_reach():
    assert text_cell((100, 10, 150, 20), [(20, 10, 60, 20), (250, 11, 300, 19)], 612) == (80, 10 - TEXT_PAD, 200, 20 + TEXT_PAD)
    # a line on another row is no neighbour; with none, the box reaches TEXT_REACH and stops at the page's edge
    alone = text_cell((500, 10, 560, 20), [(20, 40, 60, 50)], 612)
    assert alone == (500 - TEXT_REACH, 10 - TEXT_PAD, 612, 20 + TEXT_PAD)


def test_text_cell_stops_at_a_column_rule_that_crosses_its_row():
    # the answer's neighbour cells are empty on the sample: the table's rules, not TEXT_REACH, end its box
    walls = [(90, 0, 90.5, 100), (170, 0, 170.5, 100), (60, 40, 60.5, 100)]  # the last one ends above the row
    assert text_cell((100, 10, 150, 20), [], 612, walls=walls) == (90.5, 10 - TEXT_PAD, 170, 20 + TEXT_PAD)
    assert text_cell((100, 10, 150, 20), [(20, 10, 60, 20)], 612, walls=walls)[0] == 90.5  # the nearer of the two


def test_a_text_line_in_a_table_drawn_a_row_at_a_time_keeps_to_its_column():
    # the columns' rules are short strokes, one per row, and the cells beside the answer are empty on the sample
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((40, 100), "Appraisal Fee", fontsize=10)
    page.insert_text((310, 100), "$405.00", fontsize=10)
    for x in (300, 360, 420):
        page.draw_line((x, 90), (x, 104), width=0.5)
    pdf = doc.tobytes()
    doc.close()
    (sample,) = read_samples([SamplePdf("a.pdf", pdf)], **NO_DETECTORS)
    with DocText(sample.pdf) as text:
        assert len(text.walls(0)) == 3 and text.walls(5) == []
    readers, _ = resolve(
        [ReaderProposal(path="loan_amount", box=BoxRef(sample=0, id=_line_id(sample, "$405.00")))],
        [sample], learn_layout([sample.pdf]), TABLE_SCHEMA,
    )  # fmt: skip
    x0, _, x1, _ = readers["loan_amount"].strategies[0].rect
    assert 300 <= x0 < 310 and 340 < x1 <= 360
