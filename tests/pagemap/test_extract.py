import pymupdf
import pytest

from formcast.geometry import points_to_norm
from formcast.pagemap import PageMap, Word
from formcast.pagemap.extract import (
    extract_page,
    image_norm_to_points,
    needs_ocr,
    text_layer_coverage,
)


def _page_map(path) -> PageMap:
    with pymupdf.open(path) as doc:
        return extract_page(doc[0])


def _word(pm: PageMap, prefix: str) -> Word:
    matches = [w for w in pm.words if w.text.startswith(prefix)]
    assert len(matches) == 1, [w.text for w in pm.words]
    return matches[0]


def _close(a, b, tol=1.0) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _baseline(page: pymupdf.Page, prefix: str) -> float:
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].startswith(prefix):
                    return span["origin"][1]
    raise AssertionError(f"no span starting with {prefix!r}")


def test_flat_form_lines_rules_and_no_widgets(flat_form_pdf):
    with pymupdf.open(flat_form_pdf) as doc:
        page = doc[0]
        pm = extract_page(page)
        baseline = _baseline(page, "Name")

    name_lines = [ln for ln in pm.lines if ln.text.startswith("Name")]
    assert len(name_lines) == 1
    name_line = name_lines[0]
    right_rules = [
        r
        for r in pm.rules
        if r.orientation == "h"
        and r.rect[0] >= name_line.rect[2] - 1
        and abs((r.rect[1] + r.rect[3]) / 2 - baseline) < 4
    ]
    assert right_rules
    assert pm.widgets == []
    assert pm.candidates == []
    assert pm.index == 0
    assert (pm.width, pm.height, pm.rotation) == (612, 792, 0)
    assert pm.kind == "digital"
    assert pm.text_source == "pdf"


def test_words_and_lines_are_cross_linked(flat_form_pdf):
    pm = _page_map(flat_form_pdf)
    assert {ln.text for ln in pm.lines} == {"Name:", "Yes", "Date"}
    for line in pm.lines:
        assert line.word_ids
        for wid in line.word_ids:
            assert pm.words[wid].line_id == line.id
            assert pm.words[wid].source == "pdf"
        assert " ".join(pm.words[wid].text for wid in line.word_ids) == line.text
        assert line.font_size == pytest.approx(12 if line.text != "Yes" else 11)


def test_small_box_edges_are_not_rules(flat_form_pdf):
    pm = _page_map(flat_form_pdf)
    # Only the two drawn lines qualify; the 10x10 box edges are under 20pt.
    assert sorted((r.orientation, r.rect) for r in pm.rules) == [
        ("h", (72.0, 212.0, 250.0, 212.0)),
        ("h", (120.0, 100.0, 300.0, 100.0)),
    ]


def test_large_rectangle_becomes_four_edges(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(pymupdf.Rect(100, 300, 400, 350), width=1)
    page.draw_rect(pymupdf.Rect(100, 500, 400, 501.5), color=None, fill=(0, 0, 0))
    pm = extract_page(page)
    edges = sorted((r.orientation, r.rect) for r in pm.rules)
    assert edges == [
        ("h", (100.0, 300.0, 400.0, 300.0)),
        ("h", (100.0, 350.0, 400.0, 350.0)),
        ("h", (100.0, 500.0, 400.0, 501.5)),
        ("v", (100.0, 300.0, 100.0, 350.0)),
        ("v", (400.0, 300.0, 400.0, 350.0)),
    ]


def test_bold_and_font_size(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Section A", fontsize=14, fontname="hebo")
    page.insert_text((72, 140), "plain text", fontsize=9)
    pm = extract_page(page)
    by_text = {ln.text: ln for ln in pm.lines}
    assert by_text["Section A"].bold is True
    assert by_text["Section A"].font_size == pytest.approx(14)
    assert by_text["plain text"].bold is False
    assert by_text["plain text"].font_size == pytest.approx(9)


def test_fillable_widgets(fillable_form_pdf):
    pm = _page_map(fillable_form_pdf)
    assert len(pm.widgets) == 2
    assert {w.kind for w in pm.widgets} == {"text", "checkbox"}
    assert {w.name for w in pm.widgets} == {"applicant_name", "agree"}
    text = next(w for w in pm.widgets if w.name == "applicant_name")
    assert _close(text.rect, (120, 86, 300, 100))
    assert pm.kind == "fillable"


def test_rotated_word_rect_matches_unrotated(flat_form_pdf, rotated_form_pdf):
    flat = _page_map(flat_form_pdf)
    rot = _page_map(rotated_form_pdf)
    assert rot.rotation == 90
    assert (rot.width, rot.height) == (flat.width, flat.height)
    assert _close(_word(rot, "Name").rect, _word(flat, "Name").rect)
    assert sorted(r.rect for r in rot.rules) == sorted(r.rect for r in flat.rules)


def test_cropped_word_rect_is_relative_to_crop_box(flat_form_pdf, cropped_form_pdf):
    flat = _page_map(flat_form_pdf)
    crop = _page_map(cropped_form_pdf)
    assert (crop.width, crop.height) == (540, 720)
    fx0, fy0, fx1, fy1 = _word(flat, "Name").rect
    assert _close(_word(crop, "Name").rect, (fx0 - 36, fy0 - 36, fx1 - 36, fy1 - 36))
    flat_rule = next(r for r in flat.rules if r.rect[1] == 100)
    shifted = tuple(v - 36 for v in flat_rule.rect)
    assert any(_close(r.rect, shifted) for r in crop.rules)


def test_needs_ocr_flat_false_image_only_true(flat_form_pdf, image_only_pdf):
    with pymupdf.open(flat_form_pdf) as doc:
        assert needs_ocr(doc[0]) is False
    with pymupdf.open(image_only_pdf) as doc:
        assert needs_ocr(doc[0]) is True
    assert _page_map(image_only_pdf).kind == "scanned"


def test_text_layer_coverage(flat_form_pdf, image_only_pdf):
    with pymupdf.open(flat_form_pdf) as doc:
        page = doc[0]
        assert text_layer_coverage(page, extract_page(page).rules) > 0.6
    with pymupdf.open(image_only_pdf) as doc:
        assert text_layer_coverage(doc[0], []) == 0.0


def test_low_coverage_without_dominant_image_needs_ocr(flat_form_pdf):
    # Labels present only as pixels on a half-page image: not image-dominated,
    # but no extractable text covers the ink.
    with pymupdf.open(flat_form_pdf) as src:
        png = src[0].get_pixmap(dpi=150, clip=pymupdf.Rect(0, 0, 612, 396)).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(0, 0, 612, 396), stream=png)
    page.insert_text((72, 600), "ok", fontsize=8)
    assert needs_ocr(page) is True


def test_underscore_run_becomes_h_rule():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 300), "Phone ________", fontsize=12)
    hit = page.search_for("________")[0]
    pm = extract_page(page)

    assert [ln.text for ln in pm.lines] == ["Phone"]
    assert all("_" not in w.text for w in pm.words)
    h_rules = [r for r in pm.rules if r.orientation == "h"]
    assert len(h_rules) == 1
    rx0, ry0, rx1, ry1 = h_rules[0].rect
    assert abs(rx0 - hit.x0) <= 1 and abs(rx1 - hit.x1) <= 1
    assert hit.y0 <= ry0 <= ry1 <= hit.y1


def test_underscore_run_splits_line():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 300), "Name ______ Date ____", fontsize=12)
    pm = extract_page(page)
    assert [ln.text for ln in pm.lines] == ["Name", "Date"]
    assert len([r for r in pm.rules if r.orientation == "h"]) == 2


def _pixmap_norm_box(page: pymupdf.Page, rect) -> tuple:
    """Where an unrotated crop-box rect shows up on page.get_pixmap(), normalized."""
    pix = page.get_pixmap()
    shown = pymupdf.Rect(rect) * page.rotation_matrix
    # Sanity: the rendered pixels in that region actually contain ink.
    samples = pix.samples
    n = pix.n
    ink = 0
    for y in range(int(shown.y0), int(shown.y1)):
        for x in range(int(shown.x0), int(shown.x1)):
            if samples[(y * pix.width + x) * n] < 128:
                ink += 1
    assert ink > 10
    return points_to_norm(tuple(shown), (0, 0, pix.width, pix.height))


@pytest.mark.parametrize("fixture", ["rotated_form_pdf", "cropped_form_pdf"])
def test_image_norm_to_points_round_trips(fixture, request):
    path = request.getfixturevalue(fixture)
    with pymupdf.open(path) as doc:
        page = doc[0]
        name = _word(extract_page(page), "Name").rect
        box = _pixmap_norm_box(page, name)
        assert _close(image_norm_to_points(page, box), name)


def test_image_norm_to_points_rotated_and_cropped(rotated_form_pdf):
    with pymupdf.open(rotated_form_pdf) as doc:
        page = doc[0]
        page.set_cropbox(pymupdf.Rect(36, 36, 576, 756))
        name = _word(extract_page(page), "Name").rect
        box = _pixmap_norm_box(page, name)
        assert _close(image_norm_to_points(page, box), name)
        assert _close(image_norm_to_points(page, (0, 0, 1, 1)), (0, 0, 540, 720))


def _section_bar_page() -> pymupdf.Page:
    """Born-digital page: a black section bar with white text and 12 checkboxes."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(pymupdf.Rect(36, 72, 576, 90), color=None, fill=(0, 0, 0))
    page.insert_text((40, 85), "SECTION 1. HISTORY", fontsize=11, color=(1, 1, 1))
    for i in range(12):
        x, y = 40 + (i % 3) * 180, 110 + (i // 3) * 20
        page.draw_rect(pymupdf.Rect(x, y, x + 10, y + 10), width=0.75)
        page.insert_text((x + 14, y + 9), "Item", fontsize=9)
    return page


def test_dark_vector_shapes_do_not_make_a_digital_page_scanned():
    page = _section_bar_page()
    assert needs_ocr(page) is False
    assert extract_page(page).kind == "digital"


def _glyph_label_page(white_background: bool = False) -> pymupdf.Page:
    """Labels drawn as small filled outlines (curves and line polygons), as some
    born-digital forms do; only a few real words and some drawn rules."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if white_background:
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    for row in range(10):
        y = 80 + row * 40
        for col in range(20):
            x = 40 + col * 8
            if col % 2:
                page.draw_oval(pymupdf.Rect(x, y, x + 5, y + 7), color=None, fill=(0, 0, 0))
            else:
                ell = [(x, y), (x + 1.5, y), (x + 1.5, y + 5.5), (x + 5, y + 5.5),
                       (x + 5, y + 7), (x, y + 7), (x, y)]
                page.draw_polyline(ell, color=None, fill=(0, 0, 0), closePath=True)
        page.draw_line((40, y + 20), (560, y + 20), width=0.75)
        page.draw_rect(pymupdf.Rect(400, y, 410, y + 10), width=0.75)
    page.insert_text((40, 700), "Form 1", fontsize=8)
    return page


@pytest.mark.parametrize("white_background", [False, True])
def test_labels_drawn_as_glyph_paths_need_ocr(white_background):
    page = _glyph_label_page(white_background)
    assert needs_ocr(page) is True
    assert extract_page(page).kind == "scanned"


def test_invisible_paint_is_not_geometry():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))  # white page background
    page.draw_rect(pymupdf.Rect(36, 300, 576, 340), color=(0.95, 0.95, 0.95), width=1)
    page.draw_rect(pymupdf.Rect(36, 400, 576, 440), color=(0, 0, 0), fill_opacity=0,
                   stroke_opacity=0, fill=(0, 0, 0))
    page.insert_text((72, 100), "Name:", fontsize=12)
    page.draw_line((120, 100), (300, 100), width=0.75)
    pm = extract_page(page)
    assert [r.rect for r in pm.rules] == [(120.0, 100.0, 300.0, 100.0)]
