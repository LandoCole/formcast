from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from rlm_lab.modeling.usage import sanitize_for_json


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    runner: str | None = None
    step: int | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    chunk_id: str
    path: str
    preview: str
    score: float = 0.0
    start_offset: int
    end_offset: int
    text: str


class RetrievalSnapshot(BaseModel):
    query: str
    top_k: int
    chunks: list[RetrievedChunk] = Field(default_factory=list)


class LMCallRecord(BaseModel):
    call_id: str
    model: str
    model_type: str
    prompt: str | None = None
    messages: Any = None
    outputs: Any = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timestamp: str | None = None


class RunnerMetrics(BaseModel):
    duration_ms: float = 0.0
    call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)


class RunnerTrace(BaseModel):
    runner: str
    success: bool
    answer: str | None = None
    error: str | None = None
    metrics: RunnerMetrics = Field(default_factory=RunnerMetrics)
    lm_calls: list[LMCallRecord] = Field(default_factory=list)
    trajectory: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class ExperimentTrace(BaseModel):
    run_id: str
    created_at: datetime
    prompt: str
    runner_mode: str
    model: str
    corpus_dir: str | None = None
    initial_retrieval: RetrievalSnapshot | None = None
    events: list[TraceEvent] = Field(default_factory=list)
    results: dict[str, RunnerTrace] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    run_id: str
    created_at: datetime
    runner_mode: str
    model: str
    prompt_preview: str
    result_count: int


class EventRecorder:
    def __init__(self, callbacks: list[Callable[[TraceEvent], None]] | None = None):
        self.callbacks = callbacks or []
        self.events: list[TraceEvent] = []

    def emit(
        self,
        kind: str,
        *,
        runner: str | None = None,
        step: int | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            kind=kind,
            runner=runner,
            step=step,
            summary=summary,
            payload=sanitize_for_json(payload or {}),
        )
        self.events.append(event)
        for callback in self.callbacks:
            callback(event)
        return event
