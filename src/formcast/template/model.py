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


def new_template(schema: dict, form_map: FormMap) -> Template:
    """Empty template for ``form_map``, with one unmapped field per existing widget.

    Widget fields get ids ``f1, f2, ...`` in page then reading order (top to
    bottom, left to right); push buttons are skipped.
    """
    fields: list[Field] = []
    for page in sorted(form_map.pages, key=lambda p: p.index):
        for widget in sorted(page.widgets, key=lambda w: (w.rect[1], w.rect[0])):
            field_type = _WIDGET_FIELD_TYPE.get(widget.kind)
            if field_type is None:
                continue
            fields.append(
                Field(
                    id=f"f{len(fields) + 1}",
                    type=field_type,
                    label=widget.tooltip or "",
                    page=page.index,
                    rect=widget.rect,
                    existing_widget=widget.name,
                    origin="widget",
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
