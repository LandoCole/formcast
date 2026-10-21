"""CommonForms field detectors, run per page and returned in PDF point space.

A page is rendered with ``get_pixmap`` (cropped and rotated as a viewer shows
it), optionally straightened if it is a scan, and handed to each detector the
policy names for that kind of page. Detectors speak in boxes normalized to the
image they were given; ``detect_page`` rotates those boxes back through the
deskew and maps them into unrotated crop-box points, then merges the detectors'
output and numbers the candidates in reading order.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Protocol

import pymupdf
from PIL import Image

from formcast.errors import FormcastError
from formcast.geometry import Rect, iou
from formcast.pagemap.extract import image_norm_to_points
from formcast.pagemap.model import Candidate
from formcast.pagemap.ocr import render, straighten, unrotate_box

# Rows of candidates whose top edges differ by less than this fraction of the
# page height count as one row (the same bucketing as commonforms' sort_widgets).
ROW_BUCKET = 0.01
# NMS threshold the detectors apply to their own raw output.
DETECTOR_NMS_IOU = 0.1

# Both models use the CommonForms class ids.
_CLASS_KINDS = {0: "text", 1: "choice", 2: "signature"}

# (kind, box normalized to the image given to the detector, confidence)
Detection = tuple[str, Rect, float]


class Detector(Protocol):
    name: str

    def detect(self, image: Image.Image) -> list[Detection]:
        """Field boxes found on ``image``, normalized to its width and height."""
        ...


class FFDNetDetector:
    """CommonForms FFDNet (YOLO); the model loads on the first ``detect`` call."""

    def __init__(self, model: str = "FFDNet-L", confidence: float = 0.3, image_size: int = 1216):
        self.name = model
        self.confidence = confidence
        self.image_size = image_size
        self._detector = None

    def detect(self, image: Image.Image) -> list[Detection]:
        from commonforms.inference import FFDNetDetector as _FFDNet

        if self._detector is None:
            self._detector = _FFDNet(self.name)
        # commonforms' extract_widgets drops the confidences, which the merge
        # needs, so call the YOLO model with the arguments it would use.
        results = self._detector.model.predict(
            image,
            iou=DETECTOR_NMS_IOU,
            conf=self.confidence,
            augment=True,
            imgsz=self.image_size,
            device=self._detector.device,
            verbose=False,
        )
        result = results[0] if isinstance(results, list) else results
        if result is None or result.boxes is None:
            return []
        boxes = result.boxes.cpu().numpy()
        return [
            (
                _CLASS_KINDS[int(cls)],
                (float(xyxyn[0]), float(xyxyn[1]), float(xyxyn[2]), float(xyxyn[3])),
                float(conf),
            )
            for cls, conf, xyxyn in zip(boxes.cls, boxes.conf, boxes.xyxyn)
        ]


class FFDetrDetector:
    """CommonForms FFDetr (RF-DETR); the model loads on the first ``detect`` call.

    commonforms' ``extract_widgets`` crashes on a single page, so the model's
    ``predict`` is called directly.
    """

    name = "FFDetr"

    def __init__(self, confidence: float = 0.3):
        self.confidence = confidence
        self._detector = None

    def detect(self, image: Image.Image) -> list[Detection]:
        from commonforms.inference import FFDetrDetector as _FFDetr

        if self._detector is None:
            self._detector = _FFDetr("FFDetr")
        result = self._detector.model.predict(image, threshold=self.confidence)
        detections = result[0] if isinstance(result, list) else result
        detections = detections.with_nms(threshold=DETECTOR_NMS_IOU, class_agnostic=True)
        width, height = image.size
        confidences = detections.confidence
        return [
            (
                _CLASS_KINDS[int(class_id)],
                (box[0] / width, box[1] / height, box[2] / width, box[3] / height),
                float(confidences[i]) if confidences is not None else 1.0,
            )
            for i, (class_id, box) in enumerate(zip(detections.class_id, detections.xyxy))
        ]


@dataclass(frozen=True)
class DetectPolicy:
    """Which detectors run on which kind of page, and how the page is rendered."""

    digital: tuple[str, ...] = ("FFDNet-L",)
    scanned: tuple[str, ...] = ("FFDetr",)
    merge_iou: float = 0.5
    dpi: int = 144
    deskew_scanned: bool = True


@functools.cache
def load_default_detectors() -> dict[str, Detector]:
    """The stock detectors by name. Weights load on first use of each, then stay cached."""
    detectors = (FFDNetDetector(), FFDetrDetector())
    return {d.name: d for d in detectors}


def merge_detections(lists: list[list[Candidate]], iou_threshold: float = 0.5) -> list[Candidate]:
    """Union of the lists; of each overlapping pair (IoU >= threshold) only the more confident survives.

    The class of the boxes is ignored, so a text box and a choice box on the
    same spot collapse to whichever detector was surer. Output is by descending
    confidence.
    """
    kept: list[Candidate] = []
    flat = sorted((c for lst in lists for c in lst), key=lambda c: -c.conf)
    for cand in flat:
        if all(iou(cand.rect, k.rect) < iou_threshold for k in kept):
            kept.append(cand)
    return kept


def _to_page_points(
    page: pymupdf.Page, box: Rect, angle: float, size: tuple[int, int]
) -> Rect:
    """Box normalized to the (possibly deskewed) detector image -> unrotated crop-box points."""
    if angle:
        width, height = size
        box = unrotate_box(
            (box[0] * width, box[1] * height, box[2] * width, box[3] * height), angle, size
        )
    box = tuple(min(1.0, max(0.0, v)) for v in box)
    return image_norm_to_points(page, box)


def _reading_order(cands: list[Candidate], page_height: float) -> list[Candidate]:
    ordered = sorted(cands, key=lambda c: (round(c.rect[1] / page_height, 3), c.rect[0]))
    rows: list[list[Candidate]] = []
    for cand in ordered:
        if rows and abs(cand.rect[1] - rows[-1][0].rect[1]) / page_height < ROW_BUCKET:
            rows[-1].append(cand)
        else:
            rows.append([cand])
    return [c for row in rows for c in sorted(row, key=lambda c: c.rect[0])]


def detect_page(
    page: pymupdf.Page,
    page_kind: str,
    detectors: dict[str, Detector],
    policy: DetectPolicy,
) -> list[Candidate]:
    """Field candidates on ``page`` in unrotated crop-box points, ids ``c{page}_{n}`` in reading order.

    ``page_kind`` "scanned" runs ``policy.scanned`` (on the deskewed render when
    ``policy.deskew_scanned``); any other kind runs ``policy.digital``.

    Raises:
        FormcastError: when the policy names a detector that is not in ``detectors``.
    """
    scanned = page_kind == "scanned"
    names = policy.scanned if scanned else policy.digital
    missing = [n for n in names if n not in detectors]
    if missing:
        raise FormcastError(f"detector {missing[0]!r} is not available; have {sorted(detectors)}")

    image = render(page, policy.dpi)
    angle = 0.0
    if scanned and policy.deskew_scanned:
        image, angle = straighten(image)

    per_detector: list[list[Candidate]] = []
    for name in names:
        detector = detectors[name]
        per_detector.append(
            [
                Candidate(
                    id="",
                    kind=kind,
                    rect=_to_page_points(page, box, angle, image.size),
                    conf=conf,
                    detector=detector.name,
                )
                for kind, box, conf in detector.detect(image)
            ]
        )

    merged = merge_detections(per_detector, policy.merge_iou)
    ordered = _reading_order(merged, page.cropbox.height)
    return [c.model_copy(update={"id": f"c{page.number}_{n}"}) for n, c in enumerate(ordered)]
