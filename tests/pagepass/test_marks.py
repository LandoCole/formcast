from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from formcast.errors import FormcastError
from formcast.pagepass.marks import marks_for, page_dpi, render_marked_page
from formcast.template.model import Field


def _field(id: str, type: str = "text", rect=(100, 300, 160, 330)) -> Field:
    return Field(id=id, type=type, page=0, rect=rect)


def _image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_marks_are_id_suffixes():
    assert marks_for([_field("w0_3"), _field("w0_10")]) == {"w0_3": "3", "w0_10": "10"}


def test_colliding_suffixes_fall_back_to_full_ids():
    assert marks_for([_field("w0_3"), _field("c0_3")]) == {"w0_3": "w0_3", "c0_3": "c0_3"}


def test_letter_page_renders_at_140_dpi(flat_form_pdf):
    assert page_dpi(612, 792) == 140
    img = _image(render_marked_page(flat_form_pdf, 0, [_field("w0_1")]))
    assert abs(img.width - round(612 * 140 / 72)) <= 1
    assert abs(img.height - round(792 * 140 / 72)) <= 1


def test_legal_page_long_edge_is_capped():
    dpi = page_dpi(612, 1008)
    assert dpi < 140
    assert 1008 / 72 * dpi <= 1568 + 1e-6


def test_box_outline_uses_the_type_colour(flat_form_pdf):
    rect = (100, 300, 160, 330)
    img = _image(render_marked_page(flat_form_pdf, 0, [_field("c0_1", "checkbox", rect)]))
    x0 = rect[0] * 140 / 72
    y_mid = round((rect[1] + rect[3]) / 2 * 140 / 72)
    assert img.getpixel((round(x0), y_mid)) == (44, 160, 44)


def test_fields_without_rect_are_skipped(flat_form_pdf):
    blank = Field.model_construct(id="w0_9", type="text", page=0, rect=None)
    assert marks_for([blank]) == {"w0_9": "9"}
    png = render_marked_page(flat_form_pdf, 0, [blank, _field("w0_1")])
    assert png.startswith(b"\x89PNG")


def test_accepts_pdf_bytes(flat_form_pdf):
    png = render_marked_page(flat_form_pdf.read_bytes(), 0, [_field("w0_1")])
    assert png.startswith(b"\x89PNG")


def test_boxes_follow_page_rotation(rotated_form_pdf):
    # /Rotate 90 (clockwise) on a 612x792 page maps unrotated (x, y) to (792 - y, x).
    rect = (100, 300, 160, 330)
    img = _image(render_marked_page(rotated_form_pdf, 0, [_field("c0_1", "checkbox", rect)]))
    assert img.size == (round(792 * 140 / 72), round(612 * 140 / 72))
    z = 140 / 72
    left, mid_y = (792 - 330) * z, (100 + 160) / 2 * z
    assert img.getpixel((round(left), round(mid_y))) == (44, 160, 44)
    assert img.getpixel((round(rect[0] * z), round(rect[1] * z))) != (44, 160, 44)


def test_page_out_of_range_raises_formcast_error(flat_form_pdf):
    with pytest.raises(FormcastError, match="page 3 out of range"):
        render_marked_page(flat_form_pdf, 3, [])


def test_oversize_page_renders_with_capped_edge_and_aligned_box(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=1008)
    pdf = doc.tobytes()
    doc.close()
    rect = (100, 600, 160, 640)
    img = _image(render_marked_page(pdf, 0, [_field("c0_1", "checkbox", rect)]))
    assert max(img.size) <= 1568
    z = img.height / 1008  # pixels per point actually rendered
    assert img.getpixel((round(rect[0] * z), round((rect[1] + rect[3]) / 2 * z))) == (44, 160, 44)


def test_rendering_waits_for_the_process_wide_pdf_lock(flat_form_pdf, monkeypatch):
    import threading

    from formcast.pagemap import extract
    from formcast.pagepass import marks as marks_mod

    opened = threading.Event()
    real_open = marks_mod.open_pdf

    def open_pdf(pdf):
        opened.set()
        return real_open(pdf)

    monkeypatch.setattr(marks_mod, "open_pdf", open_pdf)
    done: list[bytes] = []
    with extract.PDF_LOCK:  # held by this thread: a render on another thread must not start MuPDF work
        worker = threading.Thread(target=lambda: done.append(render_marked_page(flat_form_pdf, 0, [_field("w0_1")])))
        worker.start()
        assert not opened.wait(0.3)
    worker.join(10)
    assert opened.is_set() and done and done[0].startswith(b"\x89PNG")
    with extract.PDF_LOCK:  # re-entrant: the thread holding it can still render
        assert render_marked_page(flat_form_pdf, 0, []).startswith(b"\x89PNG")
