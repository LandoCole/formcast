"""Shared scoring for the eval harness: field detection and field-to-schema mapping.

Ground truth (``GTField``) comes from ``load_gt``. Predictions are either a flat
list of boxes (``score_detection``, used for the detector baseline and for any
template via ``template_boxes``) or a whole ``Template`` (``score_mapping``).
All rects are PDF points, origin top-left, unrotated page space.

Ground-truth file ``forms/gt/<id>.json``::

    {"form": id,
     "fields": [{"page": int, "rect": [x0, y0, x1, y1], "type": "text"|"choice"|"signature",
                 "label": str, "path"?: str, "also_paths"?: [str], "transforms"?: [op names]}],
     "widget_paths": {widget_name: {"path": str, "also_paths"?: [str], "transforms"?: [op names]}},
     "notes": str}

Fillable forms take their boxes from the PDF's widgets (``widget_paths`` adds
``path``/``also_paths``/``transforms`` by widget name; push buttons are not data
fields and are skipped); flat and scanned forms take them from ``fields``. A
table is ground-truthed one box per cell with path ``arr[].col``. ``also_paths``
lists the further schema paths a box is fed from (a full name from first + last:
``path`` is the first, ``also_paths`` the rest).

A manifest entry's ``scored_pages`` (0-based page indexes; absent = every page)
limits ground truth and scoring to those pages.

Division by zero: every ratio is 0.0 when its denominator is 0.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # eval scripts do not rely on the editable install

import pymupdf  # noqa: E402

from formcast.geometry import Rect, iou  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.template.model import Template  # noqa: E402

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


@dataclass
class GTField:
    page: int
    rect: Rect
    type: Literal["text", "choice", "signature"]
    label: str = ""
    path: str | None = None
    transforms: list[str] = field(default_factory=list)
    also_paths: list[str] = field(default_factory=list)


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
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source}: bad field entry {entry!r}") from exc


def load_gt(form: dict, pdf_path, gt_dir: Path | None = None) -> list[GTField] | None:
    """Ground-truth fields of one form, or None when it has none.

    ``form`` is a ``forms/manifest.json`` entry (``id``, ``kind`` and
    ``scored_pages`` are used); only fields on scored pages are returned.
    A fillable form's boxes are its PDF widgets, typed like the CommonForms
    classes: check boxes and radio buttons are "choice", signatures
    "signature", everything else "text"; push buttons (not data fields) and
    widgets under 3 pt are dropped. A ``widget_paths`` entry for the widget's
    field name adds ``path``, ``also_paths`` and ``transforms``, and its optional
    ``type`` (text, choice or signature) overrides the widget's type, e.g. for a
    signature line made as a text widget. A fillable form
    with no widgets has no ground truth. Flat and scanned forms read ``fields``
    from the GT file and have none without it.

    Raises:
        ValueError: when the GT file is not valid JSON or has a malformed field.
    """
    gt_dir = GT_DIR if gt_dir is None else Path(gt_dir)
    data = _read_gt_file(gt_dir, form["id"])
    source = f"{form['id']}.json"
    if form["kind"] != "fillable":
        if data is None:
            return None
        fields = [_gt_from_entry(e, source) for e in data.get("fields", [])]
        keep = form.get("scored_pages")
        return fields if keep is None else [g for g in fields if g.page in keep]

    widget_paths = (data or {}).get("widget_paths", {})
    for name, extra in widget_paths.items():
        if "type" in extra and extra["type"] not in TYPES:
            raise ValueError(f"{source}: widget_paths[{name!r}] has unknown type {extra['type']!r}")
    out: list[GTField] = []
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
                out.append(
                    GTField(
                        page=page.number,
                        rect=(w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1),
                        type=extra.get("type") or _widget_type(w),
                        label=w.field_label or w.field_name or "",
                        path=extra.get("path"),
                        transforms=list(extra.get("transforms", [])),
                        also_paths=list(extra.get("also_paths", [])),
                    )
                )
    finally:
        doc.close()
    return out or None


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


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def score_detection(gt: list[GTField], boxes: list[Box], iou_thr: float) -> dict:
    """Detection score of ``boxes`` (type, page, rect) against ``gt``.

    Boxes match GT boxes one to one on the same page, best IoU first, when
    IoU >= ``iou_thr``; the type is not required to agree. Returns ``gt``,
    ``pred`` and ``matched`` counts, ``recall`` (matched/gt), ``precision``
    (matched/pred), ``type_ok`` (matched pairs whose types agree), ``type_acc``
    (type_ok/matched) and ``missed_by_type`` (unmatched GT per GT type).
    """
    matched = _greedy_match(
        [(g.page, g.rect) for g in gt], [(page, rect) for _, page, rect in boxes], iou_thr
    )
    hit = {gi for gi, _ in matched}
    type_ok = sum(gt[gi].type == boxes[pi][0] for gi, pi in matched)
    missed = Counter(g.type for i, g in enumerate(gt) if i not in hit)
    return {
        "gt": len(gt),
        "pred": len(boxes),
        "matched": len(matched),
        "recall": _ratio(len(matched), len(gt)),
        "precision": _ratio(len(matched), len(boxes)),
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


def _column_path(table_path: str, key: str) -> str:
    # The same join as formcast.template.ops: "items" + "name" -> "items[].name".
    return f"{table_path}[]{'' if key.startswith('[') else '.'}{key}"


def _template_items(template: Template) -> list[_Item]:
    items: list[_Item] = []
    for f in template.fields:
        if f.type == "table":
            table_path = f.source.paths[0] if f.source and f.source.paths else ""
            for seg in f.segments:
                for col in seg.columns:
                    for row in range(seg.max_rows):
                        top = seg.row_top + row * seg.row_height
                        items.append(
                            _Item(
                                "text",
                                seg.page,
                                (col.x[0], top, col.x[1], top + seg.row_height),
                                [_column_path(table_path, col.key)] if table_path else [],
                                [t.op for t in col.transforms],
                                is_cell=True,
                            )
                        )
        else:
            items.append(
                _Item(
                    _DETECTION_TYPE[f.type],
                    f.page,
                    f.rect,
                    list(f.source.paths) if f.source else [],
                    _source_ops(f.source),
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
        ops.append(source.when.op)
    return ops


def template_boxes(template: Template) -> list[Box]:
    """The template's fields as detection boxes (type, page, rect).

    text -> "text"; checkbox, radio_option, choice_mark -> "choice"; signature ->
    "signature". A table contributes one "text" box per cell: each segment's
    ``max_rows`` rows (from ``row_top``, ``row_height`` tall) by each column's x range.
    """
    return [(i.type, i.page, i.rect) for i in _template_items(template)]


# ---------- mapping ----------


def transforms_match(gt_ops: list[str], field_ops: list[str]) -> bool:
    """Whether ``field_ops`` cover every GT op, with multiplicity, up to ``TRANSFORM_EQUIVALENCE``.

    Extra field ops are fine.
    """
    need = Counter(_CANONICAL_OP.get(op, op) for op in gt_ops)
    have = Counter(_CANONICAL_OP.get(op, op) for op in field_ops)
    return not (need - have)


def score_mapping(gt: list[GTField], template: Template, iou_thr: float = 0.3) -> dict:
    """How well the template maps boxes to schema paths, over GT fields that have a ``path``.

    Template boxes are matched one to one to *all* GT boxes (so a pathless GT
    box still claims its template field), best IoU first, IoU >= ``iou_thr``.
    The rules:

    * Plain GT (path without ``[]``) matches plain template fields by IoU only.
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

    matched = dict(
        _greedy_match([(g.page, g.rect) for g in gt], [(i.page, i.rect) for i in items], iou_thr, allowed)
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
