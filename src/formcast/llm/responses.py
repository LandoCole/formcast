"""OpenAI Responses format (`POST {base_url}/responses`)."""

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
            wire.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = f"data:{part.media_type};base64,{base64.b64encode(part.data).decode()}"
            wire.append({"type": "input_image", "image_url": url})
    return wire


def _input_items(messages: list[Message]) -> tuple[str | None, list[dict]]:
    """Split messages into (`instructions`, `input` items)."""
    instructions: list[str] = []
    items: list[dict] = []
    for m in messages:
        if m.role == "system":
            instructions.append(content_text(m.content))
        elif m.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.tool_call_id,
                    "output": content_text(m.content),
                }
            )
        elif m.role == "assistant" and m.provider_items:
            # Echo the provider's own output items verbatim: reasoning items must
            # accompany the function_call items they produced on the next turn.
            items.extend(m.provider_items)
        elif m.role == "assistant":
            if m.content:
                items.append({"role": "assistant", "content": content_text(m.content)})
            for c in m.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": c.id,
                        "name": c.name,
                        "arguments": wire_arguments(c),
                    }
                )
        else:
            items.append({"role": m.role, "content": _content_to_wire(m.content)})
    return ("\n\n".join(instructions) or None), items


class ResponsesProvider(HTTPProvider):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_output_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        cfg = self.cfg
        instructions, items = _input_items(messages)
        body: dict = {
            "model": cfg.model,
            "input": items,
            "max_output_tokens": cfg.max_output_tokens if max_output_tokens is None else max_output_tokens,
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in tools
            ]
        if cfg.temperature is not None:
            body["temperature"] = cfg.temperature
        if cfg.reasoning_effort is not None:
            body["reasoning"] = {"effort": cfg.reasoning_effort}

        raw = self.post_json("responses", body, timeout_s=timeout_s, images=has_images(messages))
        if raw.get("status") == "failed":
            error = raw.get("error") or {}
            raise FormcastError(f"responses call failed: {self.redact(str(error.get('message') or raw))[:300]}")
        output = raw.get("output")
        if not isinstance(output, list):
            raise FormcastError(f"responses reply has no output list: {self.redact(str(raw))[:300]}")

        texts: list[str] = []
        calls = []
        for item in output:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text") or "")
            elif kind == "function_call":
                calls.append(
                    make_tool_call(
                        item.get("call_id") or item.get("id") or "",
                        item.get("name") or "",
                        item.get("arguments"),
                    )
                )
        usage = raw.get("usage") or {}
        return LLMResponse(
            text="".join(texts),
            tool_calls=calls,
            usage=Usage(
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
            ),
            raw=raw,
            provider_items=list(output),
        )
