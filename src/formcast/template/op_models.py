"""Edit-op models: each op is plain data, a discriminated union on ``op``.

``move``/``resize`` on a table field take a ``segment`` index when given a ``rect``
(see :mod:`formcast.template.ops` for semantics and validation).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

import pydantic
from pydantic import BaseModel, ConfigDict

from formcast.geometry import Rect
from formcast.template.model import Column, Source, Style, TableSegment

FieldType = Literal["text", "checkbox", "radio_option", "signature", "choice_mark", "table"]


class OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddField(OpBase):
    op: Literal["add_field"] = "add_field"
    id: str | None = None
    type: FieldType
    page: int
    rect: Rect
    label: str = ""
    source: Source | None = None
    style: Style | None = None


class RemoveField(OpBase):
    op: Literal["remove_field"] = "remove_field"
    id: str


class Move(OpBase):
    op: Literal["move"] = "move"
    id: str
    rect: Rect | None = None
    dx: float | None = None
    dy: float | None = None
    segment: int | None = None  # table fields only

    @pydantic.model_validator(mode="after")
    def _rect_or_offset(self) -> Move:
        offset = self.dx is not None or self.dy is not None
        if self.rect is None and not offset:
            raise ValueError("move needs either rect=[x0, y0, x1, y1] or a dx/dy offset")
        if self.rect is not None and offset:
            raise ValueError("move takes rect or dx/dy, not both")
        return self


class Resize(OpBase):
    op: Literal["resize"] = "resize"
    id: str
    rect: Rect
    segment: int | None = None  # table fields only (required for them)


class SetLabel(OpBase):
    op: Literal["set_label"] = "set_label"
    id: str
    label: str


class SetType(OpBase):
    op: Literal["set_type"] = "set_type"
    id: str
    type: FieldType


class SetSource(OpBase):
    op: Literal["set_source"] = "set_source"
    id: str
    source: Source | None


class SetStyle(OpBase):
    op: Literal["set_style"] = "set_style"
    id: str
    style: Style


class AddTable(OpBase):
    op: Literal["add_table"] = "add_table"
    id: str | None = None
    label: str = ""
    source_path: str
    segments: list[TableSegment]


class SetTableColumns(OpBase):
    op: Literal["set_table_columns"] = "set_table_columns"
    id: str
    segment: int
    columns: list[Column]


class SetTableRows(OpBase):
    op: Literal["set_table_rows"] = "set_table_rows"
    id: str
    segment: int
    row_top: float
    row_height: float
    max_rows: int


class AddTableSegment(OpBase):
    op: Literal["add_table_segment"] = "add_table_segment"
    id: str
    segment: TableSegment


class LinkWidget(OpBase):
    op: Literal["link_widget"] = "link_widget"
    id: str
    widget: str | None


OP_CLASSES = (
    AddField, RemoveField, Move, Resize, SetLabel, SetType, SetSource, SetStyle,
    AddTable, SetTableColumns, SetTableRows, AddTableSegment, LinkWidget,
)  # fmt: skip
Op = Annotated[Union[OP_CLASSES], pydantic.Field(discriminator="op")]  # noqa: UP007
OP_NAMES = [c.model_fields["op"].default for c in OP_CLASSES]
