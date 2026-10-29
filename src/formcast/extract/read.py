"""Read one filled PDF with an extraction template: every schema path gets a value and a status, with no model call.

``extract(template, pdf)`` reads the file once and opens it once for reading (one
:class:`~formcast.extract.page.DocText`, closed on every path out); its fingerprints are computed from the same bytes
by :class:`formcast.match.PdfFeatures`, only when needed (the template has a fingerprint, or no ``doc_id`` is given).
Then, for every concrete path of the template's schema:

1. **Layout.** The document matches by ``"fingerprint"`` when its fingerprint, or its fingerprint with field values
   ignored, is the template's (a fillable copy of the blank form); else by ``"layout"`` when its
   :func:`~formcast.extract.layout.layout_score` reaches :data:`~formcast.extract.layout.LAYOUT_MATCH` (a flattened,
   shifted or rotated copy); else ``"none"``. Reads still run on ``"none"``, but positions mean nothing there, so
   ``box`` reads and the drawn boxes of a ``choice`` are skipped. On a fingerprint match a box is read where the
   template has it; on a layout match it is moved down by the page's :func:`~formcast.extract.layout.layout_offset`.
2. **Strategies in order.** The first strategy that gives non-empty text is the candidate. When the candidate fails a
   check (or cleans to nothing) the next strategies are tried, and the first of them whose value passes wins;
   when none passes, the first candidate stays, with its reasons. A ``choice`` gives the key of the one marked
   option, nothing when no option is marked, and ``", ".join`` of the keys when several are (``"individual,
   joint"``), which fails with the reason ``"N boxes checked"``: never found, whatever the schema allows.
3. **Cleaning.** The reader's ``clean`` transforms run on the raw text (``formcast.data.mapping._run``), then the
   value is coerced to the path's schema type (``_coerce``). The raw text is kept beside the value. A value that
   cleans to nothing is ``missing``, with its raw text and a reason.
4. **Checks.** Every path's first candidate is checked in one record with ``validate_data_located``; a later
   strategy's value is checked on its own (the errors located at its path), and when one replaces a failing
   candidate the record is checked again. Every error of the final record is attached to its path as a reason;
   a ``required`` error goes to the missing child's path, and a required group that has no answers at all is
   checked as an empty group, so its own required fields are named (no investor answers -> ``investor.tax_id``
   is missing, with the reason). An error located at a group goes to every field in it.
5. **Statuses**, decided in one place (:func:`_field`): a path in ``template.absent`` is ``not_on_form`` and never
   read; every field of a repeated item that has nothing at all is ``absent`` and the item is left out of the
   record; a field with no value is ``missing``; a value with a reason is ``failed_check``; a value read by
   ``anchor`` or ``phrase`` is ``unsure`` when its labels or phrases matched more than one place (all wordings
   counted together, so two wordings found once each are two places) or the document's layout is ``"none"``;
   every other value (``widget``, ``box``, ``choice``, a trusted label read) is ``found``.

A phrase's places are the most of its occurrences with text after them and its occurrences on one row
(``find_label``), so an occurrence with nothing readable after it still makes the read unsure. An anchor's value is
read at the first of its places that has text on the reader's side; ``side="inside"`` reads like ``"right"`` (the
answer after the label in the label's own box). Widget reads have no place (page and rect are ``None``).

``extract`` never raises for a bad document: one that cannot be opened (missing, not a PDF, encrypted, empty) or
read gives ``matched="none"``, no fields and ``error`` set, which the batch runner records as unreadable. A bad
template still raises (an invalid JSON Schema is a ``FormcastError``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from formcast.data.mapping import _INDEX, _coerce, _empty, _generic, _run, _set
from formcast.errors import FormcastError
from formcast.extract.layout import LAYOUT_MATCH, layout_offset, layout_score
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceRead,
    DocumentResult,
    ExtractionTemplate,
    FieldResult,
    PhraseRead,
    Reader,
    Status,
    Strategy,
    WidgetRead,
    concrete_paths,
)
from formcast.extract.page import DocText
from formcast.geometry import Rect
from formcast.match import PdfFeatures
from formcast.schema import SchemaPath, data_location, flatten_schema, resolve, validate_data_located

_REQUIRED = re.compile(r"""^(['"])(.*)\1 is a required property$""")
#: Rounds of checking a required group that has no answers as an empty group (one per level of nesting).
_MAX_GROUP_ROUNDS = 16


@dataclass
class _Read:
    """What one path read: the raw text, the cleaned value, where it came from and why it is in doubt."""

    raw: str | None = None
    value: Any = None
    strategy: str | None = None
    page: int | None = None
    rect: Rect | None = None
    weak: bool = False  # an anchor or phrase read whose place is not certain
    reasons: list[str] = field(default_factory=list)


@dataclass
class _Context:
    """One open document and what every path's read needs to know about it."""

    doc: DocText
    template: ExtractionTemplate
    matched: str
    schema_paths: list[SchemaPath]
    offsets: dict[int, float] = field(default_factory=dict)

    def moved(self, page: int, rect: Rect) -> Rect:
        """``rect`` where it sits on this document's ``page``: in place on a fingerprint match, moved on a layout one."""
        if self.matched == "layout":
            if page not in self.offsets:
                self.offsets[page] = layout_offset(self.template.layout, self.doc, page)
            dy = self.offsets[page]
            return (rect[0], rect[1] + dy, rect[2], rect[3] + dy)
        return rect


def extract(
    template: ExtractionTemplate, pdf: str | Path | bytes, *, doc_id: str | None = None, file: str = ""
) -> DocumentResult:
    """Read ``pdf`` with ``template``: one :class:`FieldResult` per concrete path of its schema, as the module says.

    ``doc_id`` defaults to the document's fingerprint with field values ignored (of its bytes when it cannot be
    opened). Never raises for a bad document; see the module docstring.
    """
    at = dt.datetime.now(dt.UTC).isoformat()

    def failed(error: str, data: bytes) -> DocumentResult:
        ident = doc_id or hashlib.sha256(data).hexdigest()[:16]
        return DocumentResult(
            doc_id=ident, file=file, template_id=template.template_id, matched="none", fields=[], error=error,
            extracted_at=at,
        )  # fmt: skip

    try:
        data = bytes(pdf) if isinstance(pdf, (bytes, bytearray, memoryview)) else Path(pdf).read_bytes()
    except OSError:
        return failed("cannot open PDF", b"")
    try:
        doc = DocText(data)
    except FormcastError as exc:
        return failed(str(exc), data)

    schema = template.schema_
    try:
        features = PdfFeatures(data)
        ident = doc_id or features.fingerprint_blind
        ctx = _Context(doc, template, _match(template, doc, features), flatten_schema(schema))
        paths = concrete_paths(schema)
        # each path's first read, all checked together as one record; a read that fails then gives way to the
        # first later one that passes on its own, and the record is checked again
        tries = {
            path: _read_path(path, template.readers.get(path), ctx) for path in paths if path not in template.absent
        }
        reads = {path: next(tried, None) or _Read() for path, tried in tries.items()}
        errors = _record_errors(schema, reads, ctx.schema_paths)
        if _fall_back(schema, tries, reads, errors):
            errors = _record_errors(schema, reads, ctx.schema_paths)
    except FormcastError:
        raise  # the template's fault (an invalid schema), not the document's
    except Exception as exc:  # a document that opens but breaks while read (MuPDF raises several types)
        return failed(f"cannot read PDF: {exc}", data)
    finally:
        doc.close()

    for path, messages in errors.items():
        reads[path].reasons += [m for m in messages if m not in reads[path].reasons]
    absent = _absent_items(reads)
    return DocumentResult(
        doc_id=ident,
        file=file,
        template_id=template.template_id,
        matched=ctx.matched,  # type: ignore[arg-type]
        fields=[_field(path, reads.get(path), path in absent) for path in paths],
        extracted_at=at,
    )


def _match(template: ExtractionTemplate, doc: DocText, features: PdfFeatures) -> str:
    if features.matches_fingerprint(template.form.fingerprint):
        return "fingerprint"
    if layout_score(template.layout, doc) >= LAYOUT_MATCH:
        return "layout"
    return "none"


def _field(path: str, read: _Read | None, absent: bool) -> FieldResult:
    """The path's result (``read`` is ``None`` for a path not on the form). Every status is decided here."""
    if read is None:
        return FieldResult(path=path, status="not_on_form", raw=None)
    status: Status
    if absent:
        status = "absent"
    elif read.value is None:
        status = "missing"
    elif read.reasons:
        status = "failed_check"
    elif read.weak:
        status = "unsure"
    else:
        status = "found"
    return FieldResult(
        path=path, status=status, raw=read.raw, value=read.value, strategy=read.strategy, page=read.page,
        rect=read.rect, reasons=read.reasons,
    )  # fmt: skip


# --------------------------------------------------------------------- one path


def _read_path(path: str, reader: Reader | None, ctx: _Context) -> Iterator[_Read]:
    """The path's reads, one per strategy that finds text, in order, each cleaned and with its own reasons.

    A generator, so a later strategy runs only when the reads before it have failed their checks.
    """
    if reader is None:
        yield _Read(reasons=["the template has no reader for this field"])
        return
    sp = resolve(ctx.schema_paths, _generic(path)[0])
    for strategy in reader.strategies:
        read = _try(strategy, ctx)
        if read is None:
            continue
        value = _run(reader.clean, read.raw)
        read.value = None if _empty(value) else _coerce(value, sp)
        if read.value is None:
            read.reasons.append(f"cleaning left nothing of {read.raw!r}")
        yield read


def _fall_back(
    schema: dict, tries: dict[str, Iterator[_Read]], reads: dict[str, _Read], errors: dict[str, list[str]]
) -> bool:
    """Put in place of each failing read the first later read of its path that passes; whether any was replaced."""
    changed = False
    for path, tried in tries.items():
        if not (reads[path].reasons or errors.get(path)):
            continue
        for later in tried:
            if not later.reasons and not _errors_at(schema, path, later.value):
                reads[path], changed = later, True
                break
    return changed


def _errors_at(schema: dict, path: str, value: Any) -> list[str]:
    """The schema's errors located at ``path`` for ``value`` checked alone (the other fields left out)."""
    record: dict = {}
    _set(record, path, value)
    return [message for location, message in validate_data_located(schema, record) if location == path]


def _try(strategy: Strategy, ctx: _Context) -> _Read | None:
    """What one strategy reads on the document, or ``None`` when it finds no text."""
    doc = ctx.doc
    if isinstance(strategy, WidgetRead):
        for name in strategy.names:
            value = doc.widget_value(name)
            raw = value if isinstance(value, str) else None if value is None else str(value).lower()
            if raw and raw.strip():
                return _Read(raw=raw.strip(), strategy="widget")
        return None
    if isinstance(strategy, BoxRead):
        if ctx.matched == "none":
            return None
        rect = ctx.moved(strategy.page, strategy.rect)
        raw = doc.text_in(strategy.page, rect)
        return _Read(raw=raw.strip(), strategy="box", page=strategy.page, rect=rect) if raw and raw.strip() else None
    if isinstance(strategy, AnchorRead):
        places = doc.find_label(strategy.labels, strategy.page)
        for page, label in places:
            if strategy.side == "below":
                got = doc.text_below(page, label, strategy.max_lines)
            else:
                got = doc.text_right_of(page, label, strategy.max_width)
            if got and got[0].strip():
                weak = len(places) > 1 or ctx.matched == "none"
                return _Read(raw=got[0].strip(), strategy="anchor", page=page, rect=got[1], weak=weak)
        return None
    if isinstance(strategy, PhraseRead):
        hits = doc.after_phrase(strategy.phrases, strategy.page, strategy.stop)
        if not hits:
            return None
        places = max(len(hits), len(doc.find_label(strategy.phrases, strategy.page)))
        page, text, rect = hits[0]
        weak = places > 1 or ctx.matched == "none"
        return _Read(raw=text.strip(), strategy="phrase", page=page, rect=rect, weak=weak)
    return _choice(strategy, ctx)


def _choice(strategy: ChoiceRead, ctx: _Context) -> _Read | None:
    """The marked option's key; the keys of all marked options, with a reason, when several are."""
    marked: list[tuple[str, int | None, Rect | None]] = []
    for key, mark in strategy.options.items():
        if mark.widget is not None:
            on = ctx.doc.widget_on(mark.widget, mark.state)
            page, rect = None, None
        elif ctx.matched == "none":
            continue  # a drawn box is a place on the page: only on a matching layout
        else:
            page, rect = mark.page, ctx.moved(mark.page, mark.rect)  # type: ignore[arg-type]
            on = ctx.doc.marked(page, rect)  # type: ignore[arg-type]
        if on:
            marked.append((key, page, rect))
    if not marked:
        return None
    read = _Read(raw=", ".join(key for key, _, _ in marked), strategy="choice")
    if len(marked) > 1:
        read.reasons.append(f"{len(marked)} boxes checked")
    else:
        read.page, read.rect = marked[0][1], marked[0][2]
    return read


# ------------------------------------------------------------- the whole record


def _record_errors(schema: dict, reads: dict[str, _Read], schema_paths: list[SchemaPath]) -> dict[str, list[str]]:
    """The values read checked as one record: each error message under every read path it belongs to."""
    valued = {path: read for path, read in reads.items() if read.value is not None}
    compact = _compact_indices(list(valued))
    record: dict = {}
    where: dict[str, str] = {}  # a location in the record -> the same place in the schema's concrete paths
    for path, read in valued.items():
        _set(record, compact[path], read.value)
        where.update(zip(_prefixes(compact[path]), _prefixes(path)))

    located: list[tuple[str, str]] = []
    for _ in range(_MAX_GROUP_ROUNDS):
        located, grown = [], False
        for location, message in validate_data_located(schema, record):
            target = where.get(location, location)
            m = _REQUIRED.match(message)
            if m is None:
                located.append((target, message))
                continue
            child, place = _child(target, m.group(2)), _child(location, m.group(2))
            if _is_group(schema_paths, child):  # a required group with no answers: check it as an empty one
                _set(record, place, {})
                where[place] = child
                grown = True
            else:
                located.append((child, message))
        if not grown:
            break

    out: dict[str, list[str]] = defaultdict(list)
    for target, message in located:
        for path in reads:
            if not target or path == target or path.startswith((target + ".", target + "[")):
                out[path].append(message)
    return out


def _compact_indices(paths: list[str]) -> dict[str, str]:
    """Each path with its item indices renumbered over the items that have a value, as the record lists them.

    ``owners[1].name`` is ``owners[0].name`` in the record when ``owners[0]`` has nothing.
    """
    items: dict[str, set[int]] = defaultdict(set)  # the path before an index -> the indices used there
    for path in paths:
        for m in _INDEX.finditer(path):
            items[path[: m.start()]].add(int(m.group(1)))
    rank = {(head, n): r for head, ns in items.items() for r, n in enumerate(sorted(ns))}
    out: dict[str, str] = {}
    for path in paths:
        pieces, last = [], 0
        for m in _INDEX.finditer(path):
            pieces += [path[last : m.start()], f"[{rank[(path[: m.start()], int(m.group(1)))]}]"]
            last = m.end()
        out[path] = "".join(pieces) + path[last:]
    return out


def _prefixes(path: str) -> list[str]:
    """``owners[1].name`` -> ``owners``, ``owners[1]``, ``owners[1].name``."""
    return [path[:i] for i in range(1, len(path)) if path[i] in ".["] + [path]


def _child(location: str, name: str) -> str:
    segment = data_location([name])
    if not location:
        return segment
    return location + segment if segment.startswith("[") else f"{location}.{segment}"


def _is_group(schema_paths: list[SchemaPath], path: str) -> bool:
    try:
        return resolve(schema_paths, _generic(path)[0]).type == "object"
    except KeyError:
        return False


def _absent_items(reads: dict[str, _Read]) -> set[str]:
    """The paths in a repeated item none of whose read fields has anything (no text and no reason)."""
    by_item: dict[str, list[str]] = defaultdict(list)
    for path in reads:
        for m in _INDEX.finditer(path):
            by_item[path[: m.end()]].append(path)
    out: set[str] = set()
    for paths in by_item.values():
        if all(reads[p].raw is None and not reads[p].reasons for p in paths):
            out.update(paths)
    return out
