"""The showcase page's tool (layout C) on the browser engine, in Chromium: the guided flow.

A sample opens blank (its fields stripped, no boxes). Detect runs the detector under a scan and the
boxes come in as it passes; where live detection is off it plays the scan over the sample's saved
detections and says so. Link's Auto map carries the AI agent's mapping onto the boxes by position.
Fill shows the filled PDF on the page; Download saves those bytes and Clear goes back to the boxes.

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

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "tests" / "web" / "fixtures" / "sample-bundle"
SAMPLES = "/tests/web/fixtures/"
FAKE_WORKER = "/tests/web/harness/fake-detect-worker.js"
CANNED_BOXES = 5  # what fake-detect-worker.js answers per page
PAGE = "/demo/web/showcase/index.html"

DESKTOP = {"viewport": {"width": 1280, "height": 900}}
PHONE = {"viewport": {"width": 375, "height": 812}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2}

LAZY = re.compile(r"huggingface\.co|onnxruntime-web|pdf-lib|detect-worker|fake-detect-worker|engines/browser/detect\.js|agent-template")
MODEL = re.compile(r"huggingface\.co|onnxruntime-web|fake-detect-worker|detect-worker\.js")
PROTECTED = "This PDF is protected against changes, so it can't be filled in the browser. The full library can fill it."
SAVED_NOTE = "Saved results from an earlier run of the detector: live detection is off on this device."
# where the browser reports under 4 GB, live detection waits for the visitor: Detect plays the saved results
LOW_MEMORY = "Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 2, configurable: true});"


def bundle_json(name: str):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


SAVED = len(bundle_json("detections.json")["pages"]["0"])  # the saved boxes on page 0


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

    def wait_status(self, start: str, timeout: int = 15_000):
        self.page.wait_for_function(
            "s => document.getElementById('detect-status').textContent.startsWith(s)", arg=start, timeout=timeout
        )

    def step(self, name: str):
        self.page.click(f"#tab-{name}")

    def drop(self, name: str, data: bytes, mime: str = "application/pdf"):
        self.page.set_input_files("#file", files=[{"name": name, "mimeType": mime, "buffer": data}])

    def detect(self, count: int):
        """Click Detect and wait for `count` boxes and the end of the scan."""
        self.page.click("#detect")
        self.wait_boxes(count)
        self.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-revealing')")
        self.page.wait_for_function("!document.getElementById('detect').disabled")

    def automap(self):
        """Run Auto map from the Link step and wait for its readout and the links landing."""
        self.step("link")
        self.page.click("#automap")
        self.page.wait_for_function("document.getElementById('automap-status').textContent.startsWith('Mapped')")
        self.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-mapping')")

    def fill(self):
        """Fill from the Fill step and wait for the filled PDF on the page."""
        self.step("fill")
        self.page.click("#fill")
        self.wait_filled()

    def wait_filled(self, title: str = ""):
        self.page.wait_for_function(
            "t => document.getElementById('pageview').classList.contains('is-filled')"
            " && document.getElementById('caption').textContent.includes(t)",
            arg=title,
            timeout=15_000,
        )

    def download(self) -> bytes:
        with self.page.expect_download() as dl:
            self.page.click("#download")
        assert dl.value.suggested_filename.endswith("-filled.pdf")
        return Path(dl.value.path()).read_bytes()

    def watch(self, script: str):
        """Start keeping in window.seen every value `script` (an expression) takes as the page changes."""
        self.page.evaluate(
            """expr => {
                window.seen = [];
                const look = new Function(`return (${expr})`);
                const note = () => { const v = look(); if (window.seen.at(-1) !== v) window.seen.push(v); };
                new MutationObserver(note).observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
                note();
            }""",
            script,
        )

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
            page.wait_for_selector(".fc-pv-media", timeout=10_000)
            page.wait_for_function("!document.getElementById('detect').disabled")
        return tool

    yield _open
    for context in contexts:
        context.close()


def greedy_matches(originals, boxes, thr=0.5) -> list[tuple[int, int]]:
    pairs = sorted(
        ((iou(o, b), i, j) for i, o in enumerate(originals) for j, b in enumerate(boxes) if iou(o, b) >= thr),
        key=lambda p: -p[0],
    )
    used_o, used_b, out = set(), set(), []
    for _, i, j in pairs:
        if i not in used_o and j not in used_b:
            used_o.add(i)
            used_b.add(j)
            out.append((i, j))
    return out


def expected_mapped(detections: dict, agent: dict) -> int:
    """How many saved boxes Auto map links: same page, IoU >= 0.3, one to one (showcase/automap.js)."""
    n = 0
    for page, cands in detections.items():
        linked = [
            f["rect"] for f in agent["fields"]
            if f["type"] != "table" and f.get("page") == int(page) and (f.get("source") or {}).get("paths")
        ]  # fmt: skip
        n += len(greedy_matches([c["rect"] for c in cands], linked, 0.3))
    return n


def user_password_pdf() -> bytes:
    doc = pymupdf.open(BUNDLE / "form.pdf")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="visitor")
    doc.close()
    return data


def published_pdf() -> bytes:
    """The fixture's form as published, with its own fields (a fillable PDF a visitor might drop)."""
    doc = pymupdf.open(ROOT / "forms" / "dev" / "fw9.pdf")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
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


def widget_values(data: bytes) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [w.field_value for p in doc for w in p.widgets() or []]
    finally:
        doc.close()


# ---------------------------------------------------------------- load


def test_a_sample_opens_blank_fast_and_loads_nothing_heavy(open_tool):
    tool = open_tool(wait=False)
    tool.page.wait_for_selector(".fc-pv-media", timeout=2000)
    assert tool.page.evaluate("performance.now()") < 2000
    tool.page.wait_for_function("!document.getElementById('detect').disabled")
    assert tool.boxes().count() == 0
    assert tool.page.inner_text("#caption") == "Fig. 1 · No boxes yet"
    assert tool.page.inner_text("#detect") == "DETECT THE BOXES"
    assert "fields stripped" in tool.page.inner_text("#docline")
    assert tool.page.is_hidden("#compare-group")
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


def test_the_developer_controls_are_gone(open_tool):
    tool = open_tool()
    for gone in ("#smart", "#save-template", "#template-file", "#report", "#history", "#load-agent", "#agent-group", "#start-over"):
        assert tool.page.locator(gone).count() == 0, gone
    text = tool.page.inner_text(".tool")
    for phrase in ("fillable empty PDF", "Download template", "Load template", "fields written", "left to fill"):
        assert phrase not in text, phrase


def test_missing_samples_say_so_and_leave_the_drop_path(open_tool):
    tool = open_tool(samples="/tests/web/no-such-samples/", wait=False)
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert "couldn't be loaded" in msg.inner_text()
    assert tool.page.is_visible("#stage-empty")


# ---------------------------------------------------------------- detect


def test_detect_runs_live_under_a_scan_and_the_boxes_come_in_as_it_passes(open_tool):
    tool = open_tool()
    tool.watch("document.getElementById('detect-status').textContent")
    tool.page.click("#detect")
    tool.page.wait_for_selector(".scan.is-sweeping", state="attached")
    assert tool.page.inner_text("#detect") == "DETECTING…"
    tool.page.wait_for_selector("#pageview.is-revealing .fc-box", state="attached")
    delays = tool.page.eval_on_selector_all(
        ".fc-box", "els => els.map(e => [parseFloat(e.style.top), parseFloat(e.style.getPropertyValue('--reveal-delay'))])"
    )
    delays.sort()
    assert [d for _, d in delays] == sorted(d for _, d in delays)  # lower on the page, later in
    assert delays[-1][1] > delays[0][1]
    tool.wait_boxes(CANNED_BOXES)
    tool.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-revealing')")
    assert tool.page.locator(".scan.is-sweeping, .scan.is-pass").count() == 0
    caption = tool.page.inner_text("#caption")
    assert "Detected live by FFDNet-S" in caption
    assert re.search(r"\d+ ms per page", caption)
    seen = tool.page.evaluate("window.seen")
    assert any("MB" in s for s in seen), seen
    assert any("Detecting" in s for s in seen), seen
    assert tool.requested(re.compile("fake-detect-worker"))
    assert tool.page.inner_text("#detect") == "DETECT AGAIN"
    # one undo takes the boxes off again
    tool.step("link")
    tool.page.click("#undo")
    tool.wait_boxes(0)
    assert tool.errors == []


def test_with_reduced_motion_nothing_sweeps_and_the_status_says_what_happens(open_tool):
    tool = open_tool(reduced_motion="reduce")
    tool.watch("[...document.querySelectorAll('.scan.is-sweeping, .scan.is-pass, #pageview.is-revealing')].length")
    tool.page.click("#detect")
    tool.wait_boxes(CANNED_BOXES)
    tool.wait_status("Done")
    assert tool.page.evaluate("window.seen") == [0]
    assert tool.errors == []


def test_saved_results_play_where_live_detection_is_off_and_say_so(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    assert "plays back the detector's saved results" in tool.page.inner_text("#detect-status")
    tool.watch("document.querySelectorAll('.scan.is-pass').length")
    tool.detect(SAVED)
    assert 1 in tool.page.evaluate("window.seen")  # the same scan, over the saved boxes
    assert "Saved results from an earlier run of FFDNet-S" in tool.page.inner_text("#caption")
    assert "Detected live" not in tool.page.inner_text("#caption")
    note = tool.page.locator("#live-note")
    assert note.is_visible()
    assert SAVED_NOTE in note.inner_text()
    tool.page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []
    tool.page.click("#try-anyway")
    tool.wait_boxes(CANNED_BOXES)
    assert "Detected live" in tool.page.inner_text("#caption")
    assert note.is_hidden()
    assert tool.errors == []


def test_original_fields_are_drawn_and_counted(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    tool.detect(SAVED)
    originals = bundle_json("original-fields.json")["pages"]["0"]
    boxes = [d["rect"] for d in bundle_json("detections.json")["pages"]["0"]]
    n = len(greedy_matches(originals, boxes))
    assert 0 < n < len(originals)  # the bundle leaves some out on purpose
    assert tool.page.locator(".fc-compare").count() == 0
    tool.page.check("#compare")
    assert tool.page.locator(".fc-compare").count() == len(originals)
    assert f"found {n} of {len(originals)}" in tool.page.inner_text("#found")
    tool.page.uncheck("#compare")
    assert tool.page.locator(".fc-compare").count() == 0


def test_failed_live_detection_says_why_and_offers_the_saved_results(open_tool):
    tool = open_tool(worker=f"{FAKE_WORKER}?mode=fail")
    tool.page.click("#detect")
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert "could not be downloaded" in msg.inner_text()
    assert tool.boxes().count() == 0
    assert tool.page.locator(".scan.is-sweeping").count() == 0
    assert not tool.page.is_disabled("#detect")
    tool.page.click("#play-saved")
    tool.wait_boxes(SAVED)
    assert "Saved results" in tool.page.inner_text("#caption")
    assert tool.page.is_hidden("#play-saved")


def test_dropped_files_bad_and_good(open_tool):
    tool = open_tool()
    msg = tool.page.locator("#detect-msg")

    tool.drop("notes.txt", b"just some text\n", "text/plain")
    msg.wait_for()
    assert msg.inner_text() == "This file isn't a PDF."

    tool.drop("locked.pdf", user_password_pdf())
    tool.page.wait_for_function("document.getElementById('detect-msg').textContent.includes('password')")
    assert "IRS W-9" in tool.page.inner_text("#docline")

    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())  # a flat PDF is detected as it opens
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
    assert tool.requested(re.compile("pdf-lib")) == []


def test_double_click_on_detect_runs_once(open_tool):
    tool = open_tool()
    tool.watch("document.getElementById('detect-status').textContent")
    tool.page.dblclick("#detect")
    assert tool.page.is_disabled("#detect")
    tool.wait_status("Done")
    tool.wait_boxes(CANNED_BOXES)
    seen = tool.page.evaluate("window.seen")
    assert sum(s.startswith("Done") for s in seen) == 1, seen
    assert sum(s == "Detecting page 1…" for s in seen) == 1, seen


def test_pdf_dropped_during_live_detection_is_detected(open_tool):
    tool = open_tool()
    tool.page.click("#detect")  # the fake worker takes 500 ms to load
    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    tool.wait_status("Done")
    assert "form.pdf" in tool.page.inner_text("#docline")
    tool.wait_boxes(CANNED_BOXES)
    assert "Detected live" in tool.page.inner_text("#caption")
    assert not tool.page.is_disabled("#detect")
    assert tool.page.locator("#detect-msg").is_hidden()
    assert tool.errors == []


# ---------------------------------------------------------------- link


def test_auto_map_carries_the_agents_mapping_as_one_undo_step(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    tool.detect(SAVED)
    want = expected_mapped(bundle_json("detections.json")["pages"], bundle_json("agent-template.json"))
    assert 0 < want < SAVED
    tool.step("link")
    note = tool.page.inner_text("#automap-note")
    assert "These sample forms are pre-mapped." in note
    assert f"the AI agent ({bundle_json('agent-scores.json')['model_label']})" in note
    assert "matched to your boxes by position" in note
    tool.watch("document.getElementById('pageview').classList.contains('is-mapping')")
    tool.page.click("#automap")
    tool.page.wait_for_function("document.getElementById('automap-status').textContent.startsWith('Mapped')")
    assert tool.page.inner_text("#automap-status") == f"Mapped {want} of {SAVED} boxes. The rest are yours to link by hand."
    assert True in tool.page.evaluate("window.seen")  # the links landed in turn
    assert tool.page.locator(".fc-box.is-linked").count() == want
    assert f"{want} linked" in tool.page.inner_text("#caption")
    assert tool.page.locator(".fc-box.is-linked .fc-tag", has_text=re.compile("^legal_name$")).count() == 1
    # still editable by hand, and one undo takes the whole mapping off
    link_first_box(tool, "vendor_id")
    assert tool.page.inner_text("#automap-status").startswith("Mapped")  # a hand edit leaves the readout
    tool.page.click("#undo")
    tool.page.click("#undo")
    tool.page.wait_for_function("document.querySelectorAll('.fc-box.is-linked').length === 0")
    assert tool.page.inner_text("#automap-status") == ""
    # Detect again puts new boxes on screen: an old readout would no longer be true
    tool.page.click("#redo")
    tool.page.wait_for_function("document.getElementById('automap-status').textContent === ''")
    tool.automap()
    tool.step("detect")
    tool.detect(SAVED)
    tool.step("link")
    assert tool.page.inner_text("#automap-status") == ""
    assert tool.errors == []


def test_auto_map_is_off_for_a_visitors_pdf_and_a_pasted_schema(open_tool):
    tool = open_tool()
    tool.step("link")
    assert tool.page.is_disabled("#automap")
    assert tool.page.inner_text("#automap-status").startswith("Detect the boxes first")
    tool.step("detect")
    tool.detect(CANNED_BOXES)
    tool.step("link")
    assert not tool.page.is_disabled("#automap")

    tool.page.click("#paste summary")
    tool.page.fill("#schema-text", json.dumps({"type": "object", "properties": {"full_name": {"type": "string"}}}))
    tool.page.click("#use-schema")
    tool.page.locator('.fc-sl-leaf[data-path="full_name"]').wait_for()
    assert tool.page.is_disabled("#automap")
    assert "no prepared mapping for yours" in tool.page.inner_text("#automap-status")
    tool.page.click("#sample-schema")
    tool.page.wait_for_function("!document.getElementById('automap').disabled")

    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    tool.page.wait_for_function("document.getElementById('docline').textContent.includes('form.pdf')")
    tool.wait_boxes(CANNED_BOXES)
    assert tool.page.is_disabled("#automap")
    assert "no prepared mapping for your PDF" in tool.page.inner_text("#automap-status")
    assert tool.page.is_hidden("#automap-note")


def test_link_undo_redo(open_tool):
    tool = open_tool()
    tool.detect(CANNED_BOXES)
    field_id = link_first_box(tool, "legal_name")
    assert tool.page.locator("[role=tab][aria-selected=true]").text_content().endswith("Link")
    tag = tool.page.locator(f'.fc-box[data-field-id="{field_id}"] .fc-tag')
    tool.page.click("#undo")
    tool.page.wait_for_function(
        "id => document.querySelector(`.fc-box[data-field-id='${id}'] .fc-tag`).textContent === 'not linked'", arg=field_id
    )
    tool.page.click("#redo")
    tool.page.wait_for_function(
        "id => document.querySelector(`.fc-box[data-field-id='${id}'] .fc-tag`).textContent === 'legal_name'", arg=field_id
    )
    assert tag.inner_text() == "legal_name"
    assert "1 linked" in tool.page.inner_text("#caption")
    assert tool.errors == []


def test_pasted_schemas(open_tool):
    tool = open_tool()
    tool.detect(CANNED_BOXES)
    field_id = link_first_box(tool, "legal_name")
    tool.page.click("#paste summary")
    error = tool.page.locator("#schema-error")

    tool.page.fill("#schema-text", "{not json")
    tool.page.click("#use-schema")
    error.wait_for()
    assert "isn't valid JSON" in error.inner_text()
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
    # the record follows the new schema: one made-up record, no picker
    tool.step("fill")
    assert set(json.loads(tool.page.input_value("#record"))) == {"full_name", "city"}
    assert tool.page.is_hidden("#record-pick-group")
    # the sample's schema brings its records back
    tool.step("link")
    tool.page.click("#sample-schema")
    tool.step("fill")
    tool.page.wait_for_function("!document.getElementById('record-pick-group').hidden")
    assert json.loads(tool.page.input_value("#record")) == bundle_json("records.json")[0]["record"]


# ---------------------------------------------------------------- fill


def test_fill_shows_the_filled_pdf_and_download_saves_it(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    tool.detect(SAVED)
    tool.automap()
    tool.step("fill")
    records = bundle_json("records.json")
    options = tool.page.locator("#record-pick option").all_text_contents()
    assert options == [r["title"] for r in records]
    assert json.loads(tool.page.input_value("#record")) == records[0]["record"]
    assert tool.page.is_disabled("#download") and tool.page.is_disabled("#clear")
    assert tool.requested(re.compile("pdf-lib")) == []

    tool.page.click("#fill")
    tool.wait_filled(records[0]["title"])
    assert tool.requested(re.compile("pdf-lib"))
    assert "The filled PDF" in tool.page.inner_text("#caption")
    assert tool.page.inner_text("#fill-msg").startswith(f"Filled with {records[0]['title']}.")
    assert tool.page.eval_on_selector(".fc-pv-layer", "el => getComputedStyle(el).visibility") == "hidden"
    assert tool.page.is_hidden("#key")
    tool.page.wait_for_selector("canvas.fc-pv-media")
    first = tool.download()
    assert records[0]["record"]["legal_name"] in widget_values(first)

    # another record fills again from the template, with no Clear in between
    tool.page.select_option("#record-pick", "1")
    tool.wait_filled(records[1]["title"])
    second = tool.download()
    values = widget_values(second)
    assert records[1]["record"]["legal_name"] in values
    assert records[0]["record"]["legal_name"] not in values  # never filled on top of the first

    tool.page.click("#clear")
    tool.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-filled')")
    assert tool.page.eval_on_selector(".fc-pv-layer", "el => getComputedStyle(el).visibility") == "visible"
    assert tool.page.is_disabled("#download") and tool.page.is_disabled("#clear")
    assert tool.boxes().count() == SAVED
    assert tool.errors == []


def test_leaving_the_fill_step_or_editing_ends_the_preview(open_tool):
    tool = open_tool()
    tool.detect(CANNED_BOXES)
    link_first_box(tool, "legal_name")
    tool.fill()
    tool.step("link")
    tool.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-filled')")
    tool.step("fill")
    assert tool.page.is_disabled("#download")
    tool.page.click("#fill")
    tool.wait_filled()
    # an edit ends it too (here an undo, with the Link step's button)
    tool.page.evaluate("document.getElementById('undo').click()")
    tool.page.wait_for_function("!document.getElementById('pageview').classList.contains('is-filled')")
    assert tool.page.is_disabled("#download")
    assert "0 linked" in tool.page.inner_text("#caption")


def test_a_fill_still_running_when_the_visitor_leaves_fill_is_never_shown(open_tool):
    tool = open_tool()
    held = []
    tool.page.route(re.compile(r"pdf-lib"), lambda route: held.append(route))  # pdf-lib arrives when let through
    tool.detect(CANNED_BOXES)
    tool.watch("document.getElementById('pageview').classList.contains('is-filled')")
    tool.step("fill")
    tool.page.click("#fill")
    for _ in range(100):  # the held request reaches the route handler while Playwright runs
        if held:
            break
        tool.page.wait_for_timeout(50)
    assert held
    tool.step("link")
    for route in held:
        route.continue_()
    tool.page.wait_for_function("!document.getElementById('fill').disabled")  # the fill has finished
    tool.page.wait_for_timeout(300)
    assert tool.page.evaluate("window.seen") == [False]
    tool.step("fill")
    assert tool.page.is_disabled("#download")
    assert tool.page.is_hidden("#fill-msg")
    assert tool.errors == []


def test_a_box_that_combines_two_fields_fills_them_joined(open_tool):
    tool = open_tool()
    tool.detect(CANNED_BOXES)
    schema = {"type": "object", "properties": {"first_name": {"type": "string"}, "last_name": {"type": "string"}}}
    tool.step("link")
    tool.page.click("#paste summary")
    tool.page.fill("#schema-text", json.dumps(schema))
    tool.page.click("#use-schema")
    tool.page.locator('.fc-sl-leaf[data-path="first_name"]').wait_for()
    field_id = link_first_box(tool, "first_name")
    tool.page.select_option("#inspector select[data-ctl=path-add]", "last_name")
    tag = tool.page.locator(f'.fc-box[data-field-id="{field_id}"] .fc-tag')
    tool.page.wait_for_function("el => el.textContent === 'first_name, last_name'", arg=tag.element_handle())
    assert tool.page.input_value("#inspector [data-ctl=join]") == " "
    assert tool.page.locator('.fc-sl-leaf[data-path="last_name"]').get_attribute("data-status") == "placed"

    tool.step("fill")
    tool.page.fill("#record", json.dumps({"first_name": "Jane", "last_name": "Doe"}))
    tool.page.click("#fill")
    tool.wait_filled("your edited record")
    assert "Jane Doe" in widget_values(tool.download())
    assert tool.errors == []


def test_bad_record_json_is_reported(open_tool):
    tool = open_tool()
    tool.detect(CANNED_BOXES)
    tool.step("fill")
    tool.page.fill("#record", "{oops")
    tool.page.click("#fill")
    error = tool.page.locator("#record-error")
    error.wait_for()
    assert "isn't valid JSON" in error.inner_text()
    assert tool.page.is_disabled("#download")


# ---------------------------------------------------------------- phones and themes


def test_phone_layout(open_tool):
    tool = open_tool(PHONE)
    page = tool.page
    tool.no_horizontal_scroll()
    fig = page.locator("#fig").bounding_box()
    panel = page.locator(".panel").bounding_box()
    assert fig["y"] + fig["height"] <= panel["y"] + 1
    assert page.locator("#model-group").is_hidden()
    tool.detect(SAVED)  # live detection is off on a phone: the saved results
    tool.no_horizontal_scroll()
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
    # the whole flow on a phone: Auto map, Fill, Download and Clear all in reach
    tool.automap()
    tool.no_horizontal_scroll()
    tool.fill()
    tool.no_horizontal_scroll()
    for button in ("#fill", "#download", "#clear"):
        box = page.locator(button).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"], button
    assert tool.download().startswith(b"%PDF")
    assert tool.requested(MODEL) == []


def test_phone_flat_pdf_waits_for_try_anyway(open_tool):
    tool = open_tool(PHONE)
    tool.drop("form.pdf", (BUNDLE / "form.pdf").read_bytes())
    note = tool.page.locator("#live-note")
    note.wait_for()
    assert "Live detection is off on this device" in note.inner_text()
    assert "Saved results" not in note.inner_text()  # a visitor's PDF has none
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
    data = published_pdf()
    tool.drop("published.pdf", data)
    doc = pymupdf.open(stream=data)
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
    # the primary buttons keep a readable label when disabled
    tool.step("link")
    assert tool.page.is_disabled("#automap")
    color = tool.page.eval_on_selector("#automap", "el => getComputedStyle(el).color")
    background = tool.page.eval_on_selector("#automap", "el => getComputedStyle(el).backgroundColor")
    assert color != background


def test_low_memory_desktop_keeps_mouse_tools_and_plays_saved_results_until_asked(open_tool):
    tool = open_tool(init_script=LOW_MEMORY)
    page = tool.page
    assert page.evaluate("navigator.deviceMemory") == 2
    assert page.locator("#model-group").is_visible()
    tool.step("link")
    assert page.locator('.fc-sl-leaf[data-path="legal_name"]').get_attribute("draggable") == "true"
    assert page.inner_text("#link-hint").startswith("Drag a field onto a box")
    tool.step("detect")
    page.select_option("#model", "L")
    tool.detect(SAVED)
    note = page.locator("#live-note")
    assert "102 MB" in note.inner_text()
    page.wait_for_timeout(300)
    assert tool.requested(MODEL) == []
    page.select_option("#model", "S")
    page.click("#try-anyway")
    tool.wait_boxes(CANNED_BOXES)
    # once accepted, Detect runs live without asking again
    tool.wait_status("Done")
    page.click("#detect")
    tool.wait_status("Done")
    assert note.is_hidden()
    assert "Detected live" in page.inner_text("#caption")


# ---------------------------------------------------------------- time limits and failures


def test_a_hung_agent_template_times_out_and_keeps_the_page(open_tool):
    tool = open_tool(timeout=500)
    tool.page.route(re.compile(r"agent-template\.json$"), lambda route: None)  # never answered
    tool.detect(CANNED_BOXES)
    tool.step("link")
    tool.page.click("#automap")
    tool.page.wait_for_function("document.getElementById('automap-status').textContent.includes('took more than')", timeout=5000)
    assert tool.page.inner_text("#automap-status").startswith("Auto map didn't finish:")
    assert tool.page.locator(".fc-box.is-linked").count() == 0
    assert not tool.page.is_disabled("#automap")


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
    tool.drop("hang.pdf", (BUNDLE / "form.pdf").read_bytes())
    msg = tool.page.locator("#detect-msg")
    msg.wait_for(timeout=5000)
    assert msg.inner_text() == "This file took more than 1 second to open. Try again, or try a smaller PDF."
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
    assert tool.page.is_disabled("#download")


def test_a_part_that_fails_to_load_says_so_plainly(open_tool):
    tool = open_tool()
    tool.page.route(re.compile(r"pdf-lib"), lambda route: route.abort())
    tool.page.route(re.compile(r"engines/browser/detect\.js"), lambda route: route.abort())
    tool.step("fill")
    tool.page.click("#fill")
    tool.page.wait_for_function("document.getElementById('fill-msg').textContent.includes('load')", timeout=5000)
    assert tool.page.inner_text("#fill-msg") == f"That didn't work: {PART_FAILED}"
    tool.step("detect")
    tool.page.click("#detect")
    msg = tool.page.locator("#detect-msg")
    msg.wait_for(timeout=5000)
    assert PART_FAILED in msg.inner_text()
    assert "dynamically imported" not in msg.inner_text()
    assert tool.boxes().count() == 0


# ---------------------------------------------------------------- the real sample bundles

REAL_SAMPLES = ROOT / "demo" / "web" / "showcase" / "samples"


@pytest.mark.parametrize("form_id", ["fw9", "fw4", "i9", "ndguard", "grandview_app"])
def test_each_real_sample_detects_auto_maps_and_fills(open_tool, form_id):
    """The page's own samples/ folder (no ?samples=), on a low-memory device so Detect plays the saved results."""
    tool = open_tool(samples=None, worker=None, wait=False, init_script=LOW_MEMORY)
    tool.page.wait_for_function("document.querySelectorAll('#sample option').length >= 3")
    index = json.loads((REAL_SAMPLES / "index.json").read_text())
    assert [o for o in tool.page.locator("#sample option").evaluate_all("els => els.map(e => e.value)")] == [e["id"] for e in index]
    title = next(e["title"] for e in index if e["id"] == form_id)

    tool.page.select_option("#sample", form_id)
    tool.page.wait_for_function("t => document.getElementById('docline').textContent.includes(t)", arg=title)
    tool.page.wait_for_function("!document.getElementById('detect').disabled")
    assert tool.boxes().count() == 0
    detections = json.loads((REAL_SAMPLES / form_id / "detections.json").read_text())["pages"]
    originals = json.loads((REAL_SAMPLES / form_id / "original-fields.json").read_text())["pages"]
    tool.detect(len(detections["0"]))
    assert "FFDNet-S" in tool.page.inner_text("#caption")
    if originals:
        assert "fields stripped" in tool.page.inner_text("#docline")
        found = sum(len(greedy_matches(originals[p], [d["rect"] for d in detections[p]])) for p in originals)
        total = sum(len(v) for v in originals.values())
        assert 0 < found <= total
        assert f"found {found} of {total}" in tool.page.inner_text("#found")
    else:  # a printed form has no fields of its own to compare with
        assert "printed form, no fields" in tool.page.inner_text("#docline")
        assert tool.page.locator("#compare-group").is_hidden()

    agent = json.loads((REAL_SAMPLES / form_id / "agent-template.json").read_text())
    want = expected_mapped(detections, agent)
    boxes = sum(len(v) for v in detections.values())
    tool.automap()
    assert tool.page.inner_text("#automap-status").startswith(f"Mapped {want} of {boxes} boxes.")
    assert "Haiku 5.5" in tool.page.inner_text("#automap-note")

    records = json.loads((REAL_SAMPLES / form_id / "records.json").read_text())
    tool.fill()
    tool.wait_filled(records[0]["title"])
    tool.page.select_option("#record-pick", str(len(records) - 1))
    tool.wait_filled(records[-1]["title"])
    values = widget_values(tool.download())
    for key in ("ein", "ssn"):
        if form_id == "fw9" and key in records[-1]["record"]:  # the W-9's split boxes take every digit
            for part in records[-1]["record"][key].split("-"):
                assert part in values, (key, part, values)
    assert tool.page.locator("#detect-msg").is_hidden()
    assert tool.requested(MODEL) == []
    assert tool.errors == []


def test_a_failed_sample_switch_keeps_the_picker_on_the_open_sample(open_tool):
    tool = open_tool(samples=None, worker=None)
    index = json.loads((REAL_SAMPLES / "index.json").read_text())
    first, other = index[0]["id"], index[1]["id"]
    tool.page.route(re.compile(rf"/samples/{other}/manifest\.json$"), lambda route: route.abort())
    tool.page.select_option("#sample", other)
    msg = tool.page.locator("#detect-msg")
    msg.wait_for()
    assert msg.inner_text().startswith("This sample couldn't be opened.")
    assert tool.page.input_value("#sample") == first
    assert index[0]["title"] in tool.page.inner_text("#docline")
