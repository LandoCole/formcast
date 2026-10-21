"""Baseline: CommonForms detectors alone (no agent) on the test corpus.

Runs the formcast detectors (``formcast.pagemap.detect``) one at a time, FFDNet-L
and FFDetr, on each page as published and, for flat/fillable forms, on a seeded
fake scan of it. Scoring is ``eval/score.py``.

For fillable forms the published widgets are ground truth and are stripped before
detection (``build_form_map`` skips detection on pages with widgets). For flat and
scanned forms ground truth comes from forms/gt/<id>.json when it exists; otherwise
only detection counts and overlays are recorded (for review by eye).

Each run is a bare detector: a ``DetectPolicy`` naming that one detector, no
deskew, and no overlap suppression (``merge_iou`` above 1 never drops a box), so
the numbers are the detector's own output rather than the per-kind pipeline's.
Fake scans are detected on the degraded image itself: ``_OnImage`` hands that
image to the detector in place of ``detect_page``'s clean render, and
``detect_page`` maps the boxes into page points. The degraded image is a
function of the 144 dpi render of the stripped page, as in the first baseline.

Usage:
    python eval/baseline_detection.py [--split dev|heldout|all] [--out eval/results]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))  # do not rely on the editable install
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import score  # noqa: E402
from formcast.pagemap.detect import DetectPolicy, Detector, detect_page, load_default_detectors  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.pagemap.ocr import render  # noqa: E402

FORMS = ROOT / "forms"
DETECTORS = ("FFDNet-L", "FFDetr")
TYPES = score.TYPES
DPI = 144  # what commonforms renders at
IOU_THRESHOLDS = (0.5, 0.3)
OVERLAY_COLORS = {"text": (220, 0, 0), "choice": (0, 90, 230), "signature": (200, 0, 200)}


# ---------- page selection ----------

def iter_pages(form: dict, pdf_path: Path, gt: list[score.GTField] | None):
    """Yield (page number, page with widgets stripped, that page's GT or None).

    Pages are in memory only; the PDF on disk is untouched. Only the manifest's
    ``scored_pages`` are visited (every page when absent), and fillable pages
    without widgets (instruction pages) are skipped.
    """
    doc = open_pdf(pdf_path)
    try:
        for pno in score.scored_pages(form, doc.page_count):
            page = doc[pno]
            page_gt = None if gt is None else [g for g in gt if g.page == page.number]
            if form["kind"] == "fillable" and not page_gt:
                continue
            for w in list(page.widgets() or []):
                page.delete_widget(w)
            yield page.number, page, page_gt
    finally:
        doc.close()


def fake_scan(img: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    g = img.convert("L").rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, fillcolor=255)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    a = np.asarray(g).astype(np.int16) + nrng.normal(0, 12, (g.height, g.width)).astype(np.int16)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB")


# ---------- detection ----------

class _OnImage:
    """A detector that looks at a fixed image instead of the one it is handed."""

    def __init__(self, detector: Detector, image: Image.Image):
        self.name = detector.name
        self._detector = detector
        self._image = image

    def detect(self, image: Image.Image):
        return self._detector.detect(self._image)


def bare_policy(name: str) -> DetectPolicy:
    return DetectPolicy(digital=(name,), scanned=(name,), merge_iou=1.01, dpi=DPI, deskew_scanned=False)


def detect(page: pymupdf.Page, name: str, dets: dict[str, Detector], image: Image.Image | None = None):
    """Boxes ``(type, page, rect)`` from detector ``name`` alone, on the page or on ``image``."""
    det = dets[name] if image is None else _OnImage(dets[name], image)
    cands = detect_page(page, "digital", {name: det}, bare_policy(name))
    return [(c.kind, page.number, c.rect) for c in cands]


# ---------- output ----------

def score_row(gt: list[score.GTField], boxes) -> dict:
    out = {}
    for thr in IOU_THRESHOLDS:
        s = score.score_detection(gt, boxes, thr)
        out[f"matched@{thr}"] = s["matched"]
        out[f"type_ok@{thr}"] = s["type_ok"]
        out[f"missed_by_type@{thr}"] = s["missed_by_type"]
        out[f"recall@{thr}"] = round(s["recall"], 4)
        out[f"precision@{thr}"] = round(s["precision"], 4)
    return out


def overlay(img: Image.Image, page: pymupdf.Page, gt, boxes, path: Path) -> None:
    """GT in green, predictions colored by type, drawn on the image the detector saw."""
    im = img.copy()
    d = ImageDraw.Draw(im)
    scale = DPI / 72

    def px(rect):
        r = pymupdf.Rect(rect) * page.rotation_matrix  # unrotated points -> as rendered
        return [r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale]

    for g in gt or []:
        b = px(g.rect)
        d.rectangle([b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2], outline=(0, 170, 0), width=3)
    for kind, _, rect in boxes:
        d.rectangle(px(rect), outline=OVERLAY_COLORS[kind], width=2)
    im.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["dev", "heldout", "all"])
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--stamp", default=None, help="date stamp for the result files (default: today)")
    args = ap.parse_args()

    out = Path(args.out)
    stamp = args.stamp or dt.date.today().isoformat()
    overlays = out / "overlays" / stamp  # per run, so earlier overlays are kept
    overlays.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FORMS / "manifest.json").read_text())["forms"]
    forms = [f for f in manifest if args.split == "all" or f["split"] == args.split]

    dets = load_default_detectors()
    rows = []
    for form in forms:
        pdf_path = FORMS / form["split"] / f"{form['id']}.pdf"
        gt = score.load_gt(form, pdf_path)
        for pno, page, page_gt in iter_pages(form, pdf_path, gt):
            clean = render(page, DPI)
            variants = [("as_published", clean, None)]
            if form["kind"] != "scanned":
                scan = fake_scan(clean, seed=zlib.crc32(f"{form['id']}:{pno}".encode()))
                variants.append(("fake_scan", scan, scan))
            for variant, shown, given in variants:
                for name in DETECTORS:
                    boxes = detect(page, name, dets, given)
                    row = dict(form=form["id"], split=form["split"], kind=form["kind"], page=pno, variant=variant,
                               detector=name, gt=None if page_gt is None else len(page_gt), pred=len(boxes),
                               pred_by_type={t: sum(1 for b in boxes if b[0] == t) for t in TYPES})
                    if page_gt:
                        row.update(score_row(page_gt, boxes))
                    rows.append(row)
                    overlay(shown, page, page_gt, boxes, overlays / f"{form['id']}_p{pno}_{variant}_{name}.png")
                    print(json.dumps(row), flush=True)

    (out / f"baseline_detection_{stamp}.json").write_text(json.dumps(rows, indent=1))
    (out / f"baseline_detection_{stamp}.md").write_text(summary_markdown(rows, stamp))


def summary_markdown(rows, stamp) -> str:
    agg = {}
    for r in rows:
        k = (r["split"], r["form"], r["kind"], r["variant"], r["detector"])
        a = agg.setdefault(k, dict(pages=0, gt=0, pred=0, has_gt=r["gt"] is not None, m5=0, m3=0, t3=0, miss_choice=0, miss_text=0))
        a["pages"] += 1
        a["pred"] += r["pred"]
        if r["gt"] is not None:
            a["gt"] += r["gt"]
            if "matched@0.3" in r:
                a["m5"] += r["matched@0.5"]; a["m3"] += r["matched@0.3"]; a["t3"] += r["type_ok@0.3"]
                a["miss_choice"] += r["missed_by_type@0.3"]["choice"]; a["miss_text"] += r["missed_by_type@0.3"]["text"]
    lines = [f"# Baseline detection, {stamp}", "",
             "CommonForms detectors only, no agent. Recall/precision at IoU 0.3 (IoU 0.5 recall in parentheses). "
             "Forms without ground truth show detection counts only.", "",
             "| split | form | kind | variant | detector | pages | GT | detected | recall@.3 (.5) | precision@.3 | type ok | missed text / choice |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (split, form, kind, variant, det), a in agg.items():
        if a["has_gt"] and a["gt"]:
            rec = f"{a['m3']/a['gt']:.2f} ({a['m5']/a['gt']:.2f})"
            prec = f"{a['m3']/max(a['pred'],1):.2f}"
            tok = f"{a['t3']/max(a['m3'],1):.2f}"
            miss = f"{a['miss_text']} / {a['miss_choice']}"
            gt = a["gt"]
        else:
            rec = prec = tok = miss = "–"
            gt = "none yet"
        lines.append(f"| {split} | {form} | {kind} | {variant} | {det} | {a['pages']} | {gt} | {a['pred']} | {rec} | {prec} | {tok} | {miss} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
