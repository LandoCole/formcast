import pytest

from formcast.geometry import (
    area,
    horizontal_overlap,
    intersect,
    iou,
    norm_to_points,
    points_to_norm,
    vertical_gap,
)


def test_iou_partial_overlap():
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)


def test_iou_disjoint_is_zero():
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_touching_edges_is_zero():
    assert iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0


def test_iou_degenerate_is_zero():
    assert iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0


def test_area_and_degenerate():
    assert area((0, 0, 10, 5)) == 50
    assert area((5, 5, 5, 10)) == 0
    assert area((10, 10, 0, 0)) == 0


def test_intersect():
    assert intersect((0, 0, 10, 10), (5, 5, 15, 15)) == (5, 5, 10, 10)
    assert intersect((0, 0, 10, 10), (10, 0, 20, 10)) is None
    assert intersect((0, 0, 10, 10), (20, 20, 30, 30)) is None


def test_norm_to_points_full_page():
    assert norm_to_points((0.5, 0.5, 1, 1), (0, 0, 612, 792)) == (306, 396, 612, 792)


def test_norm_to_points_crop_box_offset():
    crop = (36, 36, 576, 756)
    assert norm_to_points((0, 0, 1, 1), crop) == crop


def test_points_to_norm_round_trip():
    crop = (36, 36, 576, 756)
    box = (0.1, 0.25, 0.6, 0.9)
    pts = norm_to_points(box, crop)
    assert points_to_norm(pts, crop) == pytest.approx(box)


def test_horizontal_overlap():
    assert horizontal_overlap((0, 0, 10, 1), (5, 0, 30, 1)) == 0.5
    assert horizontal_overlap((0, 0, 10, 1), (20, 0, 30, 1)) == 0.0
    assert horizontal_overlap((0, 0, 0, 1), (0, 0, 10, 1)) == 0.0


def test_vertical_gap():
    assert vertical_gap((0, 0, 10, 10), (0, 15, 10, 25)) == 5
    assert vertical_gap((0, 0, 10, 10), (0, 8, 10, 25)) == -2
