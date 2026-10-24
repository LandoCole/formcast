"""Progress events and cancel for agent sessions (spec 9.3)."""

import dataclasses

import pytest
from test_session import (  # shared scripted-provider helpers (tests/agent has no package)
    ADD_NAME,
    NONE_PLACEABLE,
    SCHEMA,
    RecordingSandbox,
    ScriptedProvider,
    _form_map,
    code,
    finish,
)

import formcast
from formcast.agent.api import build_template
from formcast.agent.session import Session, StepEvent
from formcast.errors import FormcastError, SessionCancelled

NOOP = "print('step')\n"


def _session(responses, *, on_step=None, **kwargs) -> tuple[Session, ScriptedProvider]:
    provider = ScriptedProvider(responses)
    kwargs.setdefault("seed_candidates", False)
    kwargs.setdefault("unaccounted_refusals", 0)
    return Session(_form_map(), SCHEMA, provider, on_step=on_step, **kwargs), provider


def _three_steps_then_finish():
    return [code(ADD_NAME), code(NOOP), code(NOOP), finish("all done", NONE_PLACEABLE)]


def test_on_step_gets_one_event_per_reply_and_the_last_is_finished():
    events: list[StepEvent] = []
    session, _ = _session(_three_steps_then_finish(), on_step=events.append)
    result = session.run()
    assert result.finished
    assert [e.step for e in events] == [1, 2, 3, 4]
    assert [e.finished for e in events] == [False, False, False, True]
    assert [e.summary for e in events] == ["", "", "", "all done"]
    # step 1 added a field and set its source: two ops applied, none rejected
    assert (events[0].ops_applied, events[0].ops_rejected) == (2, 0)
    assert (events[1].ops_applied, events[3].ops_applied) == (0, 0)
    # coverage counts: "name" is mapped (and covers its candidate); "date" and its candidate are not
    assert events[0].unmapped_paths == 1
    assert events[0].unfilled_blanks == 1
    assert events[-1].unmapped_paths == 1
    # token usage so far: 100 in / 10 out per scripted reply, never decreasing
    assert [e.input_tokens for e in events] == [100, 200, 300, 400]
    assert [e.output_tokens for e in events] == [10, 20, 30, 40]
    assert isinstance(events[0], StepEvent)
    with pytest.raises(dataclasses.FrozenInstanceError):
        events[0].step = 9  # type: ignore[misc]


def test_a_refused_finish_is_not_reported_as_finished():
    events: list[StepEvent] = []
    session, _ = _session(
        [finish("too early"), finish("now", NONE_PLACEABLE)], on_step=events.append, unaccounted_refusals=1
    )
    result = session.run()
    assert result.finished and result.summary == "now"
    assert [(e.step, e.finished, e.summary) for e in events] == [(1, False, ""), (2, True, "now")]


def test_cancel_from_on_step_ends_cleanly_with_the_working_template():
    RecordingSandbox.instances = []

    def on_step(event: StepEvent) -> None:
        if event.step == 2:
            raise SessionCancelled("stop")

    session, provider = _session(_three_steps_then_finish(), on_step=on_step, sandbox_factory=RecordingSandbox)
    result = session.run()
    assert result.finished is False
    assert result.summary == "cancelled"
    assert result.stop_reason == "cancelled"
    assert result.error is None
    assert len(result.steps) == 2
    assert len(provider.calls) == 2  # no further model calls after the cancel
    # step 1's ops are in the template and its history
    assert [f.source.paths for f in result.template.fields] == [["name"]]
    assert [h.op["op"] for h in result.template.history] == ["add_field", "set_source"]
    # the sandbox is closed and its worker process is gone
    (sandbox,) = RecordingSandbox.instances
    assert sandbox.closed
    assert sandbox._proc is None


def test_cancel_closes_the_provider_the_session_created(monkeypatch):
    from formcast.agent import session as session_mod
    from formcast.llm import LLMConfig

    provider = ScriptedProvider([code(ADD_NAME)])
    monkeypatch.setattr(session_mod, "make_provider", lambda cfg: provider)

    def on_step(event: StepEvent) -> None:
        raise SessionCancelled

    cfg = LLMConfig(base_url="http://x", api_key="k", model="m")
    result = Session(_form_map(), SCHEMA, cfg, on_step=on_step, seed_candidates=False).run()
    assert result.summary == "cancelled" and not result.finished
    assert provider.closed


def test_other_exceptions_from_on_step_propagate_after_the_sandbox_closes():
    RecordingSandbox.instances = []

    def on_step(event: StepEvent) -> None:
        raise ValueError("callback bug")

    session, _ = _session([code(ADD_NAME), finish()], on_step=on_step, sandbox_factory=RecordingSandbox)
    with pytest.raises(ValueError, match="callback bug"):
        session.run()
    (sandbox,) = RecordingSandbox.instances
    assert sandbox.closed and sandbox._proc is None


def test_a_formcast_error_from_on_step_is_not_mistaken_for_a_sandbox_failure():
    def on_step(event: StepEvent) -> None:
        raise FormcastError("not a cancel")

    session, _ = _session([code(ADD_NAME), finish()], on_step=on_step)
    with pytest.raises(FormcastError, match="not a cancel"):
        session.run()


def test_without_on_step_the_result_is_identical():
    events: list[StepEvent] = []
    plain, _ = _session(_three_steps_then_finish())
    watched, _ = _session(_three_steps_then_finish(), on_step=events.append)
    a, b = plain.run(), watched.run()
    assert len(events) == 4
    assert (a.finished, a.summary, a.stop_reason, a.unplaceable) == (
        b.finished, b.summary, b.stop_reason, b.unplaceable
    )
    assert a.usage == b.usage and len(a.steps) == len(b.steps)
    ta, tb = a.template, b.template
    assert (ta.formcast_version, ta.schema_, ta.form) == (tb.formcast_version, tb.schema_, tb.form)
    assert len(ta.fields) == len(tb.fields) and ta.fields
    for fa, fb in zip(ta.fields, tb.fields, strict=True):
        assert fa.model_dump() == fb.model_dump()
    assert len(ta.history) == len(tb.history)
    for ha, hb in zip(ta.history, tb.history, strict=True):
        assert (ha.op, ha.author, ha.note) == (hb.op, hb.author, hb.note)


def test_build_template_and_revise_pass_on_step_through(monkeypatch):
    events: list[StepEvent] = []
    provider = ScriptedProvider([code(ADD_NAME), finish("ok", NONE_PLACEABLE)])
    result = build_template(
        SCHEMA, b"unused", provider, form_map=_form_map(), seed_candidates=False, on_step=events.append,
        vision=False,
    )
    assert result.finished and [e.step for e in events] == [1, 2]

    revise_events: list[StepEvent] = []
    provider = ScriptedProvider([code(NOOP), finish("revised")])
    revised = formcast.revise(
        result.template, b"unused", "tidy", provider, form_map=_form_map(), on_step=revise_events.append,
        vision=False,
    )
    assert revised.finished and [e.step for e in revise_events] == [1, 2]


def test_public_exports():
    assert formcast.StepEvent is StepEvent
    assert formcast.SessionCancelled is SessionCancelled
    assert issubclass(SessionCancelled, FormcastError)
    assert "StepEvent" in formcast.__all__ and "SessionCancelled" in formcast.__all__
