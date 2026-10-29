"""Template data models and ``new_template``.

Rects are PDF points, origin top-left, unrotated page space. Validation of ops
and schema paths lives elsewhere; these models only check structural invariants.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, NamedTuple

import pydantic
from pydantic import BaseModel, ConfigDict

from formcast.geometry import Rect
from formcast.pagemap.model import FormMap, FormSignature, PageMap
from formcast.transforms import Transform


class Source(BaseModel):
    """Where a field's value comes from. A multi-select path's value is a list."""

    paths: list[str]
    transforms: list[Transform] = []
    join: str | None = None
    when: Transform | None = None


# A font size in points: finite and positive (NaN/inf would poison fitting and fill).
FontSize = Annotated[float, pydantic.Field(gt=0, allow_inf_nan=False)]


class Style(BaseModel):
    font_size: FontSize | Literal["auto"] = "auto"
    min_font_size: FontSize = 6
    align: Literal["left", "center", "right"] = "left"
    comb: int | None = None
    multiline: bool = False

    @pydantic.model_validator(mode="after")
    def _check_sizes(self) -> Style:
        if self.font_size == "auto" or self.min_font_size <= self.font_size:
            return self
        if "min_font_size" in self.model_fields_set:
            raise ValueError(
                f"min_font_size ({self.min_font_size}) can't exceed font_size ({self.font_size}); "
                "lower min_font_size too, or use font_size='auto'"
            )
        # An unset (default) minimum follows a smaller explicit size. Written to __dict__ so the
        # field stays "unset" and set_style's exclude_unset merge doesn't treat it as a request.
        self.__dict__["min_font_size"] = self.font_size
        return self


class Column(BaseModel):
    """One table column. ``fields`` optionally links existing plain fields as the
    column's cells, one id per row (``None``, or rows past the list, use the cell
    computed from the segment's rows and ``x``). A linked field keeps its own rect,
    type and widget; the column supplies its value (see :func:`table_cells`)."""

    key: str
    x: tuple[float, float]
    transforms: list[Transform] = []
    wrap: int | None = None
    fields: list[str | None] = []


class TableSegment(BaseModel):
    page: int
    rect: Rect
    row_top: float
    row_height: float
    max_rows: int
    columns: list[Column]


class Field(BaseModel):
    id: str
    type: Literal["text", "checkbox", "radio_option", "signature", "choice_mark", "table"]
    label: str = ""
    page: int | None = None
    rect: Rect | None = None
    source: Source | None = None
    style: Style = pydantic.Field(default_factory=Style)
    existing_widget: str | None = None
    segments: list[TableSegment] = []
    confidence: float | None = None
    origin: Literal["detector", "agent", "human", "widget"] = "agent"

    @pydantic.model_validator(mode="after")
    def _check_shape(self) -> Field:
        if self.type == "table":
            if not self.segments:
                raise ValueError(f"table field {self.id!r} needs at least one segment")
        elif self.page is None or self.rect is None:
            raise ValueError(f"{self.type} field {self.id!r} needs page and rect")
        return self


class Cell(NamedTuple):
    """One table cell: where it is and, for a linked cell, the plain field that is the cell."""

    page: int
    rect: Rect
    segment: int
    row: int
    column: Column
    field: Field | None


def table_cells(template: Template, table: Field) -> list[Cell]:
    """Every cell of ``table``: each segment's ``max_rows`` rows by its columns, in that order.

    A computed cell spans the column's x range and one row (``row_top + row *
    row_height``, ``row_height`` tall). A cell linked through ``Column.fields`` takes
    the linked field's page and rect instead; an id the template no longer has falls
    back to the computed cell.
    """
    by_id = {f.id: f for f in template.fields}
    out: list[Cell] = []
    for si, seg in enumerate(table.segments):
        for row in range(seg.max_rows):
            top = seg.row_top + row * seg.row_height
            for col in seg.columns:
                fid = col.fields[row] if row < len(col.fields) else None
                linked = by_id.get(fid) if fid is not None else None
                if linked is not None and linked.rect is not None and linked.page is not None:
                    out.append(Cell(linked.page, linked.rect, si, row, col, linked))
                else:
                    out.append(Cell(seg.page, (col.x[0], top, col.x[1], top + seg.row_height), si, row, col, None))
    return out


def linked_cells(template: Template) -> dict[str, tuple[str, int, int, str]]:
    """Field id -> (table id, segment, row, column key) for every field linked as a table cell."""
    out: dict[str, tuple[str, int, int, str]] = {}
    for f in template.fields:
        for si, seg in enumerate(f.segments):
            for col in seg.columns:
                for row, fid in enumerate(col.fields):
                    if fid is not None:
                        out.setdefault(fid, (f.id, si, row, col.key))
    return out


class FormInfo(BaseModel):
    fingerprint: str | None  # None for templates made outside the library (browser demo); see rebind
    page_count: int
    page_sizes: list[tuple[float, float]]
    kinds: list[str]
    signature: FormSignature | None = None  # None for older templates and ones made in the browser


class HistoryEntry(BaseModel):
    op: dict[str, Any]
    author: Literal["agent", "human"]
    at: str  # ISO-8601 UTC timestamp
    note: str = ""


class Template(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    formcast_version: Literal["1"] = "1"
    template_id: str
    schema_: dict[str, Any] = pydantic.Field(alias="schema")
    form: FormInfo
    fields: list[Field] = []
    history: list[HistoryEntry] = []

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)

    @classmethod
    def from_json(cls, s: str) -> Template:
        return cls.model_validate_json(s)


_WIDGET_FIELD_TYPE = {
    "text": "text",
    "combo": "text",
    "list": "text",
    "checkbox": "checkbox",
    "radio": "radio_option",
    "signature": "signature",
}


# Field types each kind of existing widget can back (fill writes the field's value into it).
WIDGET_FIELD_TYPES: dict[str, frozenset[str]] = {
    "text": frozenset({"text", "signature"}),
    "combo": frozenset({"text"}),
    "list": frozenset({"text"}),
    "checkbox": frozenset({"checkbox", "radio_option"}),
    "radio": frozenset({"radio_option", "checkbox"}),
    "signature": frozenset({"signature"}),
    "button": frozenset(),
}

_CANDIDATE_FIELD_TYPE = {"text": "text", "choice": "checkbox", "signature": "signature"}
MAX_SEED_LABEL = 80


def form_info_for(form_map: FormMap) -> FormInfo:
    """The form info (fingerprint, page sizes, page kinds, signature) a template records for ``form_map``."""
    return FormInfo(
        fingerprint=form_map.fingerprint,
        page_count=len(form_map.pages),
        page_sizes=[(p.width, p.height) for p in form_map.pages],
        kinds=[p.kind for p in form_map.pages],
        signature=form_map.signature,
    )


def new_template(schema: dict, form_map: FormMap, *, candidates: bool = False) -> Template:
    """Empty template for ``form_map``, with one unmapped field per existing widget.

    Widget fields take the widget's form-map id (``w<page>_<index>``, as in the
    query helpers and the summary) and come in page then reading order (top to
    bottom, left to right); push buttons are skipped. With ``candidates`` each
    detector candidate also becomes an unmapped field with the candidate's id
    (``c<page>_<n>``), its rect, a type from its kind (choice -> checkbox),
    its linked label line as label, ``origin="detector"`` and its confidence.
    """
    fields: list[Field] = []
    for page in sorted(form_map.pages, key=lambda p: p.index):
        widgets = sorted(enumerate(page.widgets), key=lambda iw: (iw[1].rect[1], iw[1].rect[0]))
        for i, widget in widgets:
            field_type = _WIDGET_FIELD_TYPE.get(widget.kind)
            if field_type is None:
                continue
            fields.append(
                Field(
                    id=f"w{page.index}_{i}",
                    type=field_type,
                    label=widget.tooltip or "",
                    page=page.index,
                    rect=widget.rect,
                    existing_widget=widget.name,
                    origin="widget",
                )
            )
        if candidates:
            fields.extend(candidate_fields(page))
    return Template(template_id=uuid.uuid4().hex, schema=schema, form=form_info_for(form_map), fields=fields)


def candidate_fields(page: PageMap) -> list[Field]:
    """An unmapped field per detector candidate of ``page``, as ``new_template(..., candidates=True)`` makes them."""
    lines = {ln.id: ln.text for ln in page.lines}
    fields = []
    for c in page.candidates:
        label = lines.get(c.label_line_id, "") if c.label_line_id is not None else ""
        fields.append(
            Field(
                id=c.id,
                type=_CANDIDATE_FIELD_TYPE[c.kind],
                label=" ".join(label.split())[:MAX_SEED_LABEL],
                page=page.index,
                rect=c.rect,
                confidence=c.conf,
                origin="detector",
            )
        )
    return fields
