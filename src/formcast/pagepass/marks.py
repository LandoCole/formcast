"""Marked page images: a page render with every template field outlined and labelled."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from formcast.pagemap.extract import open_pdf
from formcast.template.model import Field

MAX_DPI = 140
MAX_LONG_EDGE_PX = 1568  # the long edge a vision model reads without resizing
FONT_SIZE = 13
OUTLINE_COLOURS = {
    "text": (31, 119, 180),
    "checkbox": (44, 160, 44),
    "radio_option": (23, 160, 180),
    "signature": (148, 103, 189),
}
OTHER_COLOUR = (200, 60, 60)
_FONT_FILES = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "DejaVuSans-Bold.ttf")


def marks_for(fields: list[Field]) -> dict[str, str]:
    """Field id to the short mark drawn on the page: the id after its first underscore, or the full id on a clash."""
    short = {f.id: f.id.partition("_")[2] or f.id for f in fields}
    if len(set(short.values())) < len(short):
        return {f.id: f.id for f in fields}
    return short


def page_dpi(width_pt: float, height_pt: float) -> float:
    """Render resolution: 140 dpi, lowered so the page's long edge is at most 1568 px."""
    return min(MAX_DPI, MAX_LONG_EDGE_PX / (max(width_pt, height_pt) / 72))


def _font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in _FONT_FILES:
        try:
            return ImageFont.truetype(name, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default(size=FONT_SIZE)


def render_marked_page(pdf: str | Path | bytes, page: int, fields: list[Field]) -> bytes:
    """PNG of one page with each field's box outlined in its type colour and tagged with its mark."""
    doc = open_pdf(pdf)
    try:
        pg = doc[page]
        dpi = page_dpi(pg.rect.width, pg.rect.height)
        pix = pg.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    finally:
        doc.close()
    draw = ImageDraw.Draw(img)
    font = _font()
    marks = marks_for(fields)
    zoom = dpi / 72
    for f in fields:
        if f.rect is None:
            continue
        x0, y0, x1, y1 = (v * zoom for v in f.rect)
        colour = OUTLINE_COLOURS.get(f.type, OTHER_COLOUR)
        draw.rectangle([x0, y0, x1, y1], outline=colour, width=2)
        mark = marks[f.id]
        tw = draw.textlength(mark, font=font)
        if y1 - y0 >= 17 and x1 - x0 >= tw + 5:  # inside the box's top-left: covers no printed text
            tx, ty = x0 + 1, y0 + 1
        else:  # too small: just left of it
            tx, ty = max(0, x0 - tw - 5), y0
        draw.rectangle([tx, ty, tx + tw + 4, ty + 15], fill=(255, 255, 255), outline=colour)
        draw.text((tx + 2, ty - 1), mark, fill=colour, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
