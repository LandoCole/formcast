"""Assemble the form map: text, rules and widgets per page, OCR where the text
layer is unusable, detector candidates, and label links.

Page kind: ``fillable`` when the page has widgets, ``scanned`` when images
cover most of it, otherwise ``digital``. The kind picks the detectors (see
:class:`DetectPolicy`); fillable pages are not run through detection.

OCR rule (``ocr="auto"``): a page that needs OCR (``needs_ocr``: an image-
dominated page, or a text layer covering little of the visible ink) gets its
words and lines *replaced* by OCR output and ``text_source="ocr"``. Any
embedded text on such a page is dropped, because a bad embedded OCR layer or
a few stray words are worse than one fresh, consistent OCR pass. The
``"pdf+ocr"`` text source is never produced. When Tesseract is missing the PDF
text is kept and a note is added to the page; ``ocr="always"`` OCRs every page
and raises instead. ``ocr="never"`` keeps the PDF text and notes pages that
needed OCR.

Prose pages (``skip_prose=True``): a long document such as a subscription agreement is mostly running
text with the forms in between. Detection costs seconds per page and finds nothing there but the odd
stray box, so a digital page that :func:`looks_like_prose` skips detection (and says so in its notes).
A page is prose when most of its lines run across the page and it has almost no labels ending in a
colon and few rules. On the eval corpus no page with fields reads as prose. Off by default.

Detection off (``detect=False``): no page is run through detection, for a person who picks the form pages
themselves. Each page that would have been says so in its notes; :func:`detect_on_page` runs detection on
one page later (it also takes a page skipped as prose).

Progress (``on_progress``): called as each page starts a step (``text``, ``ocr``, ``detect``) and when the
page is done, with a :class:`PageProgress`. Raising from it stops the build.

After text is settled, lines are split where two words are more than
``LINE_SPLIT_GAP`` em apart, so "Name ____ DOB ____ Phone #" drawn as one text
line becomes three label lines.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

from formcast.match import compute_signature, fingerprint_document
from formcast.pagemap.detect import DetectPolicy, Detector, detect_page, load_default_detectors
from formcast.pagemap.extract import extract_page, is_image_dominated, needs_ocr, open_pdf
from formcast.pagemap.model import FormMap, PageMap, TextLine, Word
from formcast.pagemap.ocr import ocr_available, ocr_page
from formcast.pagemap.query import find_label

__all__ = [
    "DetectPolicy",
    "PageProgress",
    "build_form_map",
    "detect_on_page",
    "link_labels",
    "looks_like_prose",
    "needs_detection",
    "split_wide_lines",
]

OcrMode = Literal["auto", "never", "always"]

# Words further apart than this many ems (of the line's font size) start a new line.
LINE_SPLIT_GAP = 2.0

NOTE_OCR_MISSING = (
    "needs OCR (little extractable text) but Tesseract is not installed; "
    "text below is the PDF text layer and may be incomplete"
)
NOTE_OCR_OFF = (
    "needs OCR (little extractable text) but OCR is off; "
    "text below is the PDF text layer and may be incomplete"
)
NOTE_NOT_DETECTED = "detection not run: the pages to search for blanks are picked one at a time"


@dataclass(frozen=True)
class PageProgress:
    """What ``build_form_map`` tells ``on_progress``: page ``index`` (of ``total``) starts ``step``, or is ``done``."""

    index: int
    total: int
    step: Literal["text", "ocr", "detect", "done"]
    detectors: tuple[str, ...] = ()  # step "detect": the detectors that run
    page: PageMap | None = None  # step "done": the finished page


def build_form_map(
    pdf: str | Path | bytes,
    *,
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy = DetectPolicy(),
    ocr: OcrMode = "auto",
    skip_prose: bool = False,
    detect: bool = True,
    on_progress: Callable[[PageProgress], None] | None = None,
) -> FormMap:
    """Page map of every page of ``pdf`` (a path or the PDF's bytes).

    ``detectors`` maps names (as used by ``policy``) to detectors; ``None``
    loads the stock CommonForms models when a page needs detection.
    ``skip_prose`` runs no detection on digital pages that read as running text,
    ``detect=False`` none on any page, and ``on_progress`` hears of each page's steps
    (see the module docstring).

    Raises:
        FormcastError: "encrypted PDF", "cannot open PDF", an OCR error when
            ``ocr="always"`` and Tesseract is missing, or a detector named by
            ``policy`` that is not in ``detectors``.
        ValueError: for an unknown ``ocr`` mode.
    """
    if ocr not in ("auto", "never", "always"):
        raise ValueError(f"ocr must be 'auto', 'never' or 'always', not {ocr!r}")
    doc = open_pdf(pdf)
    try:
        total = doc.page_count

        def report(index: int, step: str, **more) -> None:
            if on_progress is not None:
                on_progress(PageProgress(index, total, step, **more))  # type: ignore[arg-type]

        pages = [_build_page(page, detectors, policy, ocr, skip_prose, detect, report) for page in doc]
        fp = fingerprint_document(doc)
        signature = compute_signature(doc)
    finally:
        doc.close()
    source_name = "" if isinstance(pdf, (bytes, bytearray, memoryview)) else Path(pdf).name
    return FormMap(pages=pages, fingerprint=fp, signature=signature, source_name=source_name)


def _build_page(
    page: pymupdf.Page,
    detectors: dict[str, Detector] | None,
    policy: DetectPolicy,
    ocr: OcrMode,
    skip_prose: bool = False,
    detect: bool = True,
    report: Callable[..., None] = lambda index, step, **more: None,
) -> PageMap:
    report(page.number, "text")
    pm = extract_page(page)
    if pm.widgets:
        kind = "fillable"
        wants_ocr = needs_ocr(page)
    else:
        kind = "scanned" if is_image_dominated(page) else "digital"
        # extract_page marks a widget-free page "scanned" exactly when it needs OCR.
        wants_ocr = pm.kind == "scanned"

    words, lines, text_source, notes = pm.words, pm.lines, "pdf", []
    if ocr == "always" or (ocr == "auto" and wants_ocr and ocr_available()):
        report(page.number, "ocr")
        words, lines = ocr_page(page)
        text_source = "ocr"
    elif wants_ocr:
        notes.append(NOTE_OCR_OFF if ocr == "never" else NOTE_OCR_MISSING)
    words, lines = split_wide_lines(words, lines)

    candidates = []
    if kind == "fillable":
        pass
    elif not detect:
        notes.append(NOTE_NOT_DETECTED)
    elif kind == "digital" and skip_prose and looks_like_prose(pm):
        notes.append(NOTE_PROSE)
    else:
        if detectors is None:
            detectors = load_default_detectors()
        report(page.number, "detect", detectors=policy.scanned if kind == "scanned" else policy.digital)
        candidates = detect_page(page, kind, detectors, policy)

    pm = pm.model_copy(
        update={
            "kind": kind,
            "text_source": text_source,
            "words": words,
            "lines": lines,
            "candidates": candidates,
            "notes": notes,
        }
    )
    link_labels(pm)
    report(page.number, "done", page=pm)
    return pm


def needs_detection(page: PageMap) -> bool:
    """Whether ``page`` was not searched for blanks (detection off, or skipped as prose); fillable pages never are."""
    return page.kind != "fillable" and any(n in (NOTE_PROSE, NOTE_NOT_DETECTED) for n in page.notes)


def detect_on_page(
    page: pymupdf.Page,
    page_map: PageMap,
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy = DetectPolicy(),
) -> PageMap:
    """``page_map`` (of ``page``) with detection run: its candidates found afresh and their labels linked, and
    the note that detection was skipped dropped. A fillable page comes back unchanged. ``detectors`` as for
    :func:`build_form_map`.
    """
    if page_map.kind == "fillable":
        return page_map
    if detectors is None:
        detectors = load_default_detectors()
    candidates = detect_page(page, page_map.kind, detectors, policy)
    notes = [n for n in page_map.notes if n not in (NOTE_PROSE, NOTE_NOT_DETECTED)]
    pm = page_map.model_copy(update={"candidates": candidates, "notes": notes})
    link_labels(pm)
    return pm


NOTE_PROSE = "detection skipped: the page reads as running text (no blanks were looked for)"
PROSE_WIDE_SHARE = 0.6  # of a page's lines that run across most of it
PROSE_MAX_LABELS = 1  # short lines ending in a colon
PROSE_MAX_RULES = 8  # horizontal rules and underscore runs at least 25 pt long


def looks_like_prose(page: PageMap) -> bool:
    """Whether a page is running text, not a form: most lines span 60% of the page width with 60+ characters,
    at most one short "Label:" line, and at most eight rules of 25 pt or more (footers and the odd underline).
    Calibrated to keep every page with fields on the eval corpus, so it errs toward detecting.
    """
    lines = [ln for ln in page.lines if ln.text.strip()]
    if not lines:
        return False
    wide = sum(1 for ln in lines if ln.rect[2] - ln.rect[0] >= 0.6 * page.width and len(ln.text) >= 60)
    labels = sum(1 for ln in lines if len(ln.text.strip()) <= 45 and ln.text.strip().endswith(":"))
    rules = sum(1 for r in page.rules if r.orientation == "h" and r.rect[2] - r.rect[0] >= 25)
    return wide >= PROSE_WIDE_SHARE * len(lines) and labels <= PROSE_MAX_LABELS and rules <= PROSE_MAX_RULES


def split_wide_lines(
    words: list[Word], lines: list[TextLine], gap_em: float = LINE_SPLIT_GAP
) -> tuple[list[Word], list[TextLine]]:
    """Split lines wherever consecutive words are more than ``gap_em`` ems apart.

    Lines are renumbered in order and words' ``line_id`` updated; new lines
    keep their source line's font size and boldness. Lines with no wide gap
    come back unchanged.
    """
    new_words = list(words)
    new_lines: list[TextLine] = []
    for line in lines:
        em = line.font_size or (line.rect[3] - line.rect[1]) or 1.0
        groups: list[list[int]] = [[line.word_ids[0]]] if line.word_ids else []
        for prev, cur in zip(line.word_ids, line.word_ids[1:]):
            if words[cur].rect[0] - words[prev].rect[2] > gap_em * em:
                groups.append([cur])
            else:
                groups[-1].append(cur)
        for group in groups:
            line_id = len(new_lines)
            for i in group:
                if new_words[i].line_id != line_id:
                    new_words[i] = new_words[i].model_copy(update={"line_id": line_id})
            rects = [words[i].rect for i in group]
            new_lines.append(
                line.model_copy(
                    update={
                        "id": line_id,
                        "text": " ".join(words[i].text for i in group),
                        "rect": (
                            min(r[0] for r in rects),
                            min(r[1] for r in rects),
                            max(r[2] for r in rects),
                            max(r[3] for r in rects),
                        ),
                        "word_ids": group,
                    }
                )
            )
    return new_words, new_lines


def link_labels(page: PageMap) -> None:
    """Set ``label_line_id`` and ``label_relation`` on each candidate (see :func:`find_label`)."""
    for cand in page.candidates:
        found = find_label(page, cand.rect, cand.kind)
        if found is None:
            cand.label_line_id, cand.label_relation = None, None
        else:
            line, relation = found
            cand.label_line_id, cand.label_relation = line.id, relation
