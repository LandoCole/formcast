"""The proof of a template: its readers' results on the samples compared with what the model read on them.

A template built from samples keeps, for each sample, the model's reading of every path it was asked about
(:class:`~formcast.extract.model.SampleRecord`). :func:`prove` reads the very same samples with the template's
readers (:func:`~formcast.extract.read.extract`, no model call) and compares, path by path and sample by sample:

    agree          the readers read the model's answer (below)
    disagree       they read another value, read a value where the model saw none, or none where it saw one
    missing        neither side has a value on this sample
    failed_check   the readers read the model's answer, but the value fails the schema's checks (the form holds a
                   bad answer; both sides read it the same, so it does not count against the readers)
    absent         the path is marked "not on this form"

The readers read the model's answer when their value is it (:func:`same_value`), or when the model gave the printed
text instead of the value: the answer is the readers' raw text, it is not an answer the schema accepts at the path
("04/19/2011" for a date), and the reader's own clean steps and type coercion (``read.cleaned``) turn it into the
readers' value ("2011-04-19"). An answer the schema accepts must be the value itself: a map that turns a printed "CA"
into "US" disagrees with a model that read "CA", although cleaning "CA" the reader's way also gives "US".

A path is *ready* when it is not absent, no sample disagrees, and at least one sample agrees or failed its check:
readers that agree only on blanks have proven nothing. A path the model was not asked about on a sample counts its
reading as ``None`` there, and so does a blank reading (``""``).

:func:`learn_maps` takes a wording the model answered with an allowed answer of an ``enum`` path ("United States of
America" -> ``"US"``) into the reader's ``map`` transform, so the next proof agrees; :func:`sample_marks` gives the
places on one sample a person edits a reader by (where a value was read, the boxes, labels and options the readers
look at). None of them calls a model.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from formcast.data.mapping import Transform, _empty, _generic, _text
from formcast.errors import FormcastError
from formcast.extract.layout import page_fit
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceRead,
    ExtractionTemplate,
    FieldResult,
    Reader,
    SampleRecord,
    concrete_paths,
)
from formcast.extract.page import DocText, label_key
from formcast.extract.read import _errors_at, _extract_open, cleaned, extract, now
from formcast.geometry import Rect
from formcast.match import PdfFeatures
from formcast.schema import SchemaPath, flatten_schema, resolve

Outcome = Literal["agree", "disagree", "missing", "failed_check", "absent"]

#: What a number may carry besides its digits (currency, percent, spaces), as ``read._coerced`` drops them.
_NOT_DIGITS = re.compile(r"[$€£%\s]")
#: A number: commas only as thousands separators in groups of three ("1,234,567"; "1,5" is not a number).
_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|[-+]?\.\d+")
#: A leading zero before another digit: a code such as a ZIP or an account ("02108"), not a quantity.
_CODE = re.compile(r"[-+]?0\d")
#: Read statuses that give a value the readers stand by.
_READ = ("found", "unsure")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleProof(_Model):
    """One sample's side of a path's proof: what the readers read, the model's reading, and how they compare."""

    file: str
    outcome: Outcome
    read: FieldResult
    opinion: Any = None


class FieldProof(_Model):
    """One path proven on every sample: whether its readers are ready, and each sample's outcome in order."""

    path: str
    ready: bool
    absent: bool
    samples: list[SampleProof]

    @property
    def count(self) -> tuple[int, int]:
        """(samples that agree or failed their check alike, samples that are not ``missing``)."""
        proven = sum(s.outcome in ("agree", "failed_check") for s in self.samples)
        return proven, sum(s.outcome != "missing" for s in self.samples)


class Proof(_Model):
    """Every concrete path's proof, in schema order; ``ready`` of ``total`` paths (absent paths not counted)."""

    fields: list[FieldProof]
    ready: int
    total: int


# ---------------------------------------------------------------- comparing


def _as_number(value: Any) -> float | None:
    """``value`` as a number when it is one, or text that is one as ``read._coerced`` takes it; else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = _NOT_DIGITS.sub("", value)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not _NUMBER.fullmatch(text) or _CODE.match(text):
        return None
    return float(text.replace(",", ""))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def same_value(read: Any, opinion: Any) -> bool:
    """Whether the readers' ``read`` and the model's ``opinion`` are the same answer, written either way.

    ``None`` equals only ``None``; two bools compare as bools; two lists item by item. When both sides are numbers
    (text with thousands commas in groups of three, currency or percent counts) they compare as numbers; a number
    never equals text that is not one ("1,5" is not 15, "02108" is not 2108). Otherwise the texts compare by
    :func:`~formcast.extract.page.label_key`: case, spacing and punctuation do not matter ("84-2219037" is
    "842219037"), letters and digits do.
    """
    if read is None or opinion is None:
        return read is None and opinion is None
    if isinstance(read, bool) and isinstance(opinion, bool):
        return read == opinion
    if isinstance(read, list) and isinstance(opinion, list):
        return len(read) == len(opinion) and all(same_value(a, b) for a, b in zip(read, opinion))
    a, b = _as_number(read), _as_number(opinion)
    if a is not None and b is not None:
        return a == b
    if _is_number(read) or _is_number(opinion):
        return False
    ka, kb = label_key(_text(read)), label_key(_text(opinion))
    if not ka and not kb:  # punctuation only on both sides: the marks themselves must match
        return _text(read).strip() == _text(opinion).strip()
    return ka == kb


def _read_answer(read: FieldResult, opinion: Any, reader: Reader | None, sp: SchemaPath, schema: dict) -> bool:
    """Whether the readers read the model's answer ``opinion``: as their value, or from the same printed text.

    The printed text counts only for an answer the schema refuses at the path (the model gave the text, not the
    value), and only when the reader's cleaning of the answer gives the readers' value; see the module docstring.
    """
    if opinion is None:
        return False
    if same_value(read.value, opinion):
        return True
    if reader is None or read.raw is None or not same_value(read.raw, opinion):
        return False
    if not _errors_at(schema, read.path, opinion):
        return False  # already an answer the schema accepts: the value had to be it
    return same_value(cleaned(reader, str(opinion), sp), read.value)


def _outcome(read: FieldResult, absent: bool, answered: bool, opinion: Any) -> Outcome:
    """The sample's outcome; ``answered``: the readers read the model's answer (:func:`_read_answer`)."""
    if absent:
        return "absent"
    if read.status in _READ:
        return "agree" if answered else "disagree"
    if read.status == "failed_check":
        return "failed_check" if answered else "disagree"
    return "missing" if opinion is None else "disagree"  # missing or absent (an item with nothing in it)


# ------------------------------------------------------------------ proving


def _check_samples(template: ExtractionTemplate, pdfs: list[bytes]) -> None:
    samples = template.samples
    if len(pdfs) != len(samples):
        raise FormcastError(
            f"these PDFs are not the template's samples: {len(pdfs)} given, the template has {len(samples)}"
        )
    for n, (sample, pdf) in enumerate(zip(samples, pdfs)):
        if hashlib.sha256(pdf).hexdigest() != sample.sha256:
            raise FormcastError(f"these PDFs are not the template's samples: PDF {n} is not {sample.file!r}")


def _opinion(sample: SampleRecord, path: str) -> Any:
    """The model's reading of ``path`` on ``sample``; ``None`` when it was not asked, or saw a blank."""
    value = sample.opinion.get(path)
    return None if _empty(value) else value


def prove(template: ExtractionTemplate, pdfs: list[bytes]) -> Proof:
    """The template's readers proven on its samples: ``pdfs`` are the samples' bytes, in the template's order.

    Every concrete path of the schema gets a :class:`FieldProof`, as the module says. No model is called.

    Raises:
        FormcastError: when ``pdfs`` are not the template's samples (another count, or a file whose sha256 is not
            its sample's), or a sample cannot be read.
    """
    _check_samples(template, pdfs)
    results: list[dict[str, FieldResult]] = []
    for sample, pdf in zip(template.samples, pdfs):
        result = extract(template, pdf, file=sample.file)
        if result.error is not None:
            raise FormcastError(f"sample {sample.file!r} cannot be read: {result.error}")
        results.append({f.path: f for f in result.fields})

    schema = template.schema_
    schema_paths = flatten_schema(schema)
    fields: list[FieldProof] = []
    for path in concrete_paths(schema):
        absent = path in template.absent
        reader, sp = template.readers.get(path), resolve(schema_paths, _generic(path)[0])
        samples = []
        for sample, by_path in zip(template.samples, results):
            read = by_path.get(path) or FieldResult(path=path, status="missing", raw=None)
            opinion = _opinion(sample, path)
            outcome = _outcome(read, absent, _read_answer(read, opinion, reader, sp, schema), opinion)
            samples.append(SampleProof(file=sample.file, outcome=outcome, read=read, opinion=opinion))
        outcomes = [s.outcome for s in samples]
        ready = (
            not absent and "disagree" not in outcomes and any(o in ("agree", "failed_check") for o in outcomes)
        )
        fields.append(FieldProof(path=path, ready=ready, absent=absent, samples=samples))
    return Proof(fields=fields, ready=sum(f.ready for f in fields), total=sum(not f.absent for f in fields))


# ------------------------------------------------------------- learning maps


def _allowed(value: Any, enum: list[str]) -> str | None:
    """The enum answer ``value`` is (case aside), or ``None`` when it is none of them."""
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    return next((answer for answer in enum if answer.casefold() == key), None)


def _wordings(field: FieldProof, enum: list[str]) -> dict[str, str]:
    """Each raw text the readers read that is not an allowed answer -> the allowed answer the model read there.

    A wording is learned only when every sample that has it was read by the model as the same allowed answer.
    """
    seen: dict[str, str] = {}  # casefolded raw -> the raw as first read
    answers: dict[str, set[str | None]] = {}
    for s in field.samples:
        raw = s.read.raw
        if raw is None or not raw.strip() or _allowed(raw, enum) is not None:
            continue
        key = raw.strip().casefold()
        seen.setdefault(key, raw.strip())
        answers.setdefault(key, set()).add(_allowed(s.opinion, enum))
    learned: dict[str, str] = {}
    for key, got in answers.items():
        answer = got.pop() if len(got) == 1 else None
        if answer is not None:
            learned[seen[key]] = answer
    return learned


def learn_maps(template: ExtractionTemplate, proof: Proof) -> ExtractionTemplate:
    """A copy of ``template`` whose ``enum`` readers map each wording the model answered with an allowed answer.

    For every path of ``proof`` whose schema has an ``enum``, a raw text the readers read on a sample that is not an
    allowed answer, where the model read an allowed one, is added to the reader's ``map`` transform (raw -> answer;
    case-insensitive, as ``map`` is). The map is the reader's first clean step, so it sees the raw text: one is put
    first when the first step is not a map. A wording the model read as different answers on different samples is
    not learned. No model is called.
    """
    schema_paths: list[SchemaPath] = flatten_schema(template.schema_)
    out = template.model_copy(deep=True)
    for field in proof.fields:
        reader = out.readers.get(field.path)
        if field.absent or reader is None:
            continue
        sp = resolve(schema_paths, _generic(field.path)[0])
        if sp.type != "enum" or not sp.enum:
            continue
        learned = _wordings(field, sp.enum)
        if not learned:
            continue
        clean = list(reader.clean)
        if clean and clean[0].op == "map":
            replaced = {key.casefold() for key in learned}
            known = {k: v for k, v in (clean[0].args.get("values") or {}).items() if str(k).casefold() not in replaced}
            clean[0] = Transform(op="map", args={**clean[0].args, "values": {**known, **learned}})
        else:
            clean.insert(0, Transform(op="map", args={"values": learned}))
        out.readers[field.path] = reader.model_copy(update={"clean": clean})
    return out


# -------------------------------------------------------------------- marks


class Mark(_Model):
    """A place on a sample to show beside a path: where its value was ``read``, a ``box`` or ``label`` its readers
    look at, or an ``option`` of a choice (``answer`` is the option's key)."""

    path: str
    kind: Literal["read", "box", "label", "option"]
    page: int
    rect: Rect
    answer: str | None = None


def _moved(rect: Rect, dy: float) -> Rect:
    return (rect[0], rect[1] + dy, rect[2], rect[3] + dy)


def sample_marks(template: ExtractionTemplate, pdf: bytes) -> list[Mark]:
    """The marks of every path read on one sample, path by path in schema order; absent paths have none.

    Per path: where its value was read (when its read has a place: a widget read has none), each ``box`` strategy's
    rectangle moved down by its page's offset (:func:`~formcast.extract.layout.page_fit`; 0 on a page the layout has
    no lines on), the first place each ``anchor`` strategy's label is found, and each ``choice`` option: its drawn
    box moved by the offset, or its widget's rectangle. The PDF is opened once for reading. No model is called.

    Raises:
        FormcastError: when the PDF cannot be opened or read.
    """
    marks: list[Mark] = []
    with DocText(pdf) as doc:
        pages = {line.page for line in template.layout.lines}
        offsets = {page: page_fit(template.layout, doc, page).offset for page in pages if page < doc.page_count}
        result = _extract_open(template, doc, PdfFeatures(pdf), pdf, doc_id="sample", file="", at=now())
        if result.error is not None:
            raise FormcastError(f"cannot read the sample: {result.error}")
        reads = {f.path: f for f in result.fields}

        def on_page(page: int) -> bool:
            return 0 <= page < doc.page_count

        for path in concrete_paths(template.schema_):
            reader = template.readers.get(path)
            if path in template.absent:
                continue
            read = reads.get(path)
            if read is not None and read.page is not None and read.rect is not None:
                marks.append(Mark(path=path, kind="read", page=read.page, rect=read.rect))
            for strategy in reader.strategies if reader else []:
                if isinstance(strategy, BoxRead) and on_page(strategy.page):
                    rect = _moved(strategy.rect, offsets.get(strategy.page, 0.0))
                    marks.append(Mark(path=path, kind="box", page=strategy.page, rect=rect))
                elif isinstance(strategy, AnchorRead):
                    places = doc.find_label(strategy.labels, strategy.page)
                    if places:
                        marks.append(Mark(path=path, kind="label", page=places[0][0], rect=places[0][1]))
                elif isinstance(strategy, ChoiceRead):
                    for key, mark in strategy.options.items():
                        if mark.widget is not None:
                            for page, rect in doc.widget_places(mark.widget, mark.state):
                                marks.append(Mark(path=path, kind="option", page=page, rect=rect, answer=key))
                        elif mark.page is not None and mark.rect is not None and on_page(mark.page):
                            rect = _moved(mark.rect, offsets.get(mark.page, 0.0))
                            marks.append(Mark(path=path, kind="option", page=mark.page, rect=rect, answer=key))
    return marks
