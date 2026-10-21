"""Pure rectangle helpers. Rects are ``(x0, y0, x1, y1)`` with origin top-left."""

from __future__ import annotations

Rect = tuple[float, float, float, float]


def area(r: Rect) -> float:
    """Area of ``r``; zero for empty or inverted rects."""
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def intersect(a: Rect, b: Rect) -> Rect | None:
    """Intersection of two rects, or None when they do not overlap with positive area."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def iou(a: Rect, b: Rect) -> float:
    """Intersection over union; 0.0 for disjoint or degenerate rects."""
    inter = intersect(a, b)
    if inter is None:
        return 0.0
    inter_area = area(inter)
    union = area(a) + area(b) - inter_area
    return inter_area / union if union > 0 else 0.0


def contained_fraction(inner: Rect, outer: Rect) -> float:
    """Share (0..1) of ``inner``'s area that lies inside ``outer``; 0.0 for disjoint or empty ``inner``."""
    a = area(inner)
    inter = intersect(inner, outer)
    if inter is None or a <= 0:
        return 0.0
    return area(inter) / a


def norm_to_points(box: Rect, page_rect: Rect) -> Rect:
    """Map a box normalized to ``page_rect`` (0..1 on both axes) into point space."""
    px0, py0, px1, py1 = page_rect
    w, h = px1 - px0, py1 - py0
    return (
        px0 + box[0] * w,
        py0 + box[1] * h,
        px0 + box[2] * w,
        py0 + box[3] * h,
    )


def points_to_norm(r: Rect, page_rect: Rect) -> Rect:
    """Inverse of :func:`norm_to_points`."""
    px0, py0, px1, py1 = page_rect
    w, h = px1 - px0, py1 - py0
    return (
        (r[0] - px0) / w,
        (r[1] - py0) / h,
        (r[2] - px0) / w,
        (r[3] - py0) / h,
    )


def horizontal_overlap(a: Rect, b: Rect) -> float:
    """Horizontal overlap as a fraction (0..1) of the smaller width."""
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    smaller = min(a[2] - a[0], b[2] - b[0])
    if overlap <= 0 or smaller <= 0:
        return 0.0
    return min(1.0, overlap / smaller)


def vertical_gap(upper: Rect, lower: Rect) -> float:
    """Distance from the bottom of ``upper`` to the top of ``lower`` (negative if they overlap)."""
    return lower[1] - upper[3]
