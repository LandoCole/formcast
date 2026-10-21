from datetime import datetime, timezone

from rlm_lab.execution.events import EventRecorder, ExperimentTrace


def test_trace_models_round_trip() -> None:
    recorder = EventRecorder()
    recorder.emit("run.started", summary="start", payload={"prompt": "hello"})
    trace = ExperimentTrace(
        run_id="test-run",
        created_at=datetime.now(timezone.utc),
        prompt="hello",
        runner_mode="baseline",
        model="openai/test",
        events=recorder.events,
    )

    payload = trace.model_dump(mode="json")
    reloaded = ExperimentTrace.model_validate(payload)
    assert reloaded.run_id == "test-run"
    assert reloaded.events[0].kind == "run.started"
