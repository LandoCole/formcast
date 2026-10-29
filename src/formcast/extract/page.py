"""One open PDF and the lookups the extractor makes on it.

``DocText`` opens a document once and answers, for every field of that document, the questions the readers ask:
what a form field holds, where a label sits, what is written to the right of or below it, what lies inside a box,
whether a drawn check box is marked, and what follows a phrase in running text. Words come from
:func:`formcast.pagemap.extract.extract_page`, so they are in unrotated crop-box points with the origin at the
top-left, as everywhere in formcast; a page with ``/Rotate`` gives the same rectangles as the same page without it.

Labels and phrases are compared by :func:`label_key` (case, spacing and punctuation do not matter), a run of
consecutive words matching when their keys joined equal the label's key.

``words`` leaves out a word that repeats another word of the same text inside a form field's rectangle. A fillable
copy's field value already appears among the page's words through its appearance stream, and some files also paint
the value on the page; reading both would give "84-2219037 84-2219037". The first of the two is kept. Text outside
form fields is never thinned, and a value that appears once (appearance only, or painted only) stays.

Every lookup takes a page number; a page the document does not have gives an empty answer (``[]`` or ``None``), so
a reader aimed at a page a shorter document lacks finds nothing rather than failing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import numpy as np
import pymupdf

from formcast.geometry import Rect, area, intersect
from formcast.pagemap.extract import PDF_LOCK, extract_page, open_pdf
from formcast.pagemap.model import Word

#: Marks that stand for "checked" when typed or drawn as text in a check box.
MARK_GLYPHS = frozenset("Xx✓✔☒✕")
#: A drawn box counts as marked when this share of the pixels in its inner part is ink.
MARK_INK = 0.06
MARK_DPI = 144
MARK_INNER = 0.7  # the middle share of the box (per side) that is looked at, so the box's own border is not ink
INK_LEVEL = 128  # gray below this is ink

#: A gap wider than this many em (the label's height) ends a run of text to the right of a label or below it.
MAX_GAP_EM = 3.0
#: Share of a word's height that must overlap the label's row for the word to be on that row.
ROW_OVERLAP = 0.5
#: A gap wider than this many em (the label's height) can separate an answer from the next label on its row.
LABEL_GAP_EM = 1.0
#: Words after a phrase that are looked at when searching for its stop.
PHRASE_TAIL_WORDS = 200
#: Share of the smaller of two same-text words that must overlap, inside a form field, for one to be dropped.
DUPLICATE_OVERLAP = 0.3

_OFF = (None, "", "Off", False, 0)
_STOPS = {
    "comma": re.compile(r","),
    "sentence": re.compile(r"\.(?=\s|$)"),
}
_DIGITS = re.compile(r"[\d ]*\d[\d ]*")


def label_key(text: str) -> str:
    """The comparison form of a label or phrase: NFKC, casefolded, letters and digits only.

    ``"Taxpayer I.D.:"`` and ``"TAXPAYER ID"`` both give ``"taxpayerid"``.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _union(rects: list[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _centre(r: Rect) -> tuple[float, float]:
    return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2


def _in(r: Rect, point: tuple[float, float]) -> bool:
    return r[0] <= point[0] <= r[2] and r[1] <= point[1] <= r[3]


def _reading_order(words: list[Word]) -> tuple[list[Word], list[int]]:
    """Words top to bottom, left to right within a row, and each word's row number.

    A word joins the current row when its centre is within half the smaller height of the row's first word.
    """
    ordered = sorted(words, key=lambda w: (_centre(w.rect)[1], w.rect[0]))
    rows: list[list[Word]] = []
    ref_y = ref_h = 0.0
    for w in ordered:
        cy, h = _centre(w.rect)[1], w.rect[3] - w.rect[1]
        if rows and abs(cy - ref_y) <= 0.5 * min(h, ref_h):
            rows[-1].append(w)
        else:
            rows.append([w])
            ref_y, ref_h = cy, h
    out: list[Word] = []
    row_of: list[int] = []
    for n, row in enumerate(rows):
        for w in sorted(row, key=lambda w: w.rect[0]):
            out.append(w)
            row_of.append(n)
    return out, row_of


def _drop_repeats(words: list[Word], field_rects: list[Rect]) -> list[Word]:
    """Words without any word that repeats an earlier word's text on top of it inside a form field."""
    kept: list[Word] = []
    inside: list[Word] = []  # kept words that lie in a field, the only ones a later word can repeat
    for w in words:
        if field_rects and any(_in(f, _centre(w.rect)) for f in field_rects):
            if any(k.text == w.text and _overlaps(k.rect, w.rect) for k in inside):
                continue
            inside.append(w)
        kept.append(w)
    return kept


def _overlaps(a: Rect, b: Rect) -> bool:
    inter = intersect(a, b)
    return inter is not None and area(inter) >= DUPLICATE_OVERLAP * min(area(a), area(b))


@dataclass
class _Line:
    text: str
    rect: Rect


@dataclass
class _Text:
    """One page's words in reading order, with what the lookups need beside them."""

    words: list[Word]
    keys: list[str]  # label_key of each word
    rows: list[int]  # reading-order row of each word
    lines: list[_Line]  # the PDF's own text lines, top to bottom, made of the words kept


class DocText:
    """One PDF, opened once. Close it with :meth:`close` or use it as a context manager.

    Raises:
        FormcastError: "encrypted PDF" or "cannot open PDF" when the input cannot be read.
    """

    def __init__(self, pdf: str | Path | bytes) -> None:
        self._doc = open_pdf(pdf)
        self.page_count: int = self._doc.page_count
        self._texts: dict[int, _Text] = {}
        self._fields: dict[str, list[tuple[str, object]]] | None = None  # name -> [(kind, value)] per widget

    def close(self) -> None:
        with PDF_LOCK:
            self._doc.close()

    def __enter__(self) -> DocText:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    # ------------------------------------------------------------ form fields

    def _widgets(self) -> dict[str, list[tuple[str, object]]]:
        """Each field's widgets as ``(kind, value)``: a text is ``str | None``; a check box or radio button is
        ``(selected, on_state)``, ``selected`` being its on-state string when it is on and ``None`` when off."""
        if self._fields is None:
            fields: dict[str, list[tuple[str, object]]] = {}
            with PDF_LOCK:
                for page in self._doc:
                    for w in page.widgets() or []:
                        if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
                            on_state = w.on_state()
                            on = w.field_value == on_state or w.field_value not in _OFF
                            on_name = on_state if isinstance(on_state, str) else None
                            selected = (on_name or str(w.field_value)) if on else None
                            fields.setdefault(w.field_name, []).append(("check", (selected, on_name)))
                        elif w.field_type in (pymupdf.PDF_WIDGET_TYPE_SIGNATURE, pymupdf.PDF_WIDGET_TYPE_BUTTON):
                            fields.setdefault(w.field_name, []).append(("other", None))
                        else:
                            value = w.field_value
                            if isinstance(value, (list, tuple)):
                                value = ", ".join(str(v) for v in value)
                            text = "" if value is None else str(value)
                            fields.setdefault(w.field_name, []).append(("text", text if text.strip() else None))
            self._fields = fields
        return self._fields

    @staticmethod
    def _is_group(entries: list[tuple[str, object]]) -> bool:
        """True for a radio group: several buttons under one name that have different on-states."""
        return len({v[1] for k, v in entries if k == "check" and v[1] is not None}) > 1  # type: ignore[index]

    def widget_value(self, name: str) -> str | bool | None:
        """What the form field ``name`` holds.

        A text (or list, or combo) field gives its value, ``None`` when empty or when there is no such field. A
        check box, or a lone radio button, gives ``True`` when on and ``False`` when off. A radio group (buttons
        that share a name and have different on-states) gives the selected button's on-state string, ``None`` when
        none is selected; ask :meth:`widget_on` whether it is a given button. A name that is not a field of the
        document gives ``None``.
        """
        entries = self._widgets().get(name)
        if not entries:
            return None
        if entries[0][0] == "check":
            selected = [v[0] for k, v in entries if k == "check" and v[0] is not None]  # type: ignore[index]
            if self._is_group(entries):
                return selected[0] if selected else None
            return bool(selected)
        for kind, value in entries:
            if kind == "text" and value is not None:
                return str(value)
        return None

    def widget_on(self, name: str, state: str | None = None) -> bool | None:
        """Whether the check box or radio button ``name`` is on; ``None`` when there is no such check widget.

        For a radio group, ``state`` names one button by its on-state and the answer is whether the group's value
        is that state; without ``state`` it is whether any button is on. A check box ignores ``state``.
        """
        entries = self._widgets().get(name)
        if not entries or entries[0][0] != "check":
            return None
        selected = [v[0] for k, v in entries if k == "check" and v[0] is not None]  # type: ignore[index]
        if state is not None and self._is_group(entries):
            return state in selected
        return bool(selected)

    @property
    def has_values(self) -> bool:
        """True when any form field holds a value (a text, or a check box or radio button that is on)."""
        return any(
            (kind == "check" and value[0] is not None) or (kind == "text" and value is not None)  # type: ignore[index]
            for entries in self._widgets().values()
            for kind, value in entries
        )

    # ------------------------------------------------------------------ words

    def _page(self, page: int) -> _Text | None:
        if not 0 <= page < self.page_count:
            return None
        text = self._texts.get(page)
        if text is None:
            with PDF_LOCK:
                page_map = extract_page(self._doc[page])
            words = _drop_repeats(page_map.words, [w.rect for w in page_map.widgets])
            ordered, rows = _reading_order(words)
            by_line: dict[int, list[Word]] = {}
            for w in ordered:
                by_line.setdefault(w.line_id, []).append(w)
            lines = sorted(
                (_Line(" ".join(w.text for w in ws), _union([w.rect for w in ws])) for ws in by_line.values()),
                key=lambda ln: (ln.rect[1], ln.rect[0]),
            )
            text = _Text(ordered, [label_key(w.text) for w in ordered], rows, lines)
            self._texts[page] = text
        return text

    def words(self, page: int) -> list[Word]:
        """The page's words in reading order (rows top to bottom, left to right), cached.

        A word that repeats another word's text on top of it inside a form field is left out, as the module
        docstring says. A page the document does not have gives ``[]``.
        """
        text = self._page(page)
        return list(text.words) if text else []

    # ----------------------------------------------------------------- labels

    def _runs(self, text: _Text, key: str, same_row: bool) -> list[tuple[int, int]]:
        """Index ranges ``(first, last)`` of the runs of consecutive words whose keys joined equal ``key``."""
        found: list[tuple[int, int]] = []
        if not key:
            return found
        keys = text.keys
        for i, first in enumerate(keys):
            if not first or not key.startswith(first):
                continue
            joined = first
            j = i
            while joined != key and j + 1 < len(keys) and (not same_row or text.rows[j + 1] == text.rows[i]):
                j += 1
                joined += keys[j]
                if not key.startswith(joined):
                    break
            if joined == key:
                found.append((i, j))
        return found

    def find_label(self, labels: list[str], page: int | None) -> list[tuple[int, Rect]]:
        """Every place any of the ``labels`` occurs, as ``(page, rect)``; ``page=None`` searches every page.

        Labels are compared by :func:`label_key`, so all the wordings of a field can be given and a label
        matches whatever its case, spacing or punctuation. A label is a run of consecutive words on one row; a
        punctuation-only word right after it (a separate ":") belongs to it. When one wording is part of another
        ("Date of birth" in "Date of birth or formation") the place is returned once, with the longer. Several hits
        are returned as they are, in reading order; picking among them is the caller's business.
        """
        keys = {k for k in (label_key(label) for label in labels) if k}
        pages = range(self.page_count) if page is None else [page]
        hits: list[tuple[int, Rect]] = []
        for p in pages:
            text = self._page(p)
            if text is None:
                continue
            runs = {run for key in keys for run in self._runs(text, key, same_row=True)}
            # a run inside a longer run is the same place
            runs = {(a, b) for a, b in runs if not any(c <= a and b <= d and (c, d) != (a, b) for c, d in runs)}
            for a, b in sorted(runs):
                while b + 1 < len(text.words) and not text.keys[b + 1] and text.rows[b + 1] == text.rows[a]:
                    b += 1
                hits.append((p, _union([w.rect for w in text.words[a : b + 1]])))
        return hits

    def text_right_of(self, page: int, rect: Rect, max_width: float) -> tuple[str, Rect] | None:
        """The text written to the right of ``rect`` on its row, with its rectangle; ``None`` when there is none.

        Words count when they start after ``rect``'s right edge and share its row (at least half of the word's
        height overlaps ``rect`` vertically). The text runs left to right and stops at a gap wider than three em
        (``rect``'s height) between words and at ``max_width`` points from ``rect``'s right edge. Only the first
        word is free of the gap rule (labels often sit far from their answers).

        When a later word on the row ends with ":" (the next label), the answer is the words before the widest gap
        of more than one em that lies between the answer's first word and that colon word; the label cannot be told
        from the answer by its words, only by the space before it. Without such a gap (a row set with single
        spaces) the answer cannot be told from the next label and the result is ``None``: missing, not wrong.
        "Name: Dana Keller          Date of birth: 05/01/2020" reads "Dana Keller" after "Name:"; the same row with
        single spaces, or an empty answer before the next label, reads ``None``.
        """
        text = self._page(page)
        if text is None:
            return None
        em = rect[3] - rect[1]
        row = [
            w
            for w in text.words
            if w.rect[0] >= rect[2] - 1.0 and _vertical_overlap(w.rect, rect) >= ROW_OVERLAP * (w.rect[3] - w.rect[1])
        ]
        taken: list[Word] = []
        for w in sorted(row, key=lambda w: w.rect[0]):
            if w.rect[2] > rect[2] + max_width:
                break
            if taken and w.rect[0] - taken[-1].rect[2] > MAX_GAP_EM * em:
                break
            taken.append(w)
            if w.text.endswith(":"):
                taken = taken[: _before_label(taken, em)]  # the next label ends here: leave it out
                break
        if not taken:
            return None
        return " ".join(w.text for w in taken), _union([w.rect for w in taken])

    def text_below(self, page: int, rect: Rect, max_lines: int) -> tuple[str, Rect] | None:
        """The next ``max_lines`` lines of text below ``rect``, joined with a space, with their rectangle.

        These are the PDF's own text lines whose left edge lies within the x-span of ``rect``. Reading stops at a
        line ending with ":" (the next label, not included), at a gap of more than three em (``rect``'s height)
        above a line, and after ``max_lines``. ``None`` when no line qualifies.
        """
        text = self._page(page)
        if text is None or max_lines < 1:
            return None
        em = rect[3] - rect[1]
        taken: list[_Line] = []
        bottom = rect[3]
        for line in text.lines:
            if _centre(line.rect)[1] <= rect[3] or not rect[0] - 1.0 <= line.rect[0] <= rect[2]:
                continue
            if line.text.endswith(":") or line.rect[1] - bottom > MAX_GAP_EM * em:
                break
            taken.append(line)
            bottom = line.rect[3]
            if len(taken) == max_lines:
                break
        if not taken:
            return None
        return " ".join(ln.text for ln in taken), _union([ln.rect for ln in taken])

    # ------------------------------------------------------------------ boxes

    def text_in(self, page: int, rect: Rect) -> str | None:
        """The words whose centre lies in ``rect``, in reading order, joined with a space; ``None`` when none."""
        text = self._page(page)
        if text is None:
            return None
        inside = [w.text for w in text.words if _in(rect, _centre(w.rect))]
        return " ".join(inside) if inside else None

    def marked(self, page: int, rect: Rect) -> bool | None:
        """Whether the drawn check box at ``rect`` is marked; ``None`` when ``rect`` is off the page.

        Marked when a glyph among "X x ✓ ✔ ☒ ✕" lies in the rectangle, or at least ``MARK_INK`` of the pixels in
        the box's inner 70% (the page drawn at 144 dpi, gray below 128) are ink.
        """
        text = self._page(page)
        if text is None:
            return None
        with PDF_LOCK:
            box = self._doc[page].cropbox
        if intersect(rect, (0.0, 0.0, box.width, box.height)) is None:
            return None
        if any(w.text.strip() in MARK_GLYPHS and _in(rect, _centre(w.rect)) for w in text.words):
            return True
        dx, dy = (rect[2] - rect[0]) * (1 - MARK_INNER) / 2, (rect[3] - rect[1]) * (1 - MARK_INNER) / 2
        inner = pymupdf.Rect(rect[0] + dx, rect[1] + dy, rect[2] - dx, rect[3] - dy)
        with PDF_LOCK:
            pg = self._doc[page]
            clip = inner * pg.rotation_matrix  # the pixmap shows the page with /Rotate applied
            pix = pg.get_pixmap(dpi=MARK_DPI, clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
        if gray.size == 0:
            return False
        return bool((gray < INK_LEVEL).mean() >= MARK_INK)

    # ---------------------------------------------------------------- phrases

    def after_phrase(self, phrases: list[str], page: int | None, stop: str) -> list[tuple[int, str, Rect]]:
        """The text after every occurrence of any of the ``phrases``, as ``(page, text, rect)``.

        The page's words are read in order as running text, across line breaks. A phrase matches like a label
        (a run of consecutive words whose keys joined equal the phrase's key). The text after it runs to the
        ``stop``: ``"comma"`` (the next ","), ``"sentence"`` (the next "." followed by a space or the end),
        ``"line"`` (the end of the line the phrase ends on) or ``"digits"`` (the run of digits and spaces on the
        phrase's own line: a number wrapped onto the next line is not joined). ``"comma"`` and ``"sentence"`` may
        cross line breaks.
        ``rect`` covers the words the text is made of. An occurrence whose stop is not found (no comma, no digits)
        is left out, so a hit always names a real stop. ``page=None`` searches every page.
        """
        if stop not in ("comma", "sentence", "line", "digits"):
            raise ValueError(f"stop must be 'comma', 'sentence', 'line' or 'digits', not {stop!r}")
        keys = {k for k in (label_key(p) for p in phrases) if k}
        pages = range(self.page_count) if page is None else [page]
        out: list[tuple[int, str, Rect]] = []
        for p in pages:
            text = self._page(p)
            if text is None:
                continue
            ends = sorted({last for key in keys for _, last in self._runs(text, key, same_row=False)})
            for last in ends:
                found = self._after(text, last, stop)
                if found:
                    out.append((p, *found))
        return out

    @staticmethod
    def _after(text: _Text, last: int, stop: str) -> tuple[str, Rect] | None:
        """The words after word ``last`` up to ``stop``: their text and rectangle, or ``None``."""
        line_id = text.words[last].line_id
        own_line = stop in ("line", "digits")  # these never leave the phrase's own text line
        tail = [w for w in text.words[last + 1 :] if not own_line or w.line_id == line_id][:PHRASE_TAIL_WORDS]
        spans: list[tuple[int, int]] = []  # each word's [start, end) in the joined tail
        pos = 0
        for w in tail:
            spans.append((pos, pos + len(w.text)))
            pos += len(w.text) + 1
        joined = " ".join(w.text for w in tail)
        start = len(joined) - len(joined.lstrip(" :"))
        if stop == "line":
            end = len(joined)
        elif stop == "digits":
            m = _DIGITS.match(joined, start)
            if m is None:
                return None
            end = m.end()
        else:
            m = _STOPS[stop].search(joined, start)
            if m is None:
                return None
            end = m.start()
        chosen = [w for w, (a, b) in zip(tail, spans) if a < end and b > start]
        value = joined[start:end].strip()
        if not chosen or not value:
            return None
        return value, _union([w.rect for w in chosen])


def _before_label(words: list[Word], em: float) -> int:
    """How many of ``words`` are the answer, when the last one ends with ":" (the next label).

    The answer ends at the widest gap of more than ``LABEL_GAP_EM`` em between two of the words; 0 when there is none.
    """
    best, count = LABEL_GAP_EM * em, 0
    for i in range(1, len(words)):
        gap = words[i].rect[0] - words[i - 1].rect[2]
        if gap > best:
            best, count = gap, i
    return count


def _vertical_overlap(a: Rect, b: Rect) -> float:
    return min(a[3], b[3]) - max(a[1], b[1])
