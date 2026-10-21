# Markers `slow` and `llm` are registered in pyproject.toml; pytest's addopts
# deselects them by default. Opt in with `-m slow` or `-m llm`.

from pathlib import Path

import pymupdf
import pytest

# Fixture PDF builders. Each returns the path of a one-page Letter PDF written to
# tmp_path. Geometry (points, origin top-left, unrotated page space):
#   "Name:" baseline y=100 at x=72, horizontal line (120,100)-(300,100)
#   10x10 box (72,140,82,150) with "Yes" at x=88
#   "Date" baseline y=200 at x=72, horizontal line (72,212)-(250,212)
PAGE_W, PAGE_H = 612, 792


def _draw_flat_form(page: pymupdf.Page) -> None:
    page.insert_text((72, 100), "Name:", fontsize=12)
    page.draw_line((120, 100), (300, 100), width=0.75)
    page.draw_rect(pymupdf.Rect(72, 140, 82, 150), width=0.75)
    page.insert_text((88, 149), "Yes", fontsize=11)
    page.insert_text((72, 200), "Date", fontsize=12)
    page.draw_line((72, 212), (250, 212), width=0.75)


def _new_flat_form() -> pymupdf.Document:
    doc = pymupdf.open()
    _draw_flat_form(doc.new_page(width=PAGE_W, height=PAGE_H))
    return doc


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def flat_form_pdf(tmp_path: Path) -> Path:
    return _save(_new_flat_form(), tmp_path / "flat_form.pdf")


@pytest.fixture
def fillable_form_pdf(tmp_path: Path) -> Path:
    doc = _new_flat_form()
    page = doc[0]
    text = pymupdf.Widget()
    text.field_name = "applicant_name"
    text.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    text.rect = pymupdf.Rect(120, 86, 300, 100)
    page.add_widget(text)
    box = pymupdf.Widget()
    box.field_name = "agree"
    box.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    box.rect = pymupdf.Rect(72, 140, 82, 150)
    page.add_widget(box)
    return _save(doc, tmp_path / "fillable_form.pdf")


@pytest.fixture
def rotated_form_pdf(tmp_path: Path) -> Path:
    doc = _new_flat_form()
    doc[0].set_rotation(90)
    return _save(doc, tmp_path / "rotated_form.pdf")


@pytest.fixture
def cropped_form_pdf(tmp_path: Path) -> Path:
    doc = _new_flat_form()
    doc[0].set_cropbox(pymupdf.Rect(36, 36, PAGE_W - 36, PAGE_H - 36))
    return _save(doc, tmp_path / "cropped_form.pdf")


@pytest.fixture
def image_only_pdf(tmp_path: Path) -> Path:
    src = _new_flat_form()
    png = src[0].get_pixmap(dpi=150).tobytes("png")
    src.close()
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=png)
    return _save(doc, tmp_path / "image_only.pdf")
