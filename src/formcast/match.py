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
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import numpy as np
import pymupdf

from formcast.pagemap.extract import open_pdf, page_size

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


def fingerprint_document(doc: pymupdf.Document) -> str:
    """Fingerprint of an open document (see :func:`fingerprint`)."""
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
    doc = open_pdf(pdf)
    try:
        return fingerprint_document(doc)
    finally:
        doc.close()
