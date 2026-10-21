"""Fixtures for LLM provider tests.

The `@pytest.mark.llm` tests need real credentials. `load_dotenv` is a tiny
parser that copies KEY=VALUE lines from the repo's git-ignored `.env` into
os.environ for the test process only. Values are never printed or logged.
"""

import os
from pathlib import Path

import pytest

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
EVAL_PREFIXES = ("OPENAI_EVAL", "ANTHROPIC_EVAL")


def load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@pytest.fixture(scope="session")
def eval_env() -> None:
    load_dotenv()
