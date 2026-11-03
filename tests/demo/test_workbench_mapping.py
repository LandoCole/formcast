"""The Fill screen's Mapping view (``demo/web/workbench/mapping.js``) in Chromium against the real workbench server.

A two-box form and a three-column table: the scripted model designs a two-field schema, links each box to a field and
matches one field to one column, so the view opens with something matched, a form field with no data and two columns
not used. Every change after that is made by the server from the edited mapping, with no model call.
"""

from __future__ import annotations

import json
import threading

import pymupdf
import pytest
from playwright.sync_api import expect
from test_server_documents import get_json
from test_workbench_page import Bench

from demo.server import documents as documents_mod
from formcast.data.ai import DERIVE_SYSTEM, MAP_SYSTEM
from formcast.llm import LLMResponse, Usage
from formcast.pagemap.build import DetectPolicy

SCHEMA = {
    "title": "Two fields",
    "type": "object",
    "required": ["city"],
    "properties": {"name": {"type": "string", "title": "Full name"}, "city": {"type": "string", "title": "City"}},
}
CSV = "Full Name,Town,Ref\nAnn Lee,Erie,1\nBo Chen,Troy,2\n"


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None, **kw: real(pdf, detectors={}, policy=policy, ocr="never"))


def two_widget_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 30), "Name")
    page.insert_text((20, 90), "City")
    for name, rect in (("name_box", (20, 40, 160, 60)), ("city_box", (20, 100, 160, 120))):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = name
        widget.rect = pymupdf.Rect(rect)
        page.add_widget(widget)
    out = doc.tobytes()
    doc.close()
    return out


class Model:
    """Designs SCHEMA, links the two boxes to its two fields and matches only ``name ← Full Name``; counts its calls."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self):
        return self

    def complete(self, messages, tools, **kwargs):
        system = messages[0].content
        if system == DERIVE_SYSTEM:
            kind, text = "derive", json.dumps({"schema": SCHEMA})
        elif system.startswith(MAP_SYSTEM[:40]):
            rules = [{"path": "name", "column": "Full Name"}]
            kind, text = "map", json.dumps({"rules": rules, "unmapped_columns": ["Town", "Ref"], "notes": ""})
        else:  # the page pass
            mappings = [{"id": "w0_0", "source": {"paths": ["name"]}}, {"id": "w0_1", "source": {"paths": ["city"]}}]
            kind, text = "page", json.dumps({"mappings": mappings, "tables": []})
        with self._lock:
            self.calls[kind] = self.calls.get(kind, 0) + 1
        return LLMResponse(text=text, tool_calls=[], usage=Usage(100, 10), raw={})

    def close(self) -> None:
        pass


@pytest.fixture
def mapped(start_demo_server, page):
    """The form open with the table mapped: (bench, model)."""
    model = Model()
    b = Bench(page, start_demo_server(provider_factory=model))
    b.goto()
    b.page.set_input_files("#file", files=[{"name": "form.pdf", "mimeType": "application/pdf", "buffer": two_widget_pdf()}])
    b.page.fill("#data-text", CSV)
    b.link_by("fast")
    b.page.click("#open")
    b.build_new()
    expect(b.page.locator("#mapping-view")).to_be_visible()
    expect(b.page.locator("#fill-msg")).to_contain_text("Filled")
    yield b, model
    assert b.errors == []


def rows(b: Bench, kind: str):
    return b.page.locator(f'#mapping-view .mp-row[data-kind="{kind}"]')


def record(b: Bench) -> dict:
    return json.loads(b.page.input_value("#fill-sample"))


def counts(b: Bench):
    return b.page.locator("#map-counts")


def test_the_mapping_view_opens_with_what_was_matched_and_what_is_left(mapped):
    b, model = mapped
    expect(b.page.locator("#left-switch")).to_be_visible()
    expect(b.page.locator("#show-mapping")).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#fields-view")).to_be_hidden()
    expect(counts(b)).to_have_text("1 matched (0 confirmed) · 1 form field with no data · 2 columns not used")
    matched = rows(b, "matched")
    expect(matched).to_have_count(1)
    expect(matched.locator(".mp-title")).to_have_text("Full name")
    expect(matched.locator(".mp-src")).to_have_text("Full Name")
    expect(matched.locator(".mp-tag")).to_have_text("AI")
    expect(matched.locator(".mp-value")).to_have_text("Ann Lee")
    missing = rows(b, "missing")
    expect(missing.locator(".mp-title")).to_have_text(["City"])
    expect(missing.locator(".mp-req")).to_have_text(["required"])
    expect(rows(b, "unused").locator(".mp-title")).to_have_text(["Town", "Ref"])
    expect(rows(b, "unused").first.locator(".mp-value")).to_contain_text("Erie")
    expect(b.page.locator("#mapping-line")).to_be_visible()  # the drawer points here, not at a table of rules
    expect(b.page.locator("#mapping-box")).to_be_hidden()
    b.page.click("#show-fields")
    expect(b.page.locator("#schemalist")).to_be_visible()
    b.page.click("#mapping-review")
    expect(b.page.locator("#mapping-view")).to_be_visible()
    assert model.calls == {"derive": 1, "page": 1, "map": 1}


def test_confirming_connecting_changing_removing_and_undo_remake_the_records(mapped):
    b, model = mapped
    rows(b, "matched").locator(".mp-tick").click()
    expect(counts(b)).to_contain_text("1 matched (1 confirmed)")
    expect(rows(b, "matched").locator(".mp-tick")).to_have_attribute("aria-pressed", "true")

    rows(b, "unused").first.locator(".mp-main").click()  # Town
    b.page.locator('#map-unused .mp-row[data-column="Town"] select').select_option("city")
    expect(counts(b)).to_have_text("2 matched (1 confirmed) · 0 form fields with no data · 1 column not used")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Erie'")
    city = b.page.locator('#map-matched .mp-row[data-path="city"]')
    expect(city.locator(".mp-tag")).to_have_text("you")
    expect(city.locator(".mp-value")).to_have_text("Erie")
    expect(b.page.locator("#fill-msg")).to_contain_text("Filled 2 fields")  # the PDF on show was filled again

    name = b.page.locator('#map-matched .mp-row[data-path="name"]')
    name.locator(".mp-main").click()
    name.locator("select").select_option("Ref")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).name === '1'")
    expect(name.locator(".mp-tag")).to_have_text("you")
    expect(name.locator(".mp-tick")).to_have_attribute("aria-pressed", "false")  # a change is not yet checked

    # the row stays open after the change
    b.page.locator('#map-matched .mp-row[data-path="name"] .mp-more .btn', has_text="Remove").click()
    expect(rows(b, "missing").locator(".mp-title")).to_have_text(["Full name"])
    b.page.wait_for_function("!('name' in JSON.parse(document.getElementById('fill-sample').value))")

    b.page.click("#map-undo")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).name === '1'")
    expect(rows(b, "matched")).to_have_count(2)
    assert model.calls == {"derive": 1, "page": 1, "map": 1}  # no model call for any of it


def test_a_typed_value_fills_this_record_and_every_record_makes_it_a_match(mapped):
    b, _ = mapped
    city = b.page.locator('#map-missing .mp-row[data-path="city"]')
    city.locator(".mp-main").click()
    city.locator("input").fill("Paris")
    city.get_by_role("button", name="This record").click()
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Paris'")
    expect(city.locator(".mp-value")).to_have_text("typed for this record: Paris")
    b.page.select_option("#fill-record", "1")
    b.page.wait_for_function("!('city' in JSON.parse(document.getElementById('fill-sample').value))")
    b.page.select_option("#fill-record", "0")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Paris'")

    city = b.page.locator('#map-missing .mp-row[data-path="city"]')
    if city.locator("input").count() == 0:
        city.locator(".mp-main").click()
    city.locator("input").fill("Rome")
    city.get_by_role("button", name="Every record").click()
    matched = b.page.locator('#map-matched .mp-row[data-path="city"]')
    expect(matched.locator(".mp-src")).to_have_text("“Rome”")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Rome'")
    b.page.select_option("#fill-record", "1")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Rome'")


def test_a_value_typed_for_a_field_goes_once_a_column_sets_it(mapped):
    b, _ = mapped
    city = b.page.locator('#map-missing .mp-row[data-path="city"]')
    city.locator(".mp-main").click()
    city.locator("input").fill("Paris")
    city.get_by_role("button", name="This record").click()
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Paris'")
    b.page.locator('#map-missing .mp-row[data-path="city"] select').select_option("Town")
    b.page.wait_for_function("JSON.parse(document.getElementById('fill-sample').value).city === 'Erie'")
    expect(b.page.locator('#map-matched .mp-row[data-path="city"] .mp-value')).to_have_text("Erie")


def test_picking_a_form_field_selects_its_box(mapped):
    b, _ = mapped
    b.page.click("#fill-clear")  # back to the boxes
    b.page.locator('#map-missing .mp-row[data-path="city"] .mp-main').click()
    field = next(f for f in b.template()["fields"] if "city" in (f.get("source") or {}).get("paths", []))
    expect(b.page.locator(f'#pageview .fc-box[data-field-id="{field["id"]}"]')).to_have_class(__import__("re").compile("is-selected"))


def test_save_keeps_the_edited_mapping_and_the_same_columns_reopen_with_it(mapped):
    b, _ = mapped
    rows(b, "matched").locator(".mp-tick").click()
    expect(counts(b)).to_contain_text("(1 confirmed)")
    rows(b, "unused").first.locator(".mp-main").click()
    b.page.locator('#map-unused .mp-row[data-column="Town"] select').select_option("city")
    expect(counts(b)).to_contain_text("2 matched")
    b.idle()
    b.page.wait_for_function("!document.getElementById('map-counts').textContent.includes('updating')")
    b.page.click("#save")
    expect(b.page.locator("#save-what")).to_contain_text("what you confirmed and changed")
    b.page.fill("#save-name", "two")
    b.page.click("#save-go")
    expect(b.page.locator("#notice")).to_contain_text("/api/templates/two/fill")
    status, out = get_json(b.base, "/api/templates/two/mappings")
    assert status == 200
    rules = {r["path"]: r for r in out["mappings"][0]["rules"]}
    assert rules["name"].get("confirmed") is True and "manual" not in rules["name"]
    assert rules["city"] == {"path": "city", "column": "Town", "transforms": [], "manual": True}
