"""The schema list, inspector, history and report parts in Chromium (tests/web/harness/parts.html?part=panels)."""

from __future__ import annotations

import pytest

APPLICANT = {"description": "", "enum": None, "item_type": None, "in_table": None}


def sp(path: str, type_: str, **kw) -> dict:
    return {**APPLICANT, "path": path, "type": type_, **kw}


SCHEMA_PATHS = [
    sp("applicant", "object"),
    sp("applicant.name", "string"),
    sp("applicant.dob", "date"),
    sp("applicant.email", "string"),
    sp("status", "enum", enum=["new", "open", "closed"]),
    sp("items", "array", item_type="object"),
    sp("items[].sku", "string", in_table="items"),
    sp("items[].qty", "integer", in_table="items"),
]

FONT = {"font_size": "auto", "min_font_size": 6, "align": "left", "comb": None, "multiline": False}


def field(fid, type_="text", label="", paths=None, transforms=None, **kw) -> dict:
    return {
        "id": fid,
        "type": type_,
        "label": label,
        "page": 0,
        "rect": [10, 10, 100, 24],
        "source": None if paths is None else {"paths": paths, "transforms": transforms or [], "join": None, "when": None},
        "style": dict(FONT),
        "existing_widget": None,
        "segments": [],
        "confidence": None,
        "origin": "human",
        **kw,
    }


def make_template() -> dict:
    return {
        "formcast_version": "1",
        "template_id": "t",
        "schema": {},
        "form": {"fingerprint": None, "page_count": 1, "page_sizes": [[612, 792]], "kinds": ["digital"]},
        "fields": [
            field("name", label="Name", paths=["applicant.name"]),
            field("dob", label="Date of birth", paths=["applicant.dob"]),
            field("email", label="Email"),
            field("s1", "radio_option", "New", paths=["status"]),
            field("s2", "radio_option", "Open", paths=["status"]),
            field("old", label="Old box", paths=["applicant.fax"]),
            field(
                "items",
                "table",
                "Items",
                paths=["items"],
                page=None,
                rect=None,
                segments=[
                    {
                        "page": 0,
                        "rect": [10, 100, 300, 200],
                        "row_top": 110,
                        "row_height": 20,
                        "max_rows": 4,
                        "columns": [{"key": "sku", "x": [10, 100]}, {"key": "qty", "x": [100, 200]}],
                    }
                ],
            ),
        ],
        "history": [
            {"op": {"op": "set_label", "id": "name", "label": "Name"}, "author": "agent", "at": "2026-10-21T10:00:00+00:00", "note": "first pass"},
            {"op": {"op": "move", "id": "dob", "dx": 3}, "author": "human", "at": "2026-10-21T10:05:00+00:00", "note": ""},
        ],
    }


def by_id(template, fid):
    return next(f for f in template["fields"] if f["id"] == fid)


@pytest.fixture
def panels(server, browser):
    """A page with the panels harness; ``panels.setup(**opts)`` (re)builds them."""
    context = browser.new_context(viewport={"width": 600, "height": 1400})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{server}/tests/web/harness/parts.html?part=panels")
    page.wait_for_function("window.partsReady === true")

    class Harness:
        def __init__(self):
            self.page = page
            self.template = make_template()

        def setup(self, field_id="name", **overrides):
            opts = {
                "schemaPaths": SCHEMA_PATHS,
                "template": self.template,
                "field": by_id(self.template, field_id) if field_id else None,
            }
            opts.update(overrides)
            page.evaluate("opts => window.setup(opts)", opts)

        def events(self, kind=None):
            return [e["detail"] for e in page.evaluate("window.events") if kind is None or e["type"] == kind]

        def ops(self):
            return [e["op"] for e in self.events("op")]

    h = Harness()
    h.setup()
    try:
        yield h
        assert not errors, errors
    finally:
        context.close()


# ---- schema list ----


def test_picking_a_leaf_emits_pick(panels):
    panels.page.locator('.fc-sl-leaf[data-path="applicant.dob"]').click()
    assert panels.events("pick") == [{"path": "applicant.dob"}]
    assert panels.page.locator(".fc-sl-leaf.is-picked").get_attribute("data-path") == "applicant.dob"
    panels.page.locator('.fc-sl-leaf[data-path="applicant.email"]').press("Enter")
    assert panels.events("pick")[-1] == {"path": "applicant.email"}


def test_status_marks_follow_the_template(panels):
    def status(path):
        leaf = panels.page.locator(f'.fc-sl-leaf[data-path="{path}"]')
        return leaf.get_attribute("data-status"), leaf.locator(".fc-sl-st").inner_text()

    assert status("applicant.name") == ("placed", "✓")
    assert status("applicant.email") == ("none", "·")
    assert status("status") == ("partly", "½")  # 2 boxes for 3 options
    assert status("items[].qty") == ("placed", "✓")  # a table covers its columns
    assert panels.page.locator('.fc-sl-leaf[data-path="items[].qty"] .fc-sl-name').inner_text() == "qty"
    # placing the third option box completes it
    panels.template["fields"].append(field("s3", "radio_option", "Closed", paths=["status"]))
    panels.setup()
    assert status("status") == ("placed", "✓")
    # objects are headings, not leaves
    assert panels.page.locator(".fc-sl-grp", has_text="applicant").count() >= 1
    assert panels.page.locator('.fc-sl-leaf[data-path="applicant"]').count() == 0


def test_orphan_path_is_listed_as_not_in_schema(panels):
    leaf = panels.page.locator('.fc-sl-leaf[data-path="applicant.fax"]')
    assert leaf.get_attribute("data-status") == "orphan"
    assert "not in schema" in leaf.inner_text()
    assert leaf.get_attribute("draggable") is None


def test_leaves_drag_with_the_path_mime_type(panels):
    got = panels.page.evaluate(
        """() => {
          const leaf = document.querySelector('.fc-sl-leaf[data-path="applicant.email"]');
          const dt = new DataTransfer();
          leaf.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: dt }));
          return { draggable: leaf.draggable, path: dt.getData("application/x-formcast-path") };
        }"""
    )
    assert got == {"draggable": True, "path": "applicant.email"}
    panels.setup(draggable=False)
    assert panels.page.locator('.fc-sl-leaf[data-path="applicant.email"]').get_attribute("draggable") is None


# ---- inspector ----


def test_inspector_shows_the_field(panels):
    ins = panels.page.locator("#inspector")
    assert ins.locator('[data-ctl="label"]').input_value() == "Name"
    assert ins.locator('[data-ctl="type"]').input_value() == "text"
    assert ins.locator('[data-ctl="path"]').input_value() == "applicant.name"
    options = ins.locator('[data-ctl="path"] option').all_inner_texts()
    assert options[0] == "not linked"
    assert "applicant.dob" in options and "items[].sku" in options
    assert "applicant" not in options and "items" not in options  # objects and tables are not for a text box
    panels.setup(field_id=None)
    assert "Select a box" in panels.page.locator("#inspector").inner_text()


def test_label_and_type_emit_ops(panels):
    ins = panels.page.locator("#inspector")
    ins.locator('[data-ctl="label"]').fill("Applicant name")
    ins.locator('[data-ctl="label"]').press("Enter")
    ins.locator('[data-ctl="type"]').select_option("signature")
    assert panels.ops() == [
        {"op": "set_label", "id": "name", "label": "Applicant name"},
        {"op": "set_type", "id": "name", "type": "signature"},
    ]


def test_changing_the_data_field_emits_one_set_source(panels):
    ins = panels.page.locator("#inspector")
    ins.locator('[data-ctl="path"]').select_option("applicant.dob")
    ops = panels.ops()
    assert len(ops) == 1
    assert ops[0]["op"] == "set_source" and ops[0]["id"] == "name"
    assert ops[0]["source"]["paths"] == ["applicant.dob"]
    assert ops[0]["source"]["transforms"] == []
    ins.locator('[data-ctl="path"]').select_option("")
    assert panels.ops()[-1] == {"op": "set_source", "id": "name", "source": None}


def test_linking_an_unlinked_field(panels):
    panels.setup(field_id="email")
    panels.page.locator('#inspector [data-ctl="path"]').select_option("applicant.email")
    (op,) = panels.ops()
    assert op["source"]["paths"] == ["applicant.email"] and op["source"]["transforms"] == []
    # nothing to transform until a data field is chosen
    assert panels.page.locator('#inspector [data-ctl="add-transform"]').count() == 0


def test_adding_a_date_format_transform_emits_the_chain_once_it_is_valid(panels):
    panels.setup(field_id="dob")
    ins = panels.page.locator("#inspector")
    ins.locator('[data-ctl="add-transform"]').select_option("date_format")
    assert panels.ops() == []  # no format yet: held as a draft, not applied
    assert "Not applied yet" in ins.locator(".fc-chain-error").inner_text()
    fmt = ins.locator('[data-ctl="arg-0-format"]')
    fmt.fill("%m/%d/%Y")
    fmt.press("Enter")
    (op,) = panels.ops()
    assert op["op"] == "set_source" and op["id"] == "dob"
    assert op["source"]["paths"] == ["applicant.dob"]
    assert op["source"]["transforms"] == [{"op": "date_format", "args": {"format": "%m/%d/%Y"}}]
    assert ins.locator(".fc-chain-error").count() == 0
    # the typed value survives the redraw and keeps focus where it was
    assert ins.locator('[data-ctl="arg-0-format"]').input_value() == "%m/%d/%Y"


def test_transform_chain_reorders_and_removes(panels):
    chain = [{"op": "strip", "args": {}}, {"op": "upper", "args": {}}, {"op": "prefix", "args": {"value": "N: "}}]
    by_id(panels.template, "name")["source"]["transforms"] = chain
    panels.setup()
    ins = panels.page.locator("#inspector")
    assert ins.locator(".fc-step").evaluate_all("els => els.map(e => e.dataset.op)") == ["strip", "upper", "prefix"]
    ins.locator('[data-ctl="step-down-0"]').click()
    ops = [s["op"] for s in panels.ops()[-1]["source"]["transforms"]]
    assert ops == ["upper", "strip", "prefix"]
    panels.setup()  # the host shows the unchanged field again
    ins.locator('[data-ctl="step-remove-1"]').click()
    assert [s["op"] for s in panels.ops()[-1]["source"]["transforms"]] == ["strip", "prefix"]
    # arguments come from the transform's schema
    panels.setup()
    ins.locator('[data-ctl="arg-2-value"]').fill("Id: ")
    ins.locator('[data-ctl="arg-2-value"]').press("Enter")
    assert panels.ops()[-1]["source"]["transforms"][2] == {"op": "prefix", "args": {"value": "Id: "}}


def test_arguments_of_every_kind_are_built_from_the_schema(panels):
    chain = [
        {"op": "date_part", "args": {"part": "month"}},
        {"op": "number_format", "args": {}},
        {"op": "equals", "args": {"value": 3}},
    ]
    by_id(panels.template, "dob")["source"]["transforms"] = chain
    panels.setup(field_id="dob")
    ins = panels.page.locator("#inspector")
    part = ins.locator('[data-ctl="arg-0-part"]')
    assert part.evaluate("s => s.tagName") == "SELECT"
    assert part.locator("option").all_inner_texts() == ["month", "day", "year", "year2"]
    assert ins.locator('[data-ctl="arg-1-decimals"]').get_attribute("type") == "number"
    assert ins.locator('[data-ctl="arg-1-decimals"]').get_attribute("placeholder") == "2"
    assert ins.locator('[data-ctl="arg-2-value"]').input_value() == "3"
    ins.locator('[data-ctl="arg-2-value"]').fill("true")
    ins.locator('[data-ctl="arg-2-value"]').press("Enter")
    assert panels.ops()[-1]["source"]["transforms"][2] == {"op": "equals", "args": {"value": True}}


def test_style_controls_emit_set_style(panels):
    ins = panels.page.locator("#inspector")
    ins.locator('[data-ctl="align"]').select_option("center")
    ins.locator('[data-ctl="font-mode"]').select_option("fixed")
    ins.locator('[data-ctl="comb"]').fill("9")
    ins.locator('[data-ctl="comb"]').press("Enter")
    ins.locator('[data-ctl="min-size"]').fill("4")
    ins.locator('[data-ctl="min-size"]').press("Enter")
    assert panels.ops() == [
        {"op": "set_style", "id": "name", "style": {"align": "center"}},
        {"op": "set_style", "id": "name", "style": {"font_size": 10}},
        {"op": "set_style", "id": "name", "style": {"comb": 9}},
        {"op": "set_style", "id": "name", "style": {"min_font_size": 4}},
    ]
    by_id(panels.template, "name")["style"].update(font_size=12, comb=9)
    panels.setup()
    assert panels.page.locator('#inspector [data-ctl="font-size"]').input_value() == "12"
    panels.page.locator('#inspector [data-ctl="font-size"]').fill("14")
    panels.page.locator('#inspector [data-ctl="font-size"]').press("Enter")
    panels.page.locator('#inspector [data-ctl="comb"]').fill("")
    panels.page.locator('#inspector [data-ctl="comb"]').press("Enter")
    assert panels.ops()[-2:] == [
        {"op": "set_style", "id": "name", "style": {"font_size": 14}},
        {"op": "set_style", "id": "name", "style": {"comb": None}},
    ]


def test_checkbox_has_no_text_style_controls_and_table_lists_segments(panels):
    panels.setup(field_id="s1")
    assert panels.page.locator('#inspector [data-ctl="align"]').count() == 0
    panels.setup(field_id="items")
    text = panels.page.locator("#inspector").inner_text()
    assert "page 1" in text and "sku, qty" in text
    assert panels.page.locator('#inspector [data-ctl="type"]').is_disabled()


def test_orphaned_path_is_flagged_and_kept(panels):
    panels.setup(field_id="old")
    ins = panels.page.locator("#inspector")
    assert "This field's data field is not in the current schema" in ins.locator(".fc-ins-warn.is-orphan").inner_text()
    assert ins.locator('[data-ctl="path"]').input_value() == "applicant.fax"  # shown, not silently unlinked
    assert panels.ops() == []


def test_warnings_are_listed(panels):
    panels.setup(warnings=["Box is 26% of the line.", {"code": "overflow", "message": "Text is cut off."}])
    text = panels.page.locator("#inspector").inner_text()
    assert "Box is 26% of the line." in text and "Text is cut off." in text


# ---- history ----


def test_history_lists_newest_first_with_authors(panels):
    items = panels.page.locator("#history .fc-hi-list li")
    assert items.count() == 2
    first, second = items.nth(0).inner_text(), items.nth(1).inner_text()
    assert "you" in first and "move · Date of birth" in first
    assert "AI" in second and "set label · Name" in second and "first pass" in second


def test_history_undo_redo_buttons(panels):
    undo, redo = panels.page.locator('#history [data-action="undo"]'), panels.page.locator('#history [data-action="redo"]')
    assert undo.is_disabled() and redo.is_disabled()
    panels.setup(canUndo=True, canRedo=True)
    undo.click()
    redo.click()
    assert [e["type"] for e in panels.page.evaluate("window.events")] == ["undo", "redo"]


# ---- report ----

REPORT = {
    "template_id": "t",
    "fingerprint_ok": True,
    "strict": False,
    "writer": "browser",
    "fields": [
        {"id": "name", "type": "text", "page": 0, "paths": ["applicant.name"], "binding": "created", "value": "Ana <b>Sí</b>", "warnings": []},
        {
            "id": "dob",
            "type": "text",
            "page": 0,
            "paths": ["applicant.dob"],
            "binding": "created",
            "value": None,
            "warnings": [{"code": "missing", "severity": "warning", "message": "applicant.dob has no value"}],
        },
        {"id": "s1", "type": "radio_option", "page": 0, "paths": ["status"], "binding": "created", "value": True, "warnings": []},
    ],
    "tables": [
        {"id": "items", "path": "items", "rows_provided": 6, "rows_written": 4, "rows_dropped": 2, "rows_per_segment": [4], "cells": [], "warnings": []}
    ],
    "paths": {},
    "gaps": ["email"],
    "warnings": [],
    "counts": {"fields_written": 2, "fields_empty": 1, "cells_written": 8, "missing": 1, "rows_dropped": 1, "shrunk": 3},
}


def test_report_renders_counts_fields_tables_and_the_browser_note(panels):
    panels.setup(report=REPORT)
    rp = panels.page.locator("#report")
    # text as written, not as the CSS capitalises it
    flat = rp.locator(".fc-rp-stat").evaluate_all("els => els.map(e => e.children[0].textContent + ' ' + e.children[1].textContent)")
    assert flat[:3] == ["2 fields written", "1 fields left empty", "8 table cells written"]
    # warnings first, then info
    assert flat[3:] == ["1 missing", "1 rows dropped", "3 shrunk"]
    assert rp.locator('.fc-rp-stat[data-severity="info"]').count() == 1
    row = rp.locator('tr[data-field="dob"]')
    assert "applicant.dob has no value" in row.inner_text() and "—" in row.inner_text()
    assert "on" in rp.locator('tr[data-field="s1"] .fc-rp-val').inner_text()
    assert rp.locator('tr[data-field="name"] .fc-rp-val').inner_text() == "Ana <b>Sí</b>"  # text, never markup
    assert rp.locator("tr[data-field] b").count() == 0
    assert "4 of 6 rows written, 2 dropped" in rp.locator(".fc-rp-tbl").inner_text()
    assert "email" in rp.inner_text()
    assert "Filled in this browser" in rp.locator(".fc-rp-writer").inner_text()


def test_report_without_a_browser_writer_has_no_note(panels):
    panels.setup(report={**REPORT, "writer": None})
    assert panels.page.locator("#report .fc-rp-writer").count() == 0
