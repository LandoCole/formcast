"""A field's name, made from its category and title, so nobody writes a schema name.

    field_key("Taxpayer ID")                     taxpayer_id
    derived_path("Investor", "Taxpayer ID")      investor.taxpayer_id
    derived_path("", "Taxpayer ID")              taxpayer_id

A category is a top-level object of the schema, or a repeated group; a field with no category is a top-level property.
:func:`normalized` rewrites a schema the model designed so its keys are made this way, with nothing nested deeper than
a category, and says where each field went. A title with nothing a key can be made from keeps the key it had.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Collection

from formcast.extract.model import RESERVED_NAMES
from formcast.schema import flatten_schema

_NOT_A_KEY = re.compile(r"[^a-z0-9]+")


def field_key(text: str) -> str:
    """``text`` as a key: lower-case snake_case with accents dropped; ``""`` when nothing is left of it."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    key = _NOT_A_KEY.sub("_", ascii_text.lower()).strip("_")
    return f"f_{key}" if key[:1].isdigit() else key


def derived_path(category: str, title: str) -> str:
    """The path of a field with this ``title`` in this ``category``; ``category`` may be blank."""
    return ".".join(key for key in (field_key(category), field_key(title)) if key)


def _unique(key: str, taken: set[str]) -> str:
    """``key``, or ``key_2``, ``key_3`` ... the first ``taken`` does not hold; it is then taken."""
    name, n = key, 1
    while name in taken:
        n += 1
        name = f"{key}_{n}"
    taken.add(name)
    return name


def _is_object(node: dict) -> bool:
    return node.get("type") == "object" and isinstance(node.get("properties"), dict)


def _is_group(node: dict) -> bool:
    return node.get("type") == "array" and isinstance(node.get("items"), dict) and _is_object(node["items"])


def _leaves(key: str, node: dict, defs: dict) -> list[str]:
    """The paths of ``node`` under the property ``key``; a ``$ref`` in it is looked up in ``defs``."""
    return [sp.path for sp in flatten_schema({"type": "object", "properties": {key: node}, "$defs": defs})]


def _keyed(old: str, node: dict, taken: set[str]) -> str:
    return _unique(field_key(str(node.get("title") or "")) or field_key(old) or old, taken)


def _fields(obj: dict, prefix: str, new_prefix: str, moved: dict[str, str], defs: dict) -> tuple[dict, list[str]]:
    """The properties and ``required`` list of the category ``obj``, with anything nested in it brought up into it."""
    props: dict[str, dict] = {}
    required: list[str] = []
    taken: set[str] = set()

    def add(old: str, node: dict, old_prefix: str, needed: bool) -> None:
        key = _keyed(old, node, taken)
        props[key] = node
        if needed:
            required.append(key)
        for leaf in _leaves(old, node, defs):
            moved[f"{old_prefix}{leaf}"] = f"{new_prefix}{key}{leaf[len(old):]}"

    def walk(node: dict, old_prefix: str) -> None:
        for old, child in node["properties"].items():
            if _is_object(child):
                walk(child, f"{old_prefix}{old}.")
            else:
                add(old, child, old_prefix, old in node.get("required", []))

    walk(obj, prefix)
    return props, required


def normalized(schema: dict, *, taken: Collection[str] = ()) -> tuple[dict, dict[str, str]]:
    """``schema`` with its keys made from its titles, and each old path (with ``[]``) to its new one.

    Two fields of one category with the same title become ``date`` and ``date_2``. Nothing is nested deeper than a
    category: a field further down comes up into its category, whose ``required`` then lists it if its own object did.
    ``taken`` are top-level names in use besides the reserved ones.
    """
    out = copy.deepcopy(schema)
    defs = out.get("$defs", {})
    used = {*RESERVED_NAMES, *taken}
    moved: dict[str, str] = {}
    properties: dict[str, dict] = {}
    required: list[str] = []
    for old, node in out.get("properties", {}).items():
        group, inner, suffix = None, None, ""
        if _is_object(node):
            group, inner = node, node
        elif _is_group(node):
            group, inner, suffix = node, node["items"], "[]"
        key = _keyed(old, node, used)
        if group is None:
            moved.update({leaf: f"{key}{leaf[len(old):]}" for leaf in _leaves(old, node, defs)})
        else:
            fields, needed = _fields(inner, f"{old}{suffix}.", f"{key}{suffix}.", moved, defs)
            inner["properties"] = fields
            if needed:
                inner["required"] = needed
            else:
                inner.pop("required", None)
        properties[key] = node
        if old in out.get("required", []):
            required.append(key)
    out["properties"] = properties
    if required:
        out["required"] = required
    else:
        out.pop("required", None)
    return out, moved
