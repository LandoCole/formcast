"""Flatten a JSON Schema into addressable field paths.

Template fields reference a user's data by *path string*. This module turns a
JSON Schema into the list of valid paths (:func:`flatten_schema`) and defines
the path syntax (:func:`parse_path` / :func:`format_path`).

Path syntax
-----------
* ``a.b``                object property ``b`` of ``a``
* ``a[]``                every item of array ``a``; ``a[].b`` is property ``b`` of each item
* ``a["first name"]``    property names that are empty or contain ``.``, ``[``, ``]``,
                         ``"``, ``\\`` or whitespace use the quoted form; inside the
                         quotes ``\\"`` and ``\\\\`` escape ``"`` and ``\\``
* the quoted form is also valid for the first segment: ``["first name"].x``

Schema interpretation
---------------------
* ``format: date`` becomes type ``date``; any ``enum`` becomes type ``enum``
  (non-string enum values are stringified, JSON style: ``true``, ``1``).
* ``integer`` and ``number`` stay distinct.
* Nullable types are unwrapped: ``{"type": ["string", "null"]}`` is a string and
  ``anyOf``/``oneOf`` with a single non-null branch is that branch. A union with
  several non-null branches is represented by its FIRST non-null branch.
* ``allOf`` parts are merged (properties are unioned, later keys win).
* Local ``$ref``s (``#/...``) are resolved. Remote refs and dangling refs raise
  :class:`~formcast.errors.FormcastError`. A recursive ref is emitted but not
  expanded again (no infinite paths).
* An object emits its own path (type ``object``) and one path per property. An
  array emits its own path (type ``array``, ``item_type`` set). Arrays of
  objects additionally emit one path per column under ``name[]``, each with
  ``in_table`` set to the nearest enclosing array-of-objects path.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import unquote

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel

from formcast.errors import FormcastError

__all__ = [
    "ARRAY_ITEM",
    "SchemaPath",
    "flatten_schema",
    "format_path",
    "is_array_item",
    "parse_path",
    "resolve",
    "validate_data",
]

PathType = Literal["string", "integer", "number", "boolean", "date", "enum", "array", "object"]

_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


class SchemaPath(BaseModel):
    """One addressable field in the user's schema."""

    path: str
    type: PathType
    description: str = ""
    enum: list[str] | None = None
    item_type: str | None = None
    in_table: str | None = None


# ---------------------------------------------------------------- path syntax


class _ArrayItem(str):
    """The ``[]`` path segment. A ``str`` subclass so segments stay a ``list[str]``."""

    __slots__ = ()


#: Segment meaning "every item of the array". A property that is literally named
#: ``"[]"`` is a plain ``str``, so compare with ``is`` / :func:`is_array_item`, not ``==``.
ARRAY_ITEM: str = _ArrayItem("[]")


def is_array_item(segment: str) -> bool:
    """True when ``segment`` is the array-item marker (not a property named ``"[]"``)."""
    return isinstance(segment, _ArrayItem)


_SPECIAL = set('.[]"\\')


def _is_bare(name: str) -> bool:
    return bool(name) and not any(c in _SPECIAL or c.isspace() for c in name)


def _format_segment(segment: str, *, first: bool) -> str:
    if is_array_item(segment):
        return "[]"
    if _is_bare(segment):
        return segment if first else "." + segment
    escaped = segment.replace("\\", "\\\\").replace('"', '\\"')
    return f'["{escaped}"]'


def format_path(segments: list[str]) -> str:
    """Join segments into a path string (inverse of :func:`parse_path`)."""
    return "".join(_format_segment(s, first=i == 0) for i, s in enumerate(segments))


def parse_path(path: str) -> list[str]:
    """Split a path string into segments.

    Property names are returned as plain strings and array items as
    :data:`ARRAY_ITEM` (``"[]"``). Raises ``ValueError`` on malformed paths.
    """
    if not path:
        raise ValueError("empty path")
    segments: list[str] = []
    i, n = 0, len(path)

    def bare(start: int) -> int:
        j = start
        while j < n and path[j] not in _SPECIAL and not path[j].isspace():
            j += 1
        if j == start:
            raise ValueError(f"expected a property name at position {start} in {path!r}")
        segments.append(path[start:j])
        return j

    def bracket(start: int) -> int:  # path[start] == "["
        if path.startswith("[]", start):
            segments.append(ARRAY_ITEM)
            return start + 2
        if not path.startswith('["', start):
            raise ValueError(f"expected [] or [\"name\"] at position {start} in {path!r}")
        j = start + 2
        chars: list[str] = []
        while j < n:
            c = path[j]
            if c == "\\":
                if j + 1 >= n or path[j + 1] not in '"\\':
                    raise ValueError(f"bad escape at position {j} in {path!r}")
                chars.append(path[j + 1])
                j += 2
            elif c == '"':
                if not path.startswith("]", j + 1):
                    raise ValueError(f'expected "]" after closing quote in {path!r}')
                segments.append("".join(chars))
                return j + 2
            else:
                chars.append(c)
                j += 1
        raise ValueError(f"unterminated quoted name in {path!r}")

    i = bracket(i) if path[0] == "[" else bare(0)
    while i < n:
        if path[i] == ".":
            i = bare(i + 1)
        elif path[i] == "[":
            i = bracket(i)
        else:
            raise ValueError(f"unexpected {path[i]!r} at position {i} in {path!r}")
    return segments


# ------------------------------------------------------------------- flatten


def _merge(a: dict, b: dict) -> dict:
    out = {**a, **b}
    if isinstance(a.get("properties"), dict) and isinstance(b.get("properties"), dict):
        out["properties"] = {**a["properties"], **b["properties"]}
    return out


def _lookup_ref(ref: str, root: dict) -> dict:
    if not ref.startswith("#"):
        raise FormcastError(f"unsupported $ref {ref!r}: only local '#/...' refs are supported")
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        raise FormcastError(f"unsupported $ref {ref!r}: only JSON-pointer refs are supported")
    node: Any = root
    for raw in ref[2:].split("/"):
        key = unquote(raw).replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            raise FormcastError(f"unresolvable $ref {ref!r}")
    if not isinstance(node, dict):
        raise FormcastError(f"$ref {ref!r} does not point at a schema object")
    return node


def _single_type(schema: dict) -> str | None:
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        return non_null[0] if non_null else "null"
    return t if isinstance(t, str) else None


def _infer_type(schema: dict) -> str | None:
    t = _single_type(schema)
    if t is not None:
        return t
    if isinstance(schema.get("properties"), dict):
        return "object"
    if "items" in schema:
        return "array"
    return None


def _normalize(node: Any, root: dict, stack: tuple[str, ...]) -> tuple[dict, tuple[str, ...]]:
    """Resolve ``$ref``/``allOf``/``anyOf``/``oneOf`` and nullable type lists.

    Returns a plain schema dict (with a single ``type`` when one is knowable)
    and the ref stack to use for its children.
    """
    if not isinstance(node, dict):
        return {}, stack
    result: dict = {}
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = _lookup_ref(ref, root)
        if ref in stack:
            # Recursive reference: keep the node's type but do not expand children.
            shallow = {k: v for k, v in target.items() if k not in ("properties", "items")}
            shallow["type"] = _infer_type(target) or "object"
            result = shallow
        else:
            stack = stack + (ref,)
            result, stack = _normalize(target, root, stack)
    for part in node.get("allOf") or []:
        sub, stack = _normalize(part, root, stack)
        result = _merge(result, sub)
    union = node.get("anyOf") or node.get("oneOf")
    if isinstance(union, list) and union:
        branches = []
        for b in union:
            sub, stack = _normalize(b, root, stack)
            branches.append(sub)
        non_null = [b for b in branches if _single_type(b) != "null"]
        result = _merge(result, non_null[0] if non_null else {"type": "string"})
    own = {k: v for k, v in node.items() if k not in ("$ref", "allOf", "anyOf", "oneOf")}
    result = _merge(result, own)
    t = _infer_type(result)
    if t is not None:
        result = {**result, "type": t}
    return result, stack


def _enum_values(schema: dict) -> list[str] | None:
    values = schema.get("enum")
    if not isinstance(values, list):
        return None
    return [v if isinstance(v, str) else json.dumps(v) for v in values]


def _path_type(schema: dict) -> PathType:
    if isinstance(schema.get("enum"), list) and schema.get("type") not in ("object", "array"):
        return "enum"
    t = schema.get("type")
    if t == "string" and schema.get("format") == "date":
        return "date"
    if t in _SCALAR_TYPES or t in ("object", "array"):
        return t  # type: ignore[return-value]
    return "string"  # untyped, "null" or unknown


def flatten_schema(schema: dict) -> list[SchemaPath]:
    """Flatten ``schema`` into field paths, parents before children, in schema order."""
    root, stack = _normalize(schema, schema, ())
    if _path_type(root) != "object":
        raise FormcastError("schema root must be an object schema")
    out: list[SchemaPath] = []

    def children(props: Any, segs: list[str], stack: tuple[str, ...], in_table: str | None) -> None:
        if not isinstance(props, dict):
            return
        for name, sub in props.items():
            node, child_stack = _normalize(sub, schema, stack)
            visit(segs + [name], node, child_stack, in_table)

    def visit(segs: list[str], node: dict, stack: tuple[str, ...], in_table: str | None) -> None:
        path = format_path(segs)
        ptype = _path_type(node)
        entry = SchemaPath(
            path=path,
            type=ptype,
            description=node["description"] if isinstance(node.get("description"), str) else "",
            enum=_enum_values(node) if ptype == "enum" else None,
            in_table=in_table,
        )
        out.append(entry)
        if ptype == "object":
            children(node.get("properties"), segs, stack, in_table)
        elif ptype == "array":
            items, item_stack = _normalize(node.get("items"), schema, stack)
            item_type = _path_type(items) if node.get("items") is not None else None
            entry.item_type = item_type
            if item_type == "enum":
                entry.enum = _enum_values(items)
            if item_type == "object":
                children(items.get("properties"), segs + [ARRAY_ITEM], item_stack, path)
            elif item_type == "array":
                visit(segs + [ARRAY_ITEM], items, item_stack, in_table)

    children(root.get("properties"), [], stack, None)
    return out


def resolve(schema_paths: list[SchemaPath], path: str) -> SchemaPath:
    """Return the entry whose path is exactly ``path``; raise ``KeyError`` if none."""
    for sp in schema_paths:
        if sp.path == path:
            return sp
    raise KeyError(path)


# ------------------------------------------------------------------ validate


def _data_location(parts: list[Any]) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else _format_segment(str(p), first=not out)
    return out


def validate_data(schema: dict, data: Any) -> list[str]:
    """Validate ``data`` against ``schema``; return error messages (empty when valid).

    Each message is ``"<location>: <jsonschema message>"`` where the location uses
    path syntax with concrete indices (``medications[0].dose``); errors at the
    document root have no location prefix. ``format: date`` is checked.
    """
    cls = validator_for(schema)
    try:
        cls.check_schema(schema)
    except SchemaError as e:
        raise FormcastError(f"invalid JSON Schema: {e.message}") from e
    validator = cls(schema, format_checker=cls.FORMAT_CHECKER)
    found = [(_data_location(list(e.absolute_path)), e.message) for e in validator.iter_errors(data)]
    found.sort()
    return [f"{loc}: {msg}" if loc else msg for loc, msg in found]
