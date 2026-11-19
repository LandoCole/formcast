"""FormCast Lite: conversion, prose skipping, the in-memory queue and the HTTP API.

Run from the ``lite`` folder: ``python -m pytest tests``. The test that runs the real FFDNet-S model is skipped
when its weights are not on this machine.
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from formcast_lite.convert import ConvertError, _is_box_glyph, convert, looks_like_prose  # noqa: E402
from formcast_lite.detector import Detector, model_path  # noqa: E402
from formcast_lite.jobs import Queue  # noqa: E402
from formcast_lite.server import serve  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

PROSE = (
    "This Subscription Agreement is entered into by the undersigned subscriber and the Fund, and sets out the "
    "terms on which the subscriber offers to purchase interests in the Fund, subject to acceptance by the "
    "General Partner in its sole discretion and to the conditions described in the Memorandum. "
)


class FakeDetector:
    """A text box near the top left of every page and a check box to its right, normalized to the image."""

    loaded = True
    boxes = [("text", (0.10, 0.10, 0.50, 0.14), 0.9), ("choice", (0.60, 0.10, 0.63, 0.12), 0.8),
             ("signature", (0.10, 0.80, 0.50, 0.90), 0.7)]

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def load(self):
        return self

    def detect(self, image):
        self.calls += 1
        time.sleep(self.delay)
        return list(self.boxes)


def form_page(doc: pymupdf.Document, rotate: int = 0) -> None:
    page = doc.new_page(width=612, height=792)
    for i, label in enumerate(["Name:", "Address:", "Phone:", "Date of birth:"]):
        y = 100 + 40 * i
        page.insert_text((72, y), label, fontsize=11)
        page.draw_line((170, y + 2), (540, y + 2))
    if rotate:
        page.set_rotation(rotate)


def prose_page(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), PROSE * 12, fontsize=10)


def pdf(*builders) -> bytes:
    doc = pymupdf.open()
    for build in builders:
        build(doc)
    out = doc.tobytes()
    doc.close()
    return out


def widgets(data: bytes) -> list[list[pymupdf.Widget]]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    return [[(w.field_name, w.field_type, pymupdf.Rect(w.rect), w.field_flags) for w in (p.widgets() or [])] for p in doc]


# ---------------------------------------------------------------- conversion


def test_fields_are_added_in_reading_order_with_kinds():
    out = convert(pdf(form_page), FakeDetector())
    (page,) = widgets(out)
    assert [(n, t) for n, t, _, _ in page] == [
        ("text_1_0", pymupdf.PDF_WIDGET_TYPE_TEXT),
        ("check_1_1", pymupdf.PDF_WIDGET_TYPE_CHECKBOX),
        ("signature_1_2", pymupdf.PDF_WIDGET_TYPE_TEXT),
    ]
    name, _, rect, flags = page[0]
    assert tuple(rect) == pytest.approx((61.2, 79.2, 306, 110.88), abs=0.5)
    assert flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE  # 31.7 pt tall
    sig = page[2][2]
    assert sig.y0 == pytest.approx(633.6, abs=0.5)


def test_rotated_page_fields_sit_where_the_page_is_seen():
    out = convert(pdf(lambda d: form_page(d, rotate=90)), FakeDetector())
    doc = pymupdf.open(stream=out, filetype="pdf")
    page = doc[0]
    w = next(iter(page.widgets()))
    shown = w.rect * page.rotation_matrix
    shown.normalize()
    assert shown.x0 == pytest.approx(0.10 * page.rect.width, abs=0.5)
    assert shown.y0 == pytest.approx(0.10 * page.rect.height, abs=0.5)
    assert shown.x1 == pytest.approx(0.50 * page.rect.width, abs=0.5)


def test_prose_pages_are_skipped_only_when_asked():
    data = pdf(form_page, prose_page, form_page)
    seen = []
    det = FakeDetector()
    convert(data, det, skip_prose=True, on_page=lambda i, r: r and seen.append(r.state))
    assert seen == ["boxes", "prose", "boxes"]
    assert det.calls == 2
    seen.clear()
    convert(data, FakeDetector(), skip_prose=False, on_page=lambda i, r: r and seen.append(r.state))
    assert seen == ["boxes", "boxes", "boxes"]


def test_looks_like_prose_keeps_form_pages():
    doc = pymupdf.open(stream=pdf(form_page, prose_page), filetype="pdf")
    assert not looks_like_prose(doc[0])
    assert looks_like_prose(doc[1])


def test_running_text_with_check_boxes_is_a_form():
    def drawn_boxes(doc):
        prose_page(doc)
        for y in (80, 200):
            doc[-1].draw_rect(pymupdf.Rect(60, y, 70.3, y + 10.3))

    doc = pymupdf.open(stream=pdf(drawn_boxes, prose_page), filetype="pdf")
    assert not looks_like_prose(doc[0])
    assert looks_like_prose(doc[1])
    assert _is_box_glyph("☐", "Helvetica") and _is_box_glyph("q", "Wingdings-Regular")
    assert not _is_box_glyph("q", "TimesNewRomanPSMT")


def test_pages_that_are_already_fillable_are_left_alone():
    once = convert(pdf(form_page, form_page), FakeDetector())
    det = FakeDetector()
    states = []
    twice = convert(once, det, on_page=lambda i, r: r and states.append((r.state, r.fields)))
    assert states == [("fillable", 3), ("fillable", 3)]
    assert det.calls == 0
    assert [len(p) for p in widgets(twice)] == [3, 3]


def test_unreadable_pdfs_fail_cleanly():
    with pytest.raises(ConvertError, match="not a readable PDF"):
        convert(b"hello", FakeDetector())
    doc = pymupdf.open()
    form_page(doc)
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(ConvertError, match="password"):
        convert(locked, FakeDetector())


# ---------------------------------------------------------------- queue


def wait_for(queue: Queue, pred, timeout: float = 20.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = queue.state()
        if pred(state):
            return state
        time.sleep(0.02)
    raise AssertionError(f"timed out: {queue.state()}")


def test_queue_converts_in_order_and_zips_the_results():
    q = Queue(FakeDetector())
    a = q.add("a.pdf", pdf(form_page), False)
    b = q.add("b.pdf", pdf(form_page, prose_page), True)
    bad = q.add("bad.pdf", b"nope", False)
    assert bad.status == "failed"
    state = wait_for(q, lambda s: all(d["status"] in ("done", "failed") for d in s["docs"]))
    by_name = {d["name"]: d for d in state["docs"]}
    assert by_name["a.pdf"]["fields"] == 3
    assert [p["state"] for p in by_name["b.pdf"]["pages"]] == ["boxes", "prose"]
    assert q.get(a.id).output and q.get(b.id).data is None  # the upload is let go once converted
    names = zipfile.ZipFile(io.BytesIO(q.zip_done())).namelist()
    assert names == ["a-fillable.pdf", "b-fillable.pdf"]


def test_zip_names_never_collide():
    q = Queue(FakeDetector())
    q.add("same.pdf", pdf(form_page), False)
    q.add("same.pdf", pdf(form_page), False)
    wait_for(q, lambda s: all(d["status"] == "done" for d in s["docs"]))
    assert zipfile.ZipFile(io.BytesIO(q.zip_done())).namelist() == ["same-fillable.pdf", "same-fillable (2).pdf"]


def test_reset_forgets_everything_and_stops_the_running_document():
    det = FakeDetector(delay=0.2)
    q = Queue(det)
    q.add("long.pdf", pdf(*[form_page] * 20), False)
    q.add("next.pdf", pdf(form_page), False)
    wait_for(q, lambda s: s["docs"][0]["status"] == "converting")
    q.reset()
    assert q.state()["docs"] == []
    time.sleep(0.5)
    calls = det.calls
    assert calls < 20  # stopped after its current page
    time.sleep(0.5)
    assert det.calls == calls and q.state()["docs"] == []
    q.add("after.pdf", pdf(form_page), False)  # and the queue still works
    wait_for(q, lambda s: s["docs"] and s["docs"][0]["status"] == "done")


# ---------------------------------------------------------------- HTTP


def test_http_upload_poll_download_reset():
    httpd = serve(Queue(FakeDetector()), port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert b"<title>formcast lite</title>" in urllib.request.urlopen(base + "/").read()
        req = urllib.request.Request(base + "/api/upload?name=Intake%20Form.pdf&skip_prose=1", data=pdf(form_page), method="POST")
        doc = json.load(urllib.request.urlopen(req))
        assert doc["skip_prose"] is True and len(doc["pages"]) == 1
        end = time.time() + 20
        while json.load(urllib.request.urlopen(base + "/api/state"))["docs"][0]["status"] != "done":
            assert time.time() < end
            time.sleep(0.05)
        r = urllib.request.urlopen(f"{base}/api/docs/{doc['id']}/download")
        assert r.headers["Content-Type"] == "application/pdf"
        assert "Intake%20Form-fillable.pdf" in r.headers["Content-Disposition"]
        assert len(widgets(r.read())[0]) == 3
        z = urllib.request.urlopen(base + "/api/download-all").read()
        assert zipfile.ZipFile(io.BytesIO(z)).namelist() == ["Intake Form-fillable.pdf"]
        urllib.request.urlopen(urllib.request.Request(base + "/api/reset", data=b"", method="POST"))
        assert json.load(urllib.request.urlopen(base + "/api/state"))["docs"] == []
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- the real model


def _have_model() -> bool:
    try:
        model_path(download=False)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _have_model(), reason="FFDNet-S weights not on this machine")
def test_real_model_finds_the_blanks_of_a_static_w4():
    src = REPO / "forms" / "dev" / "fw4.pdf"
    if not src.is_file():
        pytest.skip("forms/dev/fw4.pdf not here")
    doc = pymupdf.open(src)
    for page in doc:
        for w in list(page.widgets() or []):
            page.delete_widget(w)
    static = doc.tobytes()
    out = convert(static, Detector())
    counts = [len(p) for p in widgets(out)]
    assert counts[0] >= 15 and sum(counts) >= 40
