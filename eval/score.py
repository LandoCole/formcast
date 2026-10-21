"""Shared scoring for the eval harness: field detection and field-to-schema mapping.

Ground truth (``GTField``) comes from ``load_gt``. Predictions are either a flat
list of boxes (``score_detection``, used for the detector baseline and for any
template via ``template_boxes``) or a whole ``Template`` (``score_mapping``).
All rects are PDF points, origin top-left, unrotated page space.

Ground-truth file ``forms/gt/<id>.json``::

    {"form": id,
     "fields": [{"page": int, "rect": [x0, y0, x1, y1], "type": "text"|"choice"|"signature",
                 "label": str, "path"?: str, "also_paths"?: [str], "transforms"?: [op names],
                 "value"?: any}],
     "widget_paths": {widget_name: {"path": str, "also_paths"?: [str], "transforms"?: [op names],
                                    "value"?: any}},
     "notes": str}

Fillable forms take their boxes from the PDF's widgets (``widget_paths`` adds
``path``/``also_paths``/``transforms`` by widget name; push buttons are not data
fields and are skipped); flat and scanned forms take them from ``fields``. A
table is ground-truthed one box per cell with path ``arr[].col``. ``also_paths``
lists the further schema paths a box is fed from (a full name from first + last:
``path`` is the first, ``also_paths`` the rest).

``value`` (choice boxes with a path) is the option the box stands for: an enum value, a
member of the item enum of a multi-select array (or, on a path with no enum, the box's
text), or on a boolean path ``true`` or ``false`` (the Yes box of a Yes/No pair, a lone
box that means yes: ``true``; the No box: ``false``); every choice box on a boolean path
carries one. Widgets that share a field name (a radio group) cannot share one
value: their ``widget_paths`` ``value`` is then an object keyed by each widget's on-state
name, ``{"M": "male", "F": "female"}``.

A manifest entry's ``scored_pages`` (0-based page indexes; absent = every page)
limits ground truth and scoring to those pages.

Division by zero: every ratio is 0.0 when its denominator is 0.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # eval scripts do not rely on the editable install

import pymupdf  # noqa: E402

from formcast.geometry import Rect, contained_fraction, horizontal_overlap, intersect, iou  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.template.model import Template, linked_cells, table_cells  # noqa: E402

GT_DIR = ROOT / "forms" / "gt"
TYPES = ("text", "choice", "signature")
# Widgets thinner than this (points) are artifacts, not fields.
MIN_WIDGET_SIZE = 3

# Template field types -> detection types (table cells are "text").
_DETECTION_TYPE = {
    "text": "text",
    "checkbox": "choice",
    "radio_option": "choice",
    "choice_mark": "choice",
    "signature": "signature",
}

# (type, page, rect)
Box = tuple[str, int, Rect]

# Transform ops that do the same job in different ways: a GT op is credited by
# any op of its group (taking characters out of a value; formatting a date or a
# part of it; testing a choice value).
TRANSFORM_EQUIVALENCE: tuple[frozenset[str], ...] = (
    frozenset({"slice", "split", "char_at"}),
    frozenset({"date_part", "date_format"}),
    frozenset({"equals", "contains"}),
)
_CANONICAL_OP = {op: min(group) for group in TRANSFORM_EQUIVALENCE for op in group}
# A field's ``when`` condition ``equals`` with value ``true`` is the op name this
# token stands for: it credits a GT "bool_to_check" as well as a GT "equals"
# (or anything else in the equals group), since both check a box for a true value.
WHEN_EQUALS_TRUE = "equals(true)"
_OP_ALTERNATIVES = {WHEN_EQUALS_TRUE: frozenset({_CANONICAL_OP["equals"], "bool_to_check"})}

# --- multi-line answer areas ---
# A plain template text field this many times the page's median GT text-box
# height (or with style.multiline) is a multi-line field ...
MULTILINE_HEIGHT_RATIO = 1.8
# ... and covers every GT text box with at least this share of its area inside it.
MULTILINE_CONTAINED = 0.7
# A multi-line field only counts when it holds at least two such GT boxes stacked in one
# column (horizontal overlap of at least this share of the narrower box); one loose tall
# box over a single GT box is scored by the normal IoU threshold.
MULTILINE_COLUMN_OVERLAP = 0.5


@dataclass
class GTField:
    page: int
    rect: Rect
    type: Literal["text", "choice", "signature"]
    label: str = ""
    path: str | None = None
    transforms: list[str] = field(default_factory=list)
    also_paths: list[str] = field(default_factory=list)
    value: Any = None


# ---------- ground truth ----------


def scored_pages(form: dict, n_pages: int) -> list[int]:
    """The 0-based pages of ``form`` that are ground-truthed and scored, ascending.

    A manifest entry's ``scored_pages`` names them; without it every one of the
    document's ``n_pages`` pages is scored.

    Raises:
        ValueError: when ``scored_pages`` names a page the document does not have.
    """
    pages = form.get("scored_pages")
    if pages is None:
        return list(range(n_pages))
    bad = [p for p in pages if not isinstance(p, int) or not 0 <= p < n_pages]
    if bad:
        raise ValueError(f"{form['id']}: scored_pages {bad} not in a {n_pages}-page document")
    return sorted(set(pages))


def _widget_type(w) -> str:
    if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        return "choice"
    if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
        return "signature"
    return "text"  # text, combo, list (push buttons are skipped by load_gt)


def _read_gt_file(gt_dir: Path, form_id: str) -> dict | None:
    path = gt_dir / f"{form_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON ({exc})") from exc


def _gt_from_entry(entry: dict, source: str) -> GTField:
    try:
        rect = tuple(float(v) for v in entry["rect"])
        if len(rect) != 4 or entry["type"] not in TYPES:
            raise ValueError
        return GTField(
            page=int(entry["page"]),
            rect=rect,
            type=entry["type"],
            label=entry.get("label", ""),
            path=entry.get("path"),
            transforms=list(entry.get("transforms", [])),
            also_paths=list(entry.get("also_paths", [])),
            value=entry.get("value"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source}: bad field entry {entry!r}") from exc


def _on_state(w) -> str | None:
    """A check box's or radio button's on-state name, else None."""
    if w.field_type not in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        return None
    state = w.on_state()
    return state if isinstance(state, str) and state != "Off" else None


def _widget_value(value: Any, on_state: str | None) -> Any:
    """A ``widget_paths`` value: an object is keyed by the widget's on-state name."""
    if isinstance(value, dict):
        return value.get(on_state) if on_state is not None else None
    return value


def gt_entries(form: dict, pdf_path, gt_dir: Path | None = None) -> list[tuple[str, GTField, str | None]] | None:
    """The ground-truth fields of ``load_gt``, each with the key of its GT-file entry.

    The key is ``"fields[<index in the file>]"`` for a flat or scanned form (the index
    counts entries on unscored pages too) and the widget's field name for a fillable
    one. The third item is a check box's or radio button's on-state name, else None.
    Same ``None`` and error rules as ``load_gt``.
    """
    gt_dir = GT_DIR if gt_dir is None else Path(gt_dir)
    data = _read_gt_file(gt_dir, form["id"])
    source = f"{form['id']}.json"
    if form["kind"] != "fillable":
        if data is None:
            return None
        keep = form.get("scored_pages")
        entries = [(f"fields[{i}]", _gt_from_entry(e, source), None) for i, e in enumerate(data.get("fields", []))]
        return entries if keep is None else [e for e in entries if e[1].page in keep]

    widget_paths = (data or {}).get("widget_paths", {})
    for name, extra in widget_paths.items():
        if "type" in extra and extra["type"] not in TYPES:
            raise ValueError(f"{source}: widget_paths[{name!r}] has unknown type {extra['type']!r}")
    out: list[tuple[str, GTField, str | None]] = []
    doc = open_pdf(pdf_path)
    try:
        for pno in scored_pages(form, doc.page_count):
            page = doc[pno]
            for w in page.widgets() or []:
                if w.field_type == pymupdf.PDF_WIDGET_TYPE_BUTTON:
                    continue
                if w.rect.width < MIN_WIDGET_SIZE or w.rect.height < MIN_WIDGET_SIZE:
                    continue
                extra = widget_paths.get(w.field_name) or {}
                on_state = _on_state(w)
                gt = GTField(
                    page=page.number,
                    rect=(w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1),
                    type=extra.get("type") or _widget_type(w),
                    label=w.field_label or w.field_name or "",
                    path=extra.get("path"),
                    transforms=list(extra.get("transforms", [])),
                    also_paths=list(extra.get("also_paths", [])),
                    value=_widget_value(extra.get("value"), on_state),
                )
                out.append((w.field_name, gt, on_state))
    finally:
        doc.close()
    return out or None


def load_gt(form: dict, pdf_path, gt_dir: Path | None = None) -> list[GTField] | None:
    """Ground-truth fields of one form, or None when it has none.

    ``form`` is a ``forms/manifest.json`` entry (``id``, ``kind`` and
    ``scored_pages`` are used); only fields on scored pages are returned.
    A fillable form's boxes are its PDF widgets, typed like the CommonForms
    classes: check boxes and radio buttons are "choice", signatures
    "signature", everything else "text"; push buttons (not data fields) and
    widgets under 3 pt are dropped. A ``widget_paths`` entry for the widget's
    field name adds ``path``, ``also_paths``, ``transforms`` and ``value``, and its optional
    ``type`` (text, choice or signature) overrides the widget's type, e.g. for a
    signature line made as a text widget. A fillable form
    with no widgets has no ground truth. Flat and scanned forms read ``fields``
    from the GT file and have none without it.

    Raises:
        ValueError: when the GT file is not valid JSON or has a malformed field.
    """
    entries = gt_entries(form, pdf_path, gt_dir)
    return None if entries is None else [g for _, g, _ in entries]


# ---------- matching ----------


def _greedy_match(
    gt: list[tuple[int, Rect]], pred: list[tuple[int, Rect]], thr: float, allowed=None
) -> list[tuple[int, int]]:
    """One-to-one (gt index, pred index) pairs, best IoU first, only on the same page.

    A pair needs IoU >= ``thr`` (and ``allowed(gi, pi)`` when given).
    """
    pairs = []
    for gi, (gpage, grect) in enumerate(gt):
        for pi, (ppage, prect) in enumerate(pred):
            if gpage != ppage or (allowed is not None and not allowed(gi, pi)):
                continue
            v = iou(grect, prect)
            if v >= thr:
                pairs.append((v, gi, pi))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_g: set[int] = set()
    used_p: set[int] = set()
    matched = []
    for _, gi, pi in pairs:
        if gi not in used_g and pi not in used_p:
            used_g.add(gi)
            used_p.add(pi)
            matched.append((gi, pi))
    return matched


def _stacked(a: Rect, b: Rect) -> bool:
    """Two boxes in one column, one above the other."""
    if horizontal_overlap(a, b) < MULTILINE_COLUMN_OVERLAP:
        return False
    inter = intersect(a, b)
    return inter is None or (inter[3] - inter[1]) < 0.5 * min(a[3] - a[1], b[3] - b[1])


def _multiline_cover(gt: list[GTField], pred: list[tuple[int, Rect]], multiline: list[bool], allowed=None):
    """For each multi-line pred: {gt index: share} of the GT text boxes it may cover.

    Candidates are GT text boxes on the pred's page with at least ``MULTILINE_CONTAINED``
    of their area inside it. The pred counts only when two or more candidates are stacked
    in one column; then the stacked candidates are the ones it can cover.
    """
    out: dict[int, dict[int, float]] = {}
    for pi, (page, rect) in enumerate(pred):
        if not multiline[pi]:
            continue
        cands = {}
        for gi, g in enumerate(gt):
            if g.type != "text" or g.page != page or (allowed is not None and not allowed(gi, pi)):
                continue
            share = contained_fraction(g.rect, rect)
            if share >= MULTILINE_CONTAINED:
                cands[gi] = share
        stacked = {
            gi for gi in cands
            if any(gj != gi and _stacked(gt[gi].rect, gt[gj].rect) for gj in cands)
        }
        if stacked:
            out[pi] = {gi: cands[gi] for gi in stacked}
    return out


def _match(
    gt: list[GTField], pred: list[tuple[int, Rect]], thr: float, allowed=None, multiline=None
) -> dict[int, int]:
    """GT index -> pred index.

    First the one-to-one IoU match (``_greedy_match``). Then each GT text box
    still unmatched goes to the multi-line pred (``multiline[pi]`` true) on its
    page that holds the largest share of it, when that share is at least
    ``MULTILINE_CONTAINED`` and the pred holds two or more GT text boxes stacked
    in one column (``_multiline_cover``). A multi-line pred can so cover several GT boxes,
    including one it already matched by IoU.
    """
    matched = dict(_greedy_match([(g.page, g.rect) for g in gt], pred, thr, allowed))
    if not multiline:
        return matched
    cover = _multiline_cover(gt, pred, multiline, allowed)
    for gi in range(len(gt)):
        if gi in matched:
            continue
        best = None
        for pi, shares in cover.items():
            share = shares.get(gi)
            if share is not None and (best is None or share > best[0]):
                best = (share, pi)
        if best is not None:
            matched[gi] = best[1]
    return matched


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def score_detection(
    gt: list[GTField], boxes: list[Box], iou_thr: float, multiline: list[bool] | None = None
) -> dict:
    """Detection score of ``boxes`` (type, page, rect) against ``gt``.

    Boxes match GT boxes one to one on the same page, best IoU first, when
    IoU >= ``iou_thr``; the type is not required to agree. With ``multiline``
    (one flag per box, see ``multiline_flags``) a flagged box also covers each
    unmatched GT text box mostly inside it (``_match``), so one box can match
    several GT boxes. Returns ``gt`` and ``pred`` counts, ``matched`` (GT
    boxes matched), ``pred_matched`` (boxes that matched at least one GT box),
    ``recall`` (matched/gt), ``precision`` (pred_matched/pred), ``type_ok``
    (matched GT boxes whose box has the same type), ``type_acc``
    (type_ok/matched) and ``missed_by_type`` (unmatched GT per GT type).
    """
    matched = _match(gt, [(page, rect) for _, page, rect in boxes], iou_thr, multiline=multiline)
    type_ok = sum(gt[gi].type == boxes[pi][0] for gi, pi in matched.items())
    missed = Counter(g.type for i, g in enumerate(gt) if i not in matched)
    pred_matched = len(set(matched.values()))
    return {
        "gt": len(gt),
        "pred": len(boxes),
        "matched": len(matched),
        "pred_matched": pred_matched,
        "recall": _ratio(len(matched), len(gt)),
        "precision": _ratio(pred_matched, len(boxes)),
        "type_ok": type_ok,
        "type_acc": _ratio(type_ok, len(matched)),
        "missed_by_type": {t: missed[t] for t in TYPES},
    }


# ---------- template -> boxes ----------


@dataclass
class _Item:
    """One scorable box of a template: a plain field, or one cell of a table."""

    type: str
    page: int
    rect: Rect
    paths: list[str]  # schema paths this box fills (a cell: its single column path)
    ops: list[str]  # transform op names applied to it, incl. "join" and the when condition's op
    is_cell: bool = False
    multiline_style: bool = False  # style.multiline of a plain field


def _column_path(table_path: str, key: str) -> str:
    # The same join as formcast.template.ops: "items" + "name" -> "items[].name".
    return f"{table_path}[]{'' if key.startswith('[') else '.'}{key}"


def _template_items(template: Template) -> list[_Item]:
    """Plain fields and table cells (``table_cells``); a field linked as a cell counts once, as the cell."""
    items: list[_Item] = []
    linked = linked_cells(template)
    for f in template.fields:
        if f.type == "table":
            table_path = f.source.paths[0] if f.source and f.source.paths else ""
            for cell in table_cells(template, f):
                items.append(
                    _Item(
                        _DETECTION_TYPE[cell.field.type] if cell.field is not None else "text",
                        cell.page,
                        cell.rect,
                        [_column_path(table_path, cell.column.key)] if table_path else [],
                        [t.op for t in cell.column.transforms],
                        is_cell=True,
                    )
                )
        elif f.id not in linked:
            items.append(
                _Item(
                    _DETECTION_TYPE[f.type],
                    f.page,
                    f.rect,
                    list(f.source.paths) if f.source else [],
                    _source_ops(f.source),
                    multiline_style=f.style.multiline,
                )
            )
    return items


def _source_ops(source) -> list[str]:
    """Op names a source applies: its transforms, "join" when it joins paths, and its ``when`` op.

    GT lists all three as transform names ("join", "equals", ...), while a
    template carries the join separator and the choice condition in their own
    ``Source`` fields.
    """
    if source is None:
        return []
    ops = [t.op for t in source.transforms]
    if source.join is not None:
        ops.append("join")
    if source.when is not None:
        w = source.when
        ops.append(WHEN_EQUALS_TRUE if w.op == "equals" and w.args.get("value") is True else w.op)
    return ops


def multiline_flags(gt: list[GTField], items: list[_Item]) -> list[bool]:
    """Which template boxes are multi-line fields: plain text fields (not table
    cells) with ``style.multiline``, or at least ``MULTILINE_HEIGHT_RATIO`` x the
    median height of the GT text boxes on their page.

    Representation-neutral scoring of multi-line answer areas: one tall field
    and one field per printed line both cover the same GT line boxes.
    """
    heights: dict[int, list[float]] = {}
    for g in gt:
        if g.type == "text":
            heights.setdefault(g.page, []).append(g.rect[3] - g.rect[1])
    medians = {p: statistics.median(h) for p, h in heights.items()}
    out = []
    for it in items:
        plain_text = it.type == "text" and not it.is_cell
        median = medians.get(it.page)
        tall = median is not None and it.rect[3] - it.rect[1] >= MULTILINE_HEIGHT_RATIO * median
        out.append(plain_text and (it.multiline_style or tall))
    return out


def score_template_detection(
    gt: list[GTField], template: Template, iou_thr: float, pages: set[int] | None = None
) -> dict:
    """``score_detection`` of a template's boxes (``template_boxes``), multi-line
    fields covering the GT line boxes inside them; ``pages`` keeps only boxes on
    those pages."""
    items = [i for i in _template_items(template) if pages is None or i.page in pages]
    boxes = [(i.type, i.page, i.rect) for i in items]
    return score_detection(gt, boxes, iou_thr, multiline=multiline_flags(gt, items))


def template_boxes(template: Template) -> list[Box]:
    """The template's fields as detection boxes (type, page, rect).

    text -> "text"; checkbox, radio_option, choice_mark -> "choice"; signature ->
    "signature". A table contributes one box per cell (``table_cells``): each segment's
    ``max_rows`` rows (from ``row_top``, ``row_height`` tall) by each column's x range, "text";
    a cell linked to a field (``Column.fields``) is that field's box and type instead.
    """
    return [(i.type, i.page, i.rect) for i in _template_items(template)]


# ---------- mapping ----------


def transforms_match(gt_ops: list[str], field_ops: list[str]) -> bool:
    """Whether ``field_ops`` cover every GT op, with multiplicity, up to ``TRANSFORM_EQUIVALENCE``.

    Extra field ops are fine. ``WHEN_EQUALS_TRUE`` (a ``when`` equals true)
    credits either "bool_to_check" or the equals group.
    """
    need = [_CANONICAL_OP.get(op, op) for op in gt_ops]
    have = [_OP_ALTERNATIVES.get(op, frozenset({_CANONICAL_OP.get(op, op)})) for op in field_ops]
    # Bipartite matching (each field op credits at most one GT op): augmenting paths.
    owner: dict[int, int] = {}  # field op index -> GT op index

    def assign(gi: int, seen: set[int]) -> bool:
        for fi, names in enumerate(have):
            if need[gi] in names and fi not in seen:
                seen.add(fi)
                if fi not in owner or assign(owner[fi], seen):
                    owner[fi] = gi
                    return True
        return False

    return all(assign(gi, set()) for gi in range(len(need)))


def score_mapping(gt: list[GTField], template: Template, iou_thr: float = 0.3) -> dict:
    """How well the template maps boxes to schema paths, over GT fields that have a ``path``.

    Template boxes are matched one to one to *all* GT boxes (so a pathless GT
    box still claims its template field), best IoU first, IoU >= ``iou_thr``.
    The rules:

    * Plain GT (path without ``[]``) matches plain template fields by IoU; a
      GT text box left unmatched is then covered by a multi-line template
      field holding at least 70% of it (``multiline_flags``, ``_match``), so a
      tall answer field gets its path credit through the GT line box that
      carries the path.
    * Table GT (path ``arr[].col``, one GT box per cell) matches only table
      cells, and a cell matches only if IoU >= ``iou_thr`` and the table's
      ``source.paths[0]`` + column key (``arr[].col``) equals the GT path.
    * A GT field is *path-correct* if it matched and its path is in the matched
      field's ``source.paths`` (for a cell: its column path). Unmatched GT is
      path-wrong.
    * Among GT fields with non-empty ``transforms``, one is *transform-correct*
      if it matched and every GT op name appears (with multiplicity) in the
      matched field's ops, where an op of the same ``TRANSFORM_EQUIVALENCE``
      group counts (``transforms_match``). A field's ops are its
      ``source.transforms``, plus "join" when ``source.join`` is set and the
      op of ``source.when`` (a cell: its column's ``transforms``). Extra ops
      are fine. Independent of the path.
    * A GT field is *joint-correct* if it is path-correct and, when it has
      ``transforms``, transform-correct.
    * A GT field is *exact* if it matched and the matched field's set of
      source paths equals ``{path} | also_paths`` (so extra or missing source
      paths fail it).

    Returns ``n_path`` and ``n_transform`` (denominators); counts ``matched``
    (path GT that found a template box), ``path_ok``, ``transform_ok``,
    ``joint_ok`` and ``exact_ok``; and ``matched_rate``, ``path_acc``,
    ``transform_acc`` (over ``n_transform``), ``joint_acc`` and
    ``exact_path_set_acc`` (over ``n_path``).
    """
    items = _template_items(template)

    def allowed(gi: int, pi: int) -> bool:
        g, it = gt[gi], items[pi]
        if g.path is not None and "[]" in g.path:
            return it.is_cell and g.path in it.paths
        return not it.is_cell

    matched = _match(
        gt, [(i.page, i.rect) for i in items], iou_thr, allowed, multiline=multiline_flags(gt, items)
    )
    n_path = n_transform = n_matched = path_ok = transform_ok = joint_ok = exact_ok = 0
    for gi, g in enumerate(gt):
        if g.path is None:
            continue
        item = items[matched[gi]] if gi in matched else None
        n_path += 1
        if item is None:
            n_transform += bool(g.transforms)
            continue
        n_matched += 1
        path_right = g.path in item.paths
        transforms_right = transforms_match(g.transforms, item.ops)
        path_ok += path_right
        if g.transforms:
            n_transform += 1
            transform_ok += transforms_right
        joint_ok += path_right and transforms_right
        exact_ok += set(item.paths) == {g.path, *g.also_paths}
    return {
        "n_path": n_path,
        "n_transform": n_transform,
        "matched": n_matched,
        "path_ok": path_ok,
        "transform_ok": transform_ok,
        "joint_ok": joint_ok,
        "exact_ok": exact_ok,
        "matched_rate": _ratio(n_matched, n_path),
        "path_acc": _ratio(path_ok, n_path),
        "transform_acc": _ratio(transform_ok, n_transform),
        "joint_acc": _ratio(joint_ok, n_path),
        "exact_path_set_acc": _ratio(exact_ok, n_path),
    }
