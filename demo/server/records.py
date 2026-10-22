"""Where the workbench gets schemas and records to fill forms with.

Replace with your own source: implement ``RecordSource`` (three methods). ``list`` and ``get``
raise ``KeyError`` for an unknown schema or id. The sources here read a data folder laid out as

    <data>/schemas/<schema>.json     JSON Schema, one per kind of record
    <data>/records/<schema>.csv      CsvRecords: one row per record
    <data>/records.sqlite            SqliteRecords: one table per schema, same columns

Both use the same columns. ``id`` is required and names the record; ``title`` is an optional
label for lists. Every other column is a value at a dotted path (``applicant.address.city``),
so the row becomes a nested record. Rules, kept simple on purpose:

- An empty cell (CSV) or NULL (SQLite) leaves the key out.
- Arrays and objects go in one cell as JSON text (``["a", "b"]``). In SQLite those columns are
  plain TEXT; the other columns use TEXT, INTEGER or REAL as the schema says.
- Booleans and numbers are read back as such where the schema says so (CSV ``true``/``false``,
  SQLite 0/1).
- Nothing here rejects a bad row. A cell that doesn't parse stays the raw string, a column the
  schema doesn't know becomes an extra key, and a column that can't be placed (a path under a
  value that is not an object) keeps its dotted name as a top-level key (a plain value
  for a path that already holds an object is dropped). ``fill`` then reports ``invalid`` and
  ``unknown_keys``.

What can't be read at all (a CSV that isn't UTF-8, an empty CSV, a CSV the parser rejects, a
table or file without an ``id`` column, a database file that isn't SQLite) raises one error,
``RecordSourceError``, with a plain message naming the file. ``MultiRecords`` isolates such a
source: its records are skipped, the rest still answer, and ``problems()`` says what was skipped.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol, TypedDict

from formcast import flatten_schema

from demo.server.store import NAME_RE

class RecordSourceError(ValueError):
    """A record file or table could not be read at all; the message says which and why.

    A ``ValueError`` so that callers that only know about bad data still treat it as input
    trouble; routes map it to a plain 4xx.
    """


RESERVED = ("id", "title")
_SCALARS = {"string", "integer", "number", "boolean", "date", "enum"}


class RecordSummary(TypedDict):
    id: str
    title: str


class RecordSource(Protocol):
    def schemas(self) -> dict[str, dict]: ...
    def list(self, schema: str) -> list[RecordSummary]: ...
    def get(self, schema: str, id: str) -> dict: ...


def _read_schemas(schemas_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(schemas_dir.glob("*.json")):
        if NAME_RE.fullmatch(path.stem):
            try:
                out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return out


def _leaf_types(schema: dict) -> dict[str, str]:
    try:
        return {p.path: p.type for p in flatten_schema(schema)}
    except Exception:  # a schema we can't flatten: read every cell as text
        return {}


def _value(raw: Any, kind: str | None) -> Any:
    """One cell as a JSON value: JSON text for arrays and objects, typed scalars where the schema says."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if kind not in _SCALARS and text[:1] in ("[", "{"):
            try:
                return json.loads(text)
            except ValueError:
                return raw
        try:
            if kind == "boolean" and text.lower() in ("true", "false"):
                return text.lower() == "true"
            if kind == "integer":
                return int(text)
            if kind == "number":
                return float(text) if any(c in text for c in ".eE") else int(text)
        except ValueError:
            pass
        return raw
    if kind == "boolean" and raw in (0, 1):
        return bool(raw)
    return raw


def _text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()


def _slot(out: dict, column: str) -> tuple[dict, str]:
    """The dict and key that hold ``column``; the top level under the raw name when it can't be placed."""
    parts = column.split(".")
    if all(parts):
        node = out
        for key in parts[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                return out, column
        return node, parts[-1]
    return out, column


def _record(cells: dict[str, Any], types: dict[str, str]) -> dict:
    """Nest ``{"a.b": x}`` as ``{"a": {"b": x}}``, dropping empty cells and the reserved columns."""
    out: dict[str, Any] = {}
    for column, raw in cells.items():
        if column in RESERVED or raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        node, key = _slot(out, column)
        if isinstance(node.get(key), dict):
            continue  # a plain value for a path that already holds an object: the object stays
        node[key] = _value(raw, types.get(column))
    return out


class _Tabular:
    """What CsvRecords and SqliteRecords share: rows of cells in, summaries and records out."""

    def __init__(self, schemas_dir: Path) -> None:
        self.schemas_dir = Path(schemas_dir)

    def schemas(self) -> dict[str, dict]:
        return _read_schemas(self.schemas_dir)

    def _rows(self, schema: str) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _known(self, schema: str) -> dict:
        schemas = self.schemas()
        if schema not in schemas:
            raise KeyError(schema)
        return schemas[schema]

    def list(self, schema: str) -> list[RecordSummary]:
        self._known(schema)
        out: list[RecordSummary] = []
        for row in self._rows(schema):
            rid = _text(row.get("id"))
            if rid:
                out.append({"id": rid, "title": _text(row.get("title")) or rid})
        return out

    def get(self, schema: str, id: str) -> dict:
        types = _leaf_types(self._known(schema))
        for row in self._rows(schema):
            if _text(row.get("id")) == id:
                return _record(row, types)
        raise KeyError(id)


class CsvRecords(_Tabular):
    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)
        super().__init__(self.folder / "schemas")

    def _rows(self, schema: str) -> list[dict[str, Any]]:
        path = self.folder / "records" / f"{schema}.csv"
        if not path.exists():
            return []
        try:
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                if not fieldnames:
                    raise RecordSourceError(f"{path.name} is empty")
                if "id" not in fieldnames:
                    raise RecordSourceError(f"{path.name} has no id column")
                # A row longer than the header files its extra cells under None; drop them.
                return [{k: v for k, v in row.items() if k is not None} for row in reader]
        except UnicodeDecodeError:
            raise RecordSourceError(f"{path.name} is not UTF-8 text (save it as CSV UTF-8)") from None
        except csv.Error as exc:
            raise RecordSourceError(f"{path.name} is not valid CSV: {exc}") from None


class SqliteRecords(_Tabular):
    def __init__(self, db_path: Path | str, schemas_dir: Path | str) -> None:
        self.db_path = Path(db_path)
        super().__init__(Path(schemas_dir))

    def _rows(self, schema: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        # Read-only, one connection per call: the server is threaded.
        with closing(sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            try:
                try:
                    cursor = db.execute(f'SELECT * FROM "{schema}"')  # schema is a known schema name
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc):
                        return []
                    raise
                if "id" not in [d[0] for d in cursor.description]:
                    raise RecordSourceError(f"table {schema} in {self.db_path.name} has no id column")
                return [dict(row) for row in cursor]
            except sqlite3.DatabaseError as exc:  # not a database, locked, damaged
                raise RecordSourceError(f"{self.db_path.name} cannot be read as SQLite: {exc}") from None


class MultiRecords:
    """Several sources as one: schemas merged, lists joined, ``get`` from the first that has the id.

    A source that raises ``RecordSourceError`` is skipped for that call and remembered:
    ``problems()`` lists what is currently unreadable (``schema``, ``source``, ``detail``), and
    an entry clears when the same source reads that schema again. ``get`` raises
    ``RecordSourceError`` instead of ``KeyError`` when the id was not found and a source that
    could have held it failed, so a broken file is not reported as "no such record".
    """

    def __init__(self, *sources: RecordSource) -> None:
        self.sources = sources
        self._problems: dict[tuple[int, str], str] = {}
        self._lock = threading.Lock()

    def _note(self, index: int, schema: str, error: str | None) -> None:
        with self._lock:
            if error is None:
                self._problems.pop((index, schema), None)
            else:
                self._problems[(index, schema)] = error

    def problems(self) -> list[dict[str, str]]:
        """Unreadable sources seen so far, as ``{"schema", "source", "detail"}``."""
        with self._lock:
            items = sorted(self._problems.items())
        return [
            {"schema": schema, "source": type(self.sources[i]).__name__, "detail": detail}
            for (i, schema), detail in items
        ]

    def schemas(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for source in self.sources:
            out.update(source.schemas())
        return out

    def list(self, schema: str) -> list[RecordSummary]:
        found = [(i, s) for i, s in enumerate(self.sources) if schema in s.schemas()]
        if not found:
            raise KeyError(schema)
        out: list[RecordSummary] = []
        for i, source in found:
            try:
                out.extend(source.list(schema))
            except RecordSourceError as exc:
                self._note(i, schema, str(exc))
            else:
                self._note(i, schema, None)
        return out

    def get(self, schema: str, id: str) -> dict:
        failure: str | None = None
        known = False
        for i, source in enumerate(self.sources):
            if schema in source.schemas():
                known = True
                try:
                    record = source.get(schema, id)
                except KeyError:
                    self._note(i, schema, None)
                except RecordSourceError as exc:
                    self._note(i, schema, str(exc))
                    failure = failure or str(exc)
                else:
                    self._note(i, schema, None)
                    return record
        if failure is not None:
            raise RecordSourceError(failure)
        raise KeyError(id if known else schema)
