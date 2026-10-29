"""Reading the sample PDFs a template is built from, and what a model sees of each.

A sample is a filled PDF of the layout the template is for. :func:`read_samples` opens each one, builds its form
map (text, rules, widgets, label links) and, for the pages that are not fillable, finds the blanks with the field
detectors. Which pages are searched is the ``detect`` mode:

    auto     every page that is not fillable and does not read as running text (``looks_like_prose``)
    all      every page that is not fillable
    manual   the pages a person names in ``pages`` (``{sample position: [page numbers]}``)
    off      none

A :class:`SampleDoc` keeps the PDF bytes, the form map and the value of every form field the PDF holds.
:func:`sample_view` writes a sample as text for a model (its form map and field values) and :func:`sample_images`
renders its pages, those with the most boxes first. Nothing here calls a model. Each sample's form map is built,
and each page's blanks detected, holding :data:`~formcast.pagemap.extract.PDF_LOCK` (one sample, or one sample's
pages, at a time), so reading samples may run beside other threads' MuPDF work; ``on_page`` is called with it held.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from formcast.data.ai import MAX_IMAGE_PAGES
from formcast.errors import FormcastError, SessionCancelled
from formcast.extract.page import DocText
from formcast.pagemap.build import DetectPolicy, PageProgress, build_form_map, detect_on_page, looks_like_prose
from formcast.pagemap.detect import Detector, load_default_detectors
from formcast.pagemap.extract import PDF_LOCK, open_pdf
from formcast.pagemap.model import FormMap
from formcast.pagemap.summarize import summarize_page
from formcast.pagepass.marks import render_marked_page
from formcast.template.model import candidate_fields

__all__ = [
    "Detect",
    "SAMPLE_CHARS",
    "SampleDoc",
    "SamplePdf",
    "detected_pages",
    "read_samples",
    "sample_images",
    "sample_view",
]

Detect = Literal["auto", "all", "manual", "off"]

#: The most characters of one sample's view that go to a model.
SAMPLE_CHARS = 20_000


@dataclass(frozen=True)
class SamplePdf:
    """A sample as given: its file name (for messages) and the PDF's bytes."""

    file: str
    pdf: bytes


@dataclass
class SampleDoc:
    """A sample read: the PDF, its form map and ``values``, what each form field holds by name.

    ``values`` leaves out a field that is empty, a check box that is off and a radio group with none selected.
    """

    file: str
    pdf: bytes
    form_map: FormMap
    values: dict[str, str | bool]
    _detected: list[int] = field(default_factory=list, repr=False)


def read_samples(
    samples: list[SamplePdf],
    *,
    detect: Detect = "auto",
    pages: dict[int, list[int]] | None = None,
    ocr: Literal["auto", "never"] = "auto",
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy | None = None,
    on_page: Callable[[int, PageProgress], None] | None = None,
) -> list[SampleDoc]:
    """Read every sample: its form map, its form field values and, on the pages ``detect`` picks, its blanks.

    ``pages`` maps a sample's position in ``samples`` to the page numbers (from 0) searched for blanks when
    ``detect="manual"``; a number the document lacks and a fillable page are ignored. ``detectors`` and
    ``policy`` are as for :func:`~formcast.pagemap.build.build_form_map`. ``on_page(i, progress)`` hears of each
    page's steps in sample ``i``. The two halves, reading and detection, are :func:`_read` and :func:`_detect`
    (the setup flow runs them as stages of their own).

    Raises:
        FormcastError: a sample cannot be read, naming its file ("b.pdf: cannot open PDF").
        SessionCancelled: raised by ``on_page`` (or a detector) to stop, passed on unchanged.
        ValueError: for an unknown ``detect`` mode.
    """
    _check_detect(detect)
    docs = _read(samples, ocr=ocr, detectors=detectors, policy=policy, on_page=on_page)
    return _detect(docs, detect=detect, pages=pages, detectors=detectors, policy=policy)


def _check_detect(detect: str) -> None:
    """Raises: ValueError: for an unknown ``detect`` mode."""
    if detect not in ("auto", "all", "manual", "off"):
        raise ValueError(f"detect must be 'auto', 'all', 'manual' or 'off', not {detect!r}")


def _read(
    samples: list[SamplePdf],
    *,
    ocr: Literal["auto", "never"] = "auto",
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy | None = None,
    on_page: Callable[[int, PageProgress], None] | None = None,
) -> list[SampleDoc]:
    """Every sample's form map, with no detection, and its form field values; as :func:`read_samples` says.

    Raises:
        FormcastError: a sample cannot be read, naming its file.
    """
    policy = policy or DetectPolicy()
    docs = []
    for i, sample in enumerate(samples):
        progress = None if on_page is None else (lambda event, i=i: on_page(i, event))
        try:
            with PDF_LOCK:  # build_form_map takes no lock of its own; one sample at a time holds it
                form_map = build_form_map(
                    sample.pdf, detectors=detectors, policy=policy, ocr=ocr, detect=False, on_progress=progress
                )
            with DocText(sample.pdf) as text:
                values = _values(form_map, text)
        except SessionCancelled:
            raise  # the person stopped the build: not something wrong with this sample
        except FormcastError as exc:
            raise FormcastError(f"{sample.file}: {exc}") from exc
        docs.append(SampleDoc(sample.file, sample.pdf, form_map, values))
    return docs


def _detect(
    docs: list[SampleDoc],
    *,
    detect: Detect = "auto",
    pages: dict[int, list[int]] | None = None,
    detectors: dict[str, Detector] | None = None,
    policy: DetectPolicy | None = None,
) -> list[SampleDoc]:
    """``docs`` (as :func:`_read` gives them) with their blanks found on the pages ``detect`` picks, as new docs.

    The stock detectors are loaded once, and only when some page is searched.

    Raises:
        FormcastError: a sample cannot be read, naming its file.
        ValueError: for an unknown ``detect`` mode.
    """
    _check_detect(detect)
    policy = policy or DetectPolicy()
    out = []
    for i, doc in enumerate(docs):
        wanted = _pages_to_detect(doc.form_map, detect, (pages or {}).get(i, []))
        form_map = doc.form_map
        if wanted:
            if detectors is None:
                detectors = load_default_detectors()
            try:
                form_map = _detect_pages(doc.pdf, form_map, wanted, detectors, policy)
            except SessionCancelled:
                raise
            except FormcastError as exc:
                raise FormcastError(f"{doc.file}: {exc}") from exc
        out.append(SampleDoc(doc.file, doc.pdf, form_map, dict(doc.values), wanted))
    return out


def _pages_to_detect(form_map: FormMap, detect: Detect, named: list[int]) -> list[int]:
    """The pages (from 0, in order) ``detect`` searches: never a fillable page."""
    searchable = [p for p in form_map.pages if p.kind != "fillable"]
    if detect == "auto":
        return [p.index for p in searchable if not looks_like_prose(p)]
    if detect == "all":
        return [p.index for p in searchable]
    if detect == "manual":
        return [p.index for p in searchable if p.index in named]
    return []


def _detect_pages(
    pdf: bytes, form_map: FormMap, wanted: list[int], detectors: dict[str, Detector], policy: DetectPolicy
) -> FormMap:
    """``form_map`` with detection run on the pages ``wanted``."""
    pages = list(form_map.pages)
    with PDF_LOCK:
        doc = open_pdf(pdf)
        try:
            for n, page_map in enumerate(pages):
                if page_map.index in wanted:
                    pages[n] = detect_on_page(doc[page_map.index], page_map, detectors=detectors, policy=policy)
        finally:
            doc.close()
    return form_map.model_copy(update={"pages": pages})


def _values(form_map: FormMap, text: DocText) -> dict[str, str | bool]:
    values: dict[str, str | bool] = {}
    for page in form_map.pages:
        for widget in page.widgets:
            value = text.widget_value(widget.name)
            if value is not None and value is not False and value != "":
                values[widget.name] = value
    return values


def detected_pages(doc: SampleDoc) -> list[int]:
    """The pages (from 0) that were searched for blanks when ``doc`` was read, in order."""
    return list(doc._detected)


def sample_view(doc: SampleDoc, index: int, *, max_chars: int = SAMPLE_CHARS) -> str:
    """``doc`` as text for a model, kept to ``max_chars`` unless the header and the values alone are longer.

    In order: a header (``SAMPLE 2 (a.pdf, fillable, 2 pages)``; ``fillable`` when any page has form fields, else
    ``flattened``); ``FORM FIELD VALUES:`` with a ``name = "value"`` line per form field that holds one (JSON, so a
    value stays on one line and a check box reads ``true``); then, per page (from 0), ``PAGE n`` and the page's form
    map, and for a page that is not fillable ``PAGE n TEXT:`` with its text lines as ``[x0,y0,x1,y1] text`` in whole
    points, because the filled answers of a flattened or scanned page are printed text the form map leaves out.

    The header and the values are never cut. What is left of ``max_chars`` goes to the pages in order: the form maps
    first, each cut at an item line to its share when they do not all fit, then the text lines from the first page
    on, so the last page's text goes first; a page whose text is cut ends it with a note saying how many lines went.
    """
    pages = doc.form_map.pages
    kind = "fillable" if any(p.kind == "fillable" for p in pages) else "flattened"
    head = [f"SAMPLE {index} ({doc.file}, {kind}, {len(pages)} page{'' if len(pages) == 1 else 's'})"]
    if doc.values:
        head.append("FORM FIELD VALUES:")
        head += [f"{name} = {json.dumps(value, ensure_ascii=False)}" for name, value in doc.values.items()]
    space = max_chars - len("\n".join(head)) - 1
    maps = [f"PAGE {p.index}\n{summarize_page(doc.form_map, p.index)}" for p in pages]
    titles = [f"PAGE {p.index} TEXT:" if p.kind != "fillable" else "" for p in pages]
    if sum(len(m) + len(t) + 2 for m, t in zip(maps, titles)) > space:
        maps = _shared(doc, space - sum(len(t) + 1 for t in titles))
    space = max(space - sum(len(m) + len(t) + 2 for m, t in zip(maps, titles)), 0)
    parts = []
    for form_map, title, lines in zip(maps, titles, _page_texts(doc)):
        shown, space = _fit(lines, space)
        parts.append("\n".join([form_map, *([title, *shown] if title else [])]))
    # The shares are counted with a little to spare, but a budget too small for even the page headings still wins.
    room = max_chars - len("\n".join(head)) - 1
    body = "\n".join(parts)
    if len(body) > room:
        body = body[: max(room, 0)]
        body = body[: body.rfind("\n")] if "\n" in body else ""
    return "\n".join([*head, body]) if body else "\n".join(head)


def _fit(lines: list[str], space: int) -> tuple[list[str], int]:
    """The leading ``lines`` that fit in ``space`` characters (a newline after each) and the space left. When some
    do not fit, the last line is a note saying how many were dropped (kept only if it fits itself) and no space is
    left for the pages after."""
    if sum(len(ln) + 1 for ln in lines) <= space:
        return lines, space - sum(len(ln) + 1 for ln in lines)
    note = f"[{len(lines)} more text lines dropped]"  # as long as any note this can need
    shown: list[str] = []
    for line in lines:
        if len(line) + len(note) + 2 > space:
            break
        shown.append(line)
        space -= len(line) + 1
    note = f"[{len(lines) - len(shown)} more text lines dropped]"
    if len(note) + 1 <= space:
        shown.append(note)
    return shown, 0


def _shared(doc: SampleDoc, space: int) -> list[str]:
    """Each page's ``PAGE n`` and form map, the pages sharing ``space`` characters in order."""
    maps = []
    pages = doc.form_map.pages
    for n, page in enumerate(pages):
        share = max(space // (len(pages) - n) - len(f"PAGE {page.index}\n") - 1, 0)
        maps.append(f"PAGE {page.index}\n{summarize_page(doc.form_map, page.index, max_chars=share)}")
        space -= len(maps[-1]) + 1
    return maps


def _page_texts(doc: SampleDoc) -> list[list[str]]:
    """The text lines of each page that is not fillable, as ``[x0,y0,x1,y1] text``; ``[]`` for a fillable page."""
    texts: list[list[str]] = []
    with DocText(doc.pdf) as pdf:
        for page in doc.form_map.pages:
            if page.kind == "fillable":
                texts.append([])
                continue
            if page.text_source == "ocr":  # the PDF has no text of its own here; the form map holds the OCR
                lines = [(ln.text, ln.rect) for ln in page.lines]
            else:
                lines = pdf.lines(page.index)
            texts.append([f"[{','.join(str(round(v)) for v in rect)}] {text}" for text, rect in lines if text.strip()])
    return texts


def sample_images(doc: SampleDoc, *, max_pages: int = MAX_IMAGE_PAGES) -> list[tuple[int, bytes]]:
    """PNGs of ``doc``'s pages as ``(page number, image)``, at most ``max_pages``: the pages with the most boxes
    (widgets and detected blanks) first, then in page order. The detected blanks are drawn on their pages."""
    ranked = sorted(doc.form_map.pages, key=lambda p: (-(len(p.widgets) + len(p.candidates)), p.index))
    return [(p.index, render_marked_page(doc.pdf, p.index, candidate_fields(p))) for p in ranked[:max_pages]]
