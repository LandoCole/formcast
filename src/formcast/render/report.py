"""The fill report: everything ``fill`` did, found and could not do.

Pure data (pydantic), JSON round-trippable like ``Template``. Warning codes and
their severities are fixed here; every code not listed in :data:`SEVERITY` as
``info`` is a ``warning``.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel

from formcast.geometry import Rect

__all__ = [
    "SEVERITY",
    "Binding",
    "CellReport",
    "FieldReport",
    "FillReport",
    "FillWarning",
    "PathReport",
    "Severity",
    "TableReport",
    "WarningCode",
]

Severity = Literal["warning", "info"]

WarningCode = Literal[
    "missing",
    "invalid",
    "transform_error",
    "unmapped_data",
    "shrunk",
    "overflow",
    "truncated",
    "unencodable",
    "rows_dropped",
    "widget_not_found",
    "type_mismatch",
    "not_writable",
    "not_an_option",
    "shared_field_conflict",
    "adopted",
    "font_substituted",
    "list_joined",
    "unknown_keys",
    "fingerprint_mismatch",
]

Binding = Literal["named", "adopted", "created", "none"]

_INFO: frozenset[str] = frozenset({"shrunk", "adopted", "font_substituted", "list_joined", "unknown_keys"})

SEVERITY: dict[WarningCode, Severity] = {
    code: ("info" if code in _INFO else "warning")  # type: ignore[misc]
    for code in get_args(WarningCode)
}


class FillWarning(BaseModel):
    code: WarningCode
    severity: Severity
    message: str
    path: str | None = None
    field: str | None = None
    page: int | None = None
    rect: Rect | None = None

    @classmethod
    def make(cls, code: WarningCode, message: str, **kw) -> FillWarning:
        """Build a warning whose severity comes from :data:`SEVERITY`."""
        return cls(code=code, severity=SEVERITY[code], message=message, **kw)


class FieldReport(BaseModel):
    id: str
    type: str
    page: int | None
    paths: list[str]
    binding: Binding = "none"
    widget: str | None = None
    widget_rect: Rect | None = None
    value: str | bool | None = None
    font_size: float | None = None
    warnings: list[FillWarning] = []


class CellReport(BaseModel):
    row: int
    key: str
    segment: int
    page: int
    rect: Rect
    binding: Binding = "none"
    widget: str | None = None
    widget_rect: Rect | None = None
    value: str | bool | None = None
    font_size: float | None = None
    warnings: list[FillWarning] = []


class TableReport(BaseModel):
    id: str
    path: str
    rows_provided: int
    rows_written: int
    rows_dropped: int
    rows_per_segment: list[int]
    cells: list[CellReport]
    warnings: list[FillWarning] = []


class PathReport(BaseModel):
    path: str
    mapped: bool
    has_value: bool
    fields: list[str]
    #: Final text (or on/off for choice fields) per field id; a table column's cells are
    #: keyed ``"<table id>[<row>]"``.
    rendered: dict[str, str | bool | None]
    warnings: list[FillWarning]


class FillReport(BaseModel):
    template_id: str
    fingerprint_ok: bool
    strict: bool
    fields: list[FieldReport] = []
    tables: list[TableReport] = []
    paths: dict[str, PathReport] = {}
    gaps: list[str] = []
    warnings: list[FillWarning] = []
    #: ``fields_written``, ``fields_empty``, ``cells_written`` and one entry per warning code present.
    counts: dict[str, int] = {}

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, s: str) -> FillReport:
        return cls.model_validate_json(s)
