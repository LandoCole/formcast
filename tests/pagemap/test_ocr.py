import io
import math

import pymupdf
import pytest
from PIL import Image

from formcast.errors import FormcastError
from formcast.pagemap import ocr
from formcast.pagemap.extract import extract_page
from formcast.pagemap.ocr import deskew, estimate_skew, ocr_available, ocr_page

needs_tesseract = pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")

PAGE_W, PAGE_H = 612, 792


def _close(a, b, tol) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _matches(found, expected, tol=3.0) -> bool:
    """Horizontal extent and vertical center agree within ``tol`` points.

    A PDF word rect spans the font's ascent to descent while Tesseract boxes the
    ink, so the top and bottom edges differ by a few points by construction.
    """
    center = lambda r: (r[1] + r[3]) / 2  # noqa: E731
    return (
        abs(found[0] - expected[0]) <= tol
        and abs(found[2] - expected[2]) <= tol
        and abs(center(found) - center(expected)) <= tol
    )


def _word(words, prefix):
    matches = [w for w in words if w.text.startswith(prefix)]
    assert len(matches) == 1, [w.text for w in words]
    return matches[0]


def _form_image(flat_form_pdf, dpi=150) -> Image.Image:
    with pymupdf.open(flat_form_pdf) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _image_pdf(path, image: Image.Image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


@needs_tesseract
def test_ocr_finds_name_word_where_the_text_layer_has_it(flat_form_pdf, image_only_pdf):
    with pymupdf.open(flat_form_pdf) as doc:
        expected = _word(extract_page(doc[0]).words, "Name").rect
    with pymupdf.open(image_only_pdf) as doc:
        words, lines = ocr_page(doc[0])
    found = _word(words, "Name")
    assert found.source == "ocr"
    assert 0.0 <= found.conf <= 1.0
    assert _matches(found.rect, expected), (found.rect, expected)
    assert {w.text.strip(":") for w in words} >= {"Name", "Yes", "Date"}
    # Lines are consistent with words: ids, text and rect.
    assert [ln.id for ln in lines] == list(range(len(lines)))
    for line in lines:
        ws = [words[i] for i in line.word_ids]
        assert all(w.line_id == line.id for w in ws)
        assert line.text == " ".join(w.text for w in ws)
        assert line.rect[0] == min(w.rect[0] for w in ws)
        assert line.rect[3] == max(w.rect[3] for w in ws)
    assert len({w.line_id for w in words}) >= 3  # Name, Yes, Date on separate lines


@needs_tesseract
def test_ocr_on_cropped_scan_is_relative_to_the_crop_box(
    flat_form_pdf, cropped_form_pdf, tmp_path
):
    # Scan of the full page, then crop box (36, 36, 576, 756) on the scan page.
    path = _image_pdf(tmp_path / "scan_cropped.pdf", _form_image(flat_form_pdf))
    with pymupdf.open(path) as doc:
        doc[0].set_cropbox(pymupdf.Rect(36, 36, PAGE_W - 36, PAGE_H - 36))
        words, _ = ocr_page(doc[0])
    with pymupdf.open(cropped_form_pdf) as doc:
        expected = _word(extract_page(doc[0]).words, "Name").rect
    assert _matches(_word(words, "Name").rect, expected)


@needs_tesseract
def test_ocr_on_rotated_scan_is_in_unrotated_page_space(flat_form_pdf, tmp_path):
    # Image content is the form turned 90 degrees counter-clockwise on a landscape
    # media box; /Rotate 90 shows it upright, so Tesseract can read it.
    upright = _form_image(flat_form_pdf)
    turned = upright.rotate(90, expand=True)
    buf = io.BytesIO()
    turned.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_H, height=PAGE_W)
    page.insert_image(page.rect, stream=buf.getvalue())
    page.set_rotation(90)
    with pymupdf.open(flat_form_pdf) as src:
        x0, y0, x1, y1 = _word(extract_page(src[0]).words, "Name").rect
    # Counter-clockwise quarter turn of a PAGE_W x PAGE_H sheet: (x, y) -> (y, PAGE_W - x).
    expected = (y0, PAGE_W - x1, y1, PAGE_W - x0)
    words, _ = ocr_page(page)
    doc.close()
    found = _word(words, "Name").rect
    # The text runs along y here: its extent edges and its x center must agree.
    assert abs(found[1] - expected[1]) <= 3.0 and abs(found[3] - expected[3]) <= 3.0, found
    assert abs((found[0] + found[2]) / 2 - (expected[0] + expected[2]) / 2) <= 3.0, found


def test_estimate_skew_recovers_rotation(flat_form_pdf):
    base = _form_image(flat_form_pdf)
    for angle in (1.5, -1.5, 0.0):
        rotated = base.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        assert estimate_skew(rotated) == pytest.approx(angle, abs=0.3)


def test_deskew_straightens_the_image(flat_form_pdf):
    base = _form_image(flat_form_pdf)
    rotated = base.rotate(1.5, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    fixed = deskew(rotated, estimate_skew(rotated))
    assert fixed.size == rotated.size
    assert abs(estimate_skew(fixed)) <= 0.2


def test_unrotate_point_inverts_deskew():
    # A single dark pixel, rotated away from the page center and deskewed back.
    img = Image.new("L", (400, 300), 255)
    img.putpixel((350, 60), 0)
    skewed = img.rotate(1.5, resample=Image.BILINEAR, fillcolor=255)
    angle = 1.5
    fixed = deskew(skewed, angle)
    # Locate the darkest pixel in the deskewed image and map it back.
    px = min(
        ((x, y) for x in range(400) for y in range(300)),
        key=lambda p: fixed.getpixel(p),
    )
    x, y = ocr._to_original(px[0] + 0.5, px[1] + 0.5, angle, fixed.size)
    sx, sy = min(
        ((x_, y_) for x_ in range(400) for y_ in range(300)),
        key=lambda p: skewed.getpixel(p),
    )
    assert math.hypot(x - (sx + 0.5), y - (sy + 0.5)) < 1.5


@needs_tesseract
def test_ocr_of_skewed_page_maps_back_within_3pt(flat_form_pdf, tmp_path, monkeypatch):
    skewed = _form_image(flat_form_pdf, dpi=300).rotate(
        1.5, resample=Image.BICUBIC, fillcolor=(255, 255, 255)
    )
    path = _image_pdf(tmp_path / "skewed.pdf", skewed)
    with pymupdf.open(path) as doc:
        words, _ = ocr_page(doc[0])
        assert ocr.estimate_skew(skewed) == pytest.approx(1.5, abs=0.3)
        with_deskew = _word(words, "Name").rect
        # Reference: the same page OCRed without any deskew.
        monkeypatch.setattr(ocr, "estimate_skew", lambda image: 0.0)
        words0, _ = ocr_page(doc[0])
    reference = _word(words0, "Name").rect
    assert _close(with_deskew, reference, 3.0), (with_deskew, reference)
    # And near where the word actually sits on the skewed page: the unrotated
    # position moved by the 1.5 degree rotation about the page center.
    with pymupdf.open(flat_form_pdf) as doc:
        x0, y0, x1, y1 = _word(extract_page(doc[0]).words, "Name").rect
    cx, cy = PAGE_W / 2, PAGE_H / 2
    t = math.radians(1.5)

    def fwd(x, y):
        dx, dy = x - cx, y - cy
        return cx + dx * math.cos(t) + dy * math.sin(t), cy - dx * math.sin(t) + dy * math.cos(t)

    mx, my = fwd((x0 + x1) / 2, (y0 + y1) / 2)
    assert abs((with_deskew[0] + with_deskew[2]) / 2 - mx) < 3.0
    assert abs((with_deskew[1] + with_deskew[3]) / 2 - my) < 3.0


def test_missing_tesseract_raises(flat_form_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    with pymupdf.open(flat_form_pdf) as doc:
        with pytest.raises(FormcastError) as exc:
            ocr_page(doc[0])
    assert str(exc.value) == (
        "OCR requested but Tesseract is not installed; "
        "pip install formcast[ocr] and install tesseract"
    )
