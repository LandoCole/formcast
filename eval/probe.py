"""Probe records: distinctive fill data generated from a form's JSON Schema.

The fill-correctness metric fills a template with several "probe" records whose
every value is distinctive, then checks which value landed in each ground-truth
box. This module makes the records. It is generic: it reads only the schema (and
the ground truth, for how many rows each table needs) and branches on nothing
form-specific. Per-form data files ``forms/probes/<id>.json`` supply values for
the paths it cannot satisfy by itself (``pattern`` paths).

Records (fill design section 6.1):

* K = the largest option count among enums and multi-select item enums, at least 2.
* Strings are uppercase letter tokens of ``TOKEN_LEN`` letters (shorter under
  ``maxLength``); no 3-letter substring occurs in two different tokens.
* ``format: date`` (and ``date-time``) values differ in day, month and year from
  every other date and between records, as far as the calendar allows.
* Numbers are distinct, with at least three digits where the range allows.
* Booleans alternate; enums take option ``k mod n``; multi-selects take options
  ``k mod n`` and ``(k+1) mod n``.
* Tables get one row more than the ground truth has for them (``maxItems`` caps it).
* Every record is checked with ``validate_data``.

Override file ``forms/probes/<id>.json``: ``{"<path>": value_or_list}``. A list
holds one value per record; for a path inside a table, a record's entry is itself
a list with one value per row (a plain value is used for every row).
"""

from __future__ import annotations

import json
import math
import random
import string
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # eval scripts do not rely on the editable install

from formcast.schema import (  # noqa: E402
    ARRAY_ITEM,
    SchemaPath,
    _normalize,  # private: probes need constraint keywords (maxLength, minimum, ...) SchemaPath drops
    flatten_schema,
    format_path,
    is_array_item,
    parse_path,
    validate_data,
)
from score import GTField  # noqa: E402

PROBES_DIR = ROOT / "forms" / "probes"
TOKEN_LEN = 6
#: Two table-column boxes whose tops are within this many points are one row.
ROW_TOLERANCE = 2.0
#: Rows per table when the ground truth says nothing about it (one more than 1).
DEFAULT_ROWS = 2

_LETTERS = string.ascii_uppercase
_DAYS_FIRST = list(range(13, 29))  # days 13-28 cannot be mistaken for months
_DAYS_LAST = list(range(1, 13))
_YEARS = list(range(1950, 2021))
_SCALARS = {"string", "integer", "number", "boolean", "date", "enum"}


# ------------------------------------------------------------- ground truth rows


def _column_groups(gt: list[GTField]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(gt):
        if g.path and any(is_array_item(s) for s in parse_path(g.path)):
            groups.setdefault(g.path, []).append(i)
    return groups


def gt_rows(gt: list[GTField]) -> dict[int, int]:
    """GT index -> row index, for the boxes of table columns (paths containing ``[]``).

    The boxes of one column path are ordered by page, then top, then left; boxes
    whose tops are within 2 pt share a row.
    """
    out: dict[int, int] = {}
    for indexes in _column_groups(gt).values():
        ordered = sorted(indexes, key=lambda i: (gt[i].page, gt[i].rect[1], gt[i].rect[0]))
        row = -1
        anchor: tuple[int, float] | None = None
        for i in ordered:
            here = (gt[i].page, gt[i].rect[1])
            if anchor is None or here[0] != anchor[0] or here[1] - anchor[1] > ROW_TOLERANCE:
                row += 1
                anchor = here
            out[i] = row
    return out


def _table_of(column_path: str) -> str:
    segments = parse_path(column_path)
    last = max(i for i, s in enumerate(segments) if is_array_item(s))
    return format_path(segments[:last])


def table_rows_in_gt(gt: list[GTField]) -> dict[str, int]:
    """Table path -> number of ground-truth rows (the most rows any of its columns has)."""
    rows = gt_rows(gt)
    out: dict[str, int] = {}
    for column, indexes in _column_groups(gt).items():
        table = _table_of(column)
        out[table] = max(out.get(table, 0), len({rows[i] for i in indexes}))
    return out


# ------------------------------------------------------------------- overrides


def load_overrides(form_id: str, probes_dir: Path | None = None) -> dict | None:
    """``forms/probes/<form_id>.json`` as a dict, or None when the form has none."""
    path = (PROBES_DIR if probes_dir is None else Path(probes_dir)) / f"{form_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected an object of path -> value(s)")
    return data


# ------------------------------------------------------------------- generator


class _Gen:
    def __init__(self, schema: dict, gt: list[GTField] | None, overrides: dict | None, seed: str, form_id: str):
        self.schema = schema
        self.form_id = form_id
        self.overrides = overrides or {}
        self.rng = random.Random(f"formcast-probe:{seed}")
        self.paths = flatten_schema(schema)
        self.by_path = {p.path: p for p in self.paths}
        self.table_rows = table_rows_in_gt(gt or [])
        self._nodes: dict[str, dict] = {}
        self.children: dict[str, list[SchemaPath]] = {}
        for sp in self.paths:
            self.children.setdefault(format_path(parse_path(sp.path)[:-1]), []).append(sp)
        unknown = sorted(set(self.overrides) - set(self.by_path) - {p.path + "[]" for p in self.paths})
        if unknown:
            raise ValueError(f"{form_id}: overrides name paths the schema does not have: {unknown}")
        self.k_count = max([2] + [len(p.enum) for p in self.paths if p.enum])
        self.used_trigrams: set[str] = set()
        self.used_short: set[str] = set()
        self.used_numbers: set[int] = set()
        day_rng, month_rng, year_rng = (random.Random(f"{seed}:{n}") for n in ("day", "month", "year"))
        self.days = day_rng.sample(_DAYS_FIRST, len(_DAYS_FIRST)) + day_rng.sample(_DAYS_LAST, len(_DAYS_LAST))
        self.months = month_rng.sample(range(1, 13), 12)
        self.years = year_rng.sample(_YEARS, len(_YEARS))
        self.date_slots: dict[str, int] = {}
        for sp in self.paths:
            if sp.type == "date" or (sp.type == "string" and self.node(sp.path).get("format") == "date-time"):
                self.date_slots[sp.path] = len(self.date_slots)
            if sp.type == "array" and sp.item_type == "date":
                self.date_slots[sp.path + "[]"] = len(self.date_slots)
        self.date_cells = 0

    # --- schema access

    def node(self, path: str) -> dict:
        """The normalized schema node at ``path`` (``name[]`` for an array's items)."""
        if path not in self._nodes:
            node, origin = _normalize(self.schema, self.schema, ())
            for seg in parse_path(path):
                if is_array_item(seg):
                    node, origin = _normalize(node.get("items"), self.schema, origin.items or ())
                else:
                    sub = (node.get("properties") or {}).get(seg)
                    node, origin = _normalize(sub, self.schema, origin.props.get(seg, ()))
            self._nodes[path] = node
        return self._nodes[path]

    # --- values

    def token(self, max_len: int | None, min_len: int = 0) -> str:
        length = max(min(TOKEN_LEN, max_len if max_len is not None else TOKEN_LEN), min_len, 1)
        for attempt in range(500):
            tok = "".join(self.rng.choice(_LETTERS) for _ in range(length))
            if length < 3:  # too short for trigrams: just avoid repeats while the space allows
                if tok in self.used_short and attempt < 400:
                    continue
                self.used_short.add(tok)
                return tok
            grams = {tok[i : i + 3] for i in range(length - 2)}
            if len(grams) == length - 2 and not grams & self.used_trigrams:
                self.used_trigrams |= grams
                return tok
        raise ValueError(f"{self.form_id}: ran out of distinct probe tokens")

    def number(self, node: dict, integer: bool) -> int | float:
        lo, hi = node.get("minimum"), node.get("maximum")
        lo_i = 0 if lo is None else math.ceil(lo)
        hi_i = lo_i + 99_999 if hi is None else math.floor(hi)
        if node.get("exclusiveMinimum") is not None:
            lo_i = max(lo_i, math.floor(node["exclusiveMinimum"]) + 1)
        if node.get("exclusiveMaximum") is not None:
            hi_i = min(hi_i, math.ceil(node["exclusiveMaximum"]) - 1)
        if lo_i < 100 <= hi_i:
            lo_i = 100  # at least three digits where the range allows
        if hi_i < lo_i:
            return lo if lo is not None else hi_i
        for attempt in range(200):
            value = self.rng.randint(lo_i, hi_i)
            if value not in self.used_numbers or attempt == 199:
                self.used_numbers.add(value)
                return value
        raise AssertionError("unreachable")

    def date_parts(self, path: str, k: int, row: int | None) -> tuple[int, int, int]:
        if path in self.date_slots:
            slot = self.date_slots[path]
            c = slot * self.k_count + k + (row or 0) * len(self.date_slots) * self.k_count
        else:
            c = (len(self.date_slots) + self.date_cells) * self.k_count + k
            self.date_cells += 1
        return self.years[c % len(self.years)], self.months[c % 12], self.days[c % 28]

    def scalar(self, path: str, ptype: str, node: dict, k: int, row: int | None) -> object:
        if path in self.overrides:
            return self.override(path, k, row)
        if "const" in node:
            return node["const"]
        if ptype == "enum":
            options = node["enum"]
            return options[k % len(options)]
        if ptype == "boolean":
            return k % 2 == 0
        if ptype in ("integer", "number"):
            return self.number(node, ptype == "integer")
        fmt = node.get("format")
        if ptype == "date" or fmt == "date-time":
            y, m, d = self.date_parts(path, k, row)
            day = f"{y:04d}-{m:02d}-{d:02d}"
            if ptype == "date":
                return day
            return f"{day}T{10 + k % 12:02d}:{(7 * k + 3) % 60:02d}:{(11 * k + 5) % 60:02d}Z"
        if "pattern" in node:
            raise ValueError(
                f"{self.form_id}: no probe value for pattern path {path}; add forms/probes/{self.form_id}.json"
            )
        tok = self.token(node.get("maxLength"), node.get("minLength", 0))
        if fmt == "email":
            return f"{tok.lower()}@probe.test"
        if fmt in ("uri", "url", "iri"):
            return f"https://probe.test/{tok.lower()}"
        if fmt == "uuid":
            return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
        return tok

    def override(self, path: str, k: int, row: int | None) -> object:
        entry = self.overrides[path]
        if isinstance(entry, list):
            if k >= len(entry):
                raise ValueError(f"{self.form_id}: override for {path} has {len(entry)} values, needs {self.k_count}")
            entry = entry[k]
        if isinstance(entry, list):
            if row is None or row >= len(entry):
                raise ValueError(f"{self.form_id}: override for {path}, record {k}, has too few row values")
            entry = entry[row]
        return entry

    # --- structure

    def row_count(self, path: str, node: dict) -> int:
        n = self.table_rows[path] + 1 if path in self.table_rows else DEFAULT_ROWS
        if "maxItems" in node:
            n = min(n, node["maxItems"])
        return max(n, node.get("minItems", 0))

    def value(self, sp: SchemaPath, k: int, row: int | None) -> object:
        node = self.node(sp.path)
        if sp.type == "object":
            return {_key(s): self.value(s, k, row) for s in self.children.get(sp.path, [])}
        if sp.type != "array":
            return self.scalar(sp.path, sp.type, node, k, row)
        item_path = sp.path + "[]"
        item_node = self.node(item_path)
        if sp.item_type == "enum":  # multi-select
            options = item_node["enum"]
            picks = [options[k % len(options)]]
            if len(options) > 1 and node.get("maxItems", 2) > 1:
                picks.append(options[(k + 1) % len(options)])
            return picks
        if sp.item_type is None:
            return []
        rows = self.row_count(sp.path, node)
        if sp.item_type == "object":
            return [
                {_key(s): self.value(s, k, r) for s in self.children.get(item_path, [])}
                for r in range(rows)
            ]
        if sp.item_type == "array":
            return [self.value(self.by_path[item_path], k, r) for r in range(rows)]
        return [self.scalar(item_path, sp.item_type, item_node, k, r) for r in range(rows)]

    def record(self, k: int) -> dict:
        return {_key(s): self.value(s, k, None) for s in self.children.get("", [])}


def _key(sp: SchemaPath) -> str:
    """The property name a path's value is stored under in its parent object."""
    return parse_path(sp.path)[-1]


def probe_records(
    schema: dict,
    gt: list[GTField] | None,
    *,
    overrides: dict | None = None,
    seed: str = "",
    form_id: str | None = None,
) -> list[dict]:
    """K probe records for ``schema`` (see the module docstring).

    ``gt`` gives each table's row count (None or empty: two rows). ``overrides`` is
    a ``load_overrides`` result. ``form_id`` only labels error messages (default: ``seed``).

    Raises:
        ValueError: when a ``pattern`` path has no override, an override is unusable, or
            a generated record fails ``validate_data``.
    """
    gen = _Gen(schema, gt, overrides, seed, form_id or seed or "schema")
    records = [gen.record(k) for k in range(gen.k_count)]
    for k, rec in enumerate(records):
        errors = validate_data(schema, rec)
        if errors:
            raise ValueError(f"{gen.form_id}: probe record {k} is invalid: " + "; ".join(errors[:5]))
    return records
