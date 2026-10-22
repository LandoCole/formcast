"""Build the hand-made showcase sample bundle the page tests use.

Writes ``tests/web/fixtures/sample-bundle/`` in the layout the real bundles use
(``demo/web/showcase/samples/<id>/``), plus ``tests/web/fixtures/index.json`` listing it, so the
showcase page can be pointed at ``?samples=/tests/web/fixtures/``. The files are written by
``demo/tools/build_showcase.py`` (``split_pdf``, ``write_bundle``, ``write_index``), the one bundle
writer. Source: ``forms/dev/fw9.pdf`` (dev split). The detections are canned, not a model run: each
original field on each page shifted by a point, with every sixth one left out, so the "found N of M"
readout has misses to show.

Run: ``.venv/bin/python tests/web/make_sample_bundle.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo" / "tools"))

import build_showcase as bs  # noqa: E402
import pymupdf  # noqa: E402

FIXTURES = ROOT / "tests" / "web" / "fixtures"
OUT = FIXTURES / "sample-bundle"
FORM_ID = "fw9"
BUNDLE_ID = "sample-bundle"
AGENT_TEMPLATE = FIXTURES / "templates" / "fw9_as_published.json"

# Clearly fictional; write_bundle validates it against the schema.
RECORD = {
    "vendor_id": "V-00417",
    "legal_name": "Juniper Lane Ceramics LLC",
    "business_name": "Juniper Lane Studio",
    "tax_classification": "llc",
    "llc_tax_classification": "s_corporation",
    "has_foreign_partners": False,
    "address": {"street": "418 Example Avenue", "city": "Springfield", "state": "OR", "postal_code": "97477"},
    "ein": "12-3456789",
    "remit_email": "ap@example.com",
    "certified_on": "2026-03-14",
}


def build() -> None:
    """Write the bundle and the index."""
    manifest = bs.form_entry(FORM_ID)
    assert manifest["split"] == "dev", "the test bundle only uses dev forms"
    pdfs = bs.split_pdf(ROOT / "forms" / "dev" / f"{FORM_ID}.pdf")
    # canned: every original shifted by (1, 0.5) points, every sixth one missed
    detections = {}
    doc = pymupdf.open(stream=pdfs["published"])
    try:
        for n, rects in pdfs["originals"].items():
            kinds = [bs.widget_kind(w) for w in bs.own_widgets(doc[n])]
            detections[str(n)] = [
                {"kind": kind, "rect": [r[0] + 1.0, r[1] + 0.5, r[2] + 1.0, r[3] + 0.5], "conf": 0.9}
                for i, (kind, r) in enumerate(zip(kinds, rects))
                if i % 6 != 5
            ]
    finally:
        doc.close()
    entry = bs.write_bundle(
        OUT,
        bundle_id=BUNDLE_ID,
        form_id=FORM_ID,
        title="IRS W-9 (test bundle)",
        pdfs=pdfs,
        detections=detections,
        schema=json.loads((ROOT / "forms" / "schemas" / f"{FORM_ID}.json").read_text()),
        record=RECORD,
        template=AGENT_TEMPLATE.read_bytes(),
        scores=bs.agent_scores(FORM_ID),
    )
    bs.write_index(FIXTURES / "index.json", [entry])


if __name__ == "__main__":
    build()
