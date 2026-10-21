from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

os.environ.setdefault("DSPY_CACHEDIR", str(Path.cwd() / ".dspy_cache"))
(Path.cwd() / ".dspy_cache").mkdir(parents=True, exist_ok=True)

import dspy

from rlm_lab.execution.events import (
    EventRecorder,
    ExperimentTrace,
    LMCallRecord,
    RetrievalSnapshot,
    RunnerMetrics,
    RunnerTrace,
)
from rlm_lab.execution.interpreter import LocalPythonInterpreter
from rlm_lab.execution.store import RunStore
from rlm_lab.modeling.config import ModelConfig
from rlm_lab.modeling.runtime import build_tracing_lm
from rlm_lab.modeling.usage import extract_token_counts, merge_usage, normalize_usage, sanitize_for_json
from rlm_lab.retrieval.corpus import LocalCorpus


class AnswerTask(dspy.Signature):
    """Answer the user's prompt using the retrieved context when it helps. Be explicit when the context is insufficient."""

    question: str = dspy.InputField(desc="The user prompt or task.")
    retrieved_context: str = dspy.InputField(desc="Context retrieved from the local corpus.")
    corpus_overview: str = dspy.InputField(desc="High-level corpus description and available retrieval tools.")
    answer: str = dspy.OutputField(desc="The best answer for the user.")


def strip_code_fences(text: str) -> str:
    fenced = re.match(r"```(?:python)?\s*(.*?)```", text, flags=re.DOTALL)
    return fenced.group(1).strip() if fenced else text.strip()


class InstrumentedRLM(dspy.RLM):
    def __init__(self, *args: Any, recorder: EventRecorder, runner_name: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.recorder = recorder
        self.runner_name = runner_name

    def _execute_iteration(
        self,
        repl,
        variables,
        history,
        iteration,
        input_args,
        output_field_names,
    ):
        iteration_number = iteration + 1
        variables_info = [variable.format() for variable in variables]
        self.recorder.emit(
            "rlm.iteration.started",
            runner=self.runner_name,
            step=iteration_number,
            summary=f"Iteration {iteration_number}",
            payload={"variables_info": variables_info, "history_length": len(history)},
        )

        action = self.generate_action(
            variables_info=variables_info,
            repl_history=history,
            iteration=f"{iteration_number}/{self.max_iterations}",
        )
        code = strip_code_fences(action.code)
        self.recorder.emit(
            "rlm.iteration.planned",
            runner=self.runner_name,
            step=iteration_number,
            summary=action.reasoning[:120],
            payload={"reasoning": action.reasoning, "code": code},
        )

        try:
            result = repl.execute(code, variables=dict(input_args))
        except Exception as exc:
            result = f"[Error] {exc}"

        prediction_or_history = self._process_execution_result(action, result, history, output_field_names)
        completed = isinstance(prediction_or_history, dspy.Prediction)
        self.recorder.emit(
            "rlm.iteration.completed",
            runner=self.runner_name,
            step=iteration_number,
            summary="Final output" if completed else "Continuing recursion",
            payload={
                "completed": completed,
                "result_type": type(result).__name__,
                "history_length": len(getattr(prediction_or_history, "trajectory", prediction_or_history)),
            },
        )
        return prediction_or_history


@dataclass(slots=True)
class RunOptions:
    prompt: str
    runner_mode: str = "both"
    corpus_dir: Path | None = None
    retrieval_top_k: int = 4
    max_iterations: int = 6
    max_llm_calls: int = 6


class LabRunner:
    def __init__(
        self,
        run_store: RunStore,
        model_config: ModelConfig | None = None,
        lm_factory=None,
    ) -> None:
        self.run_store = run_store
        self.model_config = model_config or ModelConfig.from_env()
        self.lm_factory = lm_factory

    def run(self, options: RunOptions, event_callback=None) -> ExperimentTrace:
        recorder = EventRecorder(callbacks=[event_callback] if event_callback else [])
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
        recorder.emit(
            "run.started",
            summary=options.runner_mode,
            payload={"prompt_preview": options.prompt[:240], "run_id": run_id},
        )

        corpus = LocalCorpus.from_directory(options.corpus_dir) if options.corpus_dir else None
        initial_retrieval = self._initial_retrieval(corpus, options.prompt, options.retrieval_top_k, recorder)
        context = corpus.format_context(initial_retrieval.chunks) if corpus else ""
        corpus_overview = corpus.overview() if corpus else "No local corpus selected."

        trace = ExperimentTrace(
            run_id=run_id,
            created_at=datetime.now(timezone.utc),
            prompt=options.prompt,
            runner_mode=options.runner_mode,
            model=self.model_config.dspy_model,
            corpus_dir=str(options.corpus_dir) if options.corpus_dir else None,
            initial_retrieval=initial_retrieval,
            config={
                "retrieval_top_k": options.retrieval_top_k,
                "max_iterations": options.max_iterations,
                "max_llm_calls": options.max_llm_calls,
            },
        )

        run_order = ["baseline", "rlm"] if options.runner_mode == "both" else [options.runner_mode]
        for runner_name in run_order:
            if runner_name == "baseline":
                trace.results[runner_name] = self._run_baseline(
                    prompt=options.prompt,
                    retrieved_context=context,
                    corpus_overview=corpus_overview,
                    recorder=recorder,
                    retrieval=initial_retrieval,
                )
            elif runner_name == "rlm":
                trace.results[runner_name] = self._run_rlm(
                    prompt=options.prompt,
                    retrieved_context=context,
                    corpus_overview=corpus_overview,
                    recorder=recorder,
                    retrieval=initial_retrieval,
                    corpus=corpus,
                    max_iterations=options.max_iterations,
                    max_llm_calls=options.max_llm_calls,
                )
            else:
                raise ValueError(f"Unknown runner mode: {runner_name}")

        recorder.emit("run.completed", summary=f"{len(trace.results)} result(s)")
        trace.events = recorder.events
        self.run_store.save(trace)
        return trace

    def _build_lm(self, runner_name: str, recorder: EventRecorder):
        if self.lm_factory is not None:
            return self.lm_factory(runner_name, recorder)
        return build_tracing_lm(self.model_config, recorder, runner_name)

    def _initial_retrieval(
        self,
        corpus: LocalCorpus | None,
        prompt: str,
        top_k: int,
        recorder: EventRecorder,
    ) -> RetrievalSnapshot | None:
        if corpus is None:
            return None
        snapshot = corpus.search(prompt, top_k=top_k)
        recorder.emit(
            "retrieval.initial",
            summary=f"{len(snapshot.chunks)} chunks",
            payload=snapshot.model_dump(mode="json"),
        )
        return snapshot

    def _run_baseline(
        self,
        *,
        prompt: str,
        retrieved_context: str,
        corpus_overview: str,
        recorder: EventRecorder,
        retrieval: RetrievalSnapshot | None,
    ) -> RunnerTrace:
        started_at = datetime.now(timezone.utc)
        recorder.emit("runner.started", runner="baseline", summary="Baseline predictor")
        lm = self._build_lm("baseline", recorder)
        predictor = dspy.Predict(AnswerTask)
        started = time.perf_counter()
        try:
            with dspy.context(lm=lm):
                with dspy.track_usage() as tracker:
                    prediction = predictor(
                        question=prompt,
                        retrieved_context=retrieved_context,
                        corpus_overview=corpus_overview,
                    )
            metrics = self._build_metrics(lm.history, tracker.get_total_tokens())
            metrics.duration_ms = (time.perf_counter() - started) * 1000
            answer = getattr(prediction, "answer", "")
            recorder.emit("runner.answer", runner="baseline", summary=answer[:120], payload={"answer": answer})
            return RunnerTrace(
                runner="baseline",
                success=True,
                answer=answer,
                metrics=metrics,
                lm_calls=self._serialize_calls(lm.history),
                retrieved_chunks=(retrieval.chunks if retrieval else []),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            return RunnerTrace(
                runner="baseline",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                metrics=RunnerMetrics(duration_ms=(time.perf_counter() - started) * 1000),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

    def _run_rlm(
        self,
        *,
        prompt: str,
        retrieved_context: str,
        corpus_overview: str,
        recorder: EventRecorder,
        retrieval: RetrievalSnapshot | None,
        corpus: LocalCorpus | None,
        max_iterations: int,
        max_llm_calls: int,
    ) -> RunnerTrace:
        started_at = datetime.now(timezone.utc)
        recorder.emit("runner.started", runner="rlm", summary="Recursive language model")
        lm = self._build_lm("rlm", recorder)
        interpreter = LocalPythonInterpreter(recorder=recorder, runner_name="rlm")
        tools = self._build_tools(corpus=corpus, recorder=recorder)
        program = InstrumentedRLM(
            AnswerTask,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            interpreter=interpreter,
            tools=tools,
            verbose=False,
            recorder=recorder,
            runner_name="rlm",
        )
        started = time.perf_counter()
        try:
            with dspy.context(lm=lm):
                with dspy.track_usage() as tracker:
                    prediction = program(
                        question=prompt,
                        retrieved_context=retrieved_context,
                        corpus_overview=corpus_overview,
                    )
            metrics = self._build_metrics(lm.history, tracker.get_total_tokens())
            metrics.duration_ms = (time.perf_counter() - started) * 1000
            answer = getattr(prediction, "answer", "")
            recorder.emit("runner.answer", runner="rlm", summary=answer[:120], payload={"answer": answer})
            return RunnerTrace(
                runner="rlm",
                success=True,
                answer=answer,
                metrics=metrics,
                lm_calls=self._serialize_calls(lm.history),
                trajectory=sanitize_for_json(getattr(prediction, "trajectory", [])),
                retrieved_chunks=(retrieval.chunks if retrieval else []),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            return RunnerTrace(
                runner="rlm",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                metrics=RunnerMetrics(duration_ms=(time.perf_counter() - started) * 1000),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

    def _build_tools(self, *, corpus: LocalCorpus | None, recorder: EventRecorder) -> list[Any]:
        if corpus is None:
            return []

        def search_corpus(query: str, top_k: int = 3) -> str:
            """Search the indexed local corpus and return the top matching chunks as JSON."""
            snapshot = corpus.search(query, top_k=max(1, min(int(top_k), 8)))
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=query[:120],
                payload=snapshot.model_dump(mode="json"),
            )
            return json.dumps(snapshot.model_dump(mode="json"), indent=2)

        def search_with_filters(
            query: str,
            path_prefix: str = "",
            file_type: str = "",
            top_k: int = 3,
        ) -> str:
            """Search the corpus with optional path prefix and file-type filters, returning JSON."""
            snapshot = corpus.search_with_filters(
                query,
                top_k=max(1, min(int(top_k), 8)),
                path_prefix=path_prefix,
                file_type=file_type,
            )
            payload = {
                "tool": "search_with_filters",
                "filters": {
                    "path_prefix": path_prefix,
                    "file_type": file_type,
                },
                **snapshot.model_dump(mode="json"),
            }
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=f"search_with_filters: {query[:96]}",
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        def read_chunk(chunk_id: str) -> str:
            """Return the full text for one previously retrieved chunk id."""
            chunk = corpus.get_chunk(chunk_id)
            payload = chunk.model_dump(mode="json")
            recorder.emit(
                "retrieval.read",
                runner="rlm",
                summary=chunk_id,
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        def expand_neighbors(chunk_id: str, window: int = 1) -> str:
            """Return a chunk and its neighboring chunks from the same document as JSON."""
            chunks = corpus.expand_neighbors(chunk_id, window=max(0, min(int(window), 5)))
            payload = {
                "tool": "expand_neighbors",
                "chunk_id": chunk_id,
                "window": window,
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            }
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=f"expand_neighbors: {chunk_id}",
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        def list_documents(path_prefix: str = "", file_type: str = "", limit: int = 50) -> str:
            """List indexed documents with optional prefix/type filters, returning JSON."""
            documents = corpus.list_documents(
                path_prefix=path_prefix,
                file_type=file_type,
                limit=max(1, min(int(limit), 200)),
            )
            payload = {
                "tool": "list_documents",
                "path_prefix": path_prefix,
                "file_type": file_type,
                "documents": documents,
            }
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=f"list_documents: {path_prefix or '/'}",
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        def get_document_chunks(path: str, limit: int = 20) -> str:
            """Return chunk metadata for a single indexed document as JSON."""
            chunks = corpus.get_document_chunks(path)[: max(1, min(int(limit), 200))]
            payload = {
                "tool": "get_document_chunks",
                "path": path,
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            }
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=f"get_document_chunks: {path}",
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        def read_document_section(path: str, start_index: int = 0, end_index: int = 1) -> str:
            """Return a contiguous range of chunks from one document as JSON."""
            chunks = corpus.read_document_chunk_range(
                path,
                start_index=int(start_index),
                end_index=int(end_index),
            )
            payload = {
                "tool": "read_document_section",
                "path": path,
                "start_index": start_index,
                "end_index": end_index,
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            }
            recorder.emit(
                "retrieval.tool",
                runner="rlm",
                summary=f"read_document_section: {path}",
                payload=payload,
            )
            return json.dumps(payload, indent=2)

        return [
            search_corpus,
            search_with_filters,
            read_chunk,
            expand_neighbors,
            list_documents,
            get_document_chunks,
            read_document_section,
        ]

    def _build_metrics(self, history: list[dict[str, Any]], tracked_usage: dict[str, dict[str, Any]]) -> RunnerMetrics:
        combined_usage: dict[str, Any] = {}
        for usage in tracked_usage.values():
            combined_usage = merge_usage(combined_usage, normalize_usage(usage))
        if not combined_usage:
            for entry in history:
                combined_usage = merge_usage(combined_usage, normalize_usage(entry.get("usage")))
        prompt_tokens, completion_tokens, total_tokens = extract_token_counts(combined_usage)
        return RunnerMetrics(
            call_count=len(history),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage=combined_usage,
        )

    def _serialize_calls(self, history: list[dict[str, Any]]) -> list[LMCallRecord]:
        records: list[LMCallRecord] = []
        for entry in history:
            usage = normalize_usage(entry.get("usage"))
            prompt_tokens, completion_tokens, total_tokens = extract_token_counts(usage)
            records.append(
                LMCallRecord(
                    call_id=entry.get("call_id", uuid4().hex),
                    model=entry.get("model", self.model_config.dspy_model),
                    model_type=entry.get("model_type", "chat"),
                    prompt=entry.get("prompt"),
                    messages=entry.get("messages"),
                    outputs=entry.get("outputs"),
                    usage=usage,
                    latency_ms=float(entry.get("latency_ms", 0.0) or 0.0),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    timestamp=entry.get("timestamp"),
                )
            )
        return records
