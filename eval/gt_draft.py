"""Draft ground truth for a flat or scanned corpus form, for a human to correct.

The draft is the union of both CommonForms detectors (FFDNet-L and FFDetr, via
``formcast.pagemap.detect``) on each scored page, with overlapping boxes
(IoU >= 0.5, either class) collapsed to the more confident one, and each box's
nearest label line from ``formcast.pagemap`` label linking (plus, for choice
boxes, the option text to its right). It is a starting point only: ground truth
records what the form asks for, not what the detectors found (see the labeling
guide).

Writes ``forms/gt/<id>.draft.json`` and one overlay PNG per scored page under
``eval/results/overlays/gt/`` (boxes numbered by their index in ``fields``,
colored by type). ``--gt`` instead draws the corrected ``forms/gt/<id>.json``.

Usage:
    python eval/gt_draft.py <id> [--gt]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))  # do not rely on the editable install
sys.path.insert(0, str(HERE))

import pymupdf  # noqa: E402
from PIL import ImageDraw, ImageFont  # noqa: E402

import score  # noqa: E402
from formcast.pagemap.build import build_form_map  # noqa: E402
from formcast.pagemap.detect import DetectPolicy, Detector, load_default_detectors  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.pagemap.ocr import render  # noqa: E402
from formcast.pagemap.query import right_text  # noqa: E402

FORMS = ROOT / "forms"
OVERLAY_DIR = ROOT / "eval" / "results" / "overlays" / "gt"
DETECTORS = ("FFDNet-L", "FFDetr")
DEDUP_IOU = 0.5
DPI = 144
TYPE_COLORS = {"text": (220, 0, 0), "choice": (0, 90, 230), "signature": (200, 0, 200)}

UNION = DetectPolicy(digital=DETECTORS, scanned=DETECTORS, merge_iou=DEDUP_IOU, dpi=DPI)


def _round(rect) -> list[float]:
    return [round(float(v), 2) for v in rect]


def draft(form: dict, pdf_path, detectors: dict[str, Detector] | None = None) -> dict:
    """Draft GT ``{"form", "pages", "fields", "notes"}`` for a flat/scanned manifest entry.

    Each field has ``page``, ``rect``, ``type``, ``label`` (linked label line
    text, "" when none), ``label_relation``, ``option`` (choice boxes: the text
    right after the box), ``conf`` and ``detector``.
    """
    if form["kind"] == "fillable":
        raise SystemExit(f"{form['id']} is fillable: its GT boxes are its widgets (use widget_paths)")
    detectors = load_default_detectors() if detectors is None else detectors
    fm = build_form_map(pdf_path, detectors=detectors, policy=UNION)
    pages = score.scored_pages(form, len(fm.pages))
    fields = []
    for pno in pages:
        page = fm.pages[pno]
        lines = {ln.id: ln for ln in page.lines}
        for c in page.candidates:
            label = lines[c.label_line_id].text if c.label_line_id in lines else ""
            entry = {
                "page": pno,
                "rect": _round(c.rect),
                "type": c.kind,
                "label": label,
                "label_relation": c.label_relation,
                "conf": round(c.conf, 3),
                "detector": c.detector,
            }
            if c.kind == "choice":
                opt = right_text(page, c.rect)
                entry["option"] = opt.text if opt else ""
            fields.append(entry)
    notes = (
        f"DRAFT: union of {', '.join(DETECTORS)} (dedup IoU {DEDUP_IOU}) on pages {pages}; "
        "correct into <id>.json per the labeling guide"
    )
    return {"form": form["id"], "pages": pages, "fields": fields, "notes": notes}


def render_overlays(gt: dict, pdf_path, out_dir: Path, suffix: str, color=None, pages=None) -> list[Path]:
    """Draw ``gt["fields"]`` numbered on each page (``pages``, default the pages they are on).

    Boxes are ``color`` or colored by type. Returns the PNG paths
    ``<out_dir>/<form>_p<page>_<suffix>.png``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = gt.get("fields", [])
    pages = sorted({f["page"] for f in fields}) if pages is None else pages
    font = ImageFont.load_default(size=11)
    scale = DPI / 72
    paths = []
    doc = open_pdf(pdf_path)
    try:
        for pno in pages:
            page = doc[pno]
            for w in list(page.widgets() or []):
                page.delete_widget(w)
            img = render(page, DPI)
            d = ImageDraw.Draw(img)
            for i, f in enumerate(fields):
                if f["page"] != pno:
                    continue
                r = pymupdf.Rect(f["rect"]) * page.rotation_matrix
                box = [r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale]
                col = color or TYPE_COLORS[f["type"]]
                d.rectangle(box, outline=col, width=2)
                d.text((box[0] + 1, box[1] - 11), str(i), fill=col, font=font)
            path = out_dir / f"{gt['form']}_p{pno}_{suffix}.png"
            img.save(path)
            paths.append(path)
    finally:
        doc.close()
    return paths


def write_draft(d: dict, pdf_path, gt_dir: Path = FORMS / "gt", overlay_dir: Path = OVERLAY_DIR) -> Path:
    """Write ``<gt_dir>/<id>.draft.json`` and the draft overlays; return the JSON path."""
    gt_dir = Path(gt_dir)
    gt_dir.mkdir(parents=True, exist_ok=True)
    out = gt_dir / f"{d['form']}.draft.json"
    out.write_text(json.dumps(d, indent=1) + "\n")
    render_overlays(d, pdf_path, overlay_dir, suffix="draft", pages=d["pages"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_id")
    ap.add_argument("--gt", action="store_true", help="draw forms/gt/<id>.json (green) instead of drafting")
    args = ap.parse_args()
    manifest = {f["id"]: f for f in json.loads((FORMS / "manifest.json").read_text())["forms"]}
    if args.form_id not in manifest:
        raise SystemExit(f"unknown form {args.form_id!r}")
    form = manifest[args.form_id]
    pdf_path = FORMS / form["split"] / f"{form['id']}.pdf"
    if args.gt:
        gt = json.loads((FORMS / "gt" / f"{form['id']}.json").read_text())
        for p in render_overlays(gt, pdf_path, OVERLAY_DIR, suffix="gt", color=(0, 160, 0)):
            print(p)
        return
    out = write_draft(draft(form, pdf_path), pdf_path)
    print(out)


if __name__ == "__main__":
    main()
