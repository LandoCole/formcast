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
from typing import Literal, NamedTuple, Protocol

import numpy as np
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

# The browser-sized ONNX exports of FFDNet take a fixed 1216 x 1216 input.
ONNX_IMAGE_SIZE = 1216
# Where the exports live (the demo loads the same files). Never re-hosted or re-exported.
ONNX_URLS = {
    "FFDNet-S": "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx",
    "FFDNet-L": "https://huggingface.co/jbarrow/FFDNet-L-cpu/resolve/main/FFDNet-L.onnx",
}
# Colour ultralytics pads a letterboxed image with.
_ONNX_PAD_VALUE = 114
# Ultralytics' default cap on boxes kept per image.
_ONNX_MAX_DET = 300

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


class _Letterbox(NamedTuple):
    """How an image of ``width`` x ``height`` sits in the square model input: scaled, then offset."""

    scale: float
    left: int
    top: int
    width: int
    height: int


def _letterbox(image: Image.Image, size: int = ONNX_IMAGE_SIZE) -> tuple[np.ndarray, _Letterbox]:
    """Scale ``image`` to fit a ``size`` square, centred on grey; the NCHW float tensor and the placement.

    Mirrors ultralytics' ``LetterBox`` for a non-``.pt`` model (``auto=False``, centred, pad 114).
    """
    width, height = image.size
    scale = min(size / width, size / height)
    new_w, new_h = max(round(width * scale), 1), max(round(height * scale), 1)
    pad_w, pad_h = (size - new_w) / 2, (size - new_h) / 2
    left, top = round(pad_w - 0.1), round(pad_h - 0.1)
    canvas = Image.new("RGB", (size, size), (_ONNX_PAD_VALUE,) * 3)
    canvas.paste(image.convert("RGB").resize((new_w, new_h), Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), _Letterbox(scale, left, top, width, height)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Indices kept by greedy non-maximum suppression over xyxy ``boxes``, best score first."""
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size:
        best = int(order[0])
        keep.append(best)
        rest = order[1:]
        if not rest.size:
            break
        x0 = np.maximum(boxes[best, 0], boxes[rest, 0])
        y0 = np.maximum(boxes[best, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[best, 2], boxes[rest, 2])
        y1 = np.minimum(boxes[best, 3], boxes[rest, 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        overlap = inter / (area[best] + area[rest] - inter)
        order = rest[overlap <= iou_threshold]
    return keep


def _decode(raw: np.ndarray, confidence: float, iou_threshold: float, letterbox: _Letterbox) -> list[Detection]:
    """Detections from the raw ONNX output ``[1, 4 + classes, anchors]``.

    Rows 0-3 are centre x, centre y, width, height in input pixels; the rest are per-class
    scores (already sigmoided). Each anchor takes its best class, anchors under ``confidence``
    go, greedy NMS runs per class at ``iou_threshold`` (ultralytics' default class-aware NMS),
    at most 300 boxes survive, and boxes are mapped back through ``letterbox`` to the
    original image, normalized to it.
    """
    anchors = raw[0].T
    scores = anchors[:, 4:]
    classes = scores.argmax(axis=1)
    best = scores[np.arange(len(anchors)), classes]
    keep = best >= confidence
    anchors, classes, best = anchors[keep], classes[keep], best[keep]
    if not len(anchors):
        return []
    cx, cy, w, h = anchors[:, 0], anchors[:, 1], anchors[:, 2], anchors[:, 3]
    xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    # Offsetting each class far apart makes one NMS pass per-class (ultralytics' trick).
    kept = _nms(xyxy + classes[:, None] * 2 * ONNX_IMAGE_SIZE, best, iou_threshold)[:_ONNX_MAX_DET]
    lb = letterbox
    out: list[Detection] = []
    for i in kept:
        x0 = (xyxy[i, 0] - lb.left) / lb.scale / lb.width
        y0 = (xyxy[i, 1] - lb.top) / lb.scale / lb.height
        x1 = (xyxy[i, 2] - lb.left) / lb.scale / lb.width
        y1 = (xyxy[i, 3] - lb.top) / lb.scale / lb.height
        box = tuple(float(min(1.0, max(0.0, v))) for v in (x0, y0, x1, y1))
        out.append((_CLASS_KINDS[int(classes[i])], box, float(best[i])))
    return out


class FFDNetOnnxDetector:
    """CommonForms FFDNet as the browser demo runs it: the ONNX export, one pass, no augmentation.

    The session loads on the first ``detect`` call (weights download from Hugging Face on first
    use). Not part of the library's default policy; ``name`` is ``"<model>-onnx"``.
    """

    def __init__(
        self,
        model: Literal["FFDNet-S", "FFDNet-L"] = "FFDNet-S",
        confidence: float = 0.3,
    ):
        if model not in ONNX_URLS:
            raise FormcastError(f"no ONNX export of {model!r}; have {sorted(ONNX_URLS)}")
        self.model = model
        self.name = f"{model}-onnx"
        self.confidence = confidence
        self._session = None

    def _load(self):
        import onnxruntime
        from huggingface_hub import hf_hub_download

        # https://huggingface.co/<owner>/<repo>/resolve/main/<file>
        repo, _, filename = ONNX_URLS[self.model].removeprefix("https://huggingface.co/").partition("/resolve/main/")
        path = hf_hub_download(repo_id=repo, filename=filename)
        return onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])

    def detect(self, image: Image.Image) -> list[Detection]:
        if self._session is None:
            self._session = self._load()
        tensor, letterbox = _letterbox(image)
        (raw,) = self._session.run(None, {self._session.get_inputs()[0].name: tensor})
        return _decode(raw, self.confidence, DETECTOR_NMS_IOU, letterbox)


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
