"""Fingerprint: a stable id for "this exact blank form".

It is not a file hash: a re-saved PDF differs byte for byte but keeps its page
count, page sizes, text and look, which is what the fingerprint hashes:

* **Text:** the PDF's own text layer (never OCR, which is slow and can vary).
* **Layout:** a coarse ink map per page, so image-only (scanned) pages, which
  have no text layer, still get distinct fingerprints. The page is rendered in
  grayscale at low resolution without annotations (so widget appearances and
  filled values do not count), averaged into a 32 x 44 grid of cells, and each
  cell becomes one bit: is its mean ink more than ``INK_DELTA`` (on 0-255) above
  the page's median cell? Measuring against the median ignores an even scan
  background; the coarse grid and threshold absorb rendering noise. A re-save
  renders the same pixels, so the bits are identical; a different MuPDF version
  could anti-alias differently and, rarely, flip a cell at the threshold.

Similarity: :func:`compute_signature` keeps, per page, the ink map and a MinHash sketch of the
page's word 3-shingles, and :func:`similarity` compares a template with a PDF using them. The
library only scores; what to do with the score (reuse, adapt, build new) is the app's call.
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import pymupdf
from pydantic import BaseModel

from formcast.errors import FormcastError
from formcast.pagemap.extract import PDF_LOCK, open_pdf, page_size
from formcast.pagemap.model import FormSignature

if TYPE_CHECKING:
    from formcast.template.model import Template

# Characters of normalized text hashed per page.
TEXT_CHARS = 2000
# Ink map: cells across and down the rendered page, render scale bounds, and threshold.
INK_GRID = (32, 44)
INK_SCALE = 0.5  # 36 dpi
INK_MAX_PIXELS = 1200  # longest rendered side, for very large pages
INK_DELTA = 4.0


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def ink_bits(page: pymupdf.Page) -> str:
    """The page's coarse ink map (see the module docstring) as hex, one bit per cell."""
    cols, rows = INK_GRID
    w, h = page.rect.width, page.rect.height  # as rendered (rotated, cropped)
    scale = max(min(INK_SCALE, INK_MAX_PIXELS / max(w, h)), 2 * cols / w, 2 * rows / h)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False,
                          annots=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    ink = 255.0 - gray.astype(np.float64)
    ys = np.linspace(0, pix.height, rows + 1).astype(int)
    xs = np.linspace(0, pix.width, cols + 1).astype(int)
    sums = np.add.reduceat(np.add.reduceat(ink, ys[:-1], axis=0), xs[:-1], axis=1)
    means = sums / np.outer(np.diff(ys), np.diff(xs))
    return np.packbits(means > np.median(means) + INK_DELTA).tobytes().hex()


# Widgets whose appearance shows a filled-in value (the ones ``fill`` writes).
_VALUE_WIDGETS = frozenset({
    pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX,
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
})  # fmt: skip
_ANNOT_HIDDEN = 2  # annotation flag bit 2


def _without_field_values(doc: pymupdf.Document) -> pymupdf.Document:
    """A copy of ``doc`` with every value-bearing widget hidden (the caller closes it)."""
    copy = pymupdf.open("pdf", doc.tobytes())
    for page in copy:
        for w in page.widgets() or []:
            if w.field_type in _VALUE_WIDGETS:
                kind, flags = copy.xref_get_key(w.xref, "F")
                copy.xref_set_key(w.xref, "F", str((int(flags) if kind == "int" else 0) | _ANNOT_HIDDEN))
    return copy


def fingerprint_document(doc: pymupdf.Document, *, ignore_field_values: bool = False) -> str:
    """Fingerprint of an open document (see :func:`fingerprint`).

    The text layer includes what widget appearances draw, so filling a form changes its
    fingerprint. With ``ignore_field_values`` the text, choice and button widgets are hidden
    first, so a filled copy fingerprints like its blank (signature and push-button captions
    still count, as they do in the blank's own fingerprint).
    """
    if ignore_field_values:
        copy = _without_field_values(doc)
        try:
            return fingerprint_document(copy)
        finally:
            copy.close()
    digest = hashlib.sha256()
    digest.update(f"pages:{doc.page_count}\n".encode())
    for page in doc:
        width, height = page_size(page)
        text = _normalize(page.get_text("text", sort=True))[:TEXT_CHARS]
        digest.update(f"{round(width)}x{round(height)}\n{text}\nink:{ink_bits(page)}\n".encode())
    return digest.hexdigest()[:16]


def fingerprint(pdf: str | Path | bytes) -> str:
    """sha256 of page count and, per page, the rounded page size, the first 2000
    normalized text characters and the coarse ink map, as 16 hex digits.

    Raises:
        FormcastError: "encrypted PDF" or "cannot open PDF".
    """
    with PDF_LOCK:
        doc = open_pdf(pdf)
        try:
            return fingerprint_document(doc)
        finally:
            doc.close()


# ---------------------------------------------------------------------------------------------
# Signature (MinHash of word shingles + ink map)

# MinHash: 64 hash functions ``(a * h + b) mod P`` over the 32-bit hash ``h`` of a shingle. P is
# the largest prime below 2**32, and a and b come from a seeded generator, so a signature is the
# same on every run and machine. An empty page gives ``MINHASH_EMPTY`` in every slot.
MINHASH_SIZE = 64
SHINGLE_WORDS = 3
_P = 4294967291
MINHASH_EMPTY = 2**32 - 1
_rng = random.Random(1729)
_A = np.array([_rng.randrange(1, 2**32 - 1) for _ in range(MINHASH_SIZE)], dtype=np.uint64)
_B = np.array([_rng.randrange(0, _P) for _ in range(MINHASH_SIZE)], dtype=np.uint64)


def _shingles(text: str) -> set[bytes]:
    """Word 3-shingles of normalized text (a text of one or two words is one shingle)."""
    words = text.split()
    if not words:
        return set()
    n = SHINGLE_WORDS
    if len(words) < n:
        return {" ".join(words).encode()}
    return {" ".join(words[i : i + n]).encode() for i in range(len(words) - n + 1)}


def _minhash(text: str) -> list[int]:
    shingles = _shingles(text)
    if not shingles:
        return [MINHASH_EMPTY] * MINHASH_SIZE
    h = np.fromiter(
        (int.from_bytes(hashlib.blake2b(sh, digest_size=4).digest(), "big") for sh in shingles),
        dtype=np.uint64,
        count=len(shingles),
    )
    # a * h < 2**64 (a < 2**32 - 1, h < 2**32), plus b < 2**32 still fits in uint64.
    mixed = (np.outer(h, _A) + _B) % np.uint64(_P)
    return [int(v) for v in mixed.min(axis=0)]


def compute_signature(doc: pymupdf.Document) -> FormSignature:
    """The form's signature (see :class:`FormSignature`) from the PDF's own text layer.

    Text is normalized as in :func:`fingerprint_document` (never OCR), and field values are
    ignored as with ``ignore_field_values=True``, so a filled copy signs like its blank.
    """
    copy = _without_field_values(doc)
    try:
        ink, text = [], []
        for page in copy:
            ink.append(ink_bits(page))
            text.append(_minhash(_normalize(page.get_text("text", sort=True))))
    finally:
        copy.close()
    return FormSignature(ink=ink, text=text)


# ---------------------------------------------------------------------------------------------
# Similarity

# Weights of the parts when both sides have a text layer. When one side has none (a scan) the
# text part is dropped and the other two keep their proportions (0.4 pages, 0.6 layout).
# Calibrated on the dev forms: tests/match/test_similarity.py records the measured scores.
W_PAGES, W_TEXT, W_INK = 0.2, 0.5, 0.3
# The layout part is 1 - Hamming distance of the ink maps, rescaled so two unrelated pages (which
# agree on about half of their cells by chance) score 0 and identical pages 1.
INK_CHANCE = 0.5
# Pages are the same size when within this many points.
SIZE_TOLERANCE = 1.0
# A PDF that is not the template's exact blank form (its fingerprint differs even with field
# values ignored) never scores as high as "reuse": `fill` would refuse it. Apps put "reuse" at
# 0.95, so a close revision tops out just below it.
REVISION_CAP = 0.94
# Labels basis (templates with no signature): pages and the share of the template's field-label
# phrases found in the PDF's text.
LABEL_W_PAGES, LABEL_W_TEXT = 0.3, 0.7
# With no field labels to compare there is little to go on beyond the pages.
NO_LABELS_FACTOR = 0.5

_WORDS = re.compile(r"[a-z0-9]+")


class Similarity(BaseModel):
    """How alike a template's form and a PDF are, from 0 to 1, with the reason in words.

    ``parts`` holds ``pages``, ``text`` and ``ink`` (each 0 to 1; ``text`` is absent when one side
    has no text layer, and on the labels basis it is the share of field labels found) and
    ``fingerprint`` (1.0) when the two are the same blank form. ``basis`` says what was compared:
    the fingerprint, the stored signature, or (for templates with no signature) the field labels.
    """

    score: float
    parts: dict[str, float]
    explanation: str
    basis: Literal["fingerprint", "signature", "labels"]


def _sizes(doc: pymupdf.Document) -> list[tuple[float, float]]:
    return [page_size(p) for p in doc]


def _pages_part(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """1.0 when page counts and sizes agree, else the fraction of matching pages."""
    same = sum(abs(x[0] - y[0]) <= SIZE_TOLERANCE and abs(x[1] - y[1]) <= SIZE_TOLERANCE for x, y in zip(a, b))
    return same / max(len(a), len(b), 1)


def _text_part(a: FormSignature, b: FormSignature) -> float | None:
    """MinHash Jaccard estimate averaged over aligned pages; None when a side has no text at all."""
    empty = [MINHASH_EMPTY] * MINHASH_SIZE
    if all(t == empty for t in a.text) or all(t == empty for t in b.text):
        return None
    total = 0.0
    for ta, tb in zip(a.text, b.text):
        if ta != empty or tb != empty:
            total += sum(x == y for x, y in zip(ta, tb)) / MINHASH_SIZE
        else:
            total += 1.0  # both pages blank: they agree
    return total / max(len(a.text), len(b.text))


def _ink_part(a: FormSignature, b: FormSignature) -> float:
    """The ink maps' agreement over aligned pages (see ``INK_CHANCE``); missing pages score 0."""
    total = 0.0
    for ia, ib in zip(a.ink, b.ink):
        bits = 4 * max(len(ia), len(ib))
        same = 1.0 - (int(ia, 16) ^ int(ib, 16)).bit_count() / bits
        total += max(0.0, (same - INK_CHANCE) / (1.0 - INK_CHANCE))
    return total / max(len(a.ink), len(b.ink))


def signature_similarity(a: FormSignature, b: FormSignature) -> float:
    """How alike two forms' text and ink are, 0 to 1: the mean of the text part and the ink part.

    A side with no text layer leaves the text part out, so the ink part stands alone. Page counts and sizes are
    not compared (``similarity`` does that and weighs the parts differently).
    """
    text, ink = _text_part(a, b), _ink_part(a, b)
    return ink if text is None else (text + ink) / 2


def _label_phrases(template: Template) -> set[tuple[str, ...]]:
    """The template's field labels as word pairs (a one-word label is its single word)."""
    phrases: set[tuple[str, ...]] = set()
    for f in template.fields:
        words = _WORDS.findall(f.label.lower())
        if len(words) == 1:
            phrases.add((words[0],))
        phrases.update(zip(words, words[1:]))
    return phrases


def _pdf_phrases(doc: pymupdf.Document) -> frozenset[tuple[str, ...]]:
    """The PDF's text as single words and word pairs (what a label's phrases are looked up in)."""
    words = _WORDS.findall(_normalize(" ".join(page.get_text("text") for page in doc)).lower())
    return frozenset(zip(words, words[1:])) | frozenset((w,) for w in words)


def _labels_part(template: Template, have: frozenset[tuple[str, ...]]) -> float | None:
    """Share of the template's label phrases found in the PDF's text; None without labels."""
    phrases = _label_phrases(template)
    if not phrases:
        return None
    return len(phrases & have) / len(phrases)


def _pages_words(a: list, b: list, part: float) -> str:
    if part == 1.0:
        return "same page count and sizes"
    if len(a) != len(b):
        return f"page count differs ({len(a)} in the template, {len(b)} in the PDF)"
    return f"{round(part * len(a))} of {len(a)} pages have the same size"


def _pct(x: float) -> int:
    return round(100 * x)


class PdfFeatures:
    """What :func:`similarity` reads from a PDF; made by :func:`pdf_features`, computed on first use.

    Each part (``fingerprint``, ``fingerprint_blind``, ``page_sizes``, ``signature``, ``phrases``) is
    computed the first time something asks for it and then kept, so a fingerprint hit never renders
    for the signature, and comparing one PDF with many templates reads the PDF once. It holds the
    PDF's bytes and opens them per part, holding :data:`~formcast.pagemap.extract.PDF_LOCK` while it opens,
    computes and closes each one, so it may be used beside other threads' MuPDF work (a first access from two
    threads at once only repeats work). Treat the attributes as opaque.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._cache: dict[str, object] = {}

    def _part(self, name: str, compute):
        if name not in self._cache:
            with PDF_LOCK:
                doc = open_pdf(self._data)
                try:
                    self._cache[name] = compute(doc)
                finally:
                    doc.close()
        return self._cache[name]

    @property
    def fingerprint(self) -> str:
        return self._part("fingerprint", fingerprint_document)  # type: ignore[return-value]

    @property
    def fingerprint_blind(self) -> str:
        """The fingerprint with field values ignored (a filled copy matches its blank)."""
        return self._part("blind", lambda d: fingerprint_document(d, ignore_field_values=True))  # type: ignore[return-value]

    @property
    def page_sizes(self) -> list[tuple[float, float]]:
        return self._part("sizes", _sizes)  # type: ignore[return-value]

    @property
    def signature(self) -> FormSignature:
        return self._part("signature", compute_signature)  # type: ignore[return-value]

    @property
    def phrases(self) -> frozenset[tuple[str, ...]]:
        return self._part("phrases", _pdf_phrases)  # type: ignore[return-value]

    def matches_fingerprint(self, expected: str | None) -> bool:
        """Whether ``expected`` is this PDF's fingerprint, or its fingerprint with field values ignored.

        Computes only the fingerprint(s), and the second one only when the first does not match.
        """
        return expected is not None and (expected == self.fingerprint or expected == self.fingerprint_blind)


def pdf_features(pdf: str | Path | bytes) -> PdfFeatures:
    """The parts of ``pdf`` that :func:`similarity` compares, for ``similarity(..., features=...)``.

    The PDF is opened once here to check it can be used; the parts are computed when first needed.

    Raises:
        FormcastError: "encrypted PDF" or "cannot open PDF".
    """
    if isinstance(pdf, (bytes, bytearray, memoryview)):
        data = bytes(pdf)
    else:
        try:
            data = Path(pdf).read_bytes()
        except OSError as exc:
            raise FormcastError("cannot open PDF") from exc
    with PDF_LOCK:
        open_pdf(data).close()
    return PdfFeatures(data)


def similarity(template: Template, pdf: str | Path | bytes, *, features: PdfFeatures | None = None) -> Similarity:
    """How alike ``template``'s form and ``pdf`` are (see :class:`Similarity`).

    * The same fingerprint (field values ignored) scores 1.0, ``basis`` "fingerprint".
    * Otherwise, with a stored signature, a weighted mean of page count and size agreement,
      text (MinHash Jaccard of word 3-shingles) and layout (ink maps), ``basis`` "signature".
      Without a text layer on one side the text part is left out. Anything short of the same
      blank form scores at most ``REVISION_CAP``.
    * A template with no signature (older, or made in the browser) compares its field labels
      with the PDF's text plus page sizes, ``basis`` "labels", and the explanation says so.

    ``features`` (from :func:`pdf_features` on the same PDF) skips reading ``pdf`` again; the
    result is the same as without it. Use it to score one PDF against many templates.

    Raises:
        FormcastError: "encrypted PDF" or "cannot open PDF".
    """
    f = features if features is not None else pdf_features(pdf)
    if f.matches_fingerprint(template.form.fingerprint):
        return Similarity(
            score=1.0,
            parts={"fingerprint": 1.0, "pages": 1.0, "text": 1.0, "ink": 1.0},
            explanation="same blank form",
            basis="fingerprint",
        )
    mine = [(float(w), float(h)) for w, h in template.form.page_sizes]
    sizes = f.page_sizes
    pages = _pages_part(mine, sizes)
    pages_phrase = _pages_words(mine, sizes, pages)
    if template.form.signature is None:
        return _by_labels(template, f.phrases, pages, pages_phrase)
    sig, theirs = f.signature, template.form.signature
    text, ink = _text_part(theirs, sig), _ink_part(theirs, sig)
    parts = {"pages": pages, "ink": ink}
    if text is None:
        raw = (W_PAGES * pages + W_INK * ink) / (W_PAGES + W_INK)
        text_phrase = "no text layer on one side, so the text was not compared"
    else:
        parts["text"] = text
        raw = W_PAGES * pages + W_TEXT * text + W_INK * ink
        text_phrase = f"{_pct(text)}% of the text matches"
    score = min(raw, REVISION_CAP)
    explanation = f"{pages_phrase}; {text_phrase}; layout {_pct(ink)}% the same"
    if score < raw:
        explanation += " (close, but not the same blank form)"
    return Similarity(score=round(score, 4), parts=parts, explanation=explanation, basis="signature")


def _by_labels(template: Template, have: frozenset[tuple[str, ...]], pages: float, pages_phrase: str) -> Similarity:
    prefix = "template has no stored signature, so this compares its field labels and page sizes: "
    found = _labels_part(template, have)
    if found is None:
        return Similarity(
            score=round(NO_LABELS_FACTOR * pages, 4),
            parts={"pages": pages},
            explanation=prefix + f"{pages_phrase}; the template has no field labels to look for",
            basis="labels",
        )
    raw = LABEL_W_PAGES * pages + LABEL_W_TEXT * found
    return Similarity(
        score=round(min(raw, REVISION_CAP), 4),
        parts={"pages": pages, "text": found},
        explanation=prefix + f"{pages_phrase}; {_pct(found)}% of its field labels appear in the PDF's text",
        basis="labels",
    )
