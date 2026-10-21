"""Fingerprint: a stable id for "this exact blank form".

It is not a file hash: a re-saved PDF differs byte for byte but keeps its page
count, page sizes and text, which is what the fingerprint hashes. Only the PDF's
own text layer is used (never OCR), so the fingerprint is cheap and deterministic.
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pymupdf

from formcast.pagemap.extract import open_pdf, page_size

# Characters of normalized text hashed per page.
TEXT_CHARS = 2000


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def fingerprint_document(doc: pymupdf.Document) -> str:
    """Fingerprint of an open document (see :func:`fingerprint`)."""
    digest = hashlib.sha256()
    digest.update(f"pages:{doc.page_count}\n".encode())
    for page in doc:
        width, height = page_size(page)
        text = _normalize(page.get_text("text", sort=True))[:TEXT_CHARS]
        digest.update(f"{round(width)}x{round(height)}\n{text}\n".encode())
    return digest.hexdigest()[:16]


def fingerprint(pdf: str | Path | bytes) -> str:
    """sha256 of page count, rounded page sizes and the first 2000 normalized
    text characters of each page, as 16 hex digits.

    Raises:
        FormcastError: "encrypted PDF" or "cannot open PDF".
    """
    doc = open_pdf(pdf)
    try:
        return fingerprint_document(doc)
    finally:
        doc.close()
