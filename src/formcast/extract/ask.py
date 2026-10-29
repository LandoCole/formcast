"""The four model calls that build an extraction template from samples, each one JSON answer checked by code.

    fields_from_lines(lines, samples, llm) -> FieldsAnswer        a person's lines made into defined fields
    fields_on_form(schema, samples, llm) -> (paths, usage)        which of a destination's fields this form has
    propose_readers(schema, paths, samples, llm) -> (proposals, usage)   how to read each path on the samples
    read_values(schema, paths, doc, index, llm) -> (values, usage)       one document's answer to each path

Each call sends the system prompt of :mod:`formcast.extract.prompts` and one user message: with ``vision``, page
images (:func:`~formcast.extract.samples.sample_images`) of the first sample with form fields and of the first
without, :data:`~formcast.data.ai.MAX_IMAGE_PAGES` at most in all, each after a ``Sample n, page m:`` line; then one
text of the samples' views (:func:`~formcast.extract.samples.sample_view`, :data:`TEXT_BUDGET` characters shared
evenly) and what is asked. ``read_values`` sends only its own document.

Samples are numbered from 1 in what the model sees (``SAMPLE 1``), and a box the model names says its sample by that
number; :class:`~formcast.extract.readers.BoxRef` counts from 0, so a proposal's box is moved down by one here (a
number that is no sample's then names none, and :func:`~formcast.extract.readers.resolve` drops it with a warning).
Pages are numbered from 0 everywhere, as in the form map.

Every call goes through :func:`formcast.data.ai._ask`: an answer the check refuses is asked for once more with what
was wrong, and a second refusal raises ``FormcastError("the model's answer could not be used: ...")``. ``llm`` is an
:class:`~formcast.llm.LLMConfig` (each call opens and closes its provider) or a ready
:class:`~formcast.llm.Provider` (the caller keeps it; ``propose_readers`` calls it from several threads at once).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from formcast.data.ai import MAX_IMAGE_PAGES, _ask, _schema_lines
from formcast.errors import FormcastError
from formcast.extract.prompts import FIELDS_SYSTEM, ON_FORM_SYSTEM, READERS_SYSTEM, VALUES_SYSTEM
from formcast.extract.readers import BoxRef, ReaderProposal
from formcast.extract.samples import SampleDoc, sample_images, sample_view
from formcast.llm import ImagePart, LLMConfig, Message, Provider, TextPart, Usage
from formcast.schema import flatten_schema, validate_data_located

__all__ = [
    "READER_CHUNK",
    "TEXT_BUDGET",
    "FieldsAnswer",
    "fields_from_lines",
    "fields_on_form",
    "propose_readers",
    "read_values",
]

#: The most paths one readers call asks about; more are sent in several calls, run side by side.
READER_CHUNK = 30
#: The characters of sample views one call sends, shared evenly by its samples.
TEXT_BUDGET = 60_000

#: The headings of what is asked, after the samples' views.
_LINES_HEADING = "LINES:"
_EXISTING_HEADING = "NAMES ALREADY USED:"
_DESTINATION_HEADING = "DESTINATION FIELDS:"
_FIELDS_HEADING = "FIELDS:"
_PATHS_HEADING = "PATHS TO ANSWER:"

Llm = LLMConfig | Provider


@dataclass
class FieldsAnswer:
    """The fields a person's lines became: their schema, each line's paths (``flatten_schema`` form) and the usage."""

    schema: dict
    line_paths: dict[str, list[str]]
    usage: Usage


# ---------------------------------------------------------------- what the model is sent


def _parts(samples: list[tuple[int, SampleDoc]], asked: str, vision: bool) -> list[TextPart | ImagePart]:
    """The user message's parts: page images when ``vision``, then the samples' views and ``asked`` in one text.

    ``samples`` pairs each sample with the number it is shown by.
    """
    parts: list[TextPart | ImagePart] = []
    if vision:
        fillable = next((s for s in samples if _has_widgets(s[1])), None)
        flattened = next((s for s in samples if not _has_widgets(s[1])), None)
        shown = [s for s in (fillable, flattened) if s is not None]
        for number, doc in shown:
            for page, png in sample_images(doc, max_pages=MAX_IMAGE_PAGES // len(shown)):
                parts += [TextPart(f"Sample {number}, page {page}:"), ImagePart(png)]
    share = TEXT_BUDGET // max(len(samples), 1)
    views = "\n\n".join(sample_view(doc, number, max_chars=share) for number, doc in samples)
    parts.append(TextPart(f"{views}\n\n{asked}"))
    return parts


def _has_widgets(doc: SampleDoc) -> bool:
    return any(page.widgets for page in doc.form_map.pages)


def _numbered(samples: list[SampleDoc]) -> list[tuple[int, SampleDoc]]:
    return [(n, doc) for n, doc in enumerate(samples, start=1)]


def _leaves(schema: dict) -> list[str]:
    """The leaf paths of ``schema`` in ``flatten_schema`` form: not an object, not a list of records.

    Raises:
        FormcastError: when ``schema`` cannot be flattened.
    """
    return [
        sp.path for sp in flatten_schema(schema)
        if sp.type != "object" and not (sp.type == "array" and sp.item_type == "object")
    ]  # fmt: skip


def _generic(path: str) -> str:
    """A concrete path in ``flatten_schema`` form: ``rows[1].quantity`` -> ``rows[].quantity``."""
    return re.sub(r"\[\d+\]", "[]", path)


def _asked(schema: dict, paths: list[str]) -> str:
    """The FIELDS lines of the fields ``paths`` come from, then the paths to answer, one per line."""
    wanted = {_generic(p) for p in paths}
    lines = [ln for ln in _schema_lines(schema).splitlines() if ln[2:].split(": ", 1)[0] in wanted]
    return f"{_FIELDS_HEADING}\n" + "\n".join(lines) + f"\n\n{_PATHS_HEADING}\n" + "\n".join(paths) + "\n"


def _messages(system: str, parts: list[TextPart | ImagePart]) -> list[Message]:
    return [Message("system", system), Message("user", parts)]


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(a.input_tokens + b.input_tokens, a.output_tokens + b.output_tokens)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


# ---------------------------------------------------------------- fields from lines


def fields_from_lines(
    lines: list[str], samples: list[SampleDoc], llm: Llm, *, existing: list[str] | None = None, vision: bool = True
) -> FieldsAnswer:
    """Defined fields (a JSON Schema) for a person's ``lines``, designed by the model from the samples.

    The answer is checked: the schema is a valid JSON Schema with at least one field, no top-level name is one of
    ``existing``, and every line has at least one path, each a leaf path of the schema. ``line_paths`` is keyed by
    the lines as given (blank ones and repeats left out).

    Raises:
        FormcastError: when no line has any text, or when the model's answer could not be used twice.
    """
    lines = [line for line in dict.fromkeys(lines) if line.strip()]
    if not lines:
        raise FormcastError("there are no lines to turn into fields")
    existing = list(existing or [])
    by_text = {_key(line): line for line in lines}
    asked = f"{_LINES_HEADING}\n" + "\n".join(f"{n}. {' '.join(line.split())}" for n, line in enumerate(lines, 1))
    if existing:
        asked += f"\n\n{_EXISTING_HEADING} {', '.join(existing)}"

    def check(answer: dict) -> tuple[dict, dict[str, list[str]]]:
        schema = answer.get("schema")
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            raise ValueError('reply as {"schema": {"type": "object", "properties": {...}}, "line_paths": {...}}')
        schema.setdefault("type", "object")
        validate_data_located(schema, {})  # a schema that is not valid JSON Schema raises FormcastError
        leaves = _leaves(schema)
        if not leaves:
            raise ValueError("the schema has no fields")
        taken = [name for name in schema["properties"] if name in existing]
        if taken:
            raise ValueError(f"{', '.join(taken)} is already used; give the new fields new top-level names")
        given = answer.get("line_paths")
        if not isinstance(given, dict):
            raise ValueError('give "line_paths": {"<line>": ["<path>", ...]} for every line')
        line_paths: dict[str, list[str]] = {}
        for key, paths in given.items():
            line = by_text.get(_key(str(key)))
            if line is None:
                raise ValueError(f"line_paths names {key!r}, which is not one of the lines")
            if not _strings(paths) or not paths:
                raise ValueError(f"line_paths of {key!r} must be a list of paths")
            unknown = [p for p in paths if p not in leaves]
            if unknown:
                raise ValueError(f"{', '.join(unknown)} is not a field of the schema (write paths as group[].name)")
            line_paths[line] = list(dict.fromkeys(paths))
        missing = [line for line in lines if line not in line_paths]
        if missing:
            raise ValueError(f"the line {missing[0]!r} has no fields in line_paths")
        return schema, line_paths

    parts = _parts(_numbered(samples), asked, vision)
    (schema, line_paths), usage, _ = _ask(llm, _messages(FIELDS_SYSTEM, parts), check, long=True)
    return FieldsAnswer(schema, line_paths, usage)


def _key(line: str) -> str:
    """A line compared the way a model may write it back: spacing and case do not matter."""
    return " ".join(line.split()).casefold()


# ---------------------------------------------------------------- fields on the form


def fields_on_form(schema: dict, samples: list[SampleDoc], llm: Llm, *, vision: bool = True) -> tuple[list[str], Usage]:
    """The leaf paths of the destination ``schema`` (``flatten_schema`` form) this form has, in the schema's order.

    Raises:
        FormcastError: when ``schema`` cannot be flattened, or when the model's answer could not be used twice.
    """
    leaves = _leaves(schema)
    asked = f"{_DESTINATION_HEADING}\n{_schema_lines(schema)}\n"

    def check(answer: dict) -> list[str]:
        paths = answer.get("on_form")
        if not _strings(paths):
            raise ValueError('reply as {"on_form": ["<path>", ...]}')
        unknown = [p for p in paths if p not in leaves]
        if unknown:
            raise ValueError(f"not fields of the destination: {', '.join(unknown[:10])} (give the paths as listed)")
        return [p for p in leaves if p in set(paths)]

    paths, usage, _ = _ask(llm, _messages(ON_FORM_SYSTEM, _parts(_numbered(samples), asked, vision)), check)
    return paths, usage


# ---------------------------------------------------------------- readers


def propose_readers(
    schema: dict, paths: list[str], samples: list[SampleDoc], llm: Llm, *, vision: bool = True, max_parallel: int = 4
) -> tuple[list[ReaderProposal], Usage]:
    """The model's proposals for reading the concrete ``paths`` of ``schema`` on the samples, in the paths' order.

    The paths go :data:`READER_CHUNK` to a call, ``max_parallel`` calls at a time, and the usage is their sum. A
    proposal for a path its call did not ask about is refused (asked again); one path proposed twice is kept twice,
    for :func:`~formcast.extract.readers.resolve` to warn of.

    Raises:
        FormcastError: when the model's answer to some call could not be used twice.
    """
    chunks = [paths[n : n + READER_CHUNK] for n in range(0, len(paths), READER_CHUNK)]
    if not chunks:
        return [], Usage()
    numbered = _numbered(samples)

    def one(chunk: list[str]) -> tuple[list[ReaderProposal], Usage]:
        allowed = set(chunk)

        def check(answer: dict) -> list[ReaderProposal]:
            given = answer.get("readers")
            if not isinstance(given, list) or not all(isinstance(r, dict) for r in given):
                raise ValueError('reply as {"readers": [{"path": "<path>", ...}, ...]}')
            proposals = [_counted_from_zero(ReaderProposal.model_validate(r)) for r in given]
            unasked = [p.path for p in proposals if p.path not in allowed]
            if unasked:
                raise ValueError(f"not asked for: {', '.join(unasked[:10])}; give readers for the listed paths only")
            return proposals

        parts = _parts(numbered, _asked(schema, chunk), vision)
        proposals, usage, _ = _ask(llm, _messages(READERS_SYSTEM, parts), check, long=True)
        return proposals, usage

    with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(chunks)))) as pool:
        results = list(pool.map(one, chunks))
    total = Usage()
    for _, usage in results:
        total = _add(total, usage)
    return [p for proposals, _ in results for p in proposals], total


def _counted_from_zero(proposal: ReaderProposal) -> ReaderProposal:
    """``proposal`` with each box's sample number (from 1, as the model sees them) counted from 0, as resolve's."""

    def moved(box: BoxRef | None) -> BoxRef | None:
        return None if box is None else box.model_copy(update={"sample": box.sample - 1})

    options = {key: option.model_copy(update={"box": moved(option.box)}) for key, option in proposal.options.items()}
    return proposal.model_copy(update={"box": moved(proposal.box), "options": options})


# ---------------------------------------------------------------- one document's values


def read_values(
    schema: dict, paths: list[str], doc: SampleDoc, index: int, llm: Llm, *, vision: bool = True
) -> tuple[dict[str, Any], Usage]:
    """The model's reading of ``doc`` (shown as sample ``index``) at each of the concrete ``paths`` of ``schema``.

    Every path is a key, ``None`` where the model found no answer; keys the model adds are dropped. The values are
    as the model wrote them, unchecked against the schema.

    Raises:
        FormcastError: when the model's answer could not be used twice.
    """
    if not paths:
        return {}, Usage()

    def check(answer: dict) -> dict[str, Any]:
        values = answer.get("values")
        if not isinstance(values, dict):
            raise ValueError('reply as {"values": {"<path>": <answer or null>, ...}}')
        if values and not any(p in values for p in paths):  # nested as objects, or the paths renamed
            raise ValueError("no key of values is a path asked for; write each path as listed, as one key")
        return {p: values.get(p) for p in paths}

    parts = _parts([(index, doc)], _asked(schema, paths), vision)
    values, usage, _ = _ask(llm, _messages(VALUES_SYSTEM, parts), check, long=True)
    return values, usage
