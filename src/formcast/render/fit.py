"""Text fitting: choose a font size (and wrapping) for a value in a box.

Pure functions; the fill step feeds a :class:`Fit` into PyMuPDF widgets and the report.
Over-long text is never cut here, except where a hard character limit forces it
(``max_len`` / ``comb``); it is kept whole at ``min_font_size`` and reported as ``overflow``.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Literal

import pymupdf

from formcast.geometry import Rect

AUTO_MAX_SIZE = 12.0
SHRINK_STEP = 0.5
PAD = 2.0  # horizontal padding per side
LINE_HEIGHT = 1.15  # multiple of font size
_VPAD = 2.0  # total vertical padding


@dataclass(frozen=True)
class Fit:
    text: str
    font_size: float
    lines: int
    status: Literal["ok", "shrunk", "overflow"]
    truncated: bool
    unencodable: str


def unencodable_chars(text: str) -> str:
    """Distinct characters of ``text`` that ``cp1252`` cannot encode, in order of first appearance."""
    out: list[str] = []
    for ch in text:
        if ch in out:
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            out.append(ch)
    return "".join(out)


#: Fonts PyMuPDF measures from a per-code table (correctly); every other standard font goes by glyph.
_TABLE_FONTS = frozenset({"zadb", "symb"})


@functools.cache
def _font(fontname: str) -> pymupdf.Font:
    return pymupdf.Font(fontname)


@functools.cache
def _char_width(ch: str, fontname: str) -> float:
    """Width at size 1 of the glyph a standard-font appearance draws for ``ch``.

    The appearance writes the value as cp1252 bytes, so ``ch`` is drawn as its own glyph. As in
    PyMuPDF's ``get_text_length``, a character cp1252 cannot encode counts as U+00B7 and one the
    font has no glyph for (a control) as a space.
    """
    font = _font(fontname)
    cp = 0xB7 if unencodable_chars(ch) else ord(ch)
    if not font.has_glyph(cp):
        cp = 0x20
    return font.glyph_advance(cp)


def _width(text: str, fontname: str, size: float) -> float:
    """Width of ``text`` in a standard font.

    Not ``pymupdf.get_text_length``: for these fonts it skips the characters after a non-ASCII one
    (it steps through the str by UTF-8 length) and measures the cp1252 extras (``€``, ``“`` ...) as
    missing glyphs.
    """
    name = fontname.lower()
    if name in _TABLE_FONTS:
        return pymupdf.get_text_length(text, fontname=name, fontsize=size)
    return sum(_char_width(ch, name) for ch in text) * size


def _break_word(word: str, fontname: str, size: float, width: float) -> list[str]:
    """Split an over-long word into chunks that each fit ``width`` (at least one char per chunk)."""
    chunks: list[str] = []
    cur = ""
    for ch in word:
        if cur and _width(cur + ch, fontname, size) > width:
            chunks.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        chunks.append(cur)
    return chunks


def _wrap(text: str, fontname: str, size: float, width: float) -> list[str]:
    """Greedy word wrap; ``\\n`` starts a new paragraph; over-long words break by characters."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            cand = word if not cur else f"{cur} {word}"
            if _width(cand, fontname, size) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
                cur = ""
            if _width(word, fontname, size) <= width:
                cur = word
            else:
                chunks = _break_word(word, fontname, size, width)
                lines.extend(chunks[:-1])
                cur = chunks[-1]
        lines.append(cur)
    return lines


def _sizes(start: float, min_font_size: float):
    """Yield start, then successively smaller sizes in SHRINK_STEP steps, ending exactly at the minimum."""
    size = start
    yield size
    while size > min_font_size:
        size = max(size - SHRINK_STEP, min_font_size)
        yield size


def fit_text(
    text: str,
    rect: Rect,
    *,
    font_size: float | Literal["auto"],
    min_font_size: float,
    fontname: str = "helv",
    multiline: bool = False,
    max_lines: int | None = None,
    max_len: int | None = None,
    comb: int | None = None,
) -> Fit:
    """Decide the font size (and line count) for ``text`` in ``rect``.

    The text is cut only to ``max_len`` / ``comb`` characters (``truncated=True``); a value
    that does not fit at ``min_font_size`` is kept whole and reported ``overflow``.
    """
    limit = max_len
    if comb:
        limit = comb if limit is None else min(limit, comb)
    truncated = False
    if limit is not None and len(text) > limit:
        text = text[:limit]
        truncated = True

    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if font_size == "auto":
        start = min(AUTO_MAX_SIZE, (height - _VPAD) / LINE_HEIGHT)
    else:
        start = float(font_size)
    start = max(start, min_font_size)
    bad = unencodable_chars(text)

    def done(size: float, lines: int, fits: bool) -> Fit:
        status: Literal["ok", "shrunk", "overflow"] = "overflow" if not fits else ("shrunk" if size < start else "ok")
        return Fit(text, size, lines, status, truncated, bad)

    if comb:
        cell = width / comb
        size = start
        fits = True
        for size in _sizes(start, min_font_size):
            widest = max((_width(ch, fontname, size) for ch in text), default=0.0)
            fits = widest <= cell and size * LINE_HEIGHT <= height - _VPAD
            if fits:
                break
        return done(size, 1, fits)

    if multiline or max_lines is not None:
        avail = width - 2 * PAD
        size = start
        lines = 1
        fits = True
        for size in _sizes(start, min_font_size):
            lines = len(_wrap(text, fontname, size, avail))
            fits = lines * size * LINE_HEIGHT <= height - _VPAD and (max_lines is None or lines <= max_lines)
            if fits:
                break
        return done(size, lines, fits)

    avail = width - 2 * PAD
    size = start
    fits = True
    for size in _sizes(start, min_font_size):
        fits = _width(text, fontname, size) <= avail
        if fits:
            break
    return done(size, 1, fits)
