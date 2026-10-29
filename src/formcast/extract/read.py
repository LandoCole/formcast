"""Read one filled PDF with an extraction template: every schema path gets a value and a status, with no model call.

``extract(template, pdf)`` reads the file's bytes once. It opens them for reading in one
:class:`~formcast.extract.page.DocText` (closed on every path out), and :class:`formcast.match.PdfFeatures` opens
them once more for each fingerprint it computes (the full one and the one with field values ignored), only when
needed (the template has a fingerprint, or no ``doc_id`` is given): up to three opens per document.
The batch runner routes a document on the same open ``DocText`` and ``PdfFeatures`` and hands them, with the match
it found, to ``_extract_open``, so a routed document is opened, fingerprinted and scored only once.
Then, for every concrete path of the template's schema:

1. **Layout.** The document matches by ``"fingerprint"`` when its fingerprint, or its fingerprint with field values
   ignored, is the template's (a fillable copy of the blank form); else by ``"layout"`` when its
   :func:`~formcast.extract.layout.layout_score` reaches :data:`~formcast.extract.layout.LAYOUT_MATCH` (a flattened,
   shifted or rotated copy); else ``"none"``. Reads still run on ``"none"``, but positions mean nothing there, so
   ``box`` reads and the drawn boxes of a ``choice`` are skipped. On a fingerprint match a box is read where the
   template has it; on a layout match it is moved down by the page's offset. Each page's
   :func:`~formcast.extract.layout.page_fit` (the offset, and where each static line was found) is made once per
   document and serves every read on that page:

   - A ``box`` read (and a ``choice``'s drawn boxes) is trusted only where the layout agrees locally
     (:meth:`~formcast.extract.layout.PageFit.agrees`: the static line nearest above the box and every static line
     on its row found at the page's offset). A score passes a revised edition whose inserted row moved everything
     below it; the box under that row would read the wrong line. Elsewhere the strategy is skipped and the path
     records the doubt ``"layout differs near this box"``: a later strategy's value is ``unsure`` with it, and
     when no later strategy reads anything the field is ``missing`` with it as the reason.
   - An ``anchor`` read never takes the form's printed words (the document's lines found as static lines) as its
     answer: it stops before them, so a blank answer followed by the next label reads nothing, not that label
     (:mod:`~formcast.extract.page`, ``stop_at``). A ``below`` read takes only the line directly under the label.
     On ``"none"`` the printed words are not known and nothing is stopped at; such reads are unsure anyway.
2. **Strategies in order.** The first strategy that gives non-empty text is the candidate. When the candidate fails a
   check (or cleans to nothing) the next strategies are tried, and the first of them whose value passes wins;
   when none passes, the first candidate stays, with its reasons. A later value that differs from the failed
   first one is ``unsure`` (the reason names the first read): two strategies disagreeing is never ``found``.
   A ``choice`` gives the key of the one marked option, nothing when no option is marked, and ``", ".join`` of
   the keys when several are (``"individual, joint"``), which fails with the reason ``"N boxes checked"``: never
   found, whatever the schema allows.
3. **Cleaning.** The reader's ``clean`` transforms run on the raw text (``formcast.data.mapping._run``), then the
   value is coerced to the path's schema type (``_coerce``). The raw text is kept beside the value. A value that
   cleans to nothing is ``missing``, with its raw text and a reason. Coercion never turns one number into another
   (:func:`_coerced`): for an ``integer`` path a number that is not whole ("12.7") stays as it is, and commas count
   only as thousands separators in groups of three ("1,234,567"; "1,5" stays the text it is), so the type check
   fails (``failed_check``) instead of the value changing.
4. **Checks.** Every path's first candidate is checked in one record with ``validate_data_located``; a later
   strategy's value is checked on its own (the errors located at its path), and when one replaces a failing
   candidate the record is checked again. Every error of the final record is attached to its path as a reason
   (an error below a path, such as one item of a list of answers, ``tags[1]``, belongs to ``tags``);
   a ``required`` error goes to the missing child's path, and a required group that has no answers at all is
   checked as an empty group, so its own required fields are named (no investor answers -> ``investor.tax_id``
   is missing, with the reason). An error located at a group goes to every field in it.
5. **Statuses**, decided in one place (:func:`_field`): a path in ``template.absent`` is ``not_on_form`` and never
   read; every field of a repeated item that has nothing at all (no text, and no reason such as a skipped box) is
   ``absent`` and the item is left out of the
   record; a field with no value is ``missing``; a value with a reason is ``failed_check``; a value read by
   ``anchor`` or ``phrase`` is ``unsure`` when its labels or phrases matched more than one place (all wordings
   counted together, so two wordings found once each are two places) or the document's layout is ``"none"``;
   a value read after a box was skipped is ``unsure`` (``"layout differs near this box"``); a value read by
   ``anchor`` or ``phrase`` whose raw text is another path's raw text is ``unsure``, and so is that other path
   (``"also read as <other path>"``: one answer taken twice, as when a blank answer's label reads the next field),
   and so is one whose rectangle overlaps the box another path is read from where the layout holds around that box
   (``"overlaps <path>'s box"``); every other value (``widget``, ``box``, ``choice``, a trusted label read) is
   ``found``. An ``unsure`` field says why in its reasons ("label found in 2 places", "layout not matched"); a
   ``failed_check`` field's reasons are its failed checks, followed by any doubts. A field no strategy read is
   ``missing``, with the reason when one is known: ``"layout differs near this box"``, or ``"answer runs into the
   next label"`` when a label read could not tell the answer from the next label on its row (R10b).

A phrase's places are the most of its occurrences with text after them and its occurrences on one row
(``find_label``), so an occurrence with nothing readable after it still makes the read unsure. An anchor's value is
read at the first of its places that has text on the reader's side. Widget reads have no place (page and rect are
``None``).

``extract`` never raises. A document that cannot be opened (missing, not a PDF, encrypted, empty) or that MuPDF fails
on while it is read gives ``matched="none"``, no fields and ``error`` set ("cannot open PDF", "encrypted PDF",
"cannot read PDF: ..."), which the batch runner records as unreadable. Any other exception is a fault of the
template or of this code, not of the document, and gives ``error="internal error: <ExceptionType>: <message>"``
(an invalid JSON Schema: ``"internal error: FormcastError: invalid JSON Schema: ..."``).
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

import pymupdf

from formcast.data.mapping import _INDEX, _coerce, _empty, _generic, _number, _run, _set
from formcast.errors import FormcastError
from formcast.extract.layout import LAYOUT_MATCH, PageFit, layout_score, page_fit
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

#: What MuPDF raises for a document it cannot read (beside open_pdf's FormcastError).
_PDF_ERRORS = (pymupdf.mupdf.FzErrorBase, pymupdf.FileDataError, pymupdf.EmptyFileError)
_REQUIRED = re.compile(r"""^(['"])(.*)\1 is a required property$""")
#: What ``_number`` drops besides commas (currency, percent, spaces), and a number whose commas group thousands.
_NOT_DIGITS = re.compile(r"[$€£%\s]")
_GROUPED = re.compile(r"[-+(]?\d{1,3}(?:,\d{3})+(?:\.\d*)?\)?")
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
    reasons: list[str] = field(default_factory=list)  # failed checks: the value is in doubt
    doubts: list[str] = field(default_factory=list)  # why a value that passes is not certain (makes it unsure)


class _SchemaError(Exception):
    """A ``FormcastError`` from checking a record: the template's schema is at fault, not the document."""


@dataclass
class _Skip:
    """A strategy that did not run on this document, and why (shown when no later strategy reads a value).

    A ``doubt`` also makes a later strategy's value unsure; a plain note only explains a field left missing.
    """

    reason: str
    doubt: bool = False


#: Why a box (or a choice's drawn box) was not read: the layout does not hold around it on this document.
LAYOUT_DIFFERS = "layout differs near this box"
#: Why a label read gave nothing: the answer cannot be told from the next label on its row (R10b).
INTO_LABEL = "answer runs into the next label"


@dataclass
class _Context:
    """One open document and what every path's read needs to know about it."""

    doc: DocText
    template: ExtractionTemplate
    matched: str
    schema_paths: list[SchemaPath]
    fits: dict[int, PageFit] = field(default_factory=dict)  # each page's fit, made once per document

    def fit(self, page: int) -> PageFit:
        if page not in self.fits:
            self.fits[page] = page_fit(self.template.layout, self.doc, page)
        return self.fits[page]

    def moved(self, page: int, rect: Rect) -> Rect:
        """``rect`` where it sits on the document's ``page``: in place on a fingerprint match, moved on a layout one."""
        if self.matched == "layout":
            dy = self.fit(page).offset
            return (rect[0], rect[1] + dy, rect[2], rect[3] + dy)
        return rect

    def agrees(self, page: int, rect: Rect) -> bool:
        """Whether the layout holds around the template's box ``rect`` on this document (matched layouts only)."""
        return self.matched != "none" and self.fit(page).agrees(rect)

    def printed(self, page: int) -> list[Rect]:
        """The document's own printed lines of the form on ``page``; none known when the layout did not match."""
        return self.fit(page).found_rects() if self.matched != "none" else []


def extract(
    template: ExtractionTemplate, pdf: str | Path | bytes, *, doc_id: str | None = None, file: str = ""
) -> DocumentResult:
    """Read ``pdf`` with ``template``: one :class:`FieldResult` per concrete path of its schema, as the module says.

    ``doc_id`` defaults to the document's fingerprint with field values ignored (of its bytes when it cannot be
    opened). Never raises for a bad document; see the module docstring.
    """
    at = now()
    try:
        data = bytes(pdf) if isinstance(pdf, (bytes, bytearray, memoryview)) else Path(pdf).read_bytes()
    except OSError:
        return _failed(template, "cannot open PDF", b"", doc_id, file, at)
    try:
        doc = DocText(data)
    except FormcastError as exc:
        return _failed(template, str(exc), data, doc_id, file, at)
    try:
        return _extract_open(template, doc, PdfFeatures(data), data, doc_id=doc_id, file=file, at=at)
    finally:
        doc.close()


def now() -> str:
    """The current UTC time as ISO-8601, as results stamp it."""
    return dt.datetime.now(dt.UTC).isoformat()


def _failed(
    template: ExtractionTemplate | None, error: str, data: bytes, doc_id: str | None, file: str, at: str
) -> DocumentResult:
    """A document that could not be read: no fields, ``error`` set; its id the bytes' hash when none is given."""
    return DocumentResult(
        doc_id=doc_id or hashlib.sha256(data).hexdigest()[:16], file=file,
        template_id=template.template_id if template else None, matched="none", fields=[], error=error,
        extracted_at=at,
    )  # fmt: skip


def error_text(exc: Exception) -> str:
    """What a result's ``error`` says for an exception raised while reading an open document (see the module)."""
    if isinstance(exc, _SchemaError):
        cause = exc.__cause__
        return f"internal error: {type(cause).__name__}: {cause}"
    if isinstance(exc, (FormcastError, *_PDF_ERRORS)):  # a document that opens but breaks while read
        return f"cannot read PDF: {exc}"
    return f"internal error: {type(exc).__name__}: {exc}"  # a fault of the template or of this code


def _extract_open(
    template: ExtractionTemplate,
    doc: DocText,
    features: PdfFeatures,
    data: bytes,
    *,
    matched: str | None = None,
    doc_id: str | None,
    file: str,
    at: str,
) -> DocumentResult:
    """:func:`extract` on a document already open as ``doc`` and ``features`` (both of ``data``); ``doc`` stays open.

    ``matched`` is the match routing already made (``"fingerprint"`` or ``"layout"``), so the fingerprints and the
    layout score are not computed again; ``None`` works it out as :func:`extract` does. Never raises.
    """
    schema = template.schema_
    try:
        ident = doc_id or features.fingerprint_blind
        ctx = _Context(doc, template, matched or _match(template, doc, features), flatten_schema(schema))
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
        for path, messages in errors.items():
            reads[path].reasons += [m for m in messages if m not in reads[path].reasons]
        _cross_check(reads, ctx)
        absent = _absent_items(reads)
        return DocumentResult(
            doc_id=ident,
            file=file,
            template_id=template.template_id,
            matched=ctx.matched,  # type: ignore[arg-type]
            fields=[_field(path, reads.get(path), path in absent) for path in paths],
            extracted_at=at,
        )
    except Exception as exc:
        return _failed(template, error_text(exc), data, doc_id, file, at)


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
    elif read.doubts:
        status = "unsure"
    else:
        status = "found"
    return FieldResult(
        path=path, status=status, raw=read.raw, value=read.value, strategy=read.strategy, page=read.page,
        rect=read.rect, reasons=read.reasons + read.doubts,
    )  # fmt: skip


# --------------------------------------------------------------------- one path


def _read_path(path: str, reader: Reader | None, ctx: _Context) -> Iterator[_Read]:
    """The path's reads, one per strategy that finds text, in order, each cleaned and with its own reasons.

    A generator, so a later strategy runs only when the reads before it have failed their checks. A strategy that is
    skipped (:class:`_Skip`) leaves its doubt on every later read; when no strategy reads anything, the path's one
    read is empty and carries the skips' reasons (missing, and why).
    """
    if reader is None:
        yield _Read(reasons=["the template has no reader for this field"])
        return
    sp = resolve(ctx.schema_paths, _generic(path)[0])
    skipped: list[_Skip] = []
    read_any = False
    for strategy in reader.strategies:
        read = _try(strategy, ctx)
        if isinstance(read, _Skip):
            if read.reason not in (s.reason for s in skipped):
                skipped.append(read)
            continue
        if read is None:
            continue
        read.doubts += [s.reason for s in skipped if s.doubt and s.reason not in read.doubts]
        value = _run(reader.clean, read.raw)
        read.value = None if _empty(value) else _coerced(value, sp)
        if read.value is None:
            read.reasons.append(f"cleaning left nothing of {read.raw!r}")
        read_any = True
        yield read
    if not read_any and skipped:
        yield _Read(reasons=[s.reason for s in skipped])


def _coerced(value: Any, sp: SchemaPath) -> Any:
    """``value`` coerced to the path's type, as ``_coerce`` does, except that no number is changed into another.

    For a number or integer path, text whose commas are not thousands separators in groups of three stays the text
    it is, and so does a number that is not whole for an integer path; the type check then fails. (Fill's own
    ``_coerce`` is left as it is: a filled form is checked again by a person, an extracted value is not.)
    """
    if sp.type not in ("integer", "number") or isinstance(value, bool):
        return _coerce(value, sp)
    if isinstance(value, str):
        bare = _NOT_DIGITS.sub("", value)
        if "," in bare and not _GROUPED.fullmatch(bare):
            return value
        number = _number(value)
        if not isinstance(number, (int, float)) or isinstance(number, bool):
            return value  # not a number at all: the text, which fails the type check
        if sp.type == "integer" and not float(number).is_integer():
            return value  # "12.7" stays "12.7", not 12
        value = number
    if sp.type == "integer" and isinstance(value, float) and not value.is_integer():
        return value
    return _coerce(value, sp)


def _fall_back(
    schema: dict, tries: dict[str, Iterator[_Read]], reads: dict[str, _Read], errors: dict[str, list[str]]
) -> bool:
    """Put in place of each failing read the first later read of its path that passes; whether any was replaced."""
    changed = False
    for path, tried in tries.items():
        if not (reads[path].reasons or errors.get(path)):
            continue
        first = reads[path]
        for later in tried:
            if not later.reasons and not _errors_at(schema, path, later.value):
                if later.value != first.value:  # two strategies disagree: the later one is not certain
                    doubt = f"the first read ({first.strategy}) gave {first.raw!r}, which failed its checks"
                    later.doubts.append(doubt)
                reads[path], changed = later, True
                break
    return changed


def _errors_at(schema: dict, path: str, value: Any) -> list[str]:
    """The schema's errors at or below ``path`` for ``value`` checked alone (the other fields left out)."""
    record: dict = {}
    _set(record, path, value)
    return [message for location, message in _validate(schema, record) if _at_or_below(location, path)]


def _validate(schema: dict, record: dict) -> list[tuple[str, str]]:
    try:
        return validate_data_located(schema, record)
    except FormcastError as exc:  # an invalid schema or $ref
        raise _SchemaError from exc


def _at_or_below(location: str, path: str) -> bool:
    """Whether ``location`` is ``path`` or a place inside it (``tags[1]`` is inside ``tags``)."""
    return location == path or location.startswith((path + ".", path + "["))


def _try(strategy: Strategy, ctx: _Context) -> _Read | _Skip | None:
    """What one strategy reads on the document; ``None`` when it finds no text, a :class:`_Skip` when it could not
    be trusted to read (a box whose neighbourhood moved) or an answer could not be told from a label."""
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
        if not ctx.agrees(strategy.page, strategy.rect):
            return _Skip(LAYOUT_DIFFERS, doubt=True)
        rect = ctx.moved(strategy.page, strategy.rect)
        raw = doc.text_in(strategy.page, rect)
        return _Read(raw=raw.strip(), strategy="box", page=strategy.page, rect=rect) if raw and raw.strip() else None
    if isinstance(strategy, AnchorRead):
        places = doc.find_label(strategy.labels, strategy.page)  # every wording counts towards the places
        into_label = False
        for page, label in places:
            printed = ctx.printed(page)
            if strategy.side == "below":
                got = doc.text_below(page, label, strategy.max_lines, stop_at=printed)
            else:
                beside = doc.right_of(page, label, strategy.max_width, stop_at=printed)
                into_label = into_label or beside.into_label
                got = (beside.text, beside.rect) if beside.text is not None and beside.rect is not None else None
            if got and got[0].strip():
                doubts = _doubts("label", len(places), ctx)
                return _Read(raw=got[0].strip(), strategy="anchor", page=page, rect=got[1], doubts=doubts)
        return _Skip(INTO_LABEL) if into_label else None
    if isinstance(strategy, PhraseRead):
        hits = doc.after_phrase(strategy.phrases, strategy.page, strategy.stop)
        if not hits:
            return None
        places = max(len(hits), len(doc.find_label(strategy.phrases, strategy.page)))
        page, text, rect = hits[0]
        doubts = _doubts("phrase", places, ctx)
        return _Read(raw=text.strip(), strategy="phrase", page=page, rect=rect, doubts=doubts)
    return _choice(strategy, ctx)


def _cross_check(reads: dict[str, _Read], ctx: _Context) -> None:
    """Doubt every label or phrase read that another path's read repeats, or that lies in another path's box.

    The same text read for two paths, one of them by a label or phrase, is one answer taken twice (an empty answer's
    label read the next field's): both reads are unsure, "also read as <other path>". A label or phrase read inside
    the box another path is read from, where the layout holds around that box, is unsure, "overlaps <path>'s box".
    """
    by_label = {path: r for path, r in reads.items() if r.strategy in ("anchor", "phrase") and r.raw}
    if not by_label:
        return
    by_text: dict[str, list[str]] = defaultdict(list)
    for path, r in reads.items():
        if r.raw:
            by_text[" ".join(r.raw.split())].append(path)
    for path, r in by_label.items():
        for other in by_text[" ".join(r.raw.split())]:  # type: ignore[union-attr]
            if other != path:
                _doubt(r, f"also read as {other}")
                _doubt(reads[other], f"also read as {path}")
    if ctx.matched == "none":
        return  # boxes are places on a matching layout only
    boxes = [
        (other, s.page, s.rect)
        for other, reader in ctx.template.readers.items()
        if other in reads
        for s in reader.strategies
        if isinstance(s, BoxRead)
    ]
    for path, r in by_label.items():
        for other, page, rect in boxes:
            if other == path or page != r.page or r.rect is None or not ctx.agrees(page, rect):
                continue
            if _overlap(r.rect, ctx.moved(page, rect)):
                _doubt(r, f"overlaps {other}'s box")


def _doubt(read: _Read, why: str) -> None:
    if why not in read.doubts:
        read.doubts.append(why)


def _overlap(a: Rect, b: Rect) -> bool:
    """Whether two rectangles share more than an edge: at least half the lower one's height, and a point across."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width >= 1.0 and height >= 0.5 * min(a[3] - a[1], b[3] - b[1])


def _doubts(what: str, places: int, ctx: _Context) -> list[str]:
    """Why a label or phrase read is not certain: more than one place, or a layout that does not match."""
    doubts = [f"{what} found in {places} places"] if places > 1 else []
    if ctx.matched == "none":
        doubts.append("layout not matched")
    return doubts


def _choice(strategy: ChoiceRead, ctx: _Context) -> _Read | _Skip | None:
    """The marked option's key; the keys of all marked options, with a reason, when several are.

    Drawn boxes are places on the page, as a box read is: when the layout does not hold around any of them, the
    choice is skipped with :data:`LAYOUT_DIFFERS`.
    """
    drawn = [m for m in strategy.options.values() if m.widget is None]
    if ctx.matched != "none" and not all(ctx.agrees(m.page, m.rect) for m in drawn):  # type: ignore[arg-type]
        return _Skip(LAYOUT_DIFFERS, doubt=True)
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
        for location, message in _validate(schema, record):
            target = _original(location, where)
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
            # the error is at the path, at a group the path is in, or inside the path (one item of a list leaf)
            if not target or _at_or_below(path, target) or _at_or_below(target, path):
                out[path].append(message)
    return out


def _original(location: str, where: dict[str, str]) -> str:
    """A record location in the schema's concrete paths: its longest known prefix mapped, the rest kept as it is."""
    for prefix in reversed(_prefixes(location)):
        if prefix in where:
            return where[prefix] + location[len(prefix) :]
    return location


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
