"""Workbench settings, read from ``FORMCAST_DEMO_*`` variables.

To swap: edit ``Settings.from_env`` to read your own config source (a file, a secrets
manager); the rest of the server only sees the ``Settings`` object. The values come from the
mapping you pass, or, with no mapping, from the repository's ``.env`` overlaid by the process
environment. Values are never printed or logged (``LLMConfig`` masks its key in ``repr``).

    FORMCAST_DEMO_BASE_URL, FORMCAST_DEMO_API_KEY, FORMCAST_DEMO_MODEL   the model endpoint
    FORMCAST_DEMO_API_FORMAT   chat_completions (default) or responses
    FORMCAST_DEMO_HOST, FORMCAST_DEMO_PORT, FORMCAST_DEMO_DATA_DIR

The AI features are on only when the base URL, key and model are all set.

The library only scores how alike two forms are (``formcast.similarity``). What to do with a
score is decided here: reuse a stored template at or above ``reuse_threshold``, adapt one at or
above ``adapt_threshold``, otherwise build a new one. The defaults were calibrated on the dev
forms only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from formcast.llm import LLMConfig

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "FORMCAST_DEMO_"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = field(default_factory=lambda: ROOT / "demo" / "data")
    reuse_threshold: float = 0.95
    adapt_threshold: float = 0.60
    llm: LLMConfig | None = None
    max_upload_bytes: int = 50 * 2**20
    max_json_bytes: int = 20 * 2**20

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Settings from ``env``; with no mapping, from ``.env`` plus ``os.environ``."""
        if env is None:
            env = {**read_dotenv(ROOT / ".env"), **os.environ}

        def get(name: str) -> str | None:
            value = (env.get(PREFIX + name) or "").strip()
            return value or None

        out = cls()
        out.host = get("HOST") or out.host
        if (port := get("PORT")) is not None:
            try:
                out.port = int(port)
            except ValueError:
                raise ValueError(f"{PREFIX}PORT must be a whole number") from None
        if (data_dir := get("DATA_DIR")) is not None:
            out.data_dir = Path(data_dir).expanduser().resolve()
        base_url, api_key, model = get("BASE_URL"), get("API_KEY"), get("MODEL")
        if base_url and api_key and model:
            out.llm = LLMConfig(
                base_url=base_url, api_key=api_key, model=model,
                api_format=get("API_FORMAT") or "chat_completions",  # type: ignore[arg-type]
            )  # fmt: skip
        return out


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines (``#`` comments, optional ``export``, optional quotes); {} if absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out
