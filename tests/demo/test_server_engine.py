"""The JS server engine (``demo/web/engines/server/client.js``) against the real workbench server.

The server runs in-process over a copy of ``demo/data`` with the scripted fake provider, and Chromium
drives the engine through ``tests/web/harness/server-engine.html``. The demo server serves only
``demo/web``, so the harness is not reachable there: the test answers one made-up address on the server's
own origin with the harness file (``page.route``), which keeps the engine's relative URLs real and the
server unchanged. Form maps are built without detectors (as in the other server tests).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf
import pytest
from test_server_jobs import ADD_NAME, SCHEMA, Factory, Gate, code, finish
from test_server_routes import FW9_EDITED

from demo.server import documents as documents_mod
from formcast.pagemap.build import DetectPolicy

ROOT = Path(__file__).resolve().parents[2]
HARNESS = (ROOT / "tests" / "web" / "harness" / "server-engine.html").read_text(encoding="utf-8")
PATH = "/__harness__/server-engine.html"
FW9_SCHEMA = "fw9"
STOP = finish("stop", [{"path": "name", "reason": "test"}])


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None: real(pdf, detectors={}, policy=policy, ocr="never"))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def blank_pdf(width: float = 200, height: float = 100, rotation: int = 0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((20, 40), "Name")
    page.set_rotation(rotation)
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture
def engine(page):
    """``engine(base)`` -> ``run(cmd)``: open the harness on the workbench at ``base``; ``run`` calls one command."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def open_harness(base: str):
        page.route(f"{base}{PATH}", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HARNESS))
        page.goto(f"{base}{PATH}")
        page.wait_for_function("window.serverReady === true")

        def run(cmd: dict):
            return page.evaluate("cmd => window.runServer(cmd)", cmd)

        return run

    yield open_harness
    assert errors == []


def test_open_fw9_seed_and_apply_a_move(demo_server, engine):
    run = engine(demo_server)
    assert run({"op": "capabilities"}) == {"detectLive": False, "ai": False, "matches": True, "store": True, "records": True, "scanned": True}
    doc = run({"op": "open", "key": "a", "form": "fw9"})
    assert doc["pageCount"] == 6 and len(doc["pages"]) == 6
    assert doc["pages"][0]["width"] == pytest.approx(612, abs=0.1) and doc["pages"][0]["rotation"] == 0
    assert doc["pages"][0]["kind"] == "fillable"

    template = run({"op": "seed", "key": "a", "schema": FW9_SCHEMA})
    assert len(template["fields"]) > 20 and template["history"] == []
    field = template["fields"][0]
    res = run({"op": "apply", "key": "a", "template": template, "edit": {"op": "move", "id": field["id"], "dx": 5, "dy": 3}})
    assert "error" not in res and res["warnings"] == []
    after = next(f for f in res["template"]["fields"] if f["id"] == field["id"])
    assert after["rect"][0] == pytest.approx(field["rect"][0] + 5) and after["rect"][1] == pytest.approx(field["rect"][1] + 3)
    assert res["template"]["history"][-1]["op"]["op"] == "move" and res["template"]["history"][-1]["author"] == "human"

    refused = run({"op": "apply", "key": "a", "template": template, "edit": {"op": "move", "id": "nope", "dx": 1, "dy": 0}})
    assert set(refused) == {"error"} and refused["error"]


def test_page_images_load_and_a_rotated_page_keeps_its_unrotated_size_and_real_rotation(demo_server, engine):
    run = engine(demo_server)
    doc = run({"op": "open", "key": "a", "form": "fw9"})
    image = run({"op": "pageImage", "key": "a", "index": 1, "dpi": 72})
    assert (image["width"], image["height"]) == (612, 792) and image["url"].endswith("/pages/1.png?dpi=72")

    turned = run({"op": "open", "key": "t", "base64": b64(blank_pdf(200, 100, rotation=90)), "name": "turned.pdf"})
    page = turned["pages"][0]
    assert (page["width"], page["height"], page["rotation"]) == (200, 100, 90)  # template space: unrotated
    shown = run({"op": "pageImage", "key": "t", "index": 0, "dpi": 72})
    assert (shown["width"], shown["height"]) == (100, 200)  # what the viewer sees; the PageView turns by `rotation`
    assert doc["pageCount"] == 6


def test_a_build_job_with_the_fake_provider_reaches_done(start_demo_server, engine):
    factory = Factory([code(ADD_NAME), finish("name mapped")])
    run = engine(start_demo_server(provider_factory=factory))
    assert run({"op": "capabilities"})["ai"] is True
    run({"op": "open", "key": "a", "base64": b64(blank_pdf(200, 300)), "name": "one.pdf"})
    started = run({"op": "startBuild", "key": "a", "schema": SCHEMA})
    assert started["kind"] == "build" and len(started["id"]) == 32
    job = run({"op": "jobDone", "id": started["id"]})
    assert job["status"] == "done" and job["error"] is None and job["summary"] == "name mapped"
    assert [f["source"]["paths"] for f in job["template"]["fields"]] == [["name"]]
    assert [s["step"] for s in job["steps"]] == [0, 1, 2]  # step 0: the page pass
    assert all(h["author"] == "agent" for h in job["template"]["history"])


def test_cancel_stops_a_running_job_and_done_reports_cancelled(start_demo_server, engine):
    gate = Gate()
    run = engine(start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, code("print(2)"), finish()])))
    run({"op": "open", "key": "a", "base64": b64(blank_pdf(200, 300)), "name": "one.pdf"})
    started = run({"op": "startBuild", "key": "a", "schema": SCHEMA})
    assert gate.reached.wait(10)
    assert run({"op": "jobPoll", "id": started["id"]})["status"] == "running"
    assert run({"op": "jobCancel", "id": started["id"]})["status"] == "running"  # it stops at its next step
    gate.release()
    job = run({"op": "jobDone", "id": started["id"]})
    assert job["status"] == "cancelled"
    assert len(job["template"]["fields"]) == 1  # the partial template


def test_ai_is_refused_without_a_model_and_a_third_job_is_busy(demo_server, start_demo_server, engine):
    run = engine(demo_server)
    run({"op": "open", "key": "a", "base64": b64(blank_pdf()), "name": "one.pdf"})
    refused = run({"op": "startBuild", "key": "a", "schema": SCHEMA})
    assert refused["error"]["code"] == "ai_not_configured" and refused["error"]["status"] == 400

    gates = [Gate(), Gate(), Gate()]
    busy_run = engine(start_demo_server(provider_factory=Factory([gates[0], STOP], [gates[1], STOP], [STOP])))
    busy_run({"op": "open", "key": "a", "base64": b64(blank_pdf()), "name": "one.pdf"})
    first = busy_run({"op": "startBuild", "key": "a", "schema": SCHEMA})
    second = busy_run({"op": "startBuild", "key": "a", "schema": SCHEMA})
    assert gates[0].reached.wait(10) and gates[1].reached.wait(10)
    assert busy_run({"op": "startBuild", "key": "a", "schema": SCHEMA})["error"]["code"] == "busy"
    gates[0].release()
    gates[1].release()
    assert busy_run({"op": "jobDone", "id": first["id"]})["status"] == "done"
    assert busy_run({"op": "jobDone", "id": second["id"]})["status"] == "done"


def test_a_revised_pdf_is_refused_until_rebound_or_told_not_to_check(demo_server, engine):
    run = engine(demo_server)
    run({"op": "open", "key": "rev", "base64": b64(FW9_EDITED), "name": "fw9_rev.pdf"})
    template = run({"op": "templates", "action": "get", "name": "fw9"})
    record = {"schema": "fw9", "id": "fw9-01"}  # a reference to a stored record

    refused = run({"op": "fill", "key": "rev", "template": template, "record": record})
    assert refused["error"]["code"] == "fingerprint_mismatch" and refused["error"]["status"] == 409
    assert "Rebind" in refused["error"]["message"]
    assert run({"op": "smartPdf", "key": "rev", "template": template})["error"]["code"] == "fingerprint_mismatch"

    forced = run({"op": "fill", "key": "rev", "template": template, "record": record, "checkFingerprint": False})
    assert forced["head"] == "%PDF-" and forced["report"]["counts"]["fingerprint_mismatch"] == 1
    assert "not the exact form" in forced["note"]

    rebound = run({"op": "rebind", "key": "rev", "template": template})
    assert rebound["form"]["fingerprint"] not in (None, template["form"]["fingerprint"])
    ok = run({"op": "fill", "key": "rev", "template": rebound, "record": record})
    assert ok["head"] == "%PDF-" and ok["report"]["fingerprint_ok"] is True and ok["note"] is None
    smart = run({"op": "smartPdf", "key": "rev", "template": rebound})
    assert base64.b64decode(smart["base64"]).startswith(b"%PDF-")


def test_matches_templates_and_records(demo_server, engine):
    run = engine(demo_server)
    run({"op": "open", "key": "a", "form": "fw9"})
    matches = run({"op": "matches", "key": "a"})
    assert isinstance(matches, list) and matches[0]["name"] == "fw9" and matches[0]["action"] == "reuse"

    names = [t["name"] for t in run({"op": "templates", "action": "list"})]
    assert "fw9" in names
    template = run({"op": "templates", "action": "get", "name": "fw9"})
    saved = run({"op": "templates", "action": "put", "name": "copy-of-fw9", "template": template})
    assert saved["name"] == "copy-of-fw9" and saved["template_id"] == template["template_id"]
    assert run({"op": "templates", "action": "delete", "name": "copy-of-fw9"}) == "copy-of-fw9"
    assert run({"op": "templates", "action": "get", "name": "copy-of-fw9"})["error"]["code"] == "not_found"
    assert run({"op": "templates", "action": "get", "name": "../x"})["error"]["status"] == 400

    assert "fw9" in run({"op": "records", "action": "schemas", "args": []})
    records = run({"op": "records", "action": "list", "args": ["fw9"]})
    assert records and records[0]["id"] == "fw9-01"
    assert run({"op": "records", "action": "get", "args": ["fw9", "fw9-01"]})["legal_name"] == "Juniper Lane Ceramics LLC"
    assert run({"op": "records", "action": "get", "args": ["fw9", "nope"]})["error"]["code"] == "not_found"
