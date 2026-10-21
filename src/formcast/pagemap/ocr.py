"""OCR words for pages without a usable text layer.

The page is rendered, its skew estimated and removed, and Tesseract run on the
straightened image. Word boxes are rotated back into the rendered image's frame
and then mapped into unrotated crop-box points with ``image_norm_to_points``,
so OCR words live in the same space as the text-layer words.

Skew convention: ``estimate_skew`` returns the angle, in degrees, that the page
content is turned counter-clockwise (the direction of ``PIL.Image.rotate`` with
a positive angle); ``deskew(image, angle)`` undoes exactly that.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

import numpy as np
import pymupdf
from PIL import Image

from formcast.errors import FormcastError
from formcast.geometry import Rect
from formcast.pagemap.extract import image_norm_to_points
from formcast.pagemap.model import TextLine, Word

NOT_INSTALLED = (
    "OCR requested but Tesseract is not installed; "
    "pip install formcast[ocr] and install tesseract"
)

# Skew search range and resolution, in degrees.
MAX_SKEW = 3.0
SKEW_STEP = 0.1
# The projection profile is computed on an image at most this many pixels wide/tall.
SKEW_WORK_SIZE = 1000
# Skews smaller than this are not worth resampling the page for.
MIN_DESKEW = 0.2
# Tesseract boxes the ink; ascender-to-baseline height is about this fraction of
# the point size, which gives OCR lines a rough ``font_size``.
INK_TO_FONT_SIZE = 0.75


def ocr_available() -> bool:
    """True when ``pytesseract`` is importable and the Tesseract binary runs."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:  # missing module, missing binary, or a broken install
        return False
    return True


def _ink_mask(image: Image.Image) -> np.ndarray:
    """Boolean array, True where a pixel is ink (Otsu threshold on gray levels)."""
    gray = np.asarray(image.convert("L"))
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = hist.sum()
    levels = np.arange(256)
    weight_bg = np.cumsum(hist)
    sum_bg = np.cumsum(hist * levels)
    weight_fg = total - weight_bg
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_bg[-1] - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    between = np.nan_to_num(between)
    threshold = int(np.argmax(between))
    return gray <= threshold


def estimate_skew(image: Image.Image) -> float:
    """Skew of the page content in degrees (counter-clockwise positive).

    Projection-profile search: the image is binarized and downscaled, rotated
    over -3..3 degrees in 0.1 steps, and the angle whose row sums are the most
    uneven (text lines and rules lined up with the rows) wins. Returns 0.0 for
    a blank page.
    """
    ink = _ink_mask(image)
    if not ink.any() or ink.all():
        return 0.0
    mask = Image.fromarray((ink * 255).astype(np.uint8))
    scale = SKEW_WORK_SIZE / max(mask.size)
    if scale < 1:
        mask = mask.resize(
            (max(1, round(mask.width * scale)), max(1, round(mask.height * scale))),
            Image.BOX,
        )
    steps = round(MAX_SKEW / SKEW_STEP)
    best_angle, best_score = 0.0, -1.0
    # Visit angles from zero outward so ties resolve to the smallest skew.
    for k in sorted(range(-steps, steps + 1), key=abs):
        angle = k * SKEW_STEP
        rows = np.asarray(mask.rotate(-angle, resample=Image.BILINEAR, fillcolor=0))
        score = float(rows.sum(axis=1, dtype=np.float64).var())
        if score > best_score + 1e-9:
            best_angle, best_score = angle, score
    return round(best_angle, 1)


def deskew(image: Image.Image, angle: float) -> Image.Image:
    """Rotate ``image`` by ``-angle`` degrees about its center (same size, white fill)."""
    fill = 255 if len(image.getbands()) == 1 else (255,) * len(image.getbands())
    return image.rotate(-angle, resample=Image.BICUBIC, fillcolor=fill)


def unrotate_point(x: float, y: float, angle: float, size: tuple[int, int]) -> tuple[float, float]:
    """Map a pixel position in ``deskew(image, angle)`` back into ``image``.

    ``size`` is the ``(width, height)`` of the image, which ``deskew`` keeps
    unchanged; the rotation is about its center.
    """
    cx, cy = size[0] / 2, size[1] / 2
    t = math.radians(angle)
    dx, dy = x - cx, y - cy
    return (
        cx + dx * math.cos(t) + dy * math.sin(t),
        cy - dx * math.sin(t) + dy * math.cos(t),
    )


def unrotate_box(box: Rect, angle: float, size: tuple[int, int]) -> Rect:
    """Axis-aligned box, normalized to ``image``, covering a pixel box of ``deskew(image, angle)``.

    ``box`` is ``(x0, y0, x1, y1)`` in pixels of the deskewed image. All four
    corners are mapped back with ``unrotate_point`` and their bounding box is
    returned, divided by ``size`` (the image's width and height).
    """
    x0, y0, x1, y1 = box
    corners = [unrotate_point(x, y, angle, size) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    width, height = size
    return (min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height)


def render(page: pymupdf.Page, dpi: int) -> Image.Image:
    """The page as an RGB image (cropped and rotated as ``get_pixmap`` shows it)."""
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def straighten(image: Image.Image) -> tuple[Image.Image, float]:
    """Deskew ``image`` when its skew is worth correcting.

    Returns the image to use and the angle applied; ``(image, 0.0)`` when the
    skew is below ``MIN_DESKEW``. A nonzero angle is what ``unrotate_point`` and
    ``unrotate_box`` need to map positions back into ``image``.
    """
    angle = estimate_skew(image)
    if abs(angle) < MIN_DESKEW:
        return image, 0.0
    return deskew(image, angle), angle


def ocr_page(page: pymupdf.Page, dpi: int = 300) -> tuple[list[Word], list[TextLine]]:
    """OCR one page into words and lines (``source="ocr"``, rects in points).

    Raises:
        FormcastError: when pytesseract or the Tesseract binary is missing, or
            Tesseract fails ("OCR failed: <Tesseract's message>").
    """
    if not ocr_available():
        raise FormcastError(NOT_INSTALLED)
    import pytesseract

    scan, angle = straighten(render(page, dpi))
    try:
        data = pytesseract.image_to_data(scan, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as exc:
        raise FormcastError(NOT_INSTALLED) from exc
    except (pytesseract.TesseractError, RuntimeError) as exc:  # bad install/language data, timeout
        detail = exc.message if isinstance(exc, pytesseract.TesseractError) else str(exc)
        raise FormcastError(f"OCR failed: {str(detail).strip() or type(exc).__name__}") from exc

    groups: dict[tuple[int, int, int], list[tuple[str, Rect, float]]] = defaultdict(list)
    for i, raw in enumerate(data["text"]):
        text = str(raw).strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        left, top = data["left"][i], data["top"][i]
        norm = unrotate_box(
            (left, top, left + data["width"][i], top + data["height"][i]), angle, scan.size
        )
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups[key].append((text, image_norm_to_points(page, norm), min(conf / 100.0, 1.0)))

    words: list[Word] = []
    lines: list[TextLine] = []
    for entries in groups.values():
        entries.sort(key=lambda e: e[1][0])
        line_id = len(lines)
        word_ids = []
        for text, rect, conf in entries:
            word_ids.append(len(words))
            words.append(Word(text=text, rect=rect, line_id=line_id, source="ocr", conf=conf))
        rects = [e[1] for e in entries]
        lines.append(
            TextLine(
                id=line_id,
                text=" ".join(e[0] for e in entries),
                rect=(
                    min(r[0] for r in rects),
                    min(r[1] for r in rects),
                    max(r[2] for r in rects),
                    max(r[3] for r in rects),
                ),
                word_ids=word_ids,
                font_size=statistics.median(r[3] - r[1] for r in rects) / INK_TO_FONT_SIZE,
            )
        )
    return words, lines
