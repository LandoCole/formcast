"""Edits to an extraction template, made by a person on the setup screen and applied with no model call.

Each edit is a small pydantic model naming a concrete schema path (``investor.tax_id``, ``owners[2].name``) and one
change to it, discriminated by ``op`` so the workbench can send them as plain JSON::

    {"op": "set_field", "path": "investor.dob", "title": "Born", "type": "date", "required": true}
    {"op": "set_box", "path": "investor.tax_id", "page": 0, "rect": [100, 100, 200, 114]}
    {"op": "set_box", "path": "investor.tax_id", "page": 0, "rect": [100, 114, 200, 128], "sample": 4}
    {"op": "set_option_box", "path": "investor.type", "answer": "trust", "page": 0, "rect": [60, 200, 72, 212]}
    {"op": "set_labels", "path": "investor.tax_id", "labels": ["TIN"], "side": "below"}
    {"op": "set_clean", "path": "investor.dob", "clean": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]}
    {"op": "rename_field", "path": "investor.tax_id", "name": "tin"}
    {"op": "set_category", "path": "tax_id", "category": "Investor"}
    {"op": "mark_absent", "path": "wire.bank"}     (and "restore", "remove_field")

:func:`apply_edit` gives back a validated copy and leaves the template it was given alone. The schema edits
(``set_field``, ``rename_field``, ``set_category``, ``remove_field``) go to the one property schema every item path of a repeated group shares, so
editing ``owners[2].percent`` edits ``owners[0].percent`` ... ``owners[3].percent`` alike. The reader edits change
one strategy of one path and keep the order of the others (a widget read, then boxes, then labels, as the template
was built), because the first strategy that finds something wins.

A box's ``rect`` is in the layout's place, the frame the template stores boxes in, unless the edit names the
``sample`` it was drawn on: then it is where the box is on that sample's page (the frame of
:func:`~formcast.extract.proof.sample_marks`), and :func:`apply_edit`, given the sample PDFs, moves it into the
layout's place by that page's offset, as ``resolve`` does a box the model took from a sample.

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
    RESERVED_NAMES,
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
from formcast.extract.layout import page_fit
from formcast.extract.names import field_key
from formcast.extract.page import DocText
from formcast.extract.proof import _check_samples
from formcast.geometry import Rect
from formcast.schema import ARRAY_ITEM, REASON_KEY, flatten_schema, parse_path

#: The reason recorded on a field whose level a person changed, when the edit gives none.
SET_BY_YOU = "Set by you"

#: A name a field can have: one segment of a path, with nothing a path reads as its own syntax.
_FIELD_NAME = re.compile(r"[^\s.\[\]\"\\]+")

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

    ``required`` adds the field to (or takes it out of) the ``required`` list of the object it is in, and records
    why on the field (:data:`~formcast.schema.REASON_KEY`): ``required_reason`` when given, else "Set by you".
    Setting a ``type`` replaces the field's type and its ``format`` (only ``date`` has one). While the template's
    names follow its titles (:attr:`~formcast.extract.model.SetupInfo.name_follows_title`), a new ``title`` also gives
    the field a new name, as :class:`RenameField` does.
    """

    op: Literal["set_field"] = "set_field"
    path: str
    title: str | None = None
    description: str | None = None
    type: Literal["text", "number", "integer", "date", "yes/no"] | None = None
    required: bool | None = None
    required_reason: str | None = None
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
    """Read the words inside ``rect`` on ``page``: replaces the path's box, or adds one right after its widget read.

    Without ``sample``, ``rect`` is in the layout's place (the frame the template stores boxes in). With ``sample``
    (a position in the template's samples) it is where the box is on that sample's page, as
    :func:`~formcast.extract.proof.sample_marks` gives the marks a person draws on; :func:`apply_edit` moves it into
    the layout's place.
    """

    op: Literal["set_box"] = "set_box"
    path: str
    page: int = Field(ge=0)
    rect: Rect
    sample: int | None = Field(default=None, ge=0)


class SetOptionBox(_Edit):
    """Set where one answer's check mark is drawn, in the path's page-rect choice read (added when missing).

    ``rect`` is in the layout's place, or on the page of ``sample`` when it is given, as for :class:`SetBox`.
    """

    op: Literal["set_option_box"] = "set_option_box"
    path: str
    answer: str
    page: int = Field(ge=0)
    rect: Rect
    sample: int | None = Field(default=None, ge=0)


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


class RenameField(_Edit):
    """Give the field a new ``name``, the last part of its path: ``investor.tax_id`` named ``tin`` is ``investor.tin``.

    The field keeps its place, level, readers and the samples' readings. On a repeated group this renames it in every
    item. A field's export column is made from its path, so this is what clears a clash with a document column.
    """

    op: Literal["rename_field"] = "rename_field"
    path: str
    name: str


class SetCategory(_Edit):
    """Move a field into ``category``, by the category's title or key, made when there is none; ``""`` moves it out.

    The field keeps its name, level, readers and readings. A category left with no fields goes. A field of a repeated
    group stays in its group, and only a template whose names still follow its titles can move a field.
    """

    op: Literal["set_category"] = "set_category"
    path: str
    category: str


Edit = Annotated[
    SetField | SetBox | SetOptionBox | SetLabels | SetClean | MarkAbsent | Restore | RemoveField | RenameField | SetCategory,
    Field(discriminator="op"),
]


def apply_edit(template: ExtractionTemplate, edit: Edit, *, pdfs: list[bytes] | None = None) -> ExtractionTemplate:
    """The template with ``edit`` made, validated; ``template`` itself is not changed.

    A box drawn on a sample (``set_box`` or ``set_option_box`` with ``sample``) needs ``pdfs``, the template's sample
    PDFs in its order (as :func:`~formcast.extract.proof.prove` takes them): the box is moved up by that sample
    page's offset (:meth:`~formcast.extract.layout.PageFit.in_layout`, as ``resolve`` moves a box taken from a
    sample), so a box drawn on a copy pushed down the page is stored where it is on the layout. No model is called.

    Raises:
        FormcastError: ``edit.path`` is not a field of the schema, a ``set_option_box`` answer is not one of the
            field's answers, a name a path cannot hold, that is reserved
            (:data:`~formcast.extract.model.RESERVED_NAMES`, at the top level) or that another field beside it has, a
            title with nothing to make a name from, a category that cannot take the field, or the field's schema is
            built with ``$ref``/``anyOf`` and cannot be edited in place;
            for a box drawn on a sample, no ``pdfs``, ``pdfs`` that are not the template's samples, or a sample or
            page the template's samples do not have.
        pydantic.ValidationError: the edited template is not valid.
    """
    if edit.path not in concrete_paths(template.schema_):
        raise FormcastError(f"no field {edit.path}")
    if isinstance(edit, (SetBox, SetOptionBox)) and edit.sample is not None:
        rect = _in_layout(template, edit.sample, edit.page, edit.rect, pdfs)
        edit = edit.model_copy(update={"rect": rect, "sample": None})
    edited = template.model_copy(deep=True)
    _HANDLERS[type(edit)](edited, edit)
    return ExtractionTemplate.model_validate(edited.model_dump(by_alias=True))


def _in_layout(template: ExtractionTemplate, sample: int, page: int, rect: Rect, pdfs: list[bytes] | None) -> Rect:
    """``rect`` on the page of the template's ``sample`` moved into the layout's place (see :func:`apply_edit`)."""
    if pdfs is None:
        raise FormcastError(f"a box drawn on sample {sample} needs the template's sample PDFs")
    _check_samples(template, pdfs)
    if sample >= len(pdfs):
        raise FormcastError(f"the template has no sample {sample}")
    with DocText(pdfs[sample]) as doc:
        if page >= doc.page_count:
            raise FormcastError(f"sample {sample} has no page {page}")
        return page_fit(template.layout, doc, page).in_layout(rect)


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
    if edit.required_reason is not None:
        node[REASON_KEY] = edit.required_reason
    elif edit.required is not None:
        node[REASON_KEY] = SET_BY_YOU
    if edit.title is not None and t.setup is not None and t.setup.name_follows_title:
        name = field_key(edit.title)
        if not name:
            raise FormcastError("a title needs a letter or number to make the field's name from")
        _rename(t, edit.path, name)


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


def _check_free(name: str, parent: dict, top_level: bool) -> None:
    """Refuse ``name`` where it cannot go: reserved at the top level, or taken in ``parent``."""
    if top_level and name.lower() in RESERVED_NAMES:
        reserved = ", ".join(RESERVED_NAMES)
        raise FormcastError(f"{name!r} is reserved for the document's own columns; these names can't be used here: {reserved}")
    if name in (parent.get("properties") or {}):
        raise FormcastError(f"there is already a field named {name!r} here")


def _repath(t: ExtractionTemplate, concrete: dict[str, str], generic: dict[str, str]) -> None:
    """Put every part of ``t`` that names a path at its new one, given each old path's."""
    t.readers = {concrete.get(p, p): r for p, r in t.readers.items()}
    t.absent = [concrete.get(p, p) for p in t.absent]
    for sample in t.samples:
        sample.opinion = {concrete.get(p, p): v for p, v in sample.opinion.items()}
    if t.setup is not None:
        lines = {line: [generic.get(p, p) for p in paths] for line, paths in t.setup.line_paths.items()}
        t.setup = t.setup.model_copy(update={"line_paths": lines})


def _rename(t: ExtractionTemplate, path: str, name: str) -> None:
    _, steps = _locate(t.schema_, path)
    parent, old = steps[-1]
    if name == old:
        return
    _check_free(name, parent, top_level=len(steps) == 1)
    concrete = concrete_paths(t.schema_)
    generic = [sp.path for sp in flatten_schema(t.schema_)]
    parent["properties"] = {name if k == old else k: v for k, v in parent["properties"].items()}
    if old in parent.get("required", []):
        parent["required"] = [name if n == old else n for n in parent["required"]]
    # the field kept its place, so the paths come out in the same order
    _repath(
        t,
        dict(zip(concrete, concrete_paths(t.schema_), strict=True)),
        dict(zip(generic, (sp.path for sp in flatten_schema(t.schema_)), strict=True)),
    )


def _rename_field(t: ExtractionTemplate, edit: RenameField) -> None:
    if not _FIELD_NAME.fullmatch(edit.name):
        raise FormcastError("a field's name is letters, numbers and underscores, with no spaces, dots or brackets")
    _rename(t, edit.path, edit.name)


def _category_key(schema: dict, category: str) -> str:
    """The key of the category with this title or key, else the one a new category would have."""
    wanted = category.strip().casefold()
    for key, node in (schema.get("properties") or {}).items():
        if key.casefold() == wanted or str(node.get("title") or "").strip().casefold() == wanted:
            return key
    key = field_key(category)
    if not key:
        raise FormcastError("a category needs a letter or number to make the field's name from")
    return key


def _set_category(t: ExtractionTemplate, edit: SetCategory) -> None:
    if t.setup is None or not t.setup.name_follows_title:
        raise FormcastError("a saved template's fields keep their category, because it is part of their export column")
    if "[" in edit.path:
        raise FormcastError("a field of a repeated group stays in its group")
    node, steps = _locate(t.schema_, edit.path)
    parent, name = steps[-1]
    if not edit.category.strip():
        target, key = t.schema_, ""
    else:
        key = _category_key(t.schema_, edit.category)
        category = t.schema_.setdefault("properties", {}).get(key)
        if category is None:
            _check_free(key, t.schema_, top_level=True)
            category = t.schema_["properties"][key] = {"type": "object", "title": edit.category.strip(), "properties": {}}
        if category.get("type") != "object":
            raise FormcastError(f"{key!r} is a field or a repeated group, so this field cannot move into it")
        target = category
    if target is parent:
        return
    _check_free(name, target, top_level=not key)
    required = name in parent.get("required", [])
    for i in range(len(steps) - 1, -1, -1):
        holder, step = steps[i]
        del holder["properties"][step]
        _set_required(holder, step, False)
        if holder["properties"] or i == 0 or holder is target:
            break  # a category left with no fields goes too
    target["properties"][name] = node
    if required:
        _set_required(target, name, True)
    new = f"{key}.{name}" if key else name
    _repath(t, {edit.path: new}, {edit.path: new})


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
    RenameField: _rename_field,
    SetCategory: _set_category,
}
