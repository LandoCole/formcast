"""Synthetic PDF builder for the widget-writing tests.

``make_pdf`` returns PDF bytes: Letter pages, optional ``/Rotate`` and crop box, static text and
widgets. Rects and text positions are in unrotated crop-box space, like every formcast rect.

Widget specs are dicts ``{page, type, name, rect, value?, on_state?, options?, maxlen?, font?,
align?, mk_r?}`` with type ``text | checkbox | radio | combo | signature``. Radio kids sharing a name form
one group, each kid's on-state from its ``on_state`` (PyMuPDF takes a new radio kid's on-state from
its ``field_value``); the group starts with every kid off. Text or checkbox widgets sharing a name
become kids of one field (PyMuPDF groups them). ``options`` entries are strings or
``[export, display]`` pairs. ``font`` writes the field's ``/DA`` font
directly (PyMuPDF itself only creates Helv/Cour/TiRo/ZaDb); ``align`` sets ``/Q`` on the parent field
only, the way many real forms store it; ``mk_r`` sets the widget's ``/MK /R`` (text drawn rotated).
Static text entries ``{page?, at, text, size?, rotate?}``; ``rotate`` (as in ``insert_text``) makes
the text read upright on a page with the same ``/Rotate``.
"""

from __future__ import annotations

import pymupdf
import pytest

PAGE_W, PAGE_H = 612, 792

_TYPES = {
    "text": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "combo": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
}
_Q = {"left": 0, "center": 1, "right": 2}


def _parent(doc: pymupdf.Document, xref: int) -> int:
    kind, val = doc.xref_get_key(xref, "Parent")
    return int(val.split()[0]) if kind == "xref" else xref


def _rename_on_state(doc: pymupdf.Document, xref: int, on: str) -> None:
    for key in ("AP/N", "AP/D"):
        kind, val = doc.xref_get_key(xref, key)
        if kind == "dict":
            doc.xref_set_key(xref, key, val.replace("/Yes", f"/{on}"))


def _add(doc: pymupdf.Document, spec: dict) -> None:
    page = doc[spec.get("page", 0)]
    kind = spec["type"]
    w = pymupdf.Widget()
    w.field_type = _TYPES[kind]
    w.field_name = spec["name"]
    w.rect = pymupdf.Rect(spec["rect"])
    w.text_fontsize = spec.get("size", 10)
    if kind == "text":
        w.field_value = spec.get("value", "")
        if spec.get("maxlen"):
            w.text_maxlen = spec["maxlen"]
        if spec.get("font") in ("Helv", "Cour", "TiRo"):
            w.text_font = spec["font"]
    elif kind == "combo":
        w.choice_values = list(spec.get("options", ()))
        w.field_value = spec.get("value", "")
    elif kind == "checkbox":
        w.field_value = False
    elif kind == "radio":
        w.field_value = spec.get("on_state", "Yes")
    page.add_widget(w)
    field = _parent(doc, w.xref)
    if kind == "checkbox" and spec.get("on_state", "Yes") != "Yes":
        _rename_on_state(doc, w.xref, spec["on_state"])
    if kind == "radio":
        doc.xref_set_key(field, "V", "/Off")
        doc.xref_set_key(w.xref, "AS", "/Off")
    if spec.get("font") and spec["font"] not in ("Helv", "Cour", "TiRo"):
        doc.xref_set_key(field, "DA", pymupdf.get_pdf_str(f"/{spec['font']} {w.text_fontsize:g} Tf 0 g"))
    if spec.get("align"):
        doc.xref_set_key(field, "Q", str(_Q[spec["align"]]))
    if spec.get("mk_r"):
        doc.xref_set_key(w.xref, "MK/R", str(spec["mk_r"]))
        w.update()


def _build(*, pages=1, rotate=0, cropbox=None, widgets=(), text=()) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if cropbox is not None:
            page.set_cropbox(pymupdf.Rect(cropbox))
        if rotate:
            page.set_rotation(rotate)
    for t in text:
        doc[t.get("page", 0)].insert_text(t["at"], t["text"], fontsize=t.get("size", 11), rotate=t.get("rotate", 0))
    for spec in widgets:
        _add(doc, spec)
    return doc.tobytes()


@pytest.fixture
def make_pdf():
    return _build
