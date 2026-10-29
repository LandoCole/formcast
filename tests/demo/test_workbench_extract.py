"""The workbench page's way in to extraction (``demo/web/workbench/``) in Chromium against the real workbench server.

The page opens on "What would you like to do?" (``#choose``): *Fill a PDF* opens the existing start screen as it is,
*Extract from PDFs* switches the page to its extraction mode (``body[data-mode]``), shown only when the server says
it can extract. The setup screen and the review (Tasks 7-9) come later in this file. The server runs in-process over
a throwaway copy of ``demo/data`` (conftest ``start_demo_server``), with form maps built without detectors, as in
``test_workbench_page``, whose ``Bench`` drives the page here too.
"""

from __future__ import annotations

from playwright.sync_api import expect
from test_server_documents import get_json
from test_workbench_page import Bench, bench, no_model_loads, widget_pdf  # noqa: F401 (bench and no_model_loads are fixtures)

from demo.server.api import Api


def test_the_workbench_opens_on_the_choice_of_fill_or_extract(demo_server, bench):
    b = bench(demo_server, fill=False)
    choose = b.page.locator("#choose")
    expect(choose).to_be_visible()
    expect(b.page.locator("#choose-title")).to_have_text("What would you like to do?")
    expect(choose.locator(".choice .choice-t")).to_have_text(["Fill a PDF", "Extract from PDFs"])
    expect(b.page.locator("#start")).to_be_hidden()
    assert b.page.evaluate("document.body.dataset.mode") == "fill"


def test_fill_a_pdf_opens_the_start_screen_and_the_fill_flow_works_from_it(demo_server, bench):
    b = bench(demo_server, fill=False)
    b.page.click('.choice[data-go="fill"]')
    expect(b.page.locator("#choose")).to_be_hidden()
    expect(b.page.locator("#start")).to_be_visible()
    expect(b.page.locator("#start-title")).to_have_text("Fill a form")
    b.upload(widget_pdf())
    b.build_new()
    expect(b.boxes()).to_have_count(1)

    b.page.click("#doc-name")  # the toolbar's form name goes back to the choice
    expect(b.page.locator("#choose")).to_be_visible()
    b.page.click('.choice[data-go="fill"]')
    expect(b.page.locator("#start")).to_be_visible()
    b.page.click("#start-close")  # a form is open: Back returns to it
    expect(b.page.locator("#start")).to_be_hidden()
    expect(b.page.locator("#choose")).to_be_hidden()
    expect(b.boxes()).to_have_count(1)


def test_back_on_the_start_screen_returns_to_the_choice_when_no_form_is_open(demo_server, bench):
    b = bench(demo_server)
    expect(b.page.locator("#start-close")).to_be_visible()
    b.page.click("#start-close")
    expect(b.page.locator("#start")).to_be_hidden()
    expect(b.page.locator("#choose")).to_be_visible()


def test_extract_from_pdfs_switches_the_page_to_extraction_and_its_toolbar_goes_back(demo_server, bench):
    b = bench(demo_server, fill=False)
    b.page.click('.choice[data-go="extract"]')
    expect(b.page.locator("#choose")).to_be_hidden()
    assert b.page.evaluate("document.body.dataset.mode") == "extract"
    expect(b.page.locator("#bench")).to_be_hidden()  # the fill screen's toolbar and columns go away
    expect(b.page.locator("#doc-name")).to_be_hidden()
    expect(b.page.locator("#doc-pick")).to_be_visible()
    b.page.click("#doc-pick")
    expect(b.page.locator("#choose")).to_be_visible()
    b.page.click('.choice[data-go="fill"]')
    assert b.page.evaluate("document.body.dataset.mode") == "fill"
    expect(b.page.locator("#start")).to_be_visible()


def test_show_start_with_a_dataset_offers_it_as_your_data_and_open_uses_it_without_an_upload(demo_server, bench):
    status, dataset = get_json(demo_server, "/api/data", "POST", {"text": "name,tin\nAda Lee,84-2219037\n"})
    assert status == 200
    b = bench(demo_server)
    b.page.evaluate("d => window.__workbench.showStart({dataset: d, form: 'fw9'})", dataset)
    given = b.page.locator("#data-given")
    expect(given).to_be_visible()
    expect(given).to_contain_text(dataset["name"])
    expect(b.page.locator("#data-file-row")).to_be_hidden()
    expect(b.page.locator("#data-text")).to_be_hidden()
    expect(b.page.locator("#form-pick")).to_have_value("saved:fw9")  # the form named: its saved template first
    expect(b.page.locator("#open")).to_have_text("Open and fill")

    b.page.click("#data-given-clear")  # Clear: the file and paste box are back
    expect(given).to_be_hidden()
    expect(b.page.locator("#data-file-row")).to_be_visible()

    b.page.evaluate("d => window.__workbench.showStart({dataset: d})", dataset)
    b.page.select_option("#form-pick", "demo:fw9")
    uploads = []
    b.page.on("request", lambda r: uploads.append(r.url) if r.url.endswith("/api/data") else None)
    b.page.click("#open")
    expect(b.page.locator("#match")).to_be_visible()  # fw9 is stored: the match prompt, with the data kept for it
    assert uploads == []


def test_a_server_that_cannot_extract_offers_only_fill(start_demo_server, page, monkeypatch):
    real = Api.capabilities

    def no_extract(self, req):
        caps = real(self, req)
        caps.pop("extract")
        return caps

    monkeypatch.setattr(Api, "capabilities", no_extract)
    b = Bench(page, start_demo_server())
    b.goto(fill=False)
    expect(page.locator('.choice[data-go="fill"]')).to_be_visible()
    expect(page.locator('.choice[data-go="extract"]')).to_be_hidden()
    assert b.errors == []
