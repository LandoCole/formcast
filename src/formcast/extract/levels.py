"""Whether a field of an extraction template is required or optional: the template's level for it.

    required_paths(schema) -> set[str]

A field is required exactly when its own object names it in ``required`` (inside an item of a repeated group, the
item schema's ``required``); a parent group's level is not inherited. Every other field is optional. The level is the
template's, the same for every document it reads. A run keeps each template's required paths in ``run.json`` (see
:mod:`formcast.extract.batch`), and the AI pass asks only about required fields.
"""

from __future__ import annotations

import re
from typing import Literal

from formcast.extract.model import concrete_paths
from formcast.schema import _normalize, is_array_item, parse_path

__all__ = ["LEVELS", "Level", "required_paths"]

#: The two levels of a field, and of a problem on it.
Level = Literal["required", "optional"]
LEVELS: tuple[Level, ...] = ("required", "optional")
_ITEM_NUMBER = re.compile(r"\[\d+\]")


def required_paths(schema: dict) -> set[str]:
    """The concrete leaf paths of ``schema`` named in their parent's ``required`` (inside an item of a repeated
    group, in the item schema's ``required``): ``investor.tax_id``, ``owners[0].name`` and ``owners[1].name``."""
    memo: dict[str, bool] = {}

    def required(generic: str) -> bool:
        if generic not in memo:
            memo[generic] = _is_required(schema, generic)
        return memo[generic]

    return {p for p in concrete_paths(schema) if required(_ITEM_NUMBER.sub("[]", p))}


def _is_required(schema: dict, generic: str) -> bool:
    node, _ = _normalize(schema, schema, ())
    required = False
    for segment in parse_path(generic):
        if is_array_item(segment):
            node, _ = _normalize(node.get("items"), schema, ())
            required = False
        else:
            required = segment in (node.get("required") or [])
            node, _ = _normalize((node.get("properties") or {}).get(segment), schema, ())
    return required
