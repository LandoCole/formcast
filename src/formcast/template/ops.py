"""Edit ops: the one mutation API shared by human editors and the agent.

Every change to a template is an op (plain data, ``{"op": name, ...}``) passed to
:func:`apply`, which validates it, returns a *new* template (the input is never
mutated) plus warnings, and appends the op to ``history``. A rejected op raises
:class:`~formcast.errors.OpError`; its ``reason`` is shown back to the agent, so
reasons name the field id, the offending value and what would be valid.

* ``move`` takes either ``rect`` or ``dx``/``dy``; ``resize`` sets ``rect``.
* ``set_style`` merges: only the style keys given in the op change.
* ``set_source`` with ``source=None`` unmaps a field; ``link_widget`` with
  ``widget=None`` unlinks it.
* A table field's ``source.paths`` is its one array path; column keys are paths
  relative to a row (``"dose"`` for ``medications[].dose``).
* History records the op as given (only the keys that were set) plus any
  generated id, so :func:`replay` reproduces the template exactly.
"""

from __future__ import annotations

import datetime
import difflib
import math
import re
from collections.abc import Callable
from typing import Annotated, Literal, Union

import pydantic
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from formcast.errors import OpError
from formcast.geometry import Rect, iou
from formcast.schema import SchemaPath
from formcast.template.model import Column, Field, HistoryEntry, Source, Style, TableSegment, Template
from formcast.transforms import Transform, validate_chain

FieldType = Literal["text", "checkbox", "radio_option", "signature", "choice_mark", "table"]
CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")
EDGE_TOLERANCE = 2.0  # how far (pt) a rect may stick out past the page edge
MIN_SIZE = 2.0  # minimum rect width and height (pt)
OVERLAP_WARN_IOU = 0.5
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{0,63}")
_GENERATED_ID = re.compile(r"f(\d+)")


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddField(_Op):
    op: Literal["add_field"] = "add_field"
    id: str | None = None
    type: FieldType
    page: int
    rect: Rect
    label: str = ""
    source: Source | None = None
    style: Style | None = None


class RemoveField(_Op):
    op: Literal["remove_field"] = "remove_field"
    id: str


class Move(_Op):
    op: Literal["move"] = "move"
    id: str
    rect: Rect | None = None
    dx: float | None = None
    dy: float | None = None

    @pydantic.model_validator(mode="after")
    def _rect_or_offset(self) -> Move:
        offset = self.dx is not None or self.dy is not None
        if self.rect is None and not offset:
            raise ValueError("move needs either rect=[x0, y0, x1, y1] or a dx/dy offset")
        if self.rect is not None and offset:
            raise ValueError("move takes rect or dx/dy, not both")
        return self


class Resize(_Op):
    op: Literal["resize"] = "resize"
    id: str
    rect: Rect


class SetLabel(_Op):
    op: Literal["set_label"] = "set_label"
    id: str
    label: str


class SetType(_Op):
    op: Literal["set_type"] = "set_type"
    id: str
    type: FieldType


class SetSource(_Op):
    op: Literal["set_source"] = "set_source"
    id: str
    source: Source | None


class SetStyle(_Op):
    op: Literal["set_style"] = "set_style"
    id: str
    style: Style


class AddTable(_Op):
    op: Literal["add_table"] = "add_table"
    id: str | None = None
    label: str = ""
    source_path: str
    segments: list[TableSegment]


class SetTableColumns(_Op):
    op: Literal["set_table_columns"] = "set_table_columns"
    id: str
    segment: int
    columns: list[Column]


class SetTableRows(_Op):
    op: Literal["set_table_rows"] = "set_table_rows"
    id: str
    segment: int
    row_top: float
    row_height: float
    max_rows: int


class AddTableSegment(_Op):
    op: Literal["add_table_segment"] = "add_table_segment"
    id: str
    segment: TableSegment


class LinkWidget(_Op):
    op: Literal["link_widget"] = "link_widget"
    id: str
    widget: str | None


_OP_CLASSES = (
    AddField, RemoveField, Move, Resize, SetLabel, SetType, SetSource, SetStyle,
    AddTable, SetTableColumns, SetTableRows, AddTableSegment, LinkWidget,
)  # fmt: skip
Op = Annotated[Union[_OP_CLASSES], pydantic.Field(discriminator="op")]  # noqa: UP007
OP_NAMES = [c.model_fields["op"].default for c in _OP_CLASSES]
_ADAPTER: TypeAdapter = TypeAdapter(Op)
# Ops after which the touched field is checked for overlap / checkbox-source warnings.
_WARN_OPS = {"add_field", "move", "resize", "set_type", "set_source"}


class OpContext(BaseModel):
    """What ops are validated against. ``widget_names``, when given, limits ``link_widget``."""

    schema_paths: list[SchemaPath]
    page_sizes: list[tuple[float, float]]
    widget_names: list[str] | None = None


# ------------------------------------------------------------------ public API


def apply(
    template: Template, op: Op | dict, *, author: str, note: str = "", context: OpContext
) -> tuple[Template, list[str]]:
    """Validate and apply ``op``; return the new template and any warnings."""
    return _apply(template, op, author=author, note=note, context=context, at=None)


def replay(base: Template, history: list[HistoryEntry], context: OpContext) -> Template:
    """Re-apply ``history`` on ``base``, keeping each entry's author, note and timestamp."""
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


def _parse(op: object) -> tuple[_Op, dict]:
    if isinstance(op, _OP_CLASSES):
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


def _fmt(rect: Rect) -> str:
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
        ids = [f.id for f in self.t.fields]
        listed = ", ".join(ids[:30]) + (", ..." if len(ids) > 30 else "") if ids else "none"
        self.fail(f"unknown field id {fid!r}; existing field ids: {listed}")

    def placed(self, fid: str) -> Field:
        f = self.field(fid)
        if f.type == "table":
            self.fail(
                f"field {fid!r} is a table; tables are positioned by their segments "
                "(set_table_rows, set_table_columns, add_table_segment), not move/resize"
            )
        return f

    def table(self, fid: str) -> Field:
        f = self.field(fid)
        if f.type != "table":
            self.fail(f"field {fid!r} is a {f.type} field, not a table; table ops need a field made by add_table")
        return f

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
                self.fail(f"{where}: column {col.key!r} x {[cx0, cx1]} must be at least {MIN_SIZE:g}pt wide "
                          f"and inside the segment rect's x range [{x0:g}, {x1:g}]")
            self.check_chain(f"{where} column {col.key!r} transforms", col.transforms)

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
                self.fail(f"field {f.id!r}: schema path {sp.path!r} is a column of table {sp.in_table!r}; map it as a "
                          f"column key in add_table/set_table_columns for {sp.in_table!r}")

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

    def warnings(self, f: Field) -> list[str]:
        out = []
        if f.rect is not None:
            for g in self.t.fields:
                if g is not f and g.page == f.page and g.rect is not None:
                    v = iou(f.rect, g.rect)
                    if v > OVERLAP_WARN_IOU:
                        out.append(f"field {f.id!r} overlaps field {g.id!r} on page {f.page} (IoU {v:.2f}); "
                                   "if they are the same blank, remove one")
        s = f.source
        if f.type == "checkbox" and s and s.when is None and not any(tr.op == "bool_to_check" for tr in s.transforms):
            for p in s.paths:
                sp = self.paths[p]
                if sp.type not in ("boolean", "enum", "array"):
                    out.append(f"checkbox field {f.id!r} maps {p!r} (type {sp.type}) with no when condition or "
                               "bool_to_check transform; add when (e.g. equals/contains) or bool_to_check "
                               "so the box state is explicit")
        return out


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
    run.t.fields.remove(run.field(op.id))


def _move(run: _Run, op: Move) -> Field:
    f = run.placed(op.id)
    if op.rect is not None:
        rect = op.rect
    else:
        dx, dy = op.dx or 0.0, op.dy or 0.0
        rect = (f.rect[0] + dx, f.rect[1] + dy, f.rect[2] + dx, f.rect[3] + dy)
    run.check_rect(f.id, f.page, rect)
    f.rect = rect
    return f


def _resize(run: _Run, op: Resize) -> Field:
    f = run.placed(op.id)
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


def _link_widget(run: _Run, op: LinkWidget) -> Field:
    f = run.field(op.id)
    if op.widget is not None:
        names = run.ctx.widget_names
        if names is not None and op.widget not in names:
            run.fail(f"field {f.id!r}: unknown widget {op.widget!r}; widgets on this form: {names or 'none'}")
        for g in run.t.fields:
            if g is not f and g.existing_widget == op.widget:
                run.fail(f"widget {op.widget!r} is already linked to field {g.id!r}; "
                         f"unlink it first with link_widget({g.id!r}, None)")
    f.existing_widget = op.widget
    return f


_HANDLERS: dict[str, Callable[[_Run, _Op], Field | None]] = {
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
