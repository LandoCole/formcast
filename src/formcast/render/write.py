"""Write bound values into a PDF as real, editable AcroForm widgets (fill design sections 5.1, 5.3).

The PDF layer under ``fill``: read the widgets a PDF already has, then write one target at a time
into a named/adopted widget or a new one. Every touched widget gets its appearance from PyMuPDF's
``Widget.update()`` (a choice_mark's custom ellipse is written after the last ``update()``);
``NeedAppearances`` is never set. Widgets of fields no call writes are left as they are; sibling
kids of a written field share its value, so their appearances are regenerated too (Ruling J).

PyMuPDF behaviour these functions rely on is recorded as Task 5 rulings in
``docs/superpowers/plans/2026-10-21-formcast-fill-rulings.md``.
"""

from __future__ import annotations

import re

import pymupdf
from pymupdf import mupdf

from formcast.geometry import Rect
from formcast.pagemap.extract import WIDGET_KINDS, choice_options, choice_pairs
from formcast.render.binding import Binding, ExistingWidget
from formcast.render.fit import Fit
from formcast.render.report import FillWarning
from formcast.template.model import Style

__all__ = ["read_existing", "text_font_for", "write_choice", "write_choice_mark", "write_text"]

# Fonts MuPDF's regenerated text appearance honours; any other /DA font is drawn in Helvetica.
KEPT_FONTS = ("Helv", "Cour", "TiRo", "ZaDb")
DEFAULT_FONT = "Helv"
_QUADDING = {"left": 0, "center": 1, "right": 2}
MARK_INSET = 1.0  # choice_mark ellipse inset from the widget rect, points
MARK_LINE_WIDTH = 1.0
_KAPPA = 0.5523  # Bezier control-point factor for a quarter ellipse
_REF = re.compile(r"(\d+) \d+ R")


# ---------------------------------------------------------------- reading


def read_existing(doc: pymupdf.Document) -> list[ExistingWidget]:
    """Every widget of ``doc``, in page then ``page.widgets()`` order."""
    out: list[ExistingWidget] = []
    for page in doc:
        for index, w in enumerate(page.widgets() or []):
            kind = WIDGET_KINDS.get(w.field_type)
            if kind is None:
                continue
            on_state = _on_state(w) if kind in ("checkbox", "radio") else None
            r = w.rect
            out.append(
                ExistingWidget(
                    page=page.number,
                    index=index,
                    xref=w.xref,
                    name=w.field_name or "",
                    kind=kind,
                    rect=(r.x0, r.y0, r.x1, r.y1),
                    on_state=on_state,
                    max_len=(w.text_maxlen or None) if kind == "text" else None,
                    options=tuple(choice_options(w)) if kind in ("combo", "list") else (),
                )
            )
    return out


_ESCAPE = re.compile(r"#([0-9A-Fa-f]{2})")


def _decode_name(spelling: str) -> str:
    """A PDF name as written (``Yes#205``) decoded to the name itself (``Yes 5``)."""
    if "#" not in spelling:
        return spelling
    raw = _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), spelling).encode("latin-1")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _on_state(w: pymupdf.Widget) -> str | None:
    """The button's "on" appearance name, decoded (``Yes#205`` -> ``Yes 5``); ``None`` if it has none.

    PyMuPDF's ``on_state()`` returns the escaped spelling of the /AP key, returns ``True`` (and
    prints a warning) when there is no on-state, and raises when /AP /D is absent and /N has only
    /Off; this reads ``button_states()`` instead.
    """
    states = w.button_states() or {}
    for key in ("normal", "down"):
        for name in states.get(key) or ():
            if name != "Off":
                return _decode_name(name)
    return None


def _kept_font(name: str | None) -> str | None:
    for f in KEPT_FONTS:
        if (name or "").lower() == f.lower():
            return f
    return None


def text_font_for(page: pymupdf.Page, binding: Binding) -> str:
    """The font the written appearance will use (``Helv``, ``Cour``, ``TiRo`` or ``ZaDb``).

    Fit text with it (``pymupdf.get_text_length`` accepts these names).
    """
    if binding.how == "created" or binding.widget is None:
        return DEFAULT_FONT
    return _kept_font(_load(page, binding.widget).text_font) or DEFAULT_FONT


# ---------------------------------------------------------------- helpers


def _check_writable(binding: Binding) -> None:
    if binding.how == "none":
        raise ValueError(f"binding {binding.key!r} has no widget to write")
    if binding.how == "created":
        if not binding.new_name:
            raise ValueError(f"created binding {binding.key!r} has no new_name")
        return
    if binding.widget is None:
        raise ValueError(f"binding {binding.key!r} ({binding.how}) carries no widget")
    if binding.widget.kind == "signature":
        raise ValueError(f"widget {binding.widget.name!r} is a signature field and is not writable")


def _load(page: pymupdf.Page, widget: ExistingWidget) -> pymupdf.Widget:
    if page.number != widget.page:
        raise ValueError(f"widget {widget.name!r} is on page {widget.page}, not page {page.number}")
    w = page.load_widget(widget.xref)
    if w is None:
        raise ValueError(f"no widget with xref {widget.xref} on page {page.number}")
    return w


def _need_rect(binding: Binding, rect: Rect | None) -> pymupdf.Rect:
    if rect is None:
        raise ValueError(f"created binding {binding.key!r} needs the target rect")
    return pymupdf.Rect(rect)


def _read_back(page: pymupdf.Page, xref: int) -> tuple[str, Rect]:
    w = page.load_widget(xref)
    r = w.rect
    return w.field_name or "", (r.x0, r.y0, r.x1, r.y1)


def _new_widget(page: pymupdf.Page, binding: Binding, field_type: int, rect: Rect | None) -> pymupdf.Widget:
    w = pymupdf.Widget()
    w.field_type = field_type
    w.field_name = binding.new_name
    w.rect = _need_rect(binding, rect)
    return w


def _parent_xref(doc: pymupdf.Document, xref: int) -> int | None:
    kind, val = doc.xref_get_key(xref, "Parent")
    return int(val.split()[0]) if kind == "xref" else None


def _inherited(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    x: int | None = xref
    seen: set[int] = set()
    while x is not None and x not in seen:
        seen.add(x)
        kind, val = doc.xref_get_key(x, key)
        if kind != "null":
            return kind, val
        x = _parent_xref(doc, x)
    return "null", "null"


def _da(color, font: str, size: float) -> str:
    col = tuple(color or (0,))
    op = {1: "g", 3: "rg", 4: "k"}.get(len(col), "g")
    if op == "g" and len(col) != 1:
        col = (0,)
    nums = " ".join(f"{c:g}" for c in col)
    return f"{nums} {op} /{font} {size:g} Tf"


def _warn(code, message: str, page: pymupdf.Page, rect: Rect) -> FillWarning:
    return FillWarning.make(code, message, page=page.number, rect=rect)


def _set_text_value(page: pymupdf.Page, w: pymupdf.Widget, value: str | None) -> None:
    """Set (or clear) a text/choice widget's value; ``update()`` follows."""
    if value:
        w.field_value = value
        return
    # update() skips an empty value, so clear /V through MuPDF first (it finds the right field).
    pdf = mupdf.pdf_document_from_fz_document(page.parent.this)
    mupdf.pdf_set_field_value(pdf, mupdf.pdf_load_object(pdf, w.xref), "", 1)
    w.field_value = ""


def _field_xref(doc: pymupdf.Document, xref: int) -> int:
    """The terminal field a widget belongs to: itself if it carries /T, else its /Parent."""
    if doc.xref_get_key(xref, "T")[0] != "null":
        return xref
    return _parent_xref(doc, xref) or xref


def _sibling_kids(doc: pymupdf.Document, xref: int) -> list[int]:
    """Other widget kids of the same terminal field (any page)."""
    field = _field_xref(doc, xref)
    if field == xref:
        return []
    kind, val = doc.xref_get_key(field, "Kids")
    if kind != "array":
        return []
    kids = [int(x) for x in _REF.findall(val)]
    return [k for k in kids if k != xref and doc.xref_get_key(k, "Subtype")[1] == "/Widget"]


def _load_any(page: pymupdf.Page, xref: int) -> tuple[pymupdf.Page, pymupdf.Widget] | None:
    """Find widget ``xref`` on any page of ``page``'s document (``page`` itself first)."""
    doc = page.parent
    numbers = [page.number]
    kind, val = doc.xref_get_key(xref, "P")
    if kind == "xref":
        pxref = int(val.split()[0])
        numbers += [n for n in range(doc.page_count) if doc.page_xref(n) == pxref]
    numbers += range(doc.page_count)
    for n in dict.fromkeys(numbers):
        pg = page if n == page.number else doc[n]
        if any(a[0] == xref for a in pg.annot_xrefs()):
            return pg, pg.load_widget(xref)
    return None


def _pin_quadding(doc: pymupdf.Document, xref: int) -> None:
    """MuPDF reads /Q only on the widget: copy an inherited non-zero /Q onto it."""
    if doc.xref_get_key(xref, "Q")[0] == "null":
        kind, q = _inherited(doc, xref, "Q")
        if kind == "int" and q != "0":
            doc.xref_set_key(xref, "Q", q)


def _refresh_text_siblings(page: pymupdf.Page, xref: int, quadding: str | None = None) -> None:
    """Regenerate the appearance of every sibling kid, each with its own /DA (Ruling J).

    ``quadding`` (an explicitly set align) is put on each kid; otherwise an inherited /Q is pinned.
    """
    doc = page.parent
    for kid in _sibling_kids(doc, xref):
        found = _load_any(page, kid)
        if found is None:
            continue
        _, sib = found
        if quadding is not None:
            doc.xref_set_key(kid, "Q", quadding)
        else:
            _pin_quadding(doc, kid)
        sib.update()


def _pdf_name(value: str) -> str:
    """``value`` as a PDF name object (``/Yes_5``), escaping delimiters, spaces and non-ASCII as ``#xx``."""
    out = []
    for byte in value.encode("utf-8"):
        c = chr(byte)
        out.append(c if 0x21 <= byte <= 0x7E and c not in "#()<>[]{}/%" else f"#{byte:02X}")
    return "/" + "".join(out)


def _settle_button_value(doc: pymupdf.Document, xref: int, value: str) -> None:
    """Put a written checkbox's value on its field, as a name, and nowhere else.

    PyMuPDF writes /V onto the kid widget too (and a string /V on the parent). A kid's own /V
    then shadows the field's in readers that inherit /V (PyMuPDF's ``field_value`` on a checkbox
    kid), so a sibling turned off by a later write would still read as on.
    """
    field = _field_xref(doc, xref)
    doc.xref_set_key(field, "V", _pdf_name(value))
    if field == xref:
        return
    for kid in [xref, *_sibling_kids(doc, xref)]:
        if doc.xref_get_key(kid, "V")[0] != "null":
            doc.xref_set_key(kid, "V", "null")


def _refresh_button_siblings(page: pymupdf.Page, xref: int) -> None:
    """Point every sibling checkbox kid's /AS at the field's new value (Ruling J)."""
    doc = page.parent
    kind, value = _inherited(doc, xref, "V")  # PyMuPDF reads a name back decoded ("/Yes 5")
    value = value[1:] if kind == "name" else "Off"
    for kid in _sibling_kids(doc, xref):
        found = _load_any(page, kid)
        if found is None:
            continue
        on = _on_state(found[1])
        doc.xref_set_key(kid, "AS", _pdf_name(value) if on is not None and on == value else "/Off")


# ---------------------------------------------------------------- text


def write_text(
    page: pymupdf.Page,
    binding: Binding,
    *,
    text: str | None,
    fit: Fit | None,
    style: Style,
    rect: Rect | None = None,
) -> tuple[str, Rect, list[FillWarning]]:
    """Write ``text`` (``fit.text`` when a Fit is given) into the bound text/combo/list widget.

    ``text=None`` clears the widget. ``rect`` is the target rect, required for a created widget.
    Returns the widget's name, its rect as read back, and warnings (``font_substituted``,
    ``not_an_option``).
    """
    _check_writable(binding)
    value = None if text is None else (fit.text if fit is not None else text)
    size: float | None = fit.font_size if fit is not None else None
    if size is None and style.font_size != "auto":
        size = float(style.font_size)
    doc = page.parent

    if binding.how == "created":
        w = _new_widget(page, binding, pymupdf.PDF_WIDGET_TYPE_TEXT, rect)
        w.text_font = DEFAULT_FONT
        w.text_fontsize = size or 0
        flags = 0
        if style.multiline or (fit is not None and fit.lines > 1):
            flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
        if style.comb:
            flags |= pymupdf.PDF_TX_FIELD_IS_COMB
            w.text_maxlen = style.comb
        w.field_flags = flags
        w.field_value = value or ""
        page.add_widget(w)
        q = str(_QUADDING[style.align])
        doc.xref_set_key(w.xref, "Q", q)  # MuPDF's appearance reads /Q only from the widget
        parent = _parent_xref(doc, w.xref)
        if parent is not None:
            doc.xref_set_key(parent, "Q", q)
        w.update()  # regenerate the appearance with the quadding
        name, got = _read_back(page, w.xref)
        return name, got, []

    if binding.widget.kind not in ("text", "combo", "list"):
        raise ValueError(f"widget {binding.widget.name!r} is a {binding.widget.kind} widget, not text/combo/list")
    w = _load(page, binding.widget)
    warnings_: list[tuple[str, str]] = []
    if value and w.text_maxlen and len(value) > w.text_maxlen:
        warnings_.append(
            ("truncated", f"value cut to the {w.text_maxlen} characters widget {w.field_name!r} allows")
        )
        value = value[: w.text_maxlen]
    font = _kept_font(w.text_font)
    if font is None:
        warnings_.append(
            (
                "font_substituted",
                f"widget {w.field_name!r} uses font {w.text_font!r}, which cannot be regenerated; "
                "written in Helvetica",
            )
        )
        font = DEFAULT_FONT
    if value and binding.widget.kind in ("combo", "list"):
        pairs = choice_pairs(w)
        exports = {e for e, _ in pairs}
        by_display = {d: e for e, d in reversed(pairs)}  # first option wins on duplicate display texts
        if value not in exports:
            if value in by_display:
                value = by_display[value]  # Ruling L: write the option's export value
            else:
                warnings_.append(
                    ("not_an_option", f"{value!r} is not one of the options of widget {w.field_name!r}; written anyway")
                )
    if size is None:
        size = w.text_fontsize or 0
    w.text_font = font
    w.text_fontsize = size
    # update() writes /DA only on a field-widget; on a kid the size would be ignored, so put the
    # fitted /DA on the widget itself.
    doc.xref_set_key(w.xref, "DA", pymupdf.get_pdf_str(_da(w.text_color, font, size)))
    field = _field_xref(doc, w.xref)
    # Ruling K: an explicitly set align / multiline overrides the widget's own; defaults keep it.
    q: str | None = None
    if "align" in style.model_fields_set:
        q = str(_QUADDING[style.align])
        doc.xref_set_key(field, "Q", q)
        doc.xref_set_key(w.xref, "Q", q)
    else:
        _pin_quadding(doc, w.xref)
    if "multiline" in style.model_fields_set and binding.widget.kind == "text":
        kind, ff = _inherited(doc, w.xref, "Ff")
        flags = int(ff) if kind == "int" else 0
        if style.multiline:
            flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
        else:
            flags &= ~pymupdf.PDF_TX_FIELD_IS_MULTILINE
        doc.xref_set_key(field, "Ff", str(flags))
        w.field_flags = flags  # a field-widget's update() writes Ff back
    _set_text_value(page, w, value)
    w.update()
    _refresh_text_siblings(page, w.xref, q)
    name, got = _read_back(page, w.xref)
    return name, got, [_warn(code, msg, page, got) for code, msg in warnings_]


# ---------------------------------------------------------------- choices


def write_choice(
    page: pymupdf.Page,
    binding: Binding,
    *,
    on: bool,
    style_circle: bool,
    rect: Rect | None = None,
) -> tuple[str, Rect, list[FillWarning]]:
    """Turn the bound checkbox / radio kid on (its own on-state) or off.

    A created widget is a checkbox at ``rect``. ``style_circle`` is accepted for ``radio_option``
    but has no effect: MuPDF always draws the check (Task 5 ruling (e)).
    """
    _check_writable(binding)
    if binding.how == "created":
        w = _new_widget(page, binding, pymupdf.PDF_WIDGET_TYPE_CHECKBOX, rect)
        w.field_value = bool(on)
        page.add_widget(w)
        name, got = _read_back(page, w.xref)
        return name, got, []
    if binding.widget.kind not in ("checkbox", "radio"):
        raise ValueError(f"widget {binding.widget.name!r} is a {binding.widget.kind} widget, not a button")
    w = _load(page, binding.widget)
    on_state = _on_state(w)
    on = on and on_state is not None  # a box with no "on" appearance can't show on (fill: not_shown)
    # True, not the on-state string: PyMuPDF compares a checkbox's value with the decoded on-state
    # name, and treats any truthy value (even "Off") as "on" for a radio kid.
    w.field_value = bool(on)
    w.update()
    if binding.widget.kind == "checkbox":  # PyMuPDF already sets every radio kid's /AS
        _settle_button_value(page.parent, w.xref, on_state if on else "Off")
        _refresh_button_siblings(page, w.xref)
    name, got = _read_back(page, w.xref)
    return name, got, []


def _ellipse(x0: float, y0: float, x1: float, y1: float) -> str:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    ox, oy = rx * _KAPPA, ry * _KAPPA
    return (
        f"{cx - rx:g} {cy:g} m\n"
        f"{cx - rx:g} {cy + oy:g} {cx - ox:g} {cy + ry:g} {cx:g} {cy + ry:g} c\n"
        f"{cx + ox:g} {cy + ry:g} {cx + rx:g} {cy + oy:g} {cx + rx:g} {cy:g} c\n"
        f"{cx + rx:g} {cy - oy:g} {cx + ox:g} {cy - ry:g} {cx:g} {cy - ry:g} c\n"
        f"{cx - ox:g} {cy - ry:g} {cx - rx:g} {cy - oy:g} {cx - rx:g} {cy:g} c\n"
    )


def _form_xobject(doc: pymupdf.Document, width: float, height: float, content: bytes) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<</Type/XObject/Subtype/Form/BBox[0 0 {width:g} {height:g}]/Resources<<>>>>")
    doc.update_stream(xref, content)
    return xref


def _set_mark_appearance(doc: pymupdf.Document, w: pymupdf.Widget) -> None:
    """Replace the checkbox's appearance: an ellipse inset in the rect when on, nothing when off."""
    width, height = w.rect.width, w.rect.height  # /Rect size; the form uses an identity matrix
    i = MARK_INSET
    path = _ellipse(i, i, max(width - i, i), max(height - i, i))
    on_src = f"q\n0 G\n{MARK_LINE_WIDTH:g} w\n{path}S\nQ\n".encode()
    on_x = _form_xobject(doc, width, height, on_src)
    off_x = _form_xobject(doc, width, height, b"")
    on_name = _pdf_name(_on_state(w) or "Yes")
    # The whole /AP is replaced (no /D down look), and a /MK /CA check caption is dropped, so
    # viewers never flash a check mark.
    doc.xref_set_key(w.xref, "AP", f"<</N<<{on_name} {on_x} 0 R/Off {off_x} 0 R>>>>")
    if doc.xref_get_key(w.xref, "MK/CA")[0] != "null":
        pdf = mupdf.pdf_document_from_fz_document(doc.this)
        mk = mupdf.pdf_dict_gets(mupdf.pdf_load_object(pdf, w.xref), "MK")
        mupdf.pdf_dict_dels(mk, "CA")


def write_choice_mark(
    page: pymupdf.Page,
    binding: Binding,
    *,
    on: bool,
    rect: Rect | None = None,
) -> tuple[str, Rect, list[FillWarning]]:
    """A checkbox over the option text whose "on" look is a stroked ellipse and "off" look empty.

    Created at ``rect``, or (Ruling H) an adopted checkbox from an earlier fill rewritten in place.
    The custom appearance is written after the widget's last ``update()``.
    """
    _check_writable(binding)
    doc = page.parent
    if binding.how == "created":
        w = _new_widget(page, binding, pymupdf.PDF_WIDGET_TYPE_CHECKBOX, rect)
        w.field_value = bool(on)
        page.add_widget(w)
    else:
        if binding.widget.kind != "checkbox":
            raise ValueError(f"choice_mark widget {binding.widget.name!r} must be a checkbox")
        w = _load(page, binding.widget)
        w.field_value = bool(on)
        w.update()
    w = page.load_widget(w.xref)
    _set_mark_appearance(doc, w)
    name, got = _read_back(page, w.xref)
    return name, got, []
