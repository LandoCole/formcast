"""OpenAI Chat Completions format (`POST {base_url}/chat/completions`)."""

from __future__ import annotations

import base64

from formcast.errors import FormcastError
from formcast.llm.base import HTTPProvider, has_images, make_tool_call, wire_arguments
from formcast.llm.types import (
    ImagePart,
    LLMResponse,
    Message,
    TextPart,
    ToolSpec,
    Usage,
    content_text,
)


def _content_to_wire(content: str | list) -> str | list[dict]:
    if isinstance(content, str):
        return content
    wire: list[dict] = []
    for part in content:
        if isinstance(part, TextPart):
            wire.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = f"data:{part.media_type};base64,{base64.b64encode(part.data).decode()}"
            wire.append({"type": "image_url", "image_url": {"url": url}})
    return wire


def _message_to_wire(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": content_text(m.content)}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": content_text(m.content) or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": wire_arguments(c)},
                }
                for c in m.tool_calls
            ],
        }
    return {"role": m.role, "content": _content_to_wire(m.content)}


class ChatCompletionsProvider(HTTPProvider):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        cfg = self.cfg
        body: dict = {
            "model": cfg.model,
            "messages": [_message_to_wire(m) for m in messages],
            cfg.max_tokens_param: cfg.max_output_tokens if max_output_tokens is None else max_output_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        if cfg.temperature is not None:
            body["temperature"] = cfg.temperature
        if cfg.reasoning_effort is not None:
            body["reasoning_effort"] = cfg.reasoning_effort

        raw = self.post_json(
            "chat/completions", body, timeout_s=timeout_s, images=has_images(messages)
        )
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise FormcastError(f"chat completions reply has no choices: {self.redact(str(raw))[:300]}")
        message = choices[0].get("message") or {}
        calls = [
            make_tool_call(
                c.get("id") or "",
                (c.get("function") or {}).get("name") or "",
                (c.get("function") or {}).get("arguments"),
            )
            for c in message.get("tool_calls") or []
        ]
        usage = raw.get("usage") or {}
        return LLMResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens") or 0,
                output_tokens=usage.get("completion_tokens") or 0,
            ),
            raw=raw,
        )
