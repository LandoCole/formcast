"""Group a folder of PDFs by layout before any template exists, and pick the documents to build a template from.

``scan`` runs first, on a whole batch. It matches nothing and detects nothing: per document it reads the page count
and sizes, the blind fingerprint (field values hidden), the layout signature (a coarse ink map and a MinHash of the
wording per page, field values hidden) and whether any form field holds a value (*fillable*, else *flattened*: the
answers are in the ink). Documents with the same blind fingerprint are one group at once. The rest join the first
group whose first member has the same page count and sizes and a signature at least ``same`` close to it, or start
a group of their own. Groups come back largest first; the largest is the main layout.

A fillable copy that is filled in rarely shares a blind fingerprint with another one: the answers also reach the
page text through other paths (a paragraph that names the client's bank), so in practice most documents are grouped
by the signature. A document that cannot be read (encrypted, corrupt, empty) is listed with the reason and skipped.

The scan is a preview for choosing a sample and seeing the mix, not the authority on which documents a finished
template fits (the run's own matching is). ``first_page_only`` reads the fingerprint and the signature from the
first page alone, for batches of tens of thousands, where the rendering of every page is the slow part; page count
and sizes still come from the whole document.

``sample`` picks the documents of a group to build a template from: evenly spaced members, fillable and flattened
ones in proportion to their numbers, and at least one of each when both exist.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from formcast.errors import FormcastError
from formcast.extract.page import DocText
from formcast.match import (
    MINHASH_EMPTY,
    SIZE_TOLERANCE,
    compute_signature,
    fingerprint_document,
    pdf_features,
    signature_similarity,
)
from formcast.pagemap.extract import open_pdf, page_size
from formcast.pagemap.model import FormSignature

# Signature closeness (``signature_similarity``: the mean of the wording part and the ink part, 0 to 1) at which a
# document joins a group. Measured against the first member of a group, over every pair of the generated corpus's
# 20 client-information forms (tests/extract/corpus.py: 13 fillable, 7 flattened, one flattened copy shifted down by
# SHIFT, one fillable copy with /Rotate 90), and against the tracked 2-page Letter forms of forms/dev, the only
# other layouts that pass the page count and size test (the W-9 has 6 pages, so it never gets compared):
#     - fillable with fillable:                 0.91 to 0.98
#     - fillable with flattened:                0.78 to 0.85
#     - flattened with flattened:               0.79 to 0.83
#     - the shifted copy with the others:       0.57 to 0.74
#     - the rotated copy with the others:       0.54 to 0.71 (its ink map is of the page as rendered, sideways)
#     - any client form with columbus_medhist, f8949 or csudh_health: 0.06 to 0.16
# So the lowest score of one layout is 0.54 and the highest of two layouts is 0.16. 0.3 sits clear of both: it keeps
# shifted, rotated, flattened and filled copies of a form together and parts another form from it. A revised edition
# of the same form (most wording the same) falls in the gap and joins; ``ScanGroup.closeness`` (a group's first
# member against the main group's) tells such an edition from a stranger, and a higher ``same`` splits it.
# A document with no text layer scores on its ink map alone (a blank page 0.34 to 0.37 against the corpus form), so it
# never joins a group whose first member has text, nor the other way round.
SCAN_SAME = 0.3


class ScanGroup(BaseModel):
    """Documents of one layout. ``fillable_files`` are the members with form-field values (a subset of ``files``).

    ``closeness`` is the signature similarity of this group's first member to the main group's first member, 0 to 1
    (``None`` for the main group): high for another edition of the form, near 0 for a stranger.
    """

    id: int
    files: list[str]
    fillable: int
    flattened: int
    page_count: int
    fillable_files: list[str] = []
    closeness: float | None = None


class ScanReport(BaseModel):
    """What ``scan`` found: groups largest first (``id`` counts from 1), unreadable files as ``(path, reason)``."""

    groups: list[ScanGroup]
    unreadable: list[tuple[str, str]]
    seconds: float

    @property
    def main(self) -> ScanGroup | None:
        """The largest group, ``None`` when no document could be read."""
        return self.groups[0] if self.groups else None


@dataclass
class _Doc:
    """What the scan reads of one document. Only a group's first one is kept whole; the others shrink to a path."""

    path: str
    key: tuple  # page count, rounded page sizes and blind fingerprint: documents with one key are one group
    sizes: list[tuple[float, float]]
    signature: FormSignature
    fillable: bool

    @property
    def has_text(self) -> bool:
        return any(t != [MINHASH_EMPTY] * len(t) for t in self.signature.text)


@dataclass
class _Group:
    first: _Doc  # kept for comparing later documents with; every other member is just ``(path, fillable)``
    members: list[tuple[str, bool]] = field(default_factory=list)


def _read(path: Path, first_page_only: bool) -> _Doc:
    """Read one document's scan features. Raises ``FormcastError`` (``OSError`` is turned into one)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FormcastError("cannot open PDF") from exc
    if first_page_only:
        doc = open_pdf(data)
        try:
            page_count, sizes = doc.page_count, [page_size(p) for p in doc]
            doc.select([0])
            blind, signature = fingerprint_document(doc, ignore_field_values=True), compute_signature(doc)
        finally:
            doc.close()
    else:
        features = pdf_features(data)
        sizes = features.page_sizes
        page_count, blind, signature = len(sizes), features.fingerprint_blind, features.signature
    with DocText(data) as text:  # form fields only: the same answer ``DocText.has_values`` gives everywhere else
        fillable = text.has_values
    key = (page_count, tuple((round(w), round(h)) for w, h in sizes), blind)
    return _Doc(str(path), key, sizes, signature, fillable)


def _same_pages(a: _Doc, b: _Doc) -> bool:
    return len(a.sizes) == len(b.sizes) and all(
        abs(x[0] - y[0]) <= SIZE_TOLERANCE and abs(x[1] - y[1]) <= SIZE_TOLERANCE for x, y in zip(a.sizes, b.sizes)
    )


def _joins(first: _Doc, doc: _Doc, same: float) -> bool:
    """Whether ``doc`` belongs with a group led by ``first``: pages, text or not, and a close enough signature."""
    return (
        _same_pages(first, doc)
        and first.has_text == doc.has_text  # without text the score is the ink map alone, which any blank page matches
        and signature_similarity(first.signature, doc.signature) >= same
    )


def scan(paths: Iterable[str | Path], *, same: float = SCAN_SAME, first_page_only: bool = False) -> ScanReport:
    """Group ``paths`` by layout (see the module docstring); ``same`` is the signature closeness to join a group.

    Unreadable files are listed in ``unreadable`` with the reason, and the scan goes on. Nothing is detected or
    matched, and no model is called.
    """
    started = time.perf_counter()
    groups: list[_Group] = []
    by_key: dict[tuple, _Group] = {}
    unreadable: list[tuple[str, str]] = []
    for path in paths:
        path = Path(path)
        try:
            doc = _read(path, first_page_only)
        except FormcastError as exc:
            unreadable.append((str(path), str(exc)))
            continue
        except Exception as exc:  # MuPDF raises several types on damaged files it could still open
            unreadable.append((str(path), f"cannot read PDF: {exc}"))
            continue
        group = by_key.get(doc.key)
        if group is None:
            group = next(
                (
                    g
                    for g in groups
                    if _joins(g.first, doc, same)
                ),
                None,
            )
            if group is None:
                group = _Group(first=doc)
                groups.append(group)
            by_key[doc.key] = group
        group.members.append((doc.path, doc.fillable))
    groups.sort(key=lambda g: -len(g.members))  # stable: equal sizes keep the order they were found in
    main = groups[0].first if groups else None
    return ScanReport(
        groups=[
            ScanGroup(
                id=n,
                files=[path for path, _ in g.members],
                fillable_files=[path for path, fillable in g.members if fillable],
                fillable=sum(fillable for _, fillable in g.members),
                flattened=sum(not fillable for _, fillable in g.members),
                page_count=len(g.first.sizes),
                closeness=None if n == 1 else signature_similarity(main.signature, g.first.signature),
            )
            for n, g in enumerate(groups, start=1)
        ],
        unreadable=unreadable,
        seconds=time.perf_counter() - started,
    )


def _spaced(items: list[str], k: int) -> list[str]:
    """``k`` of ``items`` (``k <= len(items)``) evenly spaced over the whole list, each from the middle of its share."""
    return [items[int((i + 0.5) * len(items) / k)] for i in range(k)]


def sample(group: ScanGroup, n: int, how: Literal["spread", "first"] = "spread") -> list[str]:
    """``n`` of the group's files to build a template from, in the group's order (all of them when it has fewer).

    ``"first"`` takes the first ``n``. ``"spread"`` takes evenly spaced members, fillable and flattened ones in
    proportion to their numbers, and at least one of each when the group has both and ``n`` is at least 2.
    """
    if n < 1:
        raise FormcastError("a sample needs at least 1 document")
    if n >= len(group.files):
        return list(group.files)
    if how == "first":
        return group.files[:n]
    has_values = set(group.fillable_files)
    fillable = [f for f in group.files if f in has_values]
    flattened = [f for f in group.files if f not in has_values]
    if fillable and flattened:
        if n == 1:
            want = int(len(fillable) >= len(flattened))  # one document cannot be both: take the commoner kind
        else:
            want = min(max(round(n * len(fillable) / len(group.files)), 1), n - 1)
            want = min(max(want, n - len(flattened)), len(fillable))  # never more of a kind than there are
    else:
        want = n if fillable else 0
    picked = set(_spaced(fillable, want) if want else []) | set(_spaced(flattened, n - want) if n - want else [])
    return [f for f in group.files if f in picked]
