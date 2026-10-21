"""Baseline: CommonForms detectors alone (no agent) on the test corpus.

For fillable forms, the published widgets are ground truth and are stripped before rendering.
For flat/scanned forms, ground truth comes from forms/gt/<id>.json when it exists; otherwise
only detection counts and overlays are recorded (for review by eye).

Usage:
    python eval/baseline_detection.py [--split dev|heldout|all] [--out eval/results]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import random
import zlib
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "forms"
TYPES = ("text", "choice", "signature")  # CommonForms classes 0, 1, 2
DPI = 144  # what commonforms renders at
IOU_THRESHOLDS = (0.5, 0.3)
CONFIDENCE = 0.3


# ---------- ground truth ----------

def widget_type(w) -> int:
    if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        return 1
    if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
        return 2
    return 0


def page_ground_truth(page, form_id: str, pno: int):
    """Normalized (type, [x0,y0,x1,y1]) boxes, or None when no ground truth exists."""
    W, H = page.rect.width, page.rect.height
    widgets = [w for w in page.widgets() if w.rect.width >= 3 and w.rect.height >= 3]
    if widgets:
        return [(widget_type(w), [w.rect.x0 / W, w.rect.y0 / H, w.rect.x1 / W, w.rect.y1 / H]) for w in widgets]
    gt_file = FORMS / "gt" / f"{form_id}.json"
    if gt_file.exists():
        boxes = [b for b in json.loads(gt_file.read_text())["fields"] if b["page"] == pno]
        return [(TYPES.index(b["type"]), [b["rect"][0] / W, b["rect"][1] / H, b["rect"][2] / W, b["rect"][3] / H]) for b in boxes]
    return None


# ---------- page selection and rendering ----------

def iter_pages(form: dict):
    path = FORMS / form["split"] / f"{form['id']}.pdf"
    doc = pymupdf.open(path)
    fillable = form["kind"] == "fillable"
    for pno, page in enumerate(doc):
        if form["id"] == "cms1500" and pno != 0:
            continue  # only page 0 is the blank form
        gt = page_ground_truth(page, form["id"], pno)
        if fillable and not gt:
            continue  # instruction pages of fillable forms
        for w in list(page.widgets()):
            page.delete_widget(w)
        img = Image.open(io.BytesIO(page.get_pixmap(dpi=DPI).tobytes("png"))).convert("RGB")
        yield pno, gt, img


def fake_scan(img: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    g = img.convert("L").rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, fillcolor=255)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    a = np.asarray(g).astype(np.int16) + nrng.normal(0, 12, (g.height, g.width)).astype(np.int16)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB")


# ---------- detectors ----------

class Detectors:
    def __init__(self):
        from commonforms.inference import FFDNetDetector, FFDetrDetector

        self.ffdnet = FFDNetDetector("FFDNet-L")
        self.ffdetr = FFDetrDetector("FFDetr")

    def run(self, name: str, img: Image.Image):
        from commonforms.utils import Page

        if name == "FFDNet-L":
            res = self.ffdnet.extract_widgets([Page(image=img, width=img.width, height=img.height)],
                                              confidence=CONFIDENCE, image_size=1216)[0]
            cls = {"TextBox": 0, "ChoiceButton": 1, "Signature": 2}
            return [(cls[w.widget_type], [w.bounding_box.x0, w.bounding_box.y0, w.bounding_box.x1, w.bounding_box.y1]) for w in res]
        # FFDetr: called directly; commonforms' single-page path crashes (see design §7.1)
        d = self.ffdetr.model.predict(img, threshold=CONFIDENCE)
        d = (d[0] if isinstance(d, list) else d).with_nms(threshold=0.1, class_agnostic=True)
        W, H = img.size
        return [(int(c), [b[0] / W, b[1] / H, b[2] / W, b[3] / H]) for c, b in zip(d.class_id, d.xyxy)]


# ---------- scoring ----------

def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(gt, pred, thr):
    pairs = sorted(((iou(g[1], p[1]), gi, pi) for gi, g in enumerate(gt) for pi, p in enumerate(pred)), reverse=True)
    used_g, used_p, matched = set(), set(), []
    for v, gi, pi in pairs:
        if v < thr:
            break
        if gi not in used_g and pi not in used_p:
            used_g.add(gi); used_p.add(pi); matched.append((gi, pi))
    missed = [gt[i] for i in range(len(gt)) if i not in used_g]
    return matched, missed


def score(gt, pred):
    out = {}
    for thr in IOU_THRESHOLDS:
        matched, missed = match(gt, pred, thr)
        out[f"matched@{thr}"] = len(matched)
        out[f"type_ok@{thr}"] = sum(gt[g][0] == pred[p][0] for g, p in matched)
        out[f"missed_by_type@{thr}"] = {t: sum(1 for c, _ in missed if c == i) for i, t in enumerate(TYPES)}
    return out


def overlay(img, gt, pred, path):
    im = img.copy()
    d = ImageDraw.Draw(im)
    W, H = im.size
    for _, b in gt or []:
        d.rectangle([b[0] * W - 2, b[1] * H - 2, b[2] * W + 2, b[3] * H + 2], outline=(0, 170, 0), width=3)
    colors = {0: (220, 0, 0), 1: (0, 90, 230), 2: (200, 0, 200)}
    for c, b in pred:
        d.rectangle([b[0] * W, b[1] * H, b[2] * W, b[3] * H], outline=colors[c], width=2)
    im.save(path)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["dev", "heldout", "all"])
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args()

    out = Path(args.out)
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FORMS / "manifest.json").read_text())["forms"]
    forms = [f for f in manifest if args.split == "all" or f["split"] == args.split]

    dets = Detectors()
    rows = []
    for form in forms:
        for pno, gt, img in iter_pages(form):
            variants = [("as_published", img)]
            if form["kind"] != "scanned":
                variants.append(("fake_scan", fake_scan(img, seed=zlib.crc32(f"{form['id']}:{pno}".encode()))))
            for variant, vimg in variants:
                for det in ("FFDNet-L", "FFDetr"):
                    pred = dets.run(det, vimg)
                    row = dict(form=form["id"], split=form["split"], kind=form["kind"], page=pno, variant=variant,
                               detector=det, gt=None if gt is None else len(gt), pred=len(pred),
                               pred_by_type={t: sum(1 for c, _ in pred if c == i) for i, t in enumerate(TYPES)})
                    if gt:
                        row.update(score(gt, pred))
                    rows.append(row)
                    overlay(vimg, gt, pred, out / "overlays" / f"{form['id']}_p{pno}_{variant}_{det}.png")
                    print(json.dumps(row), flush=True)

    stamp = dt.date.today().isoformat()
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
            a["gt"] += r["gt"]; a["m5"] += r["matched@0.5"]; a["m3"] += r["matched@0.3"]; a["t3"] += r["type_ok@0.3"]
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
