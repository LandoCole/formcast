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
    "FillReport": "formcast.render.report",
    "LLMConfig": "formcast.llm",
    "OpContext": "formcast.template.ops",
    "SessionBudget": "formcast.agent.session",
    "SessionResult": "formcast.agent.session",
    "Template": "formcast.template.model",
    "apply": "formcast.template.ops",
    "build_form_map": "formcast.pagemap.build",
    "build_template": "formcast.agent.api",
    "coverage": "formcast.template.coverage",
    "fill": "formcast.render.fill",
    "fingerprint": "formcast.match",
    "flatten_schema": "formcast.schema",
    "preview": "formcast.render.preview",
    "revise": "formcast.agent.api",
    "to_smart_pdf": "formcast.render.fill",
}
# Public submodules, also loaded on first use: ``formcast.ops.Move("f1", dx=4)``.
_LAZY_MODULES = {"ops": "formcast.template.ops"}

if TYPE_CHECKING:
    from formcast.agent.api import build_template, revise
    from formcast.agent.session import SessionBudget, SessionResult
    from formcast.llm import LLMConfig
    from formcast.match import fingerprint
    from formcast.pagemap.build import build_form_map
    from formcast.render.fill import fill, to_smart_pdf
    from formcast.render.preview import preview
    from formcast.render.report import FillReport
    from formcast.schema import flatten_schema
    from formcast.template import ops
    from formcast.template.coverage import coverage
    from formcast.template.model import Template
    from formcast.template.ops import OpContext, apply


def __getattr__(name: str) -> Any:
    if name in _LAZY_MODULES:
        value = importlib.import_module(_LAZY_MODULES[name])
    elif name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
    else:
        raise AttributeError(f"module 'formcast' has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY) | set(_LAZY_MODULES))


__all__ = [
    "FillReport", "FormcastError", "LLMConfig", "OpContext", "OpError", "SessionBudget", "SessionResult",
    "Template", "__version__", "apply", "build_form_map", "build_template", "coverage", "errors", "fill",
    "fingerprint", "flatten_schema", "ops", "preview", "revise", "to_smart_pdf",
]  # fmt: skip
