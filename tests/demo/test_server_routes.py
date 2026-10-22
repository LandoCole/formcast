"""demo/server routes: seed, apply, rebind, matches, fill, smart PDF, the template store and the record source.

Servers run in-process over a copy of ``demo/data`` (conftest ``start_demo_server``). Nothing here calls a
model. The one-line-edited fw9 is built with PyMuPDF from the dev form (dev forms only).
"""

from __future__ import annotations

import base64
import csv
import json
import logging
import threading
from pathlib import Path
from urllib.parse import quote

import pymupdf
import pytest
from test_server_documents import GatedBuilder, get_json, http_request, upload

from demo.server import api as api_mod
from demo.server import documents as documents_mod
from formcast import match as match_mod
from formcast import Template, apply, fingerprint
from formcast.errors import OpError
from formcast.pagemap.build import DetectPolicy, build_form_map

ROOT = Path(__file__).resolve().parents[2]
FORMS = ROOT / "demo" / "data" / "forms"
FW9 = (FORMS / "fw9.pdf").read_bytes()
WADOC = (FORMS / "wadoc_auth.pdf").read_bytes()
MENOMONIE = (FORMS / "menomonie_app.pdf").read_bytes()


def edited(pdf: bytes) -> bytes:
    """Redact the first text line of four or more words on page 1 and write different text there."""
    doc = pymupdf.open(stream=pdf)
    page = doc[0]
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if len("".join(s["text"] for s in line["spans"]).split()) >= 4:
                rect, size = pymupdf.Rect(line["bbox"]), line["spans"][0]["size"]
                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()
                page.insert_text((rect.x0, rect.y1 - 2), "Revised 3-2027 Cat. No. 99999Z", fontsize=min(size, 9))
                return doc.tobytes(garbage=4, deflate=True)
    raise AssertionError("no text line to edit")


FW9_EDITED = edited(FW9)
NO_DETECT = DetectPolicy(digital=(), scanned=())


@pytest.fixture(autouse=True)
def no_model_loads(request, monkeypatch):
    """Form maps are built without detectors or OCR (no model load, no network); the ``slow`` test uses the real ones."""
    if request.node.get_closest_marker("slow"):
        return
    real = documents_mod.build_form_map
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None: real(pdf, detectors={}, policy=NO_DETECT, ocr="never"))


def tpl(name: str) -> str:
    return "/api/templates/" + quote(name, safe="")


def open_doc(base: str, name: str, data: bytes) -> str:
    status, doc = upload(base, name, data)
    assert status == 200, doc
    return doc["id"]


def stored(base: str, name: str) -> dict:
    status, body = get_json(base, tpl(name))
    assert status == 200, body
    return body["template"]


def post(base: str, path: str, body: dict, method: str = "POST"):
    return get_json(base, path, method, body)


def browser_style(template: dict) -> dict:
    """What the browser demo records: no fingerprint, no signature."""
    t = json.loads(json.dumps(template))
    t["form"]["fingerprint"] = None
    t["form"]["signature"] = None
    return t


# ------------------------------------------------------------------ seed


def test_seed_on_fw9_returns_widget_fields_and_the_schema(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    status, body = post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})
    assert status == 200
    template = Template.model_validate(body["template"])
    widgets = [f for f in template.fields if f.origin == "widget"]
    assert widgets and all(f.id.startswith("w0_") or f.id.startswith("w") for f in widgets)
    assert any(f.id.startswith("w0_") for f in widgets)
    assert all(f.existing_widget for f in widgets)
    assert template.schema_["title"] == "Vendor tax profile"
    assert template.form.fingerprint == fingerprint(FW9)
    assert template.history == []


def test_seed_takes_a_schema_object_too(demo_server):
    from formcast.template.model import new_template

    doc = open_doc(demo_server, "fw9.pdf", FW9)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    status, body = post(demo_server, "/api/seed", {"doc": doc, "schema": schema})
    assert status == 200 and body["template"]["schema"] == schema
    expected = new_template(schema, build_form_map(FW9, detectors={}, policy=NO_DETECT, ocr="never"), candidates=True)
    assert [f["id"] for f in body["template"]["fields"]] == [f.id for f in expected.fields]


def test_seed_waits_for_the_form_map_build_the_upload_started(demo_server, monkeypatch):
    real = documents_mod.build_form_map  # the no-detector build from the autouse fixture
    builder = GatedBuilder(result=real)
    monkeypatch.setattr(documents_mod, "build_form_map", builder)
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    assert builder.started.wait(timeout=5)  # the upload started it
    got: list = []
    seed = threading.Thread(target=lambda: got.append(post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})))
    seed.start()
    seed.join(timeout=0.5)
    assert got == []  # waiting on the build under way
    builder.gate.set()
    seed.join(timeout=30)
    (status, body), = got
    assert status == 200 and body["template"]["fields"]
    assert len(builder.calls) == 1  # seed did not start a second build
    assert post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})[0] == 200 and len(builder.calls) == 1


@pytest.mark.slow
def test_seed_with_the_real_detectors_adds_candidate_fields_to_a_form_without_widgets(demo_server):
    doc = open_doc(demo_server, "wadoc_auth.pdf", WADOC)
    status, body = post(demo_server, "/api/seed", {"doc": doc, "schema": "wadoc_auth"})
    assert status == 200
    origins = {f["origin"] for f in body["template"]["fields"]}
    assert "detector" in origins and body["template"]["fields"]


@pytest.mark.parametrize(
    "payload, status, code",
    [
        ({"doc": "0" * 32, "schema": "fw9"}, 404, "not_found"),
        ({"schema": "fw9"}, 400, "bad_request"),
        ({"doc": 5, "schema": "fw9"}, 400, "bad_request"),
        ({"doc": "{doc}", "schema": "no_such_schema"}, 404, "not_found"),
        ({"doc": "{doc}", "schema": 7}, 400, "bad_schema"),
        ({"doc": "{doc}"}, 400, "bad_schema"),
    ],
)
def test_seed_rejects_bad_input_plainly(demo_server, payload, status, code):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    payload = {k: (doc if v == "{doc}" else v) for k, v in payload.items()}
    got, body = post(demo_server, "/api/seed", payload)
    assert (got, body["error"]) == (status, code), body


def test_a_body_that_is_not_an_object_is_a_400(demo_server):
    assert post(demo_server, "/api/seed", [1, 2])[0] == 400
    assert http_request(demo_server, "POST", "/api/seed", b"{nope", {"Content-Type": "application/json"})[0] == 400


# ------------------------------------------------------------------ apply


@pytest.fixture
def seeded(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    _, body = post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})
    return demo_server, doc, body["template"]


def first_widget(template: dict) -> dict:
    return next(f for f in template["fields"] if f["id"].startswith("w0_"))


def test_apply_a_move_changes_the_rect_and_adds_one_human_history_entry(seeded):
    base, doc, template = seeded
    field = first_widget(template)
    status, body = post(base, "/api/apply", {"template": template, "op": {"op": "move", "id": field["id"], "dx": 5, "dy": 3}, "author": "human", "note": "nudge"})
    assert status == 200 and body["warnings"] == []
    moved = next(f for f in body["template"]["fields"] if f["id"] == field["id"])
    assert moved["rect"] == [field["rect"][0] + 5, field["rect"][1] + 3, field["rect"][2] + 5, field["rect"][3] + 3]
    assert len(body["template"]["history"]) == len(template["history"]) + 1
    entry = body["template"]["history"][-1]
    assert entry["author"] == "human" and entry["note"] == "nudge" and entry["op"]["op"] == "move"
    assert template["history"] == []  # the request's template is not changed in place


def test_apply_by_the_agent_is_recorded_as_the_agent(seeded):
    base, doc, template = seeded
    field = first_widget(template)
    _, body = post(base, "/api/apply", {"template": template, "op": {"op": "set_label", "id": field["id"], "label": "A label"}, "author": "agent"})
    assert body["template"]["history"][-1]["author"] == "agent"


def test_an_invalid_op_is_422_with_the_librarys_message(seeded):
    base, doc, template = seeded
    op = {"op": "move", "id": "nope", "dx": 1}
    with pytest.raises(OpError) as lib:
        apply(Template.model_validate(template), op, author="human")
    status, body = post(base, "/api/apply", {"template": template, "op": op, "author": "human"})
    assert status == 422 and body == {"error": "op_rejected", "detail": lib.value.reason}
    status, body = post(base, "/api/apply", {"template": template, "op": {"op": "teleport"}, "author": "human"})
    assert status == 422 and body["error"] == "op_rejected" and "teleport" in body["detail"]


@pytest.mark.parametrize(
    "extra, code",
    [({"author": "robot"}, "bad_author"), ({}, "bad_author"), ({"author": "human", "note": 3}, "bad_request")],
)
def test_apply_rejects_a_bad_request_with_a_400(seeded, extra, code):
    base, doc, template = seeded
    status, body = post(base, "/api/apply", {"template": template, "op": {"op": "move", "id": "x", "dx": 1}, **extra})
    assert (status, body["error"]) == (400, code)
    assert post(base, "/api/apply", {"template": template, "author": "human"})[0] == 400  # no op


def test_apply_rejects_a_template_that_is_not_one(seeded):
    base, doc, template = seeded
    status, body = post(base, "/api/apply", {"template": {"template_id": "x"}, "op": {"op": "move", "id": "a", "dx": 1}, "author": "human"})
    assert (status, body["error"]) == (400, "bad_template") and "not a valid template" in body["detail"]
    assert post(base, "/api/apply", {"template": "x", "op": {}, "author": "human"})[1]["error"] == "bad_template"


def test_apply_with_a_matching_doc_checks_widget_names_and_kinds(seeded):
    base, doc, template = seeded
    field = first_widget(template)
    link = {"op": "link_widget", "id": field["id"], "widget": "no such widget"}
    # without a doc, any widget name may be linked
    status, body = post(base, "/api/apply", {"template": template, "op": link, "author": "human"})
    assert status == 200
    # with the doc the template was made for, the name must exist on the form
    status, body = post(base, "/api/apply", {"template": template, "op": link, "author": "human", "doc": doc})
    assert status == 422 and "unknown widget" in body["detail"] and body["error"] == "op_rejected"
    # a real name still links
    real = {"op": "link_widget", "id": field["id"], "widget": field["existing_widget"]}
    assert post(base, "/api/apply", {"template": template, "op": real, "author": "human", "doc": doc})[0] == 200
    # a type the linked widget cannot back is refused only when the form's widget kinds are known
    text_field = next(f for f in template["fields"] if f["type"] == "text" and f["existing_widget"])
    wrong = {"op": "set_type", "id": text_field["id"], "type": "checkbox"}
    assert post(base, "/api/apply", {"template": template, "op": wrong, "author": "human"})[0] == 200
    status, body = post(base, "/api/apply", {"template": template, "op": wrong, "author": "human", "doc": doc})
    assert status == 422 and body["error"] == "op_rejected", body


def test_apply_with_a_doc_for_another_form_validates_like_no_doc(seeded):
    base, doc, template = seeded
    other = open_doc(base, "wadoc_auth.pdf", WADOC)
    link = {"op": "link_widget", "id": first_widget(template)["id"], "widget": "no such widget"}
    assert post(base, "/api/apply", {"template": template, "op": link, "author": "human", "doc": other})[0] == 200
    # a template with no fingerprint cannot be matched to the doc either
    assert post(base, "/api/apply", {"template": browser_style(template), "op": link, "author": "human", "doc": doc})[0] == 200
    assert post(base, "/api/apply", {"template": template, "op": link, "author": "human", "doc": "0" * 32})[0] == 404


# ------------------------------------------------------------------ rebind


def test_rebind_stamps_the_pdfs_fingerprint_on_a_browser_template(seeded):
    base, doc, template = seeded
    plain = browser_style(template)
    status, body = post(base, "/api/rebind", {"template": plain, "doc": doc})
    assert status == 200
    assert body["template"]["form"]["fingerprint"] == fingerprint(FW9)
    assert body["template"]["form"]["signature"] is not None
    assert body["template"]["fields"] == plain["fields"] and body["template"]["history"] == plain["history"]


def test_rebind_to_a_pdf_that_does_not_fit_is_422_with_the_librarys_message(seeded):
    base, doc, template = seeded
    other = open_doc(base, "wadoc_auth.pdf", WADOC)
    status, body = post(base, "/api/rebind", {"template": template, "doc": other})
    assert status == 422 and body["error"] == "cannot_rebind"
    assert body["detail"].startswith("cannot rebind:") and "pages" in body["detail"]
    assert post(base, "/api/rebind", {"template": template, "doc": "f" * 32})[0] == 404


# ------------------------------------------------------------------ matches


def test_matches_rank_the_stored_template_first_as_reuse(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    status, matches = get_json(demo_server, f"/api/documents/{doc}/matches")
    assert status == 200 and isinstance(matches, list)
    assert [m["name"] for m in matches][0] == "fw9"
    assert {m["name"] for m in matches} == {"fw9", "menomonie_app", "wadoc_auth"}
    top = matches[0]
    assert set(top) == {"name", "template_id", "score", "basis", "explanation", "action"}
    assert top["score"] == 1.0 and top["action"] == "reuse" and top["basis"] == "fingerprint"
    assert top["template_id"] == stored(demo_server, "fw9")["template_id"]
    assert [m["score"] for m in matches] == sorted((m["score"] for m in matches), reverse=True)
    assert all(m["action"] == "new" for m in matches[1:])  # different forms


def test_a_one_line_edit_of_fw9_is_adapt(demo_server):
    assert fingerprint(FW9_EDITED) != fingerprint(FW9)
    doc = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    _, matches = get_json(demo_server, f"/api/documents/{doc}/matches")
    top = matches[0]
    assert top["name"] == "fw9" and top["action"] == "adapt" and top["basis"] == "signature"
    assert 0.60 <= top["score"] < 0.95 and "%" in top["explanation"]


def test_the_action_follows_the_settings_thresholds(start_demo_server):
    base = start_demo_server(reuse_threshold=0.999, adapt_threshold=0.2)
    doc = open_doc(base, "fw9_rev.pdf", FW9_EDITED)
    _, matches = get_json(base, f"/api/documents/{doc}/matches")
    assert matches[0]["action"] == "adapt"
    base = start_demo_server(reuse_threshold=0.5, adapt_threshold=0.4)
    doc = open_doc(base, "fw9_rev.pdf", FW9_EDITED)
    assert get_json(base, f"/api/documents/{doc}/matches")[1][0]["action"] == "reuse"


def test_matches_answer_while_the_form_map_build_holds_the_mupdf_lock(demo_server, monkeypatch):
    builder = GatedBuilder(result=documents_mod.build_form_map)  # the no-detector build, held at the gate
    monkeypatch.setattr(documents_mod, "build_form_map", builder)
    doc = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    try:
        assert builder.started.wait(timeout=5) and builder.calls[0][1]  # the build is under way, holding the lock
        got: list = []
        asking = threading.Thread(target=lambda: got.append(get_json(demo_server, f"/api/documents/{doc}/matches")))
        asking.start()
        asking.join(timeout=10)
        (status, matches), = got  # answered before the build finished
        assert status == 200 and matches[0]["name"] == "fw9" and matches[0]["action"] == "adapt"
        assert not builder.gate.is_set()
    finally:
        builder.gate.set()


def test_matches_read_the_pdf_once_for_all_templates(demo_server, monkeypatch):
    real, calls = documents_mod.pdf_features, []
    monkeypatch.setattr(documents_mod, "pdf_features", lambda pdf: calls.append(1) or real(pdf))
    doc = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    first = get_json(demo_server, f"/api/documents/{doc}/matches")[1]
    second = get_json(demo_server, f"/api/documents/{doc}/matches")[1]
    assert len(first) == 3 and first == second
    assert calls == [1]  # three templates scored, two requests, one read of the PDF


def test_matches_skip_a_stored_file_that_is_not_a_template_and_work_on_browser_templates(start_demo_server, tmp_path):
    base = start_demo_server()
    templates = tmp_path / "data" / "templates"
    (templates / "broken.json").write_text(json.dumps({"template_id": "x", "form": {"page_count": 1}}), encoding="utf-8")
    plain = browser_style(stored(base, "fw9"))
    assert post(base, tpl("browser fw9"), {"template": plain}, "PUT")[0] == 200
    doc = open_doc(base, "fw9.pdf", FW9)
    status, matches = get_json(base, f"/api/documents/{doc}/matches")
    assert status == 200
    names = {m["name"]: m for m in matches}
    assert "broken" not in names
    assert names["browser fw9"]["basis"] == "labels" and names["fw9"]["basis"] == "fingerprint"


def test_matches_for_an_unknown_document_is_404(demo_server):
    assert get_json(demo_server, f"/api/documents/{'0' * 32}/matches")[0] == 404


# ------------------------------------------------------------------ fill


def decode_pdf(body: dict) -> bytes:
    pdf = base64.b64decode(body["pdf_base64"], validate=True)
    assert pdf.startswith(b"%PDF")
    return pdf


def widget_values(pdf: bytes) -> dict[str, object]:
    out: dict[str, object] = {}
    with pymupdf.open(stream=pdf) as doc:
        for page in doc:
            for w in page.widgets() or []:
                out[w.field_name] = w.field_value
    return out


def test_fill_with_a_stored_record_returns_a_pdf_and_a_report(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    status, body = post(demo_server, "/api/fill", {"template": template, "doc": doc, "record_ref": {"schema": "fw9", "id": "fw9-01"}})
    assert status == 200, body
    pdf = decode_pdf(body)
    assert body["report"]["template_id"] == template["template_id"]
    assert body["report"]["fingerprint_ok"] is True and "fingerprint_note" not in body
    assert body["report"]["counts"]["fields_written"] > 0
    assert any(isinstance(v, str) and "Juniper Lane Ceramics" in v for v in widget_values(pdf).values())
    assert widget_values(FW9) != widget_values(pdf)  # the blank was not changed in place


def test_fill_with_an_inline_record_and_with_none(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    record = {"legal_name": "Inline Example Co", "address": {"city": "Springfield"}}
    status, body = post(demo_server, "/api/fill", {"template": template, "doc": doc, "record": record})
    assert status == 200 and any(v == "Inline Example Co" for v in widget_values(decode_pdf(body)).values())
    status, body = post(demo_server, "/api/fill", {"template": template, "doc": doc})  # an empty record: everything is a gap
    assert status == 200 and body["report"]["counts"]["fields_written"] == 0


@pytest.mark.parametrize(
    "extra, status, code",
    [
        ({"record": {"a": 1}, "record_ref": {"schema": "fw9", "id": "fw9-01"}}, 400, "bad_request"),
        ({"record": [1]}, 400, "bad_request"),
        ({"record_ref": {"schema": "fw9"}}, 400, "bad_request"),
        ({"record_ref": "fw9-01"}, 400, "bad_request"),
        ({"record_ref": {"schema": "fw9", "id": "nope"}}, 404, "not_found"),
        ({"record_ref": {"schema": "nope", "id": "fw9-01"}}, 404, "not_found"),
        ({"doc": "0" * 32}, 404, "not_found"),
        ({"doc": None}, 400, "bad_request"),
    ],
)
def test_fill_rejects_bad_requests(demo_server, extra, status, code):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    payload = {"template": stored(demo_server, "fw9"), "doc": doc, **extra}
    got, body = post(demo_server, "/api/fill", payload)
    assert (got, body["error"]) == (status, code), body


def test_fill_skips_the_fingerprint_check_for_a_browser_template_and_says_so(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    plain = browser_style(stored(demo_server, "fw9"))
    status, body = post(demo_server, "/api/fill", {"template": plain, "doc": doc, "record_ref": {"schema": "fw9", "id": "fw9-01"}})
    assert status == 200
    decode_pdf(body)
    assert "no fingerprint" in body["fingerprint_note"] and "Rebind" in body["fingerprint_note"]
    assert body["report"]["counts"]["fields_written"] > 0
    # after a rebind the check is on again and the note is gone
    _, rebound = post(demo_server, "/api/rebind", {"template": plain, "doc": doc})
    status, body = post(demo_server, "/api/fill", {"template": rebound["template"], "doc": doc, "record": {"legal_name": "X"}})
    assert status == 200 and "fingerprint_note" not in body


MISMATCH_DETAIL = "This template was made for a different version of this form. Rebind it to this PDF first."


def test_fill_of_a_revised_pdf_is_refused_with_409_until_rebound(demo_server):
    doc = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    template = stored(demo_server, "fw9")
    ask = {"template": template, "doc": doc, "record_ref": {"schema": "fw9", "id": "fw9-01"}}
    status, body = post(demo_server, "/api/fill", ask)
    assert (status, body) == (409, {"error": "fingerprint_mismatch", "detail": MISMATCH_DETAIL})
    # check_fingerprint: true is the same as leaving it out; only an explicit false overrides
    assert post(demo_server, "/api/fill", {**ask, "check_fingerprint": True})[0] == 409
    assert post(demo_server, "/api/fill", {**ask, "check_fingerprint": "no"})[0] == 400
    # after a rebind to the revision, the fill is checked and goes through with no note
    _, rebound = post(demo_server, "/api/rebind", {"template": template, "doc": doc})
    status, body = post(demo_server, "/api/fill", {**ask, "template": rebound["template"]})
    assert status == 200 and "fingerprint_note" not in body and body["report"]["fingerprint_ok"] is True


def test_fill_of_a_revised_pdf_goes_ahead_only_when_the_request_says_not_to_check(demo_server):
    doc = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    template = stored(demo_server, "fw9")
    ask = {"template": template, "doc": doc, "record_ref": {"schema": "fw9", "id": "fw9-01"}, "check_fingerprint": False}
    status, body = post(demo_server, "/api/fill", ask)
    assert status == 200
    decode_pdf(body)
    assert "not the exact form" in body["fingerprint_note"]
    assert body["report"]["fingerprint_ok"] is False and body["report"]["counts"]["fingerprint_mismatch"] == 1


def test_fill_with_the_matching_fingerprint_is_checked_and_a_null_one_is_unchecked_with_a_note(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    status, body = post(demo_server, "/api/fill", {"template": template, "doc": doc, "record": {}, "check_fingerprint": False})
    assert status == 200 and "fingerprint_note" not in body and body["report"]["fingerprint_ok"] is True  # equal: still checked
    status, body = post(demo_server, "/api/fill", {"template": browser_style(template), "doc": doc, "record": {}})
    assert status == 200 and "no fingerprint" in body["fingerprint_note"]


def test_smart_pdf_follows_the_same_fingerprint_rule(demo_server):
    revised = open_doc(demo_server, "fw9_rev.pdf", FW9_EDITED)
    same = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    status, body = post(demo_server, "/api/smart-pdf", {"template": template, "doc": revised})
    assert (status, body) == (409, {"error": "fingerprint_mismatch", "detail": MISMATCH_DETAIL})
    status, body = post(demo_server, "/api/smart-pdf", {"template": template, "doc": revised, "check_fingerprint": False})
    assert status == 200 and "not the exact form" in body["fingerprint_note"]
    decode_pdf(body)
    status, body = post(demo_server, "/api/smart-pdf", {"template": template, "doc": same})
    assert status == 200 and "fingerprint_note" not in body
    assert post(demo_server, "/api/smart-pdf", {"template": browser_style(template), "doc": same})[1]["fingerprint_note"]


def test_fill_of_a_filled_copy_still_checks_the_fingerprint(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    _, first = post(demo_server, "/api/fill", {"template": template, "doc": doc, "record": {"legal_name": "First Co"}})
    again = open_doc(demo_server, "filled.pdf", decode_pdf(first))
    status, body = post(demo_server, "/api/fill", {"template": template, "doc": again, "record": {"legal_name": "Second Co"}})
    assert status == 200 and "fingerprint_note" not in body and body["report"]["fingerprint_ok"] is True


def test_fill_for_a_pdf_with_another_page_count_is_a_plain_400_once_the_fingerprint_check_is_waived(demo_server):
    doc = open_doc(demo_server, "wadoc_auth.pdf", WADOC)
    ask = {"template": stored(demo_server, "fw9"), "doc": doc, "record": {}}
    assert post(demo_server, "/api/fill", ask)[0] == 409  # a different form: rebind first
    status, body = post(demo_server, "/api/fill", {**ask, "check_fingerprint": False})
    assert status == 400 and body["error"] == "bad_input" and "page count mismatch" in body["detail"]


def test_a_malformed_record_fills_with_invalid_in_the_report_not_a_500(start_demo_server, tmp_path):
    path = tmp_path / "data" / "records" / "menomonie_app.csv"
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0])
    rows[0]["certifications"] = '["CPR", "First Aid'  # an array cell that is not JSON
    rows[0]["employment_history"] = "{not json"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    base = start_demo_server()
    doc = open_doc(base, "menomonie_app.pdf", MENOMONIE)
    template = stored(base, "menomonie_app")
    status, body = post(base, "/api/fill", {"template": template, "doc": doc, "record_ref": {"schema": "menomonie_app", "id": rows[0]["id"]}})
    assert status == 200, body
    decode_pdf(body)
    assert body["report"]["counts"]["invalid"] >= 1
    assert body["report"]["counts"]["fields_written"] > 0


def test_a_record_source_that_cannot_read_its_file_is_422_with_its_message(start_demo_server):
    from demo.server.records import RecordSourceError

    class Broken:
        def schemas(self):
            return {}

        def list(self, schema):
            raise RecordSourceError("records/fw9.csv is not UTF-8 text")

        def get(self, schema, id):
            raise RecordSourceError("records/fw9.csv is not UTF-8 text")

    base = start_demo_server(records=Broken())
    doc = open_doc(base, "fw9.pdf", FW9)
    status, body = post(base, "/api/fill", {"template": stored(base, "fw9"), "doc": doc, "record_ref": {"schema": "fw9", "id": "fw9-01"}})
    assert (status, body) == (422, {"error": "bad_records", "detail": "records/fw9.csv is not UTF-8 text"})
    assert get_json(base, "/api/records?schema=fw9")[0] == 422 and get_json(base, "/api/records/fw9/x")[0] == 422


def test_smart_pdf_returns_a_pdf_with_a_field_for_every_template_field(demo_server):
    doc = open_doc(demo_server, "wadoc_auth.pdf", WADOC)
    template = stored(demo_server, "wadoc_auth")
    status, body = post(demo_server, "/api/smart-pdf", {"template": template, "doc": doc})
    assert status == 200 and set(body) == {"pdf_base64"}
    pdf = decode_pdf(body)
    with pymupdf.open(stream=pdf) as out:
        assert out.page_count == template["form"]["page_count"]
    assert len(widget_values(pdf)) >= len(widget_values(WADOC))


def test_smart_pdf_of_a_browser_template_adds_the_note(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    status, body = post(demo_server, "/api/smart-pdf", {"template": browser_style(stored(demo_server, "fw9")), "doc": doc})
    assert status == 200 and "no fingerprint" in body["fingerprint_note"]
    assert post(demo_server, "/api/smart-pdf", {"template": stored(demo_server, "fw9"), "doc": "1" * 32})[0] == 404


# ------------------------------------------------------------------ MuPDF lock


def test_fill_smart_pdf_rebind_form_map_and_features_run_under_the_mupdf_lock(demo_server, monkeypatch):
    lock = documents_mod.PYMUPDF_LOCK
    seen: dict[str, list[bool]] = {}

    def guard(module, name):
        real = getattr(module, name)

        def wrapper(*args, **kwargs):
            seen.setdefault(name, []).append(lock._is_owned())
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for name in ("fill", "to_smart_pdf", "rebind"):
        guard(api_mod, name)
    for name in ("build_form_map", "pdf_features"):
        guard(documents_mod, name)
    for name in ("fingerprint_document", "compute_signature"):  # the features compute these lazily
        guard(match_mod, name)
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})
    post(demo_server, "/api/rebind", {"template": template, "doc": doc})
    post(demo_server, "/api/fill", {"template": template, "doc": doc, "record": {}})
    post(demo_server, "/api/smart-pdf", {"template": template, "doc": doc})
    get_json(demo_server, f"/api/documents/{doc}/matches")
    names = ["fill", "to_smart_pdf", "rebind", "build_form_map", "pdf_features", "fingerprint_document", "compute_signature"]
    assert set(seen) == set(names)
    assert all(seen[n] and all(seen[n]) for n in names), seen  # every call, not just the last, held the lock


# ------------------------------------------------------------------ template store


def test_templates_list_get_put_delete(demo_server):
    status, body = get_json(demo_server, "/api/templates")
    assert status == 200
    assert {t["name"] for t in body["templates"]} == {"fw9", "menomonie_app", "wadoc_auth"}
    assert set(body["templates"][0]) == {"name", "template_id", "page_count", "updated"}
    template = stored(demo_server, "fw9")
    assert Template.model_validate(template).template_id == template["template_id"]

    status, body = post(demo_server, tpl("My copy.v2"), {"template": template}, "PUT")
    assert status == 200 and body == {"name": "My copy.v2", "template_id": template["template_id"]}
    assert stored(demo_server, "My copy.v2") == template  # a stored template reads back unchanged
    assert any(t["name"] == "My copy.v2" for t in get_json(demo_server, "/api/templates")[1]["templates"])
    # PUT replaces
    changed = json.loads(json.dumps(template))
    changed["template_id"] = "replaced"
    assert post(demo_server, tpl("My copy.v2"), {"template": changed}, "PUT")[0] == 200
    assert stored(demo_server, "My copy.v2")["template_id"] == "replaced"

    status, body = get_json(demo_server, tpl("My copy.v2"), "DELETE")
    assert status == 200 and body == {"deleted": "My copy.v2"}
    assert get_json(demo_server, tpl("My copy.v2"))[0] == 404
    assert get_json(demo_server, tpl("My copy.v2"), "DELETE")[0] == 404
    assert get_json(demo_server, tpl("never saved"))[0] == 404


def test_saving_under_a_shipped_name_goes_to_the_local_folder_and_leaves_the_shipped_file(start_demo_server, tmp_path):
    base = start_demo_server()
    data = start_demo_server.servers[-1].api.settings.data_dir
    shipped = (data / "templates" / "fw9.json").read_bytes()
    template = stored(base, "fw9")
    template["template_id"] = "my-fw9"
    assert post(base, tpl("fw9"), {"template": template}, "PUT")[0] == 200
    assert post(base, tpl("mine"), {"template": template}, "PUT")[0] == 200
    assert (data / "templates" / "fw9.json").read_bytes() == shipped
    assert sorted(p.name for p in (data / "templates").iterdir()) == ["fw9.json", "menomonie_app.json", "wadoc_auth.json"]
    assert sorted(p.name for p in (data / "local" / "templates").iterdir()) == ["fw9.json", "mine.json"]
    listed = {t["name"]: t["template_id"] for t in get_json(base, "/api/templates")[1]["templates"]}
    assert set(listed) == {"fw9", "menomonie_app", "wadoc_auth", "mine"} and listed["fw9"] == "my-fw9"
    assert stored(base, "fw9")["template_id"] == "my-fw9"
    status, body = get_json(base, tpl("wadoc_auth"), "DELETE")
    assert (status, body["error"]) == (409, "read_only") and "came with the demo" in body["detail"]
    assert get_json(base, tpl("fw9"), "DELETE")[0] == 200  # the saved copy goes; the shipped one is back
    assert stored(base, "fw9")["template_id"] != "my-fw9"


@pytest.mark.parametrize(
    "name",
    ["..", "%2e%2e%2fx", "..%2f..%2fetc%2fpasswd", "a%2Fb", "%2Fetc%2Fpasswd", ".hidden", "%20lead", "x" * 81, "a%00b", "caf%C3%A9", "back%5Cslash", "tab%09"],
)
def test_a_bad_template_name_is_400_on_every_verb_and_writes_nothing(start_demo_server, tmp_path, name):
    base = start_demo_server()
    template = stored(base, "fw9")
    before = sorted(p.name for p in (tmp_path / "data").rglob("*"))
    for method, body in (("GET", None), ("PUT", json.dumps({"template": template}).encode()), ("DELETE", None)):
        status, _, out = http_request(base, method, f"/api/templates/{name}", body, {"Content-Type": "application/json"})
        assert status == 400, (method, name, out)
        assert json.loads(out)["error"] == "bad_request" and json.loads(out)["detail"] == "bad template name"
    assert sorted(p.name for p in (tmp_path / "data").rglob("*")) == before
    assert not list(tmp_path.glob("*.json")) and not (tmp_path / "x.json").exists()


def test_put_template_rejects_a_body_that_is_not_a_template(demo_server):
    status, body = post(demo_server, "/api/templates/ok", {"template": {"nope": 1}}, "PUT")
    assert (status, body["error"]) == (400, "bad_template")
    assert post(demo_server, "/api/templates/ok", {}, "PUT")[1]["error"] == "bad_template"
    assert get_json(demo_server, "/api/templates/ok")[0] == 404


def test_a_stored_file_that_is_not_a_template_is_422_on_get(start_demo_server, tmp_path):
    (tmp_path / "data" / "templates" / "junk.json").write_text("[1, 2]", encoding="utf-8")
    base = start_demo_server()
    status, body = get_json(base, "/api/templates/junk")
    assert (status, body["error"]) == (422, "bad_template_file")
    assert "junk" not in {t["name"] for t in get_json(base, "/api/templates")[1]["templates"]}


# ------------------------------------------------------------------ schemas and records


def test_schemas_and_records(demo_server):
    status, body = get_json(demo_server, "/api/schemas")
    assert status == 200 and set(body["schemas"]) == {"fw9", "menomonie_app", "wadoc_auth"}
    assert body["schemas"]["fw9"]["title"] == "Vendor tax profile"
    status, listing = get_json(demo_server, "/api/records?schema=fw9")
    assert status == 200 and listing["schema"] == "fw9" and len(listing["records"]) >= 3
    assert set(listing["records"][0]) == {"id", "title"}
    first = listing["records"][0]["id"]
    status, one = get_json(demo_server, f"/api/records/fw9/{first}")
    assert status == 200 and one["record"]["legal_name"] and one["record"]["address"]["city"]


def test_a_record_id_with_a_slash_or_a_percent_sign_is_fetched_by_its_encoded_id(start_demo_server):
    base = start_demo_server()
    data = start_demo_server.servers[-1].api.settings.data_dir
    with (data / "records" / "fw9.csv").open("a", encoding="utf-8") as f:
        f.write("2026/07 batch,Slash Example,,Slash Example LLC\n50% off,Percent Example,,Percent Example LLC\n")
    ids = {r["id"] for r in get_json(base, "/api/records?schema=fw9")[1]["records"]}
    assert {"2026/07 batch", "50% off"} <= ids
    status, one = get_json(base, "/api/records/fw9/" + quote("2026/07 batch", safe=""))  # 2026%2F07%20batch
    assert status == 200 and one["record"]["legal_name"] == "Slash Example LLC"
    status, one = get_json(base, "/api/records/fw9/" + quote("50% off", safe=""))  # decoded once: 50%25 -> 50%
    assert status == 200 and one["record"]["legal_name"] == "Percent Example LLC"
    assert get_json(base, "/api/records/fw9/2026/07%20batch")[0] == 404  # an unencoded slash is still a separator
    assert get_json(base, "/api/records/" + quote("fw9", safe="") + "/" + quote("2026/07 batch", safe=""))[0] == 200


@pytest.mark.parametrize(
    "path, status",
    [
        ("/api/records", 400),
        ("/api/records?schema=", 400),
        ("/api/records?schema=nope", 404),
        ("/api/records/fw9/nope", 404),
        ("/api/records/nope/fw9-01", 404),
        ("/api/records/fw9", 404),
    ],
)
def test_record_errors(demo_server, path, status):
    assert get_json(demo_server, path)[0] == status


def test_unexpected_errors_in_a_new_route_are_500_with_a_log_and_no_details(demo_server, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(api_mod, "fill", boom)
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    with caplog.at_level(logging.ERROR, logger="demo.server"):
        status, body = post(demo_server, "/api/fill", {"template": stored(demo_server, "fw9"), "doc": doc})
    assert status == 500 and body["error"] == "internal" and "secret" not in json.dumps(body)
    assert "secret internals" in caplog.text


# ------------------------------------------------------------------ cheap fingerprint checks, fallbacks, schema loops


def test_a_fill_with_a_matching_fingerprint_never_computes_the_signature(demo_server, monkeypatch):
    callers: list[str] = []

    def boom(*a, **k):
        callers.append(threading.current_thread().name)
        raise AssertionError("the signature is not needed to compare fingerprints")

    monkeypatch.setattr(match_mod, "compute_signature", boom)
    monkeypatch.setattr(match_mod, "_pdf_phrases", boom)
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    template = stored(demo_server, "fw9")
    assert post(demo_server, "/api/fill", {"template": template, "doc": doc, "record": {}})[0] == 200
    assert post(demo_server, "/api/smart-pdf", {"template": template, "doc": doc})[0] == 200
    assert post(demo_server, "/api/fill", {"template": template, "doc": open_doc(demo_server, "r.pdf", FW9_EDITED), "record": {}})[0] == 409
    # only each document's build thread asks, computing the match features ahead of its form map
    assert all(name.startswith("form-map-") for name in callers), callers


def test_apply_falls_back_to_template_only_checks_when_the_form_map_cannot_be_built(demo_server, monkeypatch, caplog):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    _, body = post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})
    template = body["template"]

    def offline(pdf, detectors=None):
        raise OSError("cannot fetch the detection weights")

    monkeypatch.setattr(documents_mod, "build_form_map", offline)
    other = open_doc(demo_server, "fw9_again.pdf", FW9)  # a new document: its form map was never built
    field = first_widget(template)
    link = {"op": "link_widget", "id": field["id"], "widget": "no such widget"}
    with caplog.at_level(logging.WARNING, logger="demo.server"):
        status, out = post(demo_server, "/api/apply", {"template": template, "op": link, "author": "human", "doc": other})
        assert status == 200  # validated against the template alone, as without a doc
        status, out = post(demo_server, "/api/apply", {"template": template, "op": {"op": "move", "id": "nope", "dx": 1}, "author": "human", "doc": other})
    assert (status, out["error"]) == (422, "op_rejected")  # real op errors are still reported
    assert "could not build the form map" in caplog.text


LOOP_SCHEMA = {"type": "object", "properties": {"x": {"$ref": "#/definitions/a"}}, "definitions": {"a": {"$ref": "#/definitions/a"}}}


def test_seed_refuses_a_schema_whose_refs_loop(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    status, body = post(demo_server, "/api/seed", {"doc": doc, "schema": LOOP_SCHEMA})
    assert (status, body["error"]) == (400, "bad_schema") and "refers to itself" in body["detail"]
    through_all = {"type": "object", "definitions": {"a": {"allOf": [{"$ref": "#/definitions/b"}]}, "b": {"anyOf": [{"$ref": "#/definitions/a"}]}}}
    assert post(demo_server, "/api/seed", {"doc": doc, "schema": through_all})[0] == 400


def test_a_recursive_tree_schema_is_fine(demo_server):
    tree = {"type": "object", "properties": {"name": {"type": "string"}, "child": {"$ref": "#/definitions/node"}},
            "definitions": {"node": {"type": "object", "properties": {"child": {"$ref": "#/definitions/node"}}}}}  # fmt: skip
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    assert post(demo_server, "/api/seed", {"doc": doc, "schema": tree})[0] == 200


def test_fill_and_apply_answer_a_looping_schema_with_a_plain_422_not_a_500(demo_server):
    doc = open_doc(demo_server, "fw9.pdf", FW9)
    _, body = post(demo_server, "/api/seed", {"doc": doc, "schema": "fw9"})
    looped = body["template"]
    looped["schema"] = LOOP_SCHEMA  # a template saved or pasted with a bad schema
    status, out = post(demo_server, "/api/fill", {"template": looped, "doc": doc, "record": {"x": 1}})
    assert (status, out["error"]) == (422, "bad_schema") and "refers to itself" in out["detail"]
    status, out = post(demo_server, "/api/apply", {"template": looped, "op": {"op": "move", "id": first_widget(looped)["id"], "dx": 1}, "author": "human"})
    assert status == 200  # an edit never validates data, so a looping schema does not stop it
