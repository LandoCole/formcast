"""demo/server extraction: task jobs, the two settings, several files per upload, ``page_png``, and batches of PDFs.

Task jobs run a caller's function on a job thread (``Jobs.start_task``); the extraction jobs of the workbench are
built on them. Batches are PDFs uploaded (singly or in zips) or found in a server-side folder, scanned by layout on
a thread, with a paged file list, a sample picked from the main layout and page images. The PDFs are the generated
client-information corpus (``tests/extract/corpus.py``). Everything runs in process: no model, no network.
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest
from test_server_documents import get_json, http_request, png_size, small_pdf
from test_server_jobs import wait_for

from demo.server import documents as documents_mod
from demo.server import extraction
from demo.server.api import Api, ApiError, Request, _uploads
from demo.server.documents import PYMUPDF_LOCK, page_png
from demo.server.jobs import Jobs, JobsBusy
from demo.server.settings import Settings
from formcast import FormcastError, SessionCancelled
from formcast.pagemap.build import DetectPolicy
from tests.extract.corpus import clients, make_form


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf, detectors=None, **kw: real(pdf, detectors={}, policy=policy, ocr="never"))


def wait_ended(jobs: Jobs, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = jobs.get(job_id).snapshot()
        if snap["status"] != "running":
            return snap
        time.sleep(0.01)
    raise AssertionError("the job never ended")


# ------------------------------------------------------------------ task jobs


def test_a_task_job_starts_with_its_stages_pending_and_ends_done_with_the_stages_and_result_the_runner_set():
    jobs = Jobs()
    release = threading.Event()

    def runner(job):
        assert release.wait(10)
        job.stage("a", "done", "all of it")
        job.set_result(count=3)

    job = jobs.start_task("extract-setup", runner, stages=[("a", "Read the samples"), ("b", "Find the boxes")])
    assert job.kind == "extract-setup"
    assert job.snapshot()["stages"] == [
        {"key": "a", "label": "Read the samples", "status": "pending", "detail": ""},
        {"key": "b", "label": "Find the boxes", "status": "pending", "detail": ""},
    ]
    release.set()
    snap = wait_ended(jobs, job.id)
    assert snap["status"] == "done" and snap["error"] is None and snap["ended"] is not None
    assert snap["stages"][0] == {"key": "a", "label": "Read the samples", "status": "done", "detail": "all of it"}
    assert snap["stages"][1]["status"] == "pending"  # a done job leaves a stage the runner never reached as it was
    assert snap["result"] == {"count": 3}


def test_a_task_job_with_no_stages_has_none():
    jobs = Jobs()
    snap = wait_ended(jobs, jobs.start_task("extract-run", lambda job: None).id)
    assert snap["status"] == "done" and snap["stages"] == []


def test_a_task_job_that_raises_session_cancelled_ends_cancelled_and_skips_its_open_stages():
    jobs = Jobs()

    def runner(job):
        job.stage("a", "running")
        raise SessionCancelled("cancelled")

    snap = wait_ended(jobs, jobs.start_task("extract-run", runner, stages=[("a", "A"), ("b", "B")]).id)
    assert snap["status"] == "cancelled" and snap["error"] is None
    assert [st["status"] for st in snap["stages"]] == ["skipped", "skipped"]


def test_a_task_job_that_raises_a_formcast_error_ends_error_with_its_message():
    jobs = Jobs()

    def runner(job):
        job.stage("a", "running")
        raise FormcastError("no")

    snap = wait_ended(jobs, jobs.start_task("extract-line", runner, stages=[("a", "A")]).id)
    assert snap["status"] == "error" and snap["error"] == "no"
    assert snap["stages"][0]["status"] == "error"


def test_a_task_jobs_error_message_has_the_secrets_removed():
    jobs = Jobs(secrets=["sk-secret-value"])

    def runner(job):
        raise FormcastError("the key sk-secret-value was refused")

    snap = wait_ended(jobs, jobs.start_task("extract-line", runner).id)
    assert snap["error"] == "the key *** was refused"


def test_a_task_job_that_fails_unexpectedly_says_so_generically_and_logs_the_traceback_only(caplog):
    jobs = Jobs(secrets=["sk-secret-value"])

    def runner(job):
        raise RuntimeError("disk melted sk-secret-value")

    with caplog.at_level("ERROR", logger="demo.server"):
        snap = wait_ended(jobs, jobs.start_task("extract-run", runner).id)
    assert snap["status"] == "error"
    assert snap["error"] == "The job stopped because of an unexpected error. The server log has the details."
    assert "melted" not in str(snap) and "sk-secret-value" not in str(snap)
    assert "disk melted" in caplog.text and "sk-secret-value" not in caplog.text  # the log has it, redacted


def test_a_runner_sees_a_cancel_request_through_its_job():
    jobs = Jobs()
    started = threading.Event()

    def runner(job):
        started.set()
        while not job.cancel_requested:
            time.sleep(0.005)
        raise SessionCancelled("cancelled")

    job = jobs.start_task("extract-run", runner)
    assert started.wait(5)
    jobs.cancel(job.id)
    assert wait_ended(jobs, job.id)["status"] == "cancelled"


def test_task_jobs_take_the_same_slots_as_the_other_jobs():
    jobs = Jobs(max_running=2)
    release = threading.Event()
    first = jobs.start_task("extract-run", lambda job: release.wait(10))
    second = jobs.start_task("extract-run", lambda job: release.wait(10))
    with pytest.raises(JobsBusy):
        jobs.start_task("extract-run", lambda job: None)
    release.set()
    wait_ended(jobs, first.id)
    wait_ended(jobs, second.id)
    assert wait_ended(jobs, jobs.start_task("extract-run", lambda job: None).id)["status"] == "done"


def test_a_task_job_that_cannot_start_its_thread_frees_its_slot(monkeypatch):
    jobs = Jobs(max_running=1)

    def refuse(self):
        raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", refuse)
        with pytest.raises(RuntimeError):
            jobs.start_task("extract-run", lambda job: None)
    assert wait_ended(jobs, jobs.start_task("extract-run", lambda job: None).id)["status"] == "done"


# ------------------------------------------------------------------ settings


def test_the_extraction_settings_default_to_a_folder_scan_and_no_workers_of_their_own():
    settings = Settings.from_env({})
    assert settings.folder_scan is True and settings.extract_workers == 0


def test_the_extraction_settings_are_read_from_the_environment():
    settings = Settings.from_env({"FORMCAST_DEMO_FOLDER_SCAN": "off", "FORMCAST_DEMO_EXTRACT_WORKERS": "2"})
    assert settings.folder_scan is False and settings.extract_workers == 2
    assert Settings.from_env({"FORMCAST_DEMO_FOLDER_SCAN": "Yes", "FORMCAST_DEMO_EXTRACT_WORKERS": "0"}).folder_scan is True


@pytest.mark.parametrize(
    "name, value",
    [
        ("FORMCAST_DEMO_EXTRACT_WORKERS", "-1"),
        ("FORMCAST_DEMO_EXTRACT_WORKERS", "two"),
        ("FORMCAST_DEMO_EXTRACT_WORKERS", "1.5"),
        ("FORMCAST_DEMO_FOLDER_SCAN", "maybe"),
    ],
)
def test_a_bad_extraction_setting_is_refused_naming_the_variable_but_not_echoing_its_value(name, value):
    with pytest.raises(ValueError) as caught:
        Settings.from_env({name: value})
    message = str(caught.value)
    assert name in message and value not in message.replace(name, "")


# ------------------------------------------------------------------ the capability and the job kinds


def test_capabilities_say_extract_is_available(demo_server):
    status, caps = get_json(demo_server, "/api/capabilities")
    assert status == 200 and caps["extract"] is True


def test_an_unknown_job_kind_is_refused_naming_every_kind_even_with_no_model(demo_server):
    status, body = get_json(demo_server, "/api/jobs", "POST", {"kind": "nope"})
    assert status == 400 and body["error"] == "bad_kind"
    for kind in ("build", "adapt", "revise", "oneshot", "extract-setup", "extract-line", "extract-run"):
        assert kind in body["detail"]


def test_the_api_holds_a_place_for_each_extraction_manager():
    api = Api(Settings.from_env({}))
    for name in ("batches", "setups", "extractions", "runs"):
        assert hasattr(api, name)


# ------------------------------------------------------------------ several files per upload


def multipart_files(parts: list[tuple[str, str, bytes]]) -> tuple[bytes, dict]:
    boundary = "----formcastmulti"
    body = b""
    for field, filename, data in parts:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        body += head.encode() + b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def request(body: bytes, headers: dict) -> Request:
    return Request("POST", "/x", {}, body, {k.lower(): v for k, v in headers.items()}, {})


def test_uploads_returns_every_part_named_file_in_order_and_skips_other_parts():
    body, headers = multipart_files([("file", "a.pdf", b"AAA"), ("note", "n.txt", b"ignored"), ("file", "dir/b.zip", b"BBB")])
    assert _uploads(request(body, headers)) == [("a.pdf", b"AAA"), ("b.zip", b"BBB")]


def test_uploads_with_no_part_named_file_is_a_bad_upload():
    body, headers = multipart_files([("other", "a.pdf", b"AAA")])
    with pytest.raises(ApiError) as caught:
        _uploads(request(body, headers))
    assert caught.value.status == 400 and caught.value.code == "bad_upload"
    with pytest.raises(ApiError) as caught:
        _uploads(request(b"not multipart", {"Content-Type": "text/plain"}))
    assert caught.value.code == "bad_upload"


def test_a_single_file_upload_still_opens_a_document(demo_server):
    body, headers = multipart_files([("file", "one.pdf", small_pdf())])
    status, _, _ = http_request(demo_server, "POST", "/api/documents", body, headers)
    assert status == 200


# ------------------------------------------------------------------ page_png


def test_page_png_renders_a_page_of_the_bytes_with_the_dpi_clamped():
    pdf = small_pdf(pages=2)
    png = page_png(pdf, 1, 72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert page_png(pdf, 1, 144) != png
    assert page_png(pdf, 0, 1) == page_png(pdf, 0, documents_mod.MIN_DPI)
    assert page_png(pdf, 0, 9999) == page_png(pdf, 0, documents_mod.MAX_DPI)


@pytest.mark.parametrize("n", [-1, 2, 99])
def test_page_png_of_a_page_out_of_range_is_an_index_error(n):
    with pytest.raises(IndexError):
        page_png(small_pdf(pages=2), n, 72)


def test_page_png_holds_the_mupdf_lock_while_it_opens_the_pdf(monkeypatch):
    pdf = small_pdf()
    owned = []
    real_open = pymupdf.open

    def watching_open(*args, **kwargs):
        owned.append(PYMUPDF_LOCK._is_owned())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(documents_mod.pymupdf, "open", watching_open)
    page_png(pdf, 0, 72)
    assert owned == [True]


def test_a_documents_page_png_is_the_module_functions_png():
    pdf = small_pdf(pages=2)
    doc = documents_mod.Documents().add(pdf, "x.pdf")
    assert doc.page_png(1, 90) == page_png(pdf, 1, 90)
    with pytest.raises(IndexError):
        doc.page_png(2, 90)


# ------------------------------------------------------------------ batches


@pytest.fixture(scope="module")
def corpus_pdfs() -> list[bytes]:
    """Five filled client-information forms, three fillable and two flattened: one layout."""
    kinds = ["fillable", "flattened", "fillable", "flattened", "fillable"]
    return [make_form(client, kind=kind) for client, kind in zip(clients(5), kinds)]


def zip_of(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def new_batch(base: str, **body) -> dict:
    status, batch = get_json(base, "/api/batches", "POST", body)
    assert status == 200, batch
    return batch


def add_files(base: str, batch_id: str, parts: list[tuple[str, bytes]]) -> tuple[int, dict]:
    body, headers = multipart_files([("file", name, data) for name, data in parts])
    status, _, out = http_request(base, "POST", f"/api/batches/{batch_id}/files", body, headers)
    return status, json.loads(out)


def scanned(base: str, batch_id: str) -> dict:
    """The batch once its scan has ended."""

    def ended():
        batch = get_json(base, f"/api/batches/{batch_id}")[1]
        return batch["scan"]["phase"] != "scanning" and batch

    return wait_for(ended, what="the scan to end")


def scan_ended(batch: extraction.Batch) -> dict:
    return wait_for(lambda: (s := batch.summary())["scan"]["phase"] != "scanning" and s, what="the scan to end")


@pytest.fixture
def five(demo_server, corpus_pdfs) -> tuple[str, dict]:
    """(base URL, the batch) of the five corpus PDFs, three uploaded as PDFs and two in a zip, scanned."""
    batch = new_batch(demo_server, name="Client forms")
    status, after = add_files(demo_server, batch["id"], [(f"c{i}.pdf", corpus_pdfs[i]) for i in range(3)])
    assert status == 200 and after["count"] == 3
    status, after = add_files(demo_server, batch["id"], [("more.zip", zip_of([("c3.pdf", corpus_pdfs[3]), ("c4.pdf", corpus_pdfs[4])]))])
    assert status == 200 and after["count"] == 5
    return demo_server, scanned(demo_server, batch["id"])


def batch_folder(start_demo_server, batch_id: str) -> Path:
    return start_demo_server.servers[-1].api.settings.data_dir / "local" / "batches" / batch_id


def test_a_new_batch_is_empty_and_its_scan_is_done_at_once(demo_server):
    batch = new_batch(demo_server, name="Client forms")
    assert len(batch["id"]) == 32
    assert batch == {
        "id": batch["id"], "name": "Client forms", "count": 0, "bytes": 0, "skipped": 0, "folder": False,
        "scan": {"phase": "done", "done": 0, "total": 0, "error": None, "groups": []},
    }  # fmt: skip
    assert get_json(demo_server, f"/api/batches/{batch['id']}") == (200, batch)
    assert new_batch(demo_server)["name"] == "Uploaded files"


def test_pdfs_uploaded_in_one_request_and_a_zip_in_another_are_one_batch_scanned_into_one_layout(five, corpus_pdfs):
    base, batch = five
    assert batch["count"] == 5 and batch["skipped"] == 0 and batch["folder"] is False
    assert batch["bytes"] == sum(len(pdf) for pdf in corpus_pdfs)
    scan = batch["scan"]
    assert scan["phase"] == "done" and scan["done"] == scan["total"] == 5 and scan["error"] is None
    assert scan["groups"] == [{"group": 1, "size": 5, "fillable": 3, "flattened": 2, "closeness": None}]


def test_a_zips_pdfs_are_stored_by_number_under_the_batch_whatever_their_names_and_its_other_entries_are_skipped(start_demo_server, corpus_pdfs, tmp_path):
    base = start_demo_server()
    batch = new_batch(base)
    before = set(tmp_path.rglob("*"))
    archive = zip_of([("../evil.pdf", corpus_pdfs[0]), ("/abs.pdf", corpus_pdfs[1]), ("sub/ok.pdf", corpus_pdfs[2]), ("notes.txt", b"hello")])
    status, after = add_files(base, batch["id"], [("bad names.zip", archive)])
    assert status == 200 and after["count"] == 3 and after["skipped"] == 1
    folder = batch_folder(start_demo_server, batch["id"])
    assert sorted(p.name for p in (folder / "files").iterdir()) == ["0.pdf", "1.pdf", "2.pdf"]
    assert (folder / "files" / "2.pdf").read_bytes() == corpus_pdfs[2]
    written = set(tmp_path.rglob("*")) - before
    assert written and all(p == folder or folder in p.parents or p in folder.parents for p in written)
    assert not any(p.name in ("evil.pdf", "abs.pdf", "ok.pdf") for p in tmp_path.parent.rglob("*"))
    files = get_json(base, f"/api/batches/{batch['id']}/files")[1]["files"]
    assert [f["name"] for f in files] == ["evil.pdf", "abs.pdf", "ok.pdf"]


def test_a_zip_declaring_more_than_the_limit_is_refused_before_anything_of_the_request_is_written(start_demo_server, corpus_pdfs, monkeypatch):
    base = start_demo_server()
    batch = new_batch(base)
    assert add_files(base, batch["id"], [("a.pdf", corpus_pdfs[0])])[0] == 200
    monkeypatch.setattr(extraction, "MAX_ZIP_BYTES", len(corpus_pdfs[1]) + len(corpus_pdfs[2]) - 1)
    archive = zip_of([("b.pdf", corpus_pdfs[1]), ("c.pdf", corpus_pdfs[2])])
    status, body = add_files(base, batch["id"], [("d.pdf", corpus_pdfs[3]), ("big.zip", archive)])
    assert status == 400 and body["error"] == "bad_upload" and "big.zip" in body["detail"]
    after = scanned(base, batch["id"])
    assert after["count"] == 1 and after["skipped"] == 0
    assert [p.name for p in (batch_folder(start_demo_server, batch["id"]) / "files").iterdir()] == ["0.pdf"]


def test_an_upload_that_would_take_the_batch_past_the_most_files_is_refused(demo_server, corpus_pdfs, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_BATCH_FILES", 2)
    batch = new_batch(demo_server)
    status, body = add_files(demo_server, batch["id"], [(f"{i}.pdf", corpus_pdfs[i]) for i in range(3)])
    assert status == 400 and body["error"] == "bad_upload"
    assert get_json(demo_server, f"/api/batches/{batch['id']}")[1]["count"] == 0


def test_a_zip_that_cannot_be_opened_is_refused(demo_server):
    batch = new_batch(demo_server)
    status, body = add_files(demo_server, batch["id"], [("broken.zip", b"PK not a zip")])
    assert status == 400 and body["error"] == "bad_upload" and "broken.zip" in body["detail"]


def test_files_that_are_neither_pdfs_nor_zips_are_skipped_and_counted(demo_server, corpus_pdfs):
    batch = new_batch(demo_server)
    status, after = add_files(demo_server, batch["id"], [("A.PDF", corpus_pdfs[0]), ("notes.txt", b"x"), ("pic.png", b"y")])
    assert status == 200 and after["count"] == 1 and after["skipped"] == 2


def test_a_server_folder_is_scanned_in_place_without_copying_its_files(start_demo_server, corpus_pdfs, tmp_path):
    folder = tmp_path / "incoming"
    (folder / "sub").mkdir(parents=True)
    for i, pdf in enumerate(corpus_pdfs[:3]):
        (folder / f"{i}.pdf").write_bytes(pdf)
    (folder / "sub" / "3.PDF").write_bytes(corpus_pdfs[3])
    (folder / "readme.txt").write_text("not a pdf")
    base = start_demo_server()
    batch = new_batch(base, folder=str(folder))
    assert batch["count"] == 4 and batch["folder"] is True and batch["name"] == "incoming"
    assert str(tmp_path) not in json.dumps(batch)
    done = scanned(base, batch["id"])
    assert done["scan"]["phase"] == "done" and done["scan"]["groups"][0]["size"] == 4
    assert not batch_folder(start_demo_server, batch["id"]).exists()
    files = get_json(base, f"/api/batches/{batch['id']}/files")[1]["files"]
    assert [f["name"] for f in files] == ["0.pdf", "1.pdf", "2.pdf", "3.PDF"]
    assert http_request(base, "GET", f"/api/batches/{batch['id']}/files/3/pages/0.png")[0] == 200
    status, body = add_files(base, batch["id"], [("x.pdf", corpus_pdfs[4])])
    assert status == 400 and body["error"] == "bad_upload"  # a folder's batch is the folder


def test_a_folder_batch_is_refused_when_the_server_does_not_allow_it_or_the_folder_is_no_use(start_demo_server, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "a.pdf").write_bytes(small_pdf())
    status, body = get_json(start_demo_server(folder_scan=False), "/api/batches", "POST", {"folder": str(tmp_path)})
    assert status == 403 and body["error"] == "not_allowed"
    base = start_demo_server()
    for folder in (str(tmp_path / "missing"), str(tmp_path / "empty"), str(tmp_path / "a.pdf"), "", 7):
        status, body = get_json(base, "/api/batches", "POST", {"folder": folder})
        assert status == 400 and body["error"] == "bad_folder", folder
        assert str(tmp_path) not in body["detail"]


def test_a_batch_name_must_be_text(demo_server):
    status, body = get_json(demo_server, "/api/batches", "POST", {"name": 5})
    assert status == 400 and body["error"] == "bad_request"


def test_the_files_list_is_paged_and_a_page_is_at_most_the_limit(five):
    base, batch = five
    status, page = get_json(base, f"/api/batches/{batch['id']}/files?offset=1&limit=2")
    assert status == 200 and page["total"] == 5
    assert page["files"] == [
        {"index": 1, "name": "c1.pdf", "size": page["files"][0]["size"], "group": 1},
        {"index": 2, "name": "c2.pdf", "size": page["files"][1]["size"], "group": 1},
    ]
    assert get_json(base, f"/api/batches/{batch['id']}/files?offset=9")[1] == {"total": 5, "files": []}
    for query in ("limit=500", "limit=0", "offset=-1", "limit=two"):
        status, body = get_json(base, f"/api/batches/{batch['id']}/files?{query}")
        assert status == 400 and body["error"] == "bad_request", query


def test_a_sample_is_picked_from_the_main_layout_or_as_the_person_picked_it(five):
    base, batch = five
    url = f"/api/batches/{batch['id']}/sample"
    status, out = get_json(base, url, "POST", {"n": 3, "how": "spread"})
    assert status == 200 and len({s["index"] for s in out["samples"]}) == 3
    assert all(s["group"] == 1 and s["name"] == f"c{s['index']}.pdf" for s in out["samples"])
    assert [s["index"] for s in get_json(base, url, "POST", {"n": 3, "how": "first"})[1]["samples"]] == [0, 1, 2]
    assert [s["index"] for s in get_json(base, url, "POST", {"how": "pick", "picked": [4, 0]})[1]["samples"]] == [4, 0]
    bad = [
        {"n": 2, "how": "spread"}, {"n": 21, "how": "first"}, {"n": "3", "how": "first"}, {"n": 3, "how": "best"},
        {"how": "pick", "picked": [0, 4, 9]}, {"how": "pick", "picked": []}, {"how": "pick", "picked": [1, 1]},
    ]  # fmt: skip
    for body in bad:
        status, err = get_json(base, url, "POST", body)
        assert status == 400 and err["error"] == "bad_sample", body


def test_a_sample_waits_for_the_scan(tmp_path, corpus_pdfs, monkeypatch):
    from formcast.extract import scan as scan_mod

    real_read, release = scan_mod._read, threading.Event()
    monkeypatch.setattr(scan_mod, "_read", lambda path, first: release.wait(10) and real_read(path, first))
    batches = extraction.Batches(tmp_path / "batches")
    batch = batches.create("b")
    batches.add(batch, [("a.pdf", corpus_pdfs[0])])
    with pytest.raises(extraction.ScanNotDone):
        batches.pick(batch, 3, "spread", None)
    assert batches.pick(batch, 3, "pick", [0]) == [0]  # the person's own pick needs no scan
    release.set()
    scan_ended(batch)
    assert batches.pick(batch, 3, "spread", None) == [0]


def test_a_page_of_a_batch_file_is_a_png_and_a_page_or_batch_that_is_not_there_is_404(five):
    base, batch = five
    status, headers, png = http_request(base, "GET", f"/api/batches/{batch['id']}/files/0/pages/0.png?dpi=72")
    assert status == 200 and headers["content-type"] == "image/png"
    assert png_size(png) == (612, 792)
    status, body = get_json(base, f"/api/batches/{batch['id']}/files/0/pages/99.png")
    assert status == 404 and body["error"] == "not_found"
    status, body = get_json(base, f"/api/batches/{batch['id']}/files/5/pages/0.png")
    assert status == 404 and body["error"] == "not_found"
    status, body = get_json(base, f"/api/batches/{batch['id']}/files/0/pages/0.png?dpi=big")
    assert status == 400 and body["error"] == "bad_dpi"
    for path in (f"/api/batches/{'0' * 32}", f"/api/batches/{'0' * 32}/files", f"/api/batches/{'0' * 32}/files/0/pages/0.png"):
        status, body = get_json(base, path)
        assert status == 404 and body == {"error": "not_found", "detail": f"no such batch: {'0' * 32}"}


def test_an_unreadable_file_is_in_no_group_and_its_page_cannot_be_shown(demo_server, corpus_pdfs):
    batch = new_batch(demo_server)
    add_files(demo_server, batch["id"], [("good.pdf", corpus_pdfs[0]), ("broken.pdf", b"%PDF-1.7 not really")])
    done = scanned(demo_server, batch["id"])
    assert done["scan"]["phase"] == "done" and done["scan"]["groups"][0]["size"] == 1
    files = get_json(demo_server, f"/api/batches/{batch['id']}/files")[1]["files"]
    assert [f["group"] for f in files] == [1, None]
    status, body = get_json(demo_server, f"/api/batches/{batch['id']}/files/1/pages/0.png")
    assert status == 422 and body["error"] == "bad_pdf"


def test_an_upload_while_a_scan_runs_starts_it_again_and_the_old_scans_results_are_dropped(tmp_path, corpus_pdfs, monkeypatch):
    from formcast.extract import scan as scan_mod

    real_read = scan_mod._read
    reading, release, reads = threading.Event(), threading.Event(), []

    def slow_first_read(path, first_page_only):
        reads.append(Path(path).name)
        if len(reads) == 1:  # the first scan's first document takes a while
            reading.set()
            assert release.wait(10)
        return real_read(path, first_page_only)

    monkeypatch.setattr(scan_mod, "_read", slow_first_read)
    batches = extraction.Batches(tmp_path / "batches")
    batch = batches.create("b")
    batches.add(batch, [("a.pdf", corpus_pdfs[0]), ("b.pdf", corpus_pdfs[1])])
    assert reading.wait(10)
    batches.add(batch, [("c.pdf", corpus_pdfs[2])])  # starts the scan again, over all three
    assert batch.summary()["scan"] == {"phase": "scanning", "done": 0, "total": 3, "error": None, "groups": []}
    release.set()  # the first scan reads its document, sees it was replaced and stops; the second runs
    scan = scan_ended(batch)["scan"]
    assert scan["phase"] == "done" and scan["done"] == scan["total"] == 3 and scan["groups"][0]["size"] == 3
    assert reads == ["0.pdf", "0.pdf", "1.pdf", "2.pdf"]
    assert [batch.group_of(i) for i in range(3)] == [1, 1, 1]


def test_the_scan_reads_each_document_under_the_mupdf_lock_and_lets_go_at_the_end(tmp_path, corpus_pdfs, monkeypatch):
    from formcast.extract import scan as scan_mod

    real_read, owned = scan_mod._read, []
    monkeypatch.setattr(scan_mod, "_read", lambda path, first: owned.append(PYMUPDF_LOCK._is_owned()) or real_read(path, first))
    batches = extraction.Batches(tmp_path / "batches")
    batch = batches.create("b")
    batches.add(batch, [(f"{i}.pdf", pdf) for i, pdf in enumerate(corpus_pdfs[:3])])
    assert scan_ended(batch)["scan"]["phase"] == "done"
    assert owned == [True, True, True]
    assert PYMUPDF_LOCK.acquire(timeout=5)
    PYMUPDF_LOCK.release()


def test_a_large_batch_is_scanned_on_first_pages_only(tmp_path, corpus_pdfs, monkeypatch):
    real_scan, seen = extraction.scan, []
    monkeypatch.setattr(extraction, "scan", lambda paths, **kw: seen.append(kw["first_page_only"]) or real_scan(paths, **kw))
    monkeypatch.setattr(extraction, "FIRST_PAGE_ONLY_FROM", 2)
    batches = extraction.Batches(tmp_path / "batches")
    batch = batches.create("b")
    batches.add(batch, [("a.pdf", corpus_pdfs[0])])
    scan_ended(batch)
    batches.add(batch, [("b.pdf", corpus_pdfs[1])])
    wait_for(lambda: len(seen) == 2, what="the second scan")
    assert scan_ended(batch)["scan"]["groups"][0]["size"] == 2
    assert seen == [False, True]


def test_a_scan_that_fails_unexpectedly_says_so_generically(tmp_path, corpus_pdfs, monkeypatch, caplog):
    def broken(paths, **kw):
        raise RuntimeError("/secret/path melted")

    monkeypatch.setattr(extraction, "scan", broken)
    batches = extraction.Batches(tmp_path / "batches")
    batch = batches.create("b")
    with caplog.at_level("ERROR", logger="demo.server"):
        batches.add(batch, [("a.pdf", corpus_pdfs[0])])
        scan = scan_ended(batch)["scan"]
    assert scan["phase"] == "error" and scan["error"] and "secret" not in scan["error"]
    assert "melted" in caplog.text
    with pytest.raises(KeyError):
        batches.get("0" * 32)
    assert batches.get(batch.id) is batch
