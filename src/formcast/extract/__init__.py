"""Read filled PDFs of one layout into structured, checked values with no model call.

    ExtractionTemplate, Reader, ...   formcast.extract.model   (the template and the result of reading a document)
    DocText, label_key                formcast.extract.page    (one open PDF and the lookups made on it)
    extract                           formcast.extract.read    (one document read with a template)
    apply_edit, SetField, ...         formcast.extract.edit    (a person's edits to a template, with no model call)
    prove, learn_maps, sample_marks   formcast.extract.proof   (the readers checked against the samples' readings)

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
    "Edit": "formcast.extract.edit",
    "ExtractionTemplate": "formcast.extract.model",
    "FieldProof": "formcast.extract.proof",
    "FieldResult": "formcast.extract.model",
    "Layout": "formcast.extract.model",
    "Mark": "formcast.extract.proof",
    "MarkAbsent": "formcast.extract.edit",
    "Outcome": "formcast.extract.proof",
    "PhraseRead": "formcast.extract.model",
    "Proof": "formcast.extract.proof",
    "Reader": "formcast.extract.model",
    "RemoveField": "formcast.extract.edit",
    "Restore": "formcast.extract.edit",
    "SampleProof": "formcast.extract.proof",
    "SampleRecord": "formcast.extract.model",
    "SetBox": "formcast.extract.edit",
    "SetClean": "formcast.extract.edit",
    "SetField": "formcast.extract.edit",
    "SetLabels": "formcast.extract.edit",
    "SetOptionBox": "formcast.extract.edit",
    "SetupInfo": "formcast.extract.model",
    "StaticLine": "formcast.extract.model",
    "Status": "formcast.extract.model",
    "Strategy": "formcast.extract.model",
    "WidgetRead": "formcast.extract.model",
    "apply_edit": "formcast.extract.edit",
    "concrete_paths": "formcast.extract.model",
    "extract": "formcast.extract.read",
    "label_key": "formcast.extract.page",
    "learn_maps": "formcast.extract.proof",
    "prove": "formcast.extract.proof",
    "same_value": "formcast.extract.proof",
    "sample_marks": "formcast.extract.proof",
}

if TYPE_CHECKING:
    from formcast.extract.edit import (
        Edit, MarkAbsent, RemoveField, Restore, SetBox, SetClean, SetField, SetLabels, SetOptionBox, apply_edit,
    )  # fmt: skip
    from formcast.extract.model import (
        AnchorRead, BoxRead, ChoiceMark, ChoiceRead, DocumentResult, ExtractionTemplate, FieldResult, Layout,
        PhraseRead, Reader, SampleRecord, SetupInfo, StaticLine, Status, Strategy, WidgetRead, concrete_paths,
    )  # fmt: skip
    from formcast.extract.page import DocText, label_key
    from formcast.extract.proof import (
        FieldProof, Mark, Outcome, Proof, SampleProof, learn_maps, prove, same_value, sample_marks,
    )  # fmt: skip
    from formcast.extract.read import extract


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'formcast.extract' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
