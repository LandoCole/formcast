"""A data mapping: how the columns of a user's table become records of a form's schema.

The mapping is plain data, made once (by the model, or by :func:`direct_mapping` when the columns
already are schema paths) and then applied to every row here, with no model call. That is what
makes a saved mapping reusable: the next file with the same columns fills without AI.

    {"columns": ["Full Name", "DOB", "Citizen?"],
     "rules": [
       {"path": "applicant.first_name", "column": "Full Name", "transforms": [{"op": "split", "args": {"index": 0}}]},
       {"path": "applicant.last_name", "column": "Full Name", "transforms": [{"op": "split", "args": {"index": -1}}]},
       {"path": "applicant.dob", "column": "DOB", "transforms": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]},
       {"path": "applicant.address", "template": "{Street}, {City}, {State} {ZIP}"},
       {"path": "applicant.us_citizen", "column": "Citizen?", "transforms": [{"op": "map", "args": {"values": {"Y": true, "N": false}}}]},
       {"path": "office.state", "value": "WI"}],
     "unmapped_columns": ["Internal ID"], "notes": ""}

A rule takes its value from one ``column``, a ``template`` of ``{Column}`` placeholders, or a fixed
``value`` (not null); then its ``transforms`` run in order (see ``TRANSFORMS``). A path is a schema path; an
item of a list of records takes an index (``references[0].name``). After the transforms the value is
coerced to the path's schema type where that is safe (``"yes"`` to true for a boolean, ``"12"`` to 12,
an enum value matched without regard to case). An empty result sets nothing.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, model_validator

from formcast.data.table import Table
from formcast.schema import ARRAY_ITEM, SchemaPath, flatten_schema, parse_path

_INDEX = re.compile(r"\[(\d+)\]")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class Transform(BaseModel):
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class Rule(BaseModel):
    path: str
    column: str | None = None
    template: str | None = None
    value: Any = None
    transforms: list[Transform] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_source(self) -> Rule:
        given = [self.column is not None, self.template is not None, self.value is not None]
        if sum(given) != 1:
            raise ValueError("a rule takes exactly one of column, template or value")
        return self

    def columns_used(self) -> list[str]:
        if self.column is not None:
            return [self.column]
        if self.template is not None:
            return _PLACEHOLDER.findall(self.template)
        return []


class DataMapping(BaseModel):
    columns: list[str] = Field(default_factory=list)  # the columns of the table it was made for
    rules: list[Rule] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)
    notes: str = ""
    source: str = "model"  # "model" or "direct" (columns named by schema path)

    def fits(self, columns: list[str]) -> bool:
        """Whether every column the rules read is among ``columns`` (so the mapping applies to that table)."""
        have = set(columns)
        return bool(self.rules) and all(c in have for r in self.rules for c in r.columns_used())

    def dump(self) -> dict:
        """As plain JSON data, rules without their unused keys."""
        return self.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------- transforms

_TRUE = {"true", "t", "yes", "y", "1", "x", "on", "checked", "✓", "✔"}
_FALSE = {"false", "f", "no", "n", "0", "off", "unchecked", ""}
_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d.%m.%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y",
    "%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
)  # fmt: skip


def _text(v: Any) -> str:
    return v if isinstance(v, str) else str(v)


def _split(v: Any, sep: str | None = None, index: int | None = None, start: int | None = None, end: int | None = None) -> Any:
    parts = [p for p in _text(v).split(sep)] if sep else _text(v).split()
    parts = [p.strip() for p in parts]
    if index is not None:
        try:
            return parts[int(index)] or None
        except IndexError:
            return None
    picked = parts[start:end]
    return (sep if sep else " ").join(picked).strip() or None


def _list(v: Any, sep: str = ",") -> Any:
    if isinstance(v, list):
        return v
    items = [p.strip() for p in re.split("|".join(re.escape(s) for s in (sep if isinstance(sep, list) else [sep])), _text(v))]
    return [p for p in items if p] or None


def _regex(v: Any, pattern: str, group: int | str = 1) -> Any:
    m = re.search(pattern, _text(v))
    if m is None:
        return None
    try:
        return m.group(group)
    except IndexError:
        return m.group(0)


def _date(v: Any, formats: list[str] | None = None) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    text = _text(v).strip()
    for fmt in [*(formats or []), *_DATE_FORMATS]:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text  # left as it is: fill reports an invalid date rather than losing it


def _map(v: Any, values: dict[str, Any], default: Any = ...) -> Any:
    key = _text(v).strip().casefold()
    for k, out in values.items():
        if str(k).strip().casefold() == key:
            return out
    return v if default is ... else default


def _bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    t = _text(v).strip().casefold()
    return True if t in _TRUE else False if t in _FALSE else v


def _number(v: Any, integer: bool = False) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if integer else v
    t = re.sub(r"[,$€£%\s]", "", _text(v))
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        n = float(t)
    except ValueError:
        return v
    return int(n) if integer or n.is_integer() and "." not in t else n


#: op name -> (function, one-line doc for the model). Every function takes the value first.
TRANSFORMS: dict[str, tuple[Callable[..., Any], str]] = {
    "split": (_split, "split(sep=None, index=None, start=None, end=None): split on sep (whitespace if none); "
              "index picks one part (negative from the end), start/end join a slice of parts"),
    "list": (_list, 'list(sep=","): split one cell into a list of items (for a multi-select or a list of text)'),
    "regex": (_regex, "regex(pattern, group=1): the matched group, or nothing when it does not match"),
    "replace": (lambda v, old, new="": _text(v).replace(old, new), "replace(old, new=\"\"): replace text"),
    "date": (_date, 'date(formats=["%m/%d/%Y", ...]): parse with these strptime formats (common ones are tried too) to ISO YYYY-MM-DD'),
    "map": (_map, "map(values={from: to}, default): look a value up, case-insensitively; default when not found (else kept)"),
    "boolean": (_bool, "boolean(): yes/y/true/1/x to true, no/n/false/0 to false"),
    "number": (lambda v: _number(v), "number(): text like \"$1,200.50\" to a number"),
    "integer": (lambda v: _number(v, integer=True), "integer(): text to a whole number"),
    "digits": (lambda v: re.sub(r"\D", "", _text(v)) or None, "digits(): keep only the digits (phone, SSN, ZIP+4)"),
    "upper": (lambda v: _text(v).upper(), "upper()"),
    "lower": (lambda v: _text(v).lower(), "lower()"),
    "title": (lambda v: _text(v).title(), "title(): Title Case"),
    "strip": (lambda v, chars=None: _text(v).strip(chars) or None, "strip(chars=None)"),
    "default": (lambda v, value: v, "default(value): the value to use when the cell is empty"),
}  # fmt: skip


def transforms_ref() -> str:
    return "\n".join(f"- {doc}" for _, doc in TRANSFORMS.values())


def _run(transforms: list[Transform], value: Any) -> Any:
    for t in transforms:
        if t.op == "default":
            if _empty(value):
                value = t.args.get("value")
            continue
        if _empty(value):
            continue
        fn = TRANSFORMS.get(t.op, (None, ""))[0]
        if fn is None:
            continue  # an unknown op is skipped (check_mapping reports it)
        try:
            value = fn(value, **t.args)
        except (TypeError, ValueError, re.error):
            continue  # bad arguments: the value goes on unchanged
    return value


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


# ---------------------------------------------------------------- schema paths

def target_paths(schema: dict) -> dict[str, SchemaPath]:
    """The paths a rule may set: every leaf, a list of plain values included, but not objects or lists of records."""
    return {
        sp.path: sp
        for sp in flatten_schema(schema)
        if sp.type != "object" and not (sp.type == "array" and sp.item_type == "object")
    }


def _generic(path: str) -> tuple[str, list[int]]:
    """``a[2].b`` -> (``a[].b``, [2])."""
    return _INDEX.sub("[]", path), [int(i) for i in _INDEX.findall(path)]


def check_mapping(mapping: DataMapping, schema: dict, columns: list[str]) -> tuple[DataMapping, list[str]]:
    """The mapping with the rules it can't apply removed, and a plain warning for each removed rule.

    A rule is removed when its path is not a settable schema path (or lacks an item index), or when it
    reads a column the table doesn't have. An unknown transform is dropped from its rule, with a warning.
    """
    paths = target_paths(schema)
    have = set(columns)
    kept: list[Rule] = []
    warnings: list[str] = []
    for rule in mapping.rules:
        generic, idx = _generic(rule.path)
        if generic not in paths:
            warnings.append(f"{rule.path}: not a field of the form's schema")
            continue
        if generic.count("[]") != len(idx):
            warnings.append(f"{rule.path}: a list item needs its index, like {generic.replace('[]', '[0]', 1)}")
            continue
        missing = [c for c in rule.columns_used() if c not in have]
        if missing:
            warnings.append(f"{rule.path}: the data has no column {', '.join(repr(m) for m in missing)}")
            continue
        unknown = [t.op for t in rule.transforms if t.op not in TRANSFORMS]
        if unknown:
            warnings.append(f"{rule.path}: unknown transform {', '.join(unknown)} left out")
            rule = rule.model_copy(update={"transforms": [t for t in rule.transforms if t.op in TRANSFORMS]})
        kept.append(rule)
    used = {c for r in kept for c in r.columns_used()}
    unmapped = [c for c in columns if c not in used]
    return mapping.model_copy(update={"rules": kept, "unmapped_columns": unmapped, "columns": list(columns)}), warnings


def direct_mapping(schema: dict, columns: list[str]) -> DataMapping:
    """Rules for the columns that are named by a schema path (``applicant.name``, ``refs[0].phone``)."""
    paths = target_paths(schema)
    rules = []
    for col in columns:
        generic, idx = _generic(col)
        if generic in paths and generic.count("[]") == len(idx):
            rules.append(Rule(path=col, column=col))
    used = {r.column for r in rules}
    return DataMapping(columns=list(columns), rules=rules, unmapped_columns=[c for c in columns if c not in used], source="direct")


# ---------------------------------------------------------------- applying

def _coerce(value: Any, sp: SchemaPath) -> Any:
    if sp.type == "boolean":
        return _bool(value)
    if sp.type in ("integer", "number"):
        return _number(value, integer=sp.type == "integer")
    if sp.type == "date":
        return _date(value)
    if sp.type == "enum" and sp.enum:
        return _enum(value, sp.enum)
    if sp.type == "array":
        items = _list(value) if not isinstance(value, list) else value
        if isinstance(items, list) and sp.enum:
            items = [_enum(i, sp.enum) for i in items]
        return items
    if sp.type == "string" and not isinstance(value, str):
        return _text(value)
    return value


def _enum(value: Any, allowed: list[str]) -> Any:
    key = _text(value).strip().casefold()
    for a in allowed:
        if a.casefold() == key:
            return a
    if isinstance(value, bool) and _text(value).lower() in allowed:
        return _text(value).lower()
    return value


def _set(record: dict, path: str, value: Any) -> None:
    generic, idx = _generic(path)
    segments = parse_path(generic)
    node: Any = record
    indices = iter(idx)
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg is ARRAY_ITEM:
            continue
        nxt = segments[i + 1] if not last else None
        if last:
            node[seg] = value
            return
        if nxt is ARRAY_ITEM:
            items = node.setdefault(seg, [])
            n = next(indices)
            while len(items) <= n:
                items.append({})
            node = items[n]
        else:
            node = node.setdefault(seg, {})


def apply_mapping(mapping: DataMapping, schema: dict, row: dict[str, Any]) -> dict:
    """One row as a record of ``schema``. Rules for paths the schema lacks are skipped (``check_mapping`` reports them)."""
    paths = target_paths(schema)
    record: dict = {}
    for rule in mapping.rules:
        sp = paths.get(_generic(rule.path)[0])
        if sp is None:
            continue
        if rule.column is not None:
            value = row.get(rule.column)
        elif rule.template is not None:
            value = _fill_template(rule.template, row)
        else:
            value = rule.value
        value = _run(rule.transforms, value)
        if _empty(value):
            continue
        _set(record, rule.path, _coerce(value, sp))
    _compact_lists(record)
    return record


def records_from(mapping: DataMapping, schema: dict, table: Table) -> list[dict]:
    return [apply_mapping(mapping, schema, row) for row in table.rows]


def _fill_template(template: str, row: dict[str, Any]) -> str | None:
    found = False

    def sub(m: re.Match) -> str:
        nonlocal found
        v = row.get(m.group(1))
        if _empty(v):
            return ""
        found = True
        return _text(v)

    text = _PLACEHOLDER.sub(sub, template)
    if not found:
        return None
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,;])", r"\1", text)
    text = re.sub(r"([,;])(\s*[,;])+", r"\1", text)
    return text.strip(" ,;-/") or None


def _compact_lists(node: Any) -> None:
    """Drop the empty items a sparse index left in a list of records (``refs[2]`` set without ``refs[1]``)."""
    if isinstance(node, dict):
        for key, v in list(node.items()):
            if isinstance(v, list):
                node[key] = [x for x in v if x != {}]
            _compact_lists(node[key])
    elif isinstance(node, list):
        for x in node:
            _compact_lists(x)
