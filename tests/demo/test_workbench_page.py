"""The workbench page (``demo/web/workbench/``, layout B) in Chromium against the real workbench server.

Each test starts the server in-process over a throwaway copy of ``demo/data`` (conftest ``start_demo_server``);
AI jobs run on the scripted fake provider from ``test_server_jobs`` (no network, no real model), and a ``Gate``
in a script holds a job mid-run. Form maps are built without detectors, so a seed is the form's own widgets.
``?poll=50`` makes the page poll jobs every 50 ms (honoured on localhost only).
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pymupdf
import pytest
from playwright.sync_api import expect
from test_server_documents import get_json
from test_server_jobs import ADD_NAME, ADD_NOTE, Factory, Gate, code, finish
from test_server_routes import FW9_EDITED

from demo.server import documents as documents_mod
from formcast.pagemap.build import DetectPolicy

NO_NOTE = [{"path": "note", "reason": "this form has no notes line"}]
SCHEMA_TEXT = '{"type": "object", "properties": {"name": {"type": "string"}, "note": {"type": "string"}}}'


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None: real(pdf, detectors={}, policy=policy, ocr="never"))


def widget_pdf(*, width: float = 300, height: float = 200, rect=(20, 120, 120, 140), rotation: int = 0, fill=None) -> bytes:
    """A one-page PDF with one text widget at ``rect`` (unrotated points, origin top-left); ``fill`` paints the rect first."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((20, 30), "Name")
    if fill is not None:
        page.draw_rect(pymupdf.Rect(rect), color=None, fill=fill)
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "name_box"
    widget.rect = pymupdf.Rect(rect)
    widget.border_width = 0
    widget.fill_color = None
    page.add_widget(widget)
    page.set_rotation(rotation)
    out = doc.tobytes()
    doc.close()
    return out


class Bench:
    """Drives the workbench page."""

    def __init__(self, page, base: str) -> None:
        self.page = page
        self.base = base
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))

    def goto(self) -> None:
        self.page.goto(f"{self.base}/workbench/?poll=50")
        expect(self.page.locator("#start")).to_be_visible()
        expect(self.page.locator("#form-pick option[value='fw9']")).to_have_count(1)

    def open_form(self, name: str) -> None:
        self.page.select_option("#form-pick", name)
        expect(self.page.locator("#schema-pick")).to_have_value(name)
        self.page.click("#open")

    def upload(self, data: bytes, name: str = "form.pdf", schema_text: str | None = SCHEMA_TEXT) -> None:
        self.page.set_input_files("#file", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}])
        if schema_text is not None:
            self.page.select_option("#schema-pick", "__paste__")
            self.page.fill("#schema-text", schema_text)
        self.page.click("#open")

    def boxes(self, extra: str = ""):
        return self.page.locator(f"#pageview .fc-box{extra}")

    def template(self) -> dict:
        return self.page.evaluate("window.__workbench.template")

    def idle(self) -> None:
        self.page.wait_for_function("window.__workbench.idle")

    def history(self) -> list[tuple[str, str]]:
        rows = self.page.locator("#history .fc-hi-list li")
        return [(r.locator(".fc-hi-who").inner_text(), r.locator("b").inner_text()) for r in rows.all()]

    def drag(self, locator, dx: float, dy: float) -> None:
        bb = locator.bounding_box()
        cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
        self.page.mouse.move(cx, cy)
        self.page.mouse.down()
        self.page.mouse.move(cx + dx / 2, cy + dy / 2, steps=3)
        self.page.mouse.move(cx + dx, cy + dy, steps=3)
        self.page.mouse.up()

    def drag_on_page(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Drag across the page surface between two points given as fractions of its size."""
        bb = self.page.locator("#pageview .fc-pv-page").bounding_box()
        self.page.mouse.move(bb["x"] + bb["width"] * x0, bb["y"] + bb["height"] * y0)
        self.page.mouse.down()
        self.page.mouse.move(bb["x"] + bb["width"] * (x0 + x1) / 2, bb["y"] + bb["height"] * (y0 + y1) / 2, steps=4)
        self.page.mouse.move(bb["x"] + bb["width"] * x1, bb["y"] + bb["height"] * y1, steps=4)
        self.page.mouse.up()

    def build_new(self) -> None:
        """Answer the match prompt with Build new (when it shows) and wait for the seed on screen."""
        self.page.wait_for_function("document.querySelector('#match:not([hidden])') || window.__workbench.template")
        if self.page.locator("#match").is_visible():
            self.page.click("#build-new")
        expect(self.page.locator("#start")).to_be_hidden()
        self.page.wait_for_function("window.__workbench.template !== null")

    def download(self, button: str) -> bytes:
        with self.page.expect_download() as info:
            self.page.click(button)
        return Path(info.value.path()).read_bytes()

    def filled(self, title: str = "") -> None:
        """Wait for the filled PDF on the page (its caption naming ``title``), drawn and with the boxes hidden."""
        self.page.wait_for_function(
            "t => document.getElementById('pageview').classList.contains('is-filled')"
            " && document.getElementById('caption').textContent.includes(t)"
            " && document.querySelector('#pageview canvas.fc-pv-media')",
            arg=title,
        )
        assert self.page.eval_on_selector("#pageview .fc-pv-layer", "el => getComputedStyle(el).visibility") == "hidden"

    def cleared(self) -> None:
        """Wait for the form with its boxes back on the page."""
        self.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-filled')")
        expect(self.page.locator("#fill-download")).to_be_disabled()


# Holds every /api/apply request in the page until the test releases it (oldest first), and counts them.
GATE_APPLY = """
  (() => {
    const real = window.fetch.bind(window);
    window.__applyGates = [];
    window.__applyCalls = 0;
    window.__releaseApply = () => window.__applyGates.shift()?.();
    window.fetch = async (url, init) => {
      if (String(url).includes("/api/apply")) {
        window.__applyCalls += 1;
        await new Promise((resolve) => window.__applyGates.push(resolve));
      }
      return real(url, init);
    };
  })();
"""


# Holds every /api/seed request in the page until the test calls window.__releaseSeed().
GATE_SEED = """
  (() => {
    const real = window.fetch.bind(window);
    window.__seedGates = [];
    window.__releaseSeed = () => window.__seedGates.shift()?.();
    window.fetch = async (url, init) => {
      if (String(url).includes("/api/seed")) await new Promise((resolve) => window.__seedGates.push(resolve));
      return real(url, init);
    };
  })();
"""


def box(page, fid: str):
    return page.locator(f'#pageview .fc-box[data-field-id="{fid}"]')


def box_px(page, fid: str, prop: str) -> float:
    """A box's on-screen left, top, width or height in pixels (its inline style)."""
    return float(box(page, fid).evaluate(f"el => el.style.{prop}").removesuffix("px"))


def two_boxes(b: Bench) -> tuple[str, str]:
    """Open a one-widget PDF and add a second box by hand (its apply released at the gate); their ids."""
    b.upload(widget_pdf(rect=(20, 120, 120, 140)))
    b.build_new()
    b.page.click("#add-box")
    b.drag_on_page(0.6, 0.1, 0.9, 0.25)
    b.page.wait_for_function("window.__applyCalls === 1")
    b.page.evaluate("window.__releaseApply()")
    expect(b.boxes()).to_have_count(2)
    b.idle()
    return b.boxes().nth(0).get_attribute("data-field-id"), b.boxes().nth(1).get_attribute("data-field-id")



@pytest.fixture
def bench(page):
    made: list[Bench] = []

    def make(base: str) -> Bench:
        b = Bench(page, base)
        made.append(b)
        b.goto()
        return b

    yield make
    for b in made:
        assert b.errors == []


# ------------------------------------------------------------------ start screen and matches


def test_picking_fw9_offers_reuse_of_the_stored_template_and_reuse_loads_it(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    expect(b.page.locator("#match")).to_be_visible()
    expect(b.page.locator("#match-text")).to_contain_text("fw9")
    expect(b.page.locator("#reuse")).to_be_visible()
    b.page.click("#reuse")
    expect(b.page.locator("#match")).to_be_hidden()
    expect(b.boxes(".is-linked").first).to_be_visible()
    assert b.boxes(".is-linked").count() > 3
    assert "fw9.pdf" in b.page.locator("#doc-name").inner_text()
    stored = get_json(demo_server, "/api/templates/fw9")[1]["template"]
    assert b.template()["template_id"] == stored["template_id"]


def test_while_the_seed_waits_for_the_form_reading_the_page_says_so(demo_server, bench, page):
    page.add_init_script(GATE_SEED)
    b = bench(demo_server)
    b.upload(widget_pdf())  # nothing stored fits: straight to the seed
    page.wait_for_function("window.__seedGates.length === 1")
    expect(page.locator("#start-msg")).to_have_text("Reading the form (the first time can take a minute while the detector downloads)…")
    page.evaluate("window.__releaseSeed()")
    expect(page.locator("#start")).to_be_hidden()
    page.wait_for_function("window.__workbench.template !== null")

    page.click("#doc-name")  # Build new from the match prompt: the prompt says it while it waits
    b.open_form("fw9")
    expect(page.locator("#match")).to_be_visible()
    page.click("#build-new")
    page.wait_for_function("window.__seedGates.length === 1")
    expect(page.locator("#match-hint")).to_contain_text("Reading the form (the first time can take a minute")
    page.evaluate("window.__releaseSeed()")
    expect(page.locator("#match")).to_be_hidden()
    page.wait_for_function("window.__workbench.template?.fields.length > 20")


def test_a_pdf_that_is_not_a_pdf_gets_a_plain_message_and_the_start_screen_stays(demo_server, bench):
    b = bench(demo_server)
    b.upload(b"this is not a pdf", "notes.pdf")
    expect(b.page.locator("#start-msg")).to_contain_text("can't be used")
    expect(b.page.locator("#start")).to_be_visible()
    expect(b.page.locator("#open")).to_be_enabled()


def test_a_pasted_schema_that_is_not_json_is_refused_under_the_text_box(demo_server, bench):
    b = bench(demo_server)
    b.upload(widget_pdf(), schema_text="{not json")
    expect(b.page.locator("#start-msg")).to_contain_text("isn't valid JSON")
    expect(b.page.locator("#start")).to_be_visible()


# ------------------------------------------------------------------ editing by hand


def test_build_new_by_hand_move_undo_and_redo(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.build_new()
    expect(b.boxes().first).to_be_visible()
    seed = b.template()
    assert seed["history"] == [] and len(seed["fields"]) > 20
    first = b.boxes().first
    fid = first.get_attribute("data-field-id")
    before = next(f for f in seed["fields"] if f["id"] == fid)["rect"]

    b.drag(first, 30, 12)
    expect(b.page.locator("#history .fc-hi-list li")).to_have_count(1)
    b.idle()
    assert b.history()[0][0] == "you" and b.history()[0][1].startswith("move")
    moved = next(f for f in b.template()["fields"] if f["id"] == fid)["rect"]
    assert moved[0] > before[0] + 5

    b.page.click("#undo")
    expect(b.page.locator("#history .fc-hi-empty")).to_be_visible()
    b.idle()
    assert next(f for f in b.template()["fields"] if f["id"] == fid)["rect"] == before

    b.page.click("#redo")
    expect(b.page.locator("#history .fc-hi-list li")).to_have_count(1)
    b.idle()
    assert next(f for f in b.template()["fields"] if f["id"] == fid)["rect"] == moved


def test_a_box_moved_while_an_earlier_edit_is_in_flight_stays_where_it_was_left(demo_server, bench, page):
    """Under server latency a landing edit must not snap other boxes back or drop a held arrow-key nudge."""
    page.add_init_script(GATE_APPLY)
    b = bench(demo_server)
    a_id, c_id = two_boxes(b)
    a, c = box(page, a_id), box(page, c_id)
    left = partial(box_px, page, prop="left")

    c_start = left(c_id)
    b.drag(a, 20, 0)  # its move is in flight, held at the gate
    page.wait_for_function("window.__applyCalls === 2")
    a_dropped = left(a_id)
    c.click()
    for _ in range(3):
        page.keyboard.down("ArrowRight")  # a held key: three nudges, emitted once on keyup
    c_nudged = left(c_id)
    assert c_nudged > c_start + 2
    page.evaluate("window.__releaseApply()")  # the first move lands while the key is still held
    page.wait_for_function("window.__workbench.idle && window.__workbench.template.history.length === 2")
    assert left(c_id) == c_nudged  # not snapped back by the landing edit
    assert left(a_id) == pytest.approx(a_dropped, abs=0.5)
    page.keyboard.up("ArrowRight")
    page.wait_for_function("window.__applyCalls === 3")
    assert left(c_id) == c_nudged  # still where it was left while its own move is in flight
    page.evaluate("window.__releaseApply()")
    b.idle()
    history = b.template()["history"]
    assert [(h["op"]["op"], h["op"]["id"]) for h in history[-2:]] == [("move", a_id), ("move", c_id)]
    c_rect = next(f["rect"] for f in b.template()["fields"] if f["id"] == c_id)
    assert history[-1]["op"]["rect"] == c_rect
    assert left(c_id) == pytest.approx(c_nudged, abs=0.5)  # the landed rect is where the box was left


# ------------------------------------------------------------------ AI jobs


def test_build_with_ai_lists_the_steps_loads_the_result_and_one_undo_returns_to_the_seed(start_demo_server, bench):
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), code("print(1)"), code("print(2)"), finish("name mapped", NO_NOTE)]))
    b = bench(base)
    b.upload(widget_pdf())
    b.build_new()
    seed = b.template()
    expect(b.page.locator("#build-ai")).to_be_visible()
    b.page.click("#build-ai")
    expect(b.page.locator("#job")).to_be_visible()
    expect(b.page.locator("#job-load")).to_be_visible()
    expect(b.page.locator("#job-steps li")).to_have_count(4)
    expect(b.page.locator("#job-status")).to_contain_text("name mapped")
    b.page.click("#job-load")
    expect(b.page.locator("#job")).to_be_hidden()
    expect(b.boxes(".is-ai").first).to_be_visible()
    b.idle()
    assert any(who == "AI" for who, _ in b.history())
    b.page.click("#undo")
    expect(b.boxes(".is-ai")).to_have_count(0)
    b.idle()
    assert b.template() == seed


@pytest.mark.usefixtures("text_only_agent")
def test_area_select_revise_runs_a_job_and_loads_its_result(start_demo_server, bench):
    factory = Factory([code(ADD_NOTE), finish("added a note field")])
    base = start_demo_server(provider_factory=factory)
    b = bench(base)
    b.upload(widget_pdf())
    b.build_new()
    b.drag_on_page(0.55, 0.1, 0.95, 0.5)
    expect(b.page.locator("#ask")).to_be_visible()
    b.page.fill("#ask-text", "Add a field for notes")
    b.page.click("#ask-go")
    expect(b.page.locator("#job-load")).to_be_visible()
    b.page.click("#job-load")
    expect(b.boxes(".is-ai")).to_have_count(1)
    first_message = factory.providers[0].calls[0][1].content
    assert "Add a field for notes" in first_message and "region" in first_message.lower()


def test_adapt_seeds_the_new_pdf_then_loads_the_stored_template_moved_onto_it(start_demo_server, bench):
    unplaced = [{"path": p, "reason": "not on the form"} for p in ("vendor_id", "remit_email")]  # unlinked in the stored fw9
    base = start_demo_server(provider_factory=Factory([finish("kept every box", unplaced)]))
    b = bench(base)
    b.upload(FW9_EDITED, "fw9_rev.pdf", schema_text=None)
    expect(b.page.locator("#match-text")).to_contain_text("fw9")
    b.page.click("#adapt")
    expect(b.page.locator("#job-load")).to_be_visible()
    seed = b.template()
    assert seed["history"] == [] and not any(f["source"] for f in seed["fields"])  # the seed is on screen behind the panel
    b.page.click("#job-load")
    expect(b.boxes(".is-linked").first).to_be_visible()
    b.idle()
    stored = get_json(base, "/api/templates/fw9")[1]["template"]
    adapted = b.template()
    assert adapted["form"]["fingerprint"] == seed["form"]["fingerprint"] != stored["form"]["fingerprint"]
    assert len(adapted["fields"]) == len(stored["fields"])
    b.page.click("#undo")
    b.idle()
    assert b.template() == seed


def test_cancel_stops_after_the_step_leaves_the_template_and_offers_the_partial_result(start_demo_server, bench):
    gate = Gate()
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, code(ADD_NOTE), finish()]))
    b = bench(base)
    b.upload(widget_pdf())
    b.build_new()
    before = b.template()
    b.page.click("#build-ai")
    assert gate.reached.wait(10)
    expect(b.page.locator("#job-steps li")).to_have_count(1)
    b.page.click("#job-cancel")
    expect(b.page.locator("#job-status")).to_have_text("Stopping after this step…")
    gate.release()
    expect(b.page.locator("#job")).to_be_hidden()
    expect(b.page.locator("#offer")).to_contain_text("cancelled")
    b.idle()
    assert b.template() == before
    expect(b.boxes(".is-ai")).to_have_count(0)
    expect(b.page.locator("#offer-load")).to_have_text("Load partial result")
    b.page.click("#offer-load")
    expect(b.boxes(".is-ai")).to_have_count(2)  # the step in flight when the cancel came is in the partial result


def test_a_resize_from_a_corner_handle_survives_an_earlier_edit_landing(demo_server, bench, page):
    """The page view stops a handle press from bubbling; the workbench must still see the gesture."""
    page.add_init_script(GATE_APPLY)
    b = bench(demo_server)
    a_id, c_id = two_boxes(b)
    width = partial(box_px, page, prop="width")
    c_width = width(c_id)
    b.drag(box(page, a_id), 20, 0)  # its move is in flight, held at the gate
    page.wait_for_function("window.__applyCalls === 2")
    box(page, c_id).click()
    bb = box(page, c_id).locator('.fc-hd[data-handle="se"]').bounding_box()
    x, y = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 15, y + 6, steps=3)
    page.evaluate("window.__releaseApply()")  # the move lands mid-resize
    page.wait_for_function("window.__workbench.idle && window.__workbench.template.history.length === 2")
    assert width(c_id) > c_width + 10  # the box was not redrawn under the pointer
    page.mouse.move(x + 30, y + 6, steps=3)
    page.mouse.up()
    page.wait_for_function("window.__applyCalls === 3")
    page.evaluate("window.__releaseApply()")
    b.idle()
    last = b.template()["history"][-1]["op"]
    assert last["op"] == "resize" and last["id"] == c_id
    assert width(c_id) == pytest.approx(c_width + 30, abs=1)


def test_a_secondary_button_press_does_not_hold_back_redrawing(demo_server, bench, page):
    """A right press may never see its pointerup (a context menu takes it); it must not count as a held gesture."""
    page.add_init_script(GATE_APPLY)
    b = bench(demo_server)
    a_id, _ = two_boxes(b)
    left = partial(box_px, page, prop="left")
    start = left(a_id)
    b.drag(box(page, a_id), 20, 0)  # in flight
    page.wait_for_function("window.__applyCalls === 2")
    page.dispatch_event("#pageview .fc-pv-layer", "pointerdown", {"button": 2, "buttons": 2, "pointerType": "mouse", "isPrimary": True})
    page.keyboard.press("Control+z")  # queued behind the move: the box goes back once both have run
    page.evaluate("window.__releaseApply()")
    page.wait_for_function("window.__workbench.idle && window.__workbench.template.history.length === 1")
    assert left(a_id) == pytest.approx(start, abs=0.5)  # redrawn although no pointerup came


def test_with_ai_on_a_click_on_empty_space_clears_the_selection_and_a_drag_asks_the_ai(start_demo_server, bench):
    b = bench(start_demo_server(provider_factory=Factory()))
    b.upload(widget_pdf())
    b.build_new()
    b.boxes().first.click()
    expect(b.page.locator("#inspector .fc-ins-dl")).to_be_visible()
    bb = b.page.locator("#pageview .fc-pv-page").bounding_box()
    b.page.mouse.click(bb["x"] + bb["width"] * 0.8, bb["y"] + bb["height"] * 0.15)
    expect(b.page.locator("#inspector .fc-ins-empty")).to_be_visible()
    expect(b.page.locator("#ask")).to_be_hidden()
    b.boxes().first.click()
    b.page.keyboard.press("Escape")
    expect(b.page.locator("#inspector .fc-ins-empty")).to_be_visible()
    b.drag_on_page(0.55, 0.1, 0.95, 0.5)
    expect(b.page.locator("#ask")).to_be_visible()


def test_adapt_needs_only_the_same_page_count_while_reuse_needs_the_same_page_sizes(start_demo_server, bench, page):
    unplaced = [{"path": p, "reason": "not on the form"} for p in ("vendor_id", "remit_email")]
    factory = Factory([finish("moved the boxes", unplaced)])
    b = bench(start_demo_server(provider_factory=factory))
    fake = [{"name": "fw9", "template_id": "x", "score": 0.8, "basis": "signature", "explanation": "80% alike.", "action": "adapt"}]
    page.route("**/api/documents/*/matches", lambda route: route.fulfill(json=fake))
    doc = pymupdf.open()
    for _ in range(6):  # fw9 has six pages; these are a different size
        doc.new_page(width=500, height=500).insert_text((40, 60), "Name")
    b.upload(doc.tobytes(), "square.pdf", schema_text=None)
    expect(b.page.locator("#match-text")).to_contain_text("fw9")
    b.page.click("#reuse")
    expect(b.page.locator("#match-hint")).to_contain_text("expects page 1 to be")
    expect(b.page.locator("#match")).to_be_visible()
    b.page.click("#adapt")
    expect(b.page.locator("#job-title")).to_have_text("AI adapt")
    b.page.wait_for_function("document.querySelector('#job-load:not([hidden])') || document.querySelector('#job[hidden]')")
    assert len(factory.providers) == 1  # the adapt job ran


def test_a_cancel_the_server_refuses_can_be_retried_or_the_job_run_in_the_background(start_demo_server, bench, page):
    gate = Gate()
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, code(ADD_NOTE), finish("both added", NO_NOTE)]))
    b = bench(base)
    b.upload(widget_pdf())
    b.build_new()
    b.page.click("#build-ai")
    assert gate.reached.wait(10)
    page.route("**/api/jobs/*/cancel", lambda route: route.fulfill(status=500, body=""))
    b.page.click("#job-cancel")
    expect(b.page.locator("#job-status")).to_contain_text("couldn't be stopped")
    expect(b.page.locator("#job-cancel")).to_be_enabled()
    expect(b.page.locator("#job-close")).to_contain_text("run in background", ignore_case=True)
    b.page.click("#job-close")
    expect(b.page.locator("#job")).to_be_hidden()
    expect(b.page.locator("#notice")).to_contain_text("goes on running")
    before = b.template()
    gate.release()
    expect(b.page.locator("#offer")).to_contain_text("finished: both added")
    assert b.template() == before  # nothing loads until asked
    expect(b.page.locator("#offer-load")).to_have_text("Load result")
    b.page.click("#offer-load")
    expect(b.boxes(".is-ai")).to_have_count(2)
    expect(b.page.locator("#offer")).to_be_hidden()


def test_a_background_result_stays_on_offer_through_other_messages_and_loads_over_edits_as_one_step(start_demo_server, bench, page):
    gate = Gate()
    b = bench(start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, finish("name added", NO_NOTE)])))
    b.upload(widget_pdf())
    b.build_new()
    b.page.click("#build-ai")
    assert gate.reached.wait(10)
    b.page.click("#job-close")  # run in background
    expect(b.page.locator("#job")).to_be_hidden()

    widget = b.boxes().first  # edits while the job runs: a move and a link
    b.drag(widget, 20, 0)
    b.page.select_option('#inspector select[data-ctl="path"]', "name")
    expect(b.page.locator("#history .fc-hi-list li")).to_have_count(2)
    b.idle()
    edited = b.template()

    gate.release()
    offer = b.page.locator("#offer")
    expect(offer).to_contain_text("finished: name added")
    expect(offer).to_contain_text("including any edits made since the job started")

    # other messages come and go; the offer stays
    b.page.click('#schemalist [data-path="name"]')  # a placed path: clears the message line
    b.page.click('#schemalist [data-path="note"]')  # no box uses it: a message
    expect(b.page.locator("#notice")).to_contain_text("No box uses note")
    page.route("**/api/apply", lambda route: route.fulfill(status=422, json={"error": "op_rejected", "detail": "not here"}))
    b.drag(b.boxes().first, 0, 15)
    expect(b.page.locator("#notice")).to_contain_text("That change wasn't made")
    page.unroute("**/api/apply")
    b.idle()
    assert b.template() == edited
    expect(offer).to_be_visible()

    b.page.click("#offer-load")
    expect(b.boxes(".is-ai")).to_have_count(1)
    expect(offer).to_be_hidden()
    b.idle()
    b.page.click("#undo")  # one step back: the template with the edits made during the run
    b.idle()
    assert b.template() == edited


def test_a_result_on_offer_can_be_discarded(start_demo_server, bench):
    gate = Gate()
    b = bench(start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, code(ADD_NOTE), finish()])))
    b.upload(widget_pdf())
    b.build_new()
    before = b.template()
    b.page.click("#build-ai")
    assert gate.reached.wait(10)
    b.page.click("#job-cancel")
    gate.release()
    expect(b.page.locator("#offer-load")).to_have_text("Load partial result")
    b.page.click("#offer-dismiss")
    expect(b.page.locator("#offer")).to_be_hidden()
    b.idle()
    assert b.template() == before


def test_the_job_panel_takes_and_keeps_focus_and_gives_it_back(start_demo_server, bench, page):
    page.add_init_script(GATE_APPLY)  # counts edits: none may reach the boxes behind the panel
    gate = Gate()
    both = [{"path": p, "reason": "test"} for p in ("name", "note")]
    b = bench(start_demo_server(provider_factory=Factory([gate, finish("nothing to add", both)])))
    b.upload(widget_pdf())
    b.build_new()
    b.boxes().first.focus()
    b.page.click("#build-ai")
    assert gate.reached.wait(10)
    inside = "document.activeElement && document.activeElement.closest('#job') !== null"
    b.page.wait_for_function(inside)
    for _ in range(6):
        b.page.keyboard.press("Tab")
        assert b.page.evaluate(inside)
    for key in ("ArrowRight", "Delete", "Control+z"):
        b.page.keyboard.press(key)
    assert b.page.locator("#bench").evaluate("el => el.inert") is True
    gate.release()
    expect(b.page.locator("#job-load")).to_be_visible()
    b.page.click("#job-load")
    expect(b.page.locator("#job")).to_be_hidden()
    b.page.wait_for_function("document.activeElement && document.activeElement.id === 'build-ai'")
    assert b.page.evaluate("window.__applyCalls") == 0
    assert b.page.locator("#bench").evaluate("el => el.inert") is False


def test_without_ai_configured_build_with_ai_is_hidden_and_a_note_says_how_to_turn_it_on(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    expect(b.page.locator("#match")).to_be_visible()
    expect(b.page.locator("#adapt")).to_be_hidden()
    b.page.click("#build-new")
    b.page.wait_for_function("window.__workbench.template !== null")
    expect(b.page.locator("#build-ai")).to_be_hidden()
    expect(b.page.locator("#ai-note")).to_be_visible()
    expect(b.page.locator("#ai-note")).to_contain_text("FORMCAST_DEMO_BASE_URL")
    b.drag_on_page(0.05, 0.02, 0.3, 0.06)
    expect(b.page.locator("#ask")).to_be_hidden()  # no area select for the AI


@pytest.mark.parametrize("width", [1280, 1440])
def test_the_ai_off_note_wraps_under_the_toolbar_without_overflow(demo_server, bench, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    b = bench(demo_server)
    b.upload(widget_pdf())
    b.build_new()
    expect(page.locator("#ai-note")).to_be_visible()
    m = page.evaluate(
        """() => {
          const note = document.getElementById("ai-note");
          const range = document.createRange();
          range.selectNodeContents(note);
          const text = range.getBoundingClientRect();
          const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.bottom)));
          return {doc: document.documentElement.scrollWidth, inner: innerWidth, scroll: note.scrollWidth, client: note.clientWidth,
                  left: text.left, right: text.right, lines: lines.size,
                  toolbar: document.querySelector(".tb-right").getBoundingClientRect().right};
        }"""
    )
    assert m["doc"] <= m["inner"] and m["scroll"] <= m["client"], m
    assert m["right"] <= m["toolbar"] and m["right"] - m["left"] <= 841, m  # inside the toolbar, at a reading measure
    assert m["lines"] >= 2, m  # it wraps rather than running across the window


# ------------------------------------------------------------------ fill, save


def pdf_values(data: bytes) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [w.field_value for p in doc for w in p.widgets() or []]
    finally:
        doc.close()


def test_fill_from_a_stored_record_shows_the_filled_pdf_and_download_saves_it(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template !== null")
    b.page.click("#fill-open")
    expect(b.page.locator("#fill")).to_be_visible()
    expect(b.page.locator("#fill-schema")).to_have_value("fw9")
    expect(b.page.locator("#fill-record option[value='fw9-01']")).to_have_count(1)  # in CSV and SQLite, listed once
    expect(b.page.locator("#fill-download")).to_be_disabled()
    b.page.select_option("#fill-record", "fw9-01")
    first_title = b.page.eval_on_selector("#fill-record", "s => s.selectedOptions[0].textContent")
    b.page.click("#fill-go")
    b.filled(first_title)
    expect(b.page.locator("#report .fc-rp-counts")).to_be_visible()
    first = b.download("#fill-download")
    assert first.startswith(b"%PDF")

    # another record fills again, with no Clear in between
    with b.page.expect_request("**/api/fill") as request:
        b.page.select_option("#fill-record", "fw9-02")
    assert request.value.post_data_json["record_ref"] == {"schema": "fw9", "id": "fw9-02"}
    second_title = b.page.eval_on_selector("#fill-record", "s => s.selectedOptions[0].textContent")
    b.filled(second_title)
    second = b.download("#fill-download")
    assert second != first and pdf_values(second) != pdf_values(first)

    b.page.click("#fill-clear")
    b.cleared()
    smart = b.download("#smart-go")  # the fillable empty PDF still downloads straight away
    assert smart.startswith(b"%PDF")


def test_an_edit_or_closing_the_drawer_ends_the_filled_preview(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template !== null")
    box(b.page, "w0_0").click()  # an edit to undo later: the box's Delete key
    b.page.keyboard.press("Delete")
    expect(box(b.page, "w0_0")).to_have_count(0)
    b.idle()
    b.page.click("#fill-open")
    b.page.select_option("#fill-record", "fw9-01")
    b.page.click("#fill-go")
    b.filled()
    b.page.click("#fill-close")
    b.cleared()
    expect(b.boxes().first).to_be_visible()

    b.page.click("#fill-open")
    b.page.select_option("#fill-record", "fw9-01")
    b.page.click("#fill-go")
    b.filled()
    b.page.click("#undo")  # any template change ends the preview
    b.cleared()
    expect(box(b.page, "w0_0")).to_be_visible()


def test_clear_while_a_fill_runs_keeps_the_form_on_the_page(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template !== null")
    b.page.click("#fill-open")
    b.page.select_option("#fill-record", "fw9-01")
    b.page.click("#fill-go")
    b.filled()
    held = []
    b.page.route("**/api/fill", lambda route: held.append(route))
    b.page.select_option("#fill-record", "fw9-02")  # fills again; the server's answer is held
    b.page.wait_for_function("document.getElementById('fill-go').disabled")
    b.page.click("#fill-clear")
    b.cleared()
    for _ in range(100):  # the held request reaches the route handler while Playwright runs
        if held:
            break
        b.page.wait_for_timeout(50)
    assert len(held) == 1
    held[0].continue_()
    b.page.wait_for_function("!document.getElementById('fill-go').disabled")
    b.page.wait_for_timeout(300)
    assert not b.page.evaluate("document.getElementById('pageview').classList.contains('is-filled')")
    expect(b.page.locator("#fill-download")).to_be_disabled()


def test_a_filled_pdf_the_browser_cannot_draw_still_downloads(demo_server, bench):
    doc = pymupdf.open(stream=widget_pdf())
    for _ in range(50):  # 51 pages: more than the browser's reader takes (MAX_PAGES)
        doc.new_page(width=300, height=200)
    data = doc.tobytes()
    doc.close()
    b = bench(demo_server)
    b.upload(data, "long.pdf")
    b.build_new()
    b.page.click("#fill-open")
    b.page.click("#fill-go")
    expect(b.page.locator("#fill-msg")).to_contain_text("the preview couldn't be drawn")
    expect(b.page.locator("#fill-msg")).to_contain_text("Download saves the filled PDF")
    assert not b.page.evaluate("document.getElementById('pageview').classList.contains('is-filled')")
    pdf = b.download("#fill-download")
    assert pymupdf.open(stream=pdf).page_count == 51


def test_a_pasted_schema_fills_from_an_editable_sample_record_never_from_another_schemas_records(demo_server, bench):
    b = bench(demo_server)
    b.upload(widget_pdf())  # schema pasted on the start screen: no stored schema is its own
    b.build_new()
    b.page.click("#fill-open")
    expect(b.page.locator("#fill")).to_be_visible()
    options = b.page.locator("#fill-schema option").evaluate_all("os => os.map((o) => o.value)")
    assert options == ["__sample__"]  # no stored schema's records on offer
    expect(b.page.locator("#fill-record-group")).to_be_hidden()
    expect(b.page.locator("#fill-sample")).to_be_visible()
    sample = json.loads(b.page.locator("#fill-sample").input_value())
    assert set(sample) == {"name", "note"} and all(isinstance(v, str) and v for v in sample.values())

    b.page.fill("#fill-sample", "{not json")
    b.page.click("#fill-go")
    expect(b.page.locator("#fill-msg")).to_contain_text("isn't valid JSON")

    edited = {"name": "Ada Example", "note": "edited by hand"}
    b.page.fill("#fill-sample", json.dumps(edited))
    with b.page.expect_request("**/api/fill") as request:
        b.page.click("#fill-go")
    assert request.value.post_data_json["record"] == edited and "record_ref" not in request.value.post_data_json
    b.filled("the sample record")
    assert b.download("#fill-download").startswith(b"%PDF")
    expect(b.page.locator("#report .fc-rp-counts")).to_be_visible()

    # a stored schema's template offers its own records first, and the sample too
    b.page.click("#fill-close")
    b.page.click("#doc-name")
    b.open_form("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template?.form.page_count === 6")
    b.page.click("#fill-open")
    options = b.page.locator("#fill-schema option").evaluate_all("os => os.map((o) => o.value)")
    assert options == ["fw9", "__sample__"]
    expect(b.page.locator("#fill-schema")).to_have_value("fw9")
    expect(b.page.locator("#fill-record-group")).to_be_visible()
    expect(b.page.locator("#fill-sample-group")).to_be_hidden()


def test_fill_with_a_template_for_another_revision_offers_rebind_and_fill_anyway(demo_server, bench):
    b = bench(demo_server)
    b.upload(FW9_EDITED, "fw9_rev.pdf", schema_text=None)
    expect(b.page.locator("#match-text")).to_contain_text("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template !== null")
    b.page.click("#fill-open")
    b.page.select_option("#fill-record", "fw9-02")
    b.page.click("#fill-go")
    expect(b.page.locator("#fill-mismatch")).to_be_visible()
    expect(b.page.locator("#fill-mismatch")).to_contain_text("different version")
    b.page.click("#rebind")  # one undo step, then the fill runs again
    b.filled()
    assert b.download("#fill-download").startswith(b"%PDF")
    expect(b.page.locator("#fill-mismatch")).to_be_hidden()
    b.idle()
    assert b.template()["form"]["fingerprint"] != get_json(demo_server, "/api/templates/fw9")[1]["template"]["form"]["fingerprint"]

    b.page.click("#undo")  # back to the template as stored: the preview ends and the check refuses again
    b.cleared()
    b.idle()
    b.page.click("#fill-go")
    expect(b.page.locator("#fill-mismatch")).to_be_visible()
    b.page.click("#fill-anyway")
    b.filled()
    assert b.download("#fill-download").startswith(b"%PDF")
    expect(b.page.locator("#fill-msg")).to_contain_text("filled anyway")
    # another record keeps filling anyway, without asking again
    b.page.select_option("#fill-record", "fw9-01")
    b.page.wait_for_function("document.getElementById('caption').textContent.includes(document.getElementById('fill-record').selectedOptions[0].textContent)")
    expect(b.page.locator("#fill-mismatch")).to_be_hidden()


def test_save_puts_the_template_in_the_store_and_it_is_matched_after_a_reload(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.build_new()
    b.page.click("#save")
    expect(b.page.locator("#save-box")).to_be_visible()
    b.page.fill("#save-name", "fw9 mine")
    b.page.click("#save-go")
    expect(b.page.locator("#notice")).to_contain_text("fw9 mine")
    names = [t["name"] for t in get_json(demo_server, "/api/templates")[1]["templates"]]
    assert "fw9 mine" in names

    b.page.reload()
    expect(b.page.locator("#form-pick option[value='fw9']")).to_have_count(1)
    b.open_form("fw9")
    expect(b.page.locator("#match")).to_be_visible()
    expect(b.page.locator("#match-pick option[value='fw9 mine']")).to_have_count(1)


# ------------------------------------------------------------------ layout and rotation


def test_at_900_px_wide_the_columns_do_not_overlap(demo_server, bench, page):
    page.set_viewport_size({"width": 900, "height": 800})
    b = bench(demo_server)
    b.upload(widget_pdf())
    b.build_new()
    boxes = {k: page.locator(k).bounding_box() for k in ("#col-schema", "#col-page", "#col-side")}

    def overlap(p, q):
        apart_x = p["x"] + p["width"] - 1 <= q["x"] or q["x"] + q["width"] - 1 <= p["x"]
        apart_y = p["y"] + p["height"] - 1 <= q["y"] or q["y"] + q["height"] - 1 <= p["y"]
        return not (apart_x or apart_y)

    names = list(boxes)
    for i, m in enumerate(names):
        assert boxes[m]["width"] > 200
        for n in names[i + 1 :]:
            assert not overlap(boxes[m], boxes[n]), (m, n, boxes)
    assert page.evaluate("document.documentElement.scrollWidth") <= 900


def test_on_the_stored_w9_the_checkbox_tags_stay_apart_and_the_selected_one_shows_whole(demo_server, bench, page):
    page.set_viewport_size({"width": 1440, "height": 900})
    b = bench(demo_server)
    b.open_form("fw9")
    page.click("#reuse")
    row = ["w0_2", "w0_3", "w0_4", "w0_5", "w0_6"]
    expect(box(page, "w0_6")).to_be_visible()
    rects = lambda: page.evaluate(  # noqa: E731
        "ids => ids.map((id) => { const t = document.querySelector(`#pageview .fc-box[data-field-id='${id}'] .fc-tag`);"
        " const r = t.getBoundingClientRect(); return [r.left, r.right, t.scrollWidth > t.clientWidth]; })",
        row,
    )
    tags = rects()
    for (_, right, _), (left, _, _) in zip(tags, tags[1:]):
        assert right <= left, tags  # left to right along the row, no overlap
    box(page, "w0_3").click()
    left, right, cut = rects()[1]
    assert not cut and right > tags[2][0]  # the selected tag is whole, reaching past where the next one starts


def test_on_a_rotated_page_the_box_sits_over_the_painted_rect(demo_server, bench):
    """/Rotate 90: the server turns the image and geometry.js turns the box; both must land on the same spot."""
    red = (0.9, 0.1, 0.1)
    b = bench(demo_server)
    b.upload(widget_pdf(width=300, height=500, rect=(40, 60, 140, 110), rotation=90, fill=red))
    b.build_new()
    box = b.boxes().first
    expect(box).to_be_visible()
    b.page.wait_for_function("(m => m && m.complete && m.naturalWidth > 0)(document.querySelector('#pageview .fc-pv-media'))")
    sample = b.page.evaluate("""
      () => {
        const img = document.querySelector('#pageview .fc-pv-media');
        const box = document.querySelector('#pageview .fc-box').getBoundingClientRect();
        const at = img.getBoundingClientRect();
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const fx = (box.left + box.width / 2 - at.left) / at.width;
        const fy = (box.top + box.height / 2 - at.top) / at.height;
        const px = ctx.getImageData(Math.round(fx * img.naturalWidth), Math.round(fy * img.naturalHeight), 1, 1).data;
        return {rgb: [px[0], px[1], px[2]], boxW: box.width, boxH: box.height, imgW: at.width, imgH: at.height};
      }
    """)
    r, g, bl = sample["rgb"]
    assert r > 180 and g < 90 and bl < 90, sample
    assert sample["imgW"] > sample["imgH"]  # the turned page is landscape
    assert sample["boxH"] > sample["boxW"]  # and the 100 x 50 pt rect stands upright on it
