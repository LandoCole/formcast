"""Page map data model: the text-and-geometry view of each page.

All rects are PDF points, origin top-left of the crop box, in unrotated page
space, as ``(x0, y0, x1, y1)``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from formcast.geometry import Rect


class Word(BaseModel):
    text: str
    rect: Rect
    line_id: int
    source: Literal["pdf", "ocr"]
    conf: float = 1.0


class TextLine(BaseModel):
    id: int
    text: str
    rect: Rect
    word_ids: list[int]
    font_size: float | None
    bold: bool = False


class Rule(BaseModel):
    """A thin drawn line, or a run of underscores in the text (always ``h``)."""

    rect: Rect
    orientation: Literal["h", "v"]


class Widget(BaseModel):
    name: str
    kind: Literal["text", "checkbox", "radio", "combo", "list", "signature", "button"]
    rect: Rect
    options: list[str] = []
    tooltip: str | None = None


class Candidate(BaseModel):
    id: str
    kind: Literal["text", "choice", "signature"]
    rect: Rect
    conf: float
    detector: str
    label_line_id: int | None = None
    label_relation: Literal["left", "above", "inside", "below"] | None = None


class PageMap(BaseModel):
    index: int
    width: float
    height: float
    rotation: int
    kind: Literal["fillable", "digital", "scanned"]
    text_source: Literal["pdf", "ocr", "pdf+ocr"]
    words: list[Word]
    lines: list[TextLine]
    rules: list[Rule]
    widgets: list[Widget]
    candidates: list[Candidate] = []


class FormMap(BaseModel):
    pages: list[PageMap]
    fingerprint: str = ""
    source_name: str = ""
