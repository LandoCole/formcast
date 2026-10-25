"""Provider protocol, shared HTTP/retry machinery and the provider factory."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from formcast.errors import FormcastError
from formcast.llm.config import LLMConfig
from formcast.llm.types import ImagePart, LLMResponse, Message, ToolCall, ToolSpec

MAX_BACKOFF_S = 60.0
BASE_BACKOFF_S = 1.0
_BODY_EXCERPT = 500
_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))
_VISION_HINT = (
    " (the model or endpoint may not accept images: "
    "pass vision=False to build without page images)"
)
# 4xx statuses that say nothing about the request's content (a timeout, a rate limit): no vision hint.
_NO_HINT_STATUSES = frozenset({408, 429})

# Indirection so tests can monkeypatch the backoff sleep (or pass `sleep=` to
# make_provider) without waiting.
_sleep: Callable[[float], None] = time.sleep


class Provider(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        """One model turn. `None` overrides fall back to the config's limits."""
        ...

    def close(self) -> None:
        """Release network resources. Safe to call more than once."""
        ...


def has_images(messages: list[Message]) -> bool:
    return any(
        isinstance(m.content, list) and any(isinstance(p, ImagePart) for p in m.content)
        for m in messages
    )


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


def wire_arguments(call: ToolCall) -> str:
    """Arguments string to send back for a prior tool call: always a JSON object.

    Malformed (or non-object) arguments are replaced by "{}" because strict
    compatibility layers reject invalid JSON in history; `call.raw_arguments`
    stays untouched for error reporting.
    """
    if call.arguments is None:
        return "{}"
    if parse_arguments(call.raw_arguments)[0] is not None and call.raw_arguments.strip():
        return call.raw_arguments
    return json.dumps(call.arguments)


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

    def redact(self, text: str) -> str:
        """Mask the API key and extra-header values (gateways may echo them back)."""
        secrets = [self.cfg.api_key, *self.cfg.extra_headers.values()]
        for secret in sorted({x for x in secrets if x}, key=len, reverse=True):
            text = text.replace(secret, "***")
        return text

    def _excerpt(self, text: str) -> str:
        # Redact before truncating so a secret cut at the boundary cannot leak.
        return self.redact(text)[:_BODY_EXCERPT]

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

    def post_json(
        self,
        path: str,
        body: dict,
        *,
        timeout_s: float | None = None,
        images: bool = False,
    ) -> dict:
        """POST `body` as JSON, retrying 429/5xx and transport errors with backoff.

        `timeout_s` overrides the config timeout for this call; `images` marks a
        request carrying image parts so a 4xx (not 408 or 429) can suggest `vision=False`.
        """
        url = self._url(path)
        timeout = self.cfg.timeout_s if timeout_s is None else timeout_s
        attempts = self.cfg.max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self._client.post(
                    url, headers=self._headers(), json=body, timeout=timeout
                )
            except httpx.TransportError as exc:
                if last:
                    raise FormcastError(
                        f"LLM request to {url} failed after {attempts} attempt(s): "
                        f"{type(exc).__name__}: {self.redact(str(exc))}"
                    ) from exc
                self._wait(attempt, None)
                continue
            if response.status_code in _RETRY_STATUSES and not last:
                self._wait(attempt, response)
                continue
            if response.status_code >= 400:
                code = response.status_code
                hint = _VISION_HINT if images and code < 500 and code not in _NO_HINT_STATUSES else ""
                raise FormcastError(
                    f"LLM request to {url} failed with HTTP {response.status_code}: "
                    f"{self._excerpt(response.text)}{hint}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise FormcastError(
                    f"LLM response from {url} is not JSON: {self._excerpt(response.text)}"
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
