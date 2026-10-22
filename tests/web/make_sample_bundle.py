"""Build the hand-made showcase sample bundle the page tests use.

Writes ``tests/web/fixtures/sample-bundle/`` in the layout Task 13's real bundles use
(``demo/web/showcase/samples/<id>/``), plus ``tests/web/fixtures/index.json`` listing it, so the
showcase page can be pointed at ``?samples=/tests/web/fixtures/``. Source: ``forms/dev/fw9.pdf``
(dev split). The detections are canned, not a model run: each original field on page 0 shifted by
a point, with every sixth one left out, so the "found N of M" readout has misses to show.

Run: ``.venv/bin/python tests/web/make_sample_bundle.py``
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "web" / "fixtures"
OUT = FIXTURES / "sample-bundle"
FORM_ID = "fw9"
BUNDLE_ID = "sample-bundle"
RESULTS = ROOT / "eval" / "results" / "agent_anthropic_eval_dev_2026-10-21_defer-placement.json"
AGENT_TEMPLATE = FIXTURES / "templates" / "fw9_as_published.json"
LICENSE = "US federal government work (public domain)"

# Clearly fictional; validated against the schema below.
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


def _kind(widget: pymupdf.Widget) -> str:
    if widget.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        return "choice"
    if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
        return "signature"
    return "text"


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def build() -> None:
    """Write the bundle and the index."""
    manifest = {f["id"]: f for f in json.loads((ROOT / "forms" / "manifest.json").read_text())["forms"]}[FORM_ID]
    assert manifest["split"] == "dev", "the test bundle only uses dev forms"
    src = ROOT / "forms" / "dev" / f"{FORM_ID}.pdf"
    schema = json.loads((ROOT / "forms" / "schemas" / f"{FORM_ID}.json").read_text())
    jsonschema.validate(RECORD, schema)

    OUT.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src)
    try:
        originals: dict[str, list[list[float]]] = {}
        detections: dict[str, list[dict]] = {}
        for page in doc:
            widgets = [w for w in page.widgets() or [] if w.field_type != pymupdf.PDF_WIDGET_TYPE_BUTTON]
            if not widgets:
                continue
            rects = [[round(v, 2) for v in (w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1)] for w in widgets]
            originals[str(page.number)] = rects
            detections[str(page.number)] = [
                {"kind": _kind(w), "rect": [r[0] + 1.0, r[1] + 0.5, r[2] + 1.0, r[3] + 0.5], "conf": 0.9}
                for i, (w, r) in enumerate(zip(widgets, rects))
                if i % 6 != 5
            ]
        (OUT / "published.pdf").write_bytes(doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True))
        for page in doc:
            for w in list(page.widgets() or []):
                page.delete_widget(w)
        (OUT / "form.pdf").write_bytes(doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True))
        pages = doc.page_count
    finally:
        doc.close()

    results = json.loads(RESULTS.read_text())
    row = next(r for r in results["rows"] if r["form"] == FORM_ID and r["variant"] == "as_published")
    scores = {"run": results["meta"]["run"], "model_label": "Haiku 5.5", **row}

    title = "IRS W-9 (test bundle)"
    _write_json(OUT / "manifest.json", {
        "id": BUNDLE_ID,
        "form": FORM_ID,
        "title": title,
        "revision": manifest["revision"],
        "source_url": manifest["source"],
        "license": LICENSE,
        "pages": pages,
    })
    _write_json(OUT / "detections.json", {"model": "FFDNet-S-onnx", "pages": detections})
    _write_json(OUT / "original-fields.json", {"pages": originals})
    _write_json(OUT / "schema.json", schema)
    _write_json(OUT / "record.json", RECORD)
    shutil.copyfile(AGENT_TEMPLATE, OUT / "agent-template.json")
    _write_json(OUT / "agent-scores.json", scores)
    _write_json(FIXTURES / "index.json", [{
        "id": BUNDLE_ID,
        "title": title,
        "revision": manifest["revision"],
        "source_url": manifest["source"],
        "pages": pages,
    }])


if __name__ == "__main__":
    build()
