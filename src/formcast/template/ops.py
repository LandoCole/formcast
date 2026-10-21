"""Edit ops: the one mutation API shared by human editors and the agent.

Every change to a template is an op (plain data, ``{"op": name, ...}``) passed to
:func:`apply`, which validates it, returns a *new* template (the input is never
mutated) plus warnings, and appends the op to ``history``. A rejected op raises
:class:`~formcast.errors.OpError`; its ``reason`` is shown back to the agent, so
reasons name the field id, the offending value and what would be valid.

* ``move`` takes either ``rect`` or ``dx``/``dy``; ``resize`` sets ``rect``. On a
  table, ``dx``/``dy`` shift every segment (or only ``segment`` if given) with its
  rows and columns; a ``rect`` needs ``segment``. ``move`` carries that segment's
  rows and columns along; ``resize`` keeps them in place and checks they still fit.
* ``set_style`` merges: only the style keys given in the op change.
* ``set_source`` with ``source=None`` unmaps a field; ``link_widget`` with
  ``widget=None`` unlinks it. A widget name links to one field, except that
  radio_option fields (or checkbox fields) may share one: the kids of a radio
  or checkbox group carry their group's name.
* A table field's ``source.paths`` is its one array path; column keys are paths
  relative to a row (``"dose"`` for ``medications[].dose``).
* A table column's ``fields`` links existing plain fields as its cells (one id per
  row): each must be on the segment's page with its center inside the segment rect,
  have no source of its own, and be linked once. A linked field can't get a source
  or be removed until it is unlinked.
* Seeded fields (``origin`` widget or detector) are evidence of a blank, so an
  agent's ``remove_field`` on one is rejected unless another field or table cell
  covers the same box (IoU >= ``REPLACE_IOU``): a duplicate, or a cell that replaces
  it. A seeded box that is not a blank stays unmapped (it fills nothing). Human edits
  are not restricted.
* History records the op as given (only the keys that were set) plus any
  generated id, so :func:`replay` reproduces the template exactly.
"""

from __future__ import annotations

import datetime
import difflib
import math
import re
from collections.abc import Callable

from pydantic import BaseModel, TypeAdapter, ValidationError

from formcast.errors import OpError
from formcast.geometry import Rect, contained_fraction, iou
from formcast.pagemap.model import FormMap
from formcast.schema import SchemaPath, flatten_schema
from formcast.template.model import (
    Column,
    Field,
    HistoryEntry,
    Source,
    Style,
    TableSegment,
    Template,
    linked_cells,
    table_cells,
)
from formcast.template.op_models import (
    OP_CLASSES,
    OP_NAMES,
    AddField,
    AddTable,
    AddTableSegment,
    FieldType,
    LinkWidget,
    Move,
    Op,
    OpBase,
    RemoveField,
    Resize,
    SetLabel,
    SetSource,
    SetStyle,
    SetTableColumns,
    SetTableRows,
    SetType,
)
from formcast.transforms import Transform, validate_chain

__all__ = [  # op models are re-exported so ``from formcast.template.ops import Move`` keeps working
    "OP_NAMES", "AddField", "AddTable", "AddTableSegment", "FieldType", "LinkWidget", "Move", "Op",
    "OpContext", "RemoveField", "Resize", "SetLabel", "SetSource", "SetStyle", "SetTableColumns",
    "SetTableRows", "SetType", "apply", "diff", "replay",
]  # fmt: skip

CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")
_SHARED_WIDGET_TYPES = ("radio_option", "checkbox")  # field types whose widget kids share one name
EDGE_TOLERANCE = 2.0  # how far (pt) a rect may stick out past the page edge
MIN_SIZE = 2.0  # minimum rect width and height (pt)
OVERLAP_WARN_IOU = 0.5
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{0,63}")
_GENERATED_ID = re.compile(r"f(\d+)")
_ADAPTER: TypeAdapter = TypeAdapter(Op)
# Ops after which the touched field is checked for overlap / checkbox-source warnings.
_WARN_OPS = {
    "add_field", "move", "resize", "set_type", "set_source",
    "add_table", "set_table_rows", "set_table_columns", "add_table_segment",
}  # fmt: skip
# A plain field this much inside one table cell is covered by the table.
CELL_COVER_SHARE = 0.7
# Another box this close (IoU) to a seeded field's box holds the same blank: removing the
# seeded field then loses nothing (a duplicate, or a table cell that replaces it).
REPLACE_IOU = 0.6
_SEEDED = ("widget", "detector")
MAX_WARN_IDS = 20


class OpContext(BaseModel):
    """What ops are validated against. ``widget_names``, when given, limits ``link_widget``."""

    schema_paths: list[SchemaPath]
    page_sizes: list[tuple[float, float]]
    widget_names: list[str] | None = None

    @classmethod
    def from_template(cls, template: Template, form_map: FormMap | None = None) -> OpContext:
        """Context from the template's own schema and page sizes; widget names from ``form_map`` if given."""
        return cls(
            schema_paths=flatten_schema(template.schema_),
            page_sizes=list(template.form.page_sizes),
            widget_names=None if form_map is None else [w.name for p in form_map.pages for w in p.widgets],
        )


# ------------------------------------------------------------------ public API


def apply(
    template: Template, op: Op | dict, *, author: str, note: str = "", context: OpContext | None = None
) -> tuple[Template, list[str]]:
    """Validate and apply ``op``; return the new template and any warnings.

    Without ``context`` the op is validated against ``OpContext.from_template(template)``
    (its schema and page sizes; any widget name may be linked).
    """
    context = context if context is not None else OpContext.from_template(template)
    return _apply(template, op, author=author, note=note, context=context, at=None)


def replay(base: Template, history: list[HistoryEntry], context: OpContext | None = None) -> Template:
    """Re-apply ``history`` on ``base``, keeping each entry's author, note and timestamp.

    ``context`` defaults to ``OpContext.from_template(base)``.
    """
    context = context if context is not None else OpContext.from_template(base)
    t = base
    for entry in history:
        t, _ = _apply(t, entry.op, author=entry.author, note=entry.note, context=context, at=entry.at)
    return t


def diff(a: Template, b: Template) -> list[dict]:
    """Field-level changes from ``a`` to ``b``: removed/changed (in ``a`` order), then added."""
    out: list[dict] = []
    b_fields = {f.id: f for f in b.fields}
    a_ids = {f.id for f in a.fields}
    for fa in a.fields:
        fb = b_fields.get(fa.id)
        if fb is None:
            out.append({"id": fa.id, "change": "removed"})
            continue
        da, db = fa.model_dump(), fb.model_dump()
        keys = [k for k in da if da[k] != db[k]]
        if keys:
            out.append({"id": fa.id, "change": "changed", "keys": keys})
    out.extend({"id": f.id, "change": "added"} for f in b.fields if f.id not in a_ids)
    return out


# ------------------------------------------------------------------ internals


def _apply(
    template: Template, op: Op | dict, *, author: str, note: str, context: OpContext, at: str | None
) -> tuple[Template, list[str]]:
    model, raw = _parse(op)
    if author not in ("agent", "human"):
        raise OpError(raw, f"author must be 'agent' or 'human', got {author!r}")
    t = template.model_copy(deep=True)
    run = _Run(t, context, raw, author)
    touched = _HANDLERS[model.op](run, model)
    warnings = run.warnings(touched) if touched is not None and model.op in _WARN_OPS else []
    record = {"op": model.op, **model.model_dump(mode="json", exclude_unset=True)}
    t.history.append(
        HistoryEntry(op=record, author=author, note=note, at=at or datetime.datetime.now(datetime.UTC).isoformat())
    )
    return t, warnings


def _parse(op: object) -> tuple[OpBase, dict]:
    if isinstance(op, OP_CLASSES):
        return op.model_copy(deep=True), {"op": op.op, **op.model_dump(mode="json", exclude_unset=True)}
    if not isinstance(op, dict):
        raise OpError({}, f"an op must be a dict like {{'op': 'move', ...}} or an op model, got {type(op).__name__}")
    name = op.get("op")
    if not isinstance(name, str) or name not in OP_NAMES:
        raise OpError(dict(op), f"unknown op {name!r}; valid ops are: {', '.join(OP_NAMES)}")
    try:
        return _ADAPTER.validate_python(op), dict(op)
    except ValidationError as e:
        raise OpError(dict(op), f"invalid {name} op: {_describe(e, name)}") from e


def _describe(e: ValidationError, name: str) -> str:
    parts, rect_error = [], False
    for err in e.errors(include_url=False):
        loc = list(err["loc"])
        if loc and loc[0] == name:
            loc = loc[1:]
        rect_error = rect_error or "rect" in loc
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] not in ("missing", "extra_forbidden") and loc:
            got = repr(err.get("input"))
            msg += f" (got {got[:77] + '...' if len(got) > 80 else got})"
        parts.append(f"{'.'.join(str(p) for p in loc)}: {msg}" if loc else msg)
    if rect_error:
        parts.append("a rect is 4 numbers [x0, y0, x1, y1] in PDF points, origin top-left")
    return "; ".join(parts)


def _fmt(rect: tuple[float, ...]) -> str:
    return "[" + ", ".join(f"{v:g}" for v in rect) + "]"


def _column_path(table_path: str, key: str) -> str:
    return f"{table_path}[]{'' if key.startswith('[') else '.'}{key}"


class _Run:
    """One op application: the working template copy plus validation helpers."""

    def __init__(self, t: Template, ctx: OpContext, raw: dict, author: str):
        self.t, self.ctx, self.raw, self.author = t, ctx, raw, author
        self.paths = {sp.path: sp for sp in ctx.schema_paths}

    def fail(self, reason: str) -> None:
        raise OpError(self.raw, reason)

    # -- lookups

    def field(self, fid: str) -> Field:
        for f in self.t.fields:
            if f.id == fid:
                return f
        if any(h.op.get("op") == "remove_field" and h.op.get("id") == fid for h in self.t.history):
            self.fail(f"unknown field id {fid!r}: it was already removed by an earlier remove_field "
                      "(add_field makes a new one if the blank is needed)")
        ids = [f.id for f in self.t.fields]
        if not ids:
            self.fail(f"unknown field id {fid!r}; the template has no fields yet")
        close = difflib.get_close_matches(str(fid), ids, n=5, cutoff=0.6)
        hint = f"; similar ids: {', '.join(close)}" if close else ""
        self.fail(f"unknown field id {fid!r}{hint}; the template has {len(ids)} field(s), listed in tpl")

    def table(self, fid: str) -> Field:
        f = self.field(fid)
        if f.type != "table":
            self.fail(f"field {fid!r} is a {f.type} field, not a table; table ops need a field made by add_table")
        return f

    def table_segment(self, f: Field, index: int | None, verb: str) -> TableSegment:
        if index is None:
            self.fail(f"table field {f.id!r}: {verb} with a rect needs segment=<index> (0..{len(f.segments) - 1}) "
                      "to say which segment's rect to set; or move by dx/dy to shift the whole table")
        return self.segment(f, index)

    def no_segment(self, f: Field, index: int | None) -> None:
        if index is not None:
            self.fail(f"field {f.id!r} is a {f.type} field; segment={index} applies only to table fields")

    def segment(self, f: Field, index: int) -> TableSegment:
        if not 0 <= index < len(f.segments):
            self.fail(f"table field {f.id!r} has no segment {index}; valid segments are 0..{len(f.segments) - 1}")
        return f.segments[index]

    def new_id(self, wanted: str | None) -> str:
        if wanted is None:
            nums = [int(m.group(1)) for f in self.t.fields if (m := _GENERATED_ID.fullmatch(f.id))]
            return f"f{max(nums, default=0) + 1}"
        if not ID_PATTERN.fullmatch(wanted):
            self.fail(f"invalid field id {wanted!r}: ids start with a letter and use only letters, digits, _ or - "
                      "(at most 64 characters); or omit id to get a generated one")
        if any(f.id == wanted for f in self.t.fields):
            self.fail(f"duplicate id {wanted!r}: a field with that id already exists; pick another id or omit it")
        return wanted

    def schema_path(self, fid: str, path: str) -> SchemaPath:
        sp = self.paths.get(path)
        if sp is None:
            close = difflib.get_close_matches(path, list(self.paths), n=3, cutoff=0.6)
            hint = (f"; did you mean {', '.join(map(repr, close))}?" if close
                    else "; use a path from the schema path list")
            self.fail(f"field {fid!r}: unknown schema path {path!r}{hint}")
        return sp

    # -- geometry

    def check_rect(self, fid: str, page: int, rect: Rect, what: str = "rect") -> None:
        n = len(self.ctx.page_sizes)
        if not 0 <= page < n:
            self.fail(f"field {fid!r}: page {page} does not exist; valid pages are 0..{n - 1}")
        if not all(math.isfinite(v) for v in rect):
            self.fail(f"field {fid!r}: {what} {list(rect)} has non-finite coordinates")
        x0, y0, x1, y1 = rect
        if x1 - x0 < MIN_SIZE or y1 - y0 < MIN_SIZE:
            self.fail(
                f"field {fid!r}: {what} {_fmt(rect)} is degenerate (width {x1 - x0:g}, height {y1 - y0:g}); "
                f"rects are [x0, y0, x1, y1] with x1 - x0 and y1 - y0 each at least {MIN_SIZE:g}pt"
            )
        w, h = self.ctx.page_sizes[page]
        tol = EDGE_TOLERANCE
        if x0 < -tol or y0 < -tol or x1 > w + tol or y1 > h + tol:
            self.fail(
                f"field {fid!r}: {what} {_fmt(rect)} is off page {page}; the page is {w:g} x {h:g}pt, "
                f"so the rect must lie within [0, 0, {w:g}, {h:g}] (give or take {tol:g}pt)"
            )

    def check_segment(self, f: Field, i: int) -> None:
        seg, where = f.segments[i], f"table field {f.id!r} segment {i}"
        self.check_rect(f.id, seg.page, seg.rect, what=f"segment {i} rect")
        x0, y0, x1, y1 = seg.rect
        # NaN/inf pass every comparison below and serialize as null, so reject them first.
        for name, value in (("row_top", seg.row_top), ("row_height", seg.row_height)):
            if not math.isfinite(value):
                self.fail(f"{where}: {name} {value!r} is non-finite; give a number of PDF points")
        for col in seg.columns:
            if not all(math.isfinite(v) for v in col.x):
                self.fail(f"{where}: column {col.key!r} x {list(col.x)} has non-finite coordinates")
        if seg.row_height <= 0 or seg.max_rows < 1:
            self.fail(f"{where}: row_height must be > 0 and max_rows >= 1 "
                      f"(got row_height={seg.row_height:g}, max_rows={seg.max_rows})")
        if not y0 - EDGE_TOLERANCE <= seg.row_top < y1:
            self.fail(f"{where}: row_top {seg.row_top:g} is outside the segment rect's y range [{y0:g}, {y1:g})")
        bottom = seg.row_top + seg.row_height * seg.max_rows
        if bottom > y1 + EDGE_TOLERANCE:
            fit = int((y1 + EDGE_TOLERANCE - seg.row_top) // seg.row_height)
            self.fail(
                f"{where}: {seg.max_rows} rows of {seg.row_height:g}pt from row_top {seg.row_top:g} end at "
                f"y={bottom:g}, past the segment rect bottom {y1:g}; at most {fit} rows fit "
                "(lower max_rows or row_height, or enlarge the segment rect)"
            )
        seen: set[str] = set()
        for col in seg.columns:
            cx0, cx1 = col.x
            if col.key in seen:
                self.fail(f"{where}: column key {col.key!r} appears twice")
            seen.add(col.key)
            if cx1 - cx0 < MIN_SIZE or cx0 < x0 - EDGE_TOLERANCE or cx1 > x1 + EDGE_TOLERANCE:
                self.fail(f"{where}: column {col.key!r} x {_fmt(col.x)} must be at least {MIN_SIZE:g}pt wide "
                          f"and inside the segment rect's x range [{x0:g}, {x1:g}]")
            self.check_chain(f"{where} column {col.key!r} transforms", col.transforms)
            self.check_column_fields(f, i, col)

    def check_column_fields(self, f: Field, i: int, col: Column) -> None:
        seg, where = f.segments[i], f"table field {f.id!r} segment {i} column {col.key!r}"
        if len(col.fields) > seg.max_rows:
            self.fail(f"{where}: fields lists {len(col.fields)} ids but the segment has {seg.max_rows} rows; "
                      "give at most one field id (or None) per row")
        tables = [g for g in self.t.fields if g.type == "table" and g is not f] + [f]
        for row, fid in enumerate(col.fields):
            if fid is None:
                continue
            g = next((g for g in self.t.fields if g.id == fid), None)
            if g is None:
                self.fail(f"{where} row {row}: unknown field id {fid!r}; fields links existing plain fields "
                          "(ids in tpl), one per row, None where a row has none")
            if g.type == "table":
                self.fail(f"{where} row {row}: {fid!r} is a table; a cell can only link a plain field")
            x0, y0, x1, y1 = seg.rect
            cx, cy = (g.rect[0] + g.rect[2]) / 2, (g.rect[1] + g.rect[3]) / 2
            tol = EDGE_TOLERANCE
            if g.page != seg.page or not (x0 - tol <= cx <= x1 + tol and y0 - tol <= cy <= y1 + tol):
                self.fail(f"{where} row {row}: field {fid!r} (page {g.page}, rect {_fmt(g.rect)}) is not inside the "
                          f"segment rect {_fmt(seg.rect)} on page {seg.page}; link fields that sit in this segment")
            if g.source is not None and g.source.paths:
                self.fail(f"{where} row {row}: field {fid!r} has its own source {g.source.paths}; a linked cell gets "
                          f"its value from the column, so set_source({fid!r}, None) first")
            uses = sum(c.fields.count(fid) for t in tables for s in t.segments for c in s.columns)
            if uses > 1:
                self.fail(f"{where} row {row}: field {fid!r} is linked as a cell more than once; "
                          "each field can be one cell of one table")

    def cell_link(self, fid: str) -> str | None:
        """How a field linked as a table cell is linked, for messages; None when it is not."""
        link = linked_cells(self.t).get(fid)
        if link is None:
            return None
        table, seg, row, key = link
        return f"the cell of table {table!r} segment {seg} column {key!r} row {row}"

    # -- sources

    def check_chain(self, where: str, chain: list[Transform]) -> None:
        errors = validate_chain(chain)
        if errors:
            self.fail(f"{where}: invalid transform chain: {'; '.join(errors)}; "
                      "list the valid transforms with transforms()")

    def check_source(self, f: Field) -> None:
        s = f.source
        if s is None:
            return
        if f.type != "table" and (link := self.cell_link(f.id)):
            self.fail(f"field {f.id!r} is {link}, so its value comes from that column; set the column's "
                      "transforms with set_table_columns instead, or unlink the field from the column first")
        if not s.paths:
            self.fail(f"field {f.id!r}: source.paths is empty; give at least one schema path, or source=None to unmap")
        resolved = [self.schema_path(f.id, p) for p in s.paths]
        self.check_chain(f"field {f.id!r} source.transforms", s.transforms)
        if s.when is not None:
            self.check_chain(f"field {f.id!r} source.when", [s.when])
            if f.type not in CHOICE_TYPES:
                self.fail(f"field {f.id!r}: source.when is only allowed on choice fields ({', '.join(CHOICE_TYPES)}), "
                          f"but this is a {f.type} field; drop when, or set_type to a choice type first")
        if f.type == "table":
            self.check_table_source(f, resolved)
            return
        for sp in resolved:
            if sp.type == "object":
                self.fail(f"field {f.id!r}: schema path {sp.path!r} is an object; map one of its leaf paths instead")
            if sp.type == "array" and sp.item_type == "object":
                self.fail(f"field {f.id!r}: schema path {sp.path!r} is a list of rows; map it with "
                          f"add_table(source_path={sp.path!r}, segments=[...]) instead")
            if sp.in_table:
                self.fail(self.column_on_plain_field(f, sp))

    def column_on_plain_field(self, f: Field, sp: SchemaPath) -> str:
        """Why a table column path can't go on a plain field, and the table that would hold it."""
        table = sp.in_table
        key = sp.path[len(table) + 2:].removeprefix(".") if sp.path.startswith(table + "[]") else sp.path
        reason = (f"field {f.id!r}: schema path {sp.path!r} is a column of table {table!r}; "
                  f"map it as a column key in add_table/set_table_columns for {table!r}")
        if f.rect is None:
            return reason
        x0, y0, x1, _ = (round(v, 1) for v in f.rect)
        return (f"{reason}. For a cell at this field's rect: add_table(source_path={table!r}, segments=[{{"
                f"'page': {f.page}, 'rect': <the table area>, 'row_top': {y0:g}, 'row_height': <distance "
                "between consecutive row tops>, 'max_rows': <rows>, 'columns': "
                f"[{{'key': {key!r}, 'x': [{x0:g}, {x1:g}], 'fields': [<one field id per row>]}}, ...]}}]); "
                "fm.grid([...the fields the table holds...]) gives row_top, row_height, the column x ranges "
                "and column_fields. Link the plain fields as the cells (fields) and unmap them "
                f"(set_source({f.id!r}, None)) rather than removing them, so each cell keeps its box. If the "
                "records are printed as repeated blocks of lines: one table per block line, one segment per "
                "record (max_rows=1).")

    def check_table_source(self, f: Field, resolved: list[SchemaPath]) -> None:
        if len(resolved) != 1:
            self.fail(f"table field {f.id!r}: source needs exactly one array path, got {[sp.path for sp in resolved]}")
        sp = resolved[0]
        if sp.type != "array":
            arrays = [p.path for p in self.ctx.schema_paths if p.type == "array"]
            self.fail(f"table field {f.id!r}: source path {sp.path!r} has type {sp.type}, but a table needs a "
                      f"type=array path; array paths in the schema: {arrays or 'none'}")
        for i, seg in enumerate(f.segments):
            for col in seg.columns:
                full = _column_path(sp.path, col.key)
                if full in self.paths:
                    continue
                prefix = sp.path + "[]."
                keys = [p.path[len(prefix):] for p in self.ctx.schema_paths
                        if p.in_table == sp.path and p.path.startswith(prefix)]
                hint = (f"valid column keys: {keys}" if keys
                        else f"items of {sp.path!r} are {sp.item_type}, not objects, so it has no columns")
                self.fail(f"table field {f.id!r} segment {i}: column key {col.key!r} ({full!r}) "
                          f"is not in the schema; {hint}")

    # -- warnings

    def table_cover_warning(self, f: Field) -> list[str]:
        """Plain fields that the table's cells cover, so the table may duplicate them; the ones
        whose box no cell matches are listed apart, since removing them would lose their box."""
        cells = table_cells(self.t, f)
        linked = {c.field.id for c in cells if c.field is not None}
        matched: list[str] = []
        mismatched: list[tuple[str, float]] = []
        for g in self.t.fields:
            if g is f or g.rect is None or g.id in linked:
                continue
            inside = [
                c.rect for c in cells if c.page == g.page and contained_fraction(g.rect, c.rect) >= CELL_COVER_SHARE
            ]
            if not inside:
                continue
            best = max(iou(g.rect, c) for c in inside)
            if best >= REPLACE_IOU:
                matched.append(g.id)
            else:
                mismatched.append((g.id, best))
        out = []
        if matched:
            out.append(f"table {f.id!r} covers field(s) {_ids(matched)}; if its cells replace them, link them as "
                       "the cells (the column's fields) or remove_field them, so each blank is filled once")
        if mismatched:
            lo, hi = min(v for _, v in mismatched), max(v for _, v in mismatched)
            out.append(
                f"table {f.id!r}: its cells are larger than or offset from the boxes of field(s) "
                f"{_ids([i for i, _ in mismatched])} (cell IoU {lo:.2f}-{hi:.2f}), so cells would not sit on those "
                "blanks. Link those fields as the cells instead of removing them: give each column fields=[one "
                "field id per row, None where a row has none] (fm.grid(ids)['column_fields'] lists them per "
                "column); each cell then keeps its field's exact box and widget"
            )
        return out

    def best_cover(self, f: Field) -> tuple[float, str | None]:
        """The highest IoU of another field's box or table cell with ``f``'s box, and whose it is."""
        best: tuple[float, str | None] = (0.0, None)
        for g in self.t.fields:
            if g is f:
                continue
            if g.type == "table":
                boxes = [(c.page, c.rect) for c in table_cells(self.t, g) if c.field is not f]
            else:
                boxes = [(g.page, g.rect)]
            for page, rect in boxes:
                if page == f.page:
                    v = iou(f.rect, rect)
                    if v > best[0]:
                        best = (v, g.id)
        return best

    def check_seed_removal(self, f: Field) -> None:
        """An agent may drop a seeded box only when another field or cell holds the same box."""
        best, nearest = self.best_cover(f)
        if best >= REPLACE_IOU:
            return
        if f.origin == "widget":
            what = f"the form's own widget {f.existing_widget!r}" if f.existing_widget else "a widget's field"
        else:
            what = "a detected box"
        closest = f" (the closest box is {nearest!r}, IoU {best:.2f})" if nearest is not None and best > 0 else ""
        reason = (f"field {f.id!r} is {what} and no second field or table cell covers the same box{closest}, so "
                  "removing it could drop a blank. Seeded fields can be removed only once another field or cell "
                  f"covers the same box (IoU >= {REPLACE_IOU:g}), as for a duplicate. ")
        if best >= 0.1:
            reason += "To change a seeded box's geometry, move or resize it instead of replacing it. "
        if f.origin == "widget":
            reason += ("Keep it, with an empty source when no schema path fits. If a table holds it, link it as a "
                       "cell (the column's fields, one id per row; fm.grid(ids)['column_fields']) instead of "
                       "removing it")
        else:
            reason += ("Its seeded label is just the nearest text line and is often wrong, so judge the box itself "
                       "(fm.region around its rect): map it if it is a blank, move/resize it to fix its geometry, "
                       "and if it is not a blank or no schema path fits, leave it with an empty source (an "
                       "unmapped field fills nothing)")
        self.fail(reason)

    def warnings(self, f: Field) -> list[str]:
        if f.type == "table":
            return self.table_cover_warning(f)
        out = []
        if f.rect is not None:
            for g in self.t.fields:
                if g is not f and g.page == f.page and g.rect is not None:
                    v = iou(f.rect, g.rect)
                    if v > OVERLAP_WARN_IOU:
                        out.append(f"field {f.id!r} overlaps field {g.id!r} on page {f.page} (IoU {v:.2f}); "
                                   "if they are the same blank, remove one")
        s = f.source
        for p in s.paths if s else []:
            if p not in self.paths:
                out.append(f"field {f.id!r} maps {p!r}, which is not in the schema; set_source to a valid path")
        if f.type == "checkbox" and s and s.when is None and not any(tr.op == "bool_to_check" for tr in s.transforms):
            for p in s.paths:
                sp = self.paths.get(p)
                if sp is not None and sp.type not in ("boolean", "enum", "array"):
                    out.append(f"checkbox field {f.id!r} maps {p!r} (type {sp.type}) with no when condition or "
                               "bool_to_check transform; add when (e.g. equals/contains) or bool_to_check "
                               "so the box state is explicit")
        return out


def _ids(ids: list[str]) -> str:
    shown = ", ".join(repr(i) for i in ids[:MAX_WARN_IDS])
    return shown + (f" and {len(ids) - MAX_WARN_IDS} more" if len(ids) > MAX_WARN_IDS else "")


# ------------------------------------------------------------------ handlers
# Each handler mutates ``run.t`` and returns the field it touched (for warnings).


def _add_field(run: _Run, op: AddField) -> Field:
    if op.type == "table":
        run.fail("add_field cannot create a table; use add_table(source_path=..., segments=[...])")
    op.id = run.new_id(op.id)
    run.check_rect(op.id, op.page, op.rect)
    f = Field(id=op.id, type=op.type, label=op.label, page=op.page, rect=op.rect, source=op.source,
              style=op.style or Style(), origin=run.author)
    run.check_source(f)
    run.t.fields.append(f)
    return f


def _remove_field(run: _Run, op: RemoveField) -> None:
    f = run.field(op.id)
    if link := run.cell_link(f.id):
        run.fail(f"field {f.id!r} is {link}; unlink it first (set_table_columns with None in its place in "
                 "that column's fields) or remove the table")
    if run.author == "agent" and f.origin in _SEEDED:
        run.check_seed_removal(f)
    run.t.fields.remove(f)


def _move(run: _Run, op: Move) -> Field:
    f = run.field(op.id)
    if f.type == "table":
        _move_table(run, f, op)
        return f
    run.no_segment(f, op.segment)
    if op.rect is not None:
        rect = op.rect
    else:
        dx, dy = op.dx or 0.0, op.dy or 0.0
        rect = (f.rect[0] + dx, f.rect[1] + dy, f.rect[2] + dx, f.rect[3] + dy)
    run.check_rect(f.id, f.page, rect)
    f.rect = rect
    return f


def _shift_segment(seg: TableSegment, dx: float, dy: float) -> None:
    """Move a segment's rect, rows and columns together."""
    x0, y0, x1, y1 = seg.rect
    seg.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    seg.row_top += dy
    for col in seg.columns:
        col.x = (col.x[0] + dx, col.x[1] + dx)


def _move_table(run: _Run, f: Field, op: Move) -> None:
    if op.rect is not None:
        seg = run.table_segment(f, op.segment, "move")
        x0, y0, *_ = seg.rect
        _shift_segment(seg, op.rect[0] - x0, op.rect[1] - y0)
        seg.rect = op.rect  # a different size is allowed; check_segment verifies rows/columns still fit
        run.check_segment(f, op.segment)
        return
    if op.segment is not None:
        run.segment(f, op.segment)  # validates the index
    for i in range(len(f.segments)) if op.segment is None else [op.segment]:
        _shift_segment(f.segments[i], op.dx or 0.0, op.dy or 0.0)
        run.check_segment(f, i)


def _resize(run: _Run, op: Resize) -> Field:
    f = run.field(op.id)
    if f.type == "table":
        run.table_segment(f, op.segment, "resize").rect = op.rect  # rows and columns keep their positions
        run.check_segment(f, op.segment)
        return f
    run.no_segment(f, op.segment)
    run.check_rect(f.id, f.page, op.rect)
    f.rect = op.rect
    return f


def _set_label(run: _Run, op: SetLabel) -> Field:
    f = run.field(op.id)
    f.label = op.label
    return f


def _set_type(run: _Run, op: SetType) -> Field:
    f = run.field(op.id)
    if op.type == "table":
        run.fail(f"set_type cannot turn field {f.id!r} into a table (a table needs an array source and segments); "
                 f"remove_field({f.id!r}) then add_table(source_path=..., segments=[...])")
    if f.type == "table":
        run.fail(f"field {f.id!r} is a table and has no page/rect; remove_field({f.id!r}) then add_field instead")
    f.type = op.type
    run.check_source(f)
    return f


def _set_source(run: _Run, op: SetSource) -> Field:
    f = run.field(op.id)
    f.source = op.source
    run.check_source(f)
    return f


def _set_style(run: _Run, op: SetStyle) -> Field:
    f = run.field(op.id)
    f.style = f.style.model_copy(update=op.style.model_dump(exclude_unset=True))
    return f


def _add_table(run: _Run, op: AddTable) -> Field:
    op.id = run.new_id(op.id)
    if not op.segments:
        run.fail(f"table field {op.id!r}: add_table needs at least one segment")
    f = Field(id=op.id, type="table", label=op.label, source=Source(paths=[op.source_path]),
              segments=op.segments, origin=run.author)
    run.check_source(f)
    for i in range(len(f.segments)):
        run.check_segment(f, i)
    run.t.fields.append(f)
    return f


def _set_table_columns(run: _Run, op: SetTableColumns) -> Field:
    f = run.table(op.id)
    run.segment(f, op.segment).columns = op.columns
    run.check_segment(f, op.segment)
    run.check_source(f)
    return f


def _set_table_rows(run: _Run, op: SetTableRows) -> Field:
    f = run.table(op.id)
    seg = run.segment(f, op.segment)
    seg.row_top, seg.row_height, seg.max_rows = op.row_top, op.row_height, op.max_rows
    run.check_segment(f, op.segment)
    return f


def _add_table_segment(run: _Run, op: AddTableSegment) -> Field:
    f = run.table(op.id)
    f.segments.append(op.segment)
    run.check_segment(f, len(f.segments) - 1)
    run.check_source(f)
    return f


def _shared_widget_ok(f: Field, g: Field) -> bool:
    """Whether ``f`` and ``g`` may link the same widget name: kids of one radio group (or of one
    checkbox group) share their parent's name, one field per kid; fill picks the kid by rect/on-state."""
    return f.type == g.type and f.type in _SHARED_WIDGET_TYPES


def _link_widget(run: _Run, op: LinkWidget) -> Field:
    f = run.field(op.id)
    if op.widget is not None:
        names = run.ctx.widget_names
        if names is not None and op.widget not in names:
            run.fail(f"field {f.id!r}: unknown widget {op.widget!r}; widgets on this form: {names or 'none'}")
        for g in run.t.fields:
            if g is not f and g.existing_widget == op.widget and not _shared_widget_ok(f, g):
                run.fail(f"widget {op.widget!r} is already linked to field {g.id!r}; "
                         f"unlink it first with link_widget({g.id!r}, None)")
    f.existing_widget = op.widget
    return f


_HANDLERS: dict[str, Callable[[_Run, OpBase], Field | None]] = {
    "add_field": _add_field,
    "remove_field": _remove_field,
    "move": _move,
    "resize": _resize,
    "set_label": _set_label,
    "set_type": _set_type,
    "set_source": _set_source,
    "set_style": _set_style,
    "add_table": _add_table,
    "set_table_columns": _set_table_columns,
    "set_table_rows": _set_table_rows,
    "add_table_segment": _add_table_segment,
    "link_widget": _link_widget,
}
