"""Writing widgets into a PDF with PyMuPDF: read existing, write text / choice / circle mark."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from formcast.render.binding import Binding, Target, bind
from formcast.render.fit import Fit
from formcast.render.write import (
    read_existing,
    text_font_for,
    write_choice,
    write_choice_mark,
    write_text,
)
from formcast.template.model import Style

ROOT = Path(__file__).resolve().parents[2]
MENOMONIE = ROOT / "forms" / "dev" / "menomonie_app.pdf"
I9 = ROOT / "forms" / "dev" / "i9.pdf"


def _open(b: bytes) -> pymupdf.Document:
    return pymupdf.open("pdf", b)


def _reopen(doc: pymupdf.Document) -> pymupdf.Document:
    return pymupdf.open("pdf", doc.tobytes())


def _widget(doc: pymupdf.Document, name: str, page: int = 0) -> pymupdf.Widget:
    ws = [w for w in doc[page].widgets() if w.field_name == name]
    assert len(ws) == 1, [w.field_name for w in doc[page].widgets()]
    return ws[0]


def _named(doc: pymupdf.Document, name: str) -> Binding:
    (w,) = [w for w in read_existing(doc) if w.name == name]
    return Binding(name, "named", widget=w)


def _named_on(doc: pymupdf.Document, name: str, page: int) -> Binding:
    (w,) = [w for w in read_existing(doc) if w.name == name and w.page == page]
    return Binding(f"{name}@{page}", "named", widget=w)


def _ap(doc: pymupdf.Document, xref: int, state: str | None = None) -> bytes:
    key = "AP/N" if state is None else f"AP/N/{state}"
    kind, val = doc.xref_get_key(xref, key)
    assert kind == "xref", (kind, val)
    return doc.xref_stream(int(val.split()[0]))


def _q(doc: pymupdf.Document, xref: int) -> int:
    kind, val = doc.xref_get_key(xref, "Q")
    if kind == "null":
        kind, val = doc.xref_get_key(xref, "Parent/Q")
    return int(val) if kind == "int" else 0


def _dark_pixels(page: pymupdf.Page, rect) -> int:
    pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=144)
    s, n = pix.samples, pix.n
    return sum(1 for i in range(0, len(s), n) if min(s[i : i + 3]) < 128)


def _close(a, b, tol=0.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


TEXT_RECT = (100, 200, 250, 216)


# ---------------------------------------------------------------- read_existing


def test_read_existing_kinds_states_and_options(make_pdf):
    doc = _open(
        make_pdf(
            pages=2,
            widgets=[
                {"type": "text", "name": "t", "rect": (72, 72, 200, 88), "maxlen": 9},
                {"type": "checkbox", "name": "cb", "rect": (72, 100, 84, 112), "on_state": "Yes_5"},
                {"type": "combo", "name": "st", "rect": (72, 120, 150, 136), "options": ["WI", "MN"]},
                {"type": "signature", "name": "sig", "rect": (72, 150, 200, 180)},
                {"page": 1, "type": "radio", "name": "color", "rect": (72, 72, 84, 84), "on_state": "Red"},
                {"page": 1, "type": "radio", "name": "color", "rect": (100, 72, 112, 84), "on_state": "Blue"},
            ],
        )
    )
    ws = read_existing(doc)
    by = {(w.page, w.index): w for w in ws}
    assert [(w.page, w.index, w.name, w.kind) for w in ws] == [
        (0, 0, "t", "text"),
        (0, 1, "cb", "checkbox"),
        (0, 2, "st", "combo"),
        (0, 3, "sig", "signature"),
        (1, 0, "color", "radio"),
        (1, 1, "color", "radio"),
    ]
    assert by[0, 0].max_len == 9 and by[0, 0].on_state is None
    assert by[0, 1].on_state == "Yes_5"
    assert by[0, 2].options == ("WI", "MN")
    assert (by[1, 0].on_state, by[1, 1].on_state) == ("Red", "Blue")
    assert _close(by[0, 0].rect, (72, 72, 200, 88))
    assert by[0, 0].xref == _widget(doc, "t").xref


# ---------------------------------------------------------------- write_text


def test_created_text_widget_round_trip(make_pdf):
    doc = _open(make_pdf())
    b = Binding("f_id", "created", new_name="f_id")
    fit = Fit("AB12", 9.5, 1, "ok", False, "")
    name, rect, warns = write_text(
        doc[0], b, text="AB12", fit=fit, style=Style(align="center", comb=6), rect=TEXT_RECT
    )
    assert name == "f_id" and warns == []
    assert _close(rect, TEXT_RECT)
    d2 = _reopen(doc)
    w = _widget(d2, "f_id")
    assert w.field_value == "AB12"
    assert w.text_fontsize == 9.5 and w.text_font == "Helv"
    assert w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB
    assert not w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE
    assert w.text_maxlen == 6
    assert _q(d2, w.xref) == 1
    assert _close(w.rect, TEXT_RECT)
    ap = _ap(d2, w.xref)  # comb: one positioned glyph per cell
    assert all(f"({c})".encode() in ap for c in "AB12")


def test_created_text_widget_multiline_and_right_aligned(make_pdf):
    doc = _open(make_pdf())
    fit = Fit("one two three", 7.0, 2, "shrunk", False, "")
    write_text(doc[0], Binding("m", "created", new_name="m"), text="one two three", fit=fit,
               style=Style(align="right"), rect=(100, 300, 160, 330))
    d2 = _reopen(doc)
    w = _widget(d2, "m")
    assert w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE
    assert not w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB
    assert w.text_fontsize == 7 and _q(d2, w.xref) == 2
    # right aligned: the appearance moves the text away from the left edge
    td = [ln for ln in _ap(d2, w.xref).split(b"\n") if ln.endswith(b"Td")]
    assert max(float(t.split()[0]) for t in td) > 10


def test_created_empty_text_widget(make_pdf):
    doc = _open(make_pdf())
    name, _, _ = write_text(doc[0], Binding("e", "created", new_name="e"), text=None, fit=None,
                            style=Style(), rect=TEXT_RECT)
    assert _widget(_reopen(doc), name).field_value in ("", None)


def test_created_text_requires_rect(make_pdf):
    doc = _open(make_pdf())
    with pytest.raises(ValueError):
        write_text(doc[0], Binding("e", "created", new_name="e"), text="x", fit=None, style=Style())


def test_clear_named_text_widget(make_pdf):
    doc = _open(make_pdf(widgets=[{"type": "text", "name": "old", "rect": TEXT_RECT, "value": "stale"}]))
    name, rect, warns = write_text(doc[0], _named(doc, "old"), text=None, fit=None, style=Style())
    assert name == "old" and warns == []
    d2 = _reopen(doc)
    w = _widget(d2, "old")
    assert w.field_value == ""
    assert b"stale" not in _ap(d2, w.xref)


def test_named_text_widget_gets_fitted_size(make_pdf):
    # PyMuPDF-made widgets are kids of a parent field: update() alone would ignore the size
    doc = _open(make_pdf(widgets=[{"type": "text", "name": "n", "rect": TEXT_RECT, "size": 10}]))
    write_text(doc[0], _named(doc, "n"), text="Hello", fit=Fit("Hello", 6.5, 1, "shrunk", False, ""),
               style=Style())
    d2 = _reopen(doc)
    w = _widget(d2, "n")
    assert w.field_value == "Hello" and w.text_fontsize == 6.5
    assert b"/Helv 6.5 Tf" in _ap(d2, w.xref)


def test_named_widget_keeps_inherited_alignment(make_pdf):
    doc = _open(make_pdf(widgets=[{"type": "text", "name": "r", "rect": TEXT_RECT, "align": "right"}]))
    write_text(doc[0], _named(doc, "r"), text="Hi", fit=Fit("Hi", 10, 1, "ok", False, ""), style=Style())
    d2 = _reopen(doc)
    td = [ln for ln in _ap(d2, _widget(d2, "r").xref).split(b"\n") if ln.endswith(b"Td")]
    assert float(td[0].split()[0]) > 100


def test_font_substituted_for_non_base_font(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "text", "name": "arial", "rect": TEXT_RECT, "font": "Arial"},
        {"type": "text", "name": "cour", "rect": (100, 300, 250, 316), "font": "Cour"},
    ]))
    b_arial, b_cour = _named(doc, "arial"), _named(doc, "cour")
    assert text_font_for(doc[0], b_arial) == "Helv"
    assert text_font_for(doc[0], b_cour) == "Cour"
    assert text_font_for(doc[0], Binding("x", "created", new_name="x")) == "Helv"
    _, rect, warns = write_text(doc[0], b_arial, text="Hi", fit=Fit("Hi", 9, 1, "ok", False, ""), style=Style())
    assert [(w.code, w.severity) for w in warns] == [("font_substituted", "info")]
    assert warns[0].page == 0 and warns[0].rect == rect
    _, _, warns = write_text(doc[0], b_cour, text="Hi", fit=Fit("Hi", 9, 1, "ok", False, ""), style=Style())
    assert warns == []
    d2 = _reopen(doc)
    assert _widget(d2, "arial").text_font == "Helv"
    assert b"/Cour 9 Tf" in _ap(d2, _widget(d2, "cour").xref)


def test_combo_value_not_an_option(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "combo", "name": "st", "rect": TEXT_RECT, "options": ["WI", "MN"]},
    ]))
    b = _named(doc, "st")
    _, _, warns = write_text(doc[0], b, text="TX", fit=Fit("TX", 10, 1, "ok", False, ""), style=Style())
    assert [w.code for w in warns] == ["not_an_option"]
    assert _widget(_reopen(doc), "st").field_value == "TX"
    _, _, warns = write_text(doc[0], b, text="MN", fit=Fit("MN", 10, 1, "ok", False, ""), style=Style())
    assert warns == []
    assert _widget(_reopen(doc), "st").field_value == "MN"


def test_writers_refuse_unwritable_bindings(make_pdf):
    doc = _open(make_pdf(widgets=[{"type": "signature", "name": "sig", "rect": TEXT_RECT}]))
    with pytest.raises(ValueError):
        write_text(doc[0], _named(doc, "sig"), text="x", fit=None, style=Style())
    with pytest.raises(ValueError):
        write_text(doc[0], Binding("k", "none"), text="x", fit=None, style=Style())


# ---------------------------------------------------------------- write_choice


def test_existing_checkbox_uses_its_on_state(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "checkbox", "name": "cb", "rect": (72, 100, 84, 112), "on_state": "Yes_5"},
    ]))
    b = _named(doc, "cb")
    name, rect, warns = write_choice(doc[0], b, on=True, style_circle=False)
    assert name == "cb" and warns == [] and _close(rect, (72, 100, 84, 112))
    d2 = _reopen(doc)
    assert _widget(d2, "cb").field_value == "Yes_5"
    write_choice(doc[0], b, on=False, style_circle=False)
    assert _widget(_reopen(doc), "cb").field_value == "Off"


def test_radio_kid_on_turns_sibling_off():
    doc = pymupdf.open(MENOMONIE)
    existing = read_existing(doc)
    kids = [w for w in existing if w.name == "group_1"]
    assert sorted(k.on_state for k in kids) == ["No", "Yes_5"]
    yes, no = sorted(kids, key=lambda k: k.rect[0])
    tgt = Target("f_g1_no", "f_g1_no", "radio_option", no.page, no.rect, "group_1")
    b = bind([tgt], existing)["f_g1_no"]
    assert b.how == "named" and b.widget == no
    write_choice(doc[no.page], b, on=True, style_circle=False)
    d2 = _reopen(doc)
    states = sorted((w.rect.x0, w.field_value) for w in d2[no.page].widgets() if w.field_name == "group_1")
    assert [v for _, v in states] == ["Off", "No"]
    # switching to the other kid turns this one off
    tgt_yes = Target("f_g1_yes", "f_g1_yes", "radio_option", yes.page, yes.rect, "group_1")
    write_choice(d2[yes.page], bind([tgt_yes], read_existing(d2))["f_g1_yes"], on=True, style_circle=False)
    d3 = _reopen(d2)
    states = sorted((w.rect.x0, w.field_value) for w in d3[yes.page].widgets() if w.field_name == "group_1")
    assert [v for _, v in states] == ["Yes_5", "Off"]


def test_radio_kid_off_stays_off(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "radio", "name": "color", "rect": (72, 72, 84, 84), "on_state": "Red"},
        {"type": "radio", "name": "color", "rect": (100, 72, 112, 84), "on_state": "Blue"},
    ]))
    red, blue = sorted((w for w in read_existing(doc) if w.name == "color"), key=lambda w: w.rect[0])
    write_choice(doc[0], Binding("b", "named", widget=blue), on=True, style_circle=False)
    write_choice(doc[0], Binding("r", "named", widget=red), on=False, style_circle=False)
    d2 = _reopen(doc)
    assert sorted((w.rect.x0, w.field_value) for w in d2[0].widgets()) == [(72, "Off"), (100, "Blue")]


def test_created_choice_is_checkbox(make_pdf):
    doc = _open(make_pdf())
    for key, on, circle, y in (("c1", True, False, 100), ("c2", False, True, 130), ("c3", True, True, 160)):
        name, rect, warns = write_choice(doc[0], Binding(key, "created", new_name=key), on=on,
                                         style_circle=circle, rect=(72, y, 84, y + 12))
        assert name == key and warns == [] and _close(rect, (72, y, 84, y + 12))
    d2 = _reopen(doc)
    got = {w.field_name: (w.field_type, w.field_value) for w in d2[0].widgets()}
    cb = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    assert got == {"c1": (cb, "Yes"), "c2": (cb, "Off"), "c3": (cb, "Yes")}


# ---------------------------------------------------------------- write_choice_mark

MARK = (200, 400, 260, 418)


def test_choice_mark_on_and_off_appearance(make_pdf):
    doc = _open(make_pdf(text=[{"at": (205, 413), "text": "Yes", "size": 11}]))
    page = doc[0]
    name, rect, warns = write_choice_mark(page, Binding("m", "created", new_name="m"), on=True, rect=MARK)
    assert name == "m" and warns == [] and _close(rect, MARK)
    d2 = _reopen(doc)
    w = _widget(d2, "m")
    assert w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX and w.field_value == "Yes"
    on_ap = _ap(d2, w.xref, "Yes")
    assert on_ap.count(b" c") >= 4 and b"S" in on_ap  # stroked four-curve ellipse
    assert _ap(d2, w.xref, "Off").strip() == b""
    blank = _open(make_pdf(text=[{"at": (205, 413), "text": "Yes", "size": 11}]))
    assert _dark_pixels(d2[0], MARK) > _dark_pixels(blank[0], MARK) + 50

    # refill: the mark is re-adopted (Ruling H), switched off, and nothing extra is drawn
    existing = read_existing(d2)
    b = bind([Target("m", "m", "choice_mark", 0, MARK, None)], existing)["m"]
    assert b.how == "adopted"
    name2, _, _ = write_choice_mark(d2[0], b, on=False)
    assert name2 == "m"
    d3 = _reopen(d2)
    assert len(list(d3[0].widgets())) == 1
    assert _widget(d3, "m").field_value == "Off"
    assert _dark_pixels(d3[0], MARK) == _dark_pixels(blank[0], MARK)
    # and back on: the custom ellipse, not the default check, is the "on" look again
    write_choice_mark(d3[0], Binding("m", "adopted", widget=read_existing(d3)[0]), on=True)
    d4 = _reopen(d3)
    assert b"ZaDb" not in _ap(d4, _widget(d4, "m").xref, "Yes")
    assert _widget(d4, "m").field_value == "Yes"


# ---------------------------------------------------------------- geometry


def test_created_widget_rect_on_rotated_page(make_pdf):
    doc = _open(make_pdf(rotate=90))
    _, rect, _ = write_text(doc[0], Binding("r", "created", new_name="r"), text="Hi",
                            fit=Fit("Hi", 10, 1, "ok", False, ""), style=Style(), rect=TEXT_RECT)
    assert _close(rect, TEXT_RECT)
    d2 = _reopen(doc)
    assert d2[0].rotation == 90
    assert _close(_widget(d2, "r").rect, TEXT_RECT)
    _, mrect, _ = write_choice_mark(d2[0], Binding("m", "created", new_name="m"), on=True, rect=MARK)
    assert _close(mrect, MARK)


def test_created_widget_rect_with_offset_cropbox(make_pdf):
    doc = _open(make_pdf(cropbox=(36, 36, 576, 756)))
    _, rect, _ = write_text(doc[0], Binding("c", "created", new_name="c"), text="Hi",
                            fit=Fit("Hi", 10, 1, "ok", False, ""), style=Style(), rect=TEXT_RECT)
    assert _close(rect, TEXT_RECT)
    d2 = _reopen(doc)
    assert _close(_widget(d2, "c").rect, TEXT_RECT)
    assert [w for w in read_existing(d2) if w.name == "c"][0].rect == pytest.approx(TEXT_RECT, abs=0.5)


# ---------------------------------------------------------------- untouched widgets


def test_untouched_widgets_unchanged(make_pdf):
    doc = _open(make_pdf(pages=2, widgets=[
        {"type": "text", "name": "touched", "rect": (72, 72, 200, 88), "value": "a"},
        {"type": "text", "name": "keep_text", "rect": (72, 100, 200, 116), "value": "keep", "font": "Arial"},
        {"type": "checkbox", "name": "keep_cb", "rect": (72, 130, 84, 142), "on_state": "Yes_5"},
        {"type": "checkbox", "name": "touched_cb", "rect": (100, 130, 112, 142)},
        {"type": "combo", "name": "keep_combo", "rect": (72, 160, 150, 176), "options": ["A", "B"]},
        {"type": "signature", "name": "keep_sig", "rect": (72, 190, 200, 220)},
        {"type": "radio", "name": "keep_radio", "rect": (72, 240, 84, 252), "on_state": "R1"},
        {"type": "radio", "name": "keep_radio", "rect": (100, 240, 112, 252), "on_state": "R2"},
        # one field, two kids: writing the page-0 kid must also refresh the page-1 kid (Ruling J)
        {"page": 0, "type": "text", "name": "shared", "rect": (72, 300, 200, 316), "value": "old"},
        {"page": 1, "type": "text", "name": "shared", "rect": (72, 300, 200, 316), "value": "old"},
        {"page": 1, "type": "text", "name": "keep_p1", "rect": (72, 340, 200, 356), "value": "p1"},
    ]))
    def snapshot(d):
        out = {}
        for pg in d:
            for w in pg.widgets():
                if w.field_name.startswith("keep"):
                    out[w.xref] = d.xref_object(w.xref)
                    kind, val = d.xref_get_key(w.xref, "Parent")
                    if kind == "xref":
                        px = int(val.split()[0])
                        out[px] = d.xref_object(px)
        return out
    before = snapshot(doc)
    sibling = _named_on(doc, "shared", 1).widget.xref
    sibling_before = doc.xref_object(sibling)
    page = doc[0]
    write_text(page, _named(doc, "touched"), text="new", fit=Fit("new", 8, 1, "ok", False, ""), style=Style())
    write_text(page, _named_on(doc, "shared", 0), text="fresh", fit=Fit("fresh", 9, 1, "ok", False, ""),
               style=Style())
    write_choice(page, _named(doc, "touched_cb"), on=True, style_circle=False)
    write_text(page, Binding("n", "created", new_name="n"), text="x", fit=Fit("x", 9, 1, "ok", False, ""),
               style=Style(comb=3), rect=(300, 72, 400, 88))
    write_choice(page, Binding("nc", "created", new_name="nc"), on=True, style_circle=True, rect=(300, 100, 312, 112))
    write_choice_mark(page, Binding("nm", "created", new_name="nm"), on=True, rect=(300, 130, 340, 146))
    d2 = _reopen(doc)
    assert snapshot(d2) == before
    assert _widget(d2, "touched").field_value == "new"
    assert d2.xref_object(sibling) != sibling_before  # the sibling kid is expected to change
    assert b"fresh" in _ap(d2, sibling) and b"old" not in _ap(d2, sibling)


# ---------------------------------------------------------------- fix round 1


def test_shared_field_sibling_kid_appearance_on_i9():
    doc = pymupdf.open(I9)
    kids = [w for w in read_existing(doc) if w.name == "Document Title 1"]
    assert sorted(k.page for k in kids) == [0, 3]
    (p0,) = [k for k in kids if k.page == 0]
    (p3,) = [k for k in kids if k.page == 3]
    b = Binding("dt1", "named", widget=p0)
    write_text(doc[0], b, text="Passport", fit=Fit("Passport", 9, 1, "ok", False, ""), style=Style())
    d2 = _reopen(doc)
    assert d2[3].load_widget(p3.xref).field_value == "Passport"
    assert b"(Passport)" in _ap(d2, p3.xref)
    assert b"(Passport)" in _ap(d2, p0.xref)
    write_text(d2[0], Binding("dt1", "named", widget=p0), text=None, fit=None, style=Style())
    d3 = _reopen(d2)
    assert d3[3].load_widget(p3.xref).field_value == ""
    assert b"Passport" not in _ap(d3, p3.xref) and b"Passport" not in _ap(d3, p0.xref)


def test_explicit_align_and_multiline_apply_to_existing_widgets(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "text", "name": "a", "rect": TEXT_RECT, "align": "right"},
        {"type": "text", "name": "b", "rect": (100, 300, 250, 330), "align": "right"},
    ]))
    fit = Fit("Hi", 10, 1, "ok", False, "")
    # explicit center overrides the widget's right alignment
    write_text(doc[0], _named(doc, "a"), text="Hi", fit=fit, style=Style(align="center", multiline=True))
    # defaults (left, not multiline) keep the widget's own /Q and flags
    write_text(doc[0], _named(doc, "b"), text="Hi", fit=fit, style=Style())
    d2 = _reopen(doc)
    a, b = _widget(d2, "a"), _widget(d2, "b")
    assert _q(d2, a.xref) == 1 and d2.xref_get_key(a.xref, "Parent/Q")[1] == "1"
    assert a.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE
    assert _q(d2, b.xref) == 2
    assert not b.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE
    # explicit left on a right-aligned widget is honoured too
    write_text(d2[0], _named(d2, "b"), text="Hi", fit=fit, style=Style(align="left", multiline=False))
    d3 = _reopen(d2)
    assert _q(d3, _widget(d3, "b").xref) == 0


def test_explicit_align_reaches_sibling_kids(make_pdf):
    doc = _open(make_pdf(pages=2, widgets=[
        {"page": 0, "type": "text", "name": "s", "rect": TEXT_RECT},
        {"page": 1, "type": "text", "name": "s", "rect": TEXT_RECT},
    ]))
    sib = _named_on(doc, "s", 1).widget.xref
    write_text(doc[0], _named_on(doc, "s", 0), text="Hi", fit=Fit("Hi", 10, 1, "ok", False, ""),
               style=Style(align="right"))
    d2 = _reopen(doc)
    assert _q(d2, sib) == 2
    td = [ln for ln in _ap(d2, sib).split(b"\n") if ln.endswith(b"Td")]
    assert float(td[0].split()[0]) > 100


def test_combo_display_text_written_as_export_value(make_pdf):
    doc = _open(make_pdf(widgets=[
        {"type": "combo", "name": "st", "rect": TEXT_RECT, "options": [["WI", "Wisconsin"], ["MN", "Minnesota"]]},
    ]))
    b = _named(doc, "st")
    assert b.widget.options == ("Wisconsin", "Minnesota")
    fit = Fit("Wisconsin", 10, 1, "ok", False, "")
    _, _, warns = write_text(doc[0], b, text="Wisconsin", fit=fit, style=Style())
    assert warns == []
    assert _widget(_reopen(doc), "st").field_value == "WI"
    _, _, warns = write_text(doc[0], b, text="MN", fit=Fit("MN", 10, 1, "ok", False, ""), style=Style())
    assert warns == [] and _widget(_reopen(doc), "st").field_value == "MN"
    _, _, warns = write_text(doc[0], b, text="Texas", fit=Fit("Texas", 10, 1, "ok", False, ""), style=Style())
    assert [w.code for w in warns] == ["not_an_option"]
    assert _widget(_reopen(doc), "st").field_value == "Texas"


def test_write_text_refuses_button_widget(make_pdf):
    doc = _open(make_pdf(widgets=[{"type": "checkbox", "name": "cb", "rect": (72, 100, 84, 112)}]))
    with pytest.raises(ValueError):
        write_text(doc[0], _named(doc, "cb"), text="x", fit=None, style=Style())


def test_existing_max_len_cuts_and_warns(make_pdf):
    doc = _open(make_pdf(widgets=[{"type": "text", "name": "zip", "rect": TEXT_RECT, "maxlen": 5}]))
    _, rect, warns = write_text(doc[0], _named(doc, "zip"), text="53751-1234", fit=None, style=Style())
    assert [(w.code, w.page, w.rect) for w in warns] == [("truncated", 0, rect)]
    assert _widget(_reopen(doc), "zip").field_value == "53751"
    _, _, warns = write_text(doc[0], _named(doc, "zip"), text="53751", fit=None, style=Style())
    assert warns == []


def test_choice_mark_has_no_down_appearance_or_check_caption(make_pdf):
    doc = _open(make_pdf())
    write_choice_mark(doc[0], Binding("m", "created", new_name="m"), on=True, rect=MARK)
    d2 = _reopen(doc)
    xref = _widget(d2, "m").xref
    # simulate a viewer/older writer having left a check caption and down appearance, then refill
    d2.xref_set_key(xref, "MK", "<</CA(4)/BC[0 0 0]>>")
    write_choice_mark(d2[0], Binding("m", "adopted", widget=read_existing(d2)[0]), on=True)
    d3 = _reopen(d2)
    assert d3.xref_get_key(xref, "AP/D")[0] == "null"
    assert d3.xref_get_key(xref, "MK/CA")[0] == "null"
    assert d3.xref_get_key(xref, "MK/BC")[0] == "array"


def test_checkbox_sibling_kids_follow_the_field(make_pdf):
    doc = _open(make_pdf(pages=2, widgets=[
        {"page": 0, "type": "checkbox", "name": "agree", "rect": (72, 100, 84, 112)},
        {"page": 1, "type": "checkbox", "name": "agree", "rect": (72, 100, 84, 112)},
    ]))
    k0, k1 = _named_on(doc, "agree", 0), _named_on(doc, "agree", 1)
    write_choice(doc[0], k0, on=True, style_circle=False)
    d2 = _reopen(doc)
    assert d2.xref_get_key(k1.widget.xref, "AS")[1] == "/Yes"
    write_choice(d2[0], k0, on=False, style_circle=False)
    d3 = _reopen(d2)
    assert d3.xref_get_key(k1.widget.xref, "AS")[1] == "/Off"
    assert d3.xref_get_key(k0.widget.xref, "AS")[1] == "/Off"


def test_checkbox_kid_value_lives_on_the_field(make_pdf):
    # PyMuPDF writes /V onto the kid too; a stale kid /V would make a sibling turned off still read on.
    doc = _open(make_pdf(widgets=[
        {"type": "checkbox", "name": "sel", "rect": (72, 100, 84, 112), "on_state": "A"},
        {"type": "checkbox", "name": "sel", "rect": (100, 100, 112, 112), "on_state": "B"},
    ]))  # fmt: skip
    a, b = [w for w in read_existing(doc) if w.name == "sel"]
    write_choice(doc[0], Binding("a", "named", widget=a), on=True, style_circle=False)
    write_choice(doc[0], Binding("b", "named", widget=b), on=True, style_circle=False)
    d2 = _reopen(doc)
    kids = [w for w in d2[0].widgets() if w.field_name == "sel"]
    assert [d2.xref_get_key(w.xref, "AS")[1] for w in kids] == ["/Off", "/B"]
    assert all(d2.xref_get_key(w.xref, "V")[0] == "null" for w in kids)
    parent = int(d2.xref_get_key(kids[0].xref, "Parent")[1].split()[0])
    assert d2.xref_get_key(parent, "V") == ("name", "/B")
    assert [w.field_value for w in kids] == ["B", "B"]  # the field's value, no stale "A"
