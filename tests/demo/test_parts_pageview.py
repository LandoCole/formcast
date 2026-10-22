"""The page view part in Chromium (tests/web/harness/parts.html?part=pageview).

The page image is forms/dev/fw9.pdf page 0 rendered at 2x (144 dpi) with PyMuPDF and handed to the
harness as a data URL. Boxes come from the committed fw9 seed template, with a few fields linked.
"""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "tests" / "web" / "fixtures" / "fw9_seed.json").read_text(encoding="utf-8"))
SCALE = 2


@pytest.fixture(scope="module")
def page_png() -> str:
    """fw9 page 0 at 144 dpi as a data URL."""
    doc = pymupdf.open(ROOT / "forms" / "dev" / "fw9.pdf")
    try:
        png = doc[0].get_pixmap(dpi=72 * SCALE).tobytes("png")
        width, height = doc[0].rect.width, doc[0].rect.height
    finally:
        doc.close()
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii"), width, height


def make_template() -> dict:
    """The fw9 seed with w0_0 linked, w0_2 (checkbox) linked, and a table field added."""
    t = copy.deepcopy(SEED)
    by_id = {f["id"]: f for f in t["fields"]}
    by_id["w0_0"]["source"] = {"paths": ["applicant.name"], "transforms": []}
    by_id["w0_0"]["label"] = "Name"
    by_id["w0_2"]["source"] = {"paths": ["applicant.llc"], "transforms": []}
    by_id["w0_3"]["origin"] = "agent"
    t["fields"].append(
        {
            "id": "items",
            "type": "table",
            "label": "Items",
            "page": None,
            "rect": None,
            "source": {"paths": ["items"], "transforms": []},
            "segments": [
                {
                    "page": 0,
                    "rect": [60, 600, 400, 700],
                    "row_top": 620,
                    "row_height": 20,
                    "max_rows": 4,
                    "columns": [{"key": "a", "x": [60, 200]}, {"key": "b", "x": [200, 400]}],
                },
                {
                    "page": 1,
                    "rect": [60, 100, 400, 200],
                    "row_top": 100,
                    "row_height": 25,
                    "max_rows": 4,
                    "columns": [{"key": "a", "x": [60, 400]}],
                },
            ],
            "style": {},
            "origin": "human",
        }
    )
    return t


@pytest.fixture
def pv(server, browser, page_png):
    """A page with the harness set up for fw9 page 0 at scale 2; ``pv.setup(**overrides)`` redoes it."""
    url, width, height = page_png
    context = browser.new_context(viewport={"width": 1400, "height": 1700})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{server}/tests/web/harness/parts.html?part=pageview")
    page.wait_for_function("window.partsReady === true")

    class Harness:
        def __init__(self):
            self.page = page
            self.template = make_template()
            self.errors = errors

        def setup(self, **overrides):
            opts = {
                "page": {"index": 0, "width": width, "height": height, "imageUrl": url},
                "template": self.template,
                "scale": SCALE,
            }
            opts.update(overrides)
            page.evaluate("opts => window.setup(opts)", opts)

        def box(self, fid, segment=None):
            sel = f'.fc-box[data-field-id="{fid}"]'
            if segment is not None:
                sel += f'[data-segment="{segment}"]'
            return page.locator(sel)

        def events(self, kind=None):
            evs = page.evaluate("window.events")
            return [e for e in evs if kind is None or e["type"] == kind]

        def ops(self):
            return [e["detail"]["op"] for e in self.events("op")]

        def drag(self, locator, dx, dy):
            bb = locator.bounding_box()
            cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx + dx / 2, cy + dy / 2, steps=3)
            page.mouse.move(cx + dx, cy + dy, steps=3)
            return page.mouse

    h = Harness()
    h.setup()
    try:
        yield h
        assert not errors, errors
    finally:
        context.close()


def field(template: dict, fid: str) -> dict:
    return next(f for f in template["fields"] if f["id"] == fid)


def close(a, b, tol=0.02) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_boxes_sit_at_their_screen_positions(pv):
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    for fid in ("w0_0", "w0_2"):
        x0, y0, x1, y1 = field(pv.template, fid)["rect"]
        bb = pv.box(fid).bounding_box()
        assert bb["x"] - origin["x"] == pytest.approx(x0 * SCALE, abs=1)
        assert bb["y"] - origin["y"] == pytest.approx(y0 * SCALE, abs=1)
        assert bb["width"] == pytest.approx((x1 - x0) * SCALE, abs=1)
        assert bb["height"] == pytest.approx((y1 - y0) * SCALE, abs=1)
    assert pv.page.locator(".fc-pv-page").bounding_box()["width"] == pytest.approx(611.976 * SCALE, abs=1)
    assert pv.page.locator("img.fc-pv-media").evaluate("img => img.complete && img.naturalWidth") == pytest.approx(1224, abs=1)


def test_rotated_page_places_boxes_through_geometry(pv, page_png):
    _, width, height = page_png
    scale = 1.5  # the rotated page is 792 pt wide: 2x would not fit the 1400 px viewport
    pv.setup(page={"index": 0, "width": width, "height": height, "rotation": 90, "imageUrl": page_png[0]}, scale=scale)
    x0, y0, x1, y1 = field(pv.template, "w0_0")["rect"]
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    assert origin["width"] == pytest.approx(height * scale, abs=1)  # width and height swap
    bb = pv.box("w0_0").bounding_box()
    # at 90 degrees clockwise, (x, y) lands at (page_height - y, x)
    assert bb["x"] - origin["x"] == pytest.approx((height - y1) * scale, abs=1)
    assert bb["y"] - origin["y"] == pytest.approx(x0 * scale, abs=1)
    assert bb["width"] == pytest.approx((y1 - y0) * scale, abs=1)
    assert bb["height"] == pytest.approx((x1 - x0) * scale, abs=1)
    # a drag to the right on screen moves the rect down the page in points (screen x = page y reversed)
    rect = field(pv.template, "w0_0")["rect"]
    pv.drag(pv.box("w0_0"), 15, 0).up()
    (op,) = pv.ops()
    assert close(op["rect"], [rect[0], rect[1] - 10, rect[2], rect[3] - 10])


def test_box_roles_labels_and_state_classes(pv):
    linked = pv.box("w0_0")
    assert linked.get_attribute("role") == "button"
    assert linked.get_attribute("tabindex") == "0"
    assert linked.get_attribute("aria-label") == "Name: applicant.name"
    assert "is-linked" in linked.get_attribute("class")
    unlinked = pv.box("w0_1")
    assert unlinked.get_attribute("aria-label") == "w0_1: not linked"
    assert "is-unlinked" in unlinked.get_attribute("class")
    assert "is-ai" in pv.box("w0_3").get_attribute("class")
    assert "is-ai" not in linked.get_attribute("class")
    assert pv.box("w0_3").locator(".fc-ai").inner_text() == "AI"


def test_clicking_selects_and_shows_handles(pv):
    pv.box("w0_0").click()
    assert "is-selected" in pv.box("w0_0").get_attribute("class")
    assert pv.page.locator(".fc-hd").count() == 4
    assert pv.events("select") == [{"type": "select", "detail": {"id": "w0_0"}}]
    pv.box("w0_1").click()
    assert pv.page.locator(".is-selected").count() == 1
    assert pv.events("select")[-1]["detail"] == {"id": "w0_1"}
    pv.page.locator(".fc-pv-layer").click(position={"x": 5, "y": 5})
    assert pv.events("select")[-1]["detail"] == {"id": None}
    assert pv.page.locator(".fc-hd").count() == 0
    assert pv.ops() == []


def test_select_by_host_is_silent(pv):
    pv.page.evaluate("window.view.select('w0_2')")
    assert "is-selected" in pv.box("w0_2").get_attribute("class")
    assert pv.events() == []


def test_drag_emits_one_move_on_pointerup(pv):
    rect = field(pv.template, "w0_0")["rect"]
    mouse = pv.drag(pv.box("w0_0"), 20, 10)
    assert pv.ops() == []  # nothing while the pointer is down
    mouse.up()
    ops = pv.ops()
    assert len(ops) == 1
    assert ops[0]["op"] == "move" and ops[0]["id"] == "w0_0" and "segment" not in ops[0]
    assert close(ops[0]["rect"], [rect[0] + 10, rect[1] + 5, rect[2] + 10, rect[3] + 5])


def test_small_pointer_movement_is_a_click_not_a_move(pv):
    mouse = pv.drag(pv.box("w0_0"), 1, 1)
    mouse.up()
    assert pv.ops() == []


def test_resize_from_the_bottom_right_handle(pv):
    rect = field(pv.template, "w0_0")["rect"]
    pv.box("w0_0").click()
    handle = pv.box("w0_0").locator('.fc-hd[data-handle="se"]')
    bb = handle.bounding_box()
    assert bb["width"] >= 24 and bb["height"] >= 24  # touch hit area
    mouse = pv.drag(handle, 20, 10)
    mouse.up()
    ops = pv.ops()
    assert len(ops) == 1 and ops[0]["op"] == "resize" and ops[0]["id"] == "w0_0"
    assert close(ops[0]["rect"], [rect[0], rect[1], rect[2] + 10, rect[3] + 5])


def test_resize_from_the_top_left_handle(pv):
    rect = field(pv.template, "w0_1")["rect"]
    pv.box("w0_1").click()
    mouse = pv.drag(pv.box("w0_1").locator('.fc-hd[data-handle="nw"]'), 20, 10)
    mouse.up()
    (op,) = pv.ops()
    assert op["op"] == "resize"
    assert close(op["rect"], [rect[0] + 10, rect[1] + 5, rect[2], rect[3]])


def test_small_selected_box_body_stays_draggable(pv):
    # w0_2 is an 8 pt checkbox: 16 px at 2x, smaller than one 24 px handle
    rect = field(pv.template, "w0_2")["rect"]
    pv.box("w0_2").click()
    assert "is-small" in pv.box("w0_2").get_attribute("class")
    bb = pv.box("w0_2").bounding_box()
    cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
    assert pv.page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).closest('.fc-hd') === null", [cx, cy]
    )
    # every handle keeps a 24 px hit area and lies outside the box
    for corner in ("nw", "ne", "sw", "se"):
        hb = pv.box("w0_2").locator(f'.fc-hd[data-handle="{corner}"]').bounding_box()
        assert hb["width"] >= 24 and hb["height"] >= 24
        overlap_x = min(hb["x"] + hb["width"], bb["x"] + bb["width"]) - max(hb["x"], bb["x"])
        overlap_y = min(hb["y"] + hb["height"], bb["y"] + bb["height"]) - max(hb["y"], bb["y"])
        assert overlap_x <= 0.5 or overlap_y <= 0.5, corner
    pv.drag(pv.box("w0_2"), 20, 10).up()
    ops = pv.ops()
    assert len(ops) == 1 and ops[0]["op"] == "move"
    assert close(ops[0]["rect"], [rect[0] + 10, rect[1] + 5, rect[2] + 10, rect[3] + 5])


def test_small_box_still_resizes_from_an_outside_handle(pv):
    rect = field(pv.template, "w0_2")["rect"]
    pv.box("w0_2").click()
    pv.drag(pv.box("w0_2").locator('.fc-hd[data-handle="se"]'), 20, 10).up()
    (op,) = pv.ops()
    assert op["op"] == "resize" and close(op["rect"], [rect[0], rect[1], rect[2] + 10, rect[3] + 5])


def test_arrow_keys_nudge(pv):
    rect = field(pv.template, "w0_1")["rect"]
    pv.box("w0_1").focus()
    pv.page.keyboard.press("ArrowRight")
    (op,) = pv.ops()
    assert op["op"] == "move" and op["id"] == "w0_1"
    assert close(op["rect"], [rect[0] + 1, rect[1], rect[2] + 1, rect[3]])


def test_shift_arrow_nudges_ten_points(pv):
    rect = field(pv.template, "w0_1")["rect"]
    pv.box("w0_1").focus()
    pv.page.keyboard.press("Shift+ArrowDown")
    (op,) = pv.ops()
    assert close(op["rect"], [rect[0], rect[1] + 10, rect[2], rect[3] + 10])


def test_held_arrow_emits_one_move_on_keyup(pv):
    rect = field(pv.template, "w0_1")["rect"]
    pv.box("w0_1").focus()
    for _ in range(3):
        pv.page.keyboard.down("ArrowLeft")
    assert pv.ops() == []
    pv.page.keyboard.up("ArrowLeft")
    (op,) = pv.ops()
    assert close(op["rect"], [rect[0] - 3, rect[1], rect[2] - 3, rect[3]])


def test_delete_and_backspace_remove_the_field(pv):
    pv.box("w0_1").focus()
    pv.page.keyboard.press("Delete")
    assert pv.ops() == [{"op": "remove_field", "id": "w0_1"}]
    pv.box("w0_2").focus()
    pv.page.keyboard.press("Backspace")
    assert pv.ops()[-1] == {"op": "remove_field", "id": "w0_2"}


def test_held_delete_removes_one_box(pv):
    """Auto-repeat (a held Delete or Backspace) emits one remove_field, not one per repeat."""
    pv.box("w0_1").focus()
    for _ in range(4):
        pv.page.keyboard.down("Delete")  # the 2nd to 4th presses arrive with repeat = true
    pv.page.keyboard.up("Delete")
    assert pv.ops() == [{"op": "remove_field", "id": "w0_1"}]
    pv.box("w0_2").focus()
    for _ in range(3):
        pv.page.keyboard.down("Backspace")
    pv.page.keyboard.up("Backspace")
    assert pv.ops() == [{"op": "remove_field", "id": "w0_1"}, {"op": "remove_field", "id": "w0_2"}]


def test_drag_back_to_the_start_emits_nothing(pv):
    box = pv.box("w0_0")
    before = box.bounding_box()
    mouse = pv.drag(box, 30, 12)
    bb = before
    mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, steps=3)  # back to where it was
    mouse.up()
    assert pv.ops() == []
    assert box.bounding_box() == before
    # the same for a resize handle dragged out and back
    box.click()
    handle = box.locator('.fc-hd[data-handle="se"]')
    hb = handle.bounding_box()
    mouse = pv.drag(handle, 20, 10)
    mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2, steps=3)
    mouse.up()
    assert pv.ops() == []


def test_a_nudge_against_the_page_edge_emits_nothing(pv):
    t = copy.deepcopy(pv.template)
    edge = field(t, "w0_1")
    edge["rect"] = [0, 100, 40, 114]  # on the left edge of the page
    pv.setup(template=t)
    pv.box("w0_1").focus()
    pv.page.keyboard.press("ArrowLeft")
    assert pv.ops() == []
    assert pv.box("w0_1").bounding_box()["x"] == pytest.approx(pv.page.locator(".fc-pv-layer").bounding_box()["x"], abs=0.5)
    # right then left before either key-up: back where it started, so still nothing
    pv.page.keyboard.down("ArrowRight")
    pv.page.keyboard.down("ArrowLeft")
    pv.page.keyboard.up("ArrowLeft")
    pv.page.keyboard.up("ArrowRight")
    assert pv.ops() == []
    # a step away from the edge still moves
    pv.page.keyboard.press("ArrowRight")
    (op,) = pv.ops()
    assert close(op["rect"], [1, 100, 41, 114])


def test_destroy_removes_listeners_observer_and_its_elements(pv):
    out = pv.page.evaluate(
        """() => {
          let disconnected = 0;
          const real = ResizeObserver.prototype.disconnect;
          ResizeObserver.prototype.disconnect = function () { disconnected += 1; return real.call(this); };
          const mount = document.getElementById("mount");
          const box = mount.querySelector('.fc-box[data-field-id="w0_1"]');
          const layer = mount.querySelector(".fc-pv-layer");
          window.view.setAddMode(true);
          window.events.length = 0;
          window.view.destroy();
          ResizeObserver.prototype.disconnect = real;
          // events on the (now detached) parts and on the container reach no handler
          box.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete", bubbles: true }));
          layer.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerType: "mouse" }));
          mount.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          window.view.setTemplate({ fields: [] });
          window.view.setScale(1);
          return {
            disconnected,
            events: window.events.length,
            children: mount.children.length,
            cls: mount.classList.contains("fc-pageview"),
          };
        }"""
    )
    assert out == {"disconnected": 1, "events": 0, "children": 0, "cls": False}


def drop(box, path):
    return box.evaluate(
        """(el, path) => {
          const dt = new DataTransfer();
          dt.setData("application/x-formcast-path", path);
          const over = new DragEvent("dragover", {dataTransfer: dt, bubbles: true, cancelable: true});
          el.dispatchEvent(over);
          const hovered = el.classList.contains("is-drop");
          el.dispatchEvent(new DragEvent("drop", {dataTransfer: dt, bubbles: true, cancelable: true}));
          return {prevented: over.defaultPrevented, hovered, after: el.classList.contains("is-drop")};
        }""",
        path,
    )


def test_dropping_a_path_links_the_box(pv):
    res = drop(pv.box("w0_1"), "applicant.name")
    assert res == {"prevented": True, "hovered": True, "after": False}
    assert pv.ops() == [
        {"op": "set_source", "id": "w0_1", "source": {"paths": ["applicant.name"], "transforms": []}}
    ]


def test_other_drags_are_ignored(pv):
    res = pv.box("w0_1").evaluate(
        """el => {
          const dt = new DataTransfer();
          dt.setData("text/plain", "applicant.name");
          const over = new DragEvent("dragover", {dataTransfer: dt, bubbles: true, cancelable: true});
          el.dispatchEvent(over);
          el.dispatchEvent(new DragEvent("drop", {dataTransfer: dt, bubbles: true, cancelable: true}));
          return over.defaultPrevented;
        }"""
    )
    assert res is False and pv.ops() == []


def test_read_only_selects_but_never_edits(pv):
    pv.setup(readOnly=True)
    pv.box("w0_0").click()
    assert pv.events("select")[-1]["detail"] == {"id": "w0_0"}
    assert pv.page.locator(".fc-hd").count() == 0
    assert pv.page.locator(".fc-pv-add").count() == 0
    pv.drag(pv.box("w0_0"), 20, 10).up()
    pv.page.keyboard.press("ArrowRight")
    pv.page.keyboard.press("Delete")
    drop(pv.box("w0_1"), "applicant.name")
    assert pv.ops() == []


def test_add_mode_draws_a_new_text_box(pv):
    pv.page.locator(".fc-pv-add").click()
    assert pv.page.locator(".fc-pv-add").get_attribute("aria-pressed") == "true"
    assert pv.events("addmode")[-1]["detail"] == {"on": True}
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    # empty space near the foot of the page (no boxes there)
    x, y = origin["x"] + 100, origin["y"] + 1480
    pv.page.mouse.move(x, y)
    pv.page.mouse.down()
    pv.page.mouse.move(x + 100, y + 40, steps=4)
    assert pv.page.locator(".fc-marquee").count() == 1
    pv.page.mouse.up()
    (op,) = pv.ops()
    assert op["op"] == "add_field" and op["type"] == "text" and op["page"] == 0
    assert close(op["rect"], [50, 740, 100, 760])
    assert pv.page.locator(".fc-marquee").count() == 0
    assert pv.events("addmode")[-1]["detail"] == {"on": False}


def test_a_tiny_drawn_box_is_ignored(pv):
    pv.page.locator(".fc-pv-add").click()
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    x, y = origin["x"] + 100, origin["y"] + 1480
    pv.page.mouse.move(x, y)
    pv.page.mouse.down()
    pv.page.mouse.move(x + 3, y + 3)
    pv.page.mouse.up()
    assert pv.ops() == []


def test_region_select_reports_the_page_rect(pv):
    pv.setup(regionSelect=True)
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    x, y = origin["x"] + 100, origin["y"] + 1480
    pv.page.mouse.move(x, y)
    pv.page.mouse.down()
    pv.page.mouse.move(x + 200, y + 100, steps=4)
    pv.page.mouse.up()
    (ev,) = pv.events("region")
    assert ev["detail"]["page"] == 0 and close(ev["detail"]["rect"], [50, 740, 150, 790])
    assert pv.ops() == []
    assert pv.page.locator(".fc-marquee").count() == 1  # stays until cleared
    pv.page.evaluate("window.view.clearRegion()")
    assert pv.page.locator(".fc-marquee").count() == 0


def test_tables_draw_segment_rows_and_columns_for_the_page(pv):
    seg = pv.box("items", 0)
    assert seg.count() == 1 and pv.box("items", 1).count() == 0  # segment 1 is on page 1
    assert "fc-table" in seg.get_attribute("class")
    assert seg.get_attribute("aria-label") == "Items: items"
    origin = pv.page.locator(".fc-pv-page").bounding_box()
    bb = seg.bounding_box()
    assert bb["x"] - origin["x"] == pytest.approx(120, abs=1) and bb["width"] == pytest.approx(680, abs=1)
    # 5 row lines (4 rows) + 3 column lines (x = 60, 200, 400)
    lines = seg.locator(".fc-line")
    assert lines.count() == 8
    horizontal = [ln for ln in lines.evaluate_all("els => els.map(e => e.getBoundingClientRect().toJSON())") if ln["height"] <= 1.5]
    assert len(horizontal) == 5
    assert sorted(round(ln["y"] - origin["y"]) for ln in horizontal) == [1240, 1280, 1320, 1360, 1400]
    # selecting a table selects the field; moving a segment says which one
    seg.click()
    assert pv.events("select")[-1]["detail"] == {"id": "items"}
    pv.drag(seg, 0, 20).up()
    (op,) = [o for o in pv.ops() if o["op"] == "move"]
    assert op["id"] == "items" and op["segment"] == 0 and close(op["rect"], [60, 610, 400, 710])


def test_compare_overlay_and_warnings(pv):
    pv.setup(compare=[[10, 10, 100, 30], [200, 10, 300, 30]], warnings={"w0_1": ["overlaps w0_0"]})
    assert pv.page.locator(".fc-compare").count() == 2
    assert pv.page.locator(".fc-compare").first.evaluate("e => getComputedStyle(e).pointerEvents") == "none"
    assert "has-warning" in pv.box("w0_1").get_attribute("class")
    assert pv.box("w0_1").get_attribute("title") == "overlaps w0_0"
    assert pv.box("w0_1").locator(".fc-warn").count() == 1


def test_box_states_differ_by_line_style_only(pv):
    pv.box("w0_0").click()
    styles = pv.page.evaluate(
        """() => Object.fromEntries(["w0_0", "w0_1"].map(id => {
          const cs = getComputedStyle(document.querySelector(`.fc-box[data-field-id="${id}"]`));
          return [id, [cs.outlineStyle, cs.outlineWidth]];
        }))"""
    )
    assert styles["w0_0"] == ["solid", "2px"]  # selected
    assert styles["w0_1"] == ["dashed", "1px"]  # not linked
    pv.box("w0_2").click()
    linked = pv.box("w0_0").evaluate("e => [getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth]")
    assert linked == ["solid", "1px"]


def test_set_template_keeps_focus_and_selection(pv):
    pv.box("w0_1").focus()
    pv.page.evaluate("t => window.view.setTemplate(t)", pv.template)
    assert pv.page.evaluate("document.activeElement.dataset.fieldId") == "w0_1"
    assert "is-selected" in pv.box("w0_1").get_attribute("class")


def test_page_fits_a_phone_without_horizontal_scroll(pv):
    pv.page.set_viewport_size({"width": 375, "height": 800})
    pv.setup()
    metrics = pv.page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth,"
        " page: document.querySelector('.fc-pv-page').getBoundingClientRect().width})"
    )
    assert metrics["scroll"] <= metrics["inner"]
    assert metrics["page"] <= metrics["inner"]
    # boxes shrink with the page
    page_box = pv.page.locator(".fc-pv-page").bounding_box()
    x0, _, x1, _ = field(pv.template, "w0_0")["rect"]
    fit = page_box["width"] / 611.976
    assert pv.box("w0_0").bounding_box()["width"] == pytest.approx((x1 - x0) * fit, abs=1)
    # and a drag at this scale converts back with the fit scale, not the preferred one
    rect = field(pv.template, "w0_0")["rect"]
    pv.drag(pv.box("w0_0"), 10, 0).up()
    (op,) = pv.ops()
    assert op["rect"][0] - rect[0] == pytest.approx(10 / fit, abs=0.05)


def test_two_hundred_boxes_render_quickly(pv):
    t = copy.deepcopy(pv.template)
    t["fields"] = [
        {
            "id": f"g{i}",
            "type": "text",
            "label": f"Field {i}",
            "page": 0,
            "rect": [20 + (i % 10) * 55, 20 + (i // 10) * 36, 70 + (i % 10) * 55, 40 + (i // 10) * 36],
            "source": {"paths": [f"a.b{i}"], "transforms": []} if i % 2 else None,
            "style": {},
            "origin": "detector",
        }
        for i in range(200)
    ]
    ms = pv.page.evaluate("t => window.timeSetTemplate(t)", t)
    assert pv.page.locator(".fc-box").count() == 200
    assert ms < 500, ms


def test_canvas_render_callback_gets_the_scale(pv, page_png):
    _, width, height = page_png
    pv.page.evaluate(
        """({width, height}) => {
          window.renderCalls = [];
          window.setup({
            page: {index: 0, width, height, render: async (canvas, scale) => {
              window.renderCalls.push(scale);
              canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
              canvas.getContext("2d").fillRect(0, 0, 10, 10);
            }},
            scale: 2,
          });
        }""",
        {"width": width, "height": height},
    )
    pv.page.wait_for_function("window.renderCalls.length >= 1")
    assert pv.page.locator("canvas.fc-pv-media").count() == 1
    size = pv.page.locator("canvas.fc-pv-media").bounding_box()
    assert size["width"] == pytest.approx(width * 2, abs=1)
    assert pv.page.evaluate("window.renderCalls[0]") >= 2  # css scale x device pixel ratio
