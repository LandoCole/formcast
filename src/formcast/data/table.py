"""Read a user's data, whatever its shape: a CSV or TSV, an Excel workbook, JSON, or pasted text.

    read_table(data, name) -> Table        a file's bytes; the name's extension picks the reader
    sniff_text(text) -> Table | None       pasted text that is JSON or a delimited table; None for prose
    profile(table) -> list[ColumnProfile]  the data's own schema: per column, its type, fill and samples
    data_schema(table) -> dict             the same as a JSON Schema

A ``Table`` is a list of columns and one dict per row (column -> value). Cells keep what the source
had: text from a CSV, numbers, booleans and dates (ISO text) from a workbook. JSON is flattened into
dotted columns (``applicant.name``, ``items[0].qty``); a list of plain values stays one cell. Empty
cells are ``None``. Nothing here guesses what the columns mean: that is the data mapping's job.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from formcast.errors import FormcastError

MAX_ROWS = 10_000
MAX_COLUMNS = 500
SAMPLES = 5
_SAMPLE_CHARS = 80

TableKind = Literal["csv", "xlsx", "json"]


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, Any]]
    kind: TableKind = "csv"
    name: str = ""
    truncated: bool = False  # the source had more than MAX_ROWS rows; the rest were left out
    sheet: str | None = None  # a workbook's sheet that was read


@dataclass
class ColumnProfile:
    name: str
    type: str  # string, integer, number, boolean, date, list or empty
    filled: int
    distinct: int
    samples: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- readers


def read_table(data: bytes, name: str = "") -> Table:
    """The table in a file: ``.csv``/``.tsv``/``.txt``, ``.xlsx``/``.xlsm`` or ``.json`` (by ``name``'s extension).

    Without a known extension the bytes are tried as JSON, then as delimited text. Raises
    ``FormcastError`` with a plain message for a file that can't be read as a table.
    """
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("xlsx", "xlsm") or (not ext and data[:2] == b"PK"):
        return _read_xlsx(data, name)
    if ext == "xls":
        raise FormcastError("old .xls workbooks can't be read; save the sheet as .xlsx or .csv")
    text = _decode(data, name)
    if ext == "json":
        return _from_json(_parse_json(text, name), name)
    if not ext:
        try:
            return _from_json(json.loads(text), name)
        except (ValueError, FormcastError):
            pass
    return _read_delimited(text, name, tab=ext == "tsv")


def sniff_text(text: str) -> Table | None:
    """Pasted text as a table when it is JSON records or a delimited table with a header; None for prose."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] in "[{":
        try:
            return _from_json(json.loads(stripped), "pasted JSON")
        except (ValueError, FormcastError):
            pass
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    head = "\n".join(lines[:20])
    for delim in ("\t", ",", ";", "|"):
        try:
            rows = list(csv.reader(io.StringIO(head), delimiter=delim))
        except csv.Error:
            continue
        widths = {len(r) for r in rows}
        width = next(iter(widths))
        # every line has the same number of cells, the header's are short (not clauses of a sentence), and two
        # columns need three lines
        # (two lines of prose with a comma each are not a table)
        looks = len(widths) == 1 and (width >= 3 or (width == 2 and len(lines) >= 3))
        if looks and all(len(c) <= 40 for c in rows[0]):
            try:
                return _read_delimited(stripped, "pasted table", delimiter=delim)
            except FormcastError:
                return None
    return None


def _decode(data: bytes, name: str) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FormcastError(f"{name or 'the file'} is not text in UTF-8 or Windows-1252")


def _parse_json(text: str, name: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise FormcastError(f"{name or 'the file'} is not valid JSON: {exc}") from None


def _read_delimited(text: str, name: str, *, tab: bool = False, delimiter: str | None = None) -> Table:
    if delimiter is None:
        if tab:
            delimiter = "\t"
        else:
            try:
                delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        raise FormcastError(f"{name or 'the file'} can't be read as a table: {exc}") from None
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise FormcastError(f"{name or 'the file'} has no rows")
    header = _header(rows[0])
    body = rows[1:]
    out = [{col: _cell(r[i]) if i < len(r) else None for i, col in enumerate(header)} for r in body[:MAX_ROWS]]
    return Table(header, out, "csv", name, truncated=len(body) > MAX_ROWS)


def _read_xlsx(data: bytes, name: str) -> Table:
    try:
        import openpyxl
    except ImportError:
        raise FormcastError("reading Excel files needs openpyxl (pip install 'formcast[xlsx]'); or save the sheet as CSV") from None
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many kinds for a damaged file
        raise FormcastError(f"{name or 'the file'} can't be read as an Excel workbook ({type(exc).__name__})") from None
    try:
        for sheet in book.worksheets:
            rows = [list(r) for r in sheet.iter_rows(values_only=True)]
            rows = [r for r in rows if any(_xcell(c) is not None for c in r)]
            if not rows:
                continue
            header = _header(["" if c is None else str(c) for c in rows[0]])
            body = rows[1:]
            out = [{col: _xcell(r[i]) if i < len(r) else None for i, col in enumerate(header)} for r in body[:MAX_ROWS]]
            return Table(header, out, "xlsx", name, truncated=len(body) > MAX_ROWS, sheet=sheet.title)
    finally:
        book.close()
    raise FormcastError(f"{name or 'the workbook'} has no rows on any sheet")


def _header(cells: list[str]) -> list[str]:
    """Column names: trimmed, blanks named ``column_<n>``, repeats numbered, at most MAX_COLUMNS."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(cells[:MAX_COLUMNS]):
        base = " ".join(str(raw).split()) or f"column_{i + 1}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}_{n + 1}")
    return out


def _cell(text: str) -> str | None:
    text = text.strip()
    return text or None


def _xcell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return _cell(value)
    return value


def _from_json(value: Any, name: str) -> Table:
    """JSON records as a table: a list of objects, an object holding one such list, or one object."""
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        value = lists[0] if len(value) == 1 and len(lists) == 1 else [value]
    if not isinstance(value, list) or not value or not all(isinstance(x, dict) for x in value):
        raise FormcastError(f"{name or 'the JSON'} is not a list of records (objects)")
    rows = [_flatten(x) for x in value[:MAX_ROWS]]
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    columns = columns[:MAX_COLUMNS]
    return Table(columns, [{c: row.get(c) for c in columns} for row in rows], "json", name, truncated=len(value) > MAX_ROWS)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, v in value.items():
            out.update(_flatten(v, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        for i, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{i}]"))
    else:
        out[prefix or "value"] = None if value == "" else value
    return out


# ---------------------------------------------------------------- the data's own schema

_INT = re.compile(r"[+-]?\d{1,15}")
_NUM = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_US_DATE = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")
_BOOL_WORDS = {"true", "false", "yes", "no"}


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    text = str(value).strip()
    if text.lower() in _BOOL_WORDS:
        return "boolean"
    if len(text) > 1 and text[0] == "0" and text.isdigit():  # a leading zero: a code (ZIP, ID), not a number
        return "string"
    if _INT.fullmatch(text):
        return "integer"
    if _NUM.fullmatch(text):
        return "number"
    if _ISO_DATE.fullmatch(text) or _US_DATE.fullmatch(text):
        return "date"
    return "string"


def profile(table: Table) -> list[ColumnProfile]:
    """Per column: the one type every filled cell has (else ``string``), how many are filled, and samples."""
    out = []
    for col in table.columns:
        values = [r.get(col) for r in table.rows if r.get(col) is not None]
        types = {_type_of(v) for v in values}
        if not types:
            kind = "empty"
        elif len(types) == 1:
            kind = types.pop()
        elif types <= {"integer", "number"}:
            kind = "number"
        else:
            kind = "string"
        distinct: list[str] = []
        seen: set[str] = set()
        for v in values:
            text = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v)
            if text not in seen:
                seen.add(text)
                distinct.append(text)
        samples = [s if len(s) <= _SAMPLE_CHARS else s[: _SAMPLE_CHARS - 1] + "…" for s in distinct[:SAMPLES]]
        out.append(ColumnProfile(col, kind, len(values), len(seen), samples))
    return out


_JSON_TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean", "empty": "string"}


def data_schema(table: Table) -> dict:
    """The table's own shape as a JSON Schema: one property per column, typed from its cells, with examples."""
    props: dict[str, dict] = {}
    for p in profile(table):
        if p.type == "date":
            prop: dict = {"type": "string", "format": "date"} if all(_ISO_DATE.fullmatch(s) for s in p.samples) else {"type": "string"}
        elif p.type == "list":
            prop = {"type": "array"}
        else:
            prop = {"type": _JSON_TYPES[p.type]}
        if p.samples:
            prop["examples"] = p.samples[:3]
        props[p.name] = prop
    title = table.name or "data"
    return {"title": title, "type": "object", "properties": props}
