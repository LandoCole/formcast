"""Coverage report: what a template has not mapped or placed yet."""

from __future__ import annotations

from pydantic import BaseModel

from formcast.geometry import Rect, contained_fraction, iou
from formcast.pagemap.model import FormMap
from formcast.schema import SchemaPath
from formcast.template.model import Field, Template

LOW_CONFIDENCE = 0.5
CANDIDATE_IOU = 0.3
# A candidate this much inside a field box is covered too (a tall answer field over
# several detected line blanks, a table cell over a detected cell).
CANDIDATE_INSIDE = 0.7


class Coverage(BaseModel):
    unmapped_paths: list[str] = []  # mappable schema paths no field source references, schema order
    unused_candidates: list[str] = []  # detector candidates no field covers (see coverage)
    unmapped_fields: list[str] = []  # fields without a source
    low_confidence: list[str] = []  # fields with confidence < 0.5
    mappable_count: int = 0  # schema paths that can be mapped (see is_mappable); mapped = this - unmapped


def is_mappable(sp: SchemaPath) -> bool:
    """Leaf values (incl. multi-select arrays of scalars) and table arrays (items are objects)."""
    if sp.type == "object":
        return False
    return sp.type != "array" or sp.item_type != "array"


def _field_regions(f: Field) -> list[tuple[int, Rect]]:
    """Boxes a field occupies: its rect, or for a table each segment, row and cell."""
    out: list[tuple[int, Rect]] = []
    if f.page is not None and f.rect is not None:
        out.append((f.page, f.rect))
    for seg in f.segments:
        x0, _, x1, _ = seg.rect
        out.append((seg.page, seg.rect))
        for r in range(seg.max_rows):
            top = seg.row_top + r * seg.row_height
            bottom = top + seg.row_height
            out.append((seg.page, (x0, top, x1, bottom)))
            out.extend((seg.page, (col.x[0], top, col.x[1], bottom)) for col in seg.columns)
    return out


def _covers(region: Rect, cand: Rect) -> bool:
    return iou(cand, region) >= CANDIDATE_IOU or contained_fraction(cand, region) >= CANDIDATE_INSIDE


def coverage(template: Template, form_map: FormMap, schema_paths: list[SchemaPath]) -> Coverage:
    """What ``template`` leaves to do. A candidate is used when some field box (a
    table's segment, rows or cells included) overlaps it with IoU >= 0.3 or holds
    at least 70% of it."""
    referenced = {p for f in template.fields if f.source for p in f.source.paths}
    tables = {f.source.paths[0] for f in template.fields if f.type == "table" and f.source and f.source.paths}
    by_path = {sp.path: sp for sp in schema_paths}

    def covered_by_table(sp: SchemaPath) -> bool:
        table = sp.in_table
        while table is not None:
            if table in tables:
                return True
            parent = by_path.get(table)
            table = parent.in_table if parent else None
        return False

    unmapped_paths = [
        sp.path
        for sp in schema_paths
        if is_mappable(sp) and sp.path not in referenced and not covered_by_table(sp)
    ]

    regions: dict[int, list[Rect]] = {}
    for f in template.fields:
        for page, rect in _field_regions(f):
            regions.setdefault(page, []).append(rect)
    unused = [
        c.id
        for page in sorted(form_map.pages, key=lambda p: p.index)
        for c in page.candidates
        if not any(_covers(r, c.rect) for r in regions.get(page.index, []))
    ]

    return Coverage(
        mappable_count=sum(1 for sp in schema_paths if is_mappable(sp)),
        unmapped_paths=unmapped_paths,
        unused_candidates=unused,
        unmapped_fields=[f.id for f in template.fields if f.source is None or not f.source.paths],
        low_confidence=[
            f.id for f in template.fields if f.confidence is not None and f.confidence < LOW_CONFIDENCE
        ],
    )
