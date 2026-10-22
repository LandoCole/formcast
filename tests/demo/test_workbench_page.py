"""The workbench page (``demo/web/workbench/``, layout B) in Chromium against the real workbench server.

Each test starts the server in-process over a throwaway copy of ``demo/data`` (conftest ``start_demo_server``);
AI jobs run on the scripted fake provider from ``test_server_jobs`` (no network, no real model), and a ``Gate``
in a script holds a job mid-run. Form maps are built without detectors, so a seed is the form's own widgets.
``?poll=50`` makes the page poll jobs every 50 ms (honoured on localhost only).
"""

from __future__ import annotations

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
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf: real(pdf, detectors={}, policy=policy, ocr="never"))


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
    page.add_init_script("""
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
    """)
    b = bench(demo_server)
    b.upload(widget_pdf(rect=(20, 120, 120, 140)))
    b.build_new()
    page.click("#add-box")
    b.drag_on_page(0.6, 0.1, 0.9, 0.25)  # a second box, so there are two to move
    page.wait_for_function("window.__applyCalls === 1")
    page.evaluate("window.__releaseApply()")
    expect(b.boxes()).to_have_count(2)
    b.idle()
    a, c = b.boxes().nth(0), b.boxes().nth(1)
    a_id, c_id = a.get_attribute("data-field-id"), c.get_attribute("data-field-id")

    def left(fid: str) -> float:
        return float(page.locator(f'.fc-box[data-field-id="{fid}"]').evaluate("el => el.style.left").removesuffix("px"))

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
    expect(b.page.locator("#notice")).to_contain_text("cancelled")
    b.idle()
    assert b.template() == before
    expect(b.boxes(".is-ai")).to_have_count(0)
    b.page.click("#notice-action")
    expect(b.boxes(".is-ai")).to_have_count(2)  # the step in flight when the cancel came is in the partial result


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


# ------------------------------------------------------------------ fill, save


def test_fill_from_a_stored_record_downloads_a_pdf_and_shows_the_report(demo_server, bench):
    b = bench(demo_server)
    b.open_form("fw9")
    b.page.click("#reuse")
    b.page.wait_for_function("window.__workbench.template !== null")
    b.page.click("#fill-open")
    expect(b.page.locator("#fill")).to_be_visible()
    expect(b.page.locator("#fill-schema")).to_have_value("fw9")
    expect(b.page.locator("#fill-record option[value='fw9-01']")).to_have_count(1)  # in CSV and SQLite, listed once
    b.page.select_option("#fill-record", "fw9-01")
    pdf = b.download("#fill-go")
    assert pdf.startswith(b"%PDF")
    expect(b.page.locator("#report .fc-rp-counts")).to_be_visible()
    smart = b.download("#smart-go")
    assert smart.startswith(b"%PDF")


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
    pdf = b.download("#rebind")
    assert pdf.startswith(b"%PDF")
    expect(b.page.locator("#fill-mismatch")).to_be_hidden()
    b.idle()
    assert b.template()["form"]["fingerprint"] != get_json(demo_server, "/api/templates/fw9")[1]["template"]["form"]["fingerprint"]

    b.page.click("#undo")  # back to the template as stored: the check refuses again
    b.idle()
    b.page.click("#fill-go")
    expect(b.page.locator("#fill-mismatch")).to_be_visible()
    pdf = b.download("#fill-anyway")
    assert pdf.startswith(b"%PDF")
    expect(b.page.locator("#fill-msg")).to_contain_text("filled anyway")


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
