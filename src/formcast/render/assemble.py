"""Assemble a :class:`FillReport` from what ``fill`` resolved, bound and wrote (fill design §5.7).

Split from :mod:`.fill`, which owns the pipeline and the writing; this module owns the per-target
bookkeeping (:class:`Slot`) and the report built from it, including the post-write check that every
record value shows somewhere or is explained by a warning (``not_shown``, Ruling V).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from formcast.errors import FormcastError
from formcast.geometry import Rect
from formcast.render.binding import Binding, Target
from formcast.render.report import CellReport, FieldReport, FillReport, FillWarning, PathReport, TableReport
from formcast.render.values import (
    CellTarget,
    Resolved,
    TableResolved,
    _cell_value,
    _has_value,
    _row_problem,
    choice_on,
    is_missing,
    lookup,
)
from formcast.schema import ARRAY_ITEM, SchemaPath, flatten_schema, format_path, is_array_item, parse_path
from formcast.template.model import Column, Field, Template

__all__ = ["Slot", "build_report", "column_path", "order_key"]

CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")

#: Codes that already say why a record value isn't visible; ``not_shown`` adds nothing to them.
EXPLAINS_HIDDEN = frozenset({
    "unmapped_data", "missing", "transform_error", "widget_not_found", "type_mismatch", "not_writable",
    "shared_field_conflict", "rows_dropped", "invalid",
})  # fmt: skip


@dataclass
class Slot:
    """One bound target (a field, or one table-cell slot) and what was written to it."""

    target: Target
    field: Field  # the template field; the table field for a cell
    mapped: bool
    path: str | None  # data path for warnings: a single source path, or the cell's column path
    column: Column | None = None
    segment: int | None = None
    row: int | None = None
    list_row: bool = False  # a cell of a list-row table: its column path is the table's item path
    order: tuple = ()  # page, top, left, input position
    binding: Binding | None = None
    written: bool = False
    value: str | bool | None = None  # what the widget shows: text (None when empty) or on/off
    font_size: float | None = None
    widget_name: str | None = None
    widget_rect: Rect | None = None
    warnings: list[FillWarning] = field(default_factory=list)
    conflicts_with: set[str] = field(default_factory=set)

    @property
    def choice(self) -> bool:
        return self.field.type in CHOICE_TYPES

    def done(self, name: str, rect: Rect, value, font_size: float | None, warnings: list[FillWarning]) -> None:
        self.written = True
        self.widget_name, self.widget_rect = name, rect
        self.value, self.font_size = value, font_size
        self.warnings.extend(warnings)

    def widget(self) -> tuple[str | None, Rect | None]:
        """The widget's name and rect: as written, else the bound existing widget's."""
        if self.written:
            return self.widget_name, self.widget_rect
        w = self.binding.widget if self.binding is not None else None
        return (w.name, w.rect) if w is not None else (None, None)


def order_key(target: Target, index: int) -> tuple:
    return (target.page, target.rect[1], target.rect[0], index)


def column_path(table_path: str, key: str, *, list_rows: bool = False) -> str:
    """Schema path of a table column: ``medications`` + ``dose`` -> ``medications[].dose``.

    Columns of a list-row table (rows are arrays, keys are indices) all live at the item path
    ``rows[]``, which is what ``flatten_schema`` lists.
    """
    try:
        return format_path(parse_path(table_path) + [ARRAY_ITEM] + ([] if list_rows else parse_path(key)))
    except ValueError:
        return f"{table_path}[]" if list_rows else f"{table_path}[].{key}"


def _sort_key(w: FillWarning) -> tuple:
    """Page, then top, then left; record-level warnings (no page) first."""
    return (-1 if w.page is None else w.page, w.rect[1] if w.rect else -1.0, w.rect[0] if w.rect else -1.0)


def _top_level(warnings: list[FillWarning]) -> list[FillWarning]:
    """Every warning sorted by position, with ``missing`` kept once per path."""
    seen: set[str] = set()
    out: list[FillWarning] = []
    for w in sorted(warnings, key=_sort_key):
        if w.code == "missing" and w.path is not None:
            if w.path in seen:
                continue
            seen.add(w.path)
        out.append(w)
    return out


def _field_report(f: Field, s: Slot, r: Resolved | None) -> FieldReport:
    name, rect = s.widget()
    return FieldReport(
        id=f.id, type=f.type, page=f.page, paths=list(f.source.paths) if s.mapped else [],
        binding=s.binding.how, widget=name, widget_rect=rect, value=s.value, font_size=s.font_size,
        warnings=(list(r.warnings) if r is not None else []) + s.warnings,
    )  # fmt: skip


def _table_report(f: Field, slots: list[Slot], tr: TableResolved | None, cells: dict) -> TableReport:
    cell_reports = []
    for s in slots:
        name, rect = s.widget()
        c: CellTarget | None = cells.get((f.id, s.row, s.column.key))
        # resolve() words a cell's path as "<table>[].<key>"; use the schema path the slot carries
        resolved = [w if w.path == s.path else w.model_copy(update={"path": s.path}) for w in (c.warnings if c else [])]
        cell_reports.append(CellReport(
            row=s.row, key=s.column.key, segment=s.segment, page=s.target.page, rect=s.target.rect,
            binding=s.binding.how, widget=name, widget_rect=rect, value=s.value, font_size=s.font_size,
            warnings=resolved + s.warnings,
        ))  # fmt: skip
    per_segment = list(tr.rows_per_segment) if tr is not None else [0] * len(f.segments)
    return TableReport(
        id=f.id, path=tr.path if tr is not None else "", rows_provided=tr.rows_provided if tr is not None else 0,
        rows_written=sum(per_segment), rows_dropped=tr.rows_dropped if tr is not None else 0,
        rows_per_segment=per_segment, cells=cell_reports, warnings=list(tr.warnings) if tr is not None else [],
    )  # fmt: skip


def _is_gap(f: Field, slots: list[Slot], r: Resolved | None) -> bool:
    """A box that came out empty: no source, or mapped but nothing visible was written."""
    if f.source is None or not f.source.paths or not slots:
        return True  # a table with no cell slots (no columns, or max_rows 0) writes nothing
    if f.type == "table":
        return not any(s.value for s in slots)
    s = slots[0]
    if s.choice:
        return r is None or not r.has_value or not s.written
    return not s.value


def _multi_paths(template: Template) -> dict[str, list[str]]:
    """A field joining several paths words its warnings without a path; they belong to each one."""
    return {f.id: list(dict.fromkeys(f.source.paths)) for f in template.fields
            if f.type != "table" and f.source is not None and len(f.source.paths) > 1}  # fmt: skip


def _warning_paths(w: FillWarning, multi: dict[str, list[str]]) -> list[str]:
    return [w.path] if w.path is not None else multi.get(w.field or "", [])


def _paths(template: Template, data: Any, slots_by_field: dict[str, list[Slot]], cells: dict,
           warnings: list[FillWarning]) -> dict[str, PathReport]:  # fmt: skip
    fields_at: dict[str, list[str]] = defaultdict(list)
    rendered: dict[str, dict[str, str | bool | None]] = defaultdict(dict)
    for f in template.fields:
        slots = slots_by_field[f.id]
        if not slots or not slots[0].mapped:
            continue
        if f.type != "table":
            for p in dict.fromkeys(f.source.paths):
                fields_at[p].append(f.id)
                rendered[p][f.id] = slots[0].value
            continue
        fields_at[f.source.paths[0]].append(f.id)
        for s in slots:
            cp = s.path
            if f.id not in fields_at[cp]:
                fields_at[cp].append(f.id)
            if (f.id, s.row, s.column.key) in cells:
                key = f"{f.id}[{s.row}][{s.column.key}]" if s.list_row else f"{f.id}[{s.row}]"
                rendered[cp][key] = s.value
    multi = _multi_paths(template)
    by_path: dict[str, list[FillWarning]] = defaultdict(list)
    for w in warnings:
        for p in _warning_paths(w, multi):
            by_path[p].append(w)
    return {
        sp.path: PathReport(path=sp.path, mapped=sp.path in fields_at, has_value=_has_value(data, sp.path),
                            fields=list(fields_at.get(sp.path, [])), rendered=dict(rendered.get(sp.path, {})),
                            warnings=by_path.get(sp.path, []))  # fmt: skip
        for sp in flatten_schema(template.schema_)
    }


def build_report(
    template: Template,
    data: Any,
    *,
    strict: bool,
    fingerprint_ok: bool,
    head: list[FillWarning],
    record: list[FillWarning],
    slots: list[Slot],
    fields: dict[str, Resolved],
    tables: dict[str, TableResolved],
    cells: dict[tuple, CellTarget],
) -> FillReport:
    slots_by_field: dict[str, list[Slot]] = defaultdict(list)
    for s in slots:
        slots_by_field[s.field.id].append(s)
    field_reports: list[FieldReport] = []
    table_reports: list[TableReport] = []
    gaps: list[str] = []
    for f in template.fields:
        fs = slots_by_field[f.id]
        if f.type == "table":
            table_reports.append(_table_report(f, fs, tables.get(f.id), cells))
        else:
            field_reports.append(_field_report(f, fs[0], fields.get(f.id)))
        if _is_gap(f, fs, fields.get(f.id)):
            gaps.append(f.id)

    every = head + record + [w for fr in field_reports for w in fr.warnings]
    every += [w for tr in table_reports for w in tr.warnings]
    every += _not_shown(template, data, slots_by_field, table_reports, every)
    every += [cw for tr in table_reports for c in tr.cells for cw in c.warnings]
    every = sorted(every, key=_sort_key)
    top = _top_level(every)

    written = sum(1 for fr in field_reports if fr.value)
    counts = Counter(w.code for w in top)
    counts.update({
        "fields_written": written,
        "fields_empty": len(field_reports) - written,
        "cells_written": sum(1 for tr in table_reports for c in tr.cells if c.value),
    })  # fmt: skip
    return FillReport(
        template_id=template.template_id, fingerprint_ok=fingerprint_ok, strict=strict,
        fields=field_reports, tables=table_reports,
        paths=_paths(template, data, slots_by_field, cells, every),
        gaps=gaps, warnings=top, counts=dict(counts),
    )  # fmt: skip


# ---------------------------------------------------------------- Ruling V: nothing visible


def _not_shown(template: Template, data: Any, slots_by_field: dict[str, list[Slot]],
               table_reports: list[TableReport], warnings: list[FillWarning]) -> list[FillWarning]:  # fmt: skip
    """``not_shown`` for every record value no written widget ended up showing, unless a warning
    already explains it (:data:`EXPLAINS_HIDDEN`). Falsy values (``False``, ``0``) are exempt: an
    unticked box shows them. A table cell's warning also goes on its :class:`CellReport`.

    ``warnings`` are the record-, field- and table-level ones; a cell is explained by its own.
    """
    if not isinstance(data, dict):
        return []
    multi = _multi_paths(template)
    explained = {p for w in warnings if w.code in EXPLAINS_HIDDEN for p in _warning_paths(w, multi)}
    schema_paths = flatten_schema(template.schema_)
    by_path = {sp.path: sp for sp in schema_paths}
    mapped = [f for f in template.fields if f.source is not None and f.source.paths]
    tables = {f.source.paths[0]: f for f in mapped if f.type == "table"}
    plain = [f for f in mapped if f.type != "table"]
    out: list[FillWarning] = []
    for sp in schema_paths:
        segs = parse_path(sp.path)
        if sp.type == "object" or sp.in_table is not None or is_array_item(segs[-1]) or sp.path in tables:
            continue
        if sp.path in explained:
            continue
        value = lookup(data, sp.path)
        if is_missing(value) or not value:
            continue
        for item, in_list in _hidden(sp.path, segs, value, plain, slots_by_field, data):
            what = f"{item!r} in {sp.path!r}" if in_list else f"{item!r} at {sp.path!r}"
            out.append(FillWarning.make(
                "not_shown", f"the record has {what} but no box on the form shows it", path=sp.path))  # fmt: skip
    reports = {tr.id: tr for tr in table_reports}
    for path, f in tables.items():
        out += _hidden_cells(f, path, by_path.get(path), data, slots_by_field[f.id], reports[f.id], explained)
    return out


def _is_prefix(pre: str, segs: list[str]) -> bool:
    try:
        p = parse_path(pre)
    except ValueError:
        return False
    return segs[: len(p)] == p


def _hidden(path: str, segs: list[str], value: Any, plain: list[Field], slots_by_field: dict[str, list[Slot]],
            data: dict) -> list[tuple[Any, bool]]:  # fmt: skip
    """The parts of ``value`` (itself, or each list item) no written widget shows."""
    covering = [f for f in plain if any(_is_prefix(q, segs) for q in f.source.paths)]
    if any(f.type not in CHOICE_TYPES and slots_by_field[f.id][0].value for f in covering):
        return []  # its text was written into some widget
    boxes_on = [f for f in covering if f.type in CHOICE_TYPES and slots_by_field[f.id][0].value is True]
    if not isinstance(value, (list, tuple)):
        return [] if boxes_on else [(value, False)]
    hidden = []
    for item in value:
        if is_missing(item) or not item:
            continue
        if not any(_represents(f, path, item, data) for f in boxes_on):
            hidden.append((item, True))
    return hidden


def _represents(f: Field, path: str, item: Any, data: dict) -> bool:
    """Whether box ``f`` would be on if the list at ``path`` held only ``item`` (it stands for it)."""
    if path not in f.source.paths:
        return True  # mapped through a parent path: an "on" box shows the whole value
    values = [[item] if q == path else (None if is_missing(v := lookup(data, q)) else v) for q in f.source.paths]
    try:
        return choice_on(f.source, values)
    except FormcastError:
        return False


def _hidden_cells(f: Field, path: str, sp: SchemaPath | None, data: dict, slots: list[Slot], tr: TableReport,
                  explained: set[str]) -> list[FillWarning]:  # fmt: skip
    """``not_shown`` per written row's value that its cell doesn't show (dropped rows: ``rows_dropped``)."""
    rows = lookup(data, path)
    if not isinstance(rows, (list, tuple)):
        return []  # missing or invalid, already reported
    item_type = sp.item_type if sp is not None else None
    by_cell = {(s.row, s.column.key): (s, c) for s, c in zip(slots, tr.cells)}
    keys = list(dict.fromkeys(c.key for seg in f.segments for c in seg.columns))
    out: list[FillWarning] = []
    for i in range(min(tr.rows_written, len(rows))):
        row = rows[i]
        if _row_problem(row, item_type) is not None:
            continue  # reported as invalid; its cells are left empty
        if isinstance(row, dict):
            entries = [(k, _cell_value(row, k)) for k in keys]
        else:
            index_keys = {}
            for k in keys:
                try:
                    index_keys.setdefault(int(k), k)
                except ValueError:
                    pass
            entries = [(index_keys.get(j), v) for j, v in enumerate(row)]
        for key, value in entries:
            if is_missing(value) or not value:
                continue
            cp = column_path(path, key if key is not None else "", list_rows=not isinstance(row, dict))
            slot, cell = by_cell.get((i, key), (None, None))
            if cp in explained or (slot is not None and slot.value):
                continue
            if cell is not None and any(w.code in EXPLAINS_HIDDEN for w in cell.warnings):
                continue
            where = f"column {key!r}" if key is not None else "a position no column covers"
            w = FillWarning.make(
                "not_shown", f"{path} row {i}: the record has {value!r} in {where} but its cell shows nothing",
                path=cp, field=f.id, page=cell.page if cell else None, rect=cell.rect if cell else None,
            )  # fmt: skip
            if cell is not None:
                cell.warnings.append(w)
            else:
                out.append(w)
    return out
