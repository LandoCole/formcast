"""formcast: turn blank PDF forms into field templates using an LLM agent."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from formcast import errors
from formcast.errors import FormcastError, OpError

__version__ = "0.1.0"

# Public names loaded on first use, so importing a light submodule (the sandbox
# worker imports formcast.pagemap.query) does not pull in the whole library.
_LAZY = {
    "LLMConfig": "formcast.llm",
    "Template": "formcast.template.model",
    "apply": "formcast.template.ops",
    "build_form_map": "formcast.pagemap.build",
    "build_template": "formcast.agent.api",
    "fingerprint": "formcast.match",
    "preview": "formcast.render.preview",
    "revise": "formcast.agent.api",
}

if TYPE_CHECKING:
    from formcast.agent.api import build_template, revise
    from formcast.llm import LLMConfig
    from formcast.match import fingerprint
    from formcast.pagemap.build import build_form_map
    from formcast.render.preview import preview
    from formcast.template.model import Template
    from formcast.template.ops import apply


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'formcast' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "FormcastError", "LLMConfig", "OpError", "Template", "__version__", "apply", "build_form_map",
    "build_template", "errors", "fingerprint", "preview", "revise",
]  # fmt: skip
