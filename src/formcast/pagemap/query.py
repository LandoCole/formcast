"""Query helpers over a :class:`FormMap`, exposed to the agent's sandboxed code.

Everything here is pure Python on the page-map model (no PyMuPDF), so it can
run inside the sandbox worker. Every query returns plain JSON-serializable
dicts or lists of dicts. Each item has ``type``, ``id``, ``page`` and ``rect``
(``[x0, y0, x1, y1]`` in PDF points, origin top-left, rounded to 0.1pt).

Ids are prefixed by type and page: ``c0_3`` candidate, ``l0_5`` text line,
``t0_12`` word, ``r0_2`` rule, ``w0_1`` widget.

The module also holds the geometric rules the rest of the page map shares:
:func:`find_label` (label links), :func:`sublabels` and :func:`right_text`.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from formcast.geometry import Rect, horizontal_overlap
from formcast.pagemap.model import Candidate, FormMap, PageMap, TextLine, Widget

# --- label-link rule (find_label) ---
# A line is on the candidate's row when their vertical centers differ by less
# than this fraction of the line's height.
SAME_ROW_FRACTION = 0.6
# A line above may end at most this far above the candidate's top ...
ABOVE_MAX_GAP = 18.0
# ... and must overlap it horizontally by this fraction (of the narrower one).
MIN_OVERLAP = 0.3
# A line below (a caption under a blank) may start at most this far below,
# in a font at most this many times the page's median.
BELOW_MAX_GAP = 14.0
BELOW_MAX_FONT_RATIO = 1.1
# Edges this close count as touching.
EDGE_TOL = 2.0
# --- sub-labels ---
SUBLABEL_MAX_GAP = 14.0
# A sub-label's font is at most this fraction of the page's median line font.
SUBLABEL_FONT_RATIO = 0.9
# --- text right after a box ---
RIGHT_MAX_GAP = 24.0

DEFAULT_TYPES = ("line", "candidate", "widget", "rule")


# --- small geometry ---------------------------------------------------------


def _cx(r: Rect) -> float:
    return (r[0] + r[2]) / 2


def _cy(r: Rect) -> float:
    return (r[1] + r[3]) / 2


def _touches(a: Rect, b: Rect) -> bool:
    """Overlap test that also works for zero-height or zero-width rects (rules)."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _rect_distance(a: Rect, b: Rect) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return (dx * dx + dy * dy) ** 0.5


def _contained_fraction(inner: Rect, outer: Rect) -> float:
    """Share of ``inner``'s area that lies inside ``outer``."""
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if w <= 0 or h <= 0 or area <= 0:
        return 0.0
    return w * h / area


def _round(r: Rect) -> list[float]:
    return [round(float(v), 1) for v in r]


# --- shared rules -------------------------------------------------------------


def _blockers(page: PageMap, self_rect: Rect) -> list[tuple[str, Rect]]:
    """(kind, rect) of every candidate and widget on the page except the one at ``self_rect``."""
    out = [(c.kind, c.rect) for c in page.candidates if c.rect != self_rect]
    out += [(w.kind, w.rect) for w in page.widgets if w.rect != self_rect]
    return out


def _box_between(corridor: Rect, blockers: list[tuple[str, Rect]]) -> bool:
    """True when another field box overlaps the gap ``corridor`` between a line and a box."""
    x0, y0, x1, y1 = corridor
    if x1 - x0 <= 1.0 or y1 - y0 <= 1.0:  # a touching pair has nothing between
        return False
    inner = (x0 + 0.5, y0 + 0.5, x1 - 0.5, y1 - 0.5)
    return any(
        r[0] < inner[2] and r[2] > inner[0] and r[1] < inner[3] and r[3] > inner[1]
        for _, r in blockers
    )


def _is_choice(kind: str) -> bool:
    return kind in ("choice", "checkbox", "radio")


def _height(r: Rect) -> float:
    return max(r[3] - r[1], 1.0)


def _lead_gap(box: Rect, line: Rect) -> float | None:
    """Gap from ``box`` to the start of ``line`` when the line starts right at or after it.

    Negative when the box overlaps the line's start, as with a checkbox glyph
    printed as the first character of its option text. ``None`` when the box
    is not at the line's start or not on its row.
    """
    width = box[2] - box[0]
    at_start = box[0] <= line[0] + EDGE_TOL and box[2] <= line[0] + width + EDGE_TOL
    same_row = abs(_cy(box) - _cy(line)) < SAME_ROW_FRACTION * max(_height(line), _height(box))
    gap = line[0] - box[2]
    if at_start and same_row and gap <= RIGHT_MAX_GAP:
        return gap
    return None


def _option_owner_gap(line: TextLine, blockers: list[tuple[str, Rect]]) -> float | None:
    """Smallest lead gap from another choice box to ``line``: the line is that box's option text."""
    gaps = [
        g
        for kind, box in blockers
        if _is_choice(kind) and (g := _lead_gap(box, line.rect)) is not None
    ]
    return min(gaps) if gaps else None


def _caption_of_other(
    line: TextLine, gap_to_self: float, blockers: list[tuple[str, Rect]]
) -> bool:
    """True when ``line`` is the caption just under another box, closer to it than to us."""
    lr = line.rect
    for _, b in blockers:
        gap = lr[1] - b[3]
        if (
            -EDGE_TOL <= gap <= BELOW_MAX_GAP
            and _cy(lr) > b[3]
            and horizontal_overlap(lr, b) >= MIN_OVERLAP
            and gap < gap_to_self
        ):
            return True
    return False


def _heading_of_other(
    line: TextLine, gap_to_self: float, blockers: list[tuple[str, Rect]]
) -> bool:
    """True when ``line`` sits just above another box, closer to it than to us."""
    lr = line.rect
    for _, b in blockers:
        gap = b[1] - lr[3]
        if (
            -EDGE_TOL <= gap <= ABOVE_MAX_GAP
            and _cy(lr) < b[1]
            and horizontal_overlap(lr, b) >= MIN_OVERLAP
            and gap < gap_to_self
        ):
            return True
    return False


def median_font_size(page: PageMap) -> float | None:
    sizes = [ln.font_size for ln in page.lines if ln.font_size]
    return statistics.median(sizes) if sizes else None


def find_label(
    page: PageMap, rect: Rect, kind: str = "text"
) -> tuple[TextLine, str] | None:
    """The label line of a field box at ``rect`` and how it relates to it.

    In order, the first rule that matches wins:

    1. ``left``: the nearest line to the left on the same row (vertical
       centers closer than 0.6 x the line's height);
    2. ``above``: the nearest line whose bottom is within 18pt above the box
       and which overlaps it horizontally by at least 30%;
    3. ``inside``: a line inside the box (the first in reading order);
    4. ``below``: the nearest line starting within 14pt below the box, with the
       same 30% overlap (a caption under a blank).

    A line belongs to the nearest field box, so a candidate line is skipped when
    another field box (candidate or widget) lies between it and the box, when
    it is the caption right under another box (for ``above``), when it sits
    right above another box (for ``below``), and, for a choice box, when it is
    another choice box's option text (it starts right at or after that box;
    for ``left``, closer to that box than to this one). A ``below`` caption is never
    in a font larger than 1.1 x the page's median (that is a heading).
    """
    blockers = _blockers(page, rect)
    choice = _is_choice(kind)
    median = median_font_size(page)
    x0, y0, x1, y1 = rect

    left: list[tuple[float, TextLine]] = []
    above: list[tuple[float, float, TextLine]] = []
    inside: list[TextLine] = []
    below: list[tuple[float, float, TextLine]] = []
    for line in page.lines:
        lr = line.rect
        if _contained_fraction(lr, rect) >= 0.5:
            inside.append(line)
            continue
        overlap = horizontal_overlap(lr, rect)
        if abs(_cy(lr) - _cy(rect)) < SAME_ROW_FRACTION * _height(lr) and lr[2] <= x0 + EDGE_TOL:
            gap = x0 - lr[2]
            band = (max(lr[1], y0), min(lr[3], y1))
            if band[0] >= band[1]:
                band = (lr[1], lr[3])
            if _box_between((lr[2], band[0], x0, band[1]), blockers):
                continue
            owner = _option_owner_gap(line, blockers) if choice else None
            if owner is not None and owner < gap:
                continue  # "[ ] Yes  [ ] No": "Yes" is the first box's option
            left.append((gap, line))
            continue
        gap = y0 - lr[3]
        if -EDGE_TOL <= gap <= ABOVE_MAX_GAP and _cy(lr) < y0 and overlap >= MIN_OVERLAP:
            corridor = (max(lr[0], x0), lr[3], min(lr[2], x1), y0)
            if (
                not _box_between(corridor, blockers)
                and not _caption_of_other(line, gap, blockers)
                and not (choice and _option_owner_gap(line, blockers) is not None)
            ):
                above.append((gap, -overlap, line))
            continue
        gap = lr[1] - y1
        if -EDGE_TOL <= gap <= BELOW_MAX_GAP and _cy(lr) > y1 and overlap >= MIN_OVERLAP:
            heading = (
                median is not None
                and line.font_size is not None
                and line.font_size > BELOW_MAX_FONT_RATIO * median
            )
            if (
                not heading
                and not _heading_of_other(line, gap, blockers)
                and not (choice and _option_owner_gap(line, blockers) is not None)
            ):
                below.append((gap, -overlap, line))

    if left:
        return min(left, key=lambda t: t[0])[1], "left"
    if above:
        return min(above, key=lambda t: t[:2])[2], "above"
    if inside:
        return min(inside, key=lambda ln: (ln.rect[1], ln.rect[0])), "inside"
    if below:
        return min(below, key=lambda t: t[:2])[2], "below"
    return None


def sublabels(page: PageMap, rect: Rect) -> list[TextLine]:
    """Small-font lines starting within 14pt below ``rect``, mostly under it, left to right.

    "Small" means at most 0.9 x the page's median line font size, as with
    "Street / City / State / Zip" printed under one long address line.
    """
    median = median_font_size(page)
    if median is None:
        return []
    out = []
    for line in page.lines:
        lr = line.rect
        if not line.font_size or line.font_size > SUBLABEL_FONT_RATIO * median:
            continue
        gap = lr[1] - rect[3]
        if not -EDGE_TOL <= gap <= SUBLABEL_MAX_GAP or _cy(lr) <= rect[3]:
            continue
        width = lr[2] - lr[0]
        under = min(lr[2], rect[2]) - max(lr[0], rect[0])
        if width > 0 and under / width >= 0.5:
            out.append(line)
    return sorted(out, key=lambda ln: (ln.rect[0], ln.rect[1]))


def right_text(page: PageMap, rect: Rect) -> TextLine | None:
    """The line starting right at or just after a box on its row, unless another box is between.

    The line must start within 24pt of the box (or at the box itself, when a
    checkbox glyph is the line's first character). For a choice box this is
    its option text (the "Yes" in "[ ] Yes"); after an underline it may be the
    option the underline stands for or the next field's label.
    """
    blockers = _blockers(page, rect)
    best: tuple[float, TextLine] | None = None
    for line in page.lines:
        lr = line.rect
        gap = _lead_gap(rect, lr)
        if gap is None:
            continue
        box_between = any(
            o[0] >= rect[2] - EDGE_TOL
            and o[2] <= lr[0] + EDGE_TOL
            and abs(_cy(o) - _cy(lr)) < SAME_ROW_FRACTION * _height(lr)
            for _, o in blockers
        )
        if box_between:
            continue
        if best is None or gap < best[0]:
            best = (gap, line)
    return best[1] if best else None


def _modal_pitch(gaps: list[float]) -> float:
    """The most common spacing among adjacent-row gaps (ties: the smallest), refined
    with every gap that is a whole multiple of it (rows with no box in between)."""
    groups: list[list[float]] = []
    for g in sorted(gaps):
        if groups and g <= groups[-1][0] * 1.15:
            groups[-1].append(g)
        else:
            groups.append([g])
    base = statistics.median(max(groups, key=len))  # max keeps the first (smallest) on ties
    fits = [g / n for g in gaps if (n := round(g / base)) >= 1 and abs(g / n - base) <= 0.15 * base]
    return statistics.median(fits) if fits else base


# --- the agent-facing query object ---------------------------------------------


class FormMapQuery:
    """Read-only queries over a form map, returning plain dicts.

    ``page`` arguments are page indexes. Where a method takes a reference
    item, it accepts an id string (``"c0_3"``, ``"l0_5"``, ...), any dict this
    class returned (it has ``rect`` and ``page``), or a bare rect
    ``[x0, y0, x1, y1]`` together with ``page``.
    """

    def __init__(self, form_map: FormMap | dict):
        if isinstance(form_map, dict):
            form_map = FormMap.model_validate(form_map)
        self.form_map = form_map
        self._pages = {p.index: p for p in form_map.pages}
        self._index: dict[str, dict] = {}
        for p in form_map.pages:
            for item in self._page_items(p, ("line", "candidate", "widget", "rule", "word")):
                self._index[item["id"]] = item

    # -- item builders --

    def _line(self, p: PageMap, line: TextLine) -> dict:
        return {
            "type": "line",
            "id": f"l{p.index}_{line.id}",
            "page": p.index,
            "text": line.text,
            "rect": _round(line.rect),
            "font_size": round(line.font_size, 1) if line.font_size else None,
            "bold": line.bold,
        }

    def _line_by_id(self, p: PageMap, line_id: int) -> TextLine | None:
        return next((ln for ln in p.lines if ln.id == line_id), None)

    def _candidate(self, p: PageMap, c: Candidate) -> dict:
        label = self._line_by_id(p, c.label_line_id) if c.label_line_id is not None else None
        return {
            "type": "candidate",
            "id": c.id,
            "page": p.index,
            "kind": c.kind,
            "rect": _round(c.rect),
            "conf": round(c.conf, 3),
            "detector": c.detector,
            "label": label.text if label else None,
            "label_id": f"l{p.index}_{label.id}" if label else None,
            "label_relation": c.label_relation if label else None,
        }

    def _widget(self, p: PageMap, i: int, w: Widget) -> dict:
        return {
            "type": "widget",
            "id": f"w{p.index}_{i}",
            "page": p.index,
            "name": w.name,
            "kind": w.kind,
            "rect": _round(w.rect),
            "options": list(w.options),
            "tooltip": w.tooltip,
        }

    def _page_items(self, p: PageMap, types) -> list[dict]:
        items: list[dict] = []
        if "line" in types:
            items += [self._line(p, ln) for ln in p.lines]
        if "candidate" in types:
            items += [self._candidate(p, c) for c in p.candidates]
        if "widget" in types:
            items += [self._widget(p, i, w) for i, w in enumerate(p.widgets)]
        if "rule" in types:
            for i, r in enumerate(p.rules):
                x0, y0, x1, y1 = r.rect
                items.append({
                    "type": "rule",
                    "id": f"r{p.index}_{i}",
                    "page": p.index,
                    "rect": _round(r.rect),
                    "orientation": r.orientation,
                    "length": round(max(x1 - x0, y1 - y0), 1),
                })
        if "word" in types:
            for i, w in enumerate(p.words):
                items.append({
                    "type": "word",
                    "id": f"t{p.index}_{i}",
                    "page": p.index,
                    "text": w.text,
                    "rect": _round(w.rect),
                    "line_id": f"l{p.index}_{w.line_id}",
                    "conf": round(w.conf, 3),
                })
        return items

    def _page(self, page: int) -> PageMap:
        if page not in self._pages:
            raise ValueError(f"no page {page}; pages are {sorted(self._pages)}")
        return self._pages[page]

    def _page_list(self, page: int | None) -> list[PageMap]:
        if page is None:
            return list(self.form_map.pages)
        return [self._pages[page]] if page in self._pages else []

    def _resolve(self, ref: Any, page: int | None) -> tuple[int, Rect, str | None]:
        """(page, rect, own id) of a reference item."""
        if isinstance(ref, str):
            if ref not in self._index:
                raise KeyError(f"unknown id {ref!r}")
            item = self._index[ref]
            return item["page"], tuple(item["rect"]), ref
        if isinstance(ref, dict) and "rect" in ref:
            p = ref.get("page", page) if page is None else page
            rect, own = tuple(ref["rect"]), ref.get("id")
        elif isinstance(ref, (list, tuple)) and len(ref) == 4:
            p, rect, own = page, tuple(float(v) for v in ref), None
        else:
            raise TypeError("expected an id string, an item dict with 'rect', or [x0, y0, x1, y1]")
        if p is None:
            raise ValueError("a bare rect needs page=")
        self._page(p)
        return p, rect, own

    # -- basic listings --

    def pages(self) -> list[dict]:
        """Index, size, kind, rotation, text source and notes of each page."""
        return [
            {
                "page": p.index,
                "width": round(p.width, 1),
                "height": round(p.height, 1),
                "kind": p.kind,
                "rotation": p.rotation,
                "text_source": p.text_source,
                "notes": list(p.notes),
            }
            for p in self.form_map.pages
        ]

    def words(self, page: int | None = None) -> list[dict]:
        return [i for p in self._page_list(page) for i in self._page_items(p, ("word",))]

    def lines(self, page: int | None = None) -> list[dict]:
        return [self._line(p, ln) for p in self._page_list(page) for ln in p.lines]

    def find_text(self, pattern: str, page: int | None = None, regex: bool = False) -> list[dict]:
        """Lines containing ``pattern`` (case-insensitive substring, or a regex with ``regex=True``)."""
        if regex:
            rx = re.compile(pattern, re.IGNORECASE)
            match = lambda text: rx.search(text) is not None  # noqa: E731
        else:
            needle = " ".join(pattern.split()).casefold()
            match = lambda text: needle in " ".join(text.split()).casefold()  # noqa: E731
        return [
            self._line(p, ln) for p in self._page_list(page) for ln in p.lines if match(ln.text)
        ]

    def candidates(
        self, page: int | None = None, kind: str | None = None, min_conf: float = 0.0
    ) -> list[dict]:
        """Detector candidates in reading order, with their linked label text."""
        return [
            self._candidate(p, c)
            for p in self._page_list(page)
            for c in p.candidates
            if (kind is None or c.kind == kind) and c.conf >= min_conf
        ]

    def rules(self, page: int | None = None, orientation: str | None = None) -> list[dict]:
        """Drawn lines and underscore runs; ``orientation`` is ``"h"`` or ``"v"``."""
        return [
            r
            for p in self._page_list(page)
            for r in self._page_items(p, ("rule",))
            if orientation is None or r["orientation"] == orientation
        ]

    def widgets(self, page: int | None = None) -> list[dict]:
        return [i for p in self._page_list(page) for i in self._page_items(p, ("widget",))]

    # -- spatial queries --

    def _directional(self, ref, page, direction: str, max_dist: float, types) -> list[dict]:
        p, ref_rect, own = self._resolve(ref, page)
        rx0, ry0, rx1, ry1 = ref_rect
        out = []
        for item in self._page_items(self._pages[p], tuple(types or DEFAULT_TYPES)):
            if item["id"] == own:
                continue
            x0, y0, x1, y1 = r = tuple(item["rect"])
            same_row = abs(_cy(r) - _cy(ref_rect)) <= 0.5 * max(ry1 - ry0, y1 - y0) + EDGE_TOL
            same_col = min(x1, rx1) - max(x0, rx0) >= 0
            if direction == "right":
                ok = same_row and (x0 >= rx1 - EDGE_TOL or (_cx(r) > rx1 and x0 >= rx0))
                dist = max(0.0, x0 - rx1)
            elif direction == "left":
                ok = same_row and (x1 <= rx0 + EDGE_TOL or (_cx(r) < rx0 and x1 <= rx1))
                dist = max(0.0, rx0 - x1)
            elif direction == "above":
                ok = same_col and (y1 <= ry0 + EDGE_TOL or (_cy(r) < ry0 and y0 <= ry0))
                dist = max(0.0, ry0 - y1)
            else:
                ok = same_col and (y0 >= ry1 - EDGE_TOL or (_cy(r) > ry1 and y1 >= ry1))
                dist = max(0.0, y0 - ry1)
            if ok and dist <= max_dist:
                out.append((dist, abs(_cx(r) - _cx(ref_rect)) + abs(_cy(r) - _cy(ref_rect)), item))
        out.sort(key=lambda t: t[:2])
        return [{**item, "dist": round(dist, 1)} for dist, _, item in out]

    def right_of(self, ref, page: int | None = None, max_dist: float = 300, types=None) -> list[dict]:
        """Items on the same row to the right of ``ref``, nearest first, each with ``dist``.

        ``types`` limits the result to some of "line", "candidate", "widget",
        "rule" (all four by default).
        """
        return self._directional(ref, page, "right", max_dist, types)

    def left_of(self, ref, page: int | None = None, max_dist: float = 300, types=None) -> list[dict]:
        """Items on the same row to the left of ``ref``, nearest first."""
        return self._directional(ref, page, "left", max_dist, types)

    def above(self, ref, page: int | None = None, max_dist: float = 300, types=None) -> list[dict]:
        """Items above ``ref`` that overlap its horizontal span, nearest first."""
        return self._directional(ref, page, "above", max_dist, types)

    def below(self, ref, page: int | None = None, max_dist: float = 300, types=None) -> list[dict]:
        """Items below ``ref`` that overlap its horizontal span, nearest first."""
        return self._directional(ref, page, "below", max_dist, types)

    def near(self, ref, page: int | None = None, radius: float = 36, types=None) -> list[dict]:
        """Items whose rect is within ``radius`` points of ``ref`` (edge to edge), nearest first."""
        p, ref_rect, own = self._resolve(ref, page)
        out = []
        for item in self._page_items(self._pages[p], tuple(types or DEFAULT_TYPES)):
            dist = _rect_distance(ref_rect, tuple(item["rect"]))
            if item["id"] != own and dist <= radius:
                out.append((dist, item))
        out.sort(key=lambda t: t[0])
        return [{**item, "dist": round(dist, 1)} for dist, item in out]

    def region(self, page: int, rect) -> dict:
        """Lines, rules, candidates and widgets touching ``rect`` on ``page``."""
        p = self._page(page)
        rect = tuple(float(v) for v in rect)
        groups = {"lines": "line", "rules": "rule", "candidates": "candidate", "widgets": "widget"}
        return {
            key: [i for i in self._page_items(p, (t,)) if _touches(tuple(i["rect"]), rect)]
            for key, t in groups.items()
        }

    # -- structure --

    def grid(self, refs, page: int | None = None) -> dict:
        """Rows and columns of boxes laid out as a grid, in the shape add_table needs.

        ``refs`` are ids, item dicts (template fields work too) or rects (then give
        ``page``), all on one page: for example the candidates or widget fields a
        table will replace. Boxes whose vertical centers are within half the
        median box height share a row. The row pitch is the most common spacing
        between adjacent rows (gaps that are a whole multiple of it are rows
        with no box), and each row gets the index ``round((top - row_top) /
        pitch)``. Boxes overlapping horizontally (half of the narrower) share a
        column; a box over two or more columns is not merged into one, it is
        listed in ``spanning``.

        Returns ``page``; ``row_top``, ``row_height`` (the pitch; one row's
        height when there is one row) and ``max_rows``, which counts the rows
        from the first to the last given box only: the printed table may have
        more rows above or below (check the rules and text there);
        ``missing_rows`` (indexes in that range with no box); ``rect``
        (spanning those rows at the pitch); ``rows`` ([top, bottom] per row
        with boxes) and ``columns`` ([x0, x1] per column), medians over the
        boxes so one stray detection does not shift them; ``regular`` (false
        when some gap between rows is not a whole multiple of the pitch);
        ``cells``, one ``{row, col, id}`` per box (id None for a bare rect);
        ``column_fields``, per column the box id in each of the ``max_rows`` rows
        (None where the row has none; the first box when a row has two), ready
        to pass as a table column's ``fields`` so each cell keeps its box;
        and ``spanning``, one ``{row, id, rect}`` per box over several columns.
        """
        boxes = [self._resolve(ref, page) for ref in refs]
        if not boxes:
            raise ValueError("grid needs at least one box")
        pages = {p for p, _, _ in boxes}
        if len(pages) > 1:
            raise ValueError(f"grid boxes must be on one page, got pages {sorted(pages)}")
        med = statistics.median
        tol = 0.5 * med(_height(r) for _, r, _ in boxes)

        # -- rows: cluster by vertical center, then index by the modal pitch
        clusters: list[list[int]] = []
        for i in sorted(range(len(boxes)), key=lambda i: _cy(boxes[i][1])):
            centre = statistics.mean(_cy(boxes[j][1]) for j in clusters[-1]) if clusters else None
            if centre is not None and abs(_cy(boxes[i][1]) - centre) <= tol:
                clusters[-1].append(i)
            else:
                clusters.append([i])
        bands = [[med(boxes[i][1][1] for i in c), med(boxes[i][1][3] for i in c)] for c in clusters]
        tops = [b[0] for b in bands]
        gaps = [b - a for a, b in zip(tops, tops[1:])]
        pitch = _modal_pitch(gaps) if gaps else bands[0][1] - bands[0][0]
        index = [round((t - tops[0]) / pitch) for t in tops]
        row_of = {i: index[k] for k, c in enumerate(clusters) for i in c}
        fit = max(1.5, 0.15 * pitch)
        on_pitch = {
            i for k, c in enumerate(clusters) if abs(tops[k] - tops[0] - index[k] * pitch) <= fit for i in c
        }
        regular = all(
            abs(g - pitch * max(1, round(g / pitch))) <= max(1.5, 0.15 * pitch * max(1, round(g / pitch)))
            for g in gaps
        )
        row_top = med(boxes[i][1][1] - row_of[i] * pitch for i in range(len(boxes)))
        max_rows = index[-1] + 1
        missing = sorted(set(range(max_rows)) - set(index))

        # -- columns: narrow boxes first define them; spans are medians, never unions
        def span(members: list[int]) -> list[float]:
            return [med(boxes[i][1][0] for i in members), med(boxes[i][1][2] for i in members)]

        def overlaps(r: Rect, sp: list[float]) -> bool:
            ov = min(r[2], sp[1]) - max(r[0], sp[0])
            return ov > 0 and ov >= 0.5 * min(r[2] - r[0], sp[1] - sp[0])

        cols: list[list[int]] = []
        for i in sorted(range(len(boxes)), key=lambda i: boxes[i][1][2] - boxes[i][1][0]):
            k = next((k for k, c in enumerate(cols) if overlaps(boxes[i][1], span(c))), None)
            if k is None:
                cols.append([i])
            else:
                cols[k].append(i)
        spans = [span(c) for c in cols]

        def covered(r: Rect, sp: list[float]) -> bool:  # covers at least half of the column's width
            return min(r[2], sp[1]) - max(r[0], sp[0]) >= 0.5 * (sp[1] - sp[0])

        wide = {i for i in range(len(boxes)) if sum(covered(boxes[i][1], sp) for sp in spans) >= 2}
        cols = [[i for i in c if i not in wide] for c in cols]
        cols = [c for c in cols if c]
        cols.sort(key=lambda c: span(c)[0])
        columns = [span(c) for c in cols]
        col_of = {i: n for n, c in enumerate(cols) for i in c}

        # Refine the pitch with same-column box pairs (median of top difference per row step):
        # unbiased by alternating jitter that skews the adjacent gaps.
        pairs = [
            (boxes[j][1][1] - boxes[i][1][1]) / (row_of[j] - row_of[i])
            for i in col_of
            for j in col_of
            if col_of[i] == col_of[j] and row_of[j] > row_of[i] and {i, j} <= on_pitch
        ]
        if pairs and abs(med(pairs) - pitch) <= 0.15 * pitch:
            pitch = med(pairs)
            row_top = med(boxes[i][1][1] - row_of[i] * pitch for i in range(len(boxes)))
        bottom = max(row_top + pitch * max_rows, bands[-1][1])
        column_fields: list[list[str | None]] = [[None] * max_rows for _ in columns]
        for i in sorted(col_of, key=lambda i: (row_of[i], boxes[i][1][0])):
            row, col = row_of[i], col_of[i]
            if column_fields[col][row] is None:
                column_fields[col][row] = boxes[i][2]
        x0 = min([c[0] for c in columns] + [boxes[i][1][0] for i in wide])
        x1 = max([c[1] for c in columns] + [boxes[i][1][2] for i in wide])
        return {
            "page": boxes[0][0],
            "rect": _round((x0, row_top, x1, bottom)),
            "row_top": round(row_top, 1),
            "row_height": round(pitch, 1),
            "max_rows": max_rows,
            "missing_rows": missing,
            "rows": [_round((0, a, 0, b))[1::2] for a, b in bands],
            "columns": [_round((a, 0, b, 0))[0::2] for a, b in columns],
            "regular": regular,
            "cells": [
                {"row": row_of[i], "col": col_of[i], "id": boxes[i][2]}
                for i in sorted(col_of, key=lambda i: (row_of[i], col_of[i]))
            ],
            "column_fields": column_fields,
            "spanning": [
                {"row": row_of[i], "id": boxes[i][2], "rect": _round(boxes[i][1])}
                for i in sorted(wide, key=lambda i: (row_of[i], boxes[i][1][0]))
            ],
        }

    # -- labels --

    def label_for(self, item_id: str) -> dict | None:
        """The label line of a candidate (as linked by the page map) or widget, with ``relation``."""
        if item_id not in self._index or self._index[item_id]["type"] not in ("candidate", "widget"):
            raise KeyError(f"unknown candidate or widget id {item_id!r}")
        item = self._index[item_id]
        p = self._pages[item["page"]]
        if item["type"] == "candidate":
            cand = next(c for c in p.candidates if c.id == item_id)
            line = self._line_by_id(p, cand.label_line_id) if cand.label_line_id is not None else None
            relation = cand.label_relation
        else:
            widget = p.widgets[int(item_id.split("_")[1])]
            found = find_label(p, widget.rect, widget.kind)
            line, relation = found if found else (None, None)
        if line is None:
            return None
        return {**self._line(p, line), "relation": relation}

    def sublabels_under(self, ref, page: int | None = None) -> list[dict]:
        """Small-font lines within 14pt below ``ref`` (e.g. Street / City / State / Zip)."""
        p, rect, _ = self._resolve(ref, page)
        pm = self._pages[p]
        return [self._line(pm, ln) for ln in sublabels(pm, rect)]
