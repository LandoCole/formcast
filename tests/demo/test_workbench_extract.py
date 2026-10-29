"""The workbench page's way in to extraction (``demo/web/workbench/``) in Chromium against the real workbench server.

The page opens on "What would you like to do?" (``#choose``): *Fill a PDF* opens the existing start screen as it is,
*Extract from PDFs* switches the page to its extraction mode (``body[data-mode]``), shown only when the server says
it can extract. The Extract modal (``#extract``) takes the batch (files chosen or dropped, or a folder on the
server's machine), the sample, what to extract and how to read it; *Read the sample* builds the template in the
building panel (``#building``) and hands the setup on (``extract:setup`` on the bus) to the setup screen
(``.view[data-only=setup]``: the fields and their status across the samples, a sample page with the readers' boxes, the
field's inspector and its results on each sample), where every change is an edit of the setup on the server. The
review (Task 9) comes later in this file. The server runs in-process over a throwaway copy of ``demo/data`` (conftest
``start_demo_server``), with form maps built without detectors, as in ``test_workbench_page``, whose ``Bench`` drives
the page here too; a template is built from the five corpus samples of ``test_server_extract`` by ``CorpusModel``
(gated there as ``GatedModel``), with no detector weights loaded.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import threading
import zipfile

import pytest
from playwright.sync_api import expect
from test_server_documents import get_json, small_pdf
from test_server_extract import (
    SHIFTED, GatedModel, add_files, assert_proven, job_ended, new_batch, sample_set, start_job, zip_of,
)
from test_server_jobs import wait_for
from test_workbench_page import Bench, bench, no_model_loads, widget_pdf  # noqa: F401 (bench and no_model_loads are fixtures)

from demo.server import extraction
from demo.server.api import Api
from formcast.extract.setup import STAGES
from tests.extract.corpus import FIELD_RECTS, SHIFT, clients


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
    expect(b.page.locator("#extract")).to_be_visible()
    assert b.page.evaluate("document.body.dataset.mode") == "extract"
    expect(b.page.locator("#bench")).to_be_hidden()  # the fill screen's toolbar and columns go away
    expect(b.page.locator("#doc-name")).to_be_hidden()
    expect(b.page.locator("#doc-pick")).to_be_visible()
    b.page.evaluate("document.getElementById('extract').hidden = true")  # closed, as it is once a setup opens
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


# ------------------------------------------------------------------ the Extract modal and building the template

#: The five samples of the server setup tests (``test_server_extract.sample_set``): three fillable, two flattened.
PEOPLE = clients(5, seed=31)
FILES = sample_set(PEOPLE)


@pytest.fixture(autouse=True)
def setup_detectors_off(extract_detectors_off):
    """A setup's samples are read with no detector weights (conftest ``extract_detectors_off``)."""


def corpus_model(**kw) -> GatedModel:
    """The corpus model for the five ``FILES`` (``CorpusModel``, gated as ``test_server_extract.GatedModel`` is)."""
    return GatedModel({name: client for (name, _), client in zip(FILES, PEOPLE)}, **kw)


def extract_server(start_demo_server, model: GatedModel | None = None, **settings) -> str:
    """A server whose AI is the corpus model, reading runs in this process."""
    return start_demo_server(provider_factory=model or corpus_model(), extract_workers=1, **settings)


def pdf_files(files: list[tuple[str, bytes]], mime: str = "application/pdf") -> list[dict]:
    return [{"name": name, "mimeType": mime, "buffer": data} for name, data in files]


def stored_zip(entries: list[tuple[str, bytes]]) -> bytes:
    """A zip of ``entries`` with no compression, so it is as large as they are."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def open_extract(b: Bench) -> None:
    """From the choice, Extract from PDFs: the Extract modal."""
    b.page.click('.choice[data-go="extract"]')
    expect(b.page.locator("#extract")).to_be_visible()


def add_five(b: Bench) -> None:
    """Choose the five sample PDFs and wait for the batch's scan."""
    b.page.set_input_files("#drop-files", files=pdf_files(FILES))
    expect(b.page.locator("#batch-sum")).to_contain_text("5 fillable or flattened copies of one layout")


def data_transfer(b: Bench, files: list[tuple[str, bytes]], mime: str = "application/pdf"):
    """A DataTransfer holding ``files``, for a drop event."""
    return b.page.evaluate_handle(
        """([files, mime]) => {
            const dt = new DataTransfer();
            for (const [name, b64] of files) {
                const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
                dt.items.add(new File([bytes], name, { type: mime }));
            }
            return dt;
        }""",
        [[[name, base64.b64encode(data).decode()] for name, data in files], mime],
    )


def listen_for_setups(b: Bench) -> None:
    """Keep every setup the bus hears (``extract:setup``) in ``window.__setups``."""
    b.page.evaluate("() => { window.__setups = []; window.__workbench.on('extract:setup', ({ setup }) => window.__setups.push(setup)); }")


def test_extract_from_pdfs_opens_the_extract_modal_without_the_fill_notes_and_back_returns_to_the_choice(demo_server, bench):
    b = bench(demo_server, fill=False)
    expect(b.page.locator("#ai-note")).to_be_visible()  # AI is off: the page says how to turn it on, for filling
    open_extract(b)
    expect(b.page.locator("#extract-title")).to_have_text("Extract from PDFs")
    expect(b.page.locator("#extract .group > .label")).to_have_text(
        ["1 · Your documents", "2 · The sample", "3 · What to extract", "4 · Detection and reading · automatic, prose skipped"]
    )
    expect(b.page.locator("#drop")).to_contain_text("Drop PDFs or a .zip here")
    expect(b.page.locator("#drop-note")).to_have_text(
        "Zips up to 50 MB; for a bigger batch choose the files or a folder, or type a folder on this machine."
    )
    assert b.page.eval_on_selector("#drop-files", "i => i.multiple") is True
    assert b.page.eval_on_selector("#drop-folder", "i => i.webkitdirectory") is True
    expect(b.page.locator("#batch")).to_be_hidden()
    expect(b.page.locator("#n-sample")).to_have_value("5")
    expect(b.page.locator("#sample-how")).to_have_value("spread")
    expect(b.page.locator('#extract .seg [data-mode="ai"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#settings-sum")).to_have_text("automatic, prose skipped")
    expect(b.page.locator("#unsure")).to_be_checked()
    expect(b.page.locator("#unsure")).to_be_disabled()
    expect(b.page.locator("#extract-settings")).to_contain_text("Always on in this version")
    expect(b.page.locator("#ai-fallback")).not_to_be_checked()
    expect(b.page.locator("#ai-note")).to_be_hidden()  # fill copy is not shown while extracting

    b.page.click("#extract-back")
    expect(b.page.locator("#extract")).to_be_hidden()
    expect(b.page.locator("#choose")).to_be_visible()


def test_choosing_five_pdfs_shows_the_batch_summary_and_a_strip_cell_for_each_after_the_scan(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    add_five(b)
    expect(b.page.locator("#batch-sum")).to_contain_text("5 PDFs")
    expect(b.page.locator("#batch-strip li")).to_have_count(5)
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(5)  # five of five: all in the sample
    expect(b.page.locator("#batch-note")).to_contain_text("One layout fits 5 of them (3 fillable, 2 flattened)")


def test_dropping_pdfs_and_then_a_zip_puts_them_in_one_batch(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    b.page.dispatch_event("#drop", "drop", {"dataTransfer": data_transfer(b, FILES[:3])})
    expect(b.page.locator("#batch-sum")).to_contain_text("3 PDFs")
    archive = zip_of(FILES[3:])
    b.page.dispatch_event("#drop", "drop", {"dataTransfer": data_transfer(b, [("more.zip", archive)], "application/zip")})
    expect(b.page.locator("#batch-sum")).to_contain_text("5 fillable or flattened copies of one layout")
    expect(b.page.locator("#batch-sum")).to_contain_text("5 PDFs")
    assert len(start_demo_server.servers[-1].api.batches._batches) == 1


def test_the_sample_size_must_be_3_to_20(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    add_five(b)
    b.page.fill("#n-sample", "2")
    b.page.dispatch_event("#n-sample", "change")
    expect(b.page.locator("#extract-msg")).to_be_visible()
    expect(b.page.locator("#extract-msg")).to_contain_text("3 to 20")
    b.page.click("#read-sample")
    expect(b.page.locator("#extract-msg")).to_contain_text("3 to 20")
    expect(b.page.locator("#building")).to_be_hidden()
    b.page.fill("#n-sample", "3")
    b.page.dispatch_event("#n-sample", "change")
    expect(b.page.locator("#extract-msg")).to_be_hidden()
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(3)


def test_ill_pick_them_lists_the_batchs_files_with_checkboxes(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    expect(b.page.locator("#picklist")).to_be_hidden()
    add_five(b)
    b.page.select_option("#sample-how", "pick")
    rows = b.page.locator("#picklist li")
    expect(rows).to_have_count(5)
    expect(rows.locator('input[type="checkbox"]')).to_have_count(5)
    expect(rows).to_contain_text([name for name, _ in FILES])
    expect(rows.locator("input:checked")).to_have_count(5)  # the sample picked so far
    expect(b.page.locator("#pick-more")).to_be_hidden()
    rows.nth(1).locator("input").uncheck()
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(4)


def test_the_pick_list_shows_200_files_at_a_time_and_more_shows_the_rest(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    archive = zip_of([(f"f{i:03d}.pdf", small_pdf(text=f"file {i}")) for i in range(201)])
    b.page.set_input_files("#drop-files", files=pdf_files([("many.zip", archive)], "application/zip"))
    expect(b.page.locator("#batch-sum")).to_contain_text("201 PDFs")
    b.page.select_option("#sample-how", "pick")
    expect(b.page.locator("#picklist li")).to_have_count(200)
    expect(b.page.locator("#pick-more")).to_be_visible()
    b.page.click("#pick-more")
    expect(b.page.locator("#picklist li")).to_have_count(201)
    expect(b.page.locator("#picklist li").last).to_contain_text("f200.pdf")
    expect(b.page.locator("#pick-more")).to_be_hidden()


def test_what_to_extract_offers_your_own_lines_and_the_shipped_fill_templates_as_a_destination(demo_server, bench):
    b = bench(demo_server, fill=False)
    open_extract(b)
    expect(b.page.locator("#mode-detail")).to_contain_text("The AI reads the sample and proposes the fields it finds")
    b.page.click('#extract .seg [data-mode="user"]')
    expect(b.page.locator('#extract .seg [data-mode="user"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator('#extract .seg [data-mode="ai"]')).to_have_attribute("aria-pressed", "false")
    expect(b.page.locator("#lines")).to_be_visible()
    b.page.fill("#lines", "investor name\ntax ID")
    b.page.click('#extract .seg [data-mode="dest"]')
    expect(b.page.locator("#lines")).to_be_hidden()
    expect(b.page.locator("#dest-pick")).to_be_visible()
    expect(b.page.locator("#dest-pick option[value='fw9']")).to_have_count(1)
    values = b.page.eval_on_selector_all("#dest-pick option", "os => os.map(o => o.value)")
    assert {"fw9", "menomonie_app", "wadoc_auth"} <= set(values)
    b.page.click('#extract .seg [data-mode="user"]')
    expect(b.page.locator("#lines")).to_have_value("investor name\ntax ID")  # kept while another mode was shown


def test_read_the_sample_with_no_documents_says_to_add_them_first(demo_server, bench):
    b = bench(demo_server, fill=False)
    open_extract(b)
    b.page.click("#read-sample")
    expect(b.page.locator("#extract-msg")).to_be_visible()
    expect(b.page.locator("#extract-msg")).to_contain_text("Add the documents first")
    expect(b.page.locator("#building")).to_be_hidden()


def test_read_the_sample_builds_the_template_in_five_stages_shown_live_and_then_hands_on_the_setup(start_demo_server, bench):
    model = corpus_model(gate="derive")
    b = bench(extract_server(start_demo_server, model), fill=False)
    open_extract(b)
    add_five(b)
    listen_for_setups(b)
    stages = b.page.locator("#build-stages li")
    try:
        b.page.click("#read-sample")
        expect(b.page.locator("#building")).to_be_visible()
        expect(b.page.locator("#extract")).to_be_hidden()
        expect(b.page.locator("#building-title")).to_have_text("Building the template")
        assert model.reached.wait(20)
        expect(stages).to_have_count(5)
        expect(stages.nth(2)).to_have_attribute("data-status", "running")  # designing the fields, held at the model
        expect(stages.nth(0)).to_have_attribute("data-status", "done")
        expect(stages.nth(4)).to_have_attribute("data-status", "pending")
        expect(b.page.locator("#build-readout")).to_contain_text("Design the fields")
    finally:
        model.released.set()
    b.page.wait_for_function("window.__setups.length === 1", timeout=60_000)
    expect(b.page.locator("#building")).to_be_hidden()
    expect(stages).to_contain_text([label for _, label in STAGES])  # each with its detail ("5 samples read", ...)
    assert [stages.nth(i).get_attribute("data-status") for i in range(5)] == ["done"] * 5
    setup = b.page.evaluate("window.__setups[0]")
    assert len(setup["samples"]) == 5
    assert_proven(setup["proof"])  # every field some sample has is ready, and no sample disagrees
    info = setup["template"]["setup"]
    assert (info["start"], info["detect"], info["ocr"], info["sample_how"]) == ("ai", "auto", "auto", "spread")


def test_the_settings_and_your_lines_are_what_the_template_is_built_with(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    add_five(b)
    listen_for_setups(b)
    b.page.select_option("#sample-how", "first")
    b.page.click('#extract .seg [data-mode="user"]')
    b.page.fill("#lines", "investor legal name\n\ntaxpayer ID\n")
    b.page.evaluate("document.getElementById('extract-settings').open = true")
    b.page.select_option("#detect", "off")
    expect(b.page.locator("#settings-sum")).to_have_text("form fields and text only")
    b.page.select_option("#ocr", "never")
    b.page.click("#read-sample")
    b.page.wait_for_function("window.__setups.length === 1", timeout=60_000)
    info = b.page.evaluate("window.__setups[0].template.setup")
    assert info["start"] == "lines" and info["lines"] == ["investor legal name", "taxpayer ID"]
    assert (info["detect"], info["ocr"], info["sample_how"]) == ("off", "never", "first")
    expect(b.page.locator("#build-stages li").nth(1)).to_have_attribute("data-status", "skipped")


def test_cancel_while_building_stops_the_job_and_returns_to_the_extract_modal(start_demo_server, bench):
    model = corpus_model(gate="derive")
    base = extract_server(start_demo_server, model)
    b = bench(base, fill=False)
    open_extract(b)
    add_five(b)
    listen_for_setups(b)
    try:
        with b.page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as started:
            b.page.click("#read-sample")
        job_id = started.value.json()["id"]
        assert model.reached.wait(20)
        b.page.click("#build-cancel")
        expect(b.page.locator("#building")).to_be_hidden()
        expect(b.page.locator("#extract")).to_be_visible()
    finally:
        model.released.set()
    job = wait_for(lambda: (j := get_json(base, f"/api/jobs/{job_id}")[1])["status"] != "running" and j, timeout=30)
    assert job["status"] == "cancelled"
    assert b.page.evaluate("window.__setups.length") == 0


def test_a_build_that_cannot_start_returns_to_the_extract_modal_with_the_message(demo_server, bench):
    b = bench(demo_server, fill=False)  # no model: the server refuses an extract-setup job
    open_extract(b)
    add_five(b)
    b.page.click("#read-sample")
    expect(b.page.locator("#extract-msg")).to_contain_text("AI is not configured")
    expect(b.page.locator("#extract")).to_be_visible()
    expect(b.page.locator("#building")).to_be_hidden()


def test_the_folder_field_scans_a_folder_on_the_server(start_demo_server, bench, tmp_path):
    folder = tmp_path / "incoming"
    folder.mkdir()
    for name, data in FILES:
        (folder / name).write_bytes(data)
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    b.page.fill("#folder", str(folder))
    b.page.click("#scan")
    expect(b.page.locator("#batch-sum")).to_contain_text("5 fillable or flattened copies of one layout")
    expect(b.page.locator("#batch-sum")).to_contain_text("5 PDFs")
    expect(b.page.locator("#batch-strip li")).to_have_count(5)


def test_a_server_that_does_not_open_folders_says_so_in_the_message(start_demo_server, bench, tmp_path):
    b = bench(start_demo_server(folder_scan=False), fill=False)
    open_extract(b)
    b.page.fill("#folder", str(tmp_path))
    b.page.click("#scan")
    expect(b.page.locator("#extract-msg")).to_have_text("This server does not open folders on its machine. Upload the files instead.")
    expect(b.page.locator("#batch")).to_be_hidden()


def test_a_file_over_the_servers_upload_limit_is_skipped_and_named_and_the_rest_are_added(start_demo_server, bench):
    archive = stored_zip(FILES)
    limit = max(len(data) for _, data in FILES) + 4096  # each PDF fits a request of its own; the zip does not
    assert len(archive) > limit
    b = bench(extract_server(start_demo_server, max_upload_bytes=limit), fill=False)
    open_extract(b)
    b.page.set_input_files("#drop-files", files=pdf_files(FILES) + pdf_files([("all.zip", archive)], "application/zip"))
    expect(b.page.locator("#extract-msg")).to_contain_text("all.zip")
    expect(b.page.locator("#batch-sum")).to_contain_text("5 fillable or flattened copies of one layout")
    expect(b.page.locator("#batch-sum")).to_contain_text("5 PDFs")


def test_the_extract_modal_reopens_with_a_setups_choices(demo_server, bench):
    b = bench(demo_server)  # on the start screen: opening the modal closes it, one overlay at a time
    info = {
        "start": "destination", "lines": [], "line_paths": {}, "destination": "fw9", "detect": "manual",
        "pages": {"0": [1]}, "ocr": "never", "vision": True, "sample_how": "first",
    }  # fmt: skip
    b.page.evaluate("info => window.__workbench.openExtract(info)", info)
    expect(b.page.locator("#extract")).to_be_visible()
    expect(b.page.locator("#start")).to_be_hidden()
    assert b.page.evaluate("document.body.dataset.mode") == "extract"
    expect(b.page.locator('#extract .seg [data-mode="dest"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#dest-pick")).to_have_value("fw9")
    expect(b.page.locator("#detect")).to_have_value("manual")
    expect(b.page.locator("#ocr")).to_have_value("never")
    expect(b.page.locator("#sample-how")).to_have_value("first")
    expect(b.page.locator("#settings-sum")).to_have_text("you pick the pages")
    assert b.page.eval_on_selector("#pages-row", "row => row.hidden") is False  # shown in the (closed) settings
    expect(b.page.locator("#detect-pages")).to_have_value("2")  # sample 0's page index 1, counted from 1

    lines = {**info, "start": "lines", "lines": ["investor name", "tax ID"], "destination": None, "detect": "auto"}
    b.page.evaluate("info => window.__workbench.openExtract(info)", lines)
    expect(b.page.locator('#extract .seg [data-mode="user"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#lines")).to_have_value("investor name\ntax ID")
    expect(b.page.locator("#detect")).to_have_value("auto")
    assert b.page.eval_on_selector("#pages-row", "row => row.hidden") is True


def test_finding_the_blanks_by_hand_searches_the_pages_named_on_every_sample(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    add_five(b)
    listen_for_setups(b)
    b.page.evaluate("document.getElementById('extract-settings').open = true")
    expect(b.page.locator("#pages-row")).to_be_hidden()
    b.page.select_option("#detect", "manual")
    expect(b.page.locator("#pages-row")).to_be_visible()
    expect(b.page.locator("#pages-row")).to_contain_text("Page numbers from 1, with commas and ranges")
    for typed in ["", "0", "4-2", "x"]:  # nothing named, or nothing that names a page: refused before any job
        b.page.fill("#detect-pages", typed)
        b.page.click("#read-sample")
        expect(b.page.locator("#extract-msg")).to_have_text("Name the pages to search, such as 1 or 1, 3-4.")
        expect(b.page.locator("#building")).to_be_hidden()
        expect(b.page.locator("#read-sample")).to_be_enabled()
    b.page.fill("#detect-pages", "1")
    b.page.click("#read-sample")
    b.page.wait_for_function("window.__setups.length === 1", timeout=60_000)
    info = b.page.evaluate("window.__setups[0].template.setup")
    assert info["detect"] == "manual"
    assert info["pages"] == {str(i): [0] for i in range(5)}  # page 1 of each of the five samples, by position
    expect(b.page.locator("#build-stages li").nth(1)).to_have_attribute("data-status", "done")  # searched, not skipped


@pytest.fixture
def held_scan(monkeypatch):
    """Every batch scan waits for ``held_scan.set()`` before it reads its files (``clear()`` holds the next ones)."""
    gate = threading.Event()
    real = extraction.scan

    def scan(*args, **kwargs):
        assert gate.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(extraction, "scan", scan)
    yield gate
    gate.set()


def test_the_strip_reads_not_scanned_yet_until_the_scan_is_done_and_drops_the_sample_marks_when_it_starts_again(
    start_demo_server, bench, held_scan
):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    cells = b.page.locator("#batch-strip li")
    b.page.set_input_files("#drop-files", files=pdf_files(FILES[:3]))
    expect(b.page.locator("#batch-sum")).to_contain_text("scanning")
    expect(cells).to_have_count(3)
    expect(b.page.locator('#batch-strip li[data-state="pending"][title="not scanned yet"]')).to_have_count(3)
    held_scan.set()
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(3)

    held_scan.clear()  # two more files: the batch is scanned again, and its old sample is no longer on show
    b.page.set_input_files("#drop-files", files=pdf_files(FILES[3:]))
    expect(cells).to_have_count(5)
    expect(b.page.locator('#batch-strip li[data-state="pending"]')).to_have_count(5)
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(0)
    held_scan.set()
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(5)

    b.page.fill("#n-sample", "25")  # a size that is refused leaves no sample marked
    b.page.dispatch_event("#n-sample", "change")
    expect(b.page.locator('#batch-strip li[data-state="sample"]')).to_have_count(0)


def test_a_double_click_on_read_the_sample_starts_one_build(start_demo_server, bench):
    model = corpus_model(gate="derive")
    b = bench(extract_server(start_demo_server, model), fill=False)
    open_extract(b)
    add_five(b)
    starts = []
    b.page.on("request", lambda r: starts.append(r.url) if r.method == "POST" and r.url.endswith("/api/jobs") else None)
    try:
        disabled = b.page.evaluate(
            "() => { const go = document.getElementById('read-sample'); go.click(); const d = go.disabled; go.click(); return d; }"
        )
        assert disabled is True  # from the first click until the building panel opens
        assert model.reached.wait(20)
        expect(b.page.locator("#building")).to_be_visible()
    finally:
        model.released.set()
    b.page.wait_for_timeout(300)
    assert len(starts) == 1


# ------------------------------------------------------------------ the setup screen


def setup_on(base: str, files: list[tuple[str, bytes]] = FILES, **spec) -> dict:
    """A setup built through the API from a new batch of ``files`` (every one a sample), as the modal builds it."""
    batch = new_batch(base, name="Client forms")
    status, _ = add_files(base, batch["id"], files)
    assert status == 200
    samples = list(range(len(files)))
    status, out = start_job(base, {"kind": "extract-setup", "batch": batch["id"], "samples": samples, "spec": {"name": "Client forms", **spec}})
    assert status == 200, out
    job = job_ended(base, out["id"])
    assert job["status"] == "done", job
    return job["result"]["setup"]


def show_setup(b: Bench, setup: dict) -> None:
    """Open ``setup`` on the setup screen, as the Extract modal does once it is built."""
    b.page.evaluate("s => window.__workbench.openSetup(s)", setup)
    expect(b.page.locator('.view[data-only="setup"]')).to_be_visible()
    expect(b.page.locator("#setup-pv .fc-box").first).to_be_attached()


def row(b: Bench, path: str):
    """The field list's row of ``path`` (a repeated group's field by its first item's path)."""
    return b.page.locator(f'#fields .fc-sl-leaf[data-path="{path}"]')


def picked(b: Bench, kind: str = "box"):
    """The selected field's marks of ``kind`` on the page, drawn heavy."""
    return b.page.locator(f'#setup-pv .fc-box.is-picked[data-kind="{kind}"]')


def edits_sent(b: Bench) -> list[dict]:
    """Every edit the page sends from now on, as sent."""
    sent: list[dict] = []
    b.page.on("request", lambda r: sent.append(r.post_data_json["edit"]) if r.method == "POST" and r.url.endswith("/edit") else None)
    return sent


def setup_idle(b: Bench) -> None:
    """Wait until the setup screen has no edit on its way."""
    b.page.wait_for_function("window.__workbench.setupIdle")


def media_src(b: Bench) -> str:
    return b.page.locator("#setup-pv .fc-pv-media").get_attribute("src") or ""


def on_every_sample(setup: dict) -> list[str]:
    """The plain fields every sample agrees on: ruling P1 asserts ``● 5/5`` only of these."""
    return [
        f["path"] for f in setup["proof"]["fields"]
        if "[" not in f["path"] and all(s["outcome"] == "agree" for s in f["samples"])
    ]  # fmt: skip


def test_the_setup_screen_opens_after_the_build_with_each_fields_status_across_the_samples(start_demo_server, bench):
    b = bench(extract_server(start_demo_server), fill=False)
    open_extract(b)
    add_five(b)
    listen_for_setups(b)
    b.page.click("#read-sample")
    b.page.wait_for_function("window.__setups.length === 1", timeout=60_000)
    setup = b.page.evaluate("window.__setups[0]")
    expect(b.page.locator('.view[data-only="setup"]')).to_be_visible()
    expect(b.page.locator("#extract-empty")).to_be_hidden()
    expect(b.page.locator("#doc-pick")).to_contain_text("5 PDFs · 5 samples")
    expect(b.page.locator("#strip")).to_contain_text("AI suggests · 5 samples · automatic, prose skipped")
    expect(b.page.locator("#change")).to_be_visible()
    full = on_every_sample(setup)
    assert len(full) >= 5
    for path in full:
        expect(row(b, path).locator(".fc-sl-st")).to_have_text("●")
        expect(row(b, path).locator(".fc-sl-ty")).to_have_text("5/5")
    expect(row(b, "owners[0].name").locator(".fc-sl-name")).to_have_text("name")  # the group's items in one row
    expect(row(b, "owners[1].name")).to_have_count(0)
    expect(b.page.locator("#fields .fc-sl-grp")).to_have_text(["investor", "address", "owners[ ] · up to 4", "wire"])
    rows = b.page.locator("#fields .fc-sl-leaf").count()
    expect(b.page.locator("#fields-label")).to_have_text(f"Fields · {rows} · 0 need a look")
    expect(b.page.locator("#cap")).to_contain_text(f"{setup['proof']['ready']} of {setup['proof']['total']} fields ready")
    expect(b.page.locator("#sample-no")).to_have_text("Sample 1 / 5")
    expect(b.page.locator("#setup-pv .fc-pv-media")).to_be_attached(timeout=10_000)
    assert f"/api/setups/{setup['id']}/samples/0/pages/0.png" in media_src(b)
    expect(b.page.locator("#setup-pv .fc-box").first).to_be_visible()


def test_selecting_a_field_heavy_draws_its_box_with_its_path_and_the_inspector_says_how_it_is_read(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    row(b, "investor.tax_id").click()
    expect(row(b, "investor.tax_id")).to_have_class("fc-sl-leaf is-picked")
    expect(picked(b)).to_have_count(1)
    expect(picked(b).locator(".fc-tag")).to_have_text("investor.tax_id")
    expect(picked(b).locator(".fc-tag")).to_be_visible()
    expect(b.page.locator("#setup-pv .fc-box.is-picked")).to_have_count(2)  # its box and its label, on a fillable copy
    ins = b.page.locator("#ins")
    expect(ins).to_contain_text("investor.tax_id")
    expect(b.page.locator("#ins-title")).to_have_value("Taxpayer ID")
    expect(b.page.locator("#ins-type")).to_have_value("text")
    expect(ins.locator(".ro")).to_contain_text(["Form field tax_id", "A box on page 1", "Next to the label Taxpayer ID"])
    expect(b.page.locator("#ins-labels")).to_have_value("Taxpayer ID")
    expect(b.page.locator("#ins-required")).to_be_checked()
    expect(b.page.locator("#ins-pattern")).to_have_value(r"^\d{2}-\d{7}$|^\d{3}-\d{2}-\d{4}$")
    expect(ins).to_contain_text("label 'TIN' is on no sample")  # the build's warning about this field
    lines = b.page.locator("#per-sample li")
    expect(lines).to_have_count(5)
    expect(lines.nth(0)).to_contain_text(f"1. {PEOPLE[0].tax_id}")
    expect(lines.nth(3)).to_contain_text("s4.pdf · flattened")
    expect(lines.nth(0)).to_have_class("on")

    b.page.locator('#setup-pv .fc-box[data-field-id="investor.dob#box"]').click()  # a box on the page selects its field
    expect(row(b, "investor.dob")).to_have_class("fc-sl-leaf is-picked")
    expect(b.page.locator("#ins-title")).to_have_value("Date of birth or formation")
    expect(picked(b).locator(".fc-tag")).to_have_text("investor.dob")

    row(b, "investor.type").click()  # a checkbox group: its four boxes, and its answers
    expect(picked(b, "option")).to_have_count(4)
    expect(ins.locator(".answers li")).to_have_count(4)
    expect(ins.locator(".answers")).to_contain_text("trust")

    lines.nth(3).click()  # a sample's line shows that sample
    expect(b.page.locator("#sample-no")).to_have_text("Sample 4 / 5")
    expect(lines.nth(3)).to_have_class("on")


def test_a_box_dragged_on_the_shifted_sample_sends_one_set_box_naming_it_and_lands_where_it_was_dropped(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    for _ in range(SHIFTED):
        b.page.click("#next-s")
    expect(b.page.locator("#sample-no")).to_have_text(f"Sample {SHIFTED + 1} / 5")
    expect(b.page.locator("#sample-kind")).to_have_text(f"s{SHIFTED + 1}.pdf · flattened")
    # the legal name has no check, so its box over the tax ID reads a wrong value there (a value that fails a
    # check, as the date would under the tax ID's box, is passed over for the next way of reading the field)
    row(b, "investor.legal_name").click()
    box = picked(b)
    expect(box).to_have_count(1)
    b.page.wait_for_function("() => document.querySelector('#setup-pv .fc-pv-media')?.src.includes('/samples/4/')")
    sent = edits_sent(b)
    px_per_pt = box.bounding_box()["height"] / 20  # the box is 20 points tall
    before = box.bounding_box()["y"]
    b.drag(box, 0, 30 * px_per_pt)  # down onto the tax ID
    expect(row(b, "investor.legal_name").locator(".fc-sl-ty")).not_to_have_text("5/5")  # proven again
    assert len(sent) == 1, sent
    change = sent[0]
    assert (change["op"], change["path"], change["page"], change["sample"]) == ("set_box", "investor.legal_name", 0, SHIFTED)
    x0, y0, x1, y1 = FIELD_RECTS["investor_name"]
    assert change["rect"] == pytest.approx([x0, y0 + SHIFT + 30, x1, y1 + SHIFT + 30], abs=1)
    expect(b.page.locator("#per-sample li").nth(SHIFTED).locator("span").first).to_have_text("×")
    expect(b.page.locator("#per-sample li").nth(SHIFTED)).to_contain_text(PEOPLE[SHIFTED].tax_id)  # what it read there
    marks = get_json(base, f"/api/setups/{setup['id']}/samples/{SHIFTED}/marks")[1]["marks"]
    mark = next(m for m in marks if (m["path"], m["kind"]) == ("investor.legal_name", "box"))
    assert mark["rect"] == pytest.approx(change["rect"], abs=0.5)  # on this sample, where it was dropped
    strategies = get_json(base, f"/api/setups/{setup['id']}")[1]["template"]["readers"]["investor.legal_name"]["strategies"]
    assert next(s["rect"] for s in strategies if s["kind"] == "box") == pytest.approx([x0, y0 + 30, x1, y1 + 30], abs=1)
    setup_idle(b)
    expect(picked(b)).to_have_count(1)  # drawn again from the new marks, where it was dropped
    assert picked(b).bounding_box()["y"] == pytest.approx(before + 30 * px_per_pt, abs=2)


def test_an_edited_definition_is_kept_after_selecting_another_field_and_back(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    row(b, "investor.tax_id").click()
    sent = edits_sent(b)
    b.page.fill("#ins-def", "The EIN or SSN, with its dashes.")
    b.page.locator("#ins-def").blur()
    setup_idle(b)
    assert sent == [{"op": "set_field", "path": "investor.tax_id", "description": "The EIN or SSN, with its dashes."}]
    row(b, "investor.dob").click()
    expect(b.page.locator("#ins-def")).to_have_value("The investor's date of birth, or the date the entity was formed.")
    row(b, "investor.tax_id").click()
    expect(b.page.locator("#ins-def")).to_have_value("The EIN or SSN, with its dashes.")

    b.page.fill("#ins-title", "Tax ID")
    b.page.press("#ins-title", "Enter")
    setup_idle(b)
    b.page.uncheck("#ins-required")
    setup_idle(b)
    b.page.fill("#ins-labels", "Taxpayer ID, TIN number")
    b.page.press("#ins-labels", "Enter")
    setup_idle(b)
    assert sent[1:] == [
        {"op": "set_field", "path": "investor.tax_id", "title": "Tax ID"},
        {"op": "set_field", "path": "investor.tax_id", "required": False},
        {"op": "set_labels", "path": "investor.tax_id", "labels": ["Taxpayer ID", "TIN number"]},
    ]
    template = get_json(base, f"/api/setups/{setup['id']}")[1]["template"]
    assert template["schema"]["properties"]["investor"]["properties"]["tax_id"]["title"] == "Tax ID"
    expect(b.page.locator("#ins-title")).to_have_value("Tax ID")
    expect(b.page.locator("#ins-required")).not_to_be_checked()
    expect(b.page.locator("#ins-labels")).to_have_value("Taxpayer ID, TIN number")


def test_not_on_this_form_marks_the_field_absent_bring_back_restores_it_and_remove_takes_it_out(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    row(b, "wire.bank").click()
    expect(b.page.locator("#ins-absent")).to_have_text("Not on this form")
    b.page.click("#ins-absent")
    expect(row(b, "wire.bank").locator(".fc-sl-st")).to_have_text("○")
    expect(row(b, "wire.bank")).to_have_attribute("data-state", "absent")
    expect(row(b, "wire.bank")).to_have_class("fc-sl-leaf is-picked")  # still the field selected
    expect(b.page.locator("#per-sample li")).to_have_count(5)
    expect(b.page.locator("#per-sample li").first).to_contain_text("not on this form")
    expect(b.page.locator("#ins .ro")).to_contain_text(["Not on this form"])
    expect(b.page.locator('#setup-pv .fc-box[data-field-id^="wire.bank#"]')).to_have_count(0)
    expect(b.page.locator("#ins-absent")).to_have_text("Bring back")
    b.page.click("#ins-absent")
    expect(row(b, "wire.bank")).not_to_have_attribute("data-state", "absent")
    expect(b.page.locator("#ins-absent")).to_have_text("Not on this form")

    b.page.click("#ins-remove")
    expect(row(b, "wire.bank")).to_have_count(0)
    assert "wire.bank" not in get_json(base, f"/api/setups/{setup['id']}")[1]["template"]["readers"]
    expect(b.page.locator("#fields .fc-sl-leaf.is-picked")).to_have_count(1)  # another field is selected


def test_add_a_field_from_a_line_adds_its_fields_and_selects_the_first(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base, start="lines", lines=["tax ID"])
    b = bench(base, fill=False)
    show_setup(b, setup)
    expect(row(b, "wire.bank")).to_have_count(0)
    b.page.fill("#add", "wire details")
    b.page.click("#add-go")
    expect(row(b, "wire.bank")).to_have_class("fc-sl-leaf is-picked", timeout=30_000)  # the CorpusModel's answer
    expect(row(b, "wire.account")).to_be_visible()
    expect(b.page.locator("#add")).to_have_value("")
    expect(b.page.locator("#strip textarea")).to_have_value("tax ID\nwire details")
    expect(b.page.locator("#ins-title")).to_have_value("Sending bank")

    b.page.fill("#add", "the moon's phase")  # nothing on the samples answers it: the job's reason is shown
    b.page.click("#add-go")
    expect(b.page.locator("#add-msg")).to_contain_text("the schema has no fields", timeout=30_000)
    expect(b.page.locator("#add")).to_have_value("the moon's phase")
    expect(b.page.locator("#add-go")).to_be_enabled()


def test_the_arrows_walk_the_samples_and_their_pages_and_the_zoom_fits_the_page(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    for n in range(1, 6):
        expect(b.page.locator("#sample-no")).to_have_text(f"Sample {n} / 5")
        expect(b.page.locator("#sample-kind")).to_have_text(f"s{n}.pdf · {'fillable' if n <= 3 else 'flattened'}")
        b.page.wait_for_function("n => document.querySelector('#setup-pv .fc-pv-media')?.src.includes(`/samples/${n}/pages/0.png`)", arg=n - 1)
        b.page.click("#next-s")
    expect(b.page.locator("#sample-no")).to_have_text("Sample 1 / 5")  # round again
    b.page.click("#prev-s")
    expect(b.page.locator("#sample-no")).to_have_text("Sample 5 / 5")
    expect(b.page.locator("#page-of")).to_have_text("Page 1 / 2")
    expect(b.page.locator("#prev-p")).to_be_disabled()
    b.page.click("#next-p")
    expect(b.page.locator("#page-of")).to_have_text("Page 2 / 2")
    expect(b.page.locator("#next-p")).to_be_disabled()
    b.page.wait_for_function("() => document.querySelector('#setup-pv .fc-pv-media')?.src.includes('/samples/4/pages/1.png')")
    row(b, "investor.tax_id").click()  # its box is on page 1: the page turns to it
    expect(b.page.locator("#page-of")).to_have_text("Page 1 / 2")
    expect(picked(b)).to_have_count(1)

    width = "document.querySelector('#setup-pv .fc-pv-page').getBoundingClientRect().width"
    expect(b.page.locator('#setup-zoom [data-fit="width"]')).to_have_attribute("aria-pressed", "true")
    wide = b.page.evaluate(width)
    b.page.click('#setup-zoom [data-fit="page"]')
    expect(b.page.locator('#setup-zoom [data-fit="page"]')).to_have_attribute("aria-pressed", "true")
    b.page.wait_for_function(f"() => {width} < {wide - 1}")
    b.page.click('#setup-zoom [data-step="1"]')
    expect(b.page.locator('#setup-zoom [data-fit="page"]')).to_have_attribute("aria-pressed", "false")
    b.page.click("#setup-zoom [data-level]")  # back to fit width
    expect(b.page.locator('#setup-zoom [data-fit="width"]')).to_have_attribute("aria-pressed", "true")
    b.page.wait_for_function(f"() => Math.abs({width} - {wide}) < 2")


def test_save_asks_for_a_name_and_stores_the_extraction_template(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    b.page.click("#setup-save")
    expect(b.page.locator("#setup-save-box")).to_be_visible()
    expect(b.page.locator("#setup-save-name")).to_have_value("Client forms")
    b.page.fill("#setup-save-name", "../nope")
    b.page.click("#setup-save-go")
    expect(b.page.locator("#setup-save-msg")).to_contain_text("letters, digits")
    b.page.fill("#setup-save-name", "client intake")
    b.page.click("#setup-save-go")
    expect(b.page.locator("#setup-save-box")).to_be_hidden()
    expect(b.page.locator("#notice")).to_contain_text("Saved “client intake”")
    listed = get_json(base, "/api/extractions")[1]["extractions"]
    assert [e["name"] for e in listed] == ["client intake"] and listed[0]["samples"] == 5


def test_an_edit_after_the_server_has_forgotten_the_setup_returns_to_the_choice_with_a_notice(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base)
    b = bench(base, fill=False)
    show_setup(b, setup)
    row(b, "investor.tax_id").click()
    start_demo_server.servers[-1].api.setups._setups.clear()  # as after a restart
    b.page.click("#ins-absent")
    expect(b.page.locator("#choose")).to_be_visible()
    expect(b.page.locator("#choose-msg")).to_have_text("This setup is no longer on the server; start again.")
    expect(b.page.locator('.view[data-only="setup"]')).to_be_hidden()
    expect(b.page.locator("#extract-empty")).to_be_visible()
    b.page.click('.choice[data-go="extract"]')  # the message goes with the choice made
    expect(b.page.locator("#extract")).to_be_visible()
    expect(b.page.locator("#choose-msg")).to_be_hidden()


def test_a_setup_from_your_lines_shows_them_in_the_strip_and_a_field_for_each_of_their_paths(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base, start="lines", lines=["tax ID", "investor name"])
    b = bench(base, fill=False)
    show_setup(b, setup)
    expect(b.page.locator("#strip")).to_contain_text("I'll list them · 5 samples")
    expect(b.page.locator("#strip textarea")).to_have_value("tax ID\ninvestor name")
    wanted = {p.replace("[]", "[0]") for paths in setup["template"]["setup"]["line_paths"].values() for p in paths}
    assert wanted
    shown = set(b.page.eval_on_selector_all("#fields .fc-sl-leaf", "rows => rows.map(r => r.dataset.path)"))
    assert shown == wanted
    row(b, "investor.legal_name").click()
    expect(b.page.locator("#ins")).to_contain_text("From your line “investor name”.")

    b.page.click("#change")  # the Extract modal again, with this setup's choices
    expect(b.page.locator("#extract")).to_be_visible()
    expect(b.page.locator('#extract .seg [data-mode="user"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#lines")).to_have_value("tax ID\ninvestor name")


def test_a_setup_from_a_destination_names_it_and_lists_the_fields_this_form_lacks_as_not_on_it(start_demo_server, bench):
    base = extract_server(start_demo_server)
    setup = setup_on(base, start="destination", destination="fw9")
    absent = [f["path"] for f in setup["proof"]["fields"] if f["absent"]]
    assert len(absent) > 1
    b = bench(base, fill=False)
    show_setup(b, setup)
    expect(b.page.locator("#strip")).to_contain_text("Destination · 5 samples")
    expect(b.page.locator("#strip")).to_contain_text("fw9")
    expect(b.page.locator("#strip")).to_contain_text(f"{len(absent)} of its fields are not on this form")
    for path in absent:
        expect(row(b, path).locator(".fc-sl-st")).to_have_text("○")
        expect(row(b, path)).to_have_attribute("data-state", "absent")
    expect(row(b, "address.street").locator(".fc-sl-st")).to_have_text("●")


def test_a_wording_seen_on_the_samples_is_added_to_the_answers_map_when_asked(start_demo_server, bench):
    people = [dataclasses.replace(PEOPLE[0], country="Estados Unidos"), *PEOPLE[1:]]
    files = sample_set(people)
    model = GatedModel({name: client for (name, _), client in zip(files, people)}, us_files=["s1.pdf"])
    base = extract_server(start_demo_server, model)
    setup = setup_on(base, files)
    b = bench(base, fill=False)
    show_setup(b, setup)
    row(b, "investor.country").click()
    expect(row(b, "investor.country").locator(".fc-sl-st")).to_have_text("◐")
    wordings = b.page.locator("#ins-wordings li")
    expect(wordings).to_have_count(1)
    expect(wordings).to_have_text("Estados Unidos → US")
    calls = dict(model.calls)
    b.page.click("#ins-learn")
    expect(row(b, "investor.country").locator(".fc-sl-st")).to_have_text("●")
    expect(b.page.locator("#ins-wordings")).to_have_count(0)
    assert model.calls == calls  # no model call
    reader = get_json(base, f"/api/setups/{setup['id']}")[1]["template"]["readers"]["investor.country"]
    assert reader["clean"][0]["args"]["values"]["Estados Unidos"] == "US"
