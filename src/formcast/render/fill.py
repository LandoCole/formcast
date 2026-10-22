"""``fill`` and ``to_smart_pdf``: one record into a PDF as real, editable form fields (fill design §4).

Orchestrates the render layers: open and check the PDF, resolve values (:mod:`.values`), build one
target per field and per table-cell slot, bind targets to widgets (:mod:`.binding`), fit and write
(:mod:`.fit`, :mod:`.write`), then report (:mod:`.assemble`). Output always keeps real form fields;
there is no flatten mode.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import pymupdf

from formcast.errors import FormcastError
from formcast.match import fingerprint_document
from formcast.pagemap.extract import open_pdf
from formcast.render.assemble import Slot, build_report, column_path, order_key
from formcast.render.binding import Binding, Target, bind, cell_key
from formcast.render.fit import Fit, fit_text
from formcast.render.report import FillReport, FillWarning
from formcast.render.values import CellTarget, Resolved, resolve
from formcast.render.write import (
    option_value,
    read_existing,
    text_font_for,
    widget_rotation,
    write_choice,
    write_choice_mark,
    write_text,
)
from formcast.schema import flatten_schema, validate_data
from formcast.template.model import Style, Template
from formcast.transforms import validate_chain

__all__ = ["fill", "to_smart_pdf"]

log = logging.getLogger(__name__)

CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")
STRICT_MESSAGES = 5  # validation messages quoted in the strict-mode error


def fill(
    template: Template,
    pdf: str | Path | bytes,
    data: dict,
    *,
    check_fingerprint: bool = True,
    strict: bool = False,
) -> tuple[bytes, FillReport]:
    """Write ``data`` into ``pdf`` as real form fields; return the PDF bytes and a :class:`FillReport`.

    Raises ``FormcastError`` when the PDF can't be opened or is encrypted, its page count differs
    from the template's, its fingerprint doesn't match (unless ``check_fingerprint=False``, which
    records ``fingerprint_mismatch`` instead), the template uses an unknown transform (or bad
    transform arguments), or, with ``strict=True``, ``data`` fails the template's schema. Every
    other problem is a report entry.
    """
    out, report = _run(template, pdf, data, check_fingerprint=check_fingerprint, strict=strict, smart=False)
    assert report is not None
    return out, report


def to_smart_pdf(template: Template, pdf: str | Path | bytes, *, check_fingerprint: bool = True) -> bytes:
    """``pdf`` with an empty form field for every template field the PDF doesn't already have.

    Only new widgets are written; the form's own widgets are left as they are. Placement problems
    (a named widget not found, a type mismatch, a fingerprint mismatch when not checked) are logged
    as warnings on the ``formcast.render.fill`` logger.
    """
    out, _ = _run(template, pdf, None, check_fingerprint=check_fingerprint, strict=False, smart=True)
    return out


# ---------------------------------------------------------------- pipeline


def _run(template, pdf, data, *, check_fingerprint: bool, strict: bool, smart: bool):
    _check_transforms(template)
    doc = open_pdf(pdf)
    try:
        if doc.page_count != template.form.page_count:
            raise FormcastError(
                f"page count mismatch: template has {template.form.page_count} pages, PDF has {doc.page_count}"
            )
        head = _check_fingerprint(doc, template, check_fingerprint)
        if smart:
            fields, tables, record = {}, {}, []
        else:
            if strict:
                errors = validate_data(template.schema_, data)
                if errors:
                    raise FormcastError("data does not match the schema: " + "; ".join(errors[:STRICT_MESSAGES]))
            fields, tables, record = resolve(template, data)
        slots = _slots(template)
        bindings = bind([s.target for s in slots], read_existing(doc))
        for s in slots:
            s.binding = bindings[s.target.key]
            s.warnings.extend(_located(w, s) for w in s.binding.warnings)
        cells = {(c.table_id, c.row, c.key): c for t in tables.values() for c in t.cells}
        _write(doc, slots, fields, cells, smart=smart)
        _conflicts(slots)
        _settle_choice_states(doc, slots)
        # garbage=1 drops the appearance streams a refill orphaned; higher levels renumber every
        # object, which would rewrite the references of widgets fill never touched.
        out = doc.tobytes(garbage=1, deflate=True)
    finally:
        doc.close()
    if smart:
        for w in head + [w for s in slots for w in s.warnings if w.code != "adopted"]:
            log.warning("to_smart_pdf: %s: %s", w.code, w.message)
        return out, None
    report = build_report(template, data, strict=strict, fingerprint_ok=not head, head=head, record=record,
                          slots=slots, fields=fields, tables=tables, cells=cells)  # fmt: skip
    return out, report


def _check_transforms(template: Template) -> None:
    """Raise ``FormcastError`` on the first unknown transform or bad transform arguments in the
    template (source transforms, ``when``, table column transforms). Failures that depend on the
    data stay per-field ``transform_error`` entries."""
    for f in template.fields:
        chains: list[tuple[str, list]] = []
        if f.source is not None:
            chains.append(("transforms", f.source.transforms))
            if f.source.when is not None:
                chains.append(("when", [f.source.when]))
        chains += [(f"column {c.key!r} transforms", c.transforms) for seg in f.segments for c in seg.columns]
        for where, chain in chains:
            errors = validate_chain(chain)
            if errors:
                raise FormcastError(f"field {f.id!r} {where}: {errors[0]}")


def _check_fingerprint(doc: pymupdf.Document, template: Template, check: bool) -> list[FillWarning]:
    """``[]`` when the PDF is the template's form, else the ``fingerprint_mismatch`` warning (or raise).

    A template without a fingerprint raises when ``check`` is set, and otherwise has nothing to
    mismatch.

    A filled copy of the form (a refill) matches by its field-blind fingerprint.
    """
    expected = template.form.fingerprint
    if expected is None:
        if check:
            raise FormcastError(
                "template has no fingerprint (made outside the library, e.g. in the browser demo); "
                "call formcast.rebind(template, pdf) or pass check_fingerprint=False"
            )
        return []
    got = fingerprint_document(doc)
    if got == expected or fingerprint_document(doc, ignore_field_values=True) == expected:
        return []
    msg = f"fingerprint mismatch: the template was built for form {expected!r}, this PDF is {got!r}"
    if check:
        raise FormcastError(msg + "; pass check_fingerprint=False to fill it anyway")
    return [FillWarning.make("fingerprint_mismatch", msg + "; filled anyway (check_fingerprint=False)")]


def _slots(template: Template) -> list[Slot]:
    """One target per field with a box, and one per table-cell slot (every row of every segment)."""
    out: list[Slot] = []
    keys: set[str] = set()
    item_types = {sp.path: sp.item_type for sp in flatten_schema(template.schema_)}
    for f in template.fields:
        mapped = f.source is not None and bool(f.source.paths)
        if f.type != "table":
            assert f.page is not None and f.rect is not None
            path = f.source.paths[0] if mapped and len(f.source.paths) == 1 else None
            target = Target(f.id, f.id, f.type, f.page, f.rect, f.existing_widget)
            out.append(Slot(target, f, mapped=mapped, path=path))
            continue
        table_path = f.source.paths[0] if mapped else None
        list_rows = item_types.get(table_path) not in (None, "object")
        base = 0
        for k, seg in enumerate(f.segments):
            for r in range(seg.max_rows):
                y = seg.row_top + r * seg.row_height
                for col in seg.columns:
                    key = cell_key(f.id, base + r, col.key)
                    if key in keys:
                        continue  # a repeated column key (ops reject it) would bind twice
                    keys.add(key)
                    rect = (col.x[0], y, col.x[1], y + seg.row_height)
                    target = Target(key, f.id, "text", seg.page, rect, None)
                    path = column_path(table_path, col.key, list_rows=list_rows) if table_path else None
                    out.append(Slot(target, f, mapped=mapped, path=path, column=col, segment=k, row=base + r,
                                    list_row=list_rows))  # fmt: skip
            base += seg.max_rows
    for i, s in enumerate(out):
        s.order = order_key(s.target, i)
    return out


def _located(w: FillWarning, s: Slot) -> FillWarning:
    """``w`` with the slot's field id and data path filled in where missing."""
    update = {}
    if w.field is None:
        update["field"] = s.field.id
    if w.path is None and s.path is not None:
        update["path"] = s.path
    return w.model_copy(update=update) if update else w


# ---------------------------------------------------------------- writing


def _value(s: Slot, fields: dict[str, Resolved], cells: dict[tuple, CellTarget]) -> str | bool | None:
    """What a mapped slot should show: text (``None`` to clear) or on/off."""
    if s.column is not None:
        cell = cells.get((s.field.id, s.row, s.column.key))
        return cell.text if cell is not None else None  # slots past the data rows are cleared
    r = fields[s.field.id]
    return bool(r.on) if r.kind == "choice" else r.text


def _write(doc: pymupdf.Document, slots: list[Slot], fields, cells, *, smart: bool) -> None:
    """Write every slot that fill owns; clears and "off"s first, so a value is never wiped by a
    later clear of the same shared field (and a checkbox kid turned off can't undo a sibling's "on")."""
    plan: list[tuple[int, tuple, Slot, str | bool | None]] = []
    for s in slots:
        b = s.binding
        if b.how == "none" or any(w.code == "not_writable" for w in b.warnings):
            continue
        if smart or not s.mapped:
            if b.how == "created":  # the form's own widgets stay untouched
                plan.append((0, s.order, s, False if s.choice else None))
            continue
        value = _value(s, fields, cells)
        plan.append((1 if value else 0, s.order, s, value))
    pages: dict[int, pymupdf.Page] = {}  # keep pages alive while their widgets are written
    for _, _, s, value in sorted(plan, key=lambda p: p[:2]):
        page = pages.setdefault(s.target.page, doc[s.target.page])
        if s.choice:
            _write_choice(page, s, bool(value))
        else:
            _write_text(page, s, value if isinstance(value, str) else None)


def _write_choice(page: pymupdf.Page, s: Slot, on: bool) -> None:
    b, rect = s.binding, s.target.rect
    if s.field.type == "choice_mark":
        name, got, warns = write_choice_mark(page, b, on=on, rect=rect)
    else:
        name, got, warns = write_choice(page, b, on=on, style_circle=s.field.type == "radio_option", rect=rect)
    s.done(name, got, on, None, [_located(w, s) for w in warns])


def _cell_lines(s: Slot) -> int | None:
    wrap = s.column.wrap if s.column is not None else None
    return wrap if wrap is not None and wrap > 1 else None


def _write_text(page: pymupdf.Page, s: Slot, text: str | None) -> None:
    b = s.binding
    style = s.field.style
    lines = _cell_lines(s)
    if b.how == "created" and lines is not None and not style.multiline:
        style = style.model_copy(update={"multiline": True})  # a wrapped column is a multi-line box
    if text:
        text = option_value(page, b, text)  # a combo/list holds (and shows) the export value
    fit = _fit(page, s, b, text, style, lines) if text else None
    name, got, warns = write_text(page, b, text=text, fit=fit, style=style, rect=s.target.rect)
    written = (fit.text if fit is not None else text) or None
    fit_warns = _fit_warnings(fit, s, page.number, got) if fit is not None else []
    s.done(name, got, written, fit.font_size if fit is not None else None,
           fit_warns + [_located(w, s) for w in warns])  # fmt: skip


def _fit(page: pymupdf.Page, s: Slot, b: Binding, text: str, style: Style, lines: int | None) -> Fit:
    """Fit ``text`` with the font, box and limits the written widget will really have."""
    font = text_font_for(page, b)
    if b.how == "created":
        rect, multiline, max_len, comb = s.target.rect, style.multiline, None, style.comb
    else:
        w = b.widget
        flags = (page.load_widget(w.xref).field_flags or 0) if w.kind == "text" else 0  # combo/list bits differ
        rect = w.rect
        own_multiline = bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
        multiline = style.multiline if "multiline" in style.model_fields_set else own_multiline  # Ruling K
        max_len = w.max_len
        comb = max_len if flags & pymupdf.PDF_TX_FIELD_IS_COMB and max_len else None
        if not multiline:
            lines = None  # a single-line widget draws one line whatever the column's wrap
    if widget_rotation(page, b) in (90, 270):  # the text runs along the rect's height (/MK /R)
        x0, y0, x1, y1 = rect
        rect = (x0, y0, x0 + (y1 - y0), y0 + (x1 - x0))
    return fit_text(text, rect, font_size=style.font_size, min_font_size=style.min_font_size, fontname=font,
                    multiline=multiline or lines is not None, max_lines=lines, max_len=max_len, comb=comb)  # fmt: skip


def _fit_warnings(fit: Fit, s: Slot, page: int, rect) -> list[FillWarning]:
    where = {"field": s.field.id, "path": s.path, "page": page, "rect": rect}
    out: list[FillWarning] = []
    if fit.truncated:
        out.append(FillWarning.make("truncated", f"value cut to {len(fit.text)} characters, the box's limit", **where))
    if fit.status == "shrunk":
        out.append(FillWarning.make("shrunk", f"text shrunk to {fit.font_size:g} pt to fit", **where))
    elif fit.status == "overflow":
        out.append(FillWarning.make(
            "overflow",
            f"text doesn't fit even at {fit.font_size:g} pt; the full value is in the field, but the end "
            "will be cut off when printed or flattened", **where))  # fmt: skip
    if fit.unencodable:
        out.append(FillWarning.make(
            "unencodable",
            f"characters {fit.unencodable!r} are outside the standard PDF fonts; the value is stored but "
            "may not display", **where))  # fmt: skip
    return out


# ---------------------------------------------------------------- shared fields (Ruling G)


def _differ(a: str | bool | None, b: str | bool | None) -> bool:
    if isinstance(a, bool) and isinstance(b, bool):
        return a != b
    return isinstance(a, str) and isinstance(b, str) and a != b  # both non-empty (empty is None)


def _choice_clash(a: Slot, b: Slot) -> bool:
    """Two kids of one button field show one shared value (Ruling G2): both "on" needs equal
    on-states; one on and one off needs different ones (else the "off" kid shows on too)."""
    if not (isinstance(a.value, bool) and isinstance(b.value, bool)) or not (a.value or b.value):
        return False
    same_state = a.binding.widget.on_state == b.binding.widget.on_state
    return not same_state if a.value and b.value else same_state


def _conflicts(slots: list[Slot]) -> None:
    """Report two written targets that can't both show their values through one widget or field.

    Groups (Rulings G and G2): every bound widget instance (two targets bound to one widget);
    every text field name (text widgets with one name share a value); every button field name
    (kids share one value, so two "on" kids with different on-states clash). The later target
    (page, then top) gets ``shared_field_conflict`` naming both; identical values are harmless.
    """
    groups: dict[tuple, list[Slot]] = defaultdict(list)
    for s in slots:
        w = s.binding.widget if s.binding is not None else None
        if not s.written or w is None:
            continue
        groups[("widget", w.page, w.index)].append(s)
        groups[("buttons" if s.choice else "name", w.name)].append(s)
    for (kind, *what), members in groups.items():
        clash = _choice_clash if kind == "buttons" else (lambda a, b: _differ(a.value, b.value))
        members.sort(key=lambda s: s.order)
        for j, later in enumerate(members):
            for earlier in members[:j]:
                if earlier.target.key in later.conflicts_with or not clash(earlier, later):
                    continue
                later.conflicts_with.add(earlier.target.key)
                name = later.binding.widget.name
                via = f"widget {name!r} on page {what[0]}" if kind == "widget" else f"PDF field {name!r}"
                later.warnings.append(FillWarning.make(
                    "shared_field_conflict",
                    f"{earlier.target.key!r} and {later.target.key!r} both write to {via} with clashing "
                    f"values ({_shown(earlier)} vs {_shown(later)}); it can show only one",
                    field=later.field.id, path=later.path, page=later.target.page, rect=later.widget_rect,
                ))  # fmt: skip


def _settle_choice_states(doc: pymupdf.Document, slots: list[Slot]) -> None:
    """Report what each existing button really shows after every write (its /AS), so a box another
    target turned off (a radio sibling, a shared field's kid) is reported off, not on."""
    for s in slots:
        w = s.binding.widget if s.binding is not None else None
        if s.written and s.choice and w is not None:
            kind, state = doc.xref_get_key(w.xref, "AS")
            s.value = kind == "name" and state != "/Off"


def _shown(s: Slot) -> str:
    v = s.value
    if not isinstance(v, bool):
        return repr(v)
    state = s.binding.widget.on_state if s.binding is not None and s.binding.widget is not None else None
    return ("on" + (f" ({state!r})" if state else "")) if v else "off"
