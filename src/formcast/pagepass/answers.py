"""Turn a page-pass answer (JSON text from the model) into validated edit ops."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from formcast.errors import OpError
from formcast.pagepass.marks import marks_for
from formcast.template.model import Template
from formcast.template.ops import OpContext, apply

RECT_PAD = 1.0  # points added around a table segment's linked boxes
MIN_COLUMN_WIDTH = 4.0


@dataclass
class TableSpec:
    """One table the model found on one page: its rows name the page's boxes."""

    page: int
    source_path: str
    transforms: dict[str, list]  # column key -> transforms
    rows: list[dict[str, str]]  # column key -> box id, one dict per printed row


def _list(v: object) -> list:
    """``v`` if it is a list, else an empty list: the model's JSON values can be of any type."""
    return v if isinstance(v, list) else []


def parse_answer(text: str) -> dict | None:
    """The outermost ``{...}`` of ``text`` as ``{"mappings": [...], "tables": [...]}``; None if it does not parse.

    An answer cut off at the output limit keeps every list item that was complete (``_salvage``).
    """
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m is not None else None
    except json.JSONDecodeError:
        data = None
    if data is None:
        data = _salvage(text)
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("mappings", "tables"):
        value = data.get(key)
        out[key] = value if isinstance(value, list) else []
    return out


def _salvage(text: str) -> dict | None:
    """The complete items of the ``mappings`` and ``tables`` lists of a cut-off answer; None when there are none."""
    decoder = json.JSONDecoder()
    out: dict = {}
    for key in ("mappings", "tables"):
        m = re.search(rf'"{key}"\s*:\s*\[', text)
        if m is None:
            continue
        items, pos = [], m.end()
        while True:
            pos = len(text) - len(text[pos:].lstrip(" \t\r\n,"))
            if pos >= len(text) or text[pos] == "]":
                break
            try:
                item, pos = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                break  # the item the output limit cut off
            items.append(item)
        out[key] = items
    return out if any(out.values()) else None


def _box_ids(template: Template, page: int):
    """A resolver from the model's box reference to a field id on ``page`` (None when it names none).

    The model is asked for full ids but sometimes answers with the mark drawn on the image
    ("27" or 27 for ``c0_27``); a mark names exactly one box on its page (``marks_for``).
    """
    fields = {f.id for f in template.fields if f.page == page}
    by_mark = {mark: fid for fid, mark in marks_for([f for f in template.fields if f.page == page and f.rect]).items()}

    def resolve(ref: object) -> str | None:
        if isinstance(ref, bool) or not isinstance(ref, (str, int)):
            return None
        ref = str(ref).strip()
        return ref if ref in fields else by_mark.get(ref)

    return resolve


def mapping_ops(template: Template, answer: dict, page: int) -> tuple[list[dict], list[TableSpec]]:
    """Ops for the answer's plain mappings on ``page``, and the page's table specs with their rows cleaned."""
    fields = {f.id: f for f in template.fields if f.page == page}
    resolve = _box_ids(template, page)
    tables = [t for t in _list(answer.get("tables")) if isinstance(t, dict)]
    in_tables = {
        fid
        for t in tables
        for row in _list(t.get("rows"))
        if isinstance(row, dict)
        for fid in map(resolve, row.values())
        if fid is not None
    }
    ops: list[dict] = []
    for m in _list(answer.get("mappings")):
        if not isinstance(m, dict):
            continue
        fid, src = resolve(m.get("id")), m.get("source")
        if fid is None or fid in in_tables:
            continue
        if not isinstance(src, dict) or not _list(src.get("paths")):
            continue
        choice = src.get("when") or any(
            isinstance(t, dict) and t.get("op") == "bool_to_check" for t in _list(src.get("transforms"))
        )
        if choice and fields[fid].type == "text":
            ops.append({"op": "set_type", "id": fid, "type": "checkbox"})
        ops.append({"op": "set_source", "id": fid, "source": src})
    return ops, _table_specs(tables, resolve, page)


def _table_specs(tables: list[dict], resolve, page: int) -> list[TableSpec]:
    specs: list[TableSpec] = []
    used: set[str] = set()
    for t in tables:
        rows = []
        for row in _list(t.get("rows")):
            if not isinstance(row, dict):
                continue
            kept = {}
            for key, ref in row.items():
                fid = resolve(ref)
                if fid is not None and fid not in used:
                    kept[key] = fid
                    used.add(fid)
            if kept:
                rows.append(kept)
        if not rows:
            continue
        transforms = {
            c["key"]: _list(c.get("transforms"))
            for c in _list(t.get("columns"))
            if isinstance(c, dict) and isinstance(c.get("key"), str)
        }
        source_path = t.get("source_path")
        specs.append(TableSpec(page, source_path if isinstance(source_path, str) else "", transforms, rows))
    return specs


def table_ops(template: Template, specs: list[TableSpec]) -> list[dict]:
    """Ops for the tables: each merged table clears its linked boxes' sources, then one ``add_table``."""
    fields = {f.id: f for f in template.fields}
    ops: list[dict] = []
    for group in _merge(specs):
        ids = [fid for spec in group for row in spec.rows for fid in row.values()]
        ops.extend({"op": "set_source", "id": fid, "source": None} for fid in ids if fields[fid].source is not None)
        ops.append({
            "op": "add_table",
            "source_path": group[0].source_path,
            "segments": [_segment(spec, fields) for spec in group],
        })
    return ops


def _merge(specs: list[TableSpec]) -> list[list[TableSpec]]:
    """Group specs into tables: same ``source_path``, a shared column key and different pages continue one table."""
    groups: list[list[TableSpec]] = []
    for spec in sorted(specs, key=lambda s: s.page):  # stable: answer order within a page
        keys = {k for row in spec.rows for k in row}
        for group in groups:
            if (
                group[0].source_path == spec.source_path
                and all(g.page != spec.page for g in group)
                and keys & {k for g in group for row in g.rows for k in row}
            ):
                group.append(spec)
                break
        else:
            groups.append([spec])
    return groups


def _segment(spec: TableSpec, fields: dict) -> dict:
    rects = [fields[fid].rect for row in spec.rows for fid in row.values()]
    x0, y0 = min(r[0] for r in rects) - RECT_PAD, min(r[1] for r in rects) - RECT_PAD
    x1, y1 = max(r[2] for r in rects) + RECT_PAD, max(r[3] for r in rects) + RECT_PAD
    columns = []
    for key in dict.fromkeys(k for row in spec.rows for k in row):
        boxes = [fields[row[key]].rect for row in spec.rows if key in row]
        cx0, cx1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
        columns.append({
            "key": key,
            "x": [cx0, max(cx1, cx0 + MIN_COLUMN_WIDTH)],
            "transforms": spec.transforms.get(key, []),
            "fields": [row.get(key) for row in spec.rows],
        })
    return {
        "page": spec.page,
        "rect": [x0, y0, x1, y1],
        "row_top": y0,
        "row_height": (y1 - y0) / len(spec.rows),
        "max_rows": len(spec.rows),
        "columns": columns,
    }


def apply_ops(
    template: Template, ops: list[dict], page: int | None, context: OpContext | None
) -> tuple[Template, int, list[dict]]:
    """Apply ``ops`` one by one as the agent; a rejected op is recorded and skipped.

    Returns the template, the number of ops applied and ``[{"op": op, "reason": str}]`` for the rest.
    """
    note = "page pass" if page is None else f"page pass p{page}"
    applied, rejected = 0, []
    for op in ops:
        try:
            template, _ = apply(template, op, author="agent", note=note, context=context)
        except OpError as e:
            rejected.append({"op": op, "reason": e.reason})
        else:
            applied += 1
    return template, applied, rejected
