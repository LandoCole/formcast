from pathlib import Path

import numpy as np
import pytest

from formcast.geometry import iou
from formcast.pagemap.detect import (
    ONNX_IMAGE_SIZE,
    ONNX_URLS,
    FFDNetDetector,
    FFDNetOnnxDetector,
    _decode,
    _Letterbox,
)
from formcast.pagemap.extract import open_pdf
from formcast.pagemap.ocr import render

FORMS = Path(__file__).resolve().parents[2] / "forms"


@pytest.fixture
def fw9_image():
    """First page of forms/dev/fw9.pdf with its widgets stripped, rendered at the library's 144 dpi."""
    doc = open_pdf(FORMS / "dev" / "fw9.pdf")
    try:
        page = doc[0]
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
        yield render(page, 144)
    finally:
        doc.close()


def greedy_pairs(a, b, iou_min):
    """Number of boxes in ``a`` paired one-to-one with a box in ``b`` at IoU >= iou_min (best first)."""
    pairs = sorted(
        ((iou(x, y), i, j) for i, x in enumerate(a) for j, y in enumerate(b)), reverse=True
    )
    used_a, used_b, matched = set(), set(), 0
    for value, i, j in pairs:
        if value < iou_min:
            break
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            matched += 1
    return matched


def _row(cx, cy, w, h, scores):
    return [cx, cy, w, h, *scores]


def _raw(rows):
    """Model output layout: [1, 4 + classes, anchors], boxes as centre x/y, w/h in input pixels."""
    return np.array(rows, dtype=np.float32).T[None]


# A square image letterboxed to the full 1216 input: no padding, scale 1.
_FULL = _Letterbox(scale=1.0, left=0, top=0, width=1216, height=1216)


def test_decode_keeps_the_more_confident_of_two_overlapping_boxes():
    raw = _raw(
        [
            _row(300, 300, 100, 40, [0.9, 0.0, 0.0]),
            _row(304, 300, 100, 40, [0.6, 0.0, 0.0]),
        ]
    )
    out = _decode(raw, 0.3, 0.1, _FULL)
    assert len(out) == 1
    kind, box, conf = out[0]
    assert (kind, round(conf, 2)) == ("text", 0.9)
    assert box == pytest.approx((250 / 1216, 280 / 1216, 350 / 1216, 320 / 1216))


def test_decode_nms_is_per_class_and_conf_threshold_drops_weak_boxes():
    raw = _raw(
        [
            _row(300, 300, 100, 40, [0.9, 0.0, 0.0]),
            _row(300, 300, 100, 40, [0.0, 0.5, 0.0]),  # same place, other class: kept
            _row(800, 800, 60, 60, [0.29, 0.1, 0.0]),  # best score under 0.3: dropped
        ]
    )
    out = _decode(raw, 0.3, 0.1, _FULL)
    assert sorted(k for k, _, _ in out) == ["choice", "text"]


def test_decode_maps_class_ids_to_kinds():
    raw = _raw([_row(100, 100, 40, 40, [0.0, 0.0, 0.8])])
    assert [k for k, _, _ in _decode(raw, 0.3, 0.1, _FULL)] == ["signature"]


def test_decode_undoes_the_letterbox_offset_and_scale():
    # A 600 x 400 image scaled by 1.5 to 900 x 600 and centred in the 1216 square (left 158, top 308).
    lb = _Letterbox(scale=1.5, left=158, top=308, width=600, height=400)
    # Box covering image pixels x 150..300, y 100..200 -> input pixels 158+225..158+450, 308+150..308+300.
    raw = _raw([_row((383 + 608) / 2, (458 + 608) / 2, 225, 150, [0.8, 0.0, 0.0])])
    ((_, box, _),) = _decode(raw, 0.3, 0.1, lb)
    assert box == pytest.approx((150 / 600, 100 / 400, 300 / 600, 200 / 400))


def test_decode_clamps_boxes_that_reach_into_the_padding():
    lb = _Letterbox(scale=1.0, left=100, top=0, width=1000, height=1216)
    raw = _raw([_row(105, 600, 40, 40, [0.8, 0.0, 0.0])])  # x spans 85..125 in input pixels
    ((_, box, _),) = _decode(raw, 0.3, 0.1, lb)
    assert box[0] == 0.0 and 0 < box[2] < 1


def test_decode_empty():
    assert _decode(_raw([_row(100, 100, 40, 40, [0.1, 0.1, 0.1])]), 0.3, 0.1, _FULL) == []


def test_onnx_constants():
    assert ONNX_IMAGE_SIZE == 1216
    assert ONNX_URLS == {
        "FFDNet-S": "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx",
        "FFDNet-L": "https://huggingface.co/jbarrow/FFDNet-L-cpu/resolve/main/FFDNet-L.onnx",
    }


@pytest.mark.slow
def test_onnx_s_finds_fw9_blanks(fw9_image):
    det = FFDNetOnnxDetector("FFDNet-S")
    boxes = det.detect(fw9_image)
    assert det.name == "FFDNet-S-onnx"
    assert 15 <= len(boxes) <= 80
    assert {k for k, _, _ in boxes} <= {"text", "choice", "signature"}
    assert all(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 for _, (x0, y0, x1, y1), _ in boxes)
    assert all(c >= 0.3 for _, _, c in boxes)


@pytest.mark.slow
def test_onnx_l_agrees_with_pt_on_most_boxes(fw9_image):
    # single-pass ONNX L vs the library's augmented .pt L on the same page: same blanks, mostly
    onnx = FFDNetOnnxDetector("FFDNet-L").detect(fw9_image)
    pt = FFDNetDetector("FFDNet-L").detect(fw9_image)
    matched = greedy_pairs([b for _, b, _ in onnx], [b for _, b, _ in pt], iou_min=0.5)
    assert matched >= 0.8 * len(pt)
