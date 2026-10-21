"""Rendering: fill a PDF from a template and a record, smart PDFs, and preview images."""

from formcast.render.fill import fill, to_smart_pdf
from formcast.render.preview import preview
from formcast.render.report import FillReport

__all__ = ["FillReport", "fill", "preview", "to_smart_pdf"]
