"""CommonForms FFDNet-S, run as its official ONNX export on onnxruntime.

The model takes a 1216 x 1216 letterboxed RGB image and returns ``[1, 4 + 3, anchors]``: centre x, centre y,
width, height in input pixels, then a score per class (text, choice, signature). This is the same decode
FormCast's ``FFDNetOnnxDetector`` and commonforms' ``fast=True`` path use, without torch or ultralytics.

Where the weights come from, first match wins:
1. ``FORMCAST_LITE_MODEL`` (a path to ``FFDNet-S.onnx``)
2. ``models/FFDNet-S.onnx`` next to the app (inside the exe, PyInstaller unpacks it there)
3. the Hugging Face cache, if commonforms or FormCast downloaded it before
4. a one-time download from Hugging Face into ``models/``
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

MODEL_NAME = "FFDNet-S.onnx"
MODEL_URL = "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx"
IMAGE_SIZE = 1216
CONFIDENCE = 0.3
NMS_IOU = 0.1
MAX_DET = 300
PAD_VALUE = 114
KINDS = {0: "text", 1: "choice", 2: "signature"}

# (kind, (x0, y0, x1, y1) normalized to the image, confidence)
Detection = tuple[str, tuple[float, float, float, float], float]


def app_dir() -> Path:
    """The folder holding ``models/``: PyInstaller's unpack dir in the exe, else the ``lite`` folder."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def model_path(download: bool = True) -> Path:
    env = os.environ.get("FORMCAST_LITE_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    local = app_dir() / "models" / MODEL_NAME
    if local.is_file():
        return local
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    for cached in (hub / "models--jbarrow--FFDNet-S-cpu" / "snapshots").glob(f"*/{MODEL_NAME}"):
        return cached
    if not download:
        raise FileNotFoundError(f"{MODEL_NAME} not found")
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(local)
    return local


def letterbox(image: Image.Image) -> tuple[np.ndarray, tuple[float, int, int, int, int]]:
    """``image`` scaled into the square input, centred on grey (ultralytics' LetterBox, auto=False)."""
    width, height = image.size
    scale = min(IMAGE_SIZE / width, IMAGE_SIZE / height)
    new_w, new_h = max(round(width * scale), 1), max(round(height * scale), 1)
    left, top = round((IMAGE_SIZE - new_w) / 2 - 0.1), round((IMAGE_SIZE - new_h) / 2 - 0.1)
    canvas = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (PAD_VALUE,) * 3)
    canvas.paste(image.convert("RGB").resize((new_w, new_h), Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), (scale, left, top, width, height)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    order = np.argsort(-scores, kind="stable")
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
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
        overlap = inter / (area[best] + area[rest] - inter)
        order = rest[overlap <= iou_threshold]
    return keep


def decode(raw: np.ndarray, placement: tuple[float, int, int, int, int], confidence: float = CONFIDENCE) -> list[Detection]:
    scale, left, top, width, height = placement
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
    # Offsetting each class far apart makes one NMS pass class-aware.
    kept = nms(xyxy + classes[:, None] * 2 * IMAGE_SIZE, best, NMS_IOU)[:MAX_DET]
    out: list[Detection] = []
    for i in kept:
        x0 = (xyxy[i, 0] - left) / scale / width
        y0 = (xyxy[i, 1] - top) / scale / height
        x1 = (xyxy[i, 2] - left) / scale / width
        y1 = (xyxy[i, 3] - top) / scale / height
        box = tuple(float(min(1.0, max(0.0, v))) for v in (x0, y0, x1, y1))
        out.append((KINDS.get(int(classes[i]), "text"), box, float(best[i])))
    return out


class Detector:
    """FFDNet-S on CPU; the session loads on the first ``detect``."""

    def __init__(self, confidence: float = CONFIDENCE):
        self.confidence = confidence
        self._session = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self):
        with self._lock:
            if self._session is None:
                import onnxruntime

                options = onnxruntime.SessionOptions()
                options.log_severity_level = 3
                self._session = onnxruntime.InferenceSession(
                    str(model_path()), sess_options=options, providers=["CPUExecutionProvider"]
                )
            return self._session

    def detect(self, image: Image.Image) -> list[Detection]:
        session = self.load()
        tensor, placement = letterbox(image)
        (raw,) = session.run(None, {session.get_inputs()[0].name: tensor})
        return decode(raw, placement, self.confidence)
