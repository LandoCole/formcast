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
        "/t/key": (KeyError("template-x"), 404, "not_found"),
        "/t/api": (ApiError(409, "conflict", "already there"), 409, "conflict"),
        "/t/lib": (FormcastError("cannot open PDF"), 400, "bad_input"),
        "/t/value": (ValueError("bad template name"), 400, "bad_request"),
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


def test_the_workbench_page_is_served_once_it_exists(demo_server):
    if not (ROOT / "demo" / "web" / "workbench" / "index.html").is_file():
        pytest.skip("demo/web/workbench/ arrives with the workbench page (Plan 3B, Task 10)")
    status, headers, body = http_request(demo_server, "GET", "/workbench/")
    assert status == 200 and headers["content-type"].startswith("text/html") and b"<html" in body.lower()


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


def test_python_dash_m_demo_server_runs_without_formcast_installed():
    # In this venv formcast is not installed (pytest adds src/ to sys.path); the module puts src/ there itself.
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); sys.argv = ['demo.server', '--help']; import runpy; runpy.run_module('demo.server', run_name='__main__')"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "usage: python -m demo.server" in result.stdout
