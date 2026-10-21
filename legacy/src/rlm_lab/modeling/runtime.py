from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

os.environ.setdefault("DSPY_CACHEDIR", str(Path.cwd() / ".dspy_cache"))

import dspy

from rlm_lab.execution.events import EventRecorder
from rlm_lab.modeling.config import ModelConfig
from rlm_lab.modeling.usage import normalize_usage, sanitize_for_json


class TracingLM(dspy.BaseLM):
    """Thin LM wrapper that emits events and preserves per-call history slices."""

    def __init__(self, inner: Any, recorder: EventRecorder, runner_name: str):
        self.inner = inner
        self.recorder = recorder
        self.runner_name = runner_name
        model = getattr(inner, "model", "unknown")
        model_type = getattr(inner, "model_type", "chat")
        cache = getattr(inner, "cache", True)
        kwargs = dict(getattr(inner, "kwargs", {}))
        super().__init__(
            model=model,
            model_type=model_type,
            temperature=kwargs.get("temperature", 0.0),
            max_tokens=kwargs.get("max_tokens", 1000),
            cache=cache,
        )
        self.kwargs = kwargs
        self.history = []

    def copy(self, **kwargs: Any) -> "TracingLM":
        if hasattr(self.inner, "copy"):
            inner = self.inner.copy(**kwargs)
        else:
            inner = self.inner
        return TracingLM(inner=inner, recorder=self.recorder, runner_name=self.runner_name)

    def forward(self, prompt: str | None = None, messages: list[dict[str, Any]] | None = None, **kwargs: Any):
        raise NotImplementedError("TracingLM delegates directly through __call__.")

    async def acall(self, prompt: str | None = None, messages: list[dict[str, Any]] | None = None, **kwargs: Any):
        return self.__call__(prompt=prompt, messages=messages, **kwargs)

    def inspect_history(self, n: int = 1) -> list[dict[str, Any]]:
        return self.history[-n:]

    def __call__(self, prompt: str | None = None, messages: list[dict[str, Any]] | None = None, **kwargs: Any):
        started = time.perf_counter()
        call_id = str(uuid.uuid4())
        inner_history = getattr(self.inner, "history", [])
        start_index = len(inner_history)
        self.recorder.emit(
            "lm.call.started",
            runner=self.runner_name,
            summary=f"{self.model}",
            payload={
                "call_id": call_id,
                "prompt_preview": (prompt or "")[:240],
                "message_count": len(messages or []),
            },
        )
        outputs = self.inner(prompt=prompt, messages=messages, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000

        new_entries = list(getattr(self.inner, "history", [])[start_index:])
        for entry in new_entries:
            usage = normalize_usage(entry.get("usage"))
            serialized = {
                "call_id": call_id,
                "model": entry.get("model", self.model),
                "model_type": entry.get("model_type", self.model_type),
                "prompt": sanitize_for_json(entry.get("prompt")),
                "messages": sanitize_for_json(entry.get("messages")),
                "outputs": sanitize_for_json(entry.get("outputs")),
                "usage": usage,
                "timestamp": entry.get("timestamp"),
                "latency_ms": elapsed_ms,
                "cost": entry.get("cost"),
                "runner": self.runner_name,
            }
            self.history.append(serialized)
            self.recorder.emit(
                "lm.call.completed",
                runner=self.runner_name,
                summary=f"{serialized['model']} {int(elapsed_ms)}ms",
                payload={
                    "call_id": call_id,
                    "latency_ms": elapsed_ms,
                    "usage": usage,
                    "outputs": serialized["outputs"],
                },
            )

        if not new_entries:
            self.recorder.emit(
                "lm.call.completed",
                runner=self.runner_name,
                summary=f"{self.model} {int(elapsed_ms)}ms",
                payload={
                    "call_id": call_id,
                    "latency_ms": elapsed_ms,
                    "usage": {},
                    "outputs": sanitize_for_json(outputs),
                },
            )

        return outputs


def build_base_lm(config: ModelConfig) -> Any:
    config.ensure_cache_dir()
    if config.missing_env:
        raise RuntimeError(f"Missing required environment variables: {', '.join(config.missing_env)}")

    kwargs: dict[str, Any] = {
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "cache": False,
        "api_key": config.api_key,
    }
    if config.base_url:
        kwargs["api_base"] = config.base_url

    return dspy.LM(config.dspy_model, **kwargs)


def build_tracing_lm(
    config: ModelConfig,
    recorder: EventRecorder,
    runner_name: str,
    inner: Any | None = None,
) -> TracingLM:
    return TracingLM(inner=inner or build_base_lm(config), recorder=recorder, runner_name=runner_name)
