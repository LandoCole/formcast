"""demo/server AI jobs: build, adapt and revise sessions on threads, with progress, cancel, limits and errors.

Everything runs against a scripted fake provider through ``provider_factory``: no network, no real model.
A ``Gate`` in a script blocks the provider until the test releases it, so a test can hold a job mid-run.
Form maps are built without detectors (the same autouse fixture as the route tests).
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from test_server_documents import SENTINEL_KEY, get_json, http_request, small_pdf, upload

from demo.server import documents as documents_mod
from demo.server.jobs import Jobs, JobsBusy
from formcast import Template
from formcast.llm import LLMConfig, LLMResponse, ToolCall, Usage
from formcast.pagemap.build import DetectPolicy, build_form_map

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}
PDF = small_pdf(text="Name")
OTHER_PDF = small_pdf(text="Full name of the applicant")
ADD_NAME = (
    'fid = add_field(type="text", page=0, rect=[60, 30, 150, 45], label="Name")\n'
    'set_source(fid, source={"paths": ["name"]})\n'
)
ADD_NOTE = 'add_field(type="text", page=0, rect=[60, 80, 150, 95], label="Note")\n'


@pytest.fixture(autouse=True)
def no_model_loads(monkeypatch):
    real = documents_mod.build_form_map
    policy = DetectPolicy(digital=(), scanned=())
    monkeypatch.setattr(documents_mod, "build_form_map", lambda pdf: real(pdf, detectors={}, policy=policy, ocr="never"))


# ------------------------------------------------------------------ a scripted provider


def reply(name: str, args: dict) -> LLMResponse:
    call = ToolCall(id=f"call_{name}", name=name, arguments=args, raw_arguments=json.dumps(args))
    return LLMResponse(text="", tool_calls=[call], usage=Usage(input_tokens=100, output_tokens=10), raw={})


def code(src: str) -> LLMResponse:
    return reply("run_python", {"code": src})


def finish(summary: str = "all mapped", unplaceable=()) -> LLMResponse:
    return reply("finish", {"summary": summary, "unplaceable": list(unplaceable)})


def chatter() -> LLMResponse:
    return LLMResponse(text="thinking", tool_calls=[], usage=Usage(input_tokens=10, output_tokens=1), raw={})


class Gate:
    """Placed in a script: the provider stops here until ``release()``; ``reached`` says it got here."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self._release = threading.Event()

    def release(self) -> None:
        self._release.set()


class FakeProvider:
    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[list] = []
        self.closed = False

    def complete(self, messages, tools):
        self.calls.append(list(messages))
        while self.script and isinstance(self.script[0], Gate):
            gate = self.script.pop(0)
            gate.reached.set()
            assert gate._release.wait(30), "the test never released the gate"
        if not self.script:
            raise AssertionError("scripted provider ran out of responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class Factory:
    """A ``provider_factory``: each job gets the next script. ``providers`` are the ones it built."""

    def __init__(self, *scripts) -> None:
        self.scripts = list(scripts)
        self.providers: list[FakeProvider] = []

    def __call__(self) -> FakeProvider:
        provider = FakeProvider(self.scripts.pop(0))
        self.providers.append(provider)
        return provider


# ------------------------------------------------------------------ helpers


def post(base: str, path: str, body: dict | None = None):
    return get_json(base, path, "POST", body if body is not None else {})


def open_doc(base: str, pdf: bytes = PDF, name: str = "form.pdf") -> str:
    status, doc = upload(base, name, pdf)
    assert status == 200, doc
    return doc["id"]


def seed(base: str, doc: str) -> dict:
    status, body = post(base, "/api/seed", {"doc": doc, "schema": SCHEMA})
    assert status == 200, body
    return body["template"]


def start(base: str, **body) -> str:
    status, out = post(base, "/api/jobs", body)
    assert status == 200, out
    return out["id"]


def job(base: str, job_id: str) -> dict:
    status, body = get_json(base, f"/api/jobs/{job_id}")
    assert status == 200, body
    return body


def wait_for(predicate, timeout: float = 20.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def wait_ended(base: str, job_id: str) -> dict:
    return wait_for(lambda: (j := job(base, job_id))["status"] != "running" and j, what="the job to end")


def wait_steps(base: str, job_id: str, n: int) -> dict:
    return wait_for(lambda: (j := job(base, job_id))["status"] == "running" and len(j["steps"]) >= n and j, what=f"{n} steps")


# ------------------------------------------------------------------ build


def test_a_build_job_reaches_done_with_a_step_event_per_reply_and_a_template(start_demo_server):
    factory = Factory([code(ADD_NAME), code("print(1)"), code("print(2)"), finish("name mapped")])
    base = start_demo_server(provider_factory=factory)
    doc = open_doc(base)
    job_id = start(base, kind="build", doc=doc, schema=SCHEMA)
    body = wait_ended(base, job_id)
    assert body["status"] == "done" and body["error"] is None
    assert body["id"] == job_id and body["kind"] == "build" and body["summary"] == "name mapped"
    assert [s["step"] for s in body["steps"]] == [1, 2, 3, 4]
    assert [s["finished"] for s in body["steps"]] == [False, False, False, True]
    assert body["steps"][0]["ops_applied"] == 2 and body["steps"][0]["unmapped_paths"] == 0
    assert body["steps"][3]["summary"] == "name mapped" and body["steps"][3]["input_tokens"] == 400
    template = Template.model_validate(body["template"])
    assert [f.source.paths for f in template.fields] == [["name"]]
    assert all(h.author == "agent" for h in template.history)
    assert isinstance(body["started"], float) and body["ended"] >= body["started"]
    assert factory.providers[0].closed  # the job closes the provider it was given


def test_a_running_job_shows_its_steps_so_far_and_no_template_yet(start_demo_server):
    gate = Gate()
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, finish()]))
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    gate.reached.wait(10)
    body = wait_steps(base, job_id, 1)
    assert body["status"] == "running" and body["template"] is None and body["ended"] is None
    gate.release()
    assert wait_ended(base, job_id)["status"] == "done"


def test_a_build_job_takes_the_name_of_a_stored_schema(start_demo_server):
    factory = Factory([finish("stop", [{"path": "vendor_id", "reason": "t"}])])
    base = start_demo_server(provider_factory=factory)
    job_id = start(base, kind="build", doc=open_doc(base), schema="fw9")
    wait_ended(base, job_id)
    assert "legal_name" in factory.providers[0].calls[0][1].content  # the stored schema's paths reached the model


# ------------------------------------------------------------------ adapt and revise


def test_an_adapt_job_starts_from_the_template_and_rebinds_it_to_the_new_pdf(start_demo_server):
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), finish()]))
    old_doc, new_doc = open_doc(base, PDF), open_doc(base, OTHER_PDF, "revised.pdf")
    template = seed(base, old_doc)
    job_id = start(base, kind="adapt", doc=new_doc, template=template)
    body = wait_ended(base, job_id)
    assert body["status"] == "done", body
    adapted = Template.model_validate(body["template"])
    assert adapted.schema_ == template["schema"]
    assert adapted.form.fingerprint != template["form"]["fingerprint"]  # now describes the new PDF
    assert [f.source.paths for f in adapted.fields] == [["name"]]


def test_a_revise_job_with_a_region_appends_agent_history_and_sends_the_instruction(start_demo_server):
    factory = Factory([code(ADD_NOTE), finish("added a note field")])
    base = start_demo_server(provider_factory=factory)
    doc = open_doc(base)
    template = seed(base, doc)
    status, applied = post(base, "/api/apply", {
        "template": template, "op": {"op": "add_field", "type": "text", "page": 0, "rect": [60, 30, 150, 45]}, "author": "human",
    })  # fmt: skip
    assert status == 200
    before = applied["template"]
    job_id = start(base, kind="revise", doc=doc, template=before, instruction="Add a field for notes", region={"page": 0, "rect": [50, 70, 160, 100]})
    body = wait_ended(base, job_id)
    assert body["status"] == "done" and body["summary"] == "added a note field"
    revised = Template.model_validate(body["template"])
    assert len(revised.fields) == 2
    new_history = revised.history[len(before["history"]) :]
    assert new_history and all(h.author == "agent" for h in new_history)
    assert [h.author for h in revised.history[: len(before["history"])]] == ["human"]
    first_message = factory.providers[0].calls[0][1].content
    assert "Add a field for notes" in first_message and "region" in first_message.lower()


def test_a_revise_job_works_without_a_region(start_demo_server):
    base = start_demo_server(provider_factory=Factory([finish("no change needed")]))
    doc = open_doc(base)
    job_id = start(base, kind="revise", doc=doc, template=seed(base, doc), instruction="Check the name field")
    assert wait_ended(base, job_id)["status"] == "done"


# ------------------------------------------------------------------ cancel


def test_cancel_during_step_2_ends_cancelled_with_the_partial_template_including_that_step(start_demo_server):
    gate = Gate()
    factory = Factory([code(ADD_NAME), gate, code(ADD_NOTE), code("print('never reached')"), finish()])
    base = start_demo_server(provider_factory=factory)
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    assert gate.reached.wait(10)  # the provider is inside the model call for step 2
    wait_steps(base, job_id, 1)
    status, body = post(base, f"/api/jobs/{job_id}/cancel")
    assert status == 200 and body["status"] == "running"  # takes effect when the call returns
    gate.release()
    body = wait_ended(base, job_id)
    assert body["status"] == "cancelled" and body["error"] is None
    assert [s["step"] for s in body["steps"]] == [1, 2]  # the step in flight is recorded; none after it
    template = Template.model_validate(body["template"])
    assert sorted(f.label for f in template.fields) == ["Name", "Note"]  # the in-flight step's ops are kept
    assert factory.providers[0].closed
    assert len(factory.providers[0].script) == 2  # the model was not asked again


def test_a_cancel_that_arrives_as_the_final_step_lands_still_reports_done(start_demo_server):
    gate = Gate()
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, finish("finished anyway")]))
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    assert gate.reached.wait(10)
    post(base, f"/api/jobs/{job_id}/cancel")
    gate.release()
    body = wait_ended(base, job_id)
    assert body["status"] == "done" and body["summary"] == "finished anyway" and body["steps"][-1]["finished"] is True


def test_cancelling_a_finished_job_changes_nothing(start_demo_server):
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), finish()]))
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    done = wait_ended(base, job_id)
    status, body = post(base, f"/api/jobs/{job_id}/cancel")
    assert status == 200 and body == done == job(base, job_id)


# ------------------------------------------------------------------ errors


def test_a_provider_error_ends_in_error_with_a_plain_message_the_partial_template_and_no_secret(start_demo_server):
    boom = RuntimeError(f"401 Unauthorized for key {SENTINEL_KEY}")
    factory = Factory([code(ADD_NAME), boom])
    base = start_demo_server(provider_factory=factory, llm=LLMConfig(base_url="https://llm.example.com/v1", api_key=SENTINEL_KEY, model="m"))
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    body = wait_ended(base, job_id)
    assert body["status"] == "error"
    assert "model request failed" in body["error"] and "RuntimeError" in body["error"]
    assert SENTINEL_KEY not in json.dumps(body) and "Traceback" not in body["error"]
    template = Template.model_validate(body["template"])  # what the agent had done before the failure
    assert [f.label for f in template.fields] == ["Name"]
    assert factory.providers[0].closed


def test_running_out_of_budget_is_an_error_with_the_partial_template(start_demo_server):
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME)] + [chatter()] * 39))
    job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
    body = wait_ended(base, job_id)
    assert body["status"] == "error" and "budget" in body["error"]
    assert len(body["steps"]) == 40
    assert [f.label for f in Template.model_validate(body["template"]).fields] == ["Name"]


def test_an_unexpected_exception_gives_a_generic_message_and_logs_the_traceback_without_the_key(start_demo_server, caplog):
    def broken():
        raise RuntimeError(f"cannot reach the endpoint with {SENTINEL_KEY}")

    base = start_demo_server(provider_factory=broken, llm=LLMConfig(base_url="https://llm.example.com/v1", api_key=SENTINEL_KEY, model="m"))
    with caplog.at_level(logging.INFO, logger="demo.server"):
        job_id = start(base, kind="build", doc=open_doc(base), schema=SCHEMA)
        body = wait_ended(base, job_id)
    assert body["status"] == "error" and body["template"] is None
    assert body["error"] == "The job stopped because of an unexpected error. The server log has the details."
    assert SENTINEL_KEY not in json.dumps(body)
    assert "Traceback" in caplog.text and "RuntimeError" in caplog.text
    assert SENTINEL_KEY not in caplog.text


def test_a_job_whose_form_map_cannot_be_built_ends_in_error(start_demo_server, monkeypatch):
    base = start_demo_server(provider_factory=Factory([finish()]))
    doc = open_doc(base)

    def fail(pdf):
        raise OSError("the detection weights are not available")

    monkeypatch.setattr(documents_mod, "build_form_map", fail)
    body = wait_ended(base, start(base, kind="build", doc=doc, schema=SCHEMA))
    assert body["status"] == "error" and body["template"] is None and "unexpected" in body["error"]


# ------------------------------------------------------------------ not configured, validation, limits


def test_ai_not_configured_is_a_400(demo_server):
    doc = open_doc(demo_server)
    status, body = post(demo_server, "/api/jobs", {"kind": "build", "doc": doc, "schema": SCHEMA})
    assert status == 400 and body["error"] == "ai_not_configured"


@pytest.fixture
def ai(start_demo_server):
    factory = Factory(*[[Gate(), finish()] for _ in range(8)])  # never run: these requests are all refused
    base = start_demo_server(provider_factory=factory)
    doc = open_doc(base)
    return base, doc, seed(base, doc), factory


@pytest.mark.parametrize(
    "body, code",
    [
        ({"kind": "teleport"}, "bad_kind"),
        ({}, "bad_kind"),
        ({"kind": "build"}, "bad_request"),  # no doc
        ({"kind": "build", "doc": 5}, "bad_request"),
        ({"kind": "build", "doc": "ab" * 16}, "not_found"),
        ({"kind": "adapt"}, "bad_request"),
        ({"kind": "revise"}, "bad_request"),
    ],
)
def test_a_job_with_no_valid_document_is_refused_before_a_thread_starts(ai, body, code):
    base, doc, template, factory = ai
    status, out = post(base, "/api/jobs", body)
    assert status in (400, 404) and out["error"] == code, out
    assert factory.providers == []


@pytest.mark.parametrize(
    "extra, code",
    [
        ({}, "bad_schema"),
        ({"schema": 3}, "bad_schema"),
        ({"schema": "no-such-schema"}, "not_found"),
        ({"schema": {"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#/$defs/a"}}}}, "bad_schema"),
    ],
)
def test_build_validates_the_schema(ai, extra, code):
    base, doc, template, factory = ai
    status, out = post(base, "/api/jobs", {"kind": "build", "doc": doc, **extra})
    assert (status, out["error"]) == (404 if code == "not_found" else 400, code)
    assert factory.providers == []


@pytest.mark.parametrize("kind", ["adapt", "revise"])
def test_adapt_and_revise_need_a_valid_template(ai, kind):
    base, doc, template, factory = ai
    extra = {"instruction": "do it"} if kind == "revise" else {}
    assert post(base, "/api/jobs", {"kind": kind, "doc": doc, **extra})[1]["error"] == "bad_template"
    assert post(base, "/api/jobs", {"kind": kind, "doc": doc, "template": {"x": 1}, **extra})[1]["error"] == "bad_template"
    assert factory.providers == []


@pytest.mark.parametrize("instruction", [None, "", "   ", 7, ["do it"]])
def test_revise_needs_a_non_empty_instruction(ai, instruction):
    base, doc, template, factory = ai
    status, out = post(base, "/api/jobs", {"kind": "revise", "doc": doc, "template": template, "instruction": instruction})
    assert (status, out["error"]) == (400, "bad_instruction")
    assert factory.providers == []


@pytest.mark.parametrize(
    "region",
    [
        "page 1",
        {},
        {"page": 0},
        {"rect": [0, 0, 10, 10]},
        {"page": 1, "rect": [0, 0, 10, 10]},  # the document has one page
        {"page": -1, "rect": [0, 0, 10, 10]},
        {"page": True, "rect": [0, 0, 10, 10]},
        {"page": "0", "rect": [0, 0, 10, 10]},
        {"page": 0, "rect": [0, 0, 10]},
        {"page": 0, "rect": [0, 0, 10, 10, 10]},
        {"page": 0, "rect": ["0", "0", "10", "10"]},
        {"page": 0, "rect": [0, 0, True, 10]},
        {"page": 0, "rect": [0, 0, None, 10]},
        {"page": 0, "rect": "0,0,10,10"},
    ],
)
def test_revise_validates_the_region(ai, region):
    base, doc, template, factory = ai
    status, out = post(base, "/api/jobs", {"kind": "revise", "doc": doc, "template": template, "instruction": "x", "region": region})
    assert (status, out["error"]) == (400, "bad_region"), out
    assert factory.providers == []


def test_a_huge_or_non_finite_region_number_is_a_400_not_a_500(ai):
    base, doc, template, factory = ai
    for raw in ('[0, 0, 1e999, 10]', '[0, 0, NaN, 10]', "[0, 0, " + "9" * 400 + ", 10]"):
        body = ('{"kind": "revise", "doc": "%s", "instruction": "x", "template": %s, "region": {"page": 0, "rect": %s}}' % (doc, json.dumps(template), raw)).encode()
        status, _, out = http_request(base, "POST", "/api/jobs", body, {"Content-Type": "application/json"})
        assert (status, json.loads(out)["error"]) == (400, "bad_region")


def test_a_non_json_or_non_object_body_is_a_400(ai):
    base, doc, template, factory = ai
    assert http_request(base, "POST", "/api/jobs", b"{nope", {"Content-Type": "application/json"})[0] == 400
    assert post(base, "/api/jobs", [1])[0] == 400  # type: ignore[arg-type]


def test_unknown_jobs_are_404(demo_server):
    for path in (f"/api/jobs/{'0' * 32}", f"/api/jobs/{'0' * 32}/cancel"):
        method = "POST" if path.endswith("cancel") else "GET"
        status, body = get_json(demo_server, path, method, {} if method == "POST" else None)
        assert (status, body["error"]) == (404, "not_found")


def test_a_third_concurrent_job_is_429_busy_and_a_finished_job_frees_its_slot(start_demo_server):
    gates = [Gate(), Gate()]
    base = start_demo_server(provider_factory=Factory([gates[0], finish("a", [{"path": "name", "reason": "t"}])],
                                                      [gates[1], finish("b", [{"path": "name", "reason": "t"}])],
                                                      [finish("c", [{"path": "name", "reason": "t"}])]))
    doc = open_doc(base)
    first = start(base, kind="build", doc=doc, schema=SCHEMA)
    second = start(base, kind="build", doc=doc, schema=SCHEMA)
    for gate in gates:
        assert gate.reached.wait(10)
    status, body = post(base, "/api/jobs", {"kind": "build", "doc": doc, "schema": SCHEMA})
    assert status == 429 and body["error"] == "busy" and "wait" in body["detail"]
    gates[0].release()
    assert wait_ended(base, first)["status"] == "done"
    third = start(base, kind="build", doc=doc, schema=SCHEMA)  # a slot is free again
    assert wait_ended(base, third)["status"] == "done"
    gates[1].release()
    assert wait_ended(base, second)["status"] == "done"


def test_finished_jobs_are_kept_up_to_a_limit_and_the_oldest_are_forgotten(start_demo_server):
    scripts = [[finish(str(i), [{"path": "name", "reason": "t"}])] for i in range(4)]
    base = start_demo_server(provider_factory=Factory(*scripts))
    api = start_demo_server.servers[-1].api
    api.jobs.keep_finished = 2
    doc = open_doc(base)
    ids = []
    for _ in range(4):
        ids.append(start(base, kind="build", doc=doc, schema=SCHEMA))
        wait_ended(base, ids[-1])
    assert [get_json(base, f"/api/jobs/{i}")[0] for i in ids] == [404, 404, 200, 200]


def test_jobs_unit_start_checks_its_own_arguments_and_the_limit():
    class FakeDoc:
        pass

    jobs = Jobs(max_running=1)
    with pytest.raises(ValueError):
        jobs.start("build", doc=FakeDoc(), provider_factory=lambda: None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        jobs.start("revise", doc=FakeDoc(), template=object(), provider_factory=lambda: None)  # type: ignore[arg-type]
    assert issubclass(JobsBusy, Exception)


# ------------------------------------------------------------------ other requests stay responsive


def test_apply_and_page_images_answer_promptly_while_a_job_is_running(start_demo_server):
    gate = Gate()
    base = start_demo_server(provider_factory=Factory([code(ADD_NAME), gate, finish()]))
    doc = open_doc(base)
    template = seed(base, doc)
    job_id = start(base, kind="build", doc=doc, schema=SCHEMA)
    assert gate.reached.wait(10)
    op = {"op": "add_field", "type": "text", "page": 0, "rect": [60, 30, 150, 45]}
    try:
        t0 = time.monotonic()
        status, body = post(base, "/api/apply", {"template": template, "op": op, "author": "human", "doc": doc})
        apply_s = time.monotonic() - t0
        assert status == 200, body
        t0 = time.monotonic()
        status, headers, png = http_request(base, "GET", f"/api/documents/{doc}/pages/0.png?dpi=72")
        image_s = time.monotonic() - t0
        assert status == 200 and png[:4] == b"\x89PNG"
        t0 = time.monotonic()
        assert get_json(base, f"/api/jobs/{job_id}")[1]["status"] == "running"
        poll_s = time.monotonic() - t0
        assert max(apply_s, image_s, poll_s) < 1.0, (apply_s, image_s, poll_s)
    finally:
        gate.release()
    assert wait_ended(base, job_id)["status"] == "done"
