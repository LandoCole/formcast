import io
import math

import pymupdf
import pytest
from PIL import Image

from formcast.geometry import iou, points_to_norm
from formcast.pagemap import ocr
from formcast.pagemap.detect import (
    DetectPolicy,
    FFDNetDetector,
    detect_page,
    merge_detections,
)
from formcast.pagemap.model import Candidate


class FakeDetector:
    """Returns fixed normalized boxes and records the image it was given."""

    def __init__(self, name, boxes):
        self.name = name
        self.boxes = boxes
        self.images: list[Image.Image] = []

    def detect(self, image):
        self.images.append(image)
        return list(self.boxes)


def _close(a, b, tol) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _cand(rect, conf, detector="a", kind="text", id=""):
    return Candidate(id=id, kind=kind, rect=rect, conf=conf, detector=detector)


def _policy(**kw):
    return DetectPolicy(digital=("fake",), scanned=("fake",), **kw)


def test_full_page_box_maps_to_crop_box(cropped_form_pdf):
    det = FakeDetector("fake", [("text", (0.0, 0.0, 1.0, 1.0), 0.9)])
    with pymupdf.open(cropped_form_pdf) as doc:
        page = doc[0]
        out = detect_page(page, "digital", {"fake": det}, _policy())
        crop = (0.0, 0.0, page.cropbox.width, page.cropbox.height)
    assert len(out) == 1
    assert _close(out[0].rect, crop, 0.5)
    assert (out[0].kind, out[0].conf, out[0].detector, out[0].id) == ("text", 0.9, "fake", "c0_0")


def test_detector_sees_the_rendered_page_at_policy_dpi(flat_form_pdf):
    det = FakeDetector("fake", [])
    with pymupdf.open(flat_form_pdf) as doc:
        detect_page(doc[0], "digital", {"fake": det}, _policy(dpi=72))
    assert det.images[0].size == (612, 792)
    assert det.images[0].mode == "RGB"


def test_rotated_page_box_maps_back_to_unrotated_points(rotated_form_pdf):
    line = (120.0, 90.0, 300.0, 102.0)  # around the "Name:" rule, unrotated points
    with pymupdf.open(rotated_form_pdf) as doc:
        page = doc[0]
        shown = pymupdf.Rect(line) * page.rotation_matrix  # where it appears in the pixmap
        norm = points_to_norm(
            (shown.x0, shown.y0, shown.x1, shown.y1), (0.0, 0.0, page.rect.width, page.rect.height)
        )
        det = FakeDetector("fake", [("text", norm, 0.8)])
        out = detect_page(page, "digital", {"fake": det}, _policy())
    assert len(out) == 1
    assert _close(out[0].rect, line, 2.0)


def test_merge_keeps_the_higher_confidence_of_an_overlapping_pair():
    a = _cand((100, 100, 200, 120), 0.6, "a", "text")
    b = _cand((102, 100, 202, 120), 0.9, "b", "choice")  # IoU ~0.96, different class
    assert iou(a.rect, b.rect) > 0.8
    far = _cand((300, 300, 400, 320), 0.5, "a")
    out = merge_detections([[a, far], [b]])
    assert len(out) == 2
    kept = next(c for c in out if c.rect[0] < 200)
    assert (kept.conf, kept.detector, kept.kind) == (0.9, "b", "choice")


def test_merge_keeps_boxes_below_the_threshold():
    a = _cand((0, 0, 100, 10), 0.6)
    b = _cand((60, 0, 160, 10), 0.7)  # IoU = 40/160 = 0.25
    assert len(merge_detections([[a], [b]])) == 2
    assert len(merge_detections([[a], [b]], iou_threshold=0.2)) == 1


def test_ids_follow_reading_order(flat_form_pdf):
    boxes = [
        ("text", (0.50, 0.50, 0.70, 0.52), 0.9),  # lower row
        ("text", (0.50, 0.100, 0.70, 0.12), 0.9),  # top row, right
        ("choice", (0.10, 0.104, 0.12, 0.12), 0.9),  # top row (y within 0.01), left
        ("signature", (0.10, 0.80, 0.40, 0.83), 0.9),
    ]
    with pymupdf.open(flat_form_pdf) as doc:
        out = detect_page(doc[0], "digital", {"fake": FakeDetector("fake", boxes)}, _policy())
    assert [c.id for c in out] == ["c0_0", "c0_1", "c0_2", "c0_3"]
    assert [c.kind for c in out] == ["choice", "text", "text", "signature"]


def test_policy_picks_detectors_by_page_kind(flat_form_pdf):
    dig = FakeDetector("dig", [("text", (0.1, 0.1, 0.2, 0.2), 0.9)])
    scn = FakeDetector("scn", [("text", (0.5, 0.5, 0.6, 0.6), 0.9)])
    policy = DetectPolicy(digital=("dig",), scanned=("scn",), deskew_scanned=False)
    with pymupdf.open(flat_form_pdf) as doc:
        d = detect_page(doc[0], "digital", {"dig": dig, "scn": scn}, policy)
        s = detect_page(doc[0], "scanned", {"dig": dig, "scn": scn}, policy)
    assert [c.detector for c in d] == ["dig"]
    assert [c.detector for c in s] == ["scn"]


def test_scanned_page_is_detected_on_the_deskewed_image_and_mapped_back(flat_form_pdf, tmp_path):
    # Build a scan of the form rotated 1.5 degrees, then ask for a box on the line.
    with pymupdf.open(flat_form_pdf) as doc:
        pix = doc[0].get_pixmap(dpi=144)
    clean = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    skewed = clean.rotate(1.5, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    buf = io.BytesIO()
    skewed.save(buf, "PNG")
    scan_pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), stream=buf.getvalue())
    doc.save(scan_pdf)
    doc.close()

    # Where the long rule (120..300, y=100) sits in the skewed image, in points.
    w, h = skewed.size
    corners = []
    for px, py in ((120, 100), (300, 100)):
        x, y = px * 144 / 72, py * 144 / 72
        # rotate() turns content counter-clockwise by 1.5 degrees about the center
        t = math.radians(1.5)
        dx, dy = x - w / 2, y - h / 2
        corners.append((w / 2 + dx * math.cos(t) + dy * math.sin(t), h / 2 - dx * math.sin(t) + dy * math.cos(t)))
    expected = (
        min(c[0] for c in corners) / 2,
        min(c[1] for c in corners) / 2,
        max(c[0] for c in corners) / 2,
        max(c[1] for c in corners) / 2,
    )

    det = FakeDetector("fake", [("text", (120 / 612, 98 / 792, 300 / 612, 102 / 792), 0.9)])
    with pymupdf.open(scan_pdf) as doc:
        out = detect_page(doc[0], "scanned", {"fake": det}, _policy(dpi=144))
    seen = det.images[0]
    assert seen.size == skewed.size
    # The detector saw the straightened page, not the raw scan...
    assert ocr.estimate_skew(seen) == pytest.approx(0.0, abs=0.3)
    # ...and the box on the straight line lands on the line as it sits in the scan.
    assert len(out) == 1
    assert abs(out[0].rect[0] - expected[0]) <= 3 and abs(out[0].rect[2] - expected[2]) <= 3
    assert abs((out[0].rect[1] + out[0].rect[3]) / 2 - (expected[1] + expected[3]) / 2) <= 3


def test_skew_is_left_alone_when_deskew_is_off(flat_form_pdf):
    det = FakeDetector("fake", [])
    with pymupdf.open(flat_form_pdf) as doc:
        detect_page(doc[0], "scanned", {"fake": det}, _policy(deskew_scanned=False))
    with pymupdf.open(flat_form_pdf) as doc:
        pix = doc[0].get_pixmap(dpi=144, alpha=False)
    assert det.images[0].tobytes() == pix.samples


def test_unknown_detector_name_is_a_clear_error(flat_form_pdf):
    from formcast.errors import FormcastError

    with pymupdf.open(flat_form_pdf) as doc, pytest.raises(FormcastError, match="FFDNet-L"):
        detect_page(doc[0], "digital", {}, DetectPolicy())


@pytest.mark.slow
def test_real_ffdnet_finds_a_text_field_on_the_flat_form(flat_form_pdf):
    from formcast.pagemap.detect import load_default_detectors

    detectors = load_default_detectors()
    with pymupdf.open(flat_form_pdf) as doc:
        out = detect_page(doc[0], "digital", detectors, DetectPolicy())
    line = (120.0, 94.0, 300.0, 102.0)
    assert any(c.kind == "text" and iou(c.rect, line) > 0 for c in out), out


def test_constructing_a_detector_does_not_load_weights():
    detector = FFDNetDetector()
    assert detector.name == "FFDNet-L"
    assert detector._detector is None
