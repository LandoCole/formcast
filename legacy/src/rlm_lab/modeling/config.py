from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ModelConfig:
    api_key: str | None
    model_name: str
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1200
    cache_dir: Path = field(default_factory=lambda: Path.cwd() / ".dspy_cache")

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            api_key=os.getenv("OPENAI_API_KEY"),
            model_name=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )

    @property
    def dspy_model(self) -> str:
        return self.model_name if "/" in self.model_name else f"openai/{self.model_name}"

    @property
    def missing_env(self) -> list[str]:
        missing: list[str] = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.model_name:
            missing.append("OPENAI_MODEL")
        return missing

    def ensure_cache_dir(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
