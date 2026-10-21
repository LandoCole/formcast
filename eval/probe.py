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
* Free-text choice options (Ruling P): a ground-truth choice box on a string path, or an
  array of strings, with no enum carries free text as its ``value``. Those texts are the
  path's options: record ``k`` takes option ``k mod (n + 1)``, where the extra slot leaves the
  path missing (absent; ``[]`` for an array), so every such box is on in one record and off
  in another. K counts ``n + 1``.
* Every record is checked with ``validate_data``.

Override file ``forms/probes/<id>.json``: ``{"<path>": value_or_list}``. A list
holds one value per record; for a path inside a table, a record's entry is itself
a list with one value per row (a plain value is used for every row).
"""

from __future__ import annotations

import json
import math
import random
import re
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
_MISSING = object()  # a property left out of its object


# ------------------------------------------------------------- ground truth rows


def _table_of(column_path: str) -> str:
    segments = parse_path(column_path)
    last = max(i for i, s in enumerate(segments) if is_array_item(s))
    return format_path(segments[:last])


def _table_groups(gt: list[GTField]) -> dict[str, list[int]]:
    """Table path -> GT indexes of all its column boxes (paths containing ``[]``)."""
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(gt):
        if g.path and any(is_array_item(s) for s in parse_path(g.path)):
            groups.setdefault(_table_of(g.path), []).append(i)
    return groups


def _cluster_rows(gt: list[GTField], indexes: list[int]) -> dict[int, int]:
    """Row index per box, clustering tops across all of ``indexes`` (page by page)."""
    out: dict[int, int] = {}
    row = -1
    anchor: tuple[int, float] | None = None
    for i in sorted(indexes, key=lambda i: (gt[i].page, gt[i].rect[1], gt[i].rect[0])):
        here = (gt[i].page, gt[i].rect[1])
        if anchor is None or here[0] != anchor[0] or here[1] - anchor[1] > ROW_TOLERANCE:
            row += 1
            anchor = here
        out[i] = row
    return out


def gt_rows(gt: list[GTField]) -> dict[int, int]:
    """GT index -> row index, for the boxes of table columns (paths containing ``[]``).

    Per table, row tops are first clustered across ALL its column boxes (page by page,
    top to bottom; a top within 2 pt of the row's first top joins it; the counter
    continues across pages), which keeps sparse columns aligned. If that gives more rows
    than the table's largest column has rows of its own (that column's boxes clustered
    alone), the table is block-style (one record's columns sit on different lines), so
    each column's boxes are numbered in their own order (page, top, left; tops within
    2 pt share a row) instead.
    """
    out: dict[int, int] = {}
    for indexes in _table_groups(gt).values():
        clustered = _cluster_rows(gt, indexes)
        columns: dict[str, list[int]] = {}
        for i in indexes:
            columns.setdefault(gt[i].path, []).append(i)
        own = [_cluster_rows(gt, members) for members in columns.values()]
        if len(set(clustered.values())) <= max(len(set(c.values())) for c in own):
            out.update(clustered)
        else:
            for ordinals in own:
                out.update(ordinals)
    return out


def table_rows_in_gt(gt: list[GTField]) -> dict[str, int]:
    """Table path -> number of ground-truth rows (whichever rule ``gt_rows`` applied)."""
    rows = gt_rows(gt)
    return {table: len({rows[i] for i in indexes}) for table, indexes in _table_groups(gt).items()}


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


def _strings(value: object):
    """Every string inside a JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def free_text_options(gt: list[GTField], paths: list[SchemaPath]) -> dict[str, list[str]]:
    """Path -> the free-text ``value``s of its GT choice boxes, for string paths and arrays of
    strings that have no enum (Ruling P), in GT order without repeats."""
    by_path = {p.path: p for p in paths}
    out: dict[str, list[str]] = {}
    for g in gt:
        sp = by_path.get(g.path) if g.path else None
        if g.type != "choice" or not isinstance(g.value, str) or sp is None:
            continue
        if sp.type == "string" or (sp.type == "array" and sp.item_type == "string"):
            options = out.setdefault(g.path, [])
            if g.value not in options:
                options.append(g.value)
    return out


def _option_trigrams(gen: "_Gen") -> set[str]:
    """Trigrams of every alphabetic run in the schema's enum options and consts, and in the
    free-text choice options, uppercased.

    Option text is given, not generated, so tokens must avoid sharing trigrams with it.
    """
    texts: list[str] = [t for options in gen.choice_options.values() for t in options]
    for sp in gen.paths:
        for path in (sp.path, sp.path + "[]"):
            node = gen.node(path) if path == sp.path or sp.type == "array" else {}
            texts += [v for v in node.get("enum") or [] if isinstance(v, str)]
            if isinstance(node.get("const"), str):
                texts.append(node["const"])
    grams: set[str] = set()
    for run in re.findall(r"[A-Za-z]+", " ".join(texts).upper()):
        grams |= {run[i : i + 3] for i in range(len(run) - 2)}
    return grams


class _Gen:
    def __init__(
        self,
        schema: dict,
        gt: list[GTField] | None,
        overrides: dict | None,
        seed: str,
        form_id: str,
    ):
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
        self.choice_options = free_text_options(gt or [], self.paths)
        self.k_count = max(
            [2] + [len(p.enum) for p in self.paths if p.enum] + [len(o) + 1 for o in self.choice_options.values()]
        )
        self.used_trigrams: set[str] = set(_option_trigrams(self))
        self.used_short: set[str] = set()
        self.used_numbers: set[int] = set()
        day_rng, month_rng, year_rng = (random.Random(f"{seed}:{n}") for n in ("day", "month", "year"))
        self.days = day_rng.sample(_DAYS_FIRST, len(_DAYS_FIRST)) + day_rng.sample(_DAYS_LAST, len(_DAYS_LAST))
        self.months = month_rng.sample(range(1, 13), 12)
        # Digit groups of override values (3+ digits) must not recur in generated numbers or years.
        reserved = {int(m) for v in _strings(self.overrides) for m in re.findall(r"\d{3,}", v)}
        self.used_numbers |= reserved
        years = [y for y in _YEARS if y not in reserved] or _YEARS
        self.years = year_rng.sample(years, len(years))
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
        """A fresh uppercase token whose 3-letter substrings are all unused so far.

        Letters are chosen one at a time among those that keep every new trigram unused,
        so tokens stay findable when most of the trigram space is taken. If a token of
        the wanted length no longer fits (a huge form), a shorter one (down to 3 letters,
        never below ``min_len``) is used.
        """
        length = max(min(TOKEN_LEN, max_len if max_len is not None else TOKEN_LEN), min_len, 1)
        if length < 3:  # too short for trigrams: just avoid repeats while the space allows
            for attempt in range(500):
                tok = "".join(self.rng.choice(_LETTERS) for _ in range(length))
                if tok not in self.used_short or attempt == 499:
                    self.used_short.add(tok)
                    return tok
        for want in range(length, max(min_len, 3) - 1, -1):
            for _ in range(200):
                tok = self._guided_token(want)
                if tok is not None:
                    self.used_trigrams |= {tok[i : i + 3] for i in range(want - 2)}
                    return tok
        raise ValueError(f"{self.form_id}: ran out of distinct probe tokens")

    def _guided_token(self, length: int) -> str | None:
        for _ in range(50):
            first = "".join(self.rng.choice(_LETTERS) for _ in range(3))
            if first in self.used_trigrams:
                continue
            letters, taken = list(first), {first}
            while len(letters) < length:
                prefix = letters[-2] + letters[-1]
                options = [c for c in _LETTERS if prefix + c not in self.used_trigrams and prefix + c not in taken]
                if not options:
                    break
                letters.append(self.rng.choice(options))
                taken.add(prefix + letters[-1])
            else:
                return "".join(letters)
        return None

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
            return self.obj(sp.path, k, row)
        if sp.path in self.choice_options and sp.path not in self.overrides:
            options = self.choice_options[sp.path]
            pick = k % (len(options) + 1)
            if sp.type == "array":
                return [options[pick]] if pick < len(options) else []
            return options[pick] if pick < len(options) else _MISSING
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
            return [self.obj(item_path, k, r) for r in range(rows)]
        if sp.item_type == "array":
            return [self.value(self.by_path[item_path], k, r) for r in range(rows)]
        return [self.scalar(item_path, sp.item_type, item_node, k, r) for r in range(rows)]

    def obj(self, path: str, k: int, row: int | None) -> dict:
        """The object at ``path`` (``""``: the record), leaving out properties that are missing."""
        values = ((_key(s), self.value(s, k, row)) for s in self.children.get(path, []))
        return {key: v for key, v in values if v is not _MISSING}

    def record(self, k: int) -> dict:
        return self.obj("", k, None)


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
