"""A datum the form writes in many places is one field: fields that repeat each other are merged, and filled form
fields that no field reads are placed by the model.

    merge_repeats(schema, readers, line_paths) -> Merged        fields reading the very same form field made one
    unread_widgets(docs, readers) -> list[Unread]               filled text form fields no reader reads
    place_unread(schema, paths, unread, llm) -> (answers, usage)    the model: a place of a field, a new field, or skip
    apply_answers(schema, readers, unread, answers) -> Placed       the answers made into places, fields and warnings
    fold_repeats(template, proof) -> (template, warnings)           a field that failed folded into the one it repeats

A long agreement writes the subscriber's name on every signature page and its date on every form. The setup's
model may make one field per section for it; two fields whose first strategy reads the same form field (the same
names, on the same page or on every page) are one datum, so :func:`merge_repeats` keeps the first in schema order and
drops the others from the schema, their places going to it. Only plain fields (not rows of a repeated group) of the
same type are merged.

After the readers are written, a filled form field that no reader reads (by its strategies, its places, or as a copy
of a form field it reads) is an answer the template would lose. :func:`place_unread` asks the model, for each, whether
it writes again a field's answer (it becomes a place of that field), is an answer of its own (a new top-level
field, read from it; several such form fields given one new name are the parts of one answer, joined in the order
answered), or is not data. Nothing is left silent: :func:`apply_answers` warns of each one skipped.

Once the template is proven, a field that is not ready may be a repeat whose reader the model got wrong (a date
read from its month box alone). :func:`fold_repeats` folds such a field into a ready one when, on a sample, the
model read the same answer for both, and the failed field's own box is on one of the ready field's places there:
the same answer, written in the same spot. A field of another answer that only holds the same words elsewhere (a
province "Beijing" beside a city "Beijing") is never folded.
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from formcast.data.ai import _ask
from formcast.extract.ask import Llm, _asked, _messages
from formcast.extract.model import (
    RESERVED_NAMES,
    BoxRead,
    ChoiceRead,
    ExtractionTemplate,
    Reader,
    WidgetRead,
    concrete_paths,
)
from formcast.extract.page import DocText
from formcast.extract.proof import Proof, SampleProof, same_value
from formcast.extract.prompts import UNREAD_SYSTEM
from formcast.extract.samples import SampleDoc
from formcast.geometry import Rect
from formcast.llm import TextPart, Usage
from formcast.schema import ARRAY_ITEM, REASON_KEY, flatten_schema, parse_path, resolve

__all__ = [
    "Merged", "Placed", "Unread", "apply_answers", "fold_repeats", "merge_repeats", "place_unread", "unread_widgets",
]

#: The most unread form fields one call asks about; more are sent in several calls, one after another.
UNREAD_CHUNK = 80
#: The most printed lines given as a form field's surroundings.
NEAR_LINES = 3
#: The types a new field may have.
NEW_TYPES = ("string", "number", "integer")
#: Why a field added for an unread form field is optional.
ADDED_REASON = "the form fills it in; added because no field read it"


@dataclass
class Merged:
    schema: dict
    readers: dict[str, Reader]
    line_paths: dict[str, list[str]]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Unread:
    """A filled form field no reader reads: its form map id, name, page (from 0), rectangle, value and the printed
    words around it."""

    id: str
    name: str
    page: int
    rect: Rect
    value: str
    near: tuple[str, ...]
    reused: bool  # the sample holds different answers under this name on different pages


@dataclass
class Placed:
    schema: dict
    readers: dict[str, Reader]
    added: list[str] = field(default_factory=list)  # the new fields' paths
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ merging repeats


def _first_widget(reader: Reader) -> WidgetRead | None:
    return next((s for s in reader.strategies if isinstance(s, WidgetRead)), None)


def merge_repeats(
    schema: dict, readers: dict[str, Reader], line_paths: dict[str, list[str]], docs: list[SampleDoc] | None = None
) -> Merged:
    """Fields whose first strategy reads the same form field made one (see the module docstring). A read kept to one
    page counts as reading every page when no sample of ``docs`` holds different answers under its names."""
    schema_paths = flatten_schema(schema)
    differs = _differing_names(docs or [])
    keep: dict[tuple, str] = {}
    gone: dict[str, str] = {}  # dropped path -> the path kept
    for path in concrete_paths(schema):
        reader = readers.get(path)
        widget = _first_widget(reader) if reader is not None else None
        if widget is None or "[" in path:
            continue
        page = widget.page if any(n in differs for n in widget.names) or docs is None else None
        key = (tuple(widget.names), page, resolve(schema_paths, path).type)
        if key in keep:
            gone[path] = keep[key]
        else:
            keep[key] = path
    if not gone:
        return Merged(schema, readers, line_paths)
    readers = dict(readers)
    warnings = []
    for path, kept in gone.items():
        dropped = readers.pop(path)
        into = readers[kept]
        places = list(into.places) + [p for p in dropped.places if p not in into.places]
        readers[kept] = into.model_copy(update={"places": places})
        _drop(schema, path)
        warnings.append(f"{path}: reads the same form field as {kept}; merged into it (one field, every place listed)")
    line_paths = {
        line: list(dict.fromkeys(gone.get(p, p) for p in paths)) for line, paths in line_paths.items()
    }
    return Merged(schema, readers, line_paths, warnings)


def _differing_names(docs: list[SampleDoc]) -> set[str]:
    """The form field names some sample holds different answers under on different pages."""
    out: set[str] = set()
    for doc in docs:
        with DocText(doc.pdf) as text:
            for page in doc.form_map.pages:
                for w in page.widgets:
                    pages = text.widget_pages(w.name)
                    if w.name not in out and len(pages) > 1 and len({repr(text.widget_value(w.name, p)) for p in pages}) > 1:
                        out.add(w.name)
    return out


def _drop(schema: dict, path: str) -> None:
    """Take the plain field ``path`` out of ``schema``, and any group it leaves empty."""
    names = [s for s in parse_path(path) if s is not ARRAY_ITEM]
    chain = [schema]
    for name in names[:-1]:
        chain.append(chain[-1]["properties"][name])
    for depth in range(len(names) - 1, -1, -1):
        parent, name = chain[depth], names[depth]
        parent["properties"].pop(name, None)
        if name in (parent.get("required") or []):
            parent["required"] = [n for n in parent["required"] if n != name]
        if depth == 0 or parent["properties"]:
            break


# ------------------------------------------------------------------ unread form fields


def _claims(readers: dict[str, Reader], name: str, page: int, reused: bool) -> bool:
    """Whether a reader reads the form field ``name`` on ``page``: a read kept to another page does only when the
    field holds different answers on different pages (``reused``), else it reads every copy (``read``, places)."""
    for reader in readers.values():
        for s in [*reader.strategies, *reader.places]:
            if isinstance(s, WidgetRead) and name in s.names and (s.page in (None, page) or not reused):
                return True
            if isinstance(s, ChoiceRead) and any(m.widget == name and m.page in (None, page) for m in s.options.values()):
                return True
    return False


def unread_widgets(docs: list[SampleDoc], readers: dict[str, Reader]) -> list[Unread]:
    """The filled text form fields of the first sample that has form fields that no reader reads, in page order.

    Raises:
        FormcastError: when the sample cannot be opened.
    """
    doc = next((d for d in docs if any(p.widgets for p in d.form_map.pages)), None)
    if doc is None:
        return []
    out: list[Unread] = []
    seen: set[tuple[str, int]] = set()
    with ExitStack() as stack:
        text = stack.enter_context(DocText(doc.pdf))
        for page in doc.form_map.pages:
            for i, widget in enumerate(page.widgets):
                if widget.kind not in ("text", "combo", "list") or (widget.name, page.index) in seen:
                    continue
                if text.read_only(widget.name):
                    continue  # the form's own printing, not an answer
                seen.add((widget.name, page.index))
                pages = text.widget_pages(widget.name)
                value = text.widget_value(widget.name, page.index if len(pages) > 1 else None)
                reused = len({repr(text.widget_value(widget.name, p)) for p in pages}) > 1
                if not isinstance(value, str) or not value.strip() or _claims(readers, widget.name, page.index, reused):
                    continue
                out.append(Unread(
                    f"w{page.index}_{i}", widget.name, page.index, widget.rect, " ".join(value.split()),
                    _near(page, widget.rect) + ((widget.tooltip,) if widget.tooltip else ()), reused,
                ))  # fmt: skip
    return out


def _near(page, rect: Rect) -> tuple[str, ...]:
    """The printed lines nearest the form field: on its row to the left, just under it (a signature line's caption)
    or just above it, closest first."""
    x0, y0, x1, y1 = rect
    scored = []
    for line in page.lines:
        lx0, ly0, lx1, ly1 = line.rect
        text = " ".join(line.text.split())
        if not text or len(text) > 160:
            continue
        across = min(x1, lx1) - max(x0, lx0) > 0
        if min(y1, ly1) - max(y0, ly0) > 0 and lx1 <= x0 + 2:
            scored.append((x0 - lx1, text))  # on the row, to the left
        elif across and 0 <= ly0 - y1 <= 24:
            scored.append((ly0 - y1 + 5, text))  # just under
        elif across and 0 <= y0 - ly1 <= 18:
            scored.append((y0 - ly1 + 10, text))  # just above
    return tuple(t for _, t in sorted(scored)[:NEAR_LINES])


# ------------------------------------------------------------------ the model's answers


def place_unread(schema: dict, paths: list[str], unread: list[Unread], llm: Llm) -> tuple[list[dict], Usage]:
    """The model's answer for each unread form field: ``{"id", "path"}``, ``{"id", "new": {...}}`` or ``{"id", "skip"}``.

    Raises:
        FormcastError: when the model's answer to some call could not be used twice.
    """
    known = set(paths)
    answers: list[dict] = []
    usage = Usage()
    for n in range(0, len(unread), UNREAD_CHUNK):
        chunk = unread[n : n + UNREAD_CHUNK]
        ids = {u.id for u in chunk}

        def check(answer: dict, ids: set[str] = ids) -> list[dict]:
            given = answer.get("answers")
            if not isinstance(given, list) or not all(isinstance(a, dict) for a in given):
                raise ValueError('reply as {"answers": [{"id": "<id>", "path" | "new" | "skip": ...}, ...]}')
            out = []
            for a in given:
                if a.get("id") not in ids:
                    raise ValueError(f"{a.get('id')!r} is not one of the form fields listed")
                if "path" in a and a["path"] not in known:
                    raise ValueError(f"{a['path']!r} is not one of the paths listed")
                new = a.get("new")
                if new is not None and (
                    not isinstance(new, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", str(new.get("name", "")))
                    or new.get("type", "string") not in NEW_TYPES
                ):
                    raise ValueError('"new" is {"name": "<snake_case>", "title": "...", "type": "string", "description": "..."}')
                if "path" not in a and new is None and "skip" not in a:
                    raise ValueError(f"give {a['id']} a path, a new field or a skip")
                out.append(a)
            missing = ids - {a["id"] for a in out}
            if missing:
                raise ValueError(f"answer every form field listed; missing {', '.join(sorted(missing)[:10])}")
            return out

        listed = "\n".join(
            f"- {u.id} name={json.dumps(u.name)} page {u.page} value={json.dumps(u.value, ensure_ascii=False)}"
            + (f" near: {' | '.join(json.dumps(t, ensure_ascii=False) for t in u.near)}" if u.near else "")
            for u in chunk
        )
        asked = _asked(schema, paths) + f"\nUNREAD FORM FIELDS:\n{listed}\n"
        got, used, _ = _ask(llm, _messages(UNREAD_SYSTEM, [TextPart(asked)]), check, long=True)
        answers += got
        usage = Usage(usage.input_tokens + used.input_tokens, usage.output_tokens + used.output_tokens)
    return answers, usage


def apply_answers(
    schema: dict, readers: dict[str, Reader], unread: list[Unread], answers: list[dict], *, new_fields: bool = True
) -> Placed:
    """The answers made into places of fields and new top-level fields (``new_fields``: else a new field is warned
    of and left out, as for a destination's fixed fields); every skip is warned of."""
    by_id = {u.id: u for u in unread}
    readers = dict(readers)
    placed = Placed(schema, readers)
    new: dict[str, tuple[dict, list[Unread]]] = {}
    for a in answers:
        u = by_id[a["id"]]
        where = f"{u.name} (page {u.page + 1}) = {u.value!r}"
        if "path" in a:
            reader = readers[a["path"]]
            place = WidgetRead(names=[u.name], page=u.page if u.reused else None)
            if place not in reader.places:
                readers[a["path"]] = reader.model_copy(update={"places": [*reader.places, place]})
        elif a.get("new") is not None:
            if not new_fields:
                placed.warnings.append(f"form field {where}: no destination field takes it; not read")
                continue
            entry = new.setdefault(a["new"]["name"], (a["new"], []))
            entry[1].append(u)
        else:
            placed.warnings.append(f"form field {where}: not read ({a.get('skip') or 'not data'})")
    props = schema.setdefault("properties", {})
    for name, (spec, parts) in new.items():
        key = _free(name, props)
        props[key] = {
            "type": spec.get("type", "string"),
            "title": str(spec.get("title") or name.replace("_", " ").capitalize()),
            "description": str(spec.get("description") or ""),
            REASON_KEY: ADDED_REASON,
        }
        names = list(dict.fromkeys(u.name for u in parts))
        page = parts[0].page if any(u.reused for u in parts) else None
        first = parts[0]
        readers[key] = Reader(strategies=[WidgetRead(names=names, page=page), BoxRead(page=first.page, rect=first.rect)])
        placed.added.append(key)
        placed.warnings.append(f"{key}: added for the form field {', '.join(names)} (page {first.page + 1}), which no field read")
    return placed


def _free(name: str, props: dict[str, Any]) -> str:
    """``name``, or ``name_2`` ... when the schema or the document's own columns have it."""
    key, n = name, 2
    while key in props or key in RESERVED_NAMES:
        key, n = f"{name}_{n}", n + 1
    return key


# ------------------------------------------------------------------ folding failed repeats


def fold_repeats(template: ExtractionTemplate, proof: Proof) -> tuple[ExtractionTemplate, list[str]]:
    """``template`` with each failed field that repeats a ready one folded into it (see the module docstring), and a
    warning for each; the template itself when there is none. The caller proves the result again."""
    schema_paths = flatten_schema(template.schema_)
    by_path = {f.path: f for f in proof.fields}
    ready = [f for f in proof.fields if f.ready and "[" not in f.path]
    folded: dict[str, str] = {}
    for fp in proof.fields:
        reader = template.readers.get(fp.path)
        if fp.ready or fp.absent or reader is None or "[" in fp.path:
            continue
        boxes = [(s.page, s.rect) for s in reader.strategies if isinstance(s, BoxRead)]
        kind = resolve(schema_paths, fp.path).type
        for other in ready:
            if other.path in folded or resolve(schema_paths, other.path).type != kind:
                continue
            if any(_repeats(mine, theirs, boxes) for mine, theirs in zip(fp.samples, by_path[other.path].samples)):
                folded[fp.path] = other.path
                break
    if not folded:
        return template, []
    schema = copy.deepcopy(template.schema_)
    readers = dict(template.readers)
    for path in folded:
        _drop(schema, path)
        readers.pop(path)
    samples = [
        s.model_copy(update={"opinion": {p: v for p, v in s.opinion.items() if p not in folded}}) for s in template.samples
    ]
    setup = template.setup
    if setup is not None and setup.line_paths:
        line_paths = {line: list(dict.fromkeys(folded.get(p, p) for p in paths)) for line, paths in setup.line_paths.items()}
        setup = setup.model_copy(update={"line_paths": line_paths})
    out = template.model_copy(update={"schema_": schema, "readers": readers, "samples": samples, "setup": setup})
    warnings = [
        f"{path}: the same answer, written in one of {into}'s places; folded into it (its reader read it wrongly)"
        for path, into in folded.items()
    ]
    return ExtractionTemplate.model_validate(out.model_dump(by_alias=True)), warnings


def _repeats(mine: SampleProof, theirs: SampleProof, boxes: list[tuple[int, Rect]]) -> bool:
    """Whether, on one sample, the model read the same answer for both fields and one of the failed field's boxes is
    on a place of the ready one."""
    if mine.opinion is None or theirs.opinion is None or not same_value(theirs.opinion, mine.opinion):
        return False
    return any(page == pl.page and _overlaps(rect, pl.rect) for page, rect in boxes for pl in theirs.read.places)


def _overlaps(a: Rect, b: Rect) -> bool:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w > 0 and h > 0
