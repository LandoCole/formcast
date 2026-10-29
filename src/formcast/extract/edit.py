"""Edits to an extraction template, made by a person on the setup screen and applied with no model call.

Each edit is a small pydantic model naming a concrete schema path (``investor.tax_id``, ``owners[2].name``) and one
change to it, discriminated by ``op`` so the workbench can send them as plain JSON::

    {"op": "set_field", "path": "investor.dob", "title": "Born", "type": "date", "required": true}
    {"op": "set_box", "path": "investor.tax_id", "page": 0, "rect": [100, 100, 200, 114]}
    {"op": "set_option_box", "path": "investor.type", "answer": "trust", "page": 0, "rect": [60, 200, 72, 212]}
    {"op": "set_labels", "path": "investor.tax_id", "labels": ["TIN"], "side": "below"}
    {"op": "set_clean", "path": "investor.dob", "clean": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]}
    {"op": "mark_absent", "path": "wire.bank"}     (and "restore", "remove_field")

:func:`apply_edit` gives back a validated copy and leaves the template it was given alone. The schema edits
(``set_field``, ``remove_field``) go to the one property schema every item path of a repeated group shares, so
editing ``owners[2].percent`` edits ``owners[0].percent`` ... ``owners[3].percent`` alike. The reader edits change
one strategy of one path and keep the order of the others (a widget read, then boxes, then labels, as the template
was built), because the first strategy that finds something wins.

A path marked absent has no reader (``mark_absent`` drops it), and a reader is never read on an absent path, so an
edit that gives a path a reader takes it out of ``absent``: the person has just said it is on the form.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from formcast.data.mapping import Transform
from formcast.errors import FormcastError
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    ExtractionTemplate,
    Reader,
    Strategy,
    WidgetRead,
    concrete_paths,
)
from formcast.geometry import Rect
from formcast.schema import ARRAY_ITEM, flatten_schema, parse_path

#: The JSON Schema each type a person can pick stands for. A date is a string with a ``format``.
_TYPES: dict[str, dict[str, str]] = {
    "text": {"type": "string"},
    "number": {"type": "number"},
    "integer": {"type": "integer"},
    "date": {"type": "string", "format": "date"},
    "yes/no": {"type": "boolean"},
}


class _Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetField(_Edit):
    """Change what the schema says about a field. A key left ``None`` is kept; ``pattern=""`` or ``enum=[]`` clears it.

    ``required`` adds the field to (or takes it out of) the ``required`` list of the object it is in. Setting a
    ``type`` replaces the field's type and its ``format`` (only ``date`` has one).
    """

    op: Literal["set_field"] = "set_field"
    path: str
    title: str | None = None
    description: str | None = None
    type: Literal["text", "number", "integer", "date", "yes/no"] | None = None
    required: bool | None = None
    pattern: str | None = None
    enum: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None

    @field_validator("pattern")
    @classmethod
    def _is_a_regex(cls, pattern: str | None) -> str | None:
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"not a regular expression: {e}") from e
        return pattern


class SetBox(_Edit):
    """Read the words inside ``rect`` on ``page``: replaces the path's box, or adds one right after its widget read."""

    op: Literal["set_box"] = "set_box"
    path: str
    page: int = Field(ge=0)
    rect: Rect


class SetOptionBox(_Edit):
    """Set where one answer's check mark is drawn, in the path's page-rect choice read (added when missing)."""

    op: Literal["set_option_box"] = "set_option_box"
    path: str
    answer: str
    page: int = Field(ge=0)
    rect: Rect


class SetLabels(_Edit):
    """Replace the printed labels the path is read beside (and the side, when given); ``[]`` removes the label read."""

    op: Literal["set_labels"] = "set_labels"
    path: str
    labels: list[str]
    side: Literal["right", "below"] | None = None

    @field_validator("labels")
    @classmethod
    def _not_blank(cls, labels: list[str]) -> list[str]:
        if any(not label.strip() for label in labels):
            raise ValueError("a label cannot be blank")
        return labels


class SetClean(_Edit):
    """Replace the transforms that clean the path's raw text."""

    op: Literal["set_clean"] = "set_clean"
    path: str
    clean: list[Transform]


class MarkAbsent(_Edit):
    """Say the field is not on this form: it is never read, and its reader is dropped."""

    op: Literal["mark_absent"] = "mark_absent"
    path: str


class Restore(_Edit):
    """Say the field is on the form after all. It has no reader until the person gives it one."""

    op: Literal["restore"] = "restore"
    path: str


class RemoveField(_Edit):
    """Take the field out of the schema, with its readers and the samples' readings of it.

    A group left with no fields goes too. On a repeated group this removes the field from every item.
    """

    op: Literal["remove_field"] = "remove_field"
    path: str


Edit = Annotated[
    SetField | SetBox | SetOptionBox | SetLabels | SetClean | MarkAbsent | Restore | RemoveField,
    Field(discriminator="op"),
]


def apply_edit(template: ExtractionTemplate, edit: Edit) -> ExtractionTemplate:
    """The template with ``edit`` made, validated; ``template`` itself is not changed.

    Raises:
        FormcastError: ``edit.path`` is not a field of the schema, a ``set_option_box`` answer is not one of the
            field's answers, or the field's schema is built with ``$ref``/``anyOf`` and cannot be edited in place.
        pydantic.ValidationError: the edited template is not valid.
    """
    if edit.path not in concrete_paths(template.schema_):
        raise FormcastError(f"no field {edit.path}")
    edited = template.model_copy(deep=True)
    _HANDLERS[type(edit)](edited, edit)
    return ExtractionTemplate.model_validate(edited.model_dump(by_alias=True))


# ------------------------------------------------------------------- the schema


def _locate(schema: dict, path: str) -> tuple[dict, list[tuple[dict, str]]]:
    """The property schema of a concrete ``path``, and the ``(object schema, property name)`` of each step to it.

    An item path walks the ``items`` of its group, so every item of the group has the same property schema.
    """
    node = schema
    steps: list[tuple[dict, str]] = []
    for seg in parse_path(re.sub(r"\[\d+\]", "[]", path)):
        if seg is ARRAY_ITEM:
            child = node.get("items")
        else:
            child = (node.get("properties") or {}).get(seg)
            steps.append((node, seg))
        if not isinstance(child, dict):
            raise FormcastError(f"{path} cannot be edited: its schema is not written out in place (a $ref or anyOf)")
        node = child
    return node, steps


def _set_required(obj: dict, name: str, required: bool) -> None:
    names = [n for n in obj.get("required", []) if n != name]
    if required:
        names.append(name)
    if names:
        obj["required"] = names
    else:
        obj.pop("required", None)


def _set_field(t: ExtractionTemplate, edit: SetField) -> None:
    node, steps = _locate(t.schema_, edit.path)
    if edit.title is not None:
        node["title"] = edit.title
    if edit.description is not None:
        node["description"] = edit.description
    if edit.type is not None:
        node.pop("format", None)
        node.update(_TYPES[edit.type])
    for key, value in (("pattern", edit.pattern), ("enum", edit.enum)):
        if value:
            node[key] = value
        elif value is not None:  # "" or []: clear it
            node.pop(key, None)
    if edit.minimum is not None:
        node["minimum"] = edit.minimum
    if edit.maximum is not None:
        node["maximum"] = edit.maximum
    if edit.required is not None:
        parent, name = steps[-1]
        _set_required(parent, name, edit.required)


def _remove_field(t: ExtractionTemplate, edit: RemoveField) -> None:
    before = {sp.path for sp in flatten_schema(t.schema_)}
    _, steps = _locate(t.schema_, edit.path)
    for i in range(len(steps) - 1, -1, -1):
        parent, name = steps[i]
        del parent["properties"][name]
        _set_required(parent, name, False)
        if parent["properties"] or i == 0:
            break  # a group left with no fields goes too, up to the root
    known = set(concrete_paths(t.schema_))
    t.readers = {p: r for p, r in t.readers.items() if p in known}
    t.absent = [p for p in t.absent if p in known]
    for sample in t.samples:
        sample.opinion = {p: v for p, v in sample.opinion.items() if p in known}
    if t.setup is not None:
        after = {sp.path for sp in flatten_schema(t.schema_)}
        gone = before - after
        lines = {line: [p for p in paths if p not in gone] for line, paths in t.setup.line_paths.items()}
        t.setup = t.setup.model_copy(update={"line_paths": lines})


# ------------------------------------------------------------------- the readers


def _give_reader(
    t: ExtractionTemplate, path: str, strategies: list[Strategy], clean: list[Transform] | None = None
) -> None:
    """Put ``strategies`` (and ``clean``, else the reader's own) on ``path``, which is then on the form."""
    if clean is None:
        clean = t.readers[path].clean if path in t.readers else []
    t.readers[path] = Reader(strategies=strategies, clean=clean)
    t.absent = [p for p in t.absent if p != path]


def _strategies(t: ExtractionTemplate, path: str) -> list[Strategy]:
    reader = t.readers.get(path)
    return list(reader.strategies) if reader else []


def _first(strategies: list[Strategy], pick: Callable[[Strategy], bool]) -> int | None:
    return next((i for i, s in enumerate(strategies) if pick(s)), None)


def _set_box(t: ExtractionTemplate, edit: SetBox) -> None:
    strategies = _strategies(t, edit.path)
    box = BoxRead(page=edit.page, rect=edit.rect)
    at = _first(strategies, lambda s: isinstance(s, BoxRead))
    if at is not None:
        strategies[at] = box
    else:
        widget = _first(strategies, lambda s: isinstance(s, WidgetRead))
        strategies.insert(0 if widget is None else widget + 1, box)
    _give_reader(t, edit.path, strategies)


def _drawn(strategy: Strategy) -> bool:
    return isinstance(strategy, ChoiceRead) and not any(m.widget for m in strategy.options.values())


def _set_option_box(t: ExtractionTemplate, edit: SetOptionBox) -> None:
    node, _ = _locate(t.schema_, edit.path)
    strategies = _strategies(t, edit.path)
    answers = {str(v) for v in node.get("enum") or []}
    for s in strategies:
        if isinstance(s, ChoiceRead):
            answers.update(s.options)
    if edit.answer not in answers:
        raise FormcastError(f"{edit.path} has no answer {edit.answer!r}")
    mark = ChoiceMark(page=edit.page, rect=edit.rect)
    at = _first(strategies, _drawn)
    if at is not None:
        strategies[at] = ChoiceRead(options={**strategies[at].options, edit.answer: mark})
    else:
        # after the form-field reads (a widget read, or a choice read of widget marks), which are tried first
        last = max((i for i, s in enumerate(strategies) if isinstance(s, (WidgetRead, ChoiceRead))), default=-1)
        strategies.insert(last + 1, ChoiceRead(options={edit.answer: mark}))
    _give_reader(t, edit.path, strategies)


def _set_labels(t: ExtractionTemplate, edit: SetLabels) -> None:
    strategies = _strategies(t, edit.path)
    if not edit.labels:
        if any(isinstance(s, AnchorRead) for s in strategies):
            _give_reader(t, edit.path, [s for s in strategies if not isinstance(s, AnchorRead)])
        return
    at = _first(strategies, lambda s: isinstance(s, AnchorRead))
    if at is None:
        strategies.append(AnchorRead(labels=edit.labels, side=edit.side or "right"))
    else:
        update = {"labels": edit.labels} | ({"side": edit.side} if edit.side else {})
        strategies[at] = strategies[at].model_copy(update=update)
    _give_reader(t, edit.path, strategies)


def _set_clean(t: ExtractionTemplate, edit: SetClean) -> None:
    _give_reader(t, edit.path, _strategies(t, edit.path), clean=edit.clean)


def _mark_absent(t: ExtractionTemplate, edit: MarkAbsent) -> None:
    t.readers.pop(edit.path, None)
    if edit.path not in t.absent:
        t.absent.append(edit.path)


def _restore(t: ExtractionTemplate, edit: Restore) -> None:
    t.absent = [p for p in t.absent if p != edit.path]


_HANDLERS = {
    SetField: _set_field,
    SetBox: _set_box,
    SetOptionBox: _set_option_box,
    SetLabels: _set_labels,
    SetClean: _set_clean,
    MarkAbsent: _mark_absent,
    Restore: _restore,
    RemoveField: _remove_field,
}
