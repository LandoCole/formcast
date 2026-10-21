"""Template data models and ``new_template``.

Rects are PDF points, origin top-left, unrotated page space. Validation of ops
and schema paths lives elsewhere; these models only check structural invariants.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import pydantic
from pydantic import BaseModel, ConfigDict

from formcast.geometry import Rect
from formcast.pagemap.model import FormMap
from formcast.transforms import Transform


class Source(BaseModel):
    """Where a field's value comes from. A multi-select path's value is a list."""

    paths: list[str]
    transforms: list[Transform] = []
    join: str | None = None
    when: Transform | None = None


class Style(BaseModel):
    font_size: float | Literal["auto"] = "auto"
    min_font_size: float = 6
    align: Literal["left", "center", "right"] = "left"
    comb: int | None = None
    multiline: bool = False


class Column(BaseModel):
    key: str
    x: tuple[float, float]
    transforms: list[Transform] = []
    wrap: int | None = None


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


class FormInfo(BaseModel):
    fingerprint: str
    page_count: int
    page_sizes: list[tuple[float, float]]
    kinds: list[str]


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


_CANDIDATE_FIELD_TYPE = {"text": "text", "choice": "checkbox", "signature": "signature"}
MAX_SEED_LABEL = 80


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
        if not candidates:
            continue
        lines = {ln.id: ln.text for ln in page.lines}
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
    return Template(
        template_id=uuid.uuid4().hex,
        schema=schema,
        form=FormInfo(
            fingerprint=form_map.fingerprint,
            page_count=len(form_map.pages),
            page_sizes=[(p.width, p.height) for p in form_map.pages],
            kinds=[p.kind for p in form_map.pages],
        ),
        fields=fields,
    )
