"""Read filled PDFs of one layout into structured, checked values with no model call (a model is called only
while a template is built from samples: ``build_setup``, ``add_line``, and by the opt-in ``ai_pass``).

    ExtractionTemplate, Reader, ...   formcast.extract.model   (the template and the result of reading a document)
    DocText, label_key                formcast.extract.page    (one open PDF and the lookups made on it)
    extract                           formcast.extract.read    (one document read with a template)
    apply_edit, SetField, ...         formcast.extract.edit    (a person's edits to a template, with no model call)
    prove, learn_maps, sample_marks   formcast.extract.proof   (the readers checked against the samples' readings)
    read_samples, sample_view, ...    formcast.extract.samples (the sample PDFs read, and what a model sees of them)
    resolve, ReaderProposal, ...      formcast.extract.readers (a model's reader proposals made readers, by code)
    read_values, propose_readers, ... formcast.extract.ask     (the model calls that build a template from samples)
    build_setup, add_line, SetupSpec  formcast.extract.setup   (a template built from samples in stages, with a model)
    required_paths                    formcast.extract.levels  (which fields of a template are required)
    ai_pass                           formcast.extract.aipass  (a model asked for a run's failing required fields)

The public names load on first use, as in ``formcast``, so importing one module does not pull in the rest.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_LAZY = {
    "FIELDS_LABELS": "formcast.extract.setup",
    "MAX_SAMPLES": "formcast.extract.setup",
    "STAGES": "formcast.extract.setup",
    "AiPassSummary": "formcast.extract.aipass",
    "AnchorRead": "formcast.extract.model",
    "BoxRead": "formcast.extract.model",
    "BoxRef": "formcast.extract.readers",
    "ChoiceMark": "formcast.extract.model",
    "ChoiceRead": "formcast.extract.model",
    "DocText": "formcast.extract.page",
    "DocumentResult": "formcast.extract.model",
    "Edit": "formcast.extract.edit",
    "ExtractionTemplate": "formcast.extract.model",
    "FieldProof": "formcast.extract.proof",
    "FieldResult": "formcast.extract.model",
    "FieldsAnswer": "formcast.extract.ask",
    "Layout": "formcast.extract.model",
    "Mark": "formcast.extract.proof",
    "OptionProposal": "formcast.extract.readers",
    "MarkAbsent": "formcast.extract.edit",
    "Outcome": "formcast.extract.proof",
    "PhraseRead": "formcast.extract.model",
    "Proof": "formcast.extract.proof",
    "Reader": "formcast.extract.model",
    "ReaderProposal": "formcast.extract.readers",
    "RemoveField": "formcast.extract.edit",
    "Restore": "formcast.extract.edit",
    "SampleDoc": "formcast.extract.samples",
    "SamplePdf": "formcast.extract.samples",
    "SampleProof": "formcast.extract.proof",
    "SampleRecord": "formcast.extract.model",
    "SetBox": "formcast.extract.edit",
    "SetClean": "formcast.extract.edit",
    "SetField": "formcast.extract.edit",
    "SetLabels": "formcast.extract.edit",
    "SetOptionBox": "formcast.extract.edit",
    "SetupInfo": "formcast.extract.model",
    "SetupResult": "formcast.extract.setup",
    "SetupSpec": "formcast.extract.setup",
    "StageEvent": "formcast.extract.setup",
    "StaticLine": "formcast.extract.model",
    "Status": "formcast.extract.model",
    "Strategy": "formcast.extract.model",
    "WidgetRead": "formcast.extract.model",
    "add_line": "formcast.extract.setup",
    "ai_pass": "formcast.extract.aipass",
    "apply_edit": "formcast.extract.edit",
    "build_setup": "formcast.extract.setup",
    "concrete_paths": "formcast.extract.model",
    "detected_pages": "formcast.extract.samples",
    "extract": "formcast.extract.read",
    "fields_from_lines": "formcast.extract.ask",
    "fields_on_form": "formcast.extract.ask",
    "label_key": "formcast.extract.page",
    "learn_maps": "formcast.extract.proof",
    "propose_readers": "formcast.extract.ask",
    "prove": "formcast.extract.proof",
    "read_samples": "formcast.extract.samples",
    "read_values": "formcast.extract.ask",
    "required_paths": "formcast.extract.levels",
    "resolve": "formcast.extract.readers",
    "same_value": "formcast.extract.proof",
    "sample_marks": "formcast.extract.proof",
    "sample_images": "formcast.extract.samples",
    "sample_view": "formcast.extract.samples",
}

if TYPE_CHECKING:
    from formcast.extract.aipass import AiPassSummary, ai_pass
    from formcast.extract.ask import FieldsAnswer, fields_from_lines, fields_on_form, propose_readers, read_values
    from formcast.extract.edit import (
        Edit, MarkAbsent, RemoveField, Restore, SetBox, SetClean, SetField, SetLabels, SetOptionBox, apply_edit,
    )  # fmt: skip
    from formcast.extract.levels import required_paths
    from formcast.extract.model import (
        AnchorRead, BoxRead, ChoiceMark, ChoiceRead, DocumentResult, ExtractionTemplate, FieldResult, Layout,
        PhraseRead, Reader, SampleRecord, SetupInfo, StaticLine, Status, Strategy, WidgetRead, concrete_paths,
    )  # fmt: skip
    from formcast.extract.page import DocText, label_key
    from formcast.extract.proof import (
        FieldProof, Mark, Outcome, Proof, SampleProof, learn_maps, prove, same_value, sample_marks,
    )  # fmt: skip
    from formcast.extract.read import extract
    from formcast.extract.readers import BoxRef, OptionProposal, ReaderProposal, resolve
    from formcast.extract.samples import SampleDoc, SamplePdf, detected_pages, read_samples, sample_images, sample_view
    from formcast.extract.setup import (
        FIELDS_LABELS, MAX_SAMPLES, STAGES, SetupResult, SetupSpec, StageEvent, add_line, build_setup,
    )  # fmt: skip


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'formcast.extract' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
