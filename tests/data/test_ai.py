"""formcast.data.ai: designing a form's schema, mapping a table and reading free text, against a scripted model."""

from __future__ import annotations

import json

import pymupdf
import pytest

from formcast.data import Table, derive_schema, extract_records, map_table
from formcast.errors import FormcastError
from formcast.llm import ImagePart, LLMResponse, TextPart, Usage
from formcast.pagemap.build import DetectPolicy, build_form_map

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "dob": {"type": "string", "format": "date"}}}


class Scripted:
    """Answers each call with the next text; records what it was sent."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list] = []

    def complete(self, messages, tools, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(text=self.answers.pop(0), tool_calls=[], usage=Usage(10, 5), raw={})

    def close(self) -> None:
        pass


@pytest.fixture
def form(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 40), "Name:")
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.rect = "name", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(70, 28, 250, 44)
    page.add_widget(w)
    pdf = doc.tobytes()
    doc.close()
    return pdf, build_form_map(pdf, detectors={}, policy=DetectPolicy(digital=(), scanned=()), ocr="never")


def test_derive_schema_sends_the_page_image_and_map_and_returns_the_schema(form):
    pdf, fm = form
    llm = Scripted(json.dumps({"schema": {"title": "Tiny", "type": "object", "properties": {"name": {"type": "string"}}}}))
    result = derive_schema(pdf, llm, form_map=fm)
    assert result.schema["title"] == "Tiny"
    assert result.usage == Usage(10, 5)
    parts = llm.calls[0][1].content
    assert any(isinstance(p, ImagePart) for p in parts)
    assert any(isinstance(p, TextPart) and "PAGE MAP" in p.text and "Name" in p.text for p in parts)


def test_derive_schema_without_vision_sends_no_image(form):
    pdf, fm = form
    llm = Scripted('{"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}')
    derive_schema(pdf, llm, form_map=fm, vision=False)
    assert not any(isinstance(p, ImagePart) for p in llm.calls[0][1].content)


def test_a_bad_answer_is_asked_for_again_once_then_refused(form):
    pdf, fm = form
    llm = Scripted("no json here", '{"schema": {"type": "object", "properties": {"x": {"type": "string"}}}}')
    assert derive_schema(pdf, llm, form_map=fm).usage == Usage(20, 10)
    assert "can't be used" in llm.calls[1][-1].content
    with pytest.raises(FormcastError, match="could not be used"):
        derive_schema(pdf, Scripted('{"schema": {"type": "object", "properties": {}}}', "{}"), form_map=fm)


def test_map_table_sends_columns_and_samples_not_rows_and_checks_the_rules():
    table = Table(["Full Name", "Birth", "Secret"], [{"Full Name": f"Person {i}", "Birth": "01/02/1990", "Secret": "s3cr3t-row-9"} for i in range(10)])
    answer = {
        "rules": [
            {"path": "name", "column": "Full Name"},
            {"path": "dob", "column": "Birth", "transforms": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]},
            {"path": "nope", "column": "Birth"},
            {"path": "name"},
        ],
        "unmapped_columns": ["Secret"],
        "notes": "fine",
    }
    llm = Scripted(json.dumps(answer))
    result = map_table(SCHEMA, table, llm)
    assert [r.path for r in result.mapping.rules] == ["name", "dob"]
    assert result.mapping.notes == "fine"
    assert result.mapping.unmapped_columns == ["Secret"]
    assert len(result.warnings) == 2
    sent = llm.calls[0][1].content
    assert "Person 9" not in sent and "Person 4" in sent  # five samples per column, not every row


def test_extract_records_returns_the_records_the_text_gives():
    llm = Scripted('{"records": [{"name": "Ann"}, {"name": "Bo"}], "notes": "two people"}')
    result = extract_records(SCHEMA, "Ann and Bo both apply.", llm)
    assert result.records == [{"name": "Ann"}, {"name": "Bo"}]
    assert result.notes == "two people"
    assert "Ann and Bo both apply." in llm.calls[0][1].content
    with pytest.raises(FormcastError):
        extract_records(SCHEMA, "   ", llm)


def test_a_long_agreement_shows_the_model_its_form_pages_not_its_prose():
    """40 pages of legal prose, then one page with a blank: the image and full text are the last page's."""
    doc = pymupdf.open()
    for i in range(40):
        page = doc.new_page(width=300, height=400)
        page.insert_textbox(pymupdf.Rect(20, 20, 280, 380), f"Section {i}. " + "The Subscriber agrees to the terms. " * 40, fontsize=7)
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 40), "Commitment amount:")
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.rect = "amount", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(130, 28, 280, 44)
    page.add_widget(w)
    pdf = doc.tobytes()
    doc.close()
    fm = build_form_map(pdf, detectors={}, policy=DetectPolicy(digital=(), scanned=()), ocr="never")
    llm = Scripted('{"schema": {"type": "object", "properties": {"commitment_amount": {"type": "number"}}}}')
    derive_schema(pdf, llm, form_map=fm)
    parts = llm.calls[0][1].content
    assert [p.text for p in parts if isinstance(p, TextPart) and p.text.startswith("Page ")] == ["Page 41:"]
    assert sum(isinstance(p, ImagePart) for p in parts) == 1
    text = next(p.text for p in parts if isinstance(p, TextPart) and p.text.startswith("PAGE MAP"))
    assert "Commitment amount" in text and "Blanks were found on page(s) 41" in text
    assert len(text) < 40 * 700  # the prose pages are excerpts
