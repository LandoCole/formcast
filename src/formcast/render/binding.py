"""Bind template fields and table cells to PDF widgets (fill design section 5.3).

Pure logic over widget descriptors; no PDF access. Three outcomes per target, tried in order:
a named existing widget, an adopted unclaimed widget under the target, or a new widget with a
deterministic unique name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from formcast.geometry import Rect, iou
from formcast.render.report import FillWarning
from formcast.template.model import WIDGET_FIELD_TYPES

__all__ = ["Binding", "ExistingWidget", "Target", "bind", "cell_key", "widget_name_for"]

_CELL_KEY = re.compile(r"^(.*?)\[(\d+)\]\.(.*)$", re.DOTALL)

SIGNATURE_NOT_WRITABLE = (
    "the form's own signature field can only be signed in a PDF viewer; value not written"
)


@dataclass(frozen=True)
class ExistingWidget:
    page: int
    index: int  # position in page.widgets() order
    xref: int
    name: str
    kind: str  # pagemap kinds: text, checkbox, radio, combo, list, signature, button
    rect: Rect
    on_state: str | None
    max_len: int | None
    options: tuple[str, ...]


@dataclass(frozen=True)
class Target:
    key: str  # field id, or cell_key(table_id, row, col_key) for a table cell
    field_id: str
    field_type: str  # template type; table cells are "text"
    page: int
    rect: Rect
    existing_widget: str | None


@dataclass
class Binding:
    key: str
    how: Literal["named", "adopted", "created", "none"]
    widget: ExistingWidget | None = None
    new_name: str | None = None
    warnings: list[FillWarning] = field(default_factory=list)


def cell_key(table_id: str, row: int, col_key: str) -> str:
    return f"{table_id}[{row}].{col_key}"


def widget_name_for(key: str) -> str:
    """Readable PDF field name for a target key: cells become ``<table>_r<row>_<col>``."""
    m = _CELL_KEY.match(key)
    if m:
        return f"{m.group(1)}_r{m.group(2)}_{m.group(3)}"
    return key


def _centre(r: Rect) -> tuple[float, float]:
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


def _centre_dist(a: Rect, b: Rect) -> float:
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _warn(code, message: str, t: Target):
    return FillWarning.make(code, message, field=t.field_id, page=t.page, rect=t.rect)


def _backs(widget: ExistingWidget, field_type: str) -> bool:
    return field_type in WIDGET_FIELD_TYPES.get(widget.kind, frozenset())


def _with_signature_note(b: Binding, t: Target) -> Binding:
    if b.widget is not None and b.widget.kind == "signature":
        b.warnings.append(_warn("not_writable", SIGNATURE_NOT_WRITABLE, t))
    return b


def _bind_named(t: Target, widgets: list[ExistingWidget]) -> tuple[Binding, ExistingWidget | None]:
    """Resolve a named target. Returns the binding and the widget picked (even on type mismatch)."""
    same = [w for w in widgets if w.page == t.page and w.name == t.existing_widget]
    if not same:
        msg = f"no widget named {t.existing_widget!r} on page {t.page}; field left empty"
        return Binding(t.key, "none", warnings=[_warn("widget_not_found", msg, t)]), None
    if len(same) == 1:
        pick = same[0]
    else:
        scored = [(iou(w.rect, t.rect), -_centre_dist(w.rect, t.rect), -i, w) for i, w in enumerate(same)]
        best = max(scored, key=lambda s: s[:3])
        if best[0] <= 0:
            msg = f"no widget named {t.existing_widget!r} overlaps the field's box on page {t.page}; field left empty"
            return Binding(t.key, "none", warnings=[_warn("widget_not_found", msg, t)]), None
        pick = best[3]
    if not _backs(pick, t.field_type):
        msg = f"widget {pick.name!r} is a {pick.kind} widget and cannot hold a {t.field_type} field; skipped"
        return Binding(t.key, "none", warnings=[_warn("type_mismatch", msg, t)]), pick
    return _with_signature_note(Binding(t.key, "named", widget=pick), t), pick


def bind(targets: list[Target], existing: list[ExistingWidget]) -> dict[str, Binding]:
    """Decide, for every target, which widget it writes to (see module docstring)."""
    out: dict[str, Binding] = {}
    # Rule 1: named. A target's widget name claims every widget of that name, so adoption never
    # grabs a sibling kid of a radio/checkbox group some field already names.
    claimed_names: set[str] = set()
    for t in targets:
        if t.field_type == "choice_mark":
            continue  # always created; any existing_widget is ignored
        if t.existing_widget is None:
            continue
        claimed_names.add(t.existing_widget)
        out[t.key], _ = _bind_named(t, existing)

    # Rule 2: adopt, in page / top / left / key order so the outcome is deterministic.
    claimed: set[tuple[int, int]] = {(w.page, w.index) for w in existing if w.name in claimed_names}
    pending = [t for t in targets if t.key not in out and t.field_type != "choice_mark" and t.existing_widget is None]
    for t in sorted(pending, key=lambda t: (t.page, t.rect[1], t.rect[0], t.key)):
        best: tuple[float, int, ExistingWidget] | None = None
        for w in existing:
            if w.page != t.page or (w.page, w.index) in claimed or not _backs(w, t.field_type):
                continue
            cx, cy = _centre(w.rect)
            if not (t.rect[0] <= cx <= t.rect[2] and t.rect[1] <= cy <= t.rect[3]):
                continue
            score = (iou(w.rect, t.rect), -w.index)
            if best is None or score > best[:2]:
                best = (score[0], score[1], w)
        if best is None:
            continue
        w = best[2]
        claimed.add((w.page, w.index))
        b = Binding(t.key, "adopted", widget=w)
        b.warnings.append(_warn("adopted", f"adopted existing {w.kind} widget {w.name!r}", t))
        out[t.key] = _with_signature_note(b, t)

    # Rule 3: create the rest, in input order, with unique deterministic names.
    taken = {w.name for w in existing}
    for t in targets:
        if t.key in out:
            continue
        base = widget_name_for(t.key)
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base}_{n}"
        taken.add(name)
        out[t.key] = Binding(t.key, "created", new_name=name)
    return out
