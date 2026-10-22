"""The browser engine in Chromium: opening PDFs with pdf.js, seeding, edit ops against the open
PDF's widgets, the detector's time limit, and (slow) FFDNet-S parity with ``FFDNetOnnxDetector``.

The harness page ``tests/web/harness/engine.html`` exposes ``window.runEngine(cmd)``. PDFs are
handed over by URL (served from the repository root) or as base64 bytes made here with PyMuPDF.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from formcast.geometry import iou
from formcast.pagemap.detect import FFDNetOnnxDetector, _letterbox
from formcast.pagemap.extract import WIDGET_KINDS, image_norm_to_points
from formcast.pagemap.ocr import render
from formcast.template import ops
from formcast.template.model import Template

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "forms" / "dev"
SEED = json.loads((ROOT / "tests" / "web" / "fixtures" / "fw9_seed.json").read_text(encoding="utf-8"))
SCHEMA = SEED["schema"]
RULINGS = "docs/superpowers/plans/2026-10-21-formcast-plan3-rulings.md"


@pytest.fixture(scope="module")
def harness(server, browser):
    """``run(cmd)`` evaluates ``window.runEngine(cmd)`` in one harness page; page errors fail the test."""
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.url))
    page.goto(f"{server}/tests/web/harness/engine.html")
    page.wait_for_function("window.engineReady === true")

    def run(cmd: dict):
        out = page.evaluate("cmd => window.runEngine(cmd)", cmd)
        assert not errors, errors
        return out

    run.requests = requests  # every URL the page and its workers requested
    try:
        yield run
    finally:
        context.close()


def b64(data: bytes) -> dict:
    return {"base64": base64.b64encode(data).decode("ascii")}


def stripped(path: Path) -> bytes:
    """``path`` with every widget deleted, as the showcase's sample PDFs are."""
    doc = pymupdf.open(path)
    for page in doc:
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
    data = doc.tobytes()
    doc.close()
    return data


def encrypted(path: Path, user_pw: str) -> bytes:
    doc = pymupdf.open(path)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw=user_pw)
    doc.close()
    return data


@pytest.fixture(scope="module")
def fw9_stripped() -> bytes:
    return stripped(DEV / "fw9.pdf")


# ------------------------------------------------------------------ open


def test_open_fw9_reads_pages_widgets_and_kinds(harness):
    out = harness({"op": "open", "pdf": {"url": "forms/dev/fw9.pdf"}})
    assert out["pageCount"] == 6
    assert out["encrypted"] is False
    assert [p["kind"] for p in out["pages"]] == ["fillable"] + ["digital"] * 5
    page0 = out["pages"][0]
    assert len(page0["widgets"]) >= 20
    assert page0["rotation"] == 0
    # sizes, names, kinds and rects agree with PyMuPDF (the library's reader)
    doc = pymupdf.open(DEV / "fw9.pdf")
    assert page0["width"] == pytest.approx(doc[0].rect.width, abs=1e-3)
    assert page0["height"] == pytest.approx(doc[0].rect.height, abs=1e-3)
    expected = [(w.field_name, WIDGET_KINDS[w.field_type], tuple(w.rect)) for w in doc[0].widgets()]
    got = [(w["name"], w["kind"], tuple(w["rect"])) for w in page0["widgets"]]
    assert [(n, k) for n, k, _ in got] == [(n, k) for n, k, _ in expected]
    for (_, _, a), (_, _, b) in zip(got, expected):
        assert a == pytest.approx(b, abs=0.01)
    assert out["widgetKinds"]["topmostSubform[0].Page1[0].f1_01[0]"] == ["text"]


def test_open_stripped_page_is_digital_with_text_lines(harness, fw9_stripped):
    out = harness({"op": "open", "pdf": b64(fw9_stripped)})
    page0 = out["pages"][0]
    assert page0["kind"] == "digital"
    assert page0["widgets"] == []
    assert len(out["lines0"]) > 20
    text = " ".join(line["text"] for line in out["lines0"])
    assert "Request for Taxpayer" in text
    for line in out["lines0"]:
        x0, y0, x1, y1 = line["rect"]
        assert 0 <= x0 < x1 <= page0["width"] + 1 and 0 <= y0 < y1 <= page0["height"] + 1


def test_open_rejects_a_file_that_is_not_a_pdf(harness):
    out = harness({"op": "open", "pdf": b64((ROOT / "README.md").read_bytes())})
    assert out["error"] == {"name": "DocError", "code": "not_pdf", "message": "This file isn't a PDF."}
    assert harness({"op": "open", "pdf": b64(b"")})["error"]["code"] == "not_pdf"


def test_open_rejects_a_damaged_pdf(harness):
    out = harness({"op": "open", "pdf": b64(b"%PDF-1.7\n" + bytes(range(256)) * 8)})
    assert out["error"]["code"] == "corrupt"
    assert out["error"]["message"] == "This PDF is damaged and can't be read."


def test_open_rejects_a_pdf_that_needs_a_password(harness):
    out = harness({"op": "open", "pdf": b64(encrypted(DEV / "fw9.pdf", user_pw="user-secret"))})
    assert out["error"]["code"] == "encrypted"
    assert "password" in out["error"]["message"]


def test_open_takes_owner_password_only_pdfs_and_flags_them(harness):
    out = harness({"op": "open", "pdf": b64(encrypted(DEV / "fw9.pdf", user_pw=""))})
    assert "error" not in out
    assert out["encrypted"] is True
    assert out["pageCount"] == 6
    wadoc = harness({"op": "open", "pdf": {"url": "forms/dev/wadoc_auth.pdf"}})  # RC4, owner password only
    assert wadoc["encrypted"] is True
    assert wadoc["pageCount"] == pymupdf.open(DEV / "wadoc_auth.pdf").page_count


def test_fill_refuses_an_owner_password_only_pdf(harness):
    pdf = {"url": "forms/dev/wadoc_auth.pdf"}
    template = harness({"op": "seed", "pdf": pdf, "schema": {"type": "object", "properties": {}}})
    assert harness({"op": "fill", "pdf": pdf, "template": template, "record": {}}) == {"error": "encrypted PDF"}


def test_open_refuses_more_than_50_pages(harness):
    doc = pymupdf.open()
    for _ in range(51):
        doc.new_page()
    out = harness({"op": "open", "pdf": b64(doc.tobytes())})
    assert out["error"]["code"] == "too_many_pages"
    assert "51 pages" in out["error"]["message"]


# ------------------------------------------------------------------ seed and apply


def test_seed_of_fw9_matches_the_library_seed(harness):
    t = harness({"op": "seed", "pdf": {"url": "forms/dev/fw9.pdf"}, "schema": SCHEMA})
    Template.model_validate(t)
    assert t["form"]["fingerprint"] is None
    assert t["form"]["kinds"] == SEED["form"]["kinds"]
    for got, want in zip(t["form"]["page_sizes"], SEED["form"]["page_sizes"], strict=True):
        assert got == pytest.approx(want, abs=1e-3)
    assert t["history"] == []
    assert [f["id"] for f in t["fields"]] == [f["id"] for f in SEED["fields"]]
    for got, want in zip(t["fields"], SEED["fields"]):
        assert got["rect"] == pytest.approx(want["rect"], abs=0.01)
        assert {**got, "rect": None} == {**want, "rect": None}


def test_seed_with_detections_is_a_valid_template_with_labels(harness, fw9_stripped):
    # fw9's name box (a line above it), its "C corporation" checkbox (its option text on the left),
    # and a box in the empty top margin
    detections = {"0": [{"kind": "choice", "rect": [180, 180.2, 188, 188.2], "conf": 0.9},
                        {"kind": "text", "rect": [58.6, 118, 576, 132], "conf": 0.8},
                        {"kind": "signature", "rect": [300, 4, 310, 14], "conf": 0.4}]}  # fmt: skip
    t = harness({"op": "seed", "pdf": b64(fw9_stripped), "schema": SCHEMA, "detections": detections})
    template = Template.model_validate(t)
    assert [(f.id, f.type, f.origin, f.confidence) for f in template.fields] == [
        ("c0_0", "signature", "detector", 0.4),
        ("c0_1", "text", "detector", 0.8),
        ("c0_2", "checkbox", "detector", 0.9),
    ]
    assert template.fields[0].label == ""
    assert "name" in template.fields[1].label
    assert template.fields[2].label == "Individual/sole proprietor"


def test_apply_checks_set_type_against_the_open_pdfs_widget_kinds(harness):
    """The browser engine refuses a type the widget can't hold, with Python's message."""
    set_check = {"op": "set_type", "id": "w0_0", "type": "checkbox"}
    set_sig = {"op": "set_type", "id": "w0_0", "type": "signature"}
    out = harness({"op": "apply", "pdf": {"url": "forms/dev/fw9.pdf"}, "schema": SCHEMA, "ops": [set_check, set_sig]})
    doc = pymupdf.open(DEV / "fw9.pdf")
    kinds: dict[str, list[str]] = {}
    for page in doc:
        for w in page.widgets():
            kinds.setdefault(w.field_name, []).append(WIDGET_KINDS[w.field_type])
    seed = Template.model_validate(SEED)
    context = ops.OpContext.from_template(seed).model_copy(update={"widget_kinds": kinds})
    with pytest.raises(Exception) as exc:
        ops.apply(seed, set_check, author="human", context=context)
    assert out == [{"error": str(exc.value)}, {"warnings": []}]


def test_apply_reads_widget_kinds_from_the_document_it_is_given(harness, fw9_stripped):
    """Two documents open at once: the one passed to ``apply`` decides, not the one opened last."""
    set_check = {"op": "set_type", "id": "w0_0", "type": "checkbox"}
    out = harness({
        "op": "applyAcrossDocs",
        "pdf": {"url": "forms/dev/fw9.pdf"},
        "other": b64(fw9_stripped),  # opened last, has no widgets
        "schema": SCHEMA,
        "ops": [set_check],
    })  # fmt: skip
    assert "can't use it" in out["a"][0]["error"]
    assert out["b"] == [{"warnings": []}]
    assert out["none"] == [{"warnings": []}]


# ------------------------------------------------------------------ detection plumbing


@pytest.mark.parametrize("size, shape", [(64, (70, 100)), (64, (50, 30)), (304, (396, 306))])
def test_letterbox_matches_the_library_exactly(harness, size, shape):
    """Pillow's bilinear resize and the centred grey padding, value for value."""
    rng = np.random.default_rng(size + shape[0])
    rgb = rng.integers(0, 256, (*shape, 3), dtype=np.uint8)
    rgba = np.dstack([rgb, np.full(shape, 255, np.uint8)])
    out = harness({"op": "letterbox", "rgba": base64.b64encode(rgba.tobytes()).decode(), "width": shape[1],
                   "height": shape[0], "size": size})  # fmt: skip
    js = np.frombuffer(base64.b64decode(out["tensor"]), dtype=np.float32).reshape(1, 3, size, size)
    py, lb = _letterbox(Image.fromarray(rgb), size)
    assert out["letterbox"] == lb._asdict()
    assert np.array_equal(js, py)


LOAD_LATE = "The detection model stopped loading: nothing arrived for 0.3 seconds. Check the connection and try again."
DETECT_LATE = "Detection did not finish within 0.3 seconds on this page."


def test_detector_load_limit_rejects_a_silent_worker_with_a_plain_message(harness):
    out = harness({"op": "detector", "worker": "silent-worker.js", "loadTimeoutMs": 300})
    assert out["error"] == LOAD_LATE
    assert 290 <= out["ms"] < 1500
    assert out["workerStopped"] is True


def test_detector_load_limit_counts_from_the_last_progress(harness):
    """Progress at 0 and 200 ms, then silence: a 300 ms inactivity limit fails at about 500 ms."""
    out = harness({"op": "detector", "worker": "stub-worker.js?mode=trickle", "loadTimeoutMs": 300})
    assert out["error"] == LOAD_LATE
    assert out["ms"] >= 480
    assert out["workerStopped"] is True


def test_detect_limit_covers_a_render_that_never_finishes(harness):
    out = harness({"op": "detector", "worker": "stub-worker.js?mode=ok", "detectTimeoutMs": 300, "render": "hang"})
    assert out["error"] == DETECT_LATE
    assert 290 <= out["ms"] < 1500


def test_detect_reports_a_failed_render_plainly(harness):
    out = harness({"op": "detector", "worker": "stub-worker.js?mode=ok", "detectTimeoutMs": 300, "render": "throw"})
    assert out["error"] == "This page could not be rendered for detection."
    assert out["cause"] == "standard font fetch failed"


def test_detect_after_a_quick_render_uses_the_rest_of_the_limit(harness):
    out = harness({"op": "detector", "worker": "stub-worker.js?mode=ok", "detectTimeoutMs": 300, "render": "blank"})
    assert out == {"error": None, "detections": [], "ms": out["ms"]}


# ------------------------------------------------------------------ parity (slow: downloads FFDNet-S)

PARITY_FORMS = ["fw9.pdf", "fw4.pdf", "i9.pdf"]


def _python_boxes(pdf: bytes) -> list[tuple[str, tuple]]:
    """``FFDNetOnnxDetector("FFDNet-S")`` on page 0 at the library's 144 dpi, boxes in points."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[0]
    found = FFDNetOnnxDetector("FFDNet-S").detect(render(page, 144))
    return [(kind, image_norm_to_points(page, box)) for kind, box, _ in found]


def _paired(a: list[tuple[str, tuple]], b: list[tuple[str, tuple]], iou_min: float) -> int:
    """Boxes of ``a`` paired one-to-one with a box of the same kind in ``b`` at IoU >= iou_min, best first."""
    pairs = sorted(((iou(x, y), i, j) for i, (ka, x) in enumerate(a) for j, (kb, y) in enumerate(b) if ka == kb),
                   reverse=True)  # fmt: skip
    used_a, used_b = set(), set()
    for value, i, j in pairs:
        if value < iou_min:
            break
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
    return len(used_a)


@pytest.mark.slow
def test_ffdnet_s_in_the_browser_matches_the_library(harness, capsys):
    """Pooled over the three pages (one borderline box moves a page of 23 boxes by 4%): at least 95%
    of boxes paired at IoU >= 0.9 with the same kind, and counts within 5%, also per page."""
    rows = []
    for name in PARITY_FORMS:
        pdf = stripped(DEV / name)
        out = harness({"op": "detect", "pdf": b64(pdf), "pages": [0], "model": "S"})
        js = [(c["kind"], tuple(c["rect"])) for c in out["detections"]["0"]]
        py = _python_boxes(pdf)
        assert py, name
        assert abs(len(js) - len(py)) <= 0.05 * len(py), (name, len(js), len(py))
        rows.append((name, len(js), len(py), _paired(js, py, 0.9), out["info"]))
    n_js, n_py, paired = (sum(r[i] for r in rows) for i in (1, 2, 3))
    with capsys.disabled():
        for name, a, b, p, info in rows:
            print(f"\n{name} p0: browser {a}, library {b}, paired at IoU>=0.9 {p}; {info}")
        print(f"pooled: browser {n_js}, library {n_py}, paired {paired} (record as Ruling D in {RULINGS})")
    assert paired >= 0.95 * max(n_js, n_py)
    assert abs(n_js - n_py) <= 0.05 * n_py
    assert rows[-1][4]["provider"] in ("webgpu", "wasm")
    # the model came from Hugging Face once; a second detector reads it from the Cache API
    model_url = "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx"
    again = harness({"op": "freshLoad", "model": "S"})
    assert again["progress"] == [[38370092, 38370092]]
    assert harness.requests.count(model_url) == 1
