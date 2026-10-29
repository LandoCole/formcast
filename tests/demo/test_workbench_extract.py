"""The workbench page's way in to extraction (``demo/web/workbench/``) in Chromium against the real workbench server.

The page opens on "What would you like to do?" (``#choose``): *Fill a PDF* opens the existing start screen as it is,
*Extract from PDFs* switches the page to its extraction mode (``body[data-mode]``), shown only when the server says
it can extract. The Extract modal (``#extract``) takes the batch (files chosen or dropped, or a folder on the
server's machine), the sample, what to extract and how to read it; *Read the sample* builds the template in the
building panel (``#building``) and hands the setup on (``extract:setup`` on the bus). The setup screen and the review
(Tasks 8-9) come later in this file. The server runs in-process over a throwaway copy of ``demo/data`` (conftest
``start_demo_server``), with form maps built without detectors, as in ``test_workbench_page``, whose ``Bench`` drives
the page here too; a template is built from the five corpus samples of ``test_server_extract`` by ``CorpusModel``
(gated there as ``GatedModel``), with no detector weights loaded.
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from playwright.sync_api import expect
from test_server_documents import get_json, small_pdf
from test_server_extract import GatedModel, assert_proven, sample_set, zip_of
from test_server_jobs import wait_for
from test_workbench_page import Bench, bench, no_model_loads, widget_pdf  # noqa: F401 (bench and no_model_loads are fixtures)

from demo.server.api import Api
from formcast.extract.setup import STAGES
from tests.extract.corpus import clients


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

    lines = {**info, "start": "lines", "lines": ["investor name", "tax ID"], "destination": None, "detect": "auto"}
    b.page.evaluate("info => window.__workbench.openExtract(info)", lines)
    expect(b.page.locator('#extract .seg [data-mode="user"]')).to_have_attribute("aria-pressed", "true")
    expect(b.page.locator("#lines")).to_have_value("investor name\ntax ID")
    expect(b.page.locator("#detect")).to_have_value("auto")
