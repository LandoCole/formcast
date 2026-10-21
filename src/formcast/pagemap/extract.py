"""Build a :class:`PageMap` from a PyMuPDF page.

Coordinate space: PyMuPDF 2.x already reports text (``get_text``), drawings
(``get_drawings``), images (``get_image_info``) and widget rects in unrotated
page space with the origin at the top-left of the crop box, so those values are
used as-is. Only the rendered pixmap (``get_pixmap``) is rotated; it is mapped
with ``page.rotation_matrix`` / ``page.derotation_matrix``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

from formcast.errors import FormcastError
from formcast.geometry import Rect, norm_to_points
from formcast.pagemap.model import PageMap, Rule, TextLine, Widget, Word

# Drawn segments count as rules when this thin and at least this long.
RULE_MAX_THICKNESS = 2.0
RULE_MIN_LENGTH = 20.0
# A run of at least this many underscores is a fill-in line, not text.
UNDERSCORE_MIN_RUN = 3
# needs_ocr thresholds.
IMAGE_DOMINANT_FRACTION = 0.8
IMAGE_GRID = 4.0  # points per cell when measuring the union of image areas
MIN_TEXT_COVERAGE = 0.35
# text_layer_coverage rendering.
COVERAGE_DPI = 50
INK_THRESHOLD = 160  # gray level below which a pixel is ink
RULE_MASK_PAD = 2.0  # points around a rule whose ink is ignored
# Filled paths whose bbox fits in this many points may be glyph outlines.
GLYPH_MAX_SIZE = 20.0
# Paint this light (every channel) or this transparent leaves no visible ink.
NEAR_WHITE = 0.9
MIN_OPACITY = 0.01

# Expand ligatures into their letters so text matching works on plain words.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES

# PyMuPDF field_type -> pagemap widget kind (also used by the fill writer).
WIDGET_KINDS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combo",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "list",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
}


@dataclass
class _Char:
    c: str
    bbox: Rect
    baseline: float
    size: float
    bold: bool


def open_pdf(pdf: str | Path | bytes) -> pymupdf.Document:
    """Open a PDF from a path or bytes; the caller closes it.

    Raises:
        FormcastError: "encrypted PDF" when a password is needed, "cannot open
            PDF" when the input is missing, not a PDF, or has no pages.
    """
    try:
        if isinstance(pdf, (bytes, bytearray, memoryview)):
            doc = pymupdf.open(stream=bytes(pdf), filetype="pdf")
        else:
            doc = pymupdf.open(Path(pdf), filetype="pdf")
    except Exception as exc:  # pymupdf raises several types for unreadable input
        raise FormcastError("cannot open PDF") from exc
    if doc.needs_pass:
        doc.close()
        raise FormcastError("encrypted PDF")
    if not doc.is_pdf or doc.page_count == 0:
        doc.close()
        raise FormcastError("cannot open PDF")
    return doc


def page_size(page: pymupdf.Page) -> tuple[float, float]:
    """Width and height of the unrotated crop box, in points."""
    return page.cropbox.width, page.cropbox.height


def image_norm_to_points(page: pymupdf.Page, box: Rect) -> Rect:
    """Map a box normalized (0..1) to ``page.get_pixmap()`` into page points.

    The pixmap shows the crop box with /Rotate applied; the result is in
    unrotated crop-box space, the same space as words, rules and widgets.
    """
    shown = norm_to_points(box, (0.0, 0.0, page.rect.width, page.rect.height))
    r = pymupdf.Rect(shown) * page.derotation_matrix
    return (r.x0, r.y0, r.x1, r.y1)


def extract_page(page: pymupdf.Page) -> PageMap:
    """Text, rules and widgets of one page. ``kind`` is provisional; no candidates."""
    words, lines, underscore_rules = _extract_text(page)
    drawings = page.get_drawings()
    rules = _dedupe(_extract_drawn_rules(page, drawings) + underscore_rules)
    widgets = _extract_widgets(page)
    if widgets:
        kind = "fillable"
    elif _needs_ocr(page, words, rules, drawings):
        kind = "scanned"
    else:
        kind = "digital"
    width, height = page_size(page)
    return PageMap(
        index=page.number,
        width=width,
        height=height,
        rotation=page.rotation,
        kind=kind,
        text_source="pdf",
        words=words,
        lines=lines,
        rules=rules,
        widgets=widgets,
    )


def text_layer_coverage(page: pymupdf.Page, rules: list[Rule]) -> float:
    """Fraction of the page's unexplained ink that has extractable text over it.

    Renders the page at low dpi, counts dark pixels, and compares those inside
    word boxes against all dark pixels except those on ``rules`` or on
    geometric vector shapes (bars, boxes, frames; see :func:`_geometric_rects`).
    Glyph-like vector outlines stay unexplained, so labels drawn as paths
    still count against coverage. A page with no such ink counts as 1.0.
    """
    words, _, _ = _extract_text(page)
    return _coverage(page, words, rules, page.get_drawings())


def needs_ocr(page: pymupdf.Page) -> bool:
    """True when images cover most of the page or the text layer covers little ink."""
    words, _, underscore_rules = _extract_text(page)
    drawings = page.get_drawings()
    rules = _extract_drawn_rules(page, drawings) + underscore_rules
    return _needs_ocr(page, words, rules, drawings)


def image_coverage(page: pymupdf.Page) -> float:
    """Fraction of the crop box covered by images (their union, so tiled scans count whole)."""
    width, height = page_size(page)
    cols, rows = max(1, round(width / IMAGE_GRID)), max(1, round(height / IMAGE_GRID))
    grid = np.zeros((rows, cols), dtype=bool)
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        c0, c1 = round(max(0.0, x0) / width * cols), round(min(width, x1) / width * cols)
        r0, r1 = round(max(0.0, y0) / height * rows), round(min(height, y1) / height * rows)
        if c1 > c0 and r1 > r0:
            grid[r0:r1, c0:c1] = True
    return float(grid.mean())


def is_image_dominated(page: pymupdf.Page) -> bool:
    """True when images cover most of the page, as on a scan."""
    return image_coverage(page) > IMAGE_DOMINANT_FRACTION


def _needs_ocr(
    page: pymupdf.Page, words: list[Word], rules: list[Rule], drawings: list[dict]
) -> bool:
    if is_image_dominated(page):
        return True
    return _coverage(page, words, rules, drawings) < MIN_TEXT_COVERAGE


def _coverage(
    page: pymupdf.Page, words: list[Word], rules: list[Rule], drawings: list[dict]
) -> float:
    pix = page.get_pixmap(dpi=COVERAGE_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    ink = gray[:, : pix.width] < INK_THRESHOLD
    to_pixels = page.rotation_matrix * pymupdf.Matrix(COVERAGE_DPI / 72, COVERAGE_DPI / 72)

    def mask(rects: list[Rect], pad: float) -> np.ndarray:
        m = np.zeros_like(ink)
        for x0, y0, x1, y1 in rects:
            r = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) * to_pixels
            ir = r.irect  # smallest IRect containing r
            m[max(0, ir.y0) : max(0, ir.y1), max(0, ir.x0) : max(0, ir.x1)] = True
        return m

    explained = [r.rect for r in rules] + _geometric_rects(drawings)
    counted = ink & ~mask(explained, RULE_MASK_PAD)
    total = int(counted.sum())
    if total == 0:
        return 1.0
    covered = int((counted & mask([w.rect for w in words], 0.5)).sum())
    return covered / total


def _extract_text(page: pymupdf.Page) -> tuple[list[Word], list[TextLine], list[Rule]]:
    """Words and lines from ``rawdict``; long underscore runs become ``h`` rules.

    An underscore run also splits its line, so "Name ____ Date ____" yields the
    lines "Name" and "Date".
    """
    width, height = page_size(page)
    words: list[Word] = []
    lines: list[TextLine] = []
    rules: list[Rule] = []

    def emit_line(segment: list[list[_Char]]) -> None:
        segment = [w for w in segment if _inside(_union(c.bbox for c in w), width, height)]
        if not segment:
            return
        line_id = len(lines)
        word_ids = []
        for chars in segment:
            word_ids.append(len(words))
            words.append(
                Word(
                    text="".join(c.c for c in chars),
                    rect=_union(c.bbox for c in chars),
                    line_id=line_id,
                    source="pdf",
                )
            )
        chars = [c for w in segment for c in w]
        sizes: dict[float, int] = {}
        for c in chars:
            sizes[c.size] = sizes.get(c.size, 0) + 1
        lines.append(
            TextLine(
                id=line_id,
                text=" ".join(words[i].text for i in word_ids),
                rect=_union(words[i].rect for i in word_ids),
                word_ids=word_ids,
                font_size=max(sizes, key=sizes.get),
                bold=sum(c.bold for c in chars) * 2 > len(chars),
            )
        )

    for block in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]:
        for raw_line in block.get("lines", []):
            horizontal = abs(raw_line["dir"][1]) < 1e-3
            chars = [
                _Char(
                    c=ch["c"],
                    bbox=tuple(ch["bbox"]),
                    baseline=ch["origin"][1],
                    size=span["size"],
                    bold=bool(span["flags"] & pymupdf.TEXT_FONT_BOLD)
                    or "bold" in span["font"].lower(),
                )
                for span in raw_line["spans"]
                for ch in span["chars"]
            ]
            segment: list[list[_Char]] = []
            word: list[_Char] = []
            i = 0
            while i < len(chars):
                ch = chars[i]
                if ch.c == "_":
                    j = i
                    while j < len(chars) and chars[j].c == "_":
                        j += 1
                    run = chars[i:j]
                    if horizontal and len(run) >= UNDERSCORE_MIN_RUN:
                        if word:
                            segment.append(word)
                            word = []
                        emit_line(segment)
                        segment = []
                        # Underscores sit just below the baseline.
                        y = run[0].baseline + 0.1 * run[0].size
                        rules.append(
                            Rule(rect=(run[0].bbox[0], y, run[-1].bbox[2], y), orientation="h")
                        )
                    else:
                        word.extend(run)
                    i = j
                    continue
                if ch.c.isspace():
                    if word:
                        segment.append(word)
                        word = []
                else:
                    word.append(ch)
                i += 1
            if word:
                segment.append(word)
            emit_line(segment)
    return words, lines, rules


def _geometric_rects(drawings: list[dict]) -> list[Rect]:
    """Areas inked by geometric vector shapes, which need no text over them.

    Stroked paths count edge by edge (a frame does not hide what it encloses);
    filled paths count whole when they have no curves and are rects or larger
    than a glyph, or when they are bigger than a glyph both ways. Small filled
    outlines are left out: they may be label glyphs drawn as paths. Invisible
    paint (near-white or transparent, e.g. a white page background) never
    counts, so it cannot hide the ink drawn over it.
    """
    out: list[Rect] = []
    for path in drawings:
        items = path["items"]
        ops = {item[0] for item in items}
        bbox = path["rect"]
        fill_geometric = "c" not in ops and (
            ops <= {"re", "qu"} or max(bbox.width, bbox.height) > GLYPH_MAX_SIZE
        )
        if _fill_visible(path) and (
            fill_geometric or min(bbox.width, bbox.height) > GLYPH_MAX_SIZE
        ):
            out.append((bbox.x0, bbox.y0, bbox.x1, bbox.y1))
        elif _stroke_visible(path):
            pad = (path.get("width") or 1.0) / 2
            for item in items:
                if item[0] == "l":
                    x0, y0, x1, y1 = _seg_rect(item[1], item[2])
                    out.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
                elif item[0] in ("re", "qu"):
                    r = item[1].rect if item[0] == "qu" else item[1]
                    for x0, y0, x1, y1 in _edges(r):
                        out.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
                else:  # curve: its control-point hull
                    r = pymupdf.Rect(item[1], item[1]) | item[2] | item[3] | item[4]
                    out.append((r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad))
    return out


def _visible(color, opacity) -> bool:
    if color is None or (opacity is not None and opacity <= MIN_OPACITY):
        return False
    return not all(c >= NEAR_WHITE for c in color)


def _fill_visible(path: dict) -> bool:
    return "f" in (path.get("type") or "") and _visible(path.get("fill"), path.get("fill_opacity"))


def _stroke_visible(path: dict) -> bool:
    return "s" in (path.get("type") or "") and _visible(
        path.get("color"), path.get("stroke_opacity")
    )


def _edges(r: pymupdf.Rect) -> list[Rect]:
    return [
        (r.x0, r.y0, r.x1, r.y0),
        (r.x0, r.y1, r.x1, r.y1),
        (r.x0, r.y0, r.x0, r.y1),
        (r.x1, r.y0, r.x1, r.y1),
    ]


def _extract_drawn_rules(page: pymupdf.Page, drawings: list[dict]) -> list[Rule]:
    """Thin drawn lines; rectangles contribute their four edges."""
    width, height = page_size(page)
    rules: list[Rule] = []
    for path in drawings:
        if not (_fill_visible(path) or _stroke_visible(path)):
            continue  # leaves no visible ink
        for item in path["items"]:
            op = item[0]
            if op == "l":
                segments = [_seg_rect(item[1], item[2])]
            elif op in ("re", "qu"):
                r = item[1].rect if op == "qu" else item[1]
                if _classify(r.width, r.height):
                    segments = [(r.x0, r.y0, r.x1, r.y1)]
                else:
                    segments = _edges(r)
            else:
                continue  # curves are not rules
            for seg in segments:
                orientation = _classify(seg[2] - seg[0], seg[3] - seg[1])
                if orientation and _inside(seg, width, height):
                    rules.append(Rule(rect=seg, orientation=orientation))
    return rules


def choice_options(w: pymupdf.Widget) -> list[str]:
    """Display texts of a combo/list widget's options."""
    # Entries are either "text" or [export_value, display_text].
    return [str(v[-1]) if isinstance(v, (list, tuple)) else str(v) for v in w.choice_values or []]


def _extract_widgets(page: pymupdf.Page) -> list[Widget]:
    widgets = []
    for w in page.widgets() or []:
        kind = WIDGET_KINDS.get(w.field_type)
        if kind is None:
            continue
        options = choice_options(w) if kind in ("combo", "list") else []
        r = w.rect
        widgets.append(
            Widget(
                name=w.field_name or "",
                kind=kind,
                rect=(r.x0, r.y0, r.x1, r.y1),
                options=options,
                tooltip=w.field_label or None,
            )
        )
    return widgets


def _classify(w: float, h: float) -> str | None:
    if h <= RULE_MAX_THICKNESS and w >= RULE_MIN_LENGTH:
        return "h"
    if w <= RULE_MAX_THICKNESS and h >= RULE_MIN_LENGTH:
        return "v"
    return None


def _seg_rect(p1: pymupdf.Point, p2: pymupdf.Point) -> Rect:
    return (min(p1.x, p2.x), min(p1.y, p2.y), max(p1.x, p2.x), max(p1.y, p2.y))


def _union(rects) -> Rect:
    rs = list(rects)
    return (
        min(r[0] for r in rs),
        min(r[1] for r in rs),
        max(r[2] for r in rs),
        max(r[3] for r in rs),
    )


def _inside(r: Rect, width: float, height: float) -> bool:
    """True when ``r`` (possibly degenerate) touches the crop box."""
    return r[2] >= 0 and r[0] <= width and r[3] >= 0 and r[1] <= height


def _dedupe(rules: list[Rule]) -> list[Rule]:
    seen: set[tuple] = set()
    out = []
    for r in rules:
        key = (r.orientation, *(round(v, 1) for v in r.rect))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
