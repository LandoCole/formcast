"""demo/server with ``demo_enabled`` off: the demo forms, shipped templates and demo records are not part of the app."""

from __future__ import annotations

import json

import pytest
from test_server_documents import get_json, http_request
from test_server_oneshot import open_pdf, save_with_mapping, template_for

from demo.server.settings import Settings


@pytest.fixture
def no_demo(start_demo_server):
    return start_demo_server(demo_enabled=False)


def post(base, path, body):
    return get_json(base, path, "POST", body)


def test_with_the_demo_off_there_are_no_demo_forms_templates_schemas_or_records(no_demo):
    assert get_json(no_demo, "/api/forms")[1] == {"forms": []}
    assert get_json(no_demo, "/api/templates")[1] == {"templates": []}
    assert get_json(no_demo, "/api/schemas")[1] == {"schemas": {}}
    assert get_json(no_demo, "/api/records?schema=fw9")[0] == 404
    assert get_json(no_demo, "/api/templates/fw9")[0] == 404
    assert post(no_demo, "/api/documents", {"form": "fw9"})[0] == 404
    assert post(no_demo, "/api/documents", {"template": "fw9"})[0] == 404


def test_with_the_demo_off_your_own_templates_save_list_open_and_match(no_demo):
    doc = open_pdf(no_demo)
    save_with_mapping(no_demo, doc, template_for(no_demo, doc))
    listing = get_json(no_demo, "/api/templates")[1]["templates"]
    assert [(t["name"], t["has_pdf"]) for t in listing] == [("Tiny", True)]
    assert post(no_demo, "/api/documents", {"template": "Tiny"})[0] == 200
    status, matches = get_json(no_demo, f"/api/documents/{doc}/matches")
    assert status == 200 and [m["name"] for m in matches] == ["Tiny"]
    status, _, _ = http_request(no_demo, "POST", "/api/templates/Tiny/fill", json.dumps({"record": {"name": "Ann"}}).encode(), {"Content-Type": "application/json"})
    assert status == 200


@pytest.mark.parametrize(("text", "on"), [("false", False), ("0", False), ("OFF", False), ("no", False), ("true", True), ("1", True)])
def test_formcast_demo_enabled_reads_true_or_false(text, on):
    assert Settings.from_env({"FORMCAST_DEMO_ENABLED": text}).demo_enabled is on
    assert Settings.from_env({}).demo_enabled is True


def test_a_bad_formcast_demo_enabled_is_refused_without_echoing_it():
    with pytest.raises(ValueError, match="FORMCAST_DEMO_ENABLED must be true or false") as exc:
        Settings.from_env({"FORMCAST_DEMO_ENABLED": "maybe-secret"})
    assert "maybe-secret" not in str(exc.value)


def test_the_no_demo_flag_turns_the_demo_off(tmp_path):
    from demo.server.__main__ import build

    httpd = build(["--port", "0", "--data-dir", str(tmp_path), "--no-demo"], env={})
    try:
        assert httpd.api.settings.demo_enabled is False
    finally:
        httpd.server_close()
