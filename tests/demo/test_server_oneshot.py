"""demo/server one-shot flow: data upload, the ``oneshot`` job, saving the PDF and data mapping, and the fill route.

The model is a scripted fake that answers each kind of call (design the schema, the page pass, map a table,
read text) by the call's system prompt, so a whole one-shot run needs no network. Form maps are built
without detectors (the same autouse fixture as the route tests).
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile

import pymupdf
import pytest
from test_server_documents import get_json, http_request, multipart, small_pdf

from demo.server import documents as documents_mod
from formcast.data.ai import DERIVE_SYSTEM, EXTRACT_SYSTEM, MAP_SYSTEM
from formcast.llm import LLMResponse, Usage
from formcast.pagemap.build import DetectPolicy


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None, **kw: real(pdf, detectors={}, policy=policy, ocr="never"))


def name_form() -> bytes:
    """One page with "Name:" and a text widget for it (form-map id w0_0)."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 40), "Name:")
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.rect = "name", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(70, 28, 250, 44)
    page.add_widget(w)
    out = doc.tobytes()
    doc.close()
    return out


PDF = name_form()
DERIVED = {"title": "Tiny form", "type": "object", "properties": {"name": {"type": "string", "description": "Name"}}}
CSV = b"Full Name,Ref\nAnn Lee,1\nBo Chen,2\n"


class Model:
    """Answers by the call's system prompt; counts calls of each kind."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self):
        return self

    def complete(self, messages, tools, **kwargs):
        system = messages[0].content
        if system == DERIVE_SYSTEM:
            kind, text = "derive", json.dumps({"schema": DERIVED})
        elif system.startswith(MAP_SYSTEM[:40]):
            kind, text = "map", json.dumps({"rules": [{"path": "name", "column": "Full Name"}], "unmapped_columns": ["Ref"]})
        elif system == EXTRACT_SYSTEM:
            kind, text = "extract", json.dumps({"records": [{"name": "Priya Raman"}]})
        else:  # the page pass
            kind, text = "page", json.dumps({"mappings": [{"id": "w0_0", "source": {"paths": ["name"]}}], "tables": []})
        with self._lock:
            self.calls[kind] = self.calls.get(kind, 0) + 1
        return LLMResponse(text=text, tool_calls=[], usage=Usage(100, 10), raw={})

    def close(self) -> None:
        pass


def post(base, path, body):
    return get_json(base, path, "POST", body)


def open_pdf(base) -> str:
    body, headers = multipart("form.pdf", PDF)
    status, _, out = http_request(base, "POST", "/api/documents", body, headers)
    assert status == 200
    return json.loads(out)["id"]


def send_csv(base, data=CSV, name="people.csv") -> dict:
    body, headers = multipart(name, data)
    status, _, out = http_request(base, "POST", "/api/data", body, headers)
    assert status == 200, out
    return json.loads(out)


def wait_ended(base, job_id, timeout=30.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status, job = get_json(base, f"/api/jobs/{job_id}")
        assert status == 200
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def template_for(base, doc) -> dict:
    """A template whose only box (the widget) is linked to ``name``."""
    status, out = post(base, "/api/seed", {"doc": doc, "schema": DERIVED})
    assert status == 200
    template = out["template"]
    status, out = post(base, "/api/apply", {"template": template, "author": "human", "op": {"op": "set_source", "id": "w0_0", "source": {"paths": ["name"]}}})
    assert status == 200, out
    return out["template"]


# ------------------------------------------------------------------ data


def test_a_csv_upload_returns_its_columns_types_samples_and_own_schema(demo_server):
    ds = send_csv(demo_server)
    assert (ds["kind"], ds["format"], ds["rows"]) == ("table", "csv", 2)
    assert [c["name"] for c in ds["columns"]] == ["Full Name", "Ref"]
    assert ds["columns"][1]["type"] == "integer"
    assert ds["schema"]["properties"]["Full Name"]["type"] == "string"
    assert ds["preview"][0] == {"Full Name": "Ann Lee", "Ref": "1"}
    status, again = get_json(demo_server, f"/api/data/{ds['id']}")
    assert status == 200 and again["rows"] == 2


def test_pasted_text_is_a_table_when_it_is_one_and_free_text_otherwise(demo_server):
    status, table = post(demo_server, "/api/data", {"text": "a,b,c\n1,2,3"})
    assert status == 200 and table["kind"] == "table"
    status, prose = post(demo_server, "/api/data", {"text": "My name is Ann and I want the job."})
    assert status == 200 and prose["kind"] == "text" and prose["chars"] > 0


def test_data_that_cannot_be_read_is_a_400(demo_server):
    body, headers = multipart("x.json", b"[1, 2]")
    status, _, out = http_request(demo_server, "POST", "/api/data", body, headers)
    assert status == 400 and json.loads(out)["error"] == "bad_data"
    assert post(demo_server, "/api/data", {"text": "  "})[0] == 400


# ------------------------------------------------------------------ a mapping a person edited


def test_an_edited_mapping_remakes_the_records_with_no_model_call(demo_server):
    ds = send_csv(demo_server)
    mapping = {"rules": [{"path": "name", "column": "Ref", "manual": True, "confirmed": True}], "unmapped_columns": []}
    status, out = post(demo_server, f"/api/data/{ds['id']}/map", {"schema": DERIVED, "mapping": mapping, "mode": "model"})
    assert status == 200, out
    assert out["records"] == [{"name": "1"}, {"name": "2"}] and out["total"] == 2 and out["mode"] == "model"
    assert out["mapping"]["rules"] == [{"path": "name", "column": "Ref", "transforms": [], "confirmed": True, "manual": True}]
    assert out["mapping"]["unmapped_columns"] == ["Full Name"] and out["mapping"]["columns"] == ["Full Name", "Ref"]
    assert out["dataset"]["id"] == ds["id"] and out["warnings"] == []


def test_remapping_refuses_free_text_an_unknown_dataset_and_a_bad_mapping(demo_server):
    good = {"schema": DERIVED, "mapping": {"rules": []}}
    _, prose = post(demo_server, "/api/data", {"text": "My name is Ann and I want the job."})
    status, out = post(demo_server, f"/api/data/{prose['id']}/map", good)
    assert (status, out["error"]) == (400, "not_a_table")
    assert post(demo_server, f"/api/data/{'0' * 32}/map", good)[0] == 404
    ds = send_csv(demo_server)
    status, out = post(demo_server, f"/api/data/{ds['id']}/map", {"schema": DERIVED, "mapping": {"rules": [{"path": "name"}]}})
    assert (status, out["error"]) == (400, "bad_mapping")
    assert post(demo_server, f"/api/data/{ds['id']}/map", {"schema": DERIVED})[1]["error"] == "bad_mapping"


def test_a_rule_for_a_path_the_schema_lacks_is_dropped_with_a_warning(demo_server):
    ds = send_csv(demo_server)
    rules = [{"path": "name", "column": "Full Name"}, {"path": "nope", "column": "Ref"}]
    status, out = post(demo_server, f"/api/data/{ds['id']}/map", {"schema": DERIVED, "mapping": {"rules": rules}})
    assert status == 200 and [r["path"] for r in out["mapping"]["rules"]] == ["name"]
    assert out["warnings"] == ["nope: not a field of the form's schema"] and out["mode"] == "model"


# ------------------------------------------------------------------ the oneshot job


def test_a_oneshot_job_designs_the_schema_maps_the_boxes_and_maps_the_data(start_demo_server):
    model = Model()
    base = start_demo_server(provider_factory=model)
    doc = open_pdf(base)
    ds = send_csv(base)
    status, out = post(base, "/api/jobs", {"kind": "oneshot", "doc": doc, "data": ds["id"], "check": False})
    assert status == 200
    job = wait_ended(base, out["id"])
    assert job["status"] == "done", job["error"]
    assert [(s["key"], s["status"]) for s in job["stages"]] == [
        ("form", "done"), ("schema", "done"), ("boxes", "done"), ("data", "done"), ("match", "done"),
    ]  # fmt: skip
    assert job["result"]["schema"] == DERIVED
    assert job["template"]["schema"] == DERIVED
    linked = [f for f in job["template"]["fields"] if (f.get("source") or {}).get("paths")]
    assert [f["id"] for f in linked] == ["w0_0"]
    data = job["result"]["data"]
    assert data["mode"] == "model"
    assert data["records"] == [{"name": "Ann Lee"}, {"name": "Bo Chen"}]
    assert data["titles"][0] == "1. Ann Lee"
    assert data["mapping"]["unmapped_columns"] == ["Ref"]
    assert model.calls == {"derive": 1, "page": 1, "map": 1}
    assert job["usage"] == {"input_tokens": 300, "output_tokens": 30}


@pytest.mark.parametrize("stop", ["budget", "stalled"])
def test_a_check_that_stops_early_keeps_its_links_and_the_data_is_still_mapped(start_demo_server, monkeypatch, stop):
    from demo.server import oneshot as oneshot_mod

    real = oneshot_mod.build_template

    def stopped_early(*args, **kwargs):
        result = real(*args, **{**kwargs, "agent": False})  # the page pass's links; the check then stops
        result.stop_reason, result.finished, result.summary = stop, False, ""
        return result

    monkeypatch.setattr(oneshot_mod, "build_template", stopped_early)
    base = start_demo_server(provider_factory=Model())
    ds = send_csv(base)
    job = wait_ended(base, post(base, "/api/jobs", {"kind": "oneshot", "doc": open_pdf(base), "data": ds["id"], "check": True})[1]["id"])
    assert job["status"] == "done" and job["error"] is None, job
    assert job["summary"] == oneshot_mod.EARLY_STOP[stop]
    boxes = next(st for st in job["stages"] if st["key"] == "boxes")
    assert boxes["status"] == "done" and boxes["detail"] == f"1 of 1 boxes linked · {oneshot_mod.EARLY_STOP[stop]}"
    assert [f["id"] for f in job["template"]["fields"] if (f.get("source") or {}).get("paths")] == ["w0_0"]
    assert job["result"]["data"]["records"] == [{"name": "Ann Lee"}, {"name": "Bo Chen"}]


def test_free_text_is_read_by_the_model(start_demo_server):
    model = Model()
    base = start_demo_server(provider_factory=model)
    doc = open_pdf(base)
    _, ds = post(base, "/api/data", {"text": "Hello, I am Priya Raman and I would like to apply."})
    job = wait_ended(base, post(base, "/api/jobs", {"kind": "oneshot", "doc": doc, "data": ds["id"], "schema": DERIVED, "check": False})[1]["id"])
    assert job["status"] == "done"
    assert job["stages"][1]["status"] == "skipped"  # the schema was given
    assert job["result"]["data"]["mode"] == "text"
    assert job["result"]["data"]["records"] == [{"name": "Priya Raman"}]
    assert "derive" not in model.calls


def test_with_a_template_and_columns_named_by_schema_path_no_model_is_needed(demo_server):
    doc = open_pdf(demo_server)
    template = template_for(demo_server, doc)
    ds = send_csv(demo_server, b"name\nAnn Lee\n")
    status, out = post(demo_server, "/api/jobs", {"kind": "oneshot", "doc": doc, "template": template, "data": ds["id"]})
    assert status == 200, out  # AI is off on this server
    job = wait_ended(demo_server, out["id"])
    assert job["status"] == "done"
    assert [s["status"] for s in job["stages"]] == ["done", "skipped", "skipped", "done", "done"]
    assert job["result"]["data"]["mode"] == "direct"
    assert job["result"]["data"]["records"] == [{"name": "Ann Lee"}]
    assert job["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_without_ai_a_form_cannot_be_mapped_or_text_read(demo_server):
    doc = open_pdf(demo_server)
    status, out = post(demo_server, "/api/jobs", {"kind": "oneshot", "doc": doc})
    assert status == 400 and out["error"] == "ai_not_configured"
    _, ds = post(demo_server, "/api/data", {"text": "My name is Ann."})
    template = template_for(demo_server, doc)
    status, out = post(demo_server, "/api/jobs", {"kind": "oneshot", "doc": doc, "template": template, "data": ds["id"]})
    assert status == 400 and out["error"] == "ai_not_configured"


def test_a_data_error_keeps_the_template(demo_server):
    doc = open_pdf(demo_server)
    template = template_for(demo_server, doc)
    ds = send_csv(demo_server)  # no column is a schema path, and AI is off
    job = wait_ended(demo_server, post(demo_server, "/api/jobs", {"kind": "oneshot", "doc": doc, "template": template, "data": ds["id"]})[1]["id"])
    assert job["status"] == "done"
    assert job["template"] is not None
    assert job["stages"][-1]["status"] == "error"
    assert "AI is off" in job["result"]["data"]["error"]


# ------------------------------------------------------------------ saving, reopening and filling from code


def save_with_mapping(base, doc, template) -> None:
    mapping = {"columns": ["Full Name", "Ref"], "rules": [{"path": "name", "column": "Full Name"}], "unmapped_columns": ["Ref"]}
    status, _, out = http_request(base, "PUT", "/api/templates/Tiny", json.dumps({"template": template, "doc": doc, "mapping": mapping}).encode(), {"Content-Type": "application/json"})
    assert status == 200, out
    assert json.loads(out) | {"template_id": None} == {"name": "Tiny", "template_id": None, "pdf": True, "mapping": True}


def test_a_saved_template_keeps_its_pdf_schema_and_mapping(demo_server):
    doc = open_pdf(demo_server)
    template = template_for(demo_server, doc)
    save_with_mapping(demo_server, doc, template)
    _, listing = get_json(demo_server, "/api/templates")
    tiny = next(t for t in listing["templates"] if t["name"] == "Tiny")
    assert (tiny["title"], tiny["has_pdf"], tiny["fields"]) == ("Tiny form", True, 1)
    status, reopened = post(demo_server, "/api/documents", {"template": "Tiny"})
    assert status == 200 and reopened["page_count"] == 1
    status, headers, body = http_request(demo_server, "GET", "/api/templates/Tiny/schema")
    assert status == 200 and headers["content-type"].startswith("application/schema+json")
    assert json.loads(body) == DERIVED
    _, mappings = get_json(demo_server, "/api/templates/Tiny/mappings")
    assert mappings["mappings"][0]["rules"] == [{"path": "name", "column": "Full Name", "transforms": []}]


def test_a_saved_mapping_maps_the_same_columns_without_ai(demo_server):
    doc = open_pdf(demo_server)
    template = template_for(demo_server, doc)
    save_with_mapping(demo_server, doc, template)
    ds = send_csv(demo_server)
    job = wait_ended(demo_server, post(demo_server, "/api/jobs", {"kind": "oneshot", "doc": doc, "template": template, "data": ds["id"], "saved": "Tiny"})[1]["id"])
    assert job["result"]["data"]["mode"] == "saved"
    assert [r["name"] for r in job["result"]["data"]["records"]] == ["Ann Lee", "Bo Chen"]


def test_a_mapping_saved_for_these_very_columns_wins_over_columns_named_by_schema_path():
    from demo.server.datasets import Dataset
    from demo.server.oneshot import map_dataset
    from formcast.data import DataMapping, Table

    table = Table(["name"], [{"name": "Ann Lee"}])
    ds = Dataset("0" * 32, "t.csv", "table", table)
    assert map_dataset(DERIVED, ds, None).mode == "direct"
    reviewed = DataMapping(columns=["name"], rules=[{"path": "name", "value": "Fixed", "manual": True}])
    out = map_dataset(DERIVED, ds, None, [reviewed])
    assert (out.mode, out.records, out.mapping.rules[0].manual) == ("saved", [{"name": "Fixed"}], True)


def test_deleting_a_saved_template_deletes_its_files(demo_server):
    doc = open_pdf(demo_server)
    save_with_mapping(demo_server, doc, template_for(demo_server, doc))
    assert http_request(demo_server, "DELETE", "/api/templates/Tiny", b"{}", {"Content-Type": "application/json"})[0] == 200
    status, out = post(demo_server, "/api/documents", {"template": "Tiny"})
    assert status == 404


def test_the_fill_route_fills_a_record_records_or_a_table_file(demo_server):
    doc = open_pdf(demo_server)
    save_with_mapping(demo_server, doc, template_for(demo_server, doc))
    status, headers, body = http_request(demo_server, "POST", "/api/templates/Tiny/fill", json.dumps({"record": {"name": "Ann Lee"}}).encode(), {"Content-Type": "application/json"})
    assert status == 200 and headers["content-type"] == "application/pdf" and headers["x-formcast-records"] == "1"
    with pymupdf.open(stream=body, filetype="pdf") as filled:
        assert [w.field_value for w in filled[0].widgets()] == ["Ann Lee"]
    status, headers, body = http_request(demo_server, "POST", "/api/templates/Tiny/fill", json.dumps({"records": [{"name": "A"}, {"name": "B"}]}).encode(), {"Content-Type": "application/json"})
    assert status == 200 and headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(body)).namelist()) == ["Tiny-1.pdf", "Tiny-2.pdf", "reports.json"]
    multipart_body, multipart_headers = multipart("people.csv", CSV)  # the saved mapping's columns: no AI needed
    status, headers, body = http_request(demo_server, "POST", "/api/templates/Tiny/fill", multipart_body, multipart_headers)
    assert status == 200 and headers["x-formcast-records"] == "2"


def test_the_fill_route_reads_text_with_the_model_and_refuses_it_without(start_demo_server):
    base = start_demo_server(provider_factory=Model())
    doc = open_pdf(base)
    save_with_mapping(base, doc, template_for(base, doc))
    status, headers, _ = http_request(base, "POST", "/api/templates/Tiny/fill", json.dumps({"text": "I am Priya Raman."}).encode(), {"Content-Type": "application/json"})
    assert status == 200 and headers["content-type"] == "application/pdf"
    off = start_demo_server()  # same data folder, no AI
    status, _, out = http_request(off, "POST", "/api/templates/Tiny/fill", json.dumps({"text": "I am Priya Raman."}).encode(), {"Content-Type": "application/json"})
    assert status == 422 and json.loads(out)["error"] == "cannot_map"


def test_the_fill_route_says_what_it_takes(demo_server):
    status, _, out = http_request(demo_server, "POST", "/api/templates/fw9/fill", b"{}", {"Content-Type": "application/json"})
    assert status == 400 and "record" in json.loads(out)["detail"]
    status, _, _ = http_request(demo_server, "POST", "/api/templates/nope/fill", b"{}", {"Content-Type": "application/json"})
    assert status == 404


# ------------------------------------------------------------------ one page labelled and mapped


def test_a_pages_fill_in_maps_its_unmapped_boxes_and_leaves_mapped_ones(start_demo_server):
    model = Model()
    base = start_demo_server(provider_factory=model)
    doc = open_pdf(base)
    status, out = post(base, "/api/seed", {"doc": doc, "schema": DERIVED})
    assert status == 200
    status, out = post(base, f"/api/documents/{doc}/pages/0/fill-in", {"template": out["template"]})
    assert status == 200, out
    assert (out["page"], out["status"], out["applied"], out["error"]) == (0, "ok", 1, None)
    assert [(f["id"], f["source"]["paths"]) for f in out["template"]["fields"]] == [("w0_0", ["name"])]
    assert model.calls == {"page": 1}
    done = template_for(base, doc)
    done["fields"] = [f | {"label": "Name"} for f in done["fields"]]
    status, again = post(base, f"/api/documents/{doc}/pages/0/fill-in", {"template": done})
    assert status == 200 and (again["status"], again["applied"]) == ("skipped", 0)  # nothing left to fill in
    assert model.calls == {"page": 1}


def test_a_pages_fill_in_needs_ai_this_pdfs_template_and_a_page_it_has(demo_server, start_demo_server):
    doc = open_pdf(demo_server)
    template = template_for(demo_server, doc)
    status, out = post(demo_server, f"/api/documents/{doc}/pages/0/fill-in", {"template": template})
    assert (status, out["error"]) == (400, "ai_not_configured")
    base = start_demo_server(provider_factory=Model())
    doc = open_pdf(base)
    status, out = post(base, f"/api/documents/{doc}/pages/3/fill-in", {"template": template_for(base, doc)})
    assert status == 404
    body, headers = multipart("other.pdf", small_pdf(text="Another form"))
    other = json.loads(http_request(base, "POST", "/api/documents", body, headers)[2])["id"]
    status, out = post(base, f"/api/documents/{other}/pages/0/fill-in", {"template": template_for(base, doc)})
    assert (status, out["error"]) == (400, "wrong_document")
