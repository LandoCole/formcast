"""DocText: one PDF's widget values, words, labels, boxes, marks and phrases, on the generated corpus forms."""

import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.extract.page import DocText, label_key
from tests.extract.corpus import (
    FIELD_RECTS,
    INVESTOR_TYPES,
    ROTATE,
    SHIFT,
    TYPE_BOXES,
    WIRE_RECT,
    clients,
    make_form,
)

CLIENT = clients(4)[1]  # a "joint" investor with an SSN-shaped tax id
FILLABLE = make_form(CLIENT, kind="fillable")
FLATTENED = make_form(CLIENT, kind="flattened")


def _shifted(rect, dy):
    return (rect[0], rect[1] + dy, rect[2], rect[3] + dy)


def _lines_pdf(*lines: str) -> bytes:
    """A one-page PDF with each string on its own line."""
    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(lines):
        page.insert_text((72, 100 + 20 * n), line, fontsize=10, fontname="helv")
    return doc.tobytes()


def _radio_pdf(value: str | None, states=("indiv", "joint", "llc")) -> bytes:
    """A one-page PDF with a radio group "kind": one field, one kid per state, the kid for ``value`` selected."""
    doc = pymupdf.open()
    page = doc.new_page()
    parent = doc.get_new_xref()
    doc.update_object(parent, "<<>>")
    kids = []
    for n, state in enumerate(states):
        on, off = doc.get_new_xref(), doc.get_new_xref()
        for xref, ink in ((on, b"0 0 12 12 re S"), (off, b"")):
            doc.update_object(xref, "<< /Type /XObject /Subtype /Form /BBox [0 0 12 12] >>")
            doc.update_stream(xref, ink)
        kid, y = doc.get_new_xref(), 72 + 30 * n
        selected = state if state == value else "Off"
        doc.update_object(
            kid,
            f"<< /Type /Annot /Subtype /Widget /Rect [72 {y} 84 {y + 12}] /Parent {parent} 0 R /F 4 /AS /{selected}"
            f" /AP << /N << /{state} {on} 0 R /Off {off} 0 R >> >> >>",
        )
        kids.append(kid)
    refs = " ".join(f"{k} 0 R" for k in kids)
    v = f"/V /{value} " if value else ""
    doc.update_object(parent, f"<< /FT /Btn /Ff 32768 /T (kind) {v}/Kids [{refs}] >>")
    doc.xref_set_key(page.xref, "Annots", f"[{refs}]")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<< /Fields [{parent} 0 R] >>")
    return doc.tobytes()


# --------------------------------------------------------------------- label_key


def test_label_keys_keep_letters_and_digits_only():
    assert label_key("Taxpayer I.D.:") == label_key("TAXPAYER ID") == "taxpayerid"
    assert label_key("Ｔaxpayer ID") == "taxpayerid"  # NFKC: a full-width letter, a no-break space
    assert label_key("Line 5a:") == "line5a"
    assert label_key("  : - ") == ""


# ----------------------------------------------------------------- form fields


def test_widget_values_and_checkbox_states_read_exactly():
    with DocText(FILLABLE) as doc:
        assert doc.page_count == 2
        assert doc.has_values
        assert doc.widget_value("tax_id") == CLIENT.tax_id
        assert doc.widget_value("investor_name") == CLIENT.legal_name
        assert doc.widget_value("owner_1_pct") == CLIENT.owners[0][1]
        assert doc.widget_value("type_joint") is True
        assert [doc.widget_value(f"type_{t}") for t in INVESTOR_TYPES] == [t == "joint" for t in INVESTOR_TYPES]
        assert doc.widget_value("no_such_field") is None
        assert len(CLIENT.owners) < 4 and doc.widget_value("owner_4_name") is None  # an empty field is None


def test_a_blank_or_flattened_form_has_no_values():
    with DocText(make_form(None, kind="fillable")) as blank:
        assert not blank.has_values
        assert blank.widget_value("tax_id") is None
        assert blank.widget_value("type_joint") is False  # a check box that is off is False, not missing
    with DocText(FLATTENED) as flat:
        assert not flat.has_values
        assert flat.widget_value("tax_id") is None


def test_a_checkbox_is_on_by_its_on_state_whatever_that_is():
    doc = pymupdf.open()
    page = doc.new_page()
    for n, (name, on_value) in enumerate([("a", "Yes"), ("b", "On"), ("c", "1")]):
        w = pymupdf.Widget()
        w.field_name = name
        w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = pymupdf.Rect(72, 72 + 30 * n, 84, 84 + 30 * n)
        w.field_value = on_value if name != "c" else "Off"
        page.add_widget(w)
    with DocText(doc.tobytes()) as text:
        assert text.widget_value("a") is True and text.widget_value("b") is True
        assert text.widget_value("c") is False


def test_a_radio_group_reads_as_the_selected_buttons_state():
    with DocText(_radio_pdf("joint")) as doc:
        assert doc.has_values
        assert doc.widget_value("kind") == "joint"
        assert doc.widget_on("kind", "joint") is True
        assert doc.widget_on("kind", "indiv") is False and doc.widget_on("kind", "llc") is False
        assert doc.widget_on("kind") is True  # some button is on
    with DocText(_radio_pdf(None)) as doc:
        assert not doc.has_values
        assert doc.widget_value("kind") is None
        assert doc.widget_on("kind", "joint") is False and doc.widget_on("kind") is False


def test_widget_on_answers_for_check_boxes_and_none_for_what_is_not_one():
    with DocText(FILLABLE) as doc:
        assert doc.widget_on("type_joint") is True and doc.widget_on("type_llc") is False
        assert doc.widget_on("type_joint", "Yes") is True  # a check box has no group: state is not asked
        assert doc.widget_on("no_such_field") is None
        assert doc.widget_on("tax_id") is None  # a text field is not a check box


def test_the_input_may_be_a_path_and_bad_input_is_a_formcast_error(tmp_path):
    path = tmp_path / "form.pdf"
    path.write_bytes(FILLABLE)
    with DocText(path) as doc:
        assert doc.widget_value("tax_id") == CLIENT.tax_id
    with pytest.raises(FormcastError, match="cannot open PDF"):
        DocText(b"not a pdf")
    with pytest.raises(FormcastError, match="cannot open PDF"):
        DocText(tmp_path / "missing.pdf")


# ------------------------------------------------------------------------ words


def test_words_come_in_reading_order():
    with DocText(FLATTENED) as doc:
        words = doc.words(0)
        texts = [w.text for w in words]
        assert texts[:3] == ["Client", "Information", "Form"]
        assert texts.index("Taxpayer") < texts.index(CLIENT.tax_id) < texts.index("Date")
        assert doc.words(0) is not doc.words(0) and doc.words(0) == words  # a copy of the cached list
        assert doc.words(5) == [] and doc.words(-1) == []


def test_a_painted_fillable_value_is_read_once_not_twice():
    painted = make_form(CLIENT, kind="fillable", painted=True)
    with DocText(painted) as doc:
        texts = [w.text for w in doc.words(0)]
        assert texts.count(CLIENT.tax_id) == 1
        assert doc.text_in(0, FIELD_RECTS["tax_id"]) == CLIENT.tax_id
        assert doc.text_in(0, FIELD_RECTS["investor_name"]) == CLIENT.legal_name
        found = doc.find_label(["Taxpayer ID"], 0)
        assert doc.text_right_of(0, found[0][1], 200)[0] == CLIENT.tax_id
    with DocText(FILLABLE) as doc, DocText(painted) as both:
        # the painted copy differs only by the "X" it paints in its checked box
        assert [w.text for w in both.words(0) if w.text != "X"] == [w.text for w in doc.words(0)]


def test_a_fillable_value_stays_when_only_its_appearance_has_it():
    with DocText(FILLABLE) as doc:
        assert doc.text_in(0, FIELD_RECTS["tax_id"]) == CLIENT.tax_id


def test_text_outside_form_fields_is_never_thinned():
    # the same word twice on one spot of a page without form fields is left as the PDF has it
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Repeated", fontsize=10, fontname="helv")
    page.insert_text((73, 100), "Repeated", fontsize=10, fontname="helv")
    with DocText(doc.tobytes()) as text:
        assert [w.text for w in text.words(0)] == ["Repeated", "Repeated"]


# ----------------------------------------------------------------------- labels


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_a_label_is_found_without_regard_to_case_spacing_or_punctuation(kind):
    with DocText(make_form(CLIENT, kind=kind)) as doc:
        for spelling in ("taxpayer i.d.", "TAXPAYER  ID:", "Taxpayer ID"):
            hits = doc.find_label([spelling], 0)
            assert len(hits) == 1 and hits[0][0] == 0
        page, rect = doc.find_label(["Taxpayer ID"], 0)[0]
        assert rect[0] == pytest.approx(72, abs=1) and 135 < rect[1] < 145 and rect[2] < 135


def test_any_of_a_labels_wordings_finds_it():
    tin = make_form(CLIENT, kind="flattened", labels={"Taxpayer ID:": "TIN:"})
    with DocText(tin) as doc:
        assert len(doc.find_label(["Taxpayer ID", "TIN"], 0)) == 1
        assert doc.find_label(["Taxpayer ID"], 0) == []
    with DocText(FLATTENED) as doc:
        assert len(doc.find_label(["Taxpayer ID", "TIN"], 0)) == 1


def test_a_wording_inside_a_longer_one_is_the_same_place():
    with DocText(FLATTENED) as doc:
        hits = doc.find_label(["Date of birth or formation", "Date of birth"], 0)
        assert len(hits) == 1
        assert hits[0][1][2] > 180  # the longer wording's rectangle, up to "formation:"


def test_a_label_found_twice_returns_both_places():
    twice = make_form(CLIENT, kind="flattened", header="Taxpayer ID:")
    with DocText(twice) as doc:
        hits = doc.find_label(["Taxpayer ID"], 0)
        assert len(hits) == 2
        assert hits[0][1][1] < hits[1][1][1]  # reading order: the header first, then the row
        assert doc.find_label(["Taxpayer ID"], None) == hits
        assert doc.find_label(["Taxpayer ID"], 1) == []


def test_a_label_split_over_lines_or_off_the_page_is_not_found():
    with DocText(_lines_pdf("Taxpayer", "ID: 12-3456789")) as doc:
        assert doc.find_label(["Taxpayer ID"], 0) == []
        assert doc.find_label(["ID"], 0) != []
    with DocText(FLATTENED) as doc:
        assert doc.find_label(["Taxpayer ID"], 7) == []
        assert doc.find_label(["", "..."], 0) == []


def test_a_separate_colon_belongs_to_its_label():
    with DocText(_lines_pdf("Taxpayer ID : 12-3456789")) as doc:
        (_, rect), = doc.find_label(["Taxpayer ID"], 0)
        assert doc.text_right_of(0, rect, 200)[0] == "12-3456789"


def test_words_on_a_rotated_page_are_in_unrotated_points():
    upright = make_form(CLIENT, kind="flattened")
    rotated = make_form(CLIENT, kind="flattened", rotate=ROTATE)
    with DocText(upright) as a, DocText(rotated) as b:
        assert b.find_label(["Taxpayer ID"], 0) == a.find_label(["Taxpayer ID"], 0)
        assert b.text_in(0, FIELD_RECTS["tax_id"]) == CLIENT.tax_id
        assert [w.rect for w in a.words(0)] == [w.rect for w in b.words(0)]


# --------------------------------------------------------------- right and below


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_text_right_of_a_label_stops_at_the_next_label(kind):
    with DocText(make_form(CLIENT, kind=kind)) as doc:
        (_, label), = doc.find_label(["Taxpayer ID"], 0)
        text, rect = doc.text_right_of(0, label, 300)
        assert text == CLIENT.tax_id
        assert rect[0] > label[2] and rect[2] < 400


def test_text_right_of_stops_at_a_colon_a_wide_gap_and_the_width():
    with DocText(_lines_pdf("Name: Dana Keller     Date: 05/01/2020")) as doc:
        (_, label), = doc.find_label(["Name"], 0)
        assert doc.text_right_of(0, label, 300)[0] == "Dana Keller"
        assert doc.text_right_of(0, label, 40)[0] == "Dana"
        assert doc.text_right_of(0, label, 1) is None
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Name:", fontsize=10, fontname="helv")
    page.insert_text((120, 100), "Dana", fontsize=10, fontname="helv")
    page.insert_text((250, 100), "Far", fontsize=10, fontname="helv")  # more than three em after "Dana"
    with DocText(doc.tobytes()) as text:
        (_, label), = text.find_label(["Name"], 0)
        assert text.text_right_of(0, label, 400)[0] == "Dana"


def test_text_right_of_does_not_take_the_next_label_with_it():
    with DocText(_lines_pdf("Name: Dana Keller Date of birth: 05/01/2020")) as doc:
        (_, label), = doc.find_label(["Name"], 0)
        assert doc.text_right_of(0, label, 400)[0] == "Dana Keller"
    # an empty answer: only the next label follows, so there is nothing to read
    with DocText(_lines_pdf("Taxpayer ID:      Date of birth: 05/01/2020")) as doc:
        (_, label), = doc.find_label(["Taxpayer ID"], 0)
        assert doc.text_right_of(0, label, 400) is None
    with DocText(_lines_pdf("Name: Dana Keller Taxpayer ID: 12-3456789")) as doc:
        (_, label), = doc.find_label(["Name"], 0)
        assert doc.text_right_of(0, label, 400) is None  # capitalised words only: not told apart, so not read
    with DocText(_lines_pdf("Name: Dana Keller      Taxpayer ID: 12-3456789")) as doc:
        (_, label), = doc.find_label(["Name"], 0)
        assert doc.text_right_of(0, label, 400)[0] == "Dana Keller"  # a wide gap tells them apart


def test_text_right_of_ignores_other_rows_and_words_to_the_left():
    with DocText(FLATTENED) as doc:
        (_, label), = doc.find_label(["Country"], 0)
        assert doc.text_right_of(0, label, 300)[0] == CLIENT.country
        (_, title), = doc.find_label(["Information"], 0)
        assert doc.text_right_of(0, title, 300)[0] == "Form"
        # the blank form has no answer to the right of the label
    with DocText(make_form(None, kind="flattened")) as blank:
        (_, label), = blank.find_label(["Country"], 0)
        assert blank.text_right_of(0, label, 300) is None


def test_text_below_reads_the_address_lines():
    with DocText(FLATTENED) as doc:
        (_, label), = doc.find_label(["Mailing address"], 0)
        text, rect = doc.text_below(0, label, 2)
        assert text == f"{CLIENT.street} {CLIENT.city_state_zip}"
        assert rect[0] >= label[0] and rect[1] > label[3]
        assert doc.text_below(0, label, 1)[0] == CLIENT.street
        assert doc.text_below(0, label, 0) is None


def test_text_below_stops_at_a_label_and_at_a_wide_gap():
    with DocText(_lines_pdf("Address", "1 Main Street", "Next label:", "after")) as doc:
        (_, label), = doc.find_label(["Address"], 0)
        assert doc.text_below(0, label, 5)[0] == "1 Main Street"
    with DocText(make_form(None, kind="flattened")) as blank:
        # nothing is written under the label of the blank form, and the next heading is far below
        (_, label), = blank.find_label(["Mailing address"], 0)
        assert blank.text_below(0, label, 3) is None


# ------------------------------------------------------------------------- boxes


def test_text_in_reads_the_words_whose_centre_is_in_the_box():
    with DocText(FLATTENED) as doc:
        assert doc.text_in(0, FIELD_RECTS["investor_name"]) == CLIENT.legal_name
        assert doc.text_in(0, FIELD_RECTS["owner_1_pct"]) == CLIENT.owners[0][1]
        assert doc.text_in(0, (0.0, 0.0, 10.0, 10.0)) is None
        assert doc.text_in(3, FIELD_RECTS["tax_id"]) is None
        # a box that only clips the edge of a word does not take it: the centre decides
        (_, label), = doc.find_label(["Taxpayer ID"], 0)
        assert doc.text_in(0, (label[0] - 5, label[1], label[0] + 5, label[3])) is None


def test_text_in_on_a_shifted_page_needs_the_shifted_box():
    shifted = make_form(CLIENT, kind="flattened", shift=SHIFT)
    with DocText(shifted) as doc:
        assert doc.text_in(0, _shifted(FIELD_RECTS["address_1"], SHIFT)) == CLIENT.street
        assert doc.text_in(0, _shifted(FIELD_RECTS["address_2"], SHIFT)) == CLIENT.city_state_zip
        # each address row's own box never takes the other row's text, even with the 14 pt shift
        assert doc.text_in(0, FIELD_RECTS["address_1"]) != CLIENT.street


# ------------------------------------------------------------------------- marks


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
@pytest.mark.parametrize("rotate", [0, ROTATE])
def test_a_drawn_mark_is_seen_and_an_empty_box_is_not(kind, rotate):
    with DocText(make_form(CLIENT, kind=kind, rotate=rotate)) as doc:
        marks = {t: doc.marked(0, TYPE_BOXES[t][1]) for t in INVESTOR_TYPES}
    assert marks == {t: t == CLIENT.investor_type for t in INVESTOR_TYPES}


def test_a_painted_mark_on_a_fillable_copy_is_seen():
    with DocText(make_form(CLIENT, kind="fillable", painted=True)) as doc:
        assert doc.marked(0, TYPE_BOXES[CLIENT.investor_type][1]) is True


def test_a_mark_on_the_shifted_page_is_where_the_shifted_box_is():
    with DocText(make_form(CLIENT, kind="flattened", shift=SHIFT)) as doc:
        assert doc.marked(0, _shifted(TYPE_BOXES["joint"][1], SHIFT)) is True
        assert doc.marked(0, _shifted(TYPE_BOXES["llc"][1], SHIFT)) is False


def test_a_box_off_the_page_is_not_marked_or_unmarked():
    with DocText(FLATTENED) as doc:
        assert doc.marked(0, (700.0, 900.0, 712.0, 912.0)) is None
        assert doc.marked(0, (-30.0, -30.0, -10.0, -10.0)) is None
        assert doc.marked(9, TYPE_BOXES["joint"][1]) is None


def test_ink_alone_makes_a_mark_and_a_hairline_border_does_not():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 112, 112), width=0.75)  # empty box
    page.draw_rect(pymupdf.Rect(200, 100, 212, 112), width=0.75)
    page.draw_line((202, 102), (210, 110), width=1.2)  # a stroke, not a glyph
    page.draw_line((202, 110), (210, 102), width=1.2)
    with DocText(doc.tobytes()) as text:
        assert text.marked(0, (100, 100, 112, 112)) is False
        assert text.marked(0, (200, 100, 212, 112)) is True


# ----------------------------------------------------------------------- phrases


def test_after_phrase_reads_the_account_digits():
    with DocText(FLATTENED) as doc:
        (hit,) = doc.after_phrase(["account"], 0, "digits")
        page, text, rect = hit
        assert page == 0 and text.replace(" ", "") == CLIENT.account
        assert WIRE_RECT[0] <= rect[0] and rect[3] <= WIRE_RECT[3] + 5
        assert doc.after_phrase(["account"], None, "digits")[0][:2] == (0, text)


def test_after_phrase_reads_up_to_a_comma_across_a_line_break():
    with DocText(FLATTENED) as doc:
        (hit,) = doc.after_phrase(["Funds will be sent from"], 0, "comma")
        assert hit[1] == CLIENT.bank
    with DocText(_lines_pdf("Sent from First", "Harbor Bank, then more")) as doc:
        assert doc.after_phrase(["sent from"], 0, "comma")[0][1] == "First Harbor Bank"


def test_after_phrase_stops_at_a_sentence_or_the_end_of_the_line():
    pdf = _lines_pdf("The fee is 12.5 percent. Paid monthly. Late fees", "apply after ten days")
    with DocText(pdf) as doc:
        assert doc.after_phrase(["the fee is"], 0, "sentence")[0][1] == "12.5 percent"
        assert doc.after_phrase(["paid"], 0, "sentence")[0][1] == "monthly"
        assert doc.after_phrase(["paid"], 0, "line")[0][1] == "monthly. Late fees"
        assert doc.after_phrase(["apply after"], 0, "sentence") == []  # no full stop follows: nothing to read
    with DocText(_lines_pdf("Total due: 40.")) as doc:
        assert doc.after_phrase(["total due"], 0, "sentence")[0][1] == "40"


def test_a_digits_read_stops_at_the_end_of_the_line():
    with DocText(_lines_pdf("Wire to account 12345678", "2 Signature of investor")) as doc:
        assert doc.after_phrase(["account"], 0, "digits")[0][1] == "12345678"
        assert doc.after_phrase(["account"], 0, "line")[0][1] == "12345678"
        assert doc.after_phrase(["account"], 0, "comma") == []  # comma and sentence may cross lines
    with DocText(_lines_pdf("Wire to account", "12345678")) as doc:
        assert doc.after_phrase(["account"], 0, "digits") == []  # a wrapped number is not joined


def test_after_phrase_returns_every_occurrence_and_any_wording():
    with DocText(_lines_pdf("Ref 123, other", "Reference 456, more", "ref 789")) as doc:
        hits = doc.after_phrase(["ref", "reference"], 0, "digits")
        assert [h[1] for h in hits] == ["123", "456", "789"]
        assert doc.after_phrase(["ref"], 0, "comma")[0][1] == "123"
        assert doc.after_phrase(["nothing like it"], 0, "digits") == []
        with pytest.raises(ValueError, match="stop must be"):
            doc.after_phrase(["ref"], 0, "paragraph")


def test_a_phrase_followed_by_a_colon_still_reads_what_comes_after():
    with DocText(_lines_pdf("Account number : 1234 5678, ok")) as doc:
        assert doc.after_phrase(["account number"], 0, "digits")[0][1] == "1234 5678"


def test_phrases_on_a_rotated_fillable_copy_read_the_same():
    with DocText(make_form(CLIENT, kind="fillable", rotate=ROTATE)) as doc:
        assert doc.after_phrase(["account"], 0, "digits")[0][1].replace(" ", "") == CLIENT.account
