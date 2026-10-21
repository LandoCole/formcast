"""Provider protocol, shared HTTP/retry machinery and the provider factory."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from formcast.errors import FormcastError
from formcast.llm.config import LLMConfig
from formcast.llm.types import LLMResponse, Message, ToolCall, ToolSpec

MAX_BACKOFF_S = 60.0
BASE_BACKOFF_S = 1.0
_BODY_EXCERPT = 500
_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))

# Indirection so tests can monkeypatch the backoff sleep (or pass `sleep=` to
# make_provider) without waiting.
_sleep: Callable[[float], None] = time.sleep


class Provider(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...


def parse_arguments(raw: str | None) -> tuple[dict | None, str]:
    """Parse tool-call arguments. Returns (None, raw) when malformed or not an object.

    Empty arguments mean "no arguments" ({}): tools without parameters often
    come back with an empty string.
    """
    raw = raw or ""
    if not raw.strip():
        return {}, raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw
    return (parsed if isinstance(parsed, dict) else None), raw


def make_tool_call(call_id: str, name: str, raw_arguments: str | None) -> ToolCall:
    arguments, raw = parse_arguments(raw_arguments)
    return ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments=raw)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:  # HTTP-date form: fall back to exponential backoff
        return None
    return max(0.0, seconds)


class HTTPProvider:
    """Shared plumbing: auth headers, JSON POST, retries with backoff, errors."""

    def __init__(
        self,
        cfg: LLMConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._sleep = sleep
        self._client = httpx.Client(transport=transport, timeout=cfg.timeout_s)

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
            **self.cfg.extra_headers,
        }

    def _wait(self, attempt: int, response: httpx.Response | None) -> None:
        delay = _retry_after(response) if response is not None else None
        if delay is None:
            delay = BASE_BACKOFF_S * 2**attempt
        delay = min(delay, MAX_BACKOFF_S)
        (self._sleep or _sleep)(delay)

    def post_json(self, path: str, body: dict) -> dict:
        """POST `body` as JSON, retrying 429/5xx and transport errors with backoff."""
        url = self._url(path)
        attempts = self.cfg.max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self._client.post(url, headers=self._headers(), json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if last:
                    raise FormcastError(
                        f"LLM request to {url} failed after {attempts} attempt(s): "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                self._wait(attempt, None)
                continue
            if response.status_code in _RETRY_STATUSES and not last:
                self._wait(attempt, response)
                continue
            if response.status_code >= 400:
                raise FormcastError(
                    f"LLM request to {url} failed with HTTP {response.status_code}: "
                    f"{response.text[:_BODY_EXCERPT]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise FormcastError(
                    f"LLM response from {url} is not JSON: {response.text[:_BODY_EXCERPT]}"
                ) from exc
            if not isinstance(payload, dict):
                raise FormcastError(f"LLM response from {url} is not a JSON object")
            return payload
        raise AssertionError("unreachable")  # pragma: no cover


def make_provider(
    cfg: LLMConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Provider:
    """Build the provider for `cfg.api_format`. `transport`/`sleep` are test seams."""
    from formcast.llm.chat_completions import ChatCompletionsProvider
    from formcast.llm.responses import ResponsesProvider

    if cfg.api_format == "chat_completions":
        return ChatCompletionsProvider(cfg, transport=transport, sleep=sleep)
    if cfg.api_format == "responses":
        return ResponsesProvider(cfg, transport=transport, sleep=sleep)
    raise FormcastError(f"unknown api_format {cfg.api_format!r}")
