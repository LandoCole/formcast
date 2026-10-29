"""demo/server extraction foundations: task jobs, the two settings, several files per upload, ``page_png``.

Task jobs run a caller's function on a job thread (``Jobs.start_task``); the extraction jobs of the workbench are
built on them. Everything runs in process: no model, no network.
"""

from __future__ import annotations

import threading
import time

import pymupdf
import pytest
from test_server_documents import get_json, http_request, small_pdf

from demo.server import documents as documents_mod
from demo.server.api import Api, ApiError, Request, _uploads
from demo.server.documents import PYMUPDF_LOCK, page_png
from demo.server.jobs import Jobs, JobsBusy
from demo.server.settings import Settings
from formcast import FormcastError, SessionCancelled
from formcast.pagemap.build import DetectPolicy


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
