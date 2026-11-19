"""Turn a static PDF into a fillable one: detect blanks with FFDNet-S and add a form field on each.

Per page, in order:
- a page that already has form fields is left as it is ("fillable");
- with ``skip_prose``, a page that reads as running text is not searched ("prose"), using FormCast's rule:
  most lines run across 60% of the page with 60+ characters, at most one short "Label:" line and at most
  eight horizontal rules of 25 pt or more (it errs toward searching; image-heavy scans are always searched).
  On top of FormCast's rule, a page with two or more check boxes (small drawn squares or box glyphs such as ☐)
  is never prose: questionnaires set in long paragraphs are forms;
- any other page is rendered at 144 dpi, run through the detector, and gets a text field per text or
  signature box and a check box per choice box, named ``text_<page>_<n>``, ``check_<page>_<n>`` and
  ``signature_<page>_<n>`` in reading order ("boxes", or "empty" when nothing was found).

MuPDF is not thread-safe: every MuPDF call here holds :data:`MUPDF_LOCK`, which is let go while the detector runs.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pymupdf
from PIL import Image

from formcast_lite.detector import Detection, Detector

MUPDF_LOCK = threading.RLock()

DPI = 144
MAX_RENDER_PIXELS = 10 * 1700 * 2200
MIN_FIELD_PT = 2.0  # boxes thinner than this either way are dropped
MULTILINE_MIN_HEIGHT = 30.0  # text boxes at least this tall take several lines
ROW_BUCKET = 0.01  # of the page height: boxes whose tops are this close are one row

# Prose rule (FormCast's looks_like_prose).
PROSE_WIDE_SHARE = 0.6
PROSE_MAX_LABELS = 1
PROSE_MAX_RULES = 8
RULE_MIN_PT = 25.0
RULE_MAX_THICKNESS = 2.0
UNDERSCORE_MIN_RUN = 3
IMAGE_DOMINANT = 0.8
NEAR_WHITE = 0.9
# Beyond FormCast's rule: a page with check boxes is a form, however much running text it has.
PROSE_MIN_CHECKBOXES = 2
CHECKBOX_MIN_PT, CHECKBOX_MAX_PT = 5.0, 16.0
BOX_CHARS = "\u2610\u2611\u2612\u25a1\u25a2\u25fb\u25fd\u2751\u2752\u274f\u2750"

_TEXT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES


class ConvertError(Exception):
    """A PDF that cannot be converted (not a PDF, password-protected, no pages)."""


class Cancelled(Exception):
    pass


@dataclass
class PageResult:
    state: str  # "boxes", "empty", "prose" or "fillable"
    fields: int
    seconds: float


def open_pdf(data: bytes) -> pymupdf.Document:
    with MUPDF_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ConvertError("not a readable PDF") from exc
        if doc.needs_pass:
            doc.close()
            raise ConvertError("the PDF is password-protected")
        if not doc.is_pdf or doc.page_count == 0:
            doc.close()
            raise ConvertError("not a readable PDF")
        return doc


def page_count(data: bytes) -> int:
    doc = open_pdf(data)
    with MUPDF_LOCK:
        n = doc.page_count
        doc.close()
    return n


# ---------------------------------------------------------------- prose


def _visible(color, opacity) -> bool:
    if color is None or (opacity is not None and opacity <= 0.01):
        return False
    return not all(c >= NEAR_WHITE for c in color)


def _is_box_glyph(c: str, font: str) -> bool:
    if c in BOX_CHARS:
        return True
    font = font.lower()
    return ("wingding" in font and c in "noqx\xa8\xfd\xfe") or (("zapf" in font or "dingbat" in font) and c in "nopqr")


def _text_lines(page: pymupdf.Page) -> tuple[list[tuple[str, float]], int, int]:
    """Each text line as (text, width in points), split at underscore runs; the number of long runs; the number of
    box glyphs."""
    width, height = page.cropbox.width, page.cropbox.height
    lines: list[tuple[str, float]] = []
    runs = 0
    boxes = 0

    def emit(chars: list) -> None:
        inked = [c for c in chars if not c[0].isspace()]
        if not inked:
            return
        x0 = min(c[1][0] for c in inked)
        x1 = max(c[1][2] for c in inked)
        y0 = min(c[1][1] for c in inked)
        y1 = max(c[1][3] for c in inked)
        if x1 < 0 or x0 > width or y1 < 0 or y0 > height:
            return
        text = " ".join("".join(c[0] for c in chars).split())
        lines.append((text, x1 - x0))

    for block in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]:
        for raw in block.get("lines", []):
            horizontal = abs(raw["dir"][1]) < 1e-3
            chars = [(ch["c"], ch["bbox"]) for span in raw["spans"] for ch in span["chars"]]
            boxes += sum(1 for span in raw["spans"] for ch in span["chars"] if _is_box_glyph(ch["c"], span["font"]))
            segment: list = []
            i = 0
            while i < len(chars):
                if chars[i][0] == "_":
                    j = i
                    while j < len(chars) and chars[j][0] == "_":
                        j += 1
                    if horizontal and j - i >= UNDERSCORE_MIN_RUN:
                        emit(segment)
                        segment = []
                        if chars[j - 1][1][2] - chars[i][1][0] >= RULE_MIN_PT:
                            runs += 1
                    else:
                        segment.extend(chars[i:j])
                    i = j
                    continue
                segment.append(chars[i])
                i += 1
            emit(segment)
    return lines, runs, boxes


def _drawn_h_rules(page: pymupdf.Page) -> int:
    """Horizontal drawn lines (and rectangle edges) at least 25 pt long."""
    seen: set[tuple] = set()
    for path in page.get_drawings():
        filled = "f" in (path.get("type") or "") and _visible(path.get("fill"), path.get("fill_opacity"))
        stroked = "s" in (path.get("type") or "") and _visible(path.get("color"), path.get("stroke_opacity"))
        if not (filled or stroked):
            continue
        for item in path["items"]:
            op = item[0]
            if op == "l":
                p, q = item[1], item[2]
                segs = [(min(p.x, q.x), min(p.y, q.y), max(p.x, q.x), max(p.y, q.y))]
            elif op in ("re", "qu"):
                r = item[1].rect if op == "qu" else item[1]
                thin = r.height <= RULE_MAX_THICKNESS or r.width <= RULE_MAX_THICKNESS
                segs = [(r.x0, r.y0, r.x1, r.y1)] if thin else [(r.x0, r.y0, r.x1, r.y0), (r.x0, r.y1, r.x1, r.y1)]
            else:
                continue
            for x0, y0, x1, y1 in segs:
                if y1 - y0 <= RULE_MAX_THICKNESS and x1 - x0 >= RULE_MIN_PT:
                    seen.add(tuple(round(v, 1) for v in (x0, y0, x1, y1)))
    return len(seen)


def _checkbox_marks(page: pymupdf.Page, text_glyphs: int) -> int:
    """Check boxes drawn as small squares, plus the box glyphs (☐ □ ❑, Wingdings / ZapfDingbats boxes) in the text."""
    squares: set[tuple] = set()
    for path in page.get_drawings():
        stroked = "s" in (path.get("type") or "") and _visible(path.get("color"), path.get("stroke_opacity"))
        filled = "f" in (path.get("type") or "") and _visible(path.get("fill"), path.get("fill_opacity"))
        if not (stroked or filled):
            continue
        for item in path["items"]:
            if item[0] in ("re", "qu"):
                r = item[1].rect if item[0] == "qu" else item[1]
                if CHECKBOX_MIN_PT <= r.width <= CHECKBOX_MAX_PT and CHECKBOX_MIN_PT <= r.height <= CHECKBOX_MAX_PT \
                        and abs(r.width - r.height) <= 0.25 * max(r.width, r.height):
                    squares.add((round(r.x0), round(r.y0)))
        if path.get("items") and all(it[0] == "l" for it in path["items"]) and len(path["items"]) == 4:
            r = path["rect"]  # a square drawn as four lines
            if CHECKBOX_MIN_PT <= r.width <= CHECKBOX_MAX_PT and abs(r.width - r.height) <= 0.25 * r.width:
                squares.add((round(r.x0), round(r.y0)))
    return len(squares) + text_glyphs


def image_coverage(page: pymupdf.Page) -> float:
    width, height = page.cropbox.width, page.cropbox.height
    cols, rows = max(1, round(width / 4)), max(1, round(height / 4))
    grid = np.zeros((rows, cols), dtype=bool)
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        c0, c1 = round(max(0.0, x0) / width * cols), round(min(width, x1) / width * cols)
        r0, r1 = round(max(0.0, y0) / height * rows), round(min(height, y1) / height * rows)
        if c1 > c0 and r1 > r0:
            grid[r0:r1, c0:c1] = True
    return float(grid.mean())


def looks_like_prose(page: pymupdf.Page) -> bool:
    """Whether ``page`` is running text rather than a form (FormCast's rule; see the module docstring)."""
    if image_coverage(page) > IMAGE_DOMINANT:
        return False  # a scan: its text layer, if any, says little about its blanks
    lines, runs, box_glyphs = _text_lines(page)
    lines = [(t, w) for t, w in lines if t.strip()]
    if not lines:
        return False
    page_width = page.cropbox.width
    wide = sum(1 for t, w in lines if w >= 0.6 * page_width and len(t) >= 60)
    labels = sum(1 for t, _ in lines if len(t.strip()) <= 45 and t.strip().endswith(":"))
    if wide < PROSE_WIDE_SHARE * len(lines) or labels > PROSE_MAX_LABELS:
        return False
    if runs + _drawn_h_rules(page) > PROSE_MAX_RULES:
        return False
    return _checkbox_marks(page, box_glyphs) < PROSE_MIN_CHECKBOXES  # a questionnaire set in long paragraphs


# ---------------------------------------------------------------- fields


def render(page: pymupdf.Page) -> Image.Image:
    area = page.rect
    pixels = (area.width * DPI / 72) * (area.height * DPI / 72)
    dpi = DPI if pixels <= MAX_RENDER_PIXELS else DPI * math.sqrt(MAX_RENDER_PIXELS / pixels)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def reading_order(found: list[Detection]) -> list[Detection]:
    ordered = sorted(found, key=lambda d: (round(d[1][1], 3), d[1][0]))
    rows: list[list[Detection]] = []
    for det in ordered:
        if rows and abs(det[1][1] - rows[-1][0][1][1]) < ROW_BUCKET:
            rows[-1].append(det)
        else:
            rows.append([det])
    return [d for row in rows for d in sorted(row, key=lambda d: d[1][0])]


def _unique(name: str, taken: set[str]) -> str:
    out, n = name, 1
    while out in taken:
        n += 1
        out = f"{name}_{n}"
    taken.add(out)
    return out


def add_fields(page: pymupdf.Page, found: list[Detection], taken: set[str]) -> int:
    """Add a form field per detection (boxes normalized to the rendered page); the number added."""
    shown = page.rect  # the page as rendered: cropped, /Rotate applied
    number = page.number + 1
    added = 0
    for kind, (x0, y0, x1, y1), _conf in reading_order(found):
        rect = pymupdf.Rect(x0 * shown.width, y0 * shown.height, x1 * shown.width, y1 * shown.height)
        tall = rect.height >= MULTILINE_MIN_HEIGHT  # as the page is seen
        rect = rect * page.derotation_matrix  # widgets live in unrotated page space
        rect.normalize()
        if rect.width < MIN_FIELD_PT or rect.height < MIN_FIELD_PT:
            continue
        widget = pymupdf.Widget()
        widget.rect = rect
        if kind == "choice":
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
            widget.field_name = _unique(f"check_{number}_{added}", taken)
            widget.field_value = False
        else:
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.field_name = _unique(f"{'signature' if kind == 'signature' else 'text'}_{number}_{added}", taken)
            widget.text_font = "Helv"
            widget.text_fontsize = 0  # auto size
            if tall:
                widget.field_flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
        page.add_widget(widget)
        added += 1
    return added


def convert(
    data: bytes,
    detector: Detector,
    *,
    skip_prose: bool = False,
    on_page: Callable[[int, PageResult | None], None] = lambda i, r: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> bytes:
    """The fillable PDF made from ``data``. ``on_page(i, None)`` when page ``i`` starts, ``on_page(i, result)`` when
    it is done. Raises :class:`ConvertError` for an unreadable PDF and :class:`Cancelled` once ``cancelled()``."""
    doc = open_pdf(data)
    try:
        with MUPDF_LOCK:
            total = doc.page_count
            taken = {w.field_name for page in doc for w in (page.widgets() or []) if w.field_name}
        for index in range(total):
            if cancelled():
                raise Cancelled()
            on_page(index, None)
            start = time.perf_counter()
            with MUPDF_LOCK:
                page = doc[index]
                existing = sum(1 for _ in page.widgets() or [])
                if existing:
                    result = PageResult("fillable", existing, 0.0)
                elif skip_prose and looks_like_prose(page):
                    result = PageResult("prose", 0, 0.0)
                else:
                    result = None
                    image = render(page)
            if result is None:
                found = detector.detect(image)  # no MuPDF here, so not under the lock
                with MUPDF_LOCK:
                    added = add_fields(page, found, taken)
                result = PageResult("boxes" if added else "empty", added, 0.0)
            result.seconds = round(time.perf_counter() - start, 3)
            on_page(index, result)
        if cancelled():
            raise Cancelled()
        with MUPDF_LOCK:
            return doc.tobytes(garbage=1, deflate=True)
    finally:
        with MUPDF_LOCK:
            doc.close()
