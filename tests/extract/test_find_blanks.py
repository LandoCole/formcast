"""Finding the blanks beside a page's form fields: the detector runs on a fillable page only when asked, a blank
over a form field is dropped, and every page searched is reported."""

from __future__ import annotations

import pymupdf

from formcast.extract.samples import SamplePdf, _detect, _read
from formcast.pagemap.build import over_widget
from formcast.pagemap.detect import DetectPolicy


class FakeDetector:
    """Finds the same blanks (normalized to the page) on every page."""

    name = "fake"

    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, image):
        return list(self.boxes)


def _mixed() -> bytes:
    """One letter-size page: a form field in its top half and a printed blank line, with no field, under it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Name", fontsize=10)
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.field_value, w.rect = "Name", pymupdf.PDF_WIDGET_TYPE_TEXT, "Ann", pymupdf.Rect(120, 88, 300, 104)
    page.add_widget(w)
    page.insert_text((72, 400), "Phone ____________________", fontsize=10)
    return doc.tobytes()


# one blank drawn around the form field, one on the printed line
BOXES = [("text", (120 / 612, 86 / 792, 302 / 612, 106 / 792), 0.9), ("text", (110 / 612, 388 / 792, 240 / 612, 404 / 792), 0.8)]
POLICY = DetectPolicy(digital=("fake",), scanned=("fake",))


def test_a_blank_is_over_a_form_field_when_they_share_half_the_smaller():
    assert over_widget((120, 86, 302, 106), (120, 88, 300, 104))
    assert over_widget((60, 80, 400, 110), (120, 88, 300, 104))  # a long line the field fills part of
    assert not over_widget((110, 388, 240, 404), (120, 88, 300, 104))
    assert not over_widget((290, 88, 340, 104), (120, 88, 300, 104))  # touching its end


def test_a_fillable_page_is_searched_only_beside_its_form_fields_when_asked():
    docs = _read([SamplePdf("m.pdf", _mixed())], ocr="never", detectors={"fake": FakeDetector(BOXES)}, policy=POLICY)
    assert docs[0].form_map.pages[0].kind == "fillable"
    plain = _detect(docs, detect="all", detectors={"fake": FakeDetector(BOXES)}, policy=POLICY)
    assert plain[0].form_map.pages[0].candidates == []
    events = []
    beside = _detect(docs, detect="all", detectors={"fake": FakeDetector(BOXES)}, policy=POLICY, find="all", on_scan=events.append)
    [blank] = beside[0].form_map.pages[0].candidates  # the one drawn around the form field is dropped
    assert blank.id == "c0_0" and blank.rect[1] > 380
    assert [(e.page, e.step, e.found) for e in events] == [(-1, "start", 0), (0, "detect", 0), (0, "done", 1)]
    assert events[0].wanted == (0,) and events[0].kinds == ("fillable",)
