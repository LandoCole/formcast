"""Preview images of a template: each page with its field boxes drawn on.

For human review and debugging only; never sent to the model.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from formcast.geometry import Rect
from formcast.pagemap.extract import open_pdf
from formcast.pagemap.model import FormMap
from formcast.render.fill import fill
from formcast.template.model import Field, Template

# RGB per field type.
TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "text": (31, 119, 180),
    "checkbox": (44, 160, 44),
    "radio_option": (23, 160, 180),
    "signature": (148, 103, 189),
    "choice_mark": (255, 127, 14),
    "table": (214, 39, 40),
}
CANDIDATE_COLOR = (128, 128, 128)
FILL_ALPHA = 40
CANDIDATE_ALPHA = 110
CANDIDATE_FILL_ALPHA = 20
MAX_LABEL = 40


def _to_pixels(page: pymupdf.Page, rect: Rect, zoom: float) -> tuple[float, float, float, float]:
    """Unrotated crop-box points -> pixel box on the rendered (rotated) page."""
    r = pymupdf.Rect(rect) * page.rotation_matrix
    r.normalize()
    return (r.x0 * zoom, r.y0 * zoom, r.x1 * zoom, r.y1 * zoom)


def _caption(f: Field) -> str:
    text = f.id
    if f.label:
        label = " ".join(f.label.split())
        text += " " + (label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 3] + "...")
    if f.source and f.source.paths:
        text += " -> " + ",".join(f.source.paths)  # ASCII: the default font has no arrow glyph
    return text


def _draw_caption(draw: ImageDraw.ImageDraw, box, text: str, color, font) -> None:
    x0, y0, _, _ = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    h = bottom - top
    y = y0 - h - 2 if y0 - h - 2 >= 0 else y0 + 1
    draw.rectangle((x0, y, x0 + (right - left) + 2, y + h + 1), fill=(255, 255, 255, 150))
    draw.text((x0 + 1, y - top), text, fill=(*color, 255), font=font)


def _draw_field(draw, page, f: Field, zoom: float, font, width: int) -> None:
    color = TYPE_COLORS.get(f.type, (0, 0, 0))
    outline = (*color, 255)
    if f.type == "table":
        for seg in f.segments:
            if seg.page != page.number:
                continue
            box = _to_pixels(page, seg.rect, zoom)
            draw.rectangle(box, outline=outline, fill=(*color, FILL_ALPHA // 2), width=width)
            x0, _, x1, _ = seg.rect
            for r in range(seg.max_rows + 1):
                y = seg.row_top + r * seg.row_height
                draw.line(_to_pixels(page, (x0, y, x1, y), zoom), fill=(*color, 160), width=1)
            top, bottom = seg.row_top, seg.row_top + seg.max_rows * seg.row_height
            for col in seg.columns:
                for x in col.x:
                    draw.line(_to_pixels(page, (x, top, x, bottom), zoom), fill=(*color, 160), width=1)
            _draw_caption(draw, box, _caption(f), color, font)
        return
    if f.page != page.number or f.rect is None:
        return
    box = _to_pixels(page, f.rect, zoom)
    draw.rectangle(box, outline=outline, fill=(*color, FILL_ALPHA), width=width)
    _draw_caption(draw, box, _caption(f), color, font)


def preview(
    template: Template,
    pdf: str | Path | bytes,
    *,
    data: dict | None = None,
    dpi: int = 110,
    show_candidates: FormMap | None = None,
) -> list[bytes]:
    """One PNG per page of ``pdf`` with the template's fields drawn on.

    Boxes are coloured by field type and captioned with id, label and mapped
    path. Table segments show their rows and columns. With ``show_candidates``,
    that form map's detector candidates are drawn as faint gray boxes. With
    ``data``, the page under the boxes is ``fill(template, pdf, data)``'s output
    (fingerprint not checked), so filled values and field outlines show together.
    """
    if data is not None:
        pdf = fill(template, pdf, data, check_fingerprint=False)[0]
    zoom = dpi / 72
    font = ImageFont.load_default(size=max(8, round(9 * zoom)))
    width = max(1, round(zoom * 1.2))
    doc = open_pdf(pdf)
    try:
        out: list[bytes] = []
        for page in doc:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
            overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            if show_candidates is not None:
                for pm in show_candidates.pages:
                    if pm.index != page.number:
                        continue
                    for c in pm.candidates:
                        draw.rectangle(
                            _to_pixels(page, c.rect, zoom),
                            outline=(*CANDIDATE_COLOR, CANDIDATE_ALPHA),
                            fill=(*CANDIDATE_COLOR, CANDIDATE_FILL_ALPHA),
                            width=1,
                        )
            for f in template.fields:
                _draw_field(draw, page, f, zoom, font, width)
            image = Image.alpha_composite(base, overlay).convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    finally:
        doc.close()
