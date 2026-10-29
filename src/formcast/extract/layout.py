"""A form's static lines: the printed text every copy shares, learned from samples or a blank form.

A *layout* is the set of text lines that are the same on every copy of a form (its labels, headings and
boilerplate) with where they sit. It is what tells one form from another once the answers are ignored, and what
tells how far a copy has been pushed down the page.

``learn_layout`` takes the layout from a blank form directly, or from filled samples: a line is static when its
normalized text turns up within :data:`LINE_TOLERANCE` points of the same place on at least all but one of the
samples (on all of them when there are fewer than four). An answer is different on every client, so it never
qualifies, whether it is drawn on the page (a flattened copy) or reaches the page through a form field's appearance
stream (a fillable copy); a stamp on one copy does not either.

``layout_score`` is the share of the layout's lines a document has, and ``layout_offset`` is how far down the page
the document's copy sits. Copies are pushed down (a scanner's margin, a header added by a portal), so each page
finds its offset first, the median vertical distance between the layout's lines and the same text on the document
in the same column, and then looks for each line at that offset. Only the layout's own pages count: a document
with extra pages at the end scores as without them, and a document short of pages loses their lines. Lines come
from :class:`~formcast.extract.page.DocText`, in unrotated crop-box points, so a page with ``/Rotate`` scores as
without it.

Measured on the generated corpus (``tests/extract/corpus.py``), for Task 5 to calibrate ``LAYOUT_MATCH`` from:
    - layout learned from five clients' copies, scored on those five and three others in both kinds, each plain,
      shifted by 12 and by ``SHIFT``, rotated, with an extra page, painted, and shifted+rotated+extra at once:
      lowest 0.98 (49 of 50 lines: a client with a long bank name wraps the wire paragraph's last line
      differently), the rest 1.0, for fillable and flattened copies alike and for either kind learned from
    - layout from the blank fillable form (53 lines, 3 of them
      the wire paragraph with its blanks, which no filled copy shares), scored on five
      filled copies of each kind shifted by 12: lowest 0.943
    - highest score of another form (the repo's W-9, 6 pages): 0.0
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from formcast.errors import FormcastError
from formcast.extract.model import Layout, StaticLine
from formcast.extract.page import DocText
from formcast.geometry import Rect

#: How far (points) a line may be from where the layout has it and still be the same line.
LINE_TOLERANCE = 6.0
#: The score at which a document is "this layout" (calibrated in Task 5's corpus test).
LAYOUT_MATCH = 0.9
#: Samples below this number must all have a line for it to be static; from this number one may lack it.
_ALL_OF_THEM_BELOW = 4


def normalize_line(text: str) -> str:
    """Case-folded, with everything that is not a letter or digit as a space and spaces collapsed.

    "Taxpayer ID:" gives "taxpayer id". (``label_key`` in ``page`` is another thing: it joins words without spaces.)
    """
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.casefold()).split())


def _lines(doc: DocText, page: int) -> list[tuple[str, Rect]]:
    """A page's lines as (normalized text, rect), with the lines that have no letter or digit left out."""
    out = []
    for text, rect in doc.lines(page):
        norm = normalize_line(text)
        if norm:
            out.append((norm, rect))
    return out


def _by_text(lines: list[tuple[str, Rect]]) -> dict[str, list[Rect]]:
    found: dict[str, list[Rect]] = defaultdict(list)
    for text, rect in lines:
        found[text].append(rect)
    return found


def _same_place(a: Rect, b: Rect, dy: float = 0.0) -> bool:
    return abs(a[0] - b[0]) <= LINE_TOLERANCE and abs(a[1] + dy - b[1]) <= LINE_TOLERANCE


def learn_layout(samples: list[bytes], *, blank: bytes | None = None) -> Layout:
    """The static lines of a form.

    With ``blank`` (the empty form), its lines are the layout and ``samples`` are not read. Without it a line is
    static when it is on at least all but one of the ``samples`` (all of them when there are fewer than four), at
    the same place within :data:`LINE_TOLERANCE`. Page sizes come from the blank, else the first sample. Samples
    are assumed unshifted against one another.

    Raises:
        FormcastError: when there is neither a blank nor a sample, or a PDF cannot be opened.
    """
    if blank is None and not samples:
        raise FormcastError("learning a layout needs a blank form or at least one sample")
    if blank is not None:
        with DocText(blank) as doc:
            lines = [
                StaticLine(page=p, text=text, rect=rect)
                for p in range(doc.page_count)
                for text, rect in _lines(doc, p)
            ]
            return Layout(lines=lines, page_sizes=doc.page_sizes())

    need = len(samples) if len(samples) < _ALL_OF_THEM_BELOW else len(samples) - 1
    # sample_lines[s][page] -> {text: [rect]}
    sample_lines: list[dict[int, dict[str, list[Rect]]]] = []
    sizes: list[tuple[float, float]] = []
    for n, pdf in enumerate(samples):
        with DocText(pdf) as doc:
            if n == 0:
                sizes = doc.page_sizes()
            sample_lines.append({p: _by_text(_lines(doc, p)) for p in range(doc.page_count)})

    # A line on at least `need` samples is on the first sample or, when one may be missing, on the second.
    candidate_samples = 1 if need == len(samples) else 2
    kept: list[StaticLine] = []
    for s in range(candidate_samples):
        for page, texts in sorted(sample_lines[s].items()):
            for text, rects in texts.items():
                for rect in rects:
                    if any(k.page == page and k.text == text and _same_place(k.rect, rect) for k in kept):
                        continue  # already kept from the first sample
                    present = sum(
                        any(_same_place(rect, other) for other in lines.get(page, {}).get(text, ()))
                        for lines in sample_lines
                    )
                    if present >= need:
                        kept.append(StaticLine(page=page, text=text, rect=rect))
    kept.sort(key=lambda k: (k.page, k.rect[1], k.rect[0]))
    return Layout(lines=kept, page_sizes=sizes)


def _page_match(layout: Layout, doc: DocText, page: int) -> tuple[float, int, int]:
    """For one layout page: its offset on the document and how many of its lines the document has there.

    Returns ``(offset, found, total)``; the offset is 0.0 when no line of the page has its text in the same
    column on the document.
    """
    static = [ln for ln in layout.lines if ln.page == page]
    if not static:
        return 0.0, 0, 0
    have = _by_text(_lines(doc, page))
    # each line's own offset: to the nearest same-text line of the document in the same column
    shifts = []
    for ln in static:
        gaps = [r[1] - ln.rect[1] for r in have.get(ln.text, ()) if abs(r[0] - ln.rect[0]) <= LINE_TOLERANCE]
        if gaps:
            shifts.append(min(gaps, key=abs))
    offset = statistics.median(shifts) if shifts else 0.0
    found = sum(any(_same_place(ln.rect, r, offset) for r in have.get(ln.text, ())) for ln in static)
    return offset, found, len(static)


def layout_score(layout: Layout, doc: DocText) -> float:
    """The share of the layout's static lines the document has, after each page's offset (0 to 1).

    Pages of the document beyond the layout's are ignored; a layout page the document lacks counts as all missing.
    A layout with no lines scores 0.0: nothing is known about the form, so no document is "this layout".
    """
    found = total = 0
    for page in sorted({ln.page for ln in layout.lines}):
        _, f, t = _page_match(layout, doc, page)
        found, total = found + f, total + t
    return found / total if total else 0.0


def layout_offset(layout: Layout, doc: DocText, page: int) -> float:
    """How far down (points, negative is up) the document's ``page`` sits from the layout's, for box reads.

    The median vertical distance over the layout's lines found on that page; 0.0 when none is, or the layout has
    no such page.
    """
    return _page_match(layout, doc, page)[0]
