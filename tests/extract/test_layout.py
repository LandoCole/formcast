"""The learned layout: static lines from samples or a blank form, and the score and offset of a document against it."""

import pytest

from formcast.errors import FormcastError
from formcast.extract.layout import (
    LAYOUT_MATCH,
    LINE_TOLERANCE,
    layout_offset,
    layout_score,
    learn_layout,
    normalize_line,
    page_fit,
)
from formcast.extract.model import StaticLine
from formcast.extract.page import DocText
from tests.extract.corpus import FIELD_RECTS, REVISED_GAP, ROTATE, SHIFT, clients, make_form, other_form

KINDS = ("fillable", "flattened")


@pytest.fixture(scope="module")
def layout():
    return learn_layout([make_form(c, kind="flattened") for c in clients(5)])


def score(layout, pdf: bytes) -> float:
    with DocText(pdf) as doc:
        return layout_score(layout, doc)


def test_normalize_line_keeps_letters_and_digits_only():
    assert normalize_line("Taxpayer ID:") == "taxpayer id"
    assert normalize_line("  Beneficial owners (25% or more) ") == "beneficial owners 25 or more"
    assert normalize_line("___") == ""


@pytest.mark.parametrize("kind", KINDS)
def test_answers_are_not_static_lines_and_labels_are(kind):
    people = clients(5)
    layout = learn_layout([make_form(c, kind=kind) for c in people])
    texts = {line.text for line in layout.lines}
    assert "taxpayer id" in texts and "investor legal name" in texts and "signature" in texts
    for c in people:
        for answer in (c.tax_id, c.legal_name, c.dob, c.street, c.account, c.bank):
            assert not any(normalize_line(answer) in t for t in texts if t), answer
    # a checked fillable box has a mark of its own on the page: it is not an unchanging line either
    assert "3" not in texts and "x" not in texts
    assert layout.page_sizes == [(612.0, 792.0)] * 2
    assert {line.page for line in layout.lines} == {0, 1}


def test_a_blank_form_gives_the_layout_directly():
    blank = make_form(None, kind="fillable")
    layout = learn_layout([], blank=blank)
    texts = {line.text for line in layout.lines}
    assert {"client information form", "taxpayer id", "signature", "name", "percent"} <= texts
    assert layout.page_sizes == [(612.0, 792.0)] * 2
    with DocText(blank) as doc:
        assert layout_score(layout, doc) == pytest.approx(1.0)
    # the blank wins over samples
    other = learn_layout([make_form(c, kind="flattened") for c in clients(3)], blank=blank)
    assert other == layout


def test_a_line_must_be_on_all_but_one_sample_when_there_are_four_or_more():
    people = clients(5)
    noisy = [make_form(c, kind="flattened", header="Stamped " + c.account[:4]) for c in people]
    assert not any(line.text.startswith("stamped") for line in learn_layout(noisy).lines)
    one_off = [
        make_form(c, kind="flattened", header="Received by mail" if i == 0 else None) for i, c in enumerate(people)
    ]
    assert not any(line.text == "received by mail" for line in learn_layout(one_off).lines)  # one in five: dropped
    two = [make_form(c, kind="flattened", header="Received by mail" if i < 4 else None) for i, c in enumerate(people)]
    assert any(line.text == "received by mail" for line in learn_layout(two).lines)  # four of five: kept
    # with fewer than four samples a line must be on all of them
    three = [
        make_form(c, kind="flattened", header="Received by mail" if i < 2 else None) for i, c in enumerate(clients(3))
    ]
    assert not any(line.text == "received by mail" for line in learn_layout(three).lines)
    all_three = [make_form(c, kind="flattened", header="Received by mail") for c in clients(3)]
    assert any(line.text == "received by mail" for line in learn_layout(all_three).lines)


def test_a_line_must_be_in_the_same_place():
    people = clients(4)
    near = [make_form(c, kind="flattened", header="Received by mail") for c in people]
    assert any(line.text == "received by mail" for line in learn_layout(near).lines)
    # the same text a long way down on every other sample is a different place
    moved = [
        make_form(c, kind="flattened", header="Received by mail", shift=0.0 if i % 2 else 3 * LINE_TOLERANCE)
        for i, c in enumerate(people)
    ]
    assert not any(line.text == "received by mail" for line in learn_layout(moved).lines)


def test_no_samples_and_no_blank_is_an_error():
    with pytest.raises(FormcastError):
        learn_layout([])


@pytest.mark.parametrize("kind", KINDS)
def test_a_filled_copy_of_either_kind_scores_above_the_match_line(layout, kind):
    seen = clients(5)
    fresh = clients(3, seed=7)  # clients the layout never saw
    for c in seen + fresh:
        variants = {
            "plain": make_form(c, kind=kind),
            "shifted": make_form(c, kind=kind, shift=12),
            "rotated": make_form(c, kind=kind, rotate=ROTATE),
            "extra page": make_form(c, kind=kind, extra_page=True),
            "painted": make_form(c, kind=kind, painted=True),
            "all at once": make_form(c, kind=kind, shift=SHIFT, rotate=ROTATE, extra_page=True),
        }
        for name, pdf in variants.items():
            assert score(layout, pdf) >= LAYOUT_MATCH, (kind, name)


def test_a_shifted_copy_scores_as_high_as_an_aligned_one(layout):
    c = clients(1, seed=7)[0]
    assert score(layout, make_form(c, kind="flattened", shift=SHIFT)) == pytest.approx(
        score(layout, make_form(c, kind="flattened")), abs=0.02
    )


def test_another_form_scores_well_below_it(layout):
    assert score(layout, other_form()) < 0.3


def test_a_shorter_document_loses_the_pages_it_lacks():
    full = make_form(clients(1)[0], kind="flattened")
    layout = learn_layout([make_form(c, kind="flattened") for c in clients(4)])
    import pymupdf

    one_page = pymupdf.open(stream=full)
    one_page.delete_page(1)
    assert 0.3 < score(layout, one_page.tobytes()) < LAYOUT_MATCH


def test_the_offset_of_a_shifted_copy_is_its_shift(layout):
    c = clients(1, seed=7)[0]
    for kind in KINDS:
        with DocText(make_form(c, kind=kind, shift=12)) as doc:
            assert layout_offset(layout, doc, 0) == pytest.approx(12.0, abs=1.0)
            assert layout_offset(layout, doc, 1) == pytest.approx(0.0, abs=1.0)  # only page 0 moves
            assert layout_offset(layout, doc, 9) == 0.0  # a page the document lacks
    with DocText(make_form(c, kind="flattened", rotate=ROTATE, extra_page=True)) as doc:
        assert layout_offset(layout, doc, 0) == pytest.approx(0.0, abs=1.0)
        assert layout_offset(layout, doc, 2) == 0.0  # a page beyond the layout's has no offset to find


def test_a_page_fit_gives_the_offset_and_where_each_static_line_was_found(layout):
    c = clients(1, seed=7)[0]
    with DocText(make_form(c, kind="flattened", shift=12)) as doc:
        fit = page_fit(layout, doc, 0)
        assert fit.offset == pytest.approx(12.0, abs=1.0) == layout_offset(layout, doc, 0)
        assert [line.page for line, _ in fit.lines] == [0] * len(fit.lines) and fit.lines
        assert all(found is not None for _, found in fit.lines)
        taxpayer = next(found for line, found in fit.lines if line.text == "taxpayer id")
        assert taxpayer in fit.found_rects() and taxpayer[1] == pytest.approx(
            next(line.rect[1] for line, _ in fit.lines if line.text == "taxpayer id") + 12.0, abs=1.0
        )
    with DocText(make_form(c, kind="flattened")) as doc:
        assert page_fit(layout, doc, 9).lines == () and page_fit(layout, doc, 9).offset == 0.0


def _with_line(layout, line):
    return layout.model_copy(update={"lines": [*layout.lines, line]})


def test_a_learned_line_the_page_does_not_have_anywhere_says_nothing_about_a_box(layout):
    x0, y0, x1, y1 = FIELD_RECTS["tax_id"]
    with DocText(make_form(clients(1, seed=7)[0], kind="flattened")) as doc:
        # what every sample happened to share: an answer, not the form
        beside = StaticLine(page=0, text="northbridge capital partners", rect=(x1 + 4, y0, x1 + 120, y1))
        above = StaticLine(page=0, text="100 federal st", rect=(x0, y0 - 9, x0 + 80, y0 - 1))
        assert page_fit(_with_line(layout, beside), doc, 0).agrees(FIELD_RECTS["tax_id"])
        assert page_fit(_with_line(layout, above), doc, 0).agrees(FIELD_RECTS["tax_id"])


def test_a_learned_line_the_page_has_somewhere_else_still_counts_against_a_box(layout):
    x0, y0, x1, y1 = FIELD_RECTS["tax_id"]
    with DocText(make_form(clients(1, seed=7)[0], kind="flattened")) as doc:
        elsewhere = StaticLine(page=0, text="date of birth or formation", rect=(x1 + 4, y0, x1 + 120, y1))
        assert not page_fit(_with_line(layout, elsewhere), doc, 0).agrees(FIELD_RECTS["tax_id"])


@pytest.mark.parametrize("kind", KINDS)
def test_a_box_agrees_with_the_layout_only_where_its_neighbours_are_at_the_offset(layout, kind):
    c = clients(1, seed=7)[0]
    with DocText(make_form(c, kind=kind, shift=SHIFT)) as doc:
        fit = page_fit(layout, doc, 0)
        assert all(fit.agrees(rect) for rect in FIELD_RECTS.values())  # a copy that is only pushed down
    with DocText(make_form(c, kind=kind, edition="revised")) as doc:
        fit = page_fit(layout, doc, 0)
        assert fit.offset == pytest.approx(REVISED_GAP, abs=1.0)  # most lines moved with the inserted row
        assert not fit.agrees(FIELD_RECTS["investor_name"])  # its label stayed where it was
        assert not fit.agrees(FIELD_RECTS["tax_id"])  # the line above it (the legal name's label) stayed
        assert fit.agrees(FIELD_RECTS["dob"]) and fit.agrees(FIELD_RECTS["owner_1_name"])
    with DocText(make_form(c, kind=kind, edition="swapped")) as doc:
        fit = page_fit(layout, doc, 0)
        assert not fit.agrees(FIELD_RECTS["tax_id"]) and not fit.agrees(FIELD_RECTS["dob"])
        assert not fit.agrees(FIELD_RECTS["country"])  # the line above it moved
        assert fit.agrees(FIELD_RECTS["investor_name"]) and fit.agrees(FIELD_RECTS["address_1"])
    with DocText(make_form(c, kind=kind)) as doc:
        assert page_fit(layout, doc, 1).agrees((72.0, 72.0, 540.0, 90.0))  # page 1 is where the layout has it
