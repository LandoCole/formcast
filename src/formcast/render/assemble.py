"""Assemble a :class:`FillReport` from what ``fill`` resolved, bound and wrote (fill design §5.7).

Split from :mod:`.fill`, which owns the pipeline and the writing; this module owns the per-target
bookkeeping (:class:`Slot`) and the report built from it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from formcast.geometry import Rect
from formcast.render.binding import Binding, Target
from formcast.render.report import CellReport, FieldReport, FillReport, FillWarning, PathReport, TableReport
from formcast.render.values import CellTarget, Resolved, TableResolved, _has_value
from formcast.schema import ARRAY_ITEM, flatten_schema, format_path, parse_path
from formcast.template.model import Column, Field, Template

__all__ = ["Slot", "build_report", "column_path", "order_key"]

CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")


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


def column_path(table_path: str, key: str) -> str:
    """Schema path of a table column: ``medications`` + ``dose`` -> ``medications[].dose``."""
    try:
        return format_path(parse_path(table_path) + [ARRAY_ITEM] + parse_path(key))
    except ValueError:
        return f"{table_path}[].{key}"


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
        cell_reports.append(CellReport(
            row=s.row, key=s.column.key, segment=s.segment, page=s.target.page, rect=s.target.rect,
            binding=s.binding.how, widget=name, widget_rect=rect, value=s.value, font_size=s.font_size,
            warnings=(list(c.warnings) if c is not None else []) + s.warnings,
        ))  # fmt: skip
    per_segment = list(tr.rows_per_segment) if tr is not None else [0] * len(f.segments)
    return TableReport(
        id=f.id, path=tr.path if tr is not None else "", rows_provided=tr.rows_provided if tr is not None else 0,
        rows_written=sum(per_segment), rows_dropped=tr.rows_dropped if tr is not None else 0,
        rows_per_segment=per_segment, cells=cell_reports, warnings=list(tr.warnings) if tr is not None else [],
    )  # fmt: skip


def _is_gap(f: Field, slots: list[Slot], r: Resolved | None) -> bool:
    """A box that came out empty: no source, or mapped but nothing visible was written."""
    if not slots[0].mapped:
        return True
    if f.type == "table":
        return not any(s.value for s in slots)
    s = slots[0]
    if s.choice:
        return r is None or not r.has_value or not s.written
    return not s.value


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
                rendered[cp][f"{f.id}[{s.row}]"] = s.value
    by_path: dict[str, list[FillWarning]] = defaultdict(list)
    for w in warnings:
        if w.path is not None:
            by_path[w.path].append(w)
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
    every += [w for tr in table_reports for w in tr.warnings + [cw for c in tr.cells for cw in c.warnings]]
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
