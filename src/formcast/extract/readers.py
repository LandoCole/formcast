"""From a model's reader proposals to an extraction template's readers, by code: only what the samples have is kept.

A model that has looked at the sample PDFs proposes, for each schema path, how to read it (a
:class:`ReaderProposal`): the form fields that hold it, a box on one sample (a blank the detectors found, ``"c0_3"``,
or a form field), the printed labels beside it, the phrases before it in running text, or, for a choice, where each
option's check mark is (:class:`OptionProposal`). :func:`resolve` turns these into plan-1 strategies with no model
call, and checks every name against the samples first: a widget, box, label or phrase that no sample has is dropped
with a warning, never kept, so a template never reads by something the model made up.

    widget  kept when a sample has a form field of exactly that name
    box     kept when the sample it names has that blank (or form field)
    label   kept when ``DocText.find_label`` finds it on a sample
    phrase  kept when ``DocText.after_phrase`` finds text after it, up to the proposal's stop, on a sample

A path's strategies come in the order plan 1's template has them: ``WidgetRead`` (the kept names), one ``BoxRead``
(the proposal's box, else the first kept widget's rectangle, so a flattened copy is read where the fillable one has
the field), ``AnchorRead`` (the kept labels), ``PhraseRead`` (the kept phrases); then, for options, a ``ChoiceRead``
of the options' form fields and a ``ChoiceRead`` of drawn boxes (each option's box, else its form field's rectangle).
An anchor or phrase read is kept to the page its first kept label or phrase is on, or searches every page when its
labels or phrases are on different pages.

A choice keeps the options the samples have, except on a ``boolean`` path proposed with more than one option: there
a choice that lost one is left out (``read`` takes a one-option choice on a yes/no path for a lone check box, so a
"false" box left alone would read every unmarked copy as true). When an answer's mark is on no sample at all no
choice is kept; when only an answer's form field is missing (it was proposed by its box alone) the choice of the form
fields is left out and the choice of the drawn boxes, which has every answer, is kept.

A box is stored in the layout's place: ``extract`` moves a template's box down by the document's offset
(:func:`~formcast.extract.layout.page_fit`), so a rectangle taken from a sample is moved up by that sample page's
offset. A box from a copy pushed down the page is then where it is on the layout.

Warnings are sentences for a person: ``"<path>: <what> '<value>' is on no sample; left out"`` for each dropped item,
``"<path>: an answer's box is on no sample; the choice is left out"`` for a yes/no choice that lost an answer,
``"<path>: no way to read it was found on the samples"`` for a path left with no strategy, which gets no reader, and
``"<path>: the model found no place for it on the samples"`` for a proposal that names nothing at all (the model's
way of saying the form has no place for it), which gets none either.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from formcast.data.mapping import Transform, _generic
from formcast.extract.layout import page_fit
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    Layout,
    PhraseRead,
    Reader,
    Strategy,
    WidgetRead,
    concrete_paths,
)
from formcast.extract.page import DocText
from formcast.extract.samples import SampleDoc
from formcast.geometry import Rect
from formcast.schema import flatten_schema
from formcast.schema import resolve as schema_at

__all__ = ["BoxRef", "OptionProposal", "ReaderProposal", "resolve"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoxRef(_Model):
    """A box on one sample (``sample``, its position from 0): a detected blank's id (``"c0_3"``) or a form field's name."""

    sample: int
    id: str


class OptionProposal(_Model):
    """Where one option's check mark is: a form field (``state`` naming one button of a radio group), a box, or both."""

    widget: str | None = None
    state: str | None = None
    box: BoxRef | None = None


class ReaderProposal(_Model):
    """How a model proposes to read one schema path; :func:`resolve` keeps what the samples have of it.

    ``side``, ``max_lines`` and ``max_width`` go to the label read, ``stop`` to the phrase read and ``clean`` to the
    reader, as in :class:`~formcast.extract.model.AnchorRead`, :class:`~formcast.extract.model.PhraseRead` and
    :class:`~formcast.extract.model.Reader`.
    """

    path: str
    widgets: list[str] = Field(default_factory=list)
    box: BoxRef | None = None
    labels: list[str] = Field(default_factory=list)
    side: Literal["right", "below"] = "right"
    max_lines: int = 1
    max_width: float = 300.0
    phrases: list[str] = Field(default_factory=list)
    stop: Literal["comma", "sentence", "line", "digits"] = "comma"
    options: dict[str, OptionProposal] = Field(default_factory=dict)
    clean: list[Transform] = Field(default_factory=list)


#: A box found on a sample: its page and its rectangle in the layout's place.
_Place = tuple[int, Rect]
#: Records one dropped item as a warning: what it is ("label") and its value.
_Dropped = Callable[[str, str], None]


@dataclass
class _Samples:
    """The samples open for reading, and each sample page's offset from the layout, worked out once."""

    docs: list[SampleDoc]
    texts: list[DocText]
    layout: Layout
    offsets: dict[tuple[int, int], float] = field(default_factory=dict)

    def _in_layout(self, sample: int, page: int, rect: Rect) -> _Place:
        """``rect`` on the sample's ``page`` moved up by the page's offset: where it is on the layout."""
        if (sample, page) not in self.offsets:
            self.offsets[sample, page] = page_fit(self.layout, self.texts[sample], page).offset
        dy = self.offsets[sample, page]
        return page, (rect[0], rect[1] - dy, rect[2], rect[3] - dy)

    def widget(self, name: str, state: str | None = None) -> _Place | None:
        """The first rectangle of the form field ``name`` (its button ``state``) on the first sample that has it."""
        for sample, text in enumerate(self.texts):
            places = text.widget_places(name, state)
            if places:
                return self._in_layout(sample, *places[0])
        return None

    def box(self, ref: BoxRef) -> _Place | None:
        """The detected blank of id ``ref.id`` on the sample ``ref`` names, else its form field of that name."""
        if not 0 <= ref.sample < len(self.docs):
            return None
        for page in self.docs[ref.sample].form_map.pages:
            for candidate in page.candidates:
                if candidate.id == ref.id:
                    return self._in_layout(ref.sample, page.index, candidate.rect)
        places = self.texts[ref.sample].widget_places(ref.id)
        return self._in_layout(ref.sample, *places[0]) if places else None

    def label_page(self, label: str) -> int | None:
        """The page ``label`` is first found on, on the first sample that has it."""
        for text in self.texts:
            places = text.find_label([label], None)
            if places:
                return places[0][0]
        return None

    def phrase_page(self, phrase: str, stop: str) -> int | None:
        """The page ``phrase`` is first found on with text after it up to ``stop``, on the first sample that has it."""
        for text in self.texts:
            hits = text.after_phrase([phrase], None, stop)
            if hits:
                return hits[0][0]
        return None


def resolve(
    proposals: list[ReaderProposal], samples: list[SampleDoc], layout: Layout, schema: dict
) -> tuple[dict[str, Reader], list[str]]:
    """Readers for the proposals' paths, keeping only what the samples have, and the warnings of what was left out.

    A proposal for a path that is not a concrete path of ``schema`` is ignored, and so is a second proposal for one
    path, each with a warning. No model is called; see the module docstring.

    Raises:
        FormcastError: when a sample's PDF cannot be opened.
    """
    known = set(concrete_paths(schema))
    schema_paths = flatten_schema(schema)
    readers: dict[str, Reader] = {}
    warnings: list[str] = []
    seen: set[str] = set()
    with ExitStack() as stack:
        texts = [stack.enter_context(DocText(doc.pdf)) for doc in samples]
        found = _Samples(samples, texts, layout)
        for proposal in proposals:
            if proposal.path not in known:
                warnings.append(f"{proposal.path}: not a field of the schema; ignored")
                continue
            if proposal.path in seen:
                warnings.append(f"{proposal.path}: proposed more than once; the first proposal is kept")
                continue
            seen.add(proposal.path)
            yes_no = schema_at(schema_paths, _generic(proposal.path)[0]).type == "boolean"
            reader = _reader(proposal, found, warnings, yes_no=yes_no)
            if reader is not None:
                readers[proposal.path] = reader
            elif _names_nothing(proposal):
                warnings.append(f"{proposal.path}: the model found no place for it on the samples")
            else:
                warnings.append(f"{proposal.path}: no way to read it was found on the samples")
    return readers, warnings


def _names_nothing(proposal: ReaderProposal) -> bool:
    """Whether the proposal names no widget, box, label, phrase or option: only its path."""
    return not (proposal.widgets or proposal.box or proposal.labels or proposal.phrases or proposal.options)


def _reader(proposal: ReaderProposal, found: _Samples, warnings: list[str], *, yes_no: bool) -> Reader | None:
    """The proposal's reader from what the samples have of it; ``None`` when nothing is left. Warns of each drop.

    ``yes_no``: the path is a ``boolean`` (see the module docstring on choices).
    """

    def dropped(what: str, value: str) -> None:
        warnings.append(f"{proposal.path}: {what} '{value}' is on no sample; left out")

    names: list[str] = []
    widget_box: _Place | None = None
    for name in dict.fromkeys(proposal.widgets):
        place = found.widget(name)
        if place is None:
            dropped("widget", name)
            continue
        names.append(name)
        widget_box = widget_box or place
    box = None
    if proposal.box is not None:
        box = found.box(proposal.box)
        if box is None:
            dropped("box", proposal.box.id)
    box = box or widget_box
    labels = _kept(proposal.labels, found.label_page, "label", dropped)
    phrases = _kept(proposal.phrases, lambda p: found.phrase_page(p, proposal.stop), "phrase", dropped)

    strategies: list[Strategy] = []
    if names:
        strategies.append(WidgetRead(names=names))
    if box is not None:
        strategies.append(BoxRead(page=box[0], rect=box[1]))
    if labels:
        strategies.append(
            AnchorRead(
                labels=list(labels), page=_one_page(labels), side=proposal.side, max_width=proposal.max_width,
                max_lines=proposal.max_lines,
            )
        )  # fmt: skip
    if phrases:
        strategies.append(PhraseRead(phrases=list(phrases), page=_one_page(phrases), stop=proposal.stop))
    strategies += _choices(proposal, found, dropped, warnings, yes_no=yes_no)
    return Reader(strategies=strategies, clean=proposal.clean) if strategies else None


def _kept(
    values: list[str], page_of: Callable[[str], int | None], what: str, dropped: _Dropped
) -> dict[str, int]:
    """Each of ``values`` (once) that ``page_of`` finds, with its page, in order; the others dropped with a warning."""
    kept: dict[str, int] = {}
    for value in dict.fromkeys(values):
        page = page_of(value)
        if page is None:
            dropped(what, value)
        else:
            kept[value] = page
    return kept


def _one_page(pages: dict[str, int]) -> int | None:
    """The page all the kept labels (or phrases) are first found on; ``None`` (every page) when they differ."""
    return next(iter(pages.values())) if len(set(pages.values())) == 1 else None


def _choices(
    proposal: ReaderProposal, found: _Samples, dropped: _Dropped, warnings: list[str], *, yes_no: bool
) -> list[Strategy]:
    """A choice of the options' form fields, then one of their drawn boxes, each with the options the samples have;
    on a yes/no path of several options, only the choices that kept every option (see the module docstring)."""
    by_widget: dict[str, ChoiceMark] = {}
    by_box: dict[str, ChoiceMark] = {}
    for key, option in proposal.options.items():
        place = None
        if option.widget is not None:
            place = found.widget(option.widget, option.state)
            if place is None:
                if option.state is None:
                    dropped("widget", option.widget)
                else:
                    dropped(f"widget '{option.widget}' button", option.state)
            else:
                by_widget[key] = ChoiceMark(widget=option.widget, state=option.state)
        if option.box is not None:
            ref = found.box(option.box)
            if ref is None:
                dropped("box", option.box.id)
            place = ref or place
        if place is not None:
            by_box[key] = ChoiceMark(page=place[0], rect=place[1])
        elif option.widget is None and option.box is None:
            warnings.append(f"{proposal.path}: option '{key}' names no widget and no box; left out")
    choices = [marks for marks in (by_widget, by_box) if marks]
    if yes_no and len(proposal.options) > 1:
        if len(by_box) < len(proposal.options):  # an answer's mark is on no sample: not even the boxes are whole
            warnings.append(f"{proposal.path}: an answer's box is on no sample; the choice is left out")
        choices = [marks for marks in choices if len(marks) == len(proposal.options)]
    return [ChoiceRead(options=marks) for marks in choices]
