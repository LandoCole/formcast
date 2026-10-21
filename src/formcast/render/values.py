"""Resolve what each template field should say, from one data record. No PDF involved.

``resolve`` turns ``(template, record)`` into per-field :class:`Resolved` values, per-table
:class:`TableResolved` cell targets, and record-level warnings. It never raises on bad
*data* (that is reported); a bad template schema still raises ``FormcastError``.

Missing means: absent key, ``None``, ``""``, a whitespace-only string, or an empty list.
``0`` and ``False`` are values.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from formcast.errors import FormcastError
from formcast.geometry import Rect
from formcast.render.report import FillWarning
from formcast.schema import (
    ARRAY_ITEM,
    SchemaPath,
    data_location,
    flatten_schema,
    format_path,
    is_array_item,
    parse_path,
    validate_data_located,
)
from formcast.template.model import Field, Source, Template
from formcast.transforms import apply_chain, bool_to_check, to_text

__all__ = [
    "CellTarget",
    "Resolved",
    "TableResolved",
    "choice_on",
    "is_missing",
    "lookup",
    "resolve",
    "schema_path_of",
]

CHOICE_TYPES = ("checkbox", "radio_option", "choice_mark")


def is_missing(v: Any) -> bool:
    """True for ``None``, a blank (empty or whitespace-only) string, or an empty list."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple)):
        return len(v) == 0
    return False


def lookup(data: Any, path: str) -> Any:
    """Follow ``path`` through dicts; ``None`` for anything absent. ``[]`` paths aren't looked up here."""
    try:
        segments = parse_path(path)
    except ValueError:
        return None
    cur = data
    for seg in segments:
        if is_array_item(seg) or not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


_INDEX_OR_QUOTED = re.compile(r'\["(?:[^"\\]|\\.)*"\]|\[(\d+)\]')


def schema_path_of(location: str) -> str:
    """``"medications[2].dose"`` -> ``"medications[].dose"`` (indices become ``[]``, quoted names stay)."""
    return _INDEX_OR_QUOTED.sub(lambda m: "[]" if m.group(1) is not None else m.group(0), location)


@dataclass
class Resolved:
    field_id: str
    kind: Literal["text", "choice"]
    paths: list[str]
    #: at least one source value is not missing
    has_value: bool
    text: str | None
    on: bool | None
    warnings: list[FillWarning] = field(default_factory=list)


@dataclass
class CellTarget:
    table_id: str
    segment: int
    row: int
    key: str
    page: int
    rect: Rect
    text: str | None
    warnings: list[FillWarning] = field(default_factory=list)


@dataclass
class TableResolved:
    field_id: str
    path: str
    rows_provided: int
    cells: list[CellTarget]
    rows_per_segment: list[int]
    rows_dropped: int
    warnings: list[FillWarning] = field(default_factory=list)


# ---------------------------------------------------------------- text values


def _render(result: Any, join: str | None = None) -> tuple[str | None, bool]:
    """Final text for a transformed value, and whether a list was implicitly joined.

    Raises ``FormcastError`` if an item can't be rendered as text.
    """
    if isinstance(result, (list, tuple)):
        items = [to_text(x) for x in result if not is_missing(x)]
        if not items:
            return None, False
        if join is not None:
            return join.join(items), False
        return ", ".join(items), True
    if is_missing(result):
        return None, False
    return to_text(result), False


def _source_values(f: Field, data: dict) -> list[Any]:
    assert f.source is not None
    return [None if is_missing(v := lookup(data, p)) else v for p in f.source.paths]


def _resolve_text(f: Field, data: dict) -> Resolved:
    assert f.source is not None
    src = f.source
    values = _source_values(f, data)
    res = Resolved(f.id, "text", list(src.paths), any(v is not None for v in values), None, None)
    where = {"field": f.id, "page": f.page, "rect": f.rect, "path": src.paths[0] if len(src.paths) == 1 else None}
    try:
        result = apply_chain(values, src.transforms)
        text, joined = _render(result, src.join)
    except FormcastError as e:
        res.warnings.append(FillWarning.make("transform_error", str(e), **where))
        return res
    if text is None:
        why = "the transforms left no value for" if res.has_value else "no value for"
        res.warnings.append(FillWarning.make("missing", f"{why} {', '.join(src.paths)}", **where))
    elif joined:
        res.warnings.append(FillWarning.make("list_joined", f"list value joined with ', ' into {text!r}", **where))
    res.text = text
    return res


def choice_on(src: Source, values: list[Any]) -> bool:
    """Whether a choice box with source ``src`` is on for these source ``values``.

    Runs the transforms, then ``when`` (or ``bool_to_check`` without one). Raises ``FormcastError``
    when a transform rejects a value.
    """
    x = apply_chain(values, src.transforms)
    if src.when is not None:
        return bool(apply_chain([x], [src.when]))
    return bool_to_check(x)


def _resolve_choice(f: Field, data: dict) -> Resolved:
    assert f.source is not None
    src = f.source
    values = _source_values(f, data)
    has_value = any(v is not None for v in values)
    res = Resolved(f.id, "choice", list(src.paths), has_value, None, False)
    where = {"field": f.id, "page": f.page, "rect": f.rect, "path": src.paths[0] if len(src.paths) == 1 else None}
    if not has_value:
        res.warnings.append(FillWarning.make("missing", f"no value for {', '.join(src.paths)}", **where))
        return res
    try:
        res.on = choice_on(src, values)
    except FormcastError as e:
        res.on = False
        res.warnings.append(FillWarning.make("transform_error", str(e), **where))
    return res


# --------------------------------------------------------------------- tables


def _key_segments(key: str) -> list[str]:
    """A column key is a row-relative schema path (``dose``, ``sched.time``, ``["dose mg"]``)."""
    try:
        return parse_path(key)
    except ValueError:
        return [key]


def _cell_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        cur: Any = row
        for seg in _key_segments(key):
            if is_array_item(seg) or not isinstance(cur, dict):
                return None
            cur = cur.get(seg)
        return cur
    try:
        return row[int(key)]
    except (ValueError, IndexError, TypeError):
        return None


def _row_problem(row: Any, item_type: str | None) -> str | None:
    """Why ``row`` can't supply cells, or ``None`` when it can."""
    if row is None:
        return "row is null"
    if isinstance(row, dict):
        return None if item_type in (None, "object") else f"row is an object but the schema expects {item_type}"
    if isinstance(row, (list, tuple)):
        return None if item_type in (None, "array") else f"row is a list but the schema expects {item_type}"
    return f"row is {type(row).__name__} {row!r}, expected " + ("an object" if item_type == "object" else "a row")


def _resolve_table(f: Field, data: dict, by_path: dict[str, SchemaPath]) -> TableResolved:
    assert f.source is not None
    path = f.source.paths[0]
    item_type = by_path[path].item_type if path in by_path else None
    out = TableResolved(f.id, path, 0, [], [0] * len(f.segments), 0)
    where = {"field": f.id, "path": path, "page": f.segments[0].page}
    rows = lookup(data, path)
    if is_missing(rows):
        out.warnings.append(FillWarning.make("missing", f"no rows for {path}", **where))
        return out
    if not isinstance(rows, (list, tuple)):
        out.warnings.append(
            FillWarning.make("invalid", f"{path} should be a list of rows but is {type(rows).__name__}", **where)
        )
        return out
    out.rows_provided = len(rows)

    cursor = 0  # next row to place
    for k, seg in enumerate(f.segments):
        take = max(0, min(seg.max_rows, len(rows) - cursor))
        out.rows_per_segment[k] = take
        for r in range(take):
            index = cursor + r
            row = rows[index]
            y = seg.row_top + r * seg.row_height
            problem = _row_problem(row, item_type)
            if problem is not None:
                out.warnings.append(
                    FillWarning.make("invalid", f"{path} row {index}: {problem}; its cells are left empty",
                                     **{**where, "page": seg.page, "rect": (seg.rect[0], y, seg.rect[2], y + seg.row_height)})  # fmt: skip
                )
            for col in seg.columns:
                rect: Rect = (col.x[0], y, col.x[1], y + seg.row_height)
                cell = CellTarget(f.id, k, index, col.key, seg.page, rect, None)
                out.cells.append(cell)
                if problem is not None:
                    continue
                value = _cell_value(row, col.key)
                cwhere = {"field": f.id, "path": f"{path}[].{col.key}", "page": seg.page, "rect": rect}
                try:
                    result = apply_chain([None if is_missing(value) else value], col.transforms)
                    cell.text, joined = _render(result)
                except FormcastError as e:
                    cell.warnings.append(FillWarning.make("transform_error", str(e), **cwhere))
                    continue
                if joined:
                    cell.warnings.append(
                        FillWarning.make("list_joined", f"list value joined with ', ' into {cell.text!r}", **cwhere)
                    )
        cursor += take
    out.rows_dropped = len(rows) - cursor
    if out.rows_dropped:
        last = f.segments[-1]
        out.warnings.append(
            FillWarning.make(
                "rows_dropped",
                f"{out.rows_dropped} of {len(rows)} rows don't fit the table's {len(f.segments)} segment(s) "
                f"and were dropped",
                **{**where, "page": last.page},
            )
        )
    return out


# --------------------------------------------------------------- record level


def _values_at(node: Any, segments: list[str]) -> Iterator[Any]:
    """Every value reachable at ``segments`` (``[]`` fans out over list items)."""
    if not segments:
        yield node
        return
    head, rest = segments[0], segments[1:]
    if is_array_item(head):
        if isinstance(node, (list, tuple)):
            for item in node:
                yield from _values_at(item, rest)
    elif isinstance(node, dict) and head in node:
        yield from _values_at(node[head], rest)


def _has_value(data: Any, path: str) -> bool:
    return any(not is_missing(v) for v in _values_at(data, parse_path(path)))


def _covered(fields: list[Field], by_path: dict[str, SchemaPath]) -> tuple[set[str], list[list[str]], set[str]]:
    """Schema paths the template maps: exact paths, covering prefixes, and mapped table paths."""
    exact: set[str] = set()
    prefixes: list[list[str]] = []
    tables: set[str] = set()
    for f in fields:
        if f.source is None or not f.source.paths:
            continue
        if f.type != "table":
            for p in f.source.paths:
                exact.add(p)
                try:
                    prefixes.append(parse_path(p))
                except ValueError:
                    pass
            continue
        p = f.source.paths[0]
        exact.add(p)
        tables.add(p)
        try:
            segs = parse_path(p)
        except ValueError:
            continue
        if p in by_path and by_path[p].item_type != "object":
            prefixes.append(segs)  # list-of-list / list-of-scalar rows: the whole subtree is the table's
        else:
            prefixes.extend(segs + [ARRAY_ITEM, *_key_segments(c.key)] for seg in f.segments for c in seg.columns)
    return exact, prefixes, tables


def _unmapped(template: Template, data: dict, schema_paths: list[SchemaPath]) -> list[FillWarning]:
    by_path = {sp.path: sp for sp in schema_paths}
    exact, prefixes, mapped_tables = _covered(template.fields, by_path)
    out: list[FillWarning] = []
    for sp in schema_paths:
        if sp.type == "object" or (sp.in_table is not None and sp.in_table not in mapped_tables):
            continue
        segs = parse_path(sp.path)
        if is_array_item(segs[-1]):
            continue
        if sp.path in exact or any(segs[: len(pre)] == pre for pre in prefixes):
            continue
        if _has_value(data, sp.path):
            out.append(
                FillWarning.make("unmapped_data", f"the record has a value at {sp.path!r} but no template field maps it",
                                 path=sp.path)  # fmt: skip
            )
    return out


def _unknown_keys(data: Any, known: set[str], forbidding: set[str]) -> list[str]:
    """Schema-style paths of record keys the schema doesn't declare (skipping objects that forbid extras)."""
    found: list[str] = []

    def walk(node: Any, segs: list[str], loc: list[Any]) -> None:
        if isinstance(node, dict):
            skip = data_location(loc) in forbidding
            for key, value in node.items():
                child = segs + [str(key)]
                if format_path(child) in known:
                    walk(value, child, loc + [str(key)])
                elif not skip:
                    name = format_path(child)
                    if name not in found:
                        found.append(name)
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                walk(item, segs + [ARRAY_ITEM], loc + [i])

    walk(data, [], [])
    return found


def resolve(
    template: Template, data: dict
) -> tuple[dict[str, Resolved], dict[str, TableResolved], list[FillWarning]]:
    """Resolve every mapped field of ``template`` against ``data``.

    Returns ``(fields, tables, record_warnings)``. Unmapped template fields get no entry.
    Record-level warnings: ``invalid`` per validation error (at its schema path),
    ``unmapped_data`` per schema path with a value but no mapped field, and one
    ``unknown_keys`` note for record keys the schema doesn't declare.
    """
    schema_paths = flatten_schema(template.schema_)
    by_path = {sp.path: sp for sp in schema_paths}
    fields: dict[str, Resolved] = {}
    tables: dict[str, TableResolved] = {}
    for f in template.fields:
        if f.source is None or not f.source.paths:
            continue
        if f.type == "table":
            tables[f.id] = _resolve_table(f, data, by_path)
        elif f.type in CHOICE_TYPES:
            fields[f.id] = _resolve_choice(f, data)
        else:
            fields[f.id] = _resolve_text(f, data)

    record: list[FillWarning] = []
    located = validate_data_located(template.schema_, data)
    for loc, msg in located:
        path = schema_path_of(loc) or None
        where = f" at {loc}" if loc else ""
        record.append(FillWarning.make("invalid", f"invalid data{where}: {msg}", path=path))
    if isinstance(data, dict):
        record.extend(_unmapped(template, data, schema_paths))
        forbidding = {loc for loc, msg in located if msg.startswith("Additional properties")}
        unknown = _unknown_keys(data, set(by_path), forbidding)
        if unknown:
            record.append(
                FillWarning.make("unknown_keys", f"keys not in the schema were ignored: {', '.join(unknown)}")
            )
    return fields, tables, record
