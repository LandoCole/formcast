"""Provider-neutral message, tool and response types for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias


@dataclass
class ToolSpec:
    """A function tool offered to the model. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    """A tool call requested by the model.

    `arguments` is the parsed JSON object, or None when the model produced
    malformed JSON (or JSON that is not an object); `raw_arguments` always keeps
    the original string so the agent can show it back to the model.
    """

    id: str
    name: str
    arguments: dict | None
    raw_arguments: str


@dataclass(frozen=True)
class TextPart:
    """A text segment of a multi-part message."""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """An image segment of a multi-part message (raw bytes, sent as a data URL)."""

    data: bytes
    media_type: str = "image/png"


ContentPart: TypeAlias = TextPart | ImagePart


def content_text(content: str | list[ContentPart]) -> str:
    """Plain-text view of message content: images become `[image: N bytes]`."""
    if isinstance(content, str):
        return content
    return "\n\n".join(
        p.text if isinstance(p, TextPart) else f"[image: {len(p.data)} bytes]" for p in content
    )


@dataclass
class Message:
    """One conversation turn.

    `provider_items` is an opaque, provider-specific echo channel: for an
    assistant message it holds the raw output items the provider returned (the
    Responses API returns reasoning items that must be passed back verbatim on
    the next turn). Providers fill it in `LLMResponse.provider_items` and read it
    back when serialising; callers just copy it onto the assistant Message.
    Leave it empty for hand-built messages.

    `content` is a plain string, or a list of text/image parts (user messages
    only; the providers send images as data URLs).
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart] = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    provider_items: list[dict] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    raw: dict
    # Raw items to store on the assistant Message (see Message.provider_items).
    provider_items: list[dict] = field(default_factory=list)

    def to_message(self) -> Message:
        """The assistant Message to append to the history for the next turn."""
        return Message(
            role="assistant",
            content=self.text,
            tool_calls=list(self.tool_calls),
            provider_items=list(self.provider_items),
        )
