"""LLM endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from formcast.errors import FormcastError

ApiFormat = Literal["chat_completions", "responses"]
_FORMATS = ("chat_completions", "responses")


@dataclass
class LLMConfig:
    """Connection settings for an OpenAI-compatible endpoint.

    `temperature=None` omits the parameter (many reasoning models reject it).
    `max_tokens_param` names the Chat Completions field carrying the output
    limit: OpenAI's newer models require "max_completion_tokens" while some
    compatible servers only know "max_tokens". `reasoning_effort=None` omits it.
    """

    base_url: str
    api_key: str
    model: str
    api_format: ApiFormat = "chat_completions"
    temperature: float | None = None
    max_output_tokens: int = 8000
    timeout_s: float = 120
    max_retries: int = 3
    extra_headers: dict[str, str] = field(default_factory=dict)
    reasoning_effort: str | None = None
    max_tokens_param: str = "max_completion_tokens"

    def __post_init__(self) -> None:
        if self.api_format not in _FORMATS:
            raise FormcastError(
                f"api_format must be one of {_FORMATS}, got {self.api_format!r}"
            )

    @classmethod
    def from_env(cls, prefix: str) -> LLMConfig:
        """Read `{prefix}_BASE_URL`, `_API_KEY`, `_MODEL`, `_API_FORMAT` from os.environ."""

        def get(name: str) -> str | None:
            value = os.environ.get(f"{prefix}_{name}")
            return value.strip() if value and value.strip() else None

        missing = [n for n in ("BASE_URL", "API_KEY", "MODEL") if get(n) is None]
        if missing:
            names = ", ".join(f"{prefix}_{n}" for n in missing)
            raise FormcastError(f"missing environment variable(s): {names}")
        return cls(
            base_url=get("BASE_URL"),  # type: ignore[arg-type]
            api_key=get("API_KEY"),  # type: ignore[arg-type]
            model=get("MODEL"),  # type: ignore[arg-type]
            api_format=get("API_FORMAT") or "chat_completions",  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        shown = {
            f: ("'***'" if f == "api_key" else repr(getattr(self, f)))
            for f in self.__dataclass_fields__
        }
        # extra_headers may carry credentials too; show names only.
        shown["extra_headers"] = repr(sorted(self.extra_headers))
        return "LLMConfig(" + ", ".join(f"{k}={v}" for k, v in shown.items()) + ")"

    __str__ = __repr__
