from pathlib import Path

from dspy.utils.dummies import DummyLM

from rlm_lab.execution.runners import LabRunner, RunOptions
from rlm_lab.execution.store import RunStore
from rlm_lab.modeling.config import ModelConfig
from rlm_lab.modeling.runtime import TracingLM


def make_factory():
    def factory(runner_name, recorder):
        if runner_name == "baseline":
            inner = DummyLM([{"answer": "Baseline answer from the dummy model."}])
        else:
            inner = DummyLM(
                [
                    {
                        "reasoning": "Inspect the corpus layout before answering.",
                        "code": "docs = list_documents(path_prefix='nested', file_type='md', limit=5)\nprint(docs)",
                    },
                    {
                        "reasoning": "Run filtered retrieval and inspect nearby context.",
                        "code": "matches = search_with_filters(query='filter aware retrieval', path_prefix='nested', file_type='md', top_k=2)\nprint(matches)\nchunk_info = get_document_chunks(path='nested/guide.md', limit=5)\nprint(chunk_info)\nsection = read_document_section(path='nested/guide.md', start_index=0, end_index=2)\nprint(section)",
                    },
                    {
                        "reasoning": "Return the final answer after retrieval.",
                        "code": "SUBMIT(answer='Recursive answer from the dummy model.')",
                    },
                ]
            )
        return TracingLM(inner=inner, recorder=recorder, runner_name=runner_name)

    return factory


def test_baseline_run_emits_trace(tmp_path: Path) -> None:
    runner = LabRunner(
        run_store=RunStore(tmp_path / "runs"),
        model_config=ModelConfig(api_key="test", model_name="dummy"),
        lm_factory=make_factory(),
    )

    trace = runner.run(RunOptions(prompt="Say hello.", runner_mode="baseline"))
    result = trace.results["baseline"]

    assert result.success is True
    assert "Baseline answer" in (result.answer or "")
    assert any(event.kind == "runner.answer" for event in trace.events)


def test_rlm_run_logs_iterations_and_retrieval(tmp_path: Path) -> None:
    runner = LabRunner(
        run_store=RunStore(tmp_path / "runs"),
        model_config=ModelConfig(api_key="test", model_name="dummy"),
        lm_factory=make_factory(),
    )

    trace = runner.run(
        RunOptions(
            prompt="Use the corpus.",
            runner_mode="rlm",
            corpus_dir=Path("tests/fixtures/corpus"),
            retrieval_top_k=2,
        )
    )

    result = trace.results["rlm"]
    kinds = [event.kind for event in trace.events]
    assert result.success is True
    assert "Recursive answer" in (result.answer or "")
    assert "rlm.iteration.started" in kinds
    assert "retrieval.tool" in kinds
    assert any("list_documents" in event.summary for event in trace.events if event.kind == "retrieval.tool")


def test_both_mode_returns_side_by_side_results(tmp_path: Path) -> None:
    runner = LabRunner(
        run_store=RunStore(tmp_path / "runs"),
        model_config=ModelConfig(api_key="test", model_name="dummy"),
        lm_factory=make_factory(),
    )

    trace = runner.run(
        RunOptions(
            prompt="Compare both runners.",
            runner_mode="both",
            corpus_dir=Path("tests/fixtures/corpus"),
        )
    )

    assert set(trace.results) == {"baseline", "rlm"}
    assert trace.results["baseline"].metrics.call_count >= 1
    assert trace.results["rlm"].metrics.call_count >= 1
