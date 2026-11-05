"""From a model's reader proposals to an extraction template's readers, by code: only what the samples have is kept.

A model that has looked at the sample PDFs proposes, for each schema path, how to read it (a
:class:`ReaderProposal`): the form fields that hold it, a box on one sample (a blank the detectors found, ``"c0_3"``,
a form field, or the text line that holds the answer on a page with no form fields, ``"t0_12"``), the printed labels
beside it, the phrases before it in running text, or, for a choice, where each
option's check mark is (:class:`OptionProposal`). :func:`resolve` turns these into plan-1 strategies with no model
call, and checks every name against the samples first: a widget, box, label or phrase that no sample has is dropped
with a warning, never kept, so a template never reads by something the model made up.

    widget  kept when a sample has a form field of exactly that name
    box     kept when the sample it names has that blank (or form field, or text line)
    label   kept when ``DocText.find_label`` finds it on a sample
    phrase  kept when ``DocText.after_phrase`` finds text after it, up to the proposal's stop, on a sample

A path's strategies come in the order plan 1's template has them (but a choice first, see below): ``WidgetRead`` (the kept names), one ``BoxRead``
(the proposal's box, else the first kept widget's rectangle, so a flattened copy is read where the fillable one has
the field), ``AnchorRead`` (the kept labels), ``PhraseRead`` (the kept phrases); then, for options, a ``ChoiceRead``
of the options' form fields and a ``ChoiceRead`` of drawn boxes (each option's box, else its form field's rectangle).
A proposal's ``places`` (the other places the same answer is written) become the reader's places: a form field named
by name or form map id reads that field (on the id's page), any other box is read where it is; one no sample has is
dropped with a warning, as a box is.
An anchor or phrase read is kept to the page its first kept label or phrase is on, or searches every page when its
labels or phrases are on different pages.

A path with options is answered by its marks: its choices come first, and a widget read of only the options' own
buttons is dropped (a radio group read raw gives its on-state, "2", which no check passes, and a failing first read
makes every later answer unsure). A choice keeps the options the samples have, except on a ``boolean`` path proposed with more than one option: there
a choice that lost one is left out (``read`` takes a one-option choice on a yes/no path for a lone check box, so a
"false" box left alone would read every unmarked copy as true). When an answer's mark is on no sample at all no
choice is kept; when only an answer's form field is missing (it was proposed by its box alone) the choice of the form
fields is left out and the choice of the drawn boxes, which has every answer, is kept.

A text line is the answer itself, written on the sample: its box is the line (the lines, for ids joined with "+",
``"t0_12+t0_13"`` for an answer over two lines, all on one page) widened to the space the answer has on its row
(:func:`text_cell`, which stops at a table's column rules), so a longer answer on another copy is read whole and the
box never takes a neighbour's text.

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

import re

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from formcast.data.mapping import Transform, _generic
from formcast.extract.layout import PageFit, page_fit
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    Layout,
    PhraseRead,
    Place,
    Reader,
    Strategy,
    WidgetRead,
    concrete_paths,
)
from formcast.extract.page import DocText
from formcast.extract.samples import SampleDoc, text_lines
from formcast.geometry import Rect
from formcast.schema import flatten_schema
from formcast.schema import resolve as schema_at

__all__ = ["BoxRef", "OptionProposal", "ReaderProposal", "resolve", "text_cell"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoxRef(_Model):
    """A box on one sample (``sample``, its position from 0): a detected blank's id (``"c0_3"``), a text line's
    (``"t0_12"``, or ``"t0_12+t0_13"`` for an answer over two lines) or a form field's name."""

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
    places: list[BoxRef] = Field(default_factory=list)  # the other places the same answer is written


#: A form map's widget id, ``w<page>_<index>``, as a proposal may name a form field by.
_WIDGET_ID = re.compile(r"w(\d+)_(\d+)")
#: A sample's text line as a proposal names it, ``t<page>_<n>`` (:func:`~formcast.extract.samples.text_lines`).
_TEXT_ID = re.compile(r"t(\d+)_(\d+)")
#: How far (points) the box of a text line reaches above and below the line.
TEXT_PAD = 1.5
#: The furthest (points) the box of a text line reaches left or right of the line, when no text on its row is nearer.
TEXT_REACH = 150.0
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
    fits: dict[tuple[int, int], PageFit] = field(default_factory=dict)

    def _in_layout(self, sample: int, page: int, rect: Rect) -> _Place:
        """``rect`` on the sample's ``page`` moved up by the page's offset: where it is on the layout."""
        if (sample, page) not in self.fits:
            self.fits[sample, page] = page_fit(self.layout, self.texts[sample], page)
        return page, self.fits[sample, page].in_layout(rect)

    def widget(self, name: str, state: str | None = None, page: int | None = None) -> _Place | None:
        """The first rectangle of the form field ``name`` (its button ``state``, on ``page`` when given) on the first
        sample that has it."""
        for sample, text in enumerate(self.texts):
            places = text.widget_places(name, state, page)
            if places:
                return self._in_layout(sample, *places[0])
        return None

    def widget_ref(self, ref: str) -> tuple[str, int | None]:
        """A proposal's form field as ``(name, page)``: a name as it is (page ``None``), or a form map id such as
        ``w27_3`` (a name the samples do not have) as that widget's name and page, so a form that reuses a name on
        several pages is read on the page meant."""
        m = _WIDGET_ID.fullmatch(ref)
        if m is None or any(text.has_widget(ref) for text in self.texts):
            return ref, None
        page, index = int(m.group(1)), int(m.group(2))
        for doc in self.docs:
            if page < len(doc.form_map.pages) and index < len(widgets := doc.form_map.pages[page].widgets):
                return widgets[index].name, page
        return ref, None

    def read_only(self, name: str) -> bool:
        """Whether a sample has the form field ``name`` as read-only: the form's own printing, not an answer."""
        return any(text.has_widget(name) and text.read_only(name) for text in self.texts)

    def reused(self, name: str) -> bool:
        """Whether a sample has the form field ``name`` on several pages holding different values."""
        for text in self.texts:
            pages = text.widget_pages(name)
            if len(pages) > 1 and len({repr(text.widget_value(name, p)) for p in pages}) > 1:
                return True
        return False

    def box(self, ref: BoxRef) -> _Place | None:
        """The detected blank of id ``ref.id`` on the sample ``ref`` names, its text line (or lines) of that id
        (``t0_12``, ``t0_12+t0_13``) as :func:`text_cell` widens them, else its form field of that name or form map id
        (``w1_0``)."""
        if not 0 <= ref.sample < len(self.docs):
            return None
        if (text := self.text_box(ref)) is not None:
            return text
        for page in self.docs[ref.sample].form_map.pages:
            for candidate in page.candidates:
                if candidate.id == ref.id:
                    return self._in_layout(ref.sample, page.index, candidate.rect)
        name, page = self.widget_ref(ref.id)  # a form field's name, or its form map id
        places = self.texts[ref.sample].widget_places(name, page=page)
        return self._in_layout(ref.sample, *places[0]) if places else None

    def text_box(self, ref: BoxRef) -> _Place | None:
        """The box of the text lines ``ref.id`` names on its sample, ``None`` when it names no line of one page."""
        ids = [_TEXT_ID.fullmatch(part.strip()) for part in ref.id.split("+")]
        if not all(ids) or len({m.group(1) for m in ids if m}) != 1:
            return None
        page = int(ids[0].group(1))  # type: ignore[union-attr]
        lines = text_lines(self.docs[ref.sample], self.texts[ref.sample], page)
        chosen = {int(m.group(2)) for m in ids if m}
        if any(n >= len(lines) for n in chosen):
            return None
        rect = _union([lines[n][1] for n in sorted(chosen)])
        others = [r for n, (_, r) in enumerate(lines) if n not in chosen]
        walls = self.texts[ref.sample].walls(page)
        width = self.texts[ref.sample].page_sizes()[page][0]
        return self._in_layout(ref.sample, page, text_cell(rect, others, width, walls=walls))

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


def text_cell(rect: Rect, others: list[Rect], page_width: float, *, walls: list[Rect] = ()) -> Rect:
    """The box of an answer written at ``rect``, among the page's ``others`` text lines and drawn vertical ``walls``
    (a table's column rules): ``rect`` with :data:`TEXT_PAD` above and below, reaching left and right halfway to the
    nearest line on its row (one that shares half the height of the shorter of the two and lies wholly to that side),
    else :data:`TEXT_REACH` points, but never past a wall that crosses its row, nor off the page. A table's cells each
    keep their own answer, an empty cell beside it included; a lone answer may grow on another copy."""
    x0, y0, x1, y1 = rect
    row = [
        r for r in others
        if min(y1, r[3]) - max(y0, r[1]) >= 0.5 * min(y1 - y0, r[3] - r[1])
    ]  # fmt: skip
    left = max((r[2] for r in row if r[2] <= x0), default=None)
    right = min((r[0] for r in row if r[0] >= x1), default=None)
    reach_left = (x0 - left) / 2 if left is not None else TEXT_REACH
    reach_right = (right - x1) / 2 if right is not None else TEXT_REACH
    crossing = [w for w in walls if min(y1, w[3]) - max(y0, w[1]) >= 0.5 * (y1 - y0)]
    wall_left = max((w[2] for w in crossing if w[2] <= x0), default=0.0)
    wall_right = min((w[0] for w in crossing if w[0] >= x1), default=page_width)
    return (
        max(wall_left, x0 - min(reach_left, TEXT_REACH)),
        y0 - TEXT_PAD,
        min(wall_right, x1 + min(reach_right, TEXT_REACH)),
        y1 + TEXT_PAD,
    )


def _union(rects: list[Rect]) -> Rect:
    return min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)


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

    box = None
    if proposal.box is not None:
        box = found.box(proposal.box)
        if box is None:
            dropped("box", proposal.box.id)
    labels = _kept(proposal.labels, found.label_page, "label", dropped)
    phrases = _kept(proposal.phrases, lambda p: found.phrase_page(p, proposal.stop), "phrase", dropped)
    # A name the form reuses on several pages for different answers is read on one page: the one its id names, else
    # the page of the field's box or label, so a later form's copy of "Address01" is not read off the first page.
    hint = box[0] if box is not None else next(iter(labels.values()), None)
    names: list[str] = []
    pages: set[int | None] = set()
    widget_box: _Place | None = None
    for ref in dict.fromkeys(proposal.widgets):
        name, page = found.widget_ref(ref)
        if found.read_only(name):
            warnings.append(f"{proposal.path}: the form field {name!r} is read-only, the form's own printing; left out")
            continue
        if page is None and hint is not None and found.reused(name) and found.widget(name, page=hint) is not None:
            page = hint
        place = found.widget(name, page=page)
        if place is None:
            dropped("widget", ref)
            continue
        names.append(name)
        pages.add(page)
        widget_box = widget_box or place
    box = box or widget_box

    strategies: list[Strategy] = []
    if names:
        strategies.append(WidgetRead(names=names, page=pages.pop() if len(pages) == 1 else None))
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
    choices = _choices(proposal, found, dropped, warnings, yes_no=yes_no)
    if choices:
        # a choice is answered by its marks: they are read first, and a raw read of the same buttons (a radio group's
        # on-state, "2") is dropped, so it never stands first and makes every copy's answer unsure
        marked = {m.widget for c in choices if isinstance(c, ChoiceRead) for m in c.options.values() if m.widget}
        strategies = [s for s in strategies if not (isinstance(s, WidgetRead) and set(s.names) <= marked)]
        strategies = choices + strategies
    if not strategies:
        return None
    return Reader(strategies=strategies, clean=proposal.clean, places=_places(proposal.places, found, dropped))


def _places(refs: list[BoxRef], found: _Samples, dropped: _Dropped) -> list[Place]:
    """The proposal's other places that a sample has: a form field (by name, or by id on its page) or a box."""
    places: list[Place] = []
    for ref in refs:
        if 0 <= ref.sample < len(found.docs) and not any(c.id == ref.id for p in found.docs[ref.sample].form_map.pages for c in p.candidates):
            name, page = found.widget_ref(ref.id)
            if found.widget(name, page=page) is not None:
                place: Place = WidgetRead(names=[name], page=page if found.reused(name) else None)
                if place not in places:
                    places.append(place)
                continue
        box = found.box(ref)
        if box is None:
            dropped("place", ref.id)
        elif (place := BoxRead(page=box[0], rect=box[1])) not in places:
            places.append(place)
    return places


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
            name, page = found.widget_ref(option.widget)
            place = found.widget(name, option.state, page)
            if place is None:
                if option.state is None:
                    dropped("widget", option.widget)
                else:
                    dropped(f"widget '{option.widget}' button", option.state)
            else:
                by_widget[key] = ChoiceMark(widget=name, state=option.state, page=page)
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
