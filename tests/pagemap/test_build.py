import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.geometry import points_to_norm
from formcast.match import fingerprint
from formcast.pagemap import build
from formcast.pagemap.build import build_form_map, link_labels, split_wide_lines
from formcast.pagemap.model import Candidate, PageMap, Rule, TextLine, Word
from formcast.pagemap.ocr import ocr_available
from formcast.pagemap.query import right_text

needs_tesseract = pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")

PAGE = (0.0, 0.0, 612.0, 792.0)
NAME_BOX = (120.0, 86.0, 300.0, 100.0)
YES_BOX = (72.0, 140.0, 82.0, 150.0)
DATE_BOX = (72.0, 204.0, 250.0, 212.0)


class FakeDetector:
    """Returns fixed boxes (given in points on an unrotated Letter page)."""

    def __init__(self, name, boxes):
        self.name = name
        self.boxes = [(kind, points_to_norm(rect, PAGE), conf) for kind, rect, conf in boxes]
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return list(self.boxes)


class ExplodingDetector:
    name = "boom"

    def detect(self, image):
        raise AssertionError("detection should have been skipped")


def _flat_detector(name="FFDNet-L"):
    return FakeDetector(
        name,
        [("text", NAME_BOX, 0.9), ("choice", YES_BOX, 0.8), ("text", DATE_BOX, 0.85)],
    )


def _cand_at(fm, rect):
    for c in fm.pages[0].candidates:
        if all(abs(a - b) < 1.0 for a, b in zip(c.rect, rect)):
            return c
    raise AssertionError(f"no candidate at {rect}: {[c.rect for c in fm.pages[0].candidates]}")


def _line_text(page, line_id):
    return next(line.text for line in page.lines if line.id == line_id)


# --- build_form_map -------------------------------------------------------


def test_flat_form_is_digital_with_candidates_and_left_label(flat_form_pdf):
    det = _flat_detector()
    fm = build_form_map(flat_form_pdf, detectors={"FFDNet-L": det})
    page = fm.pages[0]
    assert page.kind == "digital"
    assert page.text_source == "pdf"
    assert len(page.candidates) == 3
    name = _cand_at(fm, NAME_BOX)
    assert name.label_relation == "left"
    assert _line_text(page, name.label_line_id) == "Name:"
    date = _cand_at(fm, DATE_BOX)
    assert date.label_relation == "above"
    assert _line_text(page, date.label_line_id) == "Date"
    assert fm.source_name == "flat_form.pdf"
    assert fm.fingerprint == fingerprint(flat_form_pdf)


def test_bytes_input_matches_path_input(flat_form_pdf):
    from_path = build_form_map(flat_form_pdf, detectors={"FFDNet-L": _flat_detector()})
    from_bytes = build_form_map(
        flat_form_pdf.read_bytes(), detectors={"FFDNet-L": _flat_detector()}
    )
    assert from_bytes.pages == from_path.pages
    assert from_bytes.fingerprint == from_path.fingerprint
    assert from_bytes.source_name == ""


def test_fillable_form_skips_detection(fillable_form_pdf):
    policy = build.DetectPolicy(digital=("boom",), scanned=("boom",))
    fm = build_form_map(fillable_form_pdf, detectors={"boom": ExplodingDetector()}, policy=policy)
    page = fm.pages[0]
    assert page.kind == "fillable"
    assert page.candidates == []
    assert len(page.widgets) == 2


def test_encrypted_pdf_raises(flat_form_pdf, tmp_path):
    out = tmp_path / "locked.pdf"
    with pymupdf.open(flat_form_pdf) as doc:
        doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(FormcastError, match="encrypted PDF"):
        build_form_map(out, detectors={})
    with pytest.raises(FormcastError, match="encrypted PDF"):
        fingerprint(out)


@pytest.mark.parametrize("data", [b"this is not a pdf at all", b"", b"%PDF-1.7\n%%EOF"])
def test_garbage_bytes_raise_cannot_open(data):
    with pytest.raises(FormcastError, match="cannot open PDF"):
        build_form_map(data, detectors={})


def test_missing_file_raises_cannot_open(tmp_path):
    with pytest.raises(FormcastError, match="cannot open PDF"):
        build_form_map(tmp_path / "nope.pdf", detectors={})


def test_invalid_ocr_mode_is_rejected(flat_form_pdf):
    with pytest.raises(ValueError, match="ocr"):
        build_form_map(flat_form_pdf, detectors={}, ocr="sometimes")


def test_scanned_page_uses_scanned_detectors(image_only_pdf, monkeypatch):
    monkeypatch.setattr(build, "ocr_available", lambda: False)
    digital, scanned = _flat_detector("FFDNet-L"), _flat_detector("FFDetr")
    fm = build_form_map(image_only_pdf, detectors={"FFDNet-L": digital, "FFDetr": scanned})
    assert fm.pages[0].kind == "scanned"
    assert (digital.calls, scanned.calls) == (0, 1)
    assert len(fm.pages[0].candidates) == 3


def test_scan_stored_as_image_tiles_is_scanned(image_only_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ocr_available", lambda: False)
    with pymupdf.open(image_only_pdf) as src:
        png = src[0].get_pixmap(dpi=72).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(0, 0, 612, 396), stream=png, keep_proportion=False)
    page.insert_image(pymupdf.Rect(0, 396, 612, 792), stream=png, keep_proportion=False)
    path = tmp_path / "tiles.pdf"
    doc.save(path)
    doc.close()
    fm = build_form_map(path, detectors={"FFDetr": _flat_detector("FFDetr")})
    assert fm.pages[0].kind == "scanned"


def test_ocr_unavailable_keeps_pdf_text_and_records_it(image_only_pdf, monkeypatch):
    monkeypatch.setattr(build, "ocr_available", lambda: False)
    fm = build_form_map(image_only_pdf, detectors={"FFDetr": _flat_detector("FFDetr")})
    page = fm.pages[0]
    assert page.text_source == "pdf"
    assert any("Tesseract" in note for note in page.notes)


def test_ocr_always_without_tesseract_raises(flat_form_pdf, monkeypatch):
    monkeypatch.setattr(build, "ocr_available", lambda: False)

    def missing(page, dpi=300):
        raise FormcastError("OCR requested but Tesseract is not installed")

    monkeypatch.setattr(build, "ocr_page", missing)
    with pytest.raises(FormcastError, match="Tesseract"):
        build_form_map(flat_form_pdf, detectors={"FFDNet-L": _flat_detector()}, ocr="always")


def test_ocr_never_does_not_run_ocr(image_only_pdf, monkeypatch):
    def fail(page, dpi=300):
        raise AssertionError("OCR should not run")

    monkeypatch.setattr(build, "ocr_page", fail)
    fm = build_form_map(image_only_pdf, detectors={"FFDetr": _flat_detector("FFDetr")}, ocr="never")
    page = fm.pages[0]
    assert page.text_source == "pdf"
    assert page.notes  # the missing text layer is recorded


def _fake_ocr(page, dpi=300):
    words = [Word(text="Fresh", rect=(40, 80, 70, 90), line_id=0, source="ocr", conf=0.9)]
    lines = [TextLine(id=0, text="Fresh", rect=(40, 80, 70, 90), word_ids=[0], font_size=10)]
    return words, lines


def test_vector_glyph_page_is_digital_but_its_text_comes_from_ocr(tmp_path, monkeypatch):
    # Labels drawn as filled outlines: no image, but the text layer covers little ink.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for row in range(10):
        y = 80 + row * 40
        for col in range(20):
            x = 40 + col * 8
            page.draw_oval(pymupdf.Rect(x, y, x + 5, y + 7), color=None, fill=(0, 0, 0))
        page.draw_line((40, y + 20), (560, y + 20), width=0.75)
    page.insert_text((40, 700), "Form 1", fontsize=8)
    path = tmp_path / "glyphs.pdf"
    doc.save(path)
    doc.close()
    monkeypatch.setattr(build, "ocr_available", lambda: True)
    monkeypatch.setattr(build, "ocr_page", _fake_ocr)
    digital, scanned = _flat_detector("FFDNet-L"), _flat_detector("FFDetr")
    fm = build_form_map(path, detectors={"FFDNet-L": digital, "FFDetr": scanned})
    pm = fm.pages[0]
    assert pm.kind == "digital"
    assert (digital.calls, scanned.calls) == (1, 0)
    assert pm.text_source == "ocr"
    assert [w.text for w in pm.words] == ["Fresh"]


def test_bad_embedded_text_layer_is_replaced_by_ocr(image_only_pdf, tmp_path, monkeypatch):
    out = tmp_path / "bad_layer.pdf"
    with pymupdf.open(image_only_pdf) as doc:
        doc[0].insert_text((72, 400), "zzqx garbage layer", fontsize=12, render_mode=3)
        doc.save(out)
    monkeypatch.setattr(build, "ocr_available", lambda: True)
    monkeypatch.setattr(build, "ocr_page", _fake_ocr)
    fm = build_form_map(out, detectors={"FFDetr": _flat_detector("FFDetr")})
    pm = fm.pages[0]
    assert pm.text_source == "ocr"
    assert [w.text for w in pm.words] == ["Fresh"]


@needs_tesseract
def test_real_ocr_on_image_only_page_links_labels(image_only_pdf):
    fm = build_form_map(image_only_pdf, detectors={"FFDetr": _flat_detector("FFDetr")})
    page = fm.pages[0]
    assert page.text_source == "ocr"
    name = _cand_at(fm, NAME_BOX)
    assert name.label_relation == "left"
    assert _line_text(page, name.label_line_id).startswith("Name")


def test_wide_gaps_split_a_line(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((36, 100), "Name", fontsize=10)
    page.insert_text((300, 100), "DOB", fontsize=10)
    page.insert_text((36, 140), "1 Name of entity", fontsize=10)
    path = tmp_path / "gaps.pdf"
    doc.save(path)
    doc.close()
    fm = build_form_map(path, detectors={"FFDNet-L": FakeDetector("FFDNet-L", [])})
    pm = fm.pages[0]
    assert [line.text for line in pm.lines] == ["Name", "DOB", "1 Name of entity"]
    assert [line.id for line in pm.lines] == [0, 1, 2]
    for line in pm.lines:
        assert all(pm.words[i].line_id == line.id for i in line.word_ids)


# --- split_wide_lines / link_labels on synthetic page maps -----------------


def _w(text, rect):
    return Word(text=text, rect=rect, line_id=0, source="pdf")


def test_split_wide_lines_keeps_ordinary_spacing():
    words = [_w("Date", (10, 0, 30, 10)), _w("of", (33, 0, 42, 10)), _w("birth", (45, 0, 70, 10))]
    lines = [TextLine(id=0, text="Date of birth", rect=(10, 0, 70, 10), word_ids=[0, 1, 2],
                      font_size=10, bold=True)]
    new_words, new_lines = split_wide_lines(words, lines)
    assert new_lines == lines
    assert new_words == words


def _page(lines, candidates, rules=()):
    words, text_lines = [], []
    for i, (text, rect, size) in enumerate(lines):
        words.append(Word(text=text, rect=rect, line_id=i, source="pdf"))
        text_lines.append(TextLine(id=i, text=text, rect=rect, word_ids=[i], font_size=size))
    cands = [
        Candidate(id=f"c0_{i}", kind=kind, rect=rect, conf=0.9, detector="t")
        for i, (kind, rect) in enumerate(candidates)
    ]
    return PageMap(index=0, width=612, height=792, rotation=0, kind="digital",
                   text_source="pdf", words=words, lines=text_lines,
                   rules=[Rule(rect=r, orientation=o) for r, o in rules], widgets=[],
                   candidates=cands)


def _link(page):
    link_labels(page)
    return [
        (page.lines[c.label_line_id].text if c.label_line_id is not None else None,
         c.label_relation)
        for c in page.candidates
    ]


def test_link_left_on_same_row():
    page = _page([("Name:", (72, 90, 105, 103), 12)], [("text", (120, 86, 300, 100))])
    assert _link(page) == [("Name:", "left")]


def test_link_above_needs_horizontal_overlap():
    page = _page(
        [("Employer", (100, 100, 160, 110), 10), ("Elsewhere", (400, 100, 460, 110), 10)],
        [("text", (90, 120, 300, 135))],
    )
    assert _link(page) == [("Employer", "above")]


def test_link_above_rejects_a_line_more_than_18pt_up():
    page = _page([("Far", (100, 100, 160, 110), 10)], [("text", (90, 130, 300, 145))])
    assert _link(page) == [(None, None)]


def test_link_inside_then_below():
    page = _page(
        [("Signature", (105, 102, 160, 110), 8), ("Date", (420, 141, 440, 149), 8)],
        [("text", (100, 100, 300, 130)), ("text", (400, 120, 500, 136))],
    )
    assert _link(page) == [("Signature", "inside"), ("Date", "below")]


def test_left_label_is_not_taken_across_another_candidate():
    page = _page(
        [("Name:", (72, 90, 105, 103), 12)],
        [("text", (110, 86, 250, 100)), ("text", (260, 86, 400, 100))],
    )
    assert _link(page) == [("Name:", "left"), (None, None)]


def test_rules_between_do_not_block_table_headers():
    # Column header above a cell, separated by the cell's top border.
    page = _page(
        [("Initials", (495, 38, 523, 51), 10)],
        [("text", (491, 54, 529, 73))],
        rules=[((490, 53, 530, 53), "h"), ((490, 53, 490, 73), "v")],
    )
    assert _link(page) == [("Initials", "above")]


def test_caption_under_one_box_is_not_the_label_above_the_next():
    # Two signature rows: each caption is printed right under its own line.
    page = _page(
        [("Signature", (36, 541, 87, 555), 12), ("Witness", (36, 583, 79, 596), 12)],
        [("text", (36, 523, 252, 540)), ("text", (36, 565, 252, 581))],
    )
    assert _link(page) == [("Signature", "below"), ("Witness", "below")]


def test_heading_below_a_box_is_not_its_caption():
    page = _page(
        [("FORM TITLE", (250, 127, 550, 145), 14), ("body", (36, 300, 90, 310), 10),
         ("more", (36, 320, 90, 330), 10)],
        [("text", (300, 100, 400, 115))],
    )
    assert _link(page) == [(None, None)]


def test_choice_box_does_not_take_the_previous_options_text():
    # [ ] Yes   [ ] No : "Yes" belongs to the first box, so the second has no left label.
    page = _page(
        [("Yes", (86, 140, 104, 150), 10), ("No", (136, 140, 150, 150), 10)],
        [("choice", (72, 140, 82, 150)), ("choice", (122, 140, 132, 150))],
    )
    assert _link(page) == [(None, None), (None, None)]


def test_choice_box_takes_its_own_option_text_on_the_left():
    # Yes [ ]   No [ ] : each box's text sits right before it.
    page = _page(
        [("Yes", (72, 140, 90, 150), 10), ("No", (122, 140, 136, 150), 10)],
        [("choice", (94, 140, 104, 150)), ("choice", (140, 140, 150, 150))],
    )
    assert _link(page) == [("Yes", "left"), ("No", "left")]


def test_text_field_after_an_option_keeps_the_option_text_as_label():
    # [ ] Other, specify ________
    page = _page(
        [("Other, specify", (86, 140, 150, 150), 10)],
        [("choice", (72, 140, 82, 150)), ("text", (155, 138, 300, 151))],
    )
    assert _link(page)[1] == ("Other, specify", "left")


def test_option_text_starting_with_a_checkbox_glyph_belongs_to_that_box():
    # A symbol-font checkbox printed as the option's first character, a detected box on it.
    page = _page(
        [("\x89Insomnia", (18, 166, 59, 177), 10), ("\x89Anemia", (162, 166, 198, 177), 10),
         ("\x89Dizziness", (18, 177, 61, 188), 10)],
        [("choice", (18, 168, 27, 175)), ("choice", (162, 168, 171, 175)),
         ("choice", (18, 179, 27, 186))],
    )
    assert _link(page) == [(None, None), (None, None), (None, None)]
    assert [right_text(page, c.rect).text for c in page.candidates] == [
        "\x89Insomnia", "\x89Anemia", "\x89Dizziness",
    ]


def test_right_text_ignores_boxes_on_the_next_row():
    # Stacked underline options: "____Option A" over "____Option B", 11.5pt apart.
    page = _page(
        [("Option A", (76, 455, 242, 468.6), 10), ("Option B", (76, 466.5, 229, 480.1), 10)],
        [("text", (40.8, 455.6, 76.3, 466.3)), ("text", (40.4, 467.2, 76.0, 478.0))],
    )
    assert [right_text(page, c.rect).text for c in page.candidates] == ["Option A", "Option B"]


def test_right_text_stops_at_a_box_in_between():
    page = _page([("Yes", (36, 100, 52, 110), 10)],
                 [("choice", (10, 100, 20, 110)), ("choice", (22, 100, 32, 110))])
    assert [getattr(right_text(page, c.rect), "text", None) for c in page.candidates] == [
        None, "Yes",
    ]


# --- fingerprint -------------------------------------------------------------


def test_fingerprint_is_stable_across_resave(flat_form_pdf, tmp_path):
    out = tmp_path / "resaved.pdf"
    with pymupdf.open(flat_form_pdf) as doc:
        doc.save(out, garbage=4, deflate=True)
    assert out.read_bytes() != flat_form_pdf.read_bytes()
    assert fingerprint(out) == fingerprint(flat_form_pdf)
    assert fingerprint(flat_form_pdf.read_bytes()) == fingerprint(flat_form_pdf)
    assert len(fingerprint(flat_form_pdf)) == 16


def test_fingerprint_changes_with_text_and_size(flat_form_pdf, address_form_pdf, tmp_path):
    assert fingerprint(address_form_pdf) != fingerprint(flat_form_pdf)
    out = tmp_path / "a4.pdf"
    with pymupdf.open(flat_form_pdf) as doc:
        doc[0].set_mediabox(pymupdf.Rect(0, 0, 595, 842))
        doc.save(out)
    assert fingerprint(out) != fingerprint(flat_form_pdf)
