"""The models of PDF extraction: an extraction template and the result of reading one document with it.

An :class:`ExtractionTemplate` is plain data, made once (by hand, or by the setup flow from sample documents)
and then applied to every document of one layout with no model call. It holds the schema the values must
meet, the form's layout (its fixed printed lines, used to recognise the layout again), and for each schema
path a :class:`Reader`: the ordered ways to find the value (``strategies``) and the transforms that clean it.

    {"formcast_extract_version": "1", "template_id": "...", "name": "Client information",
     "schema": {...}, "form": {...}, "layout": {"lines": [...], "page_sizes": [[612, 792]]},
     "readers": {
       "investor.tax_id": {"strategies": [{"kind": "widget", "names": ["TaxID", "tin"]},
                                          {"kind": "anchor", "labels": ["Taxpayer ID", "TIN"], "side": "right"}],
                           "clean": [{"op": "digits"}]},
       "owners[1].name": {"strategies": [{"kind": "box", "page": 0, "rect": [72, 300, 300, 318]}]}},
     "absent": ["investor.fax"]}

A strategy tries one way of reading: a form field (``widget``), the words inside a rectangle (``box``), the
words next to a label (``anchor``), the words after a phrase in running text (``phrase``) or a set of
check marks (``choice``). The first strategy that finds something wins. Readers are keyed by concrete
paths: a repeated group is an array of objects with ``maxItems`` (4 when it has none), and each item has its
own readers (``owners[0].name`` ... ``owners[3].name``).

A :class:`DocumentResult` has one :class:`FieldResult` per schema path; every value ends with a status, and a
value that fails its check keeps both its raw and its cleaned form and is never dropped or altered.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from formcast.data.mapping import Transform, _compact_lists, _set
from formcast.geometry import Rect
from formcast.schema import ARRAY_ITEM, _normalize, flatten_schema, format_path, parse_path
from formcast.template.model import FormInfo

#: Items given to a repeated group whose schema has no ``maxItems``.
DEFAULT_ITEMS = 4


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ strategies


class WidgetRead(_Model):
    """A form field's value. The first of ``names`` present in the document wins (versions of a form may rename a field)."""

    kind: Literal["widget"] = "widget"
    names: list[str] = Field(min_length=1)


class BoxRead(_Model):
    """The words inside ``rect`` on ``page``. Only for a document whose layout matches the template's."""

    kind: Literal["box"] = "box"
    page: int
    rect: Rect


class AnchorRead(_Model):
    """The words beside a printed label. ``labels`` are alternative wordings of one label ("Taxpayer ID", "TIN")."""

    kind: Literal["anchor"] = "anchor"
    labels: list[str] = Field(min_length=1)
    page: int | None = None  # None: search every page
    side: Literal["right", "below"] = "right"
    max_width: float = 300.0
    max_lines: int = 1


class PhraseRead(_Model):
    """The words after a phrase in running text ("my tax id is 84-2219037, and"), up to the ``stop`` rule."""

    kind: Literal["phrase"] = "phrase"
    phrases: list[str] = Field(min_length=1)
    page: int | None = None
    stop: Literal["comma", "sentence", "line", "digits"] = "comma"


class ChoiceMark(_Model):
    """Where one option's check mark is: a form field (``widget``) or a rectangle on a page, never both.

    ``state`` names one button of a radio group by its on-state ("joint"); it goes only with ``widget``.
    """

    widget: str | None = None
    state: str | None = None
    page: int | None = None
    rect: Rect | None = None

    @model_validator(mode="after")
    def _one_place(self) -> ChoiceMark:
        if self.state is not None and self.widget is None:
            raise ValueError("a choice mark takes a state only together with a widget")
        if self.widget is not None:
            if self.page is not None or self.rect is not None:
                raise ValueError("a choice mark takes a widget or a page and rect, not both")
        elif self.page is None or self.rect is None:
            raise ValueError("a choice mark takes a widget, or a page and a rect")
        return self


class ChoiceRead(_Model):
    """One value out of several check marks: ``options`` maps each value to where its mark is."""

    kind: Literal["choice"] = "choice"
    options: dict[str, ChoiceMark]


Strategy = Annotated[WidgetRead | BoxRead | AnchorRead | PhraseRead | ChoiceRead, Field(discriminator="kind")]


class Reader(_Model):
    """How one schema path is read: strategies tried in order, then the transforms that clean the raw text."""

    strategies: list[Strategy]
    clean: list[Transform] = Field(default_factory=list)


# ------------------------------------------------------------------- template


class StaticLine(_Model):
    """A line of printed text that is the same on every copy of the form (not a filled value)."""

    page: int
    text: str
    rect: Rect


class Layout(_Model):
    """The form's fixed printed lines and page sizes; a document's layout is compared with this."""

    lines: list[StaticLine]
    page_sizes: list[tuple[float, float]]


def _max_items(schema: dict, generic: str) -> list[int]:
    """The ``maxItems`` (or :data:`DEFAULT_ITEMS`) of each array on the way to ``generic``, outermost first."""
    node, _ = _normalize(schema, schema, ())
    limits: list[int] = []
    for seg in parse_path(generic):
        if seg is ARRAY_ITEM:
            n = node.get("maxItems")
            limits.append(n if isinstance(n, int) and n > 0 else DEFAULT_ITEMS)
            node, _ = _normalize(node.get("items"), schema, ())
        else:
            node, _ = _normalize((node.get("properties") or {}).get(seg), schema, ())
    return limits


def _expand(generic: str, limits: list[int]) -> list[str]:
    """``a[].b[].c`` with limits [2, 2] -> a[0].b[0].c, a[0].b[1].c, a[1].b[0].c, ..."""
    segments = parse_path(generic)
    # each segment as the text it adds to the path, so an array item can be swapped for its index
    pieces: list[str] = []
    done = ""
    for i in range(len(segments)):
        text = format_path(segments[: i + 1])
        pieces.append(text[len(done) :])
        done = text
    paths = [""]
    items = iter(limits)
    for seg, piece in zip(segments, pieces):
        if seg is ARRAY_ITEM:
            count = next(items)
            paths = [f"{p}[{n}]" for p in paths for n in range(count)]
        else:
            paths = [p + piece for p in paths]
    return paths


def concrete_paths(schema: dict) -> list[str]:
    """Every leaf path of ``schema``, with each repeated group expanded to ``[0]`` .. ``[maxItems-1]``.

    A leaf is any path that is not an object or a list of records; a list of plain values is one path.
    A group with no ``maxItems`` gets :data:`DEFAULT_ITEMS` items. Items come in order, each with all its fields.
    """
    out: list[str] = []
    group: list[str] = []  # the expanded paths of the repeated group being read, flattened in schema order

    def flush(head: str | None) -> None:
        # item by item: owners[0].name, owners[0].dob, owners[1].name, ... (a stable sort on the first index)
        if head is not None:
            start = len(head) + 1
            group.sort(key=lambda p: int(p[start : p.index("]", start)]))
        out.extend(group)
        group.clear()

    outer = None
    for sp in flatten_schema(schema):
        if sp.type == "object" or (sp.type == "array" and sp.item_type == "object"):
            continue
        head = sp.path.split("[]")[0] if "[]" in sp.path else None
        if head != outer or head is None:
            flush(outer)
            outer = head
        if head is None:
            out.append(sp.path)
        else:
            group.extend(_expand(sp.path, _max_items(schema, sp.path)))
    flush(outer)
    return out


class ExtractionTemplate(_Model):
    """A form's schema, layout and per-path readers: everything needed to read its filled copies with no model call."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    formcast_extract_version: Literal["1"] = "1"
    template_id: str
    name: str
    schema_: dict[str, Any] = Field(alias="schema")
    form: FormInfo
    layout: Layout
    readers: dict[str, Reader]
    absent: list[str] = Field(default_factory=list)  # schema paths marked "not on this form"
    fill_template: str | None = None  # id of the fill Template for the same form, if there is one

    @model_validator(mode="after")
    def _paths_are_the_schemas(self) -> ExtractionTemplate:
        known = set(concrete_paths(self.schema_))
        for path in self.readers:
            if path not in known:
                raise ValueError(f"reader for {path!r}: not a field of the schema")
        for path in self.absent:
            if path not in known:
                raise ValueError(f"absent path {path!r}: not a field of the schema")
        return self


# --------------------------------------------------------------------- results

Status = Literal["found", "unsure", "failed_check", "missing", "absent", "not_on_form"]

#: Statuses whose value is part of the document's record.
_HAS_VALUE = ("found", "unsure", "failed_check")


class FieldResult(_Model):
    """One schema path read from one document: the raw text, the cleaned value, how it was found and why it is in doubt."""

    path: str
    status: Status
    raw: str | None
    value: Any = None
    strategy: str | None = None  # which strategy found it, e.g. "widget" or "anchor"
    page: int | None = None
    rect: Rect | None = None
    reasons: list[str] = Field(default_factory=list)


class DocumentResult(_Model):
    """What one document gave: the fields read, or an ``error`` when it could not be read or matched to a template."""

    doc_id: str
    file: str
    template_id: str | None
    matched: Literal["fingerprint", "layout", "none"]
    fields: list[FieldResult]
    error: str | None = None
    extracted_at: str

    def record(self) -> dict:
        """The cleaned values of the fields that have one, as a nested record of the schema.

        Lists of records are compacted as ``apply_mapping`` does, so a record looks the same whichever way it was made.
        """
        record: dict = {}
        for f in self.fields:
            if f.status in _HAS_VALUE and f.value is not None:
                _set(record, f.path, f.value)
        _compact_lists(record)
        return record
