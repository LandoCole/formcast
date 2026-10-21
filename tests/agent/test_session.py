import copy
import json

import pytest

from formcast.agent import api as agent_api
from formcast.agent import session as session_mod
from formcast.agent.api import build_template, revise
from formcast.agent.prompts import NO_TOOL_CALL
from formcast.agent.session import Session, SessionBudget
from formcast.errors import FormcastError
from formcast.geometry import points_to_norm
from formcast.llm import LLMConfig, LLMResponse, ToolCall, Usage
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import Candidate, FormMap, PageMap, TextLine, Word
from formcast.sandbox import Sandbox

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full name"},
        "date": {"type": "string", "format": "date"},
    },
}
NAME_LINE = (72.0, 88.0, 105.0, 101.0)
NAME_BOX = (120.0, 86.0, 300.0, 100.0)
DATE_BOX = (72.0, 204.0, 250.0, 212.0)
ADD_NAME = (
    'fid = add_field(type="text", page=0, rect=[120, 86, 300, 100], label="Name")\n'
    'set_source(fid, source={"paths": ["name"]})\n'
    "print('added', fid)\n"
)


# finish() for build_template runs, which check that unmapped paths are accounted for
NONE_PLACEABLE = [{"path": "name", "reason": "test"}, {"path": "date", "reason": "test"}]


def _form_map() -> FormMap:
    page = PageMap(
        index=0,
        width=612,
        height=792,
        rotation=0,
        kind="digital",
        text_source="pdf",
        words=[Word(text="Name:", rect=NAME_LINE, line_id=0, source="pdf")],
        lines=[TextLine(id=0, text="Name:", rect=NAME_LINE, word_ids=[0], font_size=12)],
        rules=[],
        widgets=[],
        candidates=[
            Candidate(
                id="c0_0",
                kind="text",
                rect=NAME_BOX,
                conf=0.9,
                detector="fake",
                label_line_id=0,
                label_relation="left",
            ),
            Candidate(id="c0_1", kind="text", rect=DATE_BOX, conf=0.8, detector="fake"),
        ],
    )
    return FormMap(pages=[page], fingerprint="fp", source_name="test.pdf")


# --- scripted provider ------------------------------------------------------


def _usage() -> Usage:
    return Usage(input_tokens=100, output_tokens=10)


def tool_reply(
    name: str, args: dict | None, *, raw: str | None = None, call_id: str | None = None
) -> LLMResponse:
    raw_args = raw if raw is not None else json.dumps(args)
    return LLMResponse(
        text="",
        tool_calls=[
            ToolCall(id=call_id or f"call_{name}", name=name, arguments=args, raw_arguments=raw_args)
        ],
        usage=_usage(),
        raw={},
    )


def code(src: str, call_id: str | None = None) -> LLMResponse:
    return tool_reply("run_python", {"code": src}, call_id=call_id)


def finish(summary: str = "done", unplaceable=()) -> LLMResponse:
    return tool_reply("finish", {"summary": summary, "unplaceable": list(unplaceable)})


def text_reply(text: str, raw: dict | None = None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], usage=_usage(), raw=raw or {})


class ScriptedProvider:
    """Returns queued responses in order and records the messages of every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list] = []
        self.tools = None
        self.closed = False

    def complete(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        self.tools = tools
        if not self.responses:
            raise AssertionError("scripted provider ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def _tool_results(messages) -> list:
    return [m for m in messages if m.role == "tool"]


def _run(responses, **kwargs):
    """Loop-mechanics runs start from an empty template (no seeded candidate fields)."""
    provider = ScriptedProvider(responses)
    kwargs.setdefault("seed_candidates", False)
    kwargs.setdefault("unaccounted_refusals", 0)
    result = Session(_form_map(), SCHEMA, provider, **kwargs).run()
    return result, provider


# --- the happy path -----------------------------------------------------------


def test_add_field_set_source_then_finish():
    result, provider = _run(
        [code(ADD_NAME), finish("all mapped", [{"path": "date", "reason": "no date blank"}])]
    )
    assert result.finished is True
    assert result.stop_reason == "finish"
    assert result.summary == "all mapped"
    assert result.unplaceable == [{"path": "date", "reason": "no date blank"}]
    fields = result.template.fields
    assert len(fields) == 1
    assert fields[0].source.paths == ["name"]
    assert result.template.history and all(h.author == "agent" for h in result.template.history)
    assert len(result.steps) == 2
    assert [op["op"] for op in result.steps[0].applied] == ["add_field", "set_source"]
    assert result.usage.input_tokens == 200 and result.usage.output_tokens == 20
    # the model was given exactly the two tools
    assert sorted(t.name for t in provider.tools) == ["finish", "run_python"]


def test_first_messages_are_system_prompt_and_form_summary():
    from formcast.agent.prompts import SYSTEM_PROMPT

    _, provider = _run([finish()])
    first_call = provider.calls[0]
    assert [m.role for m in first_call] == ["system", "user"]
    assert first_call[0].content == SYSTEM_PROMPT
    assert "name" in first_call[1].content and "c0_0" in first_call[1].content


def test_step_report_goes_back_as_tool_result():
    _, provider = _run([code(ADD_NAME, call_id="abc"), finish()])
    second = provider.calls[1]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].id == "abc"
    report = second[-1]
    assert report.role == "tool" and report.tool_call_id == "abc"
    assert "added f1" in report.content
    assert "add_field" in report.content and "set_source" in report.content
    assert "date" in report.content  # still unmapped
    assert "c0_1" in report.content  # unused candidate


# --- malformed replies ----------------------------------------------------------


def test_plain_text_reply_gets_corrective_message_and_run_continues():
    result, provider = _run([text_reply("I will now look at the form."), code(ADD_NAME), finish()])
    assert result.stop_reason == "finish"
    assert len(result.steps) == 3
    second = provider.calls[1]
    assert second[-1].role == "user"
    assert second[-1].content.startswith(NO_TOOL_CALL)
    assert second[-2].role == "assistant" and second[-2].content == "I will now look at the form."
    assert len(result.template.fields) == 1


def test_truncated_reply_mentions_the_token_limit():
    raw = {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
    _, provider = _run([text_reply("partial", raw=raw), finish()])
    corrective = provider.calls[1][-1].content
    assert corrective.startswith(NO_TOOL_CALL)
    assert "token limit" in corrective


def test_malformed_tool_arguments_get_an_error_tool_result():
    bad = tool_reply("run_python", None, raw='{"code": "print(1)', call_id="bad1")
    result, provider = _run([bad, finish()])
    assert result.stop_reason == "finish"
    tool = _tool_results(provider.calls[1])[-1]
    assert tool.tool_call_id == "bad1"
    assert "not a valid JSON object" in tool.content
    assert len(result.steps) == 2


def test_unknown_tool_gets_an_error_tool_result():
    result, provider = _run([tool_reply("browse", {"url": "x"}, call_id="u1"), finish()])
    tool = _tool_results(provider.calls[1])[-1]
    assert tool.tool_call_id == "u1"
    assert "unknown tool 'browse'" in tool.content
    assert result.stop_reason == "finish"


def test_run_python_without_code_string_is_an_error_tool_result():
    _, provider = _run([tool_reply("run_python", {"src": "x"}, call_id="n1"), finish()])
    tool = _tool_results(provider.calls[1])[-1]
    assert tool.tool_call_id == "n1" and "code" in tool.content


def test_every_tool_call_in_a_reply_gets_a_result():
    reply = LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="a", name="run_python", arguments={"code": "x = 1"}, raw_arguments='{"code": "x = 1"}'
            ),
            ToolCall(
                id="b", name="run_python", arguments={"code": "y = 2"}, raw_arguments='{"code": "y = 2"}'
            ),
        ],
        usage=_usage(),
        raw={},
    )
    _, provider = _run([reply, finish()])
    results = {m.tool_call_id: m.content for m in _tool_results(provider.calls[1])}
    assert set(results) == {"a", "b"}
    assert "one run_python call per reply" in results["b"]


def test_finish_alongside_run_python_runs_the_code_then_finishes():
    reply = LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="a",
                name="run_python",
                arguments={"code": ADD_NAME},
                raw_arguments=json.dumps({"code": ADD_NAME}),
            ),
            ToolCall(
                id="b", name="finish", arguments={"summary": "ok", "unplaceable": []}, raw_arguments="{}"
            ),
        ],
        usage=_usage(),
        raw={},
    )
    result, _ = _run([reply])
    assert result.stop_reason == "finish" and len(result.template.fields) == 1


def test_rejected_op_appears_in_next_step_report_with_reason():
    src = 'add_field(type="text", page=5, rect=[10, 10, 100, 30], label="X")\n'
    result, provider = _run([code(src), finish()])
    assert result.steps[0].rejected and "page 5 does not exist" in result.steps[0].rejected[0]["reason"]
    report = _tool_results(provider.calls[1])[-1].content
    assert "rejected" in report.lower()
    assert "page 5 does not exist" in report
    assert result.template.fields == []


def test_ops_recorded_before_an_exec_error_are_still_applied():
    src = ADD_NAME + "raise ValueError('boom')\n"
    result, provider = _run([code(src), finish()])
    assert len(result.template.fields) == 1
    assert "boom" in result.steps[0].exec.error
    assert "ValueError: boom" in _tool_results(provider.calls[1])[-1].content


def test_sandbox_sees_the_updated_template_next_step():
    src2 = "print('fields:', [f['id'] for f in tpl['fields']])"
    _, provider = _run([code(ADD_NAME), code(src2), finish()])
    assert "fields: ['f1']" in _tool_results(provider.calls[2])[-1].content


# --- budget and errors ---------------------------------------------------------


def test_step_budget_stops_with_partial_template():
    responses = [
        code(ADD_NAME),
        code('add_field(type="text", page=0, rect=[72, 204, 250, 212])'),
        code("x = 1"),
    ]
    result, provider = _run(responses, budget=SessionBudget(max_steps=2))
    assert result.stop_reason == "budget"
    assert result.finished is False
    assert len(provider.calls) == 2
    assert len(result.steps) == 2
    assert len(result.template.fields) == 2


def test_token_budget_stops_the_run():
    result, provider = _run([code("x = 1"), code("y = 2")], budget=SessionBudget(max_input_tokens=150))
    assert result.stop_reason == "budget"
    assert len(provider.calls) == 2  # 100 tokens after one step is under 150, 200 after two is over


def test_provider_error_ends_with_error_and_template_so_far():
    result, _ = _run([code(ADD_NAME), FormcastError("HTTP 500 after retries")])
    assert result.stop_reason == "error"
    assert "HTTP 500" in result.error
    assert len(result.template.fields) == 1


# --- context management ---------------------------------------------------------


def test_old_steps_are_compressed_and_tool_pairs_stay_valid():
    responses = [code(f"v{i} = {i}", call_id=f"c{i}") for i in range(8)] + [finish()]
    responses[0] = code(ADD_NAME, call_id="c0")
    _, provider = _run(responses)
    last = provider.calls[-1]  # sent before step 9: steps 1-8 happened
    assert last[0].role == "system" and last[1].role == "user"
    compressed = last[2]
    assert compressed.role == "user"
    assert "step 1: 2 ops applied, 0 rejected" in compressed.content
    assert "step 2: 0 ops applied, 0 rejected" in compressed.content
    assert "step 3" not in compressed.content
    call_ids = [c.id for m in last if m.role == "assistant" for c in m.tool_calls]
    assert call_ids == ["c2", "c3", "c4", "c5", "c6", "c7"]  # the last 6 steps verbatim
    result_ids = [m.tool_call_id for m in last if m.role == "tool"]
    assert result_ids == call_ids  # no orphan calls or results
    # roles alternate validly after the first message
    assert last[3].role == "assistant"


# --- ownership -----------------------------------------------------------------


class RecordingSandbox(Sandbox):
    instances: list = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.closed = False
        RecordingSandbox.instances.append(self)

    def close(self):
        self.closed = True
        super().close()


def test_session_closes_sandbox_but_not_a_caller_provider():
    RecordingSandbox.instances = []
    result, provider = _run([code(ADD_NAME), finish()], sandbox_factory=RecordingSandbox)
    assert result.finished
    assert RecordingSandbox.instances and all(s.closed for s in RecordingSandbox.instances)
    assert provider.closed is False


def test_session_closes_provider_it_created_and_sandbox_on_exception(monkeypatch):
    RecordingSandbox.instances = []
    provider = ScriptedProvider([code(ADD_NAME)])

    def boom(self, template):
        raise KeyboardInterrupt

    monkeypatch.setattr(session_mod, "make_provider", lambda cfg: provider)
    monkeypatch.setattr(RecordingSandbox, "update_template", boom)
    cfg = LLMConfig(base_url="http://x", api_key="k", model="m")
    with pytest.raises(KeyboardInterrupt):
        Session(_form_map(), SCHEMA, cfg, sandbox_factory=RecordingSandbox).run()
    assert provider.closed is True
    assert all(s.closed for s in RecordingSandbox.instances)


# --- entry points ----------------------------------------------------------------


class FakeDetector:
    def __init__(self, name, boxes):
        self.name = name
        self.boxes = [(kind, points_to_norm(rect, (0, 0, 612, 792)), conf) for kind, rect, conf in boxes]

    def detect(self, image):
        return list(self.boxes)


def _flat_map(pdf):
    det = FakeDetector("FFDNet-L", [("text", NAME_BOX, 0.9), ("text", DATE_BOX, 0.85)])
    return build_form_map(pdf, detectors={"FFDNet-L": det})


def test_build_template_with_fake_detector_form_map(flat_form_pdf):
    fm = _flat_map(flat_form_pdf)
    provider = ScriptedProvider([code('set_source("c0_0", source={"paths": ["name"]})'), finish("d", NONE_PLACEABLE)])
    result = build_template(SCHEMA, flat_form_pdf, provider, form_map=fm)
    # the template starts with one field per candidate, named after it
    assert result.finished and [f.id for f in result.template.fields] == ["c0_0", "c0_1"]
    assert [f.origin for f in result.template.fields] == ["detector", "detector"]
    assert result.template.fields[0].source.paths == ["name"]
    assert "STARTING TEMPLATE: 2 unmapped field(s)" in provider.calls[0][1].content
    assert result.template.form.fingerprint == fm.fingerprint
    assert result.template.schema_ == SCHEMA


def test_build_template_builds_the_form_map_when_not_given(flat_form_pdf, monkeypatch):
    seen = {}

    def fake_build(pdf, **kwargs):
        seen["pdf"] = pdf
        return _flat_map(flat_form_pdf)

    monkeypatch.setattr(agent_api, "build_form_map", fake_build)
    result = build_template(SCHEMA, flat_form_pdf, ScriptedProvider([finish("d", NONE_PLACEABLE)]))
    assert seen["pdf"] == flat_form_pdf and result.finished


def test_revise_puts_instruction_and_region_in_first_message(flat_form_pdf):
    fm = _flat_map(flat_form_pdf)
    first = build_template(
        SCHEMA, flat_form_pdf, ScriptedProvider([code(ADD_NAME), finish("d", NONE_PLACEABLE)]), form_map=fm,
        seed_candidates=False,
    )
    base_len = len(first.template.history)
    provider = ScriptedProvider([code('move("f1", dy=2)'), finish("moved")])
    result = revise(
        first.template,
        flat_form_pdf,
        "The name box sits too high.",
        provider,
        region=(0, (110.0, 80.0, 310.5, 105.0)),
        form_map=fm,
    )
    msg = provider.calls[0][1].content
    assert "The name box sits too high." in msg
    assert "page 0" in msg and "[110, 80, 310.5, 105]" in msg
    assert "f1" in msg  # existing fields are described
    assert result.finished
    new = result.template.history[base_len:]
    assert [h.op["op"] for h in new] == ["move"] and all(h.author == "agent" for h in new)
    assert result.template.fields[0].rect == (120.0, 88.0, 300.0, 102.0)


def test_revise_default_budget_is_15_steps():
    import inspect

    assert inspect.signature(revise).parameters["budget"].default.max_steps == 15


def test_package_exports_the_public_api():
    import formcast
    from formcast.agent.api import build_template as bt
    from formcast.render.preview import preview as pv

    for name in (
        "LLMConfig",
        "build_form_map",
        "build_template",
        "revise",
        "preview",
        "apply",
        "fingerprint",
        "Template",
    ):
        assert name in formcast.__all__ and getattr(formcast, name) is not None
    assert formcast.build_template is bt and formcast.preview is pv


def test_printing_api_reaches_the_model_in_full():
    _, provider = _run([code("print(api())"), finish()])
    report = _tool_results(provider.calls[1])[-1].content
    assert "suffix(" in report  # the last line of the listing
    assert "truncated" not in report


def test_fillable_form_widget_fields_are_named_after_the_widgets_in_the_first_message(fillable_form_pdf):
    fm = build_form_map(fillable_form_pdf, detectors={})
    provider = ScriptedProvider([finish("d", NONE_PLACEABLE)])
    result = build_template(SCHEMA, fillable_form_pdf, provider, form_map=fm)
    assert result.finished
    msg = provider.calls[0][1].content
    assert "STARTING TEMPLATE: 2 unmapped field(s), 2 for the existing widgets" in msg
    ids = [f.id for f in result.template.fields]
    assert ids == ["w0_0", "w0_1"] and all(f"{i} " in msg for i in ids)  # the summary lists the same ids


def _code_and_finish(src: str) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="a", name="run_python", arguments={"code": src}, raw_arguments=json.dumps({"code": src})
            ),
            ToolCall(
                id="b", name="finish", arguments={"summary": "ok", "unplaceable": []}, raw_arguments="{}"
            ),
        ],
        usage=_usage(),
        raw={},
    )


def test_finish_is_refused_when_the_same_step_had_rejected_ops():
    bad = 'add_field(type="text", page=5, rect=[10, 10, 100, 30])'
    result, provider = _run([_code_and_finish(bad), code(ADD_NAME), finish("fixed")])
    results = {m.tool_call_id: m.content for m in _tool_results(provider.calls[1])}
    assert results["b"].startswith("finish not accepted")
    assert "page 5 does not exist" in results["a"]
    assert result.stop_reason == "finish" and result.summary == "fixed"
    assert len(result.steps) == 3 and len(result.template.fields) == 1


def test_finish_is_refused_when_the_same_step_raised():
    result, provider = _run([_code_and_finish(ADD_NAME + "1 / 0\n"), finish("now done")])
    results = {m.tool_call_id: m.content for m in _tool_results(provider.calls[1])}
    assert results["b"].startswith("finish not accepted")
    assert "ZeroDivisionError" in results["a"]
    assert result.summary == "now done" and len(result.steps) == 2


def test_truncated_tool_arguments_mention_the_token_limit():
    bad = tool_reply("run_python", None, raw='{"code": "for i in range(', call_id="t1")
    bad.raw = {"choices": [{"finish_reason": "length"}]}
    _, provider = _run([bad, finish()])
    tool = _tool_results(provider.calls[1])[-1]
    assert "not a valid JSON object" in tool.content and "token limit" in tool.content


def test_malformed_arguments_without_truncation_do_not_mention_the_token_limit():
    _, provider = _run([tool_reply("run_python", None, raw="{x", call_id="t2"), finish()])
    assert "token limit" not in _tool_results(provider.calls[1])[-1].content


def test_revise_region_lists_overlapping_template_fields(flat_form_pdf):
    fm = _flat_map(flat_form_pdf)
    src = ADD_NAME + 'add_field(type="text", page=0, rect=[72, 204, 250, 212], label="When")\n'
    first = build_template(
        SCHEMA, flat_form_pdf, ScriptedProvider([code(src), finish("d", NONE_PLACEABLE)]), form_map=fm,
        seed_candidates=False,
    )
    provider = ScriptedProvider([finish()])
    revise(
        first.template,
        flat_form_pdf,
        "Check the box.",
        provider,
        region=(0, (100, 80, 320, 110)),
        form_map=fm,
    )
    msg = provider.calls[0][1].content
    region_part = msg.split("TEMPLATE FIELDS IN THE REGION (1):", 1)[1].split("\n\n", 1)[0]
    assert "f1 text page 0 [120, 86, 300, 100] label='Name' source=name" in region_part
    assert "f2" not in region_part
    assert "lists the text lines, rules, candidates and widgets" in msg


def test_only_the_latest_steps_keep_their_full_output():
    big = "print('x' * 5000)"
    responses = [code(big, call_id=f"c{i}") for i in range(4)] + [finish()]
    _, provider = _run(responses)
    last = provider.calls[-1]  # sent before step 5: steps 1-4 happened, all in the verbatim window
    results = [m.content for m in last if m.role == "tool"]
    assert len(results) == 4
    full = [r for r in results if "x" * 5000 in r]
    assert len(full) == session_mod.FULL_OUTPUT_STEPS
    for r in results[: 4 - session_mod.FULL_OUTPUT_STEPS]:
        assert "x" * session_mod.OLDER_STDOUT in r and "x" * (session_mod.OLDER_STDOUT + 1) not in r
        assert "older step: output shortened" in r


def test_first_message_carries_the_api_reference():
    from formcast.sandbox.worker import api_text

    _, provider = _run([finish()])
    msg = provider.calls[0][1].content
    assert api_text() in msg


# --- finishing with unaccounted paths -----------------------------------------------


def test_finish_is_refused_once_while_unmapped_paths_are_not_listed_as_unplaceable():
    result, provider = _run([code(ADD_NAME), finish("early"), finish("really done")], unaccounted_refusals=1)
    refusal = _tool_results(provider.calls[2])[-1].content
    assert refusal.startswith("finish not accepted yet") and "date" in refusal and "steps left" in refusal
    assert result.stop_reason == "finish" and result.summary == "really done" and len(result.steps) == 3


def test_finish_listing_every_unmapped_path_is_accepted_at_once():
    result, _ = _run(
        [code(ADD_NAME), finish("done", [{"path": "date", "reason": "no date blank"}])], unaccounted_refusals=1
    )
    assert result.stop_reason == "finish" and len(result.steps) == 2


def test_the_default_session_checks_unaccounted_paths_once():
    import inspect

    assert inspect.signature(Session).parameters["unaccounted_refusals"].default == 1
