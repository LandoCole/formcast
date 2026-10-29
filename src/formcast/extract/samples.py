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
renders its pages, those with the most boxes first. Nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from formcast.data.ai import MAX_IMAGE_PAGES
from formcast.errors import FormcastError
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
    page's steps in sample ``i``.

    Raises:
        FormcastError: a sample cannot be read, naming its file ("b.pdf: cannot open PDF").
        ValueError: for an unknown ``detect`` mode.
    """
    if detect not in ("auto", "all", "manual", "off"):
        raise ValueError(f"detect must be 'auto', 'all', 'manual' or 'off', not {detect!r}")
    policy = policy or DetectPolicy()
    docs = []
    for i, sample in enumerate(samples):
        progress = None if on_page is None else (lambda event, i=i: on_page(i, event))
        try:
            form_map = build_form_map(
                sample.pdf, detectors=detectors, policy=policy, ocr=ocr, detect=False, on_progress=progress
            )
            wanted = _pages_to_detect(form_map, detect, (pages or {}).get(i, []))
            if wanted:
                form_map = _detect(sample.pdf, form_map, wanted, detectors, policy)
            with DocText(sample.pdf) as text:
                values = _values(form_map, text)
        except FormcastError as exc:
            raise FormcastError(f"{sample.file}: {exc}") from exc
        docs.append(SampleDoc(sample.file, sample.pdf, form_map, values, wanted))
    return docs


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


def _detect(
    pdf: bytes, form_map: FormMap, wanted: list[int], detectors: dict[str, Detector] | None, policy: DetectPolicy
) -> FormMap:
    """``form_map`` with detection run on the pages ``wanted`` (the stock detectors are loaded once)."""
    if detectors is None:
        detectors = load_default_detectors()
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
    """``doc`` as text for a model, cut to ``max_chars``.

    A header (``SAMPLE 2 (a.pdf, fillable, 2 pages)``; ``fillable`` when any page has form fields, else
    ``flattened``), then ``PAGE n`` and the page's form map for each page (from 0), then ``FORM FIELD VALUES:``
    with a ``name = "value"`` line per form field that holds one (a check box as ``true``).
    """
    kind = "fillable" if any(p.kind == "fillable" for p in doc.form_map.pages) else "flattened"
    count = len(doc.form_map.pages)
    parts = [f"SAMPLE {index} ({doc.file}, {kind}, {count} page{'' if count == 1 else 's'})"]
    for page in doc.form_map.pages:
        parts += [f"PAGE {page.index}", summarize_page(doc.form_map, page.index)]
    if doc.values:
        parts.append("FORM FIELD VALUES:")
        for name, value in doc.values.items():
            parts.append(f"{name} = {str(value).lower()}" if isinstance(value, bool) else f'{name} = "{value}"')
    return "\n".join(parts)[:max_chars]


def sample_images(doc: SampleDoc, *, max_pages: int = MAX_IMAGE_PAGES) -> list[tuple[int, bytes]]:
    """PNGs of ``doc``'s pages as ``(page number, image)``, at most ``max_pages``: the pages with the most boxes
    (widgets and detected blanks) first, then in page order. The detected blanks are drawn on their pages."""
    ranked = sorted(doc.form_map.pages, key=lambda p: (-(len(p.widgets) + len(p.candidates)), p.index))
    return [(p.index, render_marked_page(doc.pdf, p.index, candidate_fields(p))) for p in ranked[:max_pages]]
