"""A person's message about an extraction template made into edits of it by one model call, proven before they see it.

    propose_fix(template, samples, message, llm, *, area=None, history=(), vision=True) -> ChatAnswer

The model is sent :data:`~formcast.extract.prompts.CHAT_SYSTEM` and one user message: with ``vision``, at most two
page images, each after a ``Sample n, page m:`` line and drawn with the template's boxes (each tagged with its path):
the page of the person's ``area`` first, then the pages holding the most fields that are not ready; then one text of
the samples' views (:func:`~formcast.extract.samples.sample_view`, :data:`~formcast.extract.ask.TEXT_BUDGET` shared),
``TEMPLATE:`` (:func:`_template_lines`), ``AREA:`` (the person's area and the text lines inside it, with their ids),
``EARLIER:`` (the ``history`` turns, oldest first) and ``MESSAGE:``.

It answers ``{"reply": "...", "edits": [...]}``, the edits of :mod:`formcast.extract.edit`, where a ``set_box`` or
``set_option_box`` gives its box as ``"line": "t0_12"`` (or ``"t0_12+t0_13"``) with ``"sample"``, or as
``"area": true``, never as a rectangle: the model points at what the samples have, it does not make up coordinates.
The check turns a line into the answer's cell (:meth:`~formcast.extract.readers._Samples.text_box`, as a build reads a
text line) and the area into its rectangle, both in the layout's place with no ``sample``; validates the edits; makes
them in order on the template (:func:`~formcast.extract.edit.apply_edit`), refusing the answer at the first one that
cannot be made, so the model is asked once more with what was wrong; and proves the result
(:func:`~formcast.extract.proof.prove`). The template given is never changed.

Samples are numbered from 1 in what the model sees and writes (``SAMPLE 1``, ``"sample": 1``), as in
:mod:`formcast.extract.ask`; the edits returned, and :class:`ChatArea`, count them from 0, as the template does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from formcast.data.ai import _ask
from formcast.data.mapping import _generic
from formcast.errors import FormcastError
from formcast.extract.ask import Llm, _checks, _messages, _numbered, _parts
from formcast.extract.edit import Edit, apply_edit
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    ExtractionTemplate,
    PhraseRead,
    Reader,
    WidgetRead,
    concrete_paths,
)
from formcast.extract.page import DocText
from formcast.extract.proof import FieldProof, Proof, SampleProof, prove, sample_marks
from formcast.extract.prompts import CHAT_SYSTEM
from formcast.extract.readers import BoxRef, _Samples
from formcast.extract.samples import SampleDoc, text_lines
from formcast.geometry import Rect
from formcast.llm import ImagePart, TextPart, Usage
from formcast.pagepass.marks import render_marked_page
from formcast.schema import flatten_schema, resolve
from formcast.template.model import Field

__all__ = ["MAX_HISTORY", "TEMPLATE_CHARS", "ChatAnswer", "ChatArea", "ChatTurn", "propose_fix"]

#: The most earlier turns a message may carry.
MAX_HISTORY = 3
#: The characters of TEMPLATE lines one call sends; past it the lines of ready paths are cut first.
TEMPLATE_CHARS = 25_000
#: The most page images one call sends.
CHAT_IMAGES = 2
#: The longest value a TEMPLATE line shows, in characters of its JSON.
_VALUE_CHARS = 60

_EDITS = TypeAdapter(list[Edit])


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatArea(_Model):
    """A place the person marked: on the template's ``sample`` (its position from 0), ``page`` (from 0), ``rect`` in
    that sample page's points (the frame :func:`~formcast.extract.proof.sample_marks` gives)."""

    sample: int
    page: int
    rect: tuple[float, float, float, float]


class ChatTurn(_Model):
    """One earlier exchange: the person's ``message``, the ``reply``, the ``changes`` it proposed in words, and what
    became of them."""

    message: str
    reply: str
    changes: str
    outcome: Literal["applied", "discarded", "pending"]


@dataclass
class ChatAnswer:
    """The model's ``reply``, its ``edits`` (rectangles in the layout's place, samples from 0), the proof of every
    path an edit names or makes on the edited template, and the usage."""

    reply: str
    edits: list[Edit]
    preview: dict[str, FieldProof]
    usage: Usage


def propose_fix(
    template: ExtractionTemplate,
    samples: list[SampleDoc],
    message: str,
    llm: Llm,
    *,
    area: ChatArea | None = None,
    history: Sequence[ChatTurn] = (),
    vision: bool = True,
) -> ChatAnswer:
    """The edits the model proposes for ``message``, made on a copy of ``template`` and proven on ``samples``.

    ``samples`` are the template's samples, read, in its order. See the module docstring.

    Raises:
        FormcastError: more than :data:`MAX_HISTORY` turns, a message with no words, no samples, an ``area`` on a
            sample or page the template's samples do not have, ``samples`` that are not the template's, or an answer
            the model could not make usable in two tries.
    """
    if len(history) > MAX_HISTORY:
        raise FormcastError(f"at most {MAX_HISTORY} earlier turns can be sent, not {len(history)}")
    if not message.strip():
        raise FormcastError("the message has no words")
    if not samples:
        raise FormcastError("the template has no samples to prove edits on")
    if area is not None:
        if not 0 <= area.sample < len(samples):
            raise FormcastError(f"the area is on sample {area.sample}, which the template does not have")
        if not 0 <= area.page < len(samples[area.sample].form_map.pages):
            raise FormcastError(f"the area is on page {area.page}, which sample {area.sample} does not have")
    pdfs = [doc.pdf for doc in samples]
    proof = prove(template, pdfs)  # also refuses samples that are not the template's

    with ExitStack() as stack:
        texts = [stack.enter_context(DocText(doc.pdf)) for doc in samples]
        found = _Samples(samples, texts, template.layout)
        asked = "\n\n".join(
            [
                "TEMPLATE:\n" + "\n".join(_template_lines(template, proof)),
                *([_area_block(area, samples[area.sample], texts[area.sample])] if area is not None else []),
                *([_earlier(history)] if history else []),
                f"MESSAGE:\n{message.strip()}",
            ]
        )
        parts = _parts(_numbered(samples), asked, vision=False)
        if vision:
            parts = _images(template, proof, samples, area) + parts

        def check(answer: dict) -> tuple[str, list[Edit], dict[str, FieldProof]]:
            reply, given = answer.get("reply"), answer.get("edits")
            if not isinstance(reply, str) or not isinstance(given, list) or not all(isinstance(e, dict) for e in given):
                raise ValueError('reply as {"reply": "<a sentence or two>", "edits": [{"op": ..., "path": ...}, ...]}')
            placed = [_placed(e, n, found, area) for n, e in enumerate(given, start=1)]
            try:
                edits = _EDITS.validate_python(placed)
            except ValidationError as exc:
                raise ValueError(_invalid(exc)) from None
            edited = template
            for n, edit in enumerate(edits, start=1):
                try:
                    edited = apply_edit(edited, edit, pdfs=pdfs)
                except (FormcastError, ValueError) as exc:  # a ValidationError of the edited template is a ValueError
                    problem = str(exc).splitlines()[0]
                    raise ValueError(f"edit {n} ({edit.op} {edit.path}) cannot be made: {problem}") from None
            if not edits:
                return reply.strip(), [], {}
            made = set(concrete_paths(edited.schema_)) - set(concrete_paths(template.schema_))  # added, renamed
            named = {e.path for e in edits} | made
            preview = {f.path: f for f in prove(edited, pdfs).fields if f.path in named}
            return reply.strip(), edits, preview

        (reply, edits, preview), usage, _ = _ask(llm, _messages(CHAT_SYSTEM, parts), check, long=True)
    return ChatAnswer(reply, edits, preview, usage)


# ---------------------------------------------------------------- the answer's boxes


def _placed(edit: dict, n: int, found: _Samples, area: ChatArea | None) -> dict:
    """The model's ``n``-th edit as :mod:`~formcast.extract.edit` takes it: its sample counted from 0, and a box given
    by ``line`` or ``area`` made a rectangle in the layout's place.

    Raises:
        ValueError: saying what to give instead, for a sample that is none, a ``rect``, an ``area`` the person did not
            mark, or a ``line`` that names no text line of its sample.
    """
    edit = dict(edit)
    count = len(found.docs)
    if "sample" in edit:
        number = edit["sample"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= count:
            raise ValueError(f"edit {n}: sample {number!r} is no sample; they are numbered 1 to {count}")
        edit["sample"] = number - 1
    if edit.get("op") not in ("set_box", "set_option_box"):
        return edit
    if "rect" in edit:
        raise ValueError(f'edit {n}: give the box by its text line ("line", "sample") or "area": true, never by a rect')
    if edit.pop("area", False):
        if area is None:
            raise ValueError(f'edit {n}: "area": true, but the person marked no area; give the box by its text line')
        page, rect = found._in_layout(area.sample, area.page, area.rect)
        edit.pop("line", None)
    elif "line" in edit:
        line, sample = str(edit.pop("line")), edit.get("sample")
        if sample is None:
            raise ValueError(f'edit {n}: a "line" needs the "sample" it is on')
        place = found.text_box(BoxRef(sample=sample, id=line))
        if place is None:
            raise ValueError(
                f"edit {n}: {line} is no text line on one page of sample {sample + 1}; a page with form fields has "
                'none, so give "area": true there (the person marks one)'
            )
        page, rect = place
    else:
        raise ValueError(f'edit {n}: give the box as "line" with "sample", or as "area": true')
    edit.pop("sample", None)
    return {**edit, "page": page, "rect": rect}


def _invalid(exc: ValidationError) -> str:
    """The first problem of the edits' validation, naming the edit (from 1) and the key."""
    error = exc.errors()[0]
    where = [str(part) for part in error["loc"]]
    head = f"edit {int(where[0]) + 1}" if where and where[0].isdigit() else "edits"
    keys = [part for part in where[1:] if part not in _OPS]
    return f"{head}{': ' + '.'.join(keys) if keys else ''}: {error['msg']}"


#: The ops, as they appear in a validation error's location.
_OPS = {
    "set_field", "set_box", "set_option_box", "set_labels", "set_clean", "mark_absent", "restore", "remove_field",
    "rename_field", "set_category", "make_option_set", "split_option_set", "add_field", "set_reading",
}  # fmt: skip


# ---------------------------------------------------------------- what the model is sent


def _template_lines(template: ExtractionTemplate, proof: Proof, *, budget: int = TEMPLATE_CHARS) -> list[str]:
    """The TEMPLATE lines: one per concrete path, in schema order, but a repeated group's rows that have no reader
    share one per field (``owners[2..3].name: no reader``).

    Each line: the path, ``ready`` or ``NOT READY`` (or ``absent``), the field's title, format and level, its type and
    category, how it is read, and per sample (``S1`` ...) the proof's outcome with the value read and the model's
    reading. When the lines are longer than ``budget`` characters (a newline after each), ready lines are dropped from
    the last up, then the others, and a last line says how many were left out.
    """
    schema = template.schema_
    schema_paths = flatten_schema(schema)
    proofs = {f.path: f for f in proof.fields}
    lines: list[tuple[str, bool]] = []  # (line, ready)
    unread: dict[str, list[int]] = {}  # a repeated field's generic path -> its rows with no reader
    slots: dict[str, int] = {}  # where in ``lines`` a repeated field's "no reader" line goes
    for path in concrete_paths(schema):
        generic, rows = _generic(path)
        fp = proofs.get(path)
        if rows and path not in template.readers and path not in template.absent:
            if generic not in unread:
                slots[generic] = len(lines)
                lines.append(("", False))
            unread.setdefault(generic, []).extend(rows[-1:])
            continue
        lines.append((_path_line(template, path, generic, schema_paths, fp), bool(fp and fp.ready)))
    for generic, rows in unread.items():
        lines[slots[generic]] = (f"{_rows(generic, rows)}: no reader", False)

    shown = [line for line, _ in lines]
    if sum(len(line) + 1 for line in shown) <= budget:
        return shown
    kept, size = set(range(len(lines))), sum(len(line) + 1 for line in shown)
    for n in sorted(kept, key=lambda n: (not lines[n][1], -n)):  # the ready lines from the last up, then the others
        kept.discard(n)
        size -= len(lines[n][0]) + 1
        note = f"[{len(lines) - len(kept)} more paths left out]"
        if size + len(note) + 1 <= budget:
            break
    return [lines[n][0] for n in sorted(kept)] + [note]


def _path_line(template: ExtractionTemplate, path: str, generic: str, schema_paths: list, fp: FieldProof | None) -> str:
    """One path's TEMPLATE line (see :func:`_template_lines`)."""
    absent = path in template.absent
    state = "absent" if absent else "ready" if fp is not None and fp.ready else "NOT READY"
    head = f"{path} {state}{_checks(template.schema_, generic)} {_type(schema_paths, generic)}"
    top = path.split(".", 1)[0] if "." in path and "[" not in path.split(".", 1)[0] else None
    group = (template.schema_.get("properties") or {}).get(top) if top else None
    if isinstance(group, dict) and group.get("type") == "object":
        head += f", in {json.dumps(group.get('title') or top, ensure_ascii=False)}"
    reader = template.readers.get(path)
    how = _how(reader) if reader is not None else "no reader"
    if absent or fp is None:
        return f"{head} | {how}"
    seen = "; ".join(_outcome(n, s) for n, s in enumerate(fp.samples, start=1))
    return f"{head} | {how} | {seen}"


def _type(schema_paths: list, generic: str) -> str:
    """The field's type as a person picks it (``text``, ``date``, ``yes/no``), or its answers for a fixed set."""
    try:
        sp = resolve(schema_paths, generic)
    except KeyError:
        return "text"
    if sp.type == "enum" and sp.enum:
        return "one of " + "|".join(sp.enum)
    return {"string": "text", "boolean": "yes/no", "array": "list"}.get(sp.type, sp.type)


def _how(reader: Reader) -> str:
    """How ``reader`` reads, its strategies in order, its clean steps and how many further places it reads."""
    ways = []
    for s in reader.strategies:
        if isinstance(s, WidgetRead):
            ways.append("form field " + ", ".join(s.names) + (f" p{s.page}" if s.page is not None else ""))
        elif isinstance(s, BoxRead):
            ways.append(f"box p{s.page} {_rect(s.rect)}")
        elif isinstance(s, AnchorRead):
            where = f" p{s.page}" if s.page is not None else ""
            ways.append(f"labels {'/'.join(json.dumps(x, ensure_ascii=False) for x in s.labels)} {s.side}{where}")
        elif isinstance(s, PhraseRead):
            ways.append(f"phrases {'/'.join(json.dumps(x, ensure_ascii=False) for x in s.phrases)} stop {s.stop}")
        elif isinstance(s, ChoiceRead):
            marks = [f"{key}={_mark(m)}" for key, m in s.options.items()]
            ways.append("options " + ", ".join(marks))
    if reader.clean:
        steps = [t.model_dump(exclude_defaults=True) for t in reader.clean]
        ways.append("clean " + json.dumps(steps, ensure_ascii=False))
    if reader.places:
        ways.append(f"{len(reader.places)} more places")
    return "; ".join(ways) or "no strategy"


def _mark(mark: ChoiceMark) -> str:
    """Where one answer's check mark is: ``type_trust``, ``type/joint`` (a radio button), ``p0 [380,230,392,242]``."""
    if mark.widget is not None:
        return mark.widget + (f"/{mark.state}" if mark.state else "")
    return f"p{mark.page} {_rect(mark.rect)}"


def _outcome(number: int, sample: SampleProof) -> str:
    """One sample's side of a path's proof: ``S2 agree "value"``, ``S1 disagree: read "x", yours "y"``."""
    read = sample.read.value if sample.read.value is not None else sample.read.raw
    if sample.outcome == "disagree":
        return f"S{number} disagree: read {_value(read)}, yours {_value(sample.opinion)}"
    if sample.outcome in ("agree", "failed_check"):
        return f"S{number} {sample.outcome} {_value(read)}"
    return f"S{number} {sample.outcome}"


def _value(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _VALUE_CHARS else text[: _VALUE_CHARS - 2] + '…"'


def _rect(rect: Rect | None) -> str:
    return "[" + ",".join(str(round(v)) for v in rect or ()) + "]"


def _rows(generic: str, rows: list[int]) -> str:
    """``owners[].name`` with rows 2, 3 and 5: ``owners[2..3,5].name``."""
    runs: list[list[int]] = []
    for row in sorted(set(rows)):
        if runs and row == runs[-1][1] + 1:
            runs[-1][1] = row
        else:
            runs.append([row, row])
    shown = ",".join(f"{a}..{b}" if b > a else str(a) for a, b in runs)
    return generic.replace("[]", f"[{shown}]", 1) if generic.count("[]") == 1 else re.sub(r"\[\]", "[*]", generic)


def _area_block(area: ChatArea, doc: SampleDoc, text: DocText) -> str:
    """``AREA:``: the area's sample (from 1), page and rectangle, and the text lines whose middle is inside it."""
    x0, y0, x1, y1 = area.rect
    inside = [
        f"t{area.page}_{n} {_rect(rect)} {line}"
        for n, (line, rect) in enumerate(text_lines(doc, text, area.page))
        if x0 <= (rect[0] + rect[2]) / 2 <= x1 and y0 <= (rect[1] + rect[3]) / 2 <= y1
    ]  # fmt: skip
    head = f"AREA: sample {area.sample + 1}, page {area.page}, {_rect(area.rect)}"
    return "\n".join([head, *(inside or ["(no text lines inside it)"])])


def _earlier(history: Sequence[ChatTurn]) -> str:
    """``EARLIER:``: each turn, oldest first, with what became of its changes."""
    turns = [
        f"{n}. Person: {t.message}\n   You: {t.reply}\n   Changes ({t.outcome}): {t.changes}"
        for n, t in enumerate(history, start=1)
    ]  # fmt: skip
    return "EARLIER:\n" + "\n".join(turns)


def _images(
    template: ExtractionTemplate, proof: Proof, samples: list[SampleDoc], area: ChatArea | None
) -> list[TextPart | ImagePart]:
    """At most :data:`CHAT_IMAGES` pages, each after its ``Sample n, page m:`` line, drawn with the template's boxes.

    The area's page comes first; then the pages holding the most paths that are not ready, each on the sample where
    most of them are not proven (the first sample on a tie); with neither, the first sample's first page.
    """
    shown: list[tuple[int, int]] = [(area.sample, area.page)] if area is not None else []
    by_page: dict[int, int] = {}
    by_sample: dict[tuple[int, int], int] = {}
    for fp in proof.fields:
        if fp.ready or fp.absent:
            continue
        page = _page_of(fp, template.readers.get(fp.path))
        if page is None:
            continue
        by_page[page] = by_page.get(page, 0) + 1
        for n, s in enumerate(fp.samples):
            if s.outcome not in ("agree", "failed_check") and page < len(samples[n].form_map.pages):
                by_sample[n, page] = by_sample.get((n, page), 0) + 1
    for page in sorted(by_page, key=lambda p: (-by_page[p], p)):
        if len(shown) >= CHAT_IMAGES:
            break
        if all(p != page for _, p in shown):
            candidates = [n for n in range(len(samples)) if page < len(samples[n].form_map.pages)]
            if candidates:
                shown.append((max(candidates, key=lambda n: (by_sample.get((n, page), 0), -n)), page))
    if not shown:
        shown = [(0, 0)]
    parts: list[TextPart | ImagePart] = []
    for sample, page in shown:
        boxes = _boxes(template, samples[sample].pdf, page)
        png = render_marked_page(samples[sample].pdf, page, boxes)
        parts += [TextPart(f"Sample {sample + 1}, page {page}:"), ImagePart(png)]
    return parts


def _page_of(fp: FieldProof, reader: Reader | None) -> int | None:
    """The page a path is read on: where a sample's read was, else its reader's first strategy with a page."""
    for s in fp.samples:
        if s.read.page is not None:
            return s.read.page
    for strategy in reader.strategies if reader is not None else []:
        if isinstance(strategy, (BoxRead, AnchorRead, PhraseRead, WidgetRead)) and strategy.page is not None:
            return strategy.page
        if isinstance(strategy, ChoiceRead):
            pages = [m.page for m in strategy.options.values() if m.page is not None]
            if pages:
                return pages[0]
    return None


def _boxes(template: ExtractionTemplate, pdf: bytes, page: int) -> list[Field]:
    """The template's boxes and check-mark boxes on one sample's ``page``, where they are on it, tagged by path."""
    boxes = []
    for mark in sample_marks(template, pdf):
        if mark.page == page and mark.kind in ("box", "option"):
            tag = mark.path if mark.answer is None else f"{mark.path}={mark.answer}"
            kind = "text" if mark.kind == "box" else "checkbox"
            boxes.append(Field(id=f"m_{tag}", type=kind, page=page, rect=mark.rect))
    return boxes
