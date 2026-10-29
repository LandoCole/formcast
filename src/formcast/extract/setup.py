"""Building an extraction template from sample PDFs with a model, in stages, and adding fields from one more line.

    build_setup(samples, spec, llm) -> SetupResult          a template made from 1 to 20 samples, proven on them
    add_line(template, line, samples, llm) -> SetupResult   the fields of one more line added to such a template

:func:`build_setup` runs five stages (:data:`STAGES`) and tells ``on_stage`` as each one starts (``running``) and
ends (``done``, or ``skipped``):

    read      each sample's form map, with no field detection, and the values its form fields hold
    detect    the blanks the field detectors find on the pages the ``detect`` mode picks (skipped for ``off``)
    fields    the schema, by where the field list starts (``spec.start``; its label is :data:`FIELDS_LABELS`'s):
                ai           ``derive_schema`` on the sample with the most boxes (form fields and blanks)
                lines        ``fields_from_lines``: each of the person's lines made into the fields it asks for
                destination  the destination's schema; ``fields_on_form`` says which of its fields the form has,
                             and every other path is marked absent ("not on this form")
    readers   the layout learned from the samples; the model proposes how to read every path that is not
              absent, and ``resolve`` keeps only what the samples have
    check     the model reads every sample (its opinion, kept in the template's samples), the readers are proven
              against those readings, wordings of an enum's answers are learned into maps, and the proof is redone

These two functions (and ``ai_pass``) are the only ones of :mod:`formcast.extract` that call a model; the template
they give reads every later document, and is re-proven after each edit, with no model call. Every call goes
through :func:`formcast.data.ai._ask`. Given an :class:`~formcast.llm.LLMConfig`, a build opens one provider for
all its calls (the calls of a stage run side by side, ``max_parallel`` at a time, and share it) and closes it at
the end; a ready :class:`~formcast.llm.Provider` is the caller's to close.

Warnings are sentences for a person: what ``resolve`` left out, and ``"<path>: the model proposed no way to read
it"`` for a path the model gave no proposal for. Such a path gets no reader, so it is not ready.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from formcast.data.ai import derive_schema
from formcast.errors import FormcastError
from formcast.extract.ask import _generic, fields_from_lines, fields_on_form, propose_readers, read_values
from formcast.extract.layout import learn_layout
from formcast.extract.model import ExtractionTemplate, SampleRecord, SetupInfo, concrete_paths
from formcast.extract.proof import Proof, _check_samples, learn_maps, prove
from formcast.extract.readers import ReaderProposal, resolve
from formcast.extract.samples import Detect, SampleDoc, SamplePdf, _detect, _read
from formcast.llm import LLMConfig, Provider, Usage, make_provider
from formcast.pagemap.build import DetectPolicy, PageProgress
from formcast.pagemap.detect import Detector
from formcast.template.model import form_info_for

__all__ = [
    "FIELDS_LABELS",
    "MAX_SAMPLES",
    "STAGES",
    "SetupResult",
    "SetupSpec",
    "StageEvent",
    "add_line",
    "build_setup",
]

#: The stages of a build, in order: (key, label).
STAGES: tuple[tuple[str, str], ...] = (
    ("read", "Read the sample"),
    ("detect", "Find the blanks"),
    ("fields", "Design the fields"),
    ("readers", "Find each field on the sample"),
    ("check", "Check the reads"),
)
#: The ``fields`` stage's label for each start.
FIELDS_LABELS = {
    "ai": "Design the fields",
    "lines": "Turn the lines into fields",
    "destination": "Match the destination's fields",
}
#: The most samples a template is built from.
MAX_SAMPLES = 20

Llm = LLMConfig | Provider


@dataclass(frozen=True)
class StageEvent:
    """A stage (a key of :data:`STAGES`) starting (``running``), ending (``done``) or ``skipped``; ``detail`` says
    what it is doing or what it gave ("5 samples read", "14 of 17 fields ready")."""

    key: str
    status: Literal["running", "done", "skipped"]
    detail: str = ""


class SetupSpec(BaseModel):
    """What to build: the template's name (and id, else made from the name), where its fields start, how the
    samples are read (``detect``, ``pages`` and ``ocr`` as for :func:`~formcast.extract.samples.read_samples`),
    whether the model sees page images (``vision``) and, in the person's words, how the samples were chosen.

    ``lines`` are the person's lines for ``start="lines"``; ``destination`` is the destination's JSON Schema for
    ``start="destination"`` and ``destination_name`` what to call it in the template's setup.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    template_id: str | None = None
    start: Literal["ai", "lines", "destination"] = "ai"
    lines: list[str] = Field(default_factory=list)
    destination: dict[str, Any] | None = None
    destination_name: str | None = None
    detect: Detect = "auto"
    pages: dict[int, list[int]] = Field(default_factory=dict)
    ocr: Literal["auto", "never"] = "auto"
    vision: bool = True
    sample_how: str = ""

    @model_validator(mode="after")
    def _complete(self) -> SetupSpec:
        if not self.name.strip():
            raise ValueError("a template needs a name")
        if self.start == "lines" and not any(line.strip() for line in self.lines):
            raise ValueError("start 'lines' needs at least one line saying what to extract")
        if self.start == "destination" and self.destination is None:
            raise ValueError("start 'destination' needs the destination's schema")
        return self


@dataclass
class SetupResult:
    """A built template proven on its samples (``samples``: the samples as read), the usage of every model call
    made, and the warnings."""

    template: ExtractionTemplate
    proof: Proof
    samples: list[SampleDoc]
    usage: Usage
    warnings: list[str]


# ---------------------------------------------------------------- build_setup


def build_setup(
    samples: list[SamplePdf],
    spec: SetupSpec,
    llm: Llm,
    *,
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy | None = None,
    on_stage: Callable[[StageEvent], None] | None = None,
    on_page: Callable[[int, PageProgress], None] | None = None,
    max_parallel: int = 4,
) -> SetupResult:
    """A template built from ``samples`` in the stages of the module docstring, proven on them.

    ``detectors`` and ``policy`` are as for :func:`~formcast.extract.samples.read_samples`, and ``on_page(i,
    progress)`` hears of each page read in sample ``i``. ``on_stage`` is called before and after each stage; an
    exception it raises (such as :class:`~formcast.errors.SessionCancelled`) stops the build and propagates. The
    samples are read before the first model call, so a sample that cannot be read costs none.

    Raises:
        FormcastError: for no samples or more than :data:`MAX_SAMPLES`, a destination schema that cannot be
            flattened, a sample that cannot be read (naming its file), or a model answer that could not be used.
    """
    if not samples or len(samples) > MAX_SAMPLES:
        raise FormcastError(f"a template is built from 1 to {MAX_SAMPLES} samples; {len(samples)} were given")
    if spec.destination is not None:
        concrete_paths(spec.destination)  # a schema that cannot be flattened fails before any work
    emit = _emitter(on_stage)
    usage = Usage()
    with _shared(llm) as provider:
        emit("read", "running", _label("read"))
        docs = _read(samples, ocr=spec.ocr, detectors=detectors, policy=policy, on_page=on_page)
        emit("read", "done", _count(len(docs), "sample") + " read")

        if spec.detect == "off":
            emit("detect", "skipped", "field detection is off")
        else:
            emit("detect", "running", _label("detect"))
            docs = _detect(docs, detect=spec.detect, pages=spec.pages, detectors=detectors, policy=policy)
            found = sum(len(p.candidates) for d in docs for p in d.form_map.pages)
            emit("detect", "done", _count(found, "blank") + " found")

        emit("fields", "running", FIELDS_LABELS[spec.start])
        schema, absent, line_paths, used, warnings = _fields(spec, docs, provider)
        usage = _sum(usage, used)
        not_on_form = set(absent)
        paths = [p for p in concrete_paths(schema) if p not in not_on_form]
        emit("fields", "done", _count(len(paths), "field") + (f", {len(absent)} not on this form" if absent else ""))

        emit("readers", "running", _label("readers"))
        layout = learn_layout([d.pdf for d in docs])
        form = form_info_for(next((d.form_map for d in docs if _has_widgets(d)), docs[0].form_map))
        proposals, used = propose_readers(schema, paths, docs, provider, vision=spec.vision, max_parallel=max_parallel)
        usage = _sum(usage, used)
        readers, dropped = resolve(proposals, docs, layout, schema)
        warnings += dropped + _unproposed(paths, proposals)
        emit("readers", "done", f"readers for {len(readers)} of {_count(len(paths), 'field')}")

        emit("check", "running", _label("check"))
        opinions, used = _opinions(schema, paths, docs, provider, spec.vision, max_parallel)
        usage = _sum(usage, used)
        setup = SetupInfo(
            start=spec.start, lines=list(line_paths), line_paths=line_paths,
            destination=spec.destination_name if spec.start == "destination" else None, detect=spec.detect,
            sample_how=spec.sample_how,
        )  # fmt: skip
        template = ExtractionTemplate(
            template_id=spec.template_id or _template_id(spec.name), name=spec.name, schema=schema, form=form,
            layout=layout, readers=readers, absent=absent, setup=setup, samples=_records(docs, opinions),
        )  # fmt: skip
        template, proof = _proven(template, docs)
        emit("check", "done", f"{proof.ready} of {proof.total} fields ready")
    return SetupResult(template, proof, docs, usage, warnings)


def _fields(
    spec: SetupSpec, docs: list[SampleDoc], llm: Llm
) -> tuple[dict, list[str], dict[str, list[str]], Usage, list[str]]:
    """The ``fields`` stage: (schema, absent paths, each line's paths, usage, warnings)."""
    if spec.start == "ai":
        doc = max(docs, key=_boxes)  # the first of the samples with the most boxes
        derived = derive_schema(doc.pdf, llm, form_map=doc.form_map, vision=spec.vision)
        return derived.schema, [], {}, derived.usage, []
    if spec.start == "lines":
        answer = fields_from_lines(spec.lines, docs, llm, vision=spec.vision)
        given = [line for line in dict.fromkeys(spec.lines) if line in answer.line_paths]
        return answer.schema, [], {line: answer.line_paths[line] for line in given}, answer.usage, []
    schema = copy.deepcopy(spec.destination or {})
    on_form, usage = fields_on_form(schema, docs, llm, vision=spec.vision)
    on = set(on_form)
    absent = [p for p in concrete_paths(schema) if _generic(p) not in on]
    warnings = [] if on_form else ["the model found none of the destination's fields on the form"]
    return schema, absent, {}, usage, warnings


# ---------------------------------------------------------------- add_line


def add_line(
    template: ExtractionTemplate,
    line: str,
    samples: list[SampleDoc],
    llm: Llm,
    *,
    vision: bool = True,
    max_parallel: int = 4,
) -> SetupResult:
    """``template`` with the fields of one more ``line``: their schema, readers and readings, then proven again.

    ``samples`` are the template's samples as read (a :class:`SetupResult`'s), in its order. The new fields get
    new top-level names; the model proposes readers and reads each sample for their paths only, and the line is
    recorded in the template's setup with the paths it became. Calls share one provider, as in
    :func:`build_setup`.

    Raises:
        FormcastError: for a template not built from samples, ``samples`` that are not its samples, a line with
            no text, a field definition that clashes with the schema's, or a model answer that could not be used.
    """
    if template.setup is None:
        raise FormcastError("fields are added from a line only to a template built from samples")
    _check_samples(template, [d.pdf for d in samples])  # before any model call
    with _shared(llm) as provider:
        existing = list(template.schema_.get("properties") or {})
        answer = fields_from_lines([line], samples, provider, existing=existing, vision=vision)
        schema = _merged(template.schema_, answer.schema)
        old = set(concrete_paths(template.schema_))
        paths = [p for p in concrete_paths(schema) if p not in old]
        proposals, used = propose_readers(schema, paths, samples, provider, vision=vision, max_parallel=max_parallel)
        usage = _sum(answer.usage, used)
        readers, warnings = resolve(proposals, samples, template.layout, schema)
        warnings += _unproposed(paths, proposals)
        opinions, used = _opinions(schema, paths, samples, provider, vision, max_parallel)
        usage = _sum(usage, used)
    setup = template.setup.model_copy(deep=True)
    if line not in setup.lines:
        setup.lines.append(line)
    setup.line_paths[line] = list(dict.fromkeys([*setup.line_paths.get(line, []), *answer.line_paths[line]]))
    records = [
        record.model_copy(update={"opinion": {**record.opinion, **opinion}})
        for record, opinion in zip(template.samples, opinions)
    ]
    built = ExtractionTemplate.model_validate(
        {
            **template.model_dump(by_alias=True),
            "schema": schema,
            "readers": {**template.readers, **readers},
            "setup": setup,
            "samples": records,
        }
    )
    built, proof = _proven(built, samples)
    return SetupResult(built, proof, samples, usage, warnings)


def _merged(schema: dict, added: dict) -> dict:
    """``schema`` with ``added``'s top-level properties, ``required`` names and ``$defs`` put in.

    Raises:
        FormcastError: when a property or a ``$defs`` entry of ``added`` has a name ``schema`` already uses.
    """
    out = copy.deepcopy(schema)
    for key in ("properties", "$defs"):
        new = added.get(key) or {}
        if not new:
            continue
        have = out.setdefault(key, {})
        for name, node in new.items():
            if name in have:
                raise FormcastError(f"the new field {name!r} has a name the schema already uses")
            have[name] = copy.deepcopy(node)
    required = list(out.get("required") or [])
    required += [name for name in added.get("required") or [] if name not in required]
    if required:
        out["required"] = required
    return out


# ---------------------------------------------------------------- shared steps


def _opinions(
    schema: dict, paths: list[str], docs: list[SampleDoc], llm: Llm, vision: bool, max_parallel: int
) -> tuple[list[dict[str, Any]], Usage]:
    """The model's reading of ``paths`` on each sample (shown as sample 1, 2, ...), ``max_parallel`` at a time."""

    def one(numbered: tuple[int, SampleDoc]) -> tuple[dict[str, Any], Usage]:
        number, doc = numbered
        return read_values(schema, paths, doc, number, llm, vision=vision)

    with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(docs)))) as pool:
        results = list(pool.map(one, enumerate(docs, start=1)))
    usage = Usage()
    for _, used in results:
        usage = _sum(usage, used)
    return [values for values, _ in results], usage


def _records(docs: list[SampleDoc], opinions: list[dict[str, Any]]) -> list[SampleRecord]:
    return [
        SampleRecord(file=doc.file, sha256=hashlib.sha256(doc.pdf).hexdigest(), opinion=opinion)
        for doc, opinion in zip(docs, opinions)
    ]


def _proven(template: ExtractionTemplate, docs: list[SampleDoc]) -> tuple[ExtractionTemplate, Proof]:
    """``template`` with the maps the proof teaches (:func:`~formcast.extract.proof.learn_maps`), and its proof."""
    pdfs = [d.pdf for d in docs]
    proof = prove(template, pdfs)
    learned = learn_maps(template, proof)
    if learned != template:
        proof = prove(learned, pdfs)
    return learned, proof


def _unproposed(paths: list[str], proposals: list[ReaderProposal]) -> list[str]:
    proposed = {p.path for p in proposals}
    return [f"{path}: the model proposed no way to read it" for path in paths if path not in proposed]


@contextmanager
def _shared(llm: Llm) -> Iterator[Provider]:
    """One provider for every call of a build: made from an ``LLMConfig`` (and closed after), or the one given."""
    if not isinstance(llm, LLMConfig):
        yield llm
        return
    provider = make_provider(llm)
    try:
        yield provider
    finally:
        provider.close()


def _emitter(on_stage: Callable[[StageEvent], None] | None) -> Callable[..., None]:
    def emit(key: str, status: Literal["running", "done", "skipped"], detail: str = "") -> None:
        if on_stage is not None:
            on_stage(StageEvent(key, status, detail))

    return emit


def _label(key: str) -> str:
    return dict(STAGES)[key]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _sum(a: Usage, b: Usage) -> Usage:
    return Usage(a.input_tokens + b.input_tokens, a.output_tokens + b.output_tokens)


def _has_widgets(doc: SampleDoc) -> bool:
    return any(page.widgets for page in doc.form_map.pages)


def _boxes(doc: SampleDoc) -> int:
    return sum(len(page.widgets) + len(page.candidates) for page in doc.form_map.pages)


def _template_id(name: str) -> str:
    """``name`` in lower snake case, letters and digits only: "Client info (2024)" -> ``client_info_2024``."""
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "template"
