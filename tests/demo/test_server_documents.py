"""demo/server skeleton: documents, page images, static files, error shape and the threaded server.

Servers run in-process on an ephemeral port (``demo_server`` / ``start_demo_server`` in conftest) over a
copy of ``demo/data``. Settings are always built from a mapping, so the real ``.env`` is never read.
"""

from __future__ import annotations

import http.client
import json
import logging
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
import pytest

from demo.server import documents as documents_mod
from demo.server.api import Api, ApiError
from demo.server.documents import Documents
from demo.server.settings import Settings
from formcast import FormcastError
from formcast.llm import LLMConfig

ROOT = Path(__file__).resolve().parents[2]
FW9 = (ROOT / "demo" / "data" / "forms" / "fw9.pdf").read_bytes()
SENTINEL_KEY = "sk-sentinel-DO-NOT-LEAK-0123456789"


# ------------------------------------------------------------------ helpers


def http_request(base: str, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    """(status, headers, body) with the path sent exactly as given (no client-side normalising)."""
    parts = urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def get_json(base: str, path: str, method: str = "GET", body=None):
    payload = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    status, headers, data = http_request(base, method, path, payload, headers)
    return status, json.loads(data)


def multipart(filename: str, data: bytes, field: str = "file") -> tuple[bytes, dict]:
    boundary = "----formcast" + uuid.uuid4().hex
    quoted = filename.replace("\\", "\\\\").replace('"', '\\"')  # a quoted-string escapes \ and "
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{quoted}"\r\nContent-Type: application/pdf\r\n\r\n'
    body = head.encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def upload(base: str, filename: str, data: bytes, field: str = "file"):
    body, headers = multipart(filename, data, field)
    status, _, out = http_request(base, "POST", "/api/documents", body, headers)
    return status, json.loads(out)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


class FakeProvider:
    def complete(self, messages, tools):  # pragma: no cover - never called here
        raise AssertionError("no model call expected")

    def close(self):
        pass


def small_pdf(pages: int = 1, text: str = "hello") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=200, height=300).insert_text((20, 40), f"{text} {i}")
    out = doc.tobytes()
    doc.close()
    return out


# ------------------------------------------------------------------ capabilities and forms


def test_capabilities_say_ai_is_off_without_a_model_and_on_with_a_provider_factory(demo_server, start_demo_server):
    status, caps = get_json(demo_server, "/api/capabilities")
    assert status == 200
    assert caps == {"ai": False, "matches": True, "store": True, "records": True, "scanned": True, "detectLive": False}
    with_ai = start_demo_server(provider_factory=FakeProvider)
    assert get_json(with_ai, "/api/capabilities")[1]["ai"] is True


def test_a_configured_model_turns_ai_on_without_building_a_provider_or_leaking_the_key(start_demo_server):
    llm = LLMConfig(base_url="https://llm.example.com/v1", api_key=SENTINEL_KEY, model="m-1")
    base = start_demo_server(llm=llm)
    assert get_json(base, "/api/capabilities")[1]["ai"] is True
    seen = []
    for method, path in [("GET", "/api/capabilities"), ("GET", "/api/forms"), ("GET", "/api/nope"), ("DELETE", "/api/forms")]:
        status, headers, body = http_request(base, method, path)
        seen.append(body + json.dumps(headers).encode())
    status, _, body = http_request(base, "POST", "/api/documents", b"{bad", {"Content-Type": "application/json"})
    assert status == 400
    seen.append(body)
    assert all(SENTINEL_KEY.encode() not in blob for blob in seen)


def test_forms_lists_the_demo_forms_with_their_schemas(demo_server):
    status, body = get_json(demo_server, "/api/forms")
    assert status == 200
    assert [f["name"] for f in body["forms"]] == ["fw9", "menomonie_app", "wadoc_auth"]
    assert all(f["schema"] == f["name"] and f["size"] > 0 for f in body["forms"])


# ------------------------------------------------------------------ documents


def test_upload_a_pdf_returns_its_summary(demo_server):
    status, doc = upload(demo_server, "fw9.pdf", FW9)
    assert status == 200
    assert len(doc["id"]) == 32
    assert doc["name"] == "fw9.pdf"
    assert doc["page_count"] == 6
    assert doc["page_sizes"][0] == pytest.approx([612.0, 792.0], abs=0.1) and len(doc["page_sizes"]) == 6
    assert len(doc["kinds"]) == 6 and set(doc["kinds"]) <= {"fillable", "digital", "scanned"}
    assert doc["rotations"] == [0] * 6


def test_kinds_tell_fillable_digital_and_scanned_pages_apart(demo_server):
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "text only")
    fillable = pdf.new_page()
    widget = pymupdf.Widget()
    widget.field_type, widget.field_name, widget.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "a", pymupdf.Rect(72, 72, 200, 100)
    fillable.add_widget(widget)
    scan = pdf.new_page()
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 612, 792), False)
    pix.set_rect(pix.irect, (200,))
    scan.insert_image(scan.rect, pixmap=pix)
    data = pdf.tobytes()
    status, doc = upload(demo_server, "mixed.pdf", data)
    assert (status, doc["kinds"]) == (200, ["digital", "fillable", "scanned"])


@pytest.mark.parametrize(
    "data",
    [b"just some text, not a PDF\n", b"", b"%PDF-1.7\n%%EOF\n"],
    ids=["text", "empty", "no-pages"],
)
def test_a_file_that_is_not_a_usable_pdf_is_a_400_with_a_plain_message(demo_server, data):
    status, body = upload(demo_server, "notes.txt", data)
    assert status == 400
    assert body["error"] == "bad_pdf"
    assert body["detail"].startswith("This file can't be used")


def test_an_encrypted_pdf_is_a_400_bad_pdf(demo_server):
    src = pymupdf.open()
    src.new_page()
    data = src.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    status, body = upload(demo_server, "locked.pdf", data)
    assert (status, body["error"]) == (400, "bad_pdf")
    assert "encrypted" in body["detail"]


def test_an_upload_over_the_limit_is_a_413_and_the_server_carries_on(start_demo_server):
    base = start_demo_server(max_upload_bytes=1024)
    status, body = upload(base, "big.pdf", b"x" * 5000)
    assert status == 413
    assert body["error"] == "too_large"
    assert upload(base, "small.txt", b"hi")[0] == 400  # under the limit: judged on content, not size
    assert http_request(base, "GET", "/api/capabilities")[0] == 200


def test_json_bodies_have_their_own_limit(start_demo_server):
    base = start_demo_server(max_json_bytes=64)
    big = json.dumps({"form": "fw9", "pad": "x" * 200}).encode()
    status, _, body = http_request(base, "POST", "/api/documents", big, {"Content-Type": "application/json"})
    assert (status, json.loads(body)["error"]) == (413, "too_large")
    assert get_json(base, "/api/documents", "POST", {"form": "fw9"})[0] == 200


def test_the_upload_must_be_a_multipart_field_named_file(demo_server):
    status, body = upload(demo_server, "fw9.pdf", FW9, field="document")
    assert (status, body["error"]) == (400, "bad_upload")
    status, _, out = http_request(
        demo_server, "POST", "/api/documents", b"--x\r\n", {"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert (status, json.loads(out)["error"]) == (400, "bad_upload")


def test_an_upload_name_is_a_label_not_a_path(demo_server, tmp_path):
    for sent, shown in [("../../evil.pdf", "evil.pdf"), ("C:\\temp\\form.pdf", "form.pdf"), ("", "document.pdf"), ("a\x07b.pdf", "ab.pdf")]:
        status, doc = upload(demo_server, sent, small_pdf())
        assert (status, doc["name"]) == (200, shown)
    assert not (ROOT / "evil.pdf").exists() and not (ROOT.parent / "evil.pdf").exists()


def test_pick_a_demo_form_by_name(demo_server):
    status, doc = get_json(demo_server, "/api/documents", "POST", {"form": "wadoc_auth"})
    assert status == 200 and doc["name"] == "wadoc_auth.pdf" and doc["page_count"] >= 1


@pytest.mark.parametrize(
    "form",
    ["../../etc/passwd", "/etc/passwd", "..", "a/b", "fw9.pdf/../../x", "", "fw9\x00", 7, None, ["fw9"]],
)
def test_a_demo_form_name_that_is_not_plain_is_a_400(demo_server, form):
    status, body = get_json(demo_server, "/api/documents", "POST", {"form": form})
    assert (status, body["error"]) == (400, "bad_form")


def test_an_unknown_but_plain_demo_form_is_a_404(demo_server):
    status, body = get_json(demo_server, "/api/documents", "POST", {"form": "no_such_form"})
    assert (status, body["error"]) == (404, "not_found")


def test_a_body_that_is_not_json_or_not_an_object_is_a_400(demo_server):
    status, _, out = http_request(demo_server, "POST", "/api/documents", b"{nope", {"Content-Type": "application/json"})
    assert (status, json.loads(out)["error"]) == (400, "bad_json")
    assert get_json(demo_server, "/api/documents", "POST", ["fw9"])[1]["error"] == "bad_form"


# ------------------------------------------------------------------ page images


def test_page_image_at_72_dpi_is_the_page_size_in_pixels(demo_server):
    _, doc = upload(demo_server, "fw9.pdf", FW9)
    status, headers, png = http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/0.png?dpi=72")
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert png_size(png) == (612, 792)
    assert int(headers["content-length"]) == len(png)


def test_page_image_defaults_to_110_dpi_and_clamps_dpi(demo_server):
    _, doc = upload(demo_server, "fw9.pdf", FW9)
    base = f"/api/documents/{doc['id']}/pages/1.png"
    assert png_size(http_request(demo_server, "GET", base)[2]) == (935, 1210)  # 612 x 792 pt at 110 dpi
    assert png_size(http_request(demo_server, "GET", base + "?dpi=1")[2])[0] == 306  # 36 dpi
    assert png_size(http_request(demo_server, "GET", base + "?dpi=5000")[2])[0] == 1700  # 200 dpi
    status, _, out = http_request(demo_server, "GET", base + "?dpi=high")
    assert (status, json.loads(out)["error"]) == (400, "bad_dpi")


def test_page_image_follows_the_pages_rotation(demo_server):
    pdf = pymupdf.open()
    page = pdf.new_page(width=200, height=100)
    page.set_rotation(90)
    _, doc = upload(demo_server, "turned.pdf", pdf.tobytes())
    assert doc["page_sizes"][0] == [200.0, 100.0]  # unrotated crop box, as in the library
    png = http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/0.png?dpi=72")[2]
    assert png_size(png) == (100, 200)  # what the viewer sees
    assert doc["rotations"] == [90]  # so a client can place unrotated-space boxes over the turned image


def test_page_image_errors(demo_server):
    _, doc = upload(demo_server, "one.pdf", small_pdf())
    assert http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/1.png")[0] == 404
    assert http_request(demo_server, "GET", f"/api/documents/{'0' * 32}/pages/0.png")[0] == 404
    assert http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/-1.png")[0] == 404
    assert http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/0.jpg")[0] == 404
    status, _, out = http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/9.png")
    assert json.loads(out)["error"] == "not_found"


def test_adding_and_rendering_never_builds_the_form_map(demo_server, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("detection must not run for documents or page images")

    monkeypatch.setattr(documents_mod, "build_form_map", boom)
    _, doc = upload(demo_server, "fw9.pdf", FW9)
    assert http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/2.png")[0] == 200


def test_concurrent_page_requests_all_succeed(demo_server):
    _, a = upload(demo_server, "fw9.pdf", FW9)
    _, b = upload(demo_server, "other.pdf", small_pdf(pages=3))
    jobs = [(a["id"], 0, 110), (a["id"], 0, 110), (a["id"], 3, 72), (b["id"], 2, 150), (b["id"], 1, 80), (a["id"], 5, 60)]
    barrier = threading.Barrier(len(jobs))
    results: list = [None] * len(jobs)

    def run(i, doc_id, n, dpi):
        barrier.wait(timeout=10)
        status, headers, png = http_request(demo_server, "GET", f"/api/documents/{doc_id}/pages/{n}.png?dpi={dpi}")
        results[i] = (status, headers.get("content-type"), png_size(png) if status == 200 else None)

    threads = [threading.Thread(target=run, args=(i, *job)) for i, job in enumerate(jobs)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert all(r and r[:2] == (200, "image/png") for r in results), results
    assert results[0] == results[1] == (200, "image/png", (935, 1210))
    assert results[3][2] == (round(200 * 150 / 72), round(300 * 150 / 72))


# ------------------------------------------------------------------ Documents (unit)


def test_documents_keep_twenty_and_evict_the_least_recently_used():
    docs = Documents()
    first = docs.add(small_pdf(), "first.pdf")
    others = [docs.add(small_pdf(text=str(i)), f"d{i}.pdf") for i in range(19)]
    assert len(docs) == 20
    assert docs.get(first.id) is first  # now the most recently used
    docs.add(small_pdf(text="new"), "new.pdf")
    assert len(docs) == 20
    assert docs.get(first.id) is first
    with pytest.raises(KeyError):
        docs.get(others[0].id)  # the least recently used went
    assert docs.get(others[1].id) is others[1]


def test_documents_reject_bad_pdfs_without_keeping_them():
    docs = Documents()
    with pytest.raises(FormcastError):
        docs.add(b"not a pdf", "x.pdf")
    assert len(docs) == 0


def test_the_form_map_is_built_once_even_when_asked_for_concurrently(monkeypatch):
    calls = []
    gate = threading.Event()

    def slow_build(pdf):
        calls.append(1)
        gate.wait(timeout=5)
        return object()

    monkeypatch.setattr(documents_mod, "build_form_map", slow_build)
    doc = Documents().add(small_pdf(), "x.pdf")
    got: list = []
    threads = [threading.Thread(target=lambda: got.append(doc.form_map())) for _ in range(4)]
    [t.start() for t in threads]
    gate.set()
    [t.join(timeout=10) for t in threads]
    assert len(calls) == 1 and len(got) == 4 and all(g is got[0] for g in got)


def test_a_failed_form_map_build_is_not_kept(monkeypatch):
    attempts = []

    def flaky(pdf):
        attempts.append(1)
        if len(attempts) == 1:
            raise FormcastError("detector missing")
        return "map"

    monkeypatch.setattr(documents_mod, "build_form_map", flaky)
    doc = Documents().add(small_pdf(), "x.pdf")
    with pytest.raises(FormcastError):
        doc.form_map()
    assert doc.form_map() == "map" and doc.form_map() == "map" and len(attempts) == 2


def test_page_png_caches_by_page_and_dpi_and_clamps():
    doc = Documents().add(small_pdf(pages=2), "x.pdf")
    a = doc.page_png(0, 72)
    assert doc.page_png(0, 72) is a
    assert doc.page_png(0, 5) is doc.page_png(0, 36) and doc.page_png(0, 999) is doc.page_png(0, 200)
    with pytest.raises(IndexError):
        doc.page_png(2, 72)


# ------------------------------------------------------------------ error shape and mapping


def _api() -> Api:
    return Api(Settings.from_env({}))


def test_errors_map_to_statuses_without_leaking_internals(caplog):
    api = _api()
    secret = "super-secret-internal-detail"

    def raiser(exc):
        def handler(req):
            raise exc

        return handler

    cases = {
        "/t/key": (KeyError("template-x"), 500, "internal"),  # a stray KeyError is a bug, not a 404
        "/t/api": (ApiError(409, "conflict", "already there"), 409, "conflict"),
        "/t/lib": (FormcastError("cannot open PDF"), 400, "bad_input"),
        "/t/value": (ValueError("bad template name"), 500, "internal"),
        "/t/boom": (RuntimeError(secret), 500, "internal"),
    }
    for path, (exc, _, _) in cases.items():
        api._add("GET", path, raiser(exc))
    with caplog.at_level(logging.ERROR, logger="demo.server"):
        for path, (exc, status, code) in cases.items():
            got_status, headers, body = api.route("GET", path)
            payload = json.loads(body)
            assert (got_status, payload["error"]) == (status, code), path
            assert set(payload) == {"error", "detail"}
            assert headers["Content-Type"].startswith("application/json")
            assert secret not in body.decode()
    assert json.loads(api.route("GET", "/t/lib")[2])["detail"] == "cannot open PDF"
    assert json.loads(api.route("GET", "/t/boom")[2])["detail"] == "unexpected error; the server log has the details"
    assert secret in caplog.text and "Traceback" in caplog.text  # the log has the traceback


def test_expected_key_and_value_errors_are_answered_where_the_handler_calls_the_store(caplog):
    from demo.server.api import bad_request, missing
    from demo.server.records import RecordSourceError

    api = _api()

    def lookup(req):
        with missing("template"):
            api.store.get(req.query["name"])

    def named(req):
        with bad_request():
            api.store.get(req.query["name"])

    def records(req):
        with bad_request():
            raise RecordSourceError("person.csv is empty")

    def stray_inside(req):
        with missing("template"):
            raise RuntimeError("not a KeyError")

    for path, handler in [("/t/lookup", lookup), ("/t/named", named), ("/t/records", records), ("/t/stray", stray_inside)]:
        api._add("GET", path, handler)
    status, _, body = api.route("GET", "/t/lookup", {"name": "absent"})
    assert (status, json.loads(body)) == (404, {"error": "not_found", "detail": "no such template: absent"})
    status, _, body = api.route("GET", "/t/named", {"name": "../x"})
    assert (status, json.loads(body)) == (400, {"error": "bad_request", "detail": "bad template name"})
    assert api.route("GET", "/t/records")[0] == 422  # still its own answer inside bad_request()
    with caplog.at_level(logging.ERROR, logger="demo.server"):
        assert api.route("GET", "/t/stray")[0] == 500


def test_stored_template_and_record_source_errors_are_422(caplog):
    from demo.server.records import RecordSourceError
    from demo.server.store import StoredTemplateError

    api = _api()

    def raiser(exc):
        def handler(req):
            raise exc

        return handler

    api._add("GET", "/t/stored", raiser(StoredTemplateError("stored template 'x' is not a valid template")))
    api._add("GET", "/t/records", raiser(RecordSourceError("person.csv is empty")))
    assert api.route("GET", "/t/stored")[0] == 422
    assert json.loads(api.route("GET", "/t/records")[2]) == {"error": "bad_records", "detail": "person.csv is empty"}


def test_unknown_routes_are_404_and_wrong_methods_are_405(demo_server):
    status, body = get_json(demo_server, "/api/nope")
    assert (status, body["error"]) == (404, "not_found")
    status, headers, out = http_request(demo_server, "DELETE", "/api/forms")
    assert status == 405 and headers["allow"] == "GET" and json.loads(out)["error"] == "method_not_allowed"
    assert http_request(demo_server, "GET", "/api")[0] == 404


def test_head_on_an_api_route_has_no_body(demo_server):
    status, headers, body = http_request(demo_server, "HEAD", "/api/capabilities")
    assert (status, body) == (200, b"") and int(headers["content-length"]) > 0


def test_a_request_from_another_site_cannot_use_the_api(demo_server):
    host = demo_server.removeprefix("http://")
    body = json.dumps({"form": "fw9"}).encode()
    for origin, expected in [("http://evil.example", 403), (f"http://{host}", 200), ("null", 403)]:
        status, _, out = http_request(
            demo_server, "POST", "/api/documents", body, {"Content-Type": "application/json", "Origin": origin}
        )
        assert status == expected, origin
    assert http_request(demo_server, "GET", "/api/capabilities", None, {"Origin": "http://evil.example"})[0] == 200


def test_chunked_bodies_are_refused_plainly(demo_server):
    status, _, out = http_request(demo_server, "POST", "/api/documents", b"0\r\n\r\n", {"Transfer-Encoding": "chunked"})
    assert (status, json.loads(out)["error"]) == (411, "length_required")


# ------------------------------------------------------------------ static files


def test_static_files_have_the_right_types(demo_server):
    for path, content_type in [
        ("/showcase/index.html", "text/html"),
        ("/showcase/showcase.css", "text/css"),
        ("/core/geometry.js", "text/javascript"),
        ("/vendor/pdfjs/build/pdf.min.mjs", "text/javascript"),
        ("/vendor/onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm", "application/wasm"),
        ("/vendor/README.md", "text/markdown"),
    ]:
        status, headers, body = http_request(demo_server, "GET", path)
        assert status == 200, path
        assert headers["content-type"].startswith(content_type), (path, headers["content-type"])
        assert int(headers["content-length"]) == len(body) > 0
    status, headers, body = http_request(demo_server, "HEAD", "/vendor/pdfjs/build/pdf.min.mjs")
    assert (status, body) == (200, b"") and int(headers["content-length"]) > 100_000


def test_showcase_is_served_under_its_folder_and_directories_have_no_listing(demo_server):
    status, headers, _ = http_request(demo_server, "GET", "/showcase")
    assert (status, headers["location"]) == (301, "/showcase/")
    status, _, body = http_request(demo_server, "GET", "/showcase/")
    assert status == 200 and b"<html" in body.lower()
    for path in ("/core/", "/core", "/vendor/", "/vendor/pdfjs/"):
        status, headers, body = http_request(demo_server, "GET", path)
        assert status in (301, 404), path
        if status == 301:
            assert http_request(demo_server, "GET", headers["location"])[0] == 404
    assert b"geometry.js" not in http_request(demo_server, "GET", "/core/")[2]


@pytest.mark.parametrize(
    "path",
    [
        "/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/%2E%2E%2Fpyproject.toml",
        "/showcase/../../pyproject.toml",
        "/showcase/..%2f..%2fpyproject.toml",
        "/..\\pyproject.toml",
        "/showcase/%5c..%5c..%5cpyproject.toml",
        "/%00",
        "/showcase/index.html%00.png",
        "/../../../../../../etc/passwd",
        "/does/not/exist.js",
    ],
)
def test_paths_that_leave_demo_web_or_do_not_exist_are_404(demo_server, path):
    status, headers, body = http_request(demo_server, "GET", path)
    assert status == 404, path
    assert b"[project]" not in body and b"root:" not in body


def test_a_double_slash_path_never_redirects_to_another_host(demo_server):
    for path in ("//showcase", "//evil.example/showcase", "///showcase"):
        status, headers, _ = http_request(demo_server, "GET", path)
        assert headers.get("location", "/").startswith("/") and not headers.get("location", "/").startswith("//"), path


def test_a_symlink_out_of_the_web_root_is_not_followed(tmp_path, start_demo_server):
    web = tmp_path / "web"
    web.mkdir()
    (web / "ok.txt").write_text("fine", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (web / "link.txt").symlink_to(secret)
    from demo.server.server import resolve_static

    assert resolve_static(web, "/ok.txt") == (web / "ok.txt").resolve()
    assert resolve_static(web, "/link.txt") is None


def test_static_paths_only_serve_get_and_head(demo_server):
    status, headers, out = http_request(demo_server, "POST", "/showcase/index.html", b"x")
    assert status == 405 and json.loads(out)["error"] == "method_not_allowed"


def test_the_root_goes_to_a_page_that_exists(demo_server):
    status, headers, _ = http_request(demo_server, "GET", "/")
    if (ROOT / "demo" / "web" / "index.html").is_file():
        assert status == 200
    else:
        assert status == 302 and headers["location"] in ("/workbench/", "/showcase/")
        assert http_request(demo_server, "GET", headers["location"])[0] == 200


def test_the_workbench_page_is_served(demo_server):
    status, headers, body = http_request(demo_server, "GET", "/workbench/")
    assert status == 200 and headers["content-type"].startswith("text/html") and b"<html" in body.lower()


# ------------------------------------------------------------------ browser-facing hardening


def raw_exchange(base: str, payload: bytes, wait: float = 1.0) -> bytes:
    """Send raw bytes on one socket and return everything the server answers until it closes or goes quiet."""
    import socket

    parts = urlsplit(base)
    out = b""
    with socket.create_connection((parts.hostname, parts.port), timeout=wait) as sock:
        sock.sendall(payload)
        try:
            while chunk := sock.recv(65536):
                out += chunk
        except socket.timeout:
            pass
    return out


def test_an_unread_body_cannot_smuggle_a_second_request(start_demo_server):
    base = start_demo_server()
    host = base.removeprefix("http://")
    inner_body = '{"form":"fw9"}'
    inner = f"POST /api/documents HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {len(inner_body)}\r\n\r\n{inner_body}"
    outer = (
        f"POST /showcase/ HTTP/1.1\r\nHost: {host}\r\nOrigin: http://evil.example\r\n"
        f"Content-Length: {len(inner)}\r\n\r\n{inner}"
    ).encode()
    answer = raw_exchange(base, outer)
    assert answer.count(b"HTTP/1.1 ") == 1 and b"405" in answer.split(b"\r\n", 1)[0]
    assert b"connection: close" in answer.lower()
    assert len(start_demo_server.servers[-1].api.documents) == 0


def test_no_early_exit_leaves_a_body_to_be_parsed_as_the_next_request(start_demo_server):
    base = start_demo_server()
    api = start_demo_server.servers[-1].api
    host = base.removeprefix("http://")
    inner_body = '{"form":"fw9"}'
    inner = f"POST /api/documents HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {len(inner_body)}\r\n\r\n{inner_body}"
    head = lambda line, extra="": f"{line} HTTP/1.1\r\nHost: {host}\r\n{extra}Content-Length: {len(inner)}\r\n\r\n{inner}".encode()  # noqa: E731
    attempts = [
        head("POST /showcase/"),  # static 405
        head("POST /showcase/", "Origin: http://evil.example\r\n"),
        head("GET /api/capabilities"),  # a GET that carries a body
        head("GET /showcase/index.html"),
        head("POST /api/documents", "Origin: http://evil.example\r\n"),  # cross-origin 403
        head("POST /api/documents", "Transfer-Encoding: chunked\r\n"),  # 411
    ]
    for payload in attempts:
        answer = raw_exchange(base, payload)
        assert answer.count(b"HTTP/1.1 ") == 1, payload[:40]
        assert b"connection: close" in answer.lower(), payload[:40]
    wrong_host = f"POST /showcase/ HTTP/1.1\r\nHost: attacker.example\r\nContent-Length: {len(inner)}\r\n\r\n{inner}".encode()
    assert raw_exchange(base, wrong_host).count(b"HTTP/1.1 ") == 1
    assert len(api.documents) == 0
    # a well-formed pipeline of two requests still works on one connection
    two = f"GET /api/capabilities HTTP/1.1\r\nHost: {host}\r\n\r\nGET /api/forms HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
    assert raw_exchange(base, two).count(b"HTTP/1.1 200") == 2


def test_the_smuggled_request_in_those_tests_is_complete_and_would_be_served_if_parsed(start_demo_server):
    # Guards the two tests above: a well-formed inner request really does open a document when sent alone,
    # so "no document, one response" means the server did not parse it.
    base = start_demo_server()
    host = base.removeprefix("http://")
    inner_body = '{"form":"fw9"}'
    inner = (
        f"POST /api/documents HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(inner_body)}\r\nConnection: close\r\n\r\n{inner_body}"
    ).encode()
    assert raw_exchange(base, inner).startswith(b"HTTP/1.1 200")
    assert len(start_demo_server.servers[-1].api.documents) == 1


def test_a_request_with_two_content_length_headers_is_refused_and_closed(start_demo_server):
    base = start_demo_server()
    host = base.removeprefix("http://")
    body = '{"form":"fw9"}'
    second = f"POST /api/documents HTTP/1.1\r\nHost: {host}\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    payload = (
        f"POST /api/documents HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n"
        f"Content-Length: 0\r\nContent-Length: {len(second)}\r\n\r\n{second}"
    ).encode()
    answer = raw_exchange(base, payload)
    assert answer.count(b"HTTP/1.1 ") == 1 and answer.startswith(b"HTTP/1.1 400")
    assert b"connection: close" in answer.lower()
    assert len(start_demo_server.servers[-1].api.documents) == 0
    # GET with two lengths is refused too
    answer = raw_exchange(base, f"GET /api/capabilities HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n".encode())
    assert answer.startswith(b"HTTP/1.1 400")


def test_a_client_that_stalls_mid_body_is_closed_quietly(start_demo_server, caplog):
    import socket

    base = start_demo_server()
    handler = start_demo_server.servers[-1].RequestHandlerClass
    handler.timeout = 0.3
    try:
        parts = urlsplit(base)
        with caplog.at_level(logging.INFO, logger="demo.server"):
            with socket.create_connection((parts.hostname, parts.port), timeout=10) as sock:
                sock.sendall(
                    f"POST /api/documents HTTP/1.1\r\nHost: {parts.netloc}\r\nContent-Type: application/json\r\n"
                    "Content-Length: 14\r\n\r\n{\"form\"".encode()
                )  # promises 14 bytes, sends 7, then goes quiet
                assert sock.recv(1024) == b""  # closed with no answer
            time.sleep(0.2)
    finally:
        handler.timeout = 30
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text
    assert "Traceback" not in caplog.text and "stalled" in caplog.text
    assert http_request(base, "GET", "/api/capabilities")[0] == 200


def test_a_request_under_another_host_name_is_refused(demo_server):
    port = urlsplit(demo_server).port
    body = json.dumps({"form": "fw9"}).encode()
    # DNS rebinding: the attacker's page and the request agree on Host and Origin, so only the Host check can stop it.
    status, _, out = http_request(
        demo_server, "POST", "/api/documents", body,
        {"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}", "Content-Type": "application/json"},
    )  # fmt: skip
    assert (status, json.loads(out)["error"]) == (403, "bad_host")
    for host in ("attacker.example", f"attacker.example:{port}", f"127.0.0.1:{port + 1}", "127.0.0.1", f"0.0.0.0:{port}", ""):
        assert http_request(demo_server, "GET", "/api/capabilities", None, {"Host": host})[0] == 403, host
        assert http_request(demo_server, "GET", "/showcase/index.html", None, {"Host": host})[0] == 403, host
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}", f"[::1]:{port}"):
        assert http_request(demo_server, "GET", "/api/capabilities", None, {"Host": host})[0] == 200, host


def test_allowed_hosts_include_the_bind_address_unless_it_is_a_wildcard():
    from demo.server.server import allowed_hosts

    assert allowed_hosts("127.0.0.1", 8765) == {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
    assert allowed_hosts("0.0.0.0", 8765) == {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
    assert "192.168.1.5:9000" in allowed_hosts("192.168.1.5", 9000)
    assert "[fe80::1]:9000" in allowed_hosts("fe80::1", 9000)


def test_every_response_refuses_to_be_framed(demo_server):
    for method, path, body in [
        ("GET", "/api/capabilities", None),
        ("GET", "/api/nope", None),
        ("DELETE", "/api/forms", None),
        ("GET", "/showcase/index.html", None),
        ("GET", "/showcase", None),
        ("GET", "/missing.js", None),
        ("POST", "/showcase/", b"x"),
        ("GET", "/", None),
        ("HEAD", "/showcase/index.html", None),
    ]:
        status, headers, _ = http_request(demo_server, method, path, body)
        assert headers["x-frame-options"] == "DENY", (method, path, status)
        assert headers["content-security-policy"] == "frame-ancestors 'none'", (method, path)
        assert headers["x-content-type-options"] == "nosniff"
    _, doc = upload(demo_server, "fw9.pdf", FW9)
    assert http_request(demo_server, "GET", f"/api/documents/{doc['id']}/pages/0.png")[1]["x-frame-options"] == "DENY"
    over = http_request(demo_server, "POST", "/api/documents", b"x", {"Host": "evil.example", "Content-Type": "application/json"})
    assert over[0] == 403 and over[1]["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "path", ["/.git/config", "/.env", "/showcase/.hidden", "/.well-known/x", "/%2egit/config", "/showcase/%2E%2E/x", "/vendor/.DS_Store"]
)
def test_dot_files_are_never_served(demo_server, path):
    assert http_request(demo_server, "GET", path)[0] == 404


def test_dot_files_in_the_web_root_are_hidden_even_when_present(tmp_path):
    from demo.server.server import resolve_static

    (tmp_path / ".secret").write_text("x", encoding="utf-8")
    (tmp_path / ".d").mkdir()
    (tmp_path / ".d" / "f.txt").write_text("x", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    assert resolve_static(tmp_path, "/.secret") is None and resolve_static(tmp_path, "/.d/f.txt") is None
    assert resolve_static(tmp_path, "/ok.txt") == (tmp_path / "ok.txt").resolve()


def test_a_stalled_connection_is_dropped(start_demo_server):
    from demo.server.server import REQUEST_TIMEOUT

    assert REQUEST_TIMEOUT == 30
    base = start_demo_server()
    handler = start_demo_server.servers[-1].RequestHandlerClass
    assert handler.timeout == 30
    handler.timeout = 0.3  # read per connection, so this applies to the next one
    try:
        import socket

        parts = urlsplit(base)
        with socket.create_connection((parts.hostname, parts.port), timeout=10) as sock:
            sock.sendall(b"GET /api/capabilities HTTP/1.1\r\nHost: x")  # never finishes the request
            assert sock.recv(1024) == b""  # the server closed the connection
        assert http_request(base, "GET", "/api/capabilities")[0] == 200  # and still serves others
    finally:
        handler.timeout = 30


def test_a_client_that_goes_away_is_logged_quietly(start_demo_server, caplog, capsys):
    start_demo_server()
    httpd = start_demo_server.servers[-1]
    with caplog.at_level(logging.INFO, logger="demo.server"):
        for exc in (ConnectionResetError(54, "reset"), BrokenPipeError(), TimeoutError()):
            try:
                raise exc
            except Exception:
                httpd.handle_error(None, ("127.0.0.1", 5555))
    assert "Traceback" not in caplog.text and capsys.readouterr().err == ""
    assert caplog.text.count("went away") == 3
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="demo.server"):
        try:
            raise RuntimeError("real bug")
        except RuntimeError:
            httpd.handle_error(None, ("127.0.0.1", 5555))
    assert "Traceback" in caplog.text and "real bug" in caplog.text and capsys.readouterr().err == ""


def test_mupdf_work_runs_under_the_process_wide_lock(monkeypatch):
    held = []

    class Probe:
        def __init__(self, real):
            self.real = real

        def __enter__(self):
            held.append("enter")
            return self.real.__enter__()

        def __exit__(self, *a):
            held.append("exit")
            return self.real.__exit__(*a)

    monkeypatch.setattr(documents_mod, "PYMUPDF_LOCK", Probe(documents_mod.PYMUPDF_LOCK))
    doc = Documents().add(small_pdf(), "x.pdf")
    assert held == ["enter", "exit"]  # the light open
    doc.page_png(0, 72)
    assert held == ["enter", "exit"] * 2  # the render
    doc.page_png(0, 72)
    assert held == ["enter", "exit"] * 2  # a cache hit needs no MuPDF
    assert hasattr(documents_mod.PYMUPDF_LOCK.real, "acquire")


def test_renders_of_different_documents_do_not_overlap_inside_mupdf(monkeypatch):
    inside, peak = [0], [0]
    guard = threading.Lock()
    real_open = documents_mod.pymupdf.open

    class Watched:
        def __init__(self, doc):
            self.doc = doc

        def __getitem__(self, n):
            page = self.doc[n]

            class P:
                def get_pixmap(_, **kw):
                    with guard:
                        inside[0] += 1
                        peak[0] = max(peak[0], inside[0])
                    try:
                        import time

                        time.sleep(0.05)
                        return page.get_pixmap(**kw)
                    finally:
                        with guard:
                            inside[0] -= 1

            return P()

        def close(self):
            self.doc.close()

    docs = [Documents().add(small_pdf(text=str(i)), f"{i}.pdf") for i in range(4)]
    monkeypatch.setattr(documents_mod.pymupdf, "open", lambda *a, **k: Watched(real_open(*a, **k)))
    threads = [threading.Thread(target=d.page_png, args=(0, 72)) for d in docs]
    [t.start() for t in threads]
    [t.join(timeout=20) for t in threads]
    assert peak[0] == 1


# ------------------------------------------------------------------ wiring and startup


def test_make_server_uses_the_store_and_records_it_is_given(start_demo_server):
    store, records = object(), object()
    start_demo_server(store=store, records=records)
    api = start_demo_server.servers[-1].api
    assert api.store is store and api.records is records


def test_the_default_store_and_records_read_the_data_folder(demo_server, start_demo_server):
    api = start_demo_server.servers[-1].api
    assert {t["name"] for t in api.store.list()} == {"fw9", "menomonie_app", "wadoc_auth"}
    assert set(api.records.schemas()) == {"fw9", "menomonie_app", "wadoc_auth"}


def test_the_server_binds_localhost_by_default_and_shuts_down_cleanly(start_demo_server):
    from demo.server.settings import Settings

    assert Settings.from_env({}).host == "127.0.0.1"
    base = start_demo_server()
    httpd = start_demo_server.servers[-1]
    assert httpd.server_address[0] == "127.0.0.1"
    assert http_request(base, "GET", "/api/capabilities")[0] == 200


def test_the_command_line_builds_a_server_from_flags_and_a_mapping(tmp_path):
    from demo.server.__main__ import build

    httpd = build(["--port", "0", "--host", "127.0.0.1", "--data-dir", str(tmp_path)], env={"FORMCAST_DEMO_PORT": "9"})
    try:
        assert httpd.server_address[0] == "127.0.0.1" and httpd.server_address[1] != 9
        assert httpd.api.settings.data_dir == tmp_path.resolve()
        assert httpd.api.provider_factory is None
    finally:
        httpd.server_close()


def test_a_wildcard_bind_prints_a_url_that_works_and_says_how_to_serve_the_network(tmp_path, capsys, monkeypatch):
    from demo.server import __main__ as entry

    class Stub:
        server_address = ("0.0.0.0", 8765)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(entry, "build", lambda argv=None: Stub())
    assert entry.main(["--host", "0.0.0.0"]) == 0
    out = capsys.readouterr()
    assert "http://127.0.0.1:8765/workbench/" in out.out and "0.0.0.0:8765" not in out.out
    assert "--host" in out.err and "another machine" in out.err
    Stub.server_address = ("192.168.1.5", 9000)
    assert entry.main([]) == 0
    out = capsys.readouterr()
    assert "http://192.168.1.5:9000/workbench/" in out.out and "--host" not in out.err.split("Warning")[0]


def test_python_dash_m_demo_server_runs_without_formcast_installed():
    # In this venv formcast is not installed (pytest adds src/ to sys.path); the module puts src/ there itself.
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); sys.argv = ['demo.server', '--help']; import runpy; runpy.run_module('demo.server', run_name='__main__')"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "usage: python -m demo.server" in result.stdout
