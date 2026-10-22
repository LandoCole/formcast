"""The showcase page's tool (layout C) on the browser engine, in Chromium.

The page reads its samples from ``?samples=/tests/web/fixtures/`` (the hand-made bundle that
``tests/web/make_sample_bundle.py`` builds from ``forms/dev/fw9.pdf``) and detects with
``?worker=`` pointing at ``tests/web/harness/fake-detect-worker.js``, which answers with five
canned boxes and downloads nothing. Every network request is recorded so the tests can check
what loaded when; Google Fonts are answered with an empty stylesheet so no test needs the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import pymupdf
import pytest

from formcast.geometry import iou
from formcast.template.model import Template

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "tests" / "web" / "fixtures" / "sample-bundle"
SAMPLES = "/tests/web/fixtures/"
FAKE_WORKER = "/tests/web/harness/fake-detect-worker.js"
CANNED_BOXES = 5  # what fake-detect-worker.js answers per page
PAGE = "/demo/web/showcase/index.html"

DESKTOP = {"viewport": {"width": 1280, "height": 900}}
PHONE = {"viewport": {"width": 375, "height": 812}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2}

LAZY = re.compile(r"huggingface\.co|onnxruntime-web|pdf-lib|detect-worker|fake-detect-worker|engines/browser/detect\.js")
MODEL = re.compile(r"huggingface\.co|onnxruntime-web|fake-detect-worker|detect-worker\.js")
PROTECTED = "This PDF is protected against changes, so it can't be filled in the browser. The full library can fill it."


def bundle_json(name: str):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


class Tool:
    """One open showcase page: the Playwright page plus what it requested and logged."""

    def __init__(self, page, requests: list[str], errors: list[str]):
        self.page = page
        self.requests = requests
        self.errors = errors

    def requested(self, pattern: re.Pattern) -> list[str]:
        """Requested URLs whose host and path (not query, which names the fake worker) match."""
        return [u for u in self.requests if pattern.search(u.split("?", 1)[0])]

    def boxes(self):
        return self.page.locator(".fc-box")

    def wait_boxes(self, count: int | None = None, timeout: int = 10_000):
        if count is None:
            self.page.wait_for_selector(".fc-box", timeout=timeout)
        else:
            self.page.wait_for_function("n => document.querySelectorAll('.fc-box').length === n", arg=count, timeout=timeout)

    def step(self, name: str):
        self.page.click(f"#tab-{name}")

    def drop(self, name: str, data: bytes, mime: str = "application/pdf"):
        self.page.set_input_files("#file", files=[{"name": name, "mimeType": mime, "buffer": data}])

    def record_status(self):
        """Start keeping every text #detect-status shows, in window.statusSeen."""
        self.page.evaluate("""() => {
            window.statusSeen = [];
            const el = document.getElementById('detect-status');
            new MutationObserver(() => window.statusSeen.push(el.textContent))
              .observe(el, {childList: true, characterData: true, subtree: true});
        }""")

    def no_horizontal_scroll(self):
        # compared with the configured width: a mobile layout viewport grows to fit overflowing content
        width = self.page.viewport_size["width"]
        assert self.page.evaluate("innerWidth") == width
        scroll = self.page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
        assert scroll <= width, f"page scrolls sideways: {scroll} > {width}"


@pytest.fixture
def open_tool(server, browser):
    """``open_tool(profile=DESKTOP, worker=FAKE_WORKER, samples=SAMPLES, **context options)`` -> Tool."""
    contexts = []

    def _open(profile=DESKTOP, worker=FAKE_WORKER, samples=SAMPLES, wait=True, timeout=None, init_script=None, **options):
        context = browser.new_context(**profile, accept_downloads=True, **options)
        contexts.append(context)
        context.route(
            re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"),
            lambda route: route.fulfill(status=200, body="", content_type="text/css"),
        )
        context.route(re.compile(r"https://huggingface\.co/.*"), lambda route: route.abort())
        requests: list[str] = []
        errors: list[str] = []
        context.on("request", lambda req: requests.append(req.url))
        if init_script is not None:
            context.add_init_script(init_script)
        page = context.new_page()
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        query = []
        if samples is not None:
            query.append(f"samples={quote(samples)}")
        if worker is not None:
            query.append(f"worker={quote(worker)}")
        if timeout is not None:
            query.append(f"timeout={timeout}")
        page.goto(f"{server}{PAGE}?{'&'.join(query)}")
        tool = Tool(page, requests, errors)
        if wait:
            tool.wait_boxes()
        return tool

    yield _open
    for context in contexts:
        context.close()


def greedy_matches(originals, boxes, thr=0.5) -> int:
    pairs = sorted(
        ((iou(o, b), i, j) for i, o in enumerate(originals) for j, b in enumerate(boxes) if iou(o, b) >= thr),
        reverse=True,
    )
    used_o, used_b = set(), set()
    for _, i, j in pairs:
        if i not in used_o and j not in used_b:
            used_o.add(i)
            used_b.add(j)
    return len(used_o)


def user_password_pdf() -> bytes:
    doc = pymupdf.open(BUNDLE / "form.pdf")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="visitor")
    doc.close()
    return data


def link_first_box(tool: Tool, path: str) -> str:
    """Select the first box and link it to ``path`` in the inspector; returns the field id."""
    box = tool.boxes().first
    field_id = box.get_attribute("data-field-id")
    box.click()
    tool.page.select_option("#inspector select[data-ctl=path]", path)
    tag = tool.page.locator(f'.fc-box[data-field-id="{field_id}"] .fc-tag')
    tag.wait_for()
    assert tag.inner_text() == path
    return field_id


# ---------------------------------------------------------------- load


def test_first_paint_shows_the_sample_with_boxes_fast_and_nothing_heavy(open_tool):
    tool = open_tool(wait=False)
    tool.page.wait_for_selector(".fc-box", timeout=2000)
    tool.page.wait_for_selector(".fc-pv-media", timeout=2000)
    assert tool.page.evaluate("performance.now()") < 2000
    expected = len(bundle_json("detections.json")["pages"]["0"])
    assert tool.boxes().count() == expected
    assert "FFDNet-S" in tool.page.inner_text("#caption")
    tool.page.wait_for_timeout(300)
    assert tool.requested(LAZY) == []
    assert tool.errors == []


def test_steps_panel_starts_on_detect(open_tool):
    tool = open_tool()
    tabs = tool.page.locator("[role=tab]")
    assert [t.strip() for t in tabs.all_text_contents()] == ["1Detect", "2Link", "3Fill"]
    assert tool.page.locator("[role=tab][aria-selected=true]").text_content().endswith("Detect")
    assert tool.page.is_visible("#step-detect")
    assert not tool.page.is_visible("#step-link")
    assert not tool.page.is_visible("#step-fill")


def test_missing_samples_say_so_and_leave_the_drop_path(open_tool):
    tool = open_tool(samples="/tests/web/no-such-samples/", wait=False)
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert "couldn't be loaded" in msg.inner_text()
    assert tool.page.is_visible("#stage-empty")


# ---------------------------------------------------------------- detect


def test_original_fields_are_drawn_and_counted(open_tool):
    tool = open_tool()
    originals = bundle_json("original-fields.json")["pages"]["0"]
    boxes = [d["rect"] for d in bundle_json("detections.json")["pages"]["0"]]
    n = greedy_matches(originals, boxes)
    assert 0 < n < len(originals)  # the bundle leaves some out on purpose
    assert tool.page.locator(".fc-compare").count() == 0
    tool.page.check("#compare")
    assert tool.page.locator(".fc-compare").count() == len(originals)
    assert f"found {n} of {len(originals)}" in tool.page.inner_text("#found")
    tool.page.uncheck("#compare")
    assert tool.page.locator(".fc-compare").count() == 0


def test_run_live_shows_progress_then_replaces_the_boxes(open_tool):
    tool = open_tool()
    tool.record_status()
    tool.page.click("#run-live")
    tool.wait_boxes(CANNED_BOXES)
    caption = tool.page.inner_text("#caption")
    assert "Detected live by FFDNet-S" in caption
    assert re.search(r"\d+ ms per page", caption)
    seen = tool.page.evaluate("window.statusSeen")
    assert any("MB" in s for s in seen), seen
    assert any("Detecting" in s for s in seen), seen
    assert tool.requested(re.compile("fake-detect-worker"))
    assert not tool.page.is_disabled("#run-live")
    # undo brings back the boxes found ahead of time
    tool.step("link")
    tool.page.click(".fc-history [data-action=undo]")
    tool.wait_boxes(len(bundle_json("detections.json")["pages"]["0"]))
    assert tool.errors == []


def test_failed_live_detection_keeps_the_boxes_and_says_why(open_tool):
    tool = open_tool(worker=f"{FAKE_WORKER}?mode=fail")
    before = tool.boxes().count()
    tool.page.click("#run-live")
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert "could not be downloaded" in msg.inner_text()
    assert tool.boxes().count() == before
    assert not tool.page.is_disabled("#run-live")


def test_dropped_files_bad_and_good(open_tool):
    tool = open_tool()
    before = tool.boxes().count()
    msg = tool.page.locator("#detect-msg")

    tool.drop("notes.txt", b"just some text\n", "text/plain")
    msg.wait_for()
    assert msg.inner_text() == "This file isn't a PDF."
    assert tool.boxes().count() == before

    tool.drop("locked.pdf", user_password_pdf())
    tool.page.wait_for_function("document.getElementById('detect-msg').textContent.includes('password')")
    assert tool.boxes().count() == before
    assert "IRS W-9" in tool.page.inner_text("#docline")

    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    tool.wait_boxes(CANNED_BOXES)
    assert msg.is_hidden()
    assert "form.pdf" in tool.page.inner_text("#docline")
    assert "Detected live" in tool.page.inner_text("#caption")
    assert tool.requested(re.compile(r"huggingface\.co|onnxruntime-web")) == []


def test_owner_protected_pdf_opens_but_fill_refuses(open_tool):
    tool = open_tool()
    tool.drop("wadoc_auth.pdf", (ROOT / "forms" / "dev" / "wadoc_auth.pdf").read_bytes())
    tool.wait_boxes(CANNED_BOXES)
    tool.step("fill")
    tool.page.click("#fill")
    msg = tool.page.locator("#fill-msg")
    msg.wait_for()
    assert msg.inner_text() == PROTECTED
    tool.page.click("#smart")
    assert msg.inner_text() == PROTECTED
    assert tool.requested(re.compile("pdf-lib")) == []


# ---------------------------------------------------------------- link


def test_link_undo_redo(open_tool):
    tool = open_tool()
    field_id = link_first_box(tool, "legal_name")
    assert tool.page.locator("[role=tab][aria-selected=true]").text_content().endswith("Link")
    tag = tool.page.locator(f'.fc-box[data-field-id="{field_id}"] .fc-tag')
    tool.page.click(".fc-history [data-action=undo]")
    tool.page.wait_for_function(
        "id => document.querySelector(`.fc-box[data-field-id='${id}'] .fc-tag`).textContent === 'not linked'", arg=field_id
    )
    tool.page.click(".fc-history [data-action=redo]")
    tool.page.wait_for_function(
        "id => document.querySelector(`.fc-box[data-field-id='${id}'] .fc-tag`).textContent === 'legal_name'", arg=field_id
    )
    assert tag.inner_text() == "legal_name"
    assert "1 linked" in tool.page.inner_text("#caption")
    assert tool.errors == []


def test_pasted_schemas(open_tool):
    tool = open_tool()
    field_id = link_first_box(tool, "legal_name")
    tool.page.click("#paste summary")
    error = tool.page.locator("#schema-error")

    tool.page.fill("#schema-text", "{not json")
    tool.page.click("#use-schema")
    error.wait_for()
    assert "isn't valid JSON" in error.inner_text()
    assert tool.page.locator('.fc-sl-leaf[data-path="legal_name"]').count() == 1
    assert tool.page.locator('.fc-sl-leaf[data-path="legal_name"]').get_attribute("data-status") == "placed"

    tool.page.fill("#schema-text", json.dumps({"type": "array"}))
    tool.page.click("#use-schema")
    tool.page.wait_for_function("document.getElementById('schema-error').textContent.includes(\"can't be used\")")

    schema = {"type": "object", "properties": {"full_name": {"type": "string"}, "city": {"type": "string"}}}
    tool.page.fill("#schema-text", json.dumps(schema))
    tool.page.click("#use-schema")
    tool.page.locator('.fc-sl-leaf[data-path="full_name"]').wait_for()
    assert error.is_hidden()
    leaves = tool.page.locator(".fc-sl-leaf")
    assert leaves.evaluate_all("els => els.map(e => [e.dataset.path, e.dataset.status])") == [
        ["full_name", "none"],
        ["city", "none"],
        ["legal_name", "orphan"],
    ]
    # the linked box keeps its link, flagged in the inspector
    tool.page.click(f'.fc-box[data-field-id="{field_id}"]')
    assert tool.page.locator("#inspector .fc-ins-warn.is-orphan").is_visible()
    selected = tool.page.eval_on_selector("#inspector select[data-ctl=path]", "s => s.selectedOptions[0].textContent")
    assert selected == "legal_name (not in schema)"
    assert tool.page.locator(f'.fc-box[data-field-id="{field_id}"] .fc-tag').inner_text() == "legal_name"
    # the record follows the new schema
    record = json.loads(tool.page.input_value("#record"))
    assert set(record) == {"full_name", "city"}


# ---------------------------------------------------------------- fill


def test_fill_report_and_templates(open_tool, tmp_path):
    tool = open_tool()
    link_first_box(tool, "legal_name")
    tool.step("fill")
    assert json.loads(tool.page.input_value("#record")) == bundle_json("record.json")
    assert tool.requested(re.compile("pdf-lib")) == []

    with tool.page.expect_download() as dl:
        tool.page.click("#fill")
    data = Path(dl.value.path()).read_bytes()
    assert data.startswith(b"%PDF")
    assert dl.value.suggested_filename == "fw9-filled.pdf"
    tool.page.locator("#report .fc-rp-counts").wait_for()
    assert tool.requested(re.compile("pdf-lib"))
    filled = pymupdf.open(stream=data, filetype="pdf")
    values = [w.field_value for p in filled for w in p.widgets() or []]
    assert bundle_json("record.json")["legal_name"] in values

    with tool.page.expect_download() as dl:
        tool.page.click("#save-template")
    saved = Path(dl.value.path()).read_text(encoding="utf-8")
    template = Template.model_validate_json(saved)
    assert template.form.page_count == 6

    path = tmp_path / "mine.json"
    path.write_text(saved, encoding="utf-8")
    tool.page.set_input_files("#template-file", str(path))
    msg = tool.page.locator("#fill-msg")
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.startsWith('Template loaded')")

    other = ROOT / "tests" / "web" / "fixtures" / "templates" / "wadoc_auth_as_published.json"
    tool.page.set_input_files("#template-file", str(other))
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.startsWith('This template is for')")
    assert msg.inner_text() == "This template is for a 1-page form; this PDF has 6 pages."

    with tool.page.expect_download() as dl:
        tool.page.click("#smart")
    assert Path(dl.value.path()).read_bytes().startswith(b"%PDF")
    assert tool.errors == []


def test_bad_record_json_is_reported(open_tool):
    tool = open_tool()
    tool.step("fill")
    tool.page.fill("#record", "{oops")
    tool.page.click("#fill")
    error = tool.page.locator("#record-error")
    error.wait_for()
    assert "isn't valid JSON" in error.inner_text()


# ---------------------------------------------------------------- the agent's template


def test_agent_template_opens_on_the_published_pdf(open_tool):
    tool = open_tool()
    template = bundle_json("agent-template.json")
    scores = bundle_json("agent-scores.json")
    on_page0 = [f for f in template["fields"] if f.get("page") == 0]
    tool.page.click("#load-agent")
    tool.wait_boxes(len(on_page0))
    linked = [f for f in on_page0 if (f.get("source") or {}).get("paths")]
    assert tool.page.locator(".fc-box.is-linked").count() == len(linked)
    first = linked[0]
    tag = tool.page.locator(f'.fc-box[data-field-id="{first["id"]}"] .fc-tag').inner_text()
    assert tag == ", ".join(first["source"]["paths"])
    assert "as published" in tool.page.inner_text("#docline")
    line = tool.page.locator("#agent-scores")
    assert line.is_visible()
    text = line.inner_text()
    assert scores["model_label"] in text
    assert f"path accuracy {scores['mapping']['path_acc']:.2f}" in text
    assert f"fill accuracy {scores['fill']['fill_acc']:.2f}" in text
    assert f"{scores['steps']} steps" in text
    assert "The AI agent's template" in tool.page.inner_text("#caption")
    assert tool.errors == []


def test_edits_are_remembered_per_sample(open_tool):
    tool = open_tool()
    link_first_box(tool, "legal_name")
    tool.page.reload()
    tool.wait_boxes()
    assert "1 linked" in tool.page.inner_text("#caption")
    assert tool.page.is_visible("#start-over")
    tool.page.click("#start-over")
    tool.page.wait_for_function("document.getElementById('caption').textContent.includes('0 linked')")


# ---------------------------------------------------------------- phones and themes


def test_phone_layout(open_tool):
    tool = open_tool(PHONE)
    page = tool.page
    tool.no_horizontal_scroll()
    fig = page.locator("#fig").bounding_box()
    panel = page.locator(".panel").bounding_box()
    assert fig["y"] + fig["height"] <= panel["y"] + 1
    assert page.locator("#model-group").is_hidden()
    # select the small box nearest the page's right edge: its handles and tag stay inside the page width
    right = page.evaluate("""() => {
        const small = [...document.querySelectorAll('.fc-box')]
          .map(el => ({id: el.dataset.fieldId, r: el.getBoundingClientRect()}))
          .filter(b => b.r.width < 48 || b.r.height < 48);
        small.sort((a, b) => b.r.right - a.r.right);
        return small[0].id;
    }""")
    page.click(f'.fc-box[data-field-id="{right}"]')
    assert page.locator(f'.fc-box[data-field-id="{right}"].is-selected .fc-hd').count() == 4
    tool.no_horizontal_scroll()
    # a long tag on that box runs past the page's right edge; the stage clips it
    page.select_option("#inspector select[data-ctl=path]", "llc_tax_classification")
    tag = page.locator(f'.fc-box[data-field-id="{right}"] .fc-tag')
    page.wait_for_function("el => el.textContent === 'llc_tax_classification'", arg=tag.element_handle())
    assert tag.bounding_box()["x"] + tag.bounding_box()["width"] > page.viewport_size["width"]
    tool.no_horizontal_scroll()
    assert tool.requested(LAZY) == []


def test_phone_flat_pdf_waits_for_try_anyway(open_tool):
    tool = open_tool(PHONE)
    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    note = tool.page.locator("#live-note")
    note.wait_for()
    assert "Live detection is off on this device" in note.inner_text()
    assert "38 MB" in note.inner_text()
    assert tool.page.locator("#try-anyway").is_visible()
    tool.page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []
    assert tool.boxes().count() == 0
    tool.page.click("#try-anyway")
    tool.wait_boxes(CANNED_BOXES)
    assert tool.requested(re.compile("fake-detect-worker"))
    assert note.is_hidden()


def test_phone_fillable_pdf_uses_its_own_fields(open_tool):
    tool = open_tool(PHONE)
    tool.drop("published.pdf", (BUNDLE / "published.pdf").read_bytes())
    doc = pymupdf.open(BUNDLE / "published.pdf")
    widgets = len([w for w in doc[0].widgets() or [] if w.field_type != pymupdf.PDF_WIDGET_TYPE_BUTTON])
    doc.close()
    tool.wait_boxes(widgets)
    assert "own form fields" in tool.page.inner_text("#caption")
    assert tool.page.locator("#live-note").is_hidden()
    tool.page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []


def test_dark_mode(open_tool):
    tool = open_tool(color_scheme="dark")
    bg = tool.page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(16, 15, 15)"
    light = open_tool(color_scheme="light")
    assert light.page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 252, 240)"


def test_phone_run_live_asks_before_downloading(open_tool):
    tool = open_tool(PHONE)
    before = tool.boxes().count()
    tool.page.click("#run-live")
    note = tool.page.locator("#live-note")
    note.wait_for()
    tool.page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []
    assert tool.boxes().count() == before
    tool.page.click("#try-anyway")
    tool.wait_boxes(CANNED_BOXES)
    assert note.is_hidden()
    assert tool.requested(re.compile("fake-detect-worker"))


LOW_MEMORY = "Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 2, configurable: true});"


def test_low_memory_desktop_keeps_mouse_tools_but_asks_before_downloading(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    page = tool.page
    assert page.evaluate("navigator.deviceMemory") == 2
    assert page.locator("#model-group").is_visible()
    tool.step("link")
    assert page.locator('.fc-sl-leaf[data-path="legal_name"]').get_attribute("draggable") == "true"
    assert page.inner_text("#link-hint").startswith("Drag a field onto a box")
    tool.step("detect")
    page.select_option("#model", "L")
    page.click("#run-live")
    note = page.locator("#live-note")
    note.wait_for()
    assert "102 MB" in note.inner_text()
    page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []
    page.select_option("#model", "S")
    page.click("#try-anyway")
    tool.wait_boxes(CANNED_BOXES)
    # once accepted, the next run doesn't ask again
    page.wait_for_function("document.getElementById('detect-status').textContent.startsWith('Done')")
    page.click("#run-live")
    page.wait_for_function("document.getElementById('detect-status').textContent.startsWith('Done')")
    assert note.is_hidden()


def test_pdf_dropped_during_live_detection_is_detected(open_tool):
    tool = open_tool()
    tool.page.click("#run-live")  # the fake worker takes 500 ms to load
    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    tool.page.wait_for_function("document.getElementById('detect-status').textContent.startsWith('Done')", timeout=15_000)
    assert "form.pdf" in tool.page.inner_text("#docline")
    tool.wait_boxes(CANNED_BOXES)
    assert "Detected live" in tool.page.inner_text("#caption")
    assert not tool.page.is_disabled("#run-live")
    assert tool.page.locator("#detect-msg").is_hidden()
    assert tool.errors == []


def test_double_click_on_run_live_in_the_agent_view_runs_once(open_tool):
    tool = open_tool()
    tool.page.click("#load-agent")
    tool.page.wait_for_function("document.getElementById('docline').textContent.includes('as published')")
    tool.record_status()
    tool.page.dblclick("#run-live")
    assert tool.page.is_disabled("#run-live")
    tool.page.wait_for_function("document.getElementById('detect-status').textContent.startsWith('Done')", timeout=15_000)
    tool.wait_boxes(CANNED_BOXES)
    seen = tool.page.evaluate("window.statusSeen")
    assert seen.count("Opening the sample…") == 1, seen
    assert sum(s.startswith("Done") for s in seen) == 1, seen
    assert "fields stripped" in tool.page.inner_text("#docline")
    assert not tool.page.is_disabled("#run-live")


def test_a_broken_remembered_template_is_dropped(open_tool):
    tool = open_tool()
    expected = len(bundle_json("detections.json")["pages"]["0"])
    tool.page.evaluate("""() => {
        localStorage.setItem('formcast:template:sample-bundle/form', JSON.stringify({formcast_version: '1', fields: 'x'}));
    }""")
    tool.page.reload()
    tool.wait_boxes(expected)
    assert tool.page.locator("#detect-msg").is_hidden()
    assert "ahead of time" in tool.page.inner_text("#caption")
    assert tool.page.evaluate("localStorage.getItem('formcast:template:sample-bundle/form')") == "null"


def test_a_hung_sample_file_times_out_and_keeps_the_page(open_tool):
    tool = open_tool(timeout=500)
    tool.page.route(re.compile(r"agent-template\.json$"), lambda route: None)  # never answered
    before = tool.boxes().count()
    tool.page.click("#load-agent")
    msg = tool.page.locator("#detect-msg")
    msg.wait_for(timeout=5000)
    assert "took more than" in msg.inner_text()
    assert tool.boxes().count() == before
    assert "fields stripped" in tool.page.inner_text("#docline")
    assert tool.page.inner_text("#detect-status") == ""


HANG_OPEN = """(() => {
  const real = Blob.prototype.arrayBuffer;
  Blob.prototype.arrayBuffer = function () {
    return this.name === "hang.pdf" ? new Promise(() => {}) : real.call(this);
  };
})();"""
PART_FAILED = "Part of the page didn't load. Check the connection and try again."


def test_a_pdf_that_never_opens_times_out_and_keeps_the_page(open_tool):
    """A stand-in for a hung pdf.js open: the dropped file's bytes never arrive."""
    tool = open_tool(timeout=500, init_script=HANG_OPEN)
    before = tool.boxes().count()
    tool.drop("hang.pdf", (BUNDLE / "form.pdf").read_bytes())
    msg = tool.page.locator("#detect-msg")
    msg.wait_for(timeout=5000)
    assert msg.inner_text() == "This file took more than 1 second to open. Try again, or try a smaller PDF."
    assert tool.boxes().count() == before
    assert "IRS W-9" in tool.page.inner_text("#docline")
    assert tool.page.inner_text("#detect-status") == ""
    # the next file opens as usual
    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    tool.wait_boxes(CANNED_BOXES)
    assert msg.is_hidden()


def test_a_fill_that_never_finishes_times_out(open_tool):
    tool = open_tool(timeout=500)
    tool.page.route(re.compile(r"pdf-lib"), lambda route: None)  # pdf-lib never arrives
    tool.step("fill")
    msg = tool.page.locator("#fill-msg")
    tool.page.click("#fill")
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.includes('took more than')", timeout=5000)
    assert msg.inner_text() == "That didn't work: Filling took more than 1 second. Try again."
    assert not tool.page.is_disabled("#fill")
    tool.page.click("#smart")
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.includes('fillable PDF took')", timeout=5000)
    assert msg.inner_text() == "That didn't work: Making the fillable PDF took more than 1 second. Try again."


def test_a_part_that_fails_to_load_says_so_plainly(open_tool):
    tool = open_tool()
    tool.page.route(re.compile(r"pdf-lib"), lambda route: route.abort())
    tool.page.route(re.compile(r"engines/browser/detect\.js"), lambda route: route.abort())
    tool.step("fill")
    tool.page.click("#fill")
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.includes('load')", timeout=5000)
    assert tool.page.inner_text("#fill-msg") == f"That didn't work: {PART_FAILED}"
    tool.step("detect")
    before = tool.boxes().count()
    tool.page.click("#run-live")
    msg = tool.page.locator("#detect-msg")
    msg.wait_for(timeout=5000)
    assert PART_FAILED in msg.inner_text()
    assert "dynamically imported" not in msg.inner_text()
    assert tool.boxes().count() == before


# ---------------------------------------------------------------- the real sample bundles

REAL_SAMPLES = ROOT / "demo" / "web" / "showcase" / "samples"


@pytest.mark.parametrize("form_id", ["fw9", "fw4", "i9"])
def test_each_real_sample_loads_by_id_with_boxes_and_the_agent_template(open_tool, form_id):
    """The page's own samples/ folder (no ?samples=): pick the sample by id, then open the agent's file."""
    tool = open_tool(samples=None, worker=None, wait=False)
    tool.page.wait_for_function("document.querySelectorAll('#sample option').length >= 3")
    index = json.loads((REAL_SAMPLES / "index.json").read_text())
    assert [o for o in tool.page.locator("#sample option").evaluate_all("els => els.map(e => e.value)")] == [e["id"] for e in index]
    title = next(e["title"] for e in index if e["id"] == form_id)

    tool.page.select_option("#sample", form_id)
    detections = json.loads((REAL_SAMPLES / form_id / "detections.json").read_text())["pages"]
    originals = json.loads((REAL_SAMPLES / form_id / "original-fields.json").read_text())["pages"]
    tool.wait_boxes(len(detections["0"]))
    assert title in tool.page.inner_text("#docline")
    assert "fields stripped" in tool.page.inner_text("#docline")
    assert "FFDNet-S" in tool.page.inner_text("#caption")
    found = sum(greedy_matches(originals[p], [d["rect"] for d in detections[p]]) for p in originals)
    total = sum(len(v) for v in originals.values())
    assert 0 < found <= total
    assert f"found {found} of {total}" in tool.page.inner_text("#found")

    template = json.loads((REAL_SAMPLES / form_id / "agent-template.json").read_text())
    # a table is drawn as one box per segment on the page; any other field as one box
    drawn = sum(
        sum(seg["page"] == 0 for seg in f["segments"]) if f["type"] == "table" else int(f["page"] == 0 and bool(f["rect"]))
        for f in template["fields"]
    )
    tool.page.click("#load-agent")
    tool.page.wait_for_function("document.getElementById('docline').textContent.includes('as published')")
    tool.wait_boxes(drawn)
    scores = tool.page.locator("#agent-scores")
    scores.wait_for()
    assert "Haiku 5.5" in scores.inner_text()
    assert "2026-10-21_defer-placement" in scores.inner_text()
    assert tool.page.locator("#detect-msg").is_hidden()
    assert tool.requested(MODEL) == []
    assert tool.errors == []


def test_a_failed_sample_switch_keeps_the_picker_on_the_open_sample(open_tool):
    tool = open_tool(samples=None, worker=None, wait=False)
    index = json.loads((REAL_SAMPLES / "index.json").read_text())
    first, other = index[0]["id"], index[1]["id"]
    tool.wait_boxes()
    tool.page.route(re.compile(rf"/samples/{other}/manifest\.json$"), lambda route: route.abort())
    tool.page.select_option("#sample", other)
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert msg.inner_text().startswith("This sample couldn't be opened.")
    assert tool.page.input_value("#sample") == first
    assert index[0]["title"] in tool.page.inner_text("#docline")
