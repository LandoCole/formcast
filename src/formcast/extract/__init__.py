"""Read filled PDFs of one layout into structured, checked values with no model call.

    ExtractionTemplate, Reader, ...   formcast.extract.model   (the template and the result of reading a document)
    DocText, label_key                formcast.extract.page    (one open PDF and the lookups made on it)

The public names load on first use, as in ``formcast``, so importing one module does not pull in the rest.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_LAZY = {
    "AnchorRead": "formcast.extract.model",
    "BoxRead": "formcast.extract.model",
    "ChoiceMark": "formcast.extract.model",
    "ChoiceRead": "formcast.extract.model",
    "DocText": "formcast.extract.page",
    "DocumentResult": "formcast.extract.model",
    "ExtractionTemplate": "formcast.extract.model",
    "FieldResult": "formcast.extract.model",
    "Layout": "formcast.extract.model",
    "PhraseRead": "formcast.extract.model",
    "Reader": "formcast.extract.model",
    "StaticLine": "formcast.extract.model",
    "Status": "formcast.extract.model",
    "Strategy": "formcast.extract.model",
    "WidgetRead": "formcast.extract.model",
    "concrete_paths": "formcast.extract.model",
    "label_key": "formcast.extract.page",
}

if TYPE_CHECKING:
    from formcast.extract.model import (
        AnchorRead, BoxRead, ChoiceMark, ChoiceRead, DocumentResult, ExtractionTemplate, FieldResult, Layout,
        PhraseRead, Reader, StaticLine, Status, Strategy, WidgetRead, concrete_paths,
    )  # fmt: skip
    from formcast.extract.page import DocText, label_key


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'formcast.extract' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
