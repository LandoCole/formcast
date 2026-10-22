"""Build the showcase page's data: the sample bundles and ``results.json``.

    python demo/tools/build_showcase.py samples [--forms fw9,fw4,i9]
    python demo/tools/build_showcase.py results

``samples`` writes ``demo/web/showcase/samples/<id>/`` for each form plus ``samples/index.json``.
A bundle is the folder the page's Detect step reads:

    manifest.json        id, form, title, revision, source_url, license, pages
    form.pdf             the form with its fields stripped (what the detector looks at)
    detections.json      {"model", "pages": {"<page>": [{kind, rect, conf}]}} from FFDNet-S, ahead of time
    original-fields.json {"pages": {"<page>": [rect]}} the form's own fields, for "found N of M"
    schema.json          the form's sample JSON Schema (forms/schemas/)
    record.json          a clearly fictional record for the schema (demo/tools/sample_records/)
    published.pdf        the form as published, fields kept
    agent-template.json  the AI agent's archived template for that file (run ``defer-placement``)
    agent-scores.json    that session's scores from the run's results json (AGENT_SCORE_KEYS only)

``results`` writes ``demo/web/showcase/results.json`` from the committed files in ``eval/results/``,
so every number on the page traces to a file there.

Both PDFs of a bundle are saved by PyMuPDF without encryption (pdf-lib cannot fill an encrypted
file), and the stripped one also drops the form's XFA data. The functions ``write_bundle`` and
``write_index`` are the one bundle writer; ``tests/web/make_sample_bundle.py`` calls them too.
Nothing here reads ``forms/heldout/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))  # run this checkout's formcast, not an installed copy
sys.path.insert(0, str(ROOT / "eval"))

import jsonschema  # noqa: E402
import pymupdf  # noqa: E402
from score import MIN_WIDGET_SIZE  # noqa: E402

SHOWCASE = ROOT / "demo" / "web" / "showcase"
SAMPLES = SHOWCASE / "samples"
RECORDS = ROOT / "demo" / "tools" / "sample_records"
RESULTS_DIR = ROOT / "eval" / "results"
RESULTS_JSON = SHOWCASE / "results.json"

LICENSE = "US federal government work (public domain)"
DEFAULT_FORMS = ("fw9", "fw4", "i9")
TITLES = {"fw9": "IRS W-9", "fw4": "IRS W-4", "i9": "USCIS I-9"}
DETECTOR = "FFDNet-S"
DETECTOR_NAME = "FFDNet-S-onnx"
DPI = 144  # the library's detection resolution (DetectPolicy.dpi)
MAX_PDF_BYTES = 2 * 1024 * 1024

# The agent templates and the headline row come from the latest dev run (plan 3 ruling B: its
# templates were re-scored and reproduce the committed figures exactly).
AGENT_RUN = "2026-10-21_defer-placement"
AGENT_RESULTS = RESULTS_DIR / f"agent_anthropic_eval_dev_{AGENT_RUN}.json"
AGENT_TEMPLATES = RESULTS_DIR / "templates.tar.gz"
AGENT_MODEL_LABEL = "Haiku 5.5"
# What a bundle keeps of the session's results row: what the page shows (detect.js scoresLine reads
# run, model_label, model, detection, mapping, fill and steps) and the session's cost. Local paths
# and harness fields stay out.
AGENT_SCORE_KEYS = (
    "run", "model_label", "form", "variant", "model", "detection", "mapping", "fill",
    "steps", "input_tokens", "output_tokens", "wall_s", "finished",
)  # fmt: skip

# ---------------------------------------------------------------- bundles


def form_entry(form_id: str) -> dict:
    """The ``forms/manifest.json`` entry for ``form_id``."""
    forms = json.loads((ROOT / "forms" / "manifest.json").read_text(encoding="utf-8"))["forms"]
    return next(f for f in forms if f["id"] == form_id)


def own_widgets(page: pymupdf.Page) -> list[pymupdf.Widget]:
    """The page's data fields: its widgets minus push buttons and anything under 3 pt, as
    ``eval/score.py`` takes ground truth from a fillable form."""
    return [
        w
        for w in page.widgets() or []
        if w.field_type != pymupdf.PDF_WIDGET_TYPE_BUTTON
        and w.rect.width >= MIN_WIDGET_SIZE
        and w.rect.height >= MIN_WIDGET_SIZE
    ]


def widget_kind(w: pymupdf.Widget) -> str:
    """The detector class of a widget: choice (check box, radio button), signature or text."""
    if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        return "choice"
    if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
        return "signature"
    return "text"


def own_fields(page: pymupdf.Page) -> list[list[float]]:
    """Rects (points, unrotated crop-box space) of the page's data fields."""
    return [[round(v, 2) for v in (w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1)] for w in own_widgets(page)]


def _save(doc: pymupdf.Document) -> bytes:
    # explicit: the default keeps the source's encryption, which pdf-lib cannot open
    return doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True, encryption=pymupdf.PDF_ENCRYPT_NONE)


def split_pdf(source: Path) -> dict[str, Any]:
    """Read ``source`` and make its two bundle PDFs.

    Returns ``{"published", "stripped", "originals", "pages", "was_encrypted"}``: both PDFs as bytes
    (saved without encryption), ``originals`` as ``{page: [rect]}`` for pages that have fields, the
    page count, and whether the source file was encrypted.
    """
    doc = pymupdf.open(source)
    try:
        # an empty user password opens the file transparently, so ask the trailer, not is_encrypted
        was_encrypted = doc.xref_get_key(-1, "Encrypt")[0] != "null"
        if doc.needs_pass:
            raise ValueError(f"{source.name} needs a password")
        originals = {page.number: rects for page in doc if (rects := own_fields(page))}
        published = _save(doc)
        for page in doc:
            for w in list(page.widgets() or []):
                page.delete_widget(w)
        acroform = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
        if acroform[0] == "xref" and doc.xref_get_key(int(acroform[1].split()[0]), "XFA")[0] != "null":
            doc.xref_set_key(int(acroform[1].split()[0]), "XFA", "null")  # the stripped form is flat
        stripped = _save(doc)
        return {
            "published": published,
            "stripped": stripped,
            "originals": originals,
            "pages": doc.page_count,
            "was_encrypted": was_encrypted,
        }
    finally:
        doc.close()


def detect_pages(stripped: bytes, pages: list[int]) -> dict[str, list[dict]]:
    """FFDNet-S (single-pass ONNX, as the browser runs it) on ``pages`` of the stripped PDF.

    Each page is rendered at the library's detection DPI and the detector's boxes are converted to
    PDF points by ``detect_page``, the conversion the browser makes. A policy that never merges
    keeps the detector's own output.
    """
    from formcast.pagemap.detect import DetectPolicy, FFDNetOnnxDetector, detect_page

    detector = FFDNetOnnxDetector(DETECTOR)
    policy = DetectPolicy(digital=(detector.name,), scanned=(detector.name,), merge_iou=1.01, dpi=DPI, deskew_scanned=False)
    doc = pymupdf.open(stream=stripped)
    try:
        out = {}
        for n in pages:
            cands = detect_page(doc[n], "digital", {detector.name: detector}, policy)
            out[str(n)] = [
                {"kind": c.kind, "rect": [round(v, 2) for v in c.rect], "conf": round(c.conf, 3)} for c in cands
            ]
        return out
    finally:
        doc.close()


def agent_row(form_id: str, results_path: Path = AGENT_RESULTS) -> dict:
    """The ``as_published`` session row of ``form_id`` in an agent results json."""
    results = json.loads(results_path.read_text(encoding="utf-8"))
    return next(r for r in results["rows"] if r["form"] == form_id and r["variant"] == "as_published")


def agent_scores(form_id: str, results_path: Path = AGENT_RESULTS) -> dict:
    """agent-scores.json: the session's row plus the run name and the model's label, cut to
    ``AGENT_SCORE_KEYS`` (in that order)."""
    meta = json.loads(results_path.read_text(encoding="utf-8"))["meta"]
    full = {"run": meta["run"], "model_label": AGENT_MODEL_LABEL, **agent_row(form_id, results_path)}
    return {k: full[k] for k in AGENT_SCORE_KEYS}


def archived_template(form_id: str, run: str = AGENT_RUN) -> bytes:
    """The archived ``as_published`` template of ``form_id`` for ``run``, read from the tar.gz."""
    member = f"eval/results/templates/{run}/anthropic_eval/{form_id}_as_published.json"
    with tarfile.open(AGENT_TEMPLATES, "r:gz") as tar:
        f = tar.extractfile(member)
        if f is None:
            raise FileNotFoundError(member)
        return f.read()


def write_json(path: Path, data: Any) -> None:
    """One JSON file the way every file in a bundle is written."""
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_bundle(
    out: Path,
    *,
    bundle_id: str,
    form_id: str,
    title: str,
    pdfs: dict[str, Any],
    detections: dict[str, list[dict]],
    schema: dict,
    record: dict,
    template: bytes,
    scores: dict,
    model: str = DETECTOR_NAME,
) -> dict:
    """Write one sample bundle into ``out`` and return its index entry.

    ``pdfs`` is the result of ``split_pdf``; ``template`` is the agent template's file content.
    Raises ``jsonschema.ValidationError`` when ``record`` does not fit ``schema``, and ``ValueError``
    when a PDF is over 2 MB.
    """
    jsonschema.validate(record, schema)
    for name in ("published", "stripped"):
        if len(pdfs[name]) > MAX_PDF_BYTES:
            raise ValueError(f"{form_id} {name} PDF is {len(pdfs[name])} bytes, over the {MAX_PDF_BYTES} limit")
    entry = form_entry(form_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "published.pdf").write_bytes(pdfs["published"])
    (out / "form.pdf").write_bytes(pdfs["stripped"])
    write_json(out / "manifest.json", {
        "id": bundle_id,
        "form": form_id,
        "title": title,
        "revision": entry["revision"],
        "source_url": entry["source"],
        "license": LICENSE,
        "pages": pdfs["pages"],
    })
    write_json(out / "detections.json", {"model": model, "pages": detections})
    write_json(out / "original-fields.json", {"pages": {str(n): rects for n, rects in pdfs["originals"].items()}})
    write_json(out / "schema.json", schema)
    write_json(out / "record.json", record)
    (out / "agent-template.json").write_bytes(template)
    write_json(out / "agent-scores.json", scores)
    return {
        "id": bundle_id,
        "title": title,
        "revision": entry["revision"],
        "source_url": entry["source"],
        "pages": pdfs["pages"],
    }


def write_index(path: Path, entries: list[dict]) -> None:
    """samples/index.json: the bundles in picker order."""
    write_json(path, entries)


def build_samples(form_ids: tuple[str, ...] = DEFAULT_FORMS, out: Path = SAMPLES) -> list[dict]:
    """Build a bundle per form into ``out/<form>/`` and the index; returns the index entries."""
    entries = []
    for form_id in form_ids:
        entry = form_entry(form_id)
        if entry["split"] != "dev":
            raise ValueError(f"{form_id} is not a dev form; samples come from the dev corpus")
        pdfs = split_pdf(ROOT / "forms" / entry["split"] / f"{form_id}.pdf")
        detections = detect_pages(pdfs["stripped"], sorted(pdfs["originals"]))
        written = write_bundle(
            out / form_id,
            bundle_id=form_id,
            form_id=form_id,
            title=TITLES[form_id],
            pdfs=pdfs,
            detections=detections,
            schema=json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text(encoding="utf-8")),
            record=json.loads((RECORDS / f"{form_id}.json").read_text(encoding="utf-8")),
            template=archived_template(form_id),
            scores=agent_scores(form_id),
        )
        entries.append(written)
        boxes = sum(len(v) for v in detections.values())
        own = sum(len(v) for v in pdfs["originals"].values())
        print(
            f"{form_id}: {pdfs['pages']} pages, {boxes} detected boxes, {own} own fields, "
            f"source encrypted: {pdfs['was_encrypted']}, form.pdf {len(pdfs['stripped']):,} B, "
            f"published.pdf {len(pdfs['published']):,} B"
        )
    write_index(out / "index.json", entries)
    return entries


# ---------------------------------------------------------------- results.json

BASELINE_DEV = "baseline_detection_2026-10-21.json"
BASELINE_HELDOUT = "baseline_detection_2026-10-21_heldout.json"
BASELINE_ONNX = "baseline_detection_2026-10-21_onnx.json"

# (results file, label, detail, headline) in table order: the seed floor, the current code, the
# best earlier mapping, Luna 6. A run with sessions that ended in an error gets "; N of M sessions"
# appended to its detail, counted from the file.
AGENT_RUNS = (
    ("agent_seed_dev_2026-10-21_seed-fill.json", "Seeded template", "no agent", False),
    (AGENT_RESULTS.name, AGENT_MODEL_LABEL, "current code", True),
    ("agent_anthropic_eval_dev_2026-10-21_round3-gen.json", AGENT_MODEL_LABEL, "round3-gen, earlier code, best mapping so far", False),
    ("agent_openai_eval_dev_2026-10-21_round1.json", "Luna 6", "round 1, earlier code", False),
    ("agent_openai_eval_dev_2026-10-21_round2.json", "Luna 6", "round 2, earlier code", False),
)  # fmt: skip
# Runs cut short, from main spec 14.3: why, and which earlier run is re-scored on the same sessions
# (the fair comparison). Counts are not stated here; they come from the files.
PARTIAL_RUNS = {
    "agent_openai_eval_dev_2026-10-21_round2.json": {
        "name": "Luna 6, round 2",
        "reason": "the OpenAI credits ran out",
        "compare": ("agent_openai_eval_dev_2026-10-21_round1.json", "Luna 6", "round 1"),
    },
}
GROUPS = ("fillable", "digital", "scanned", "all")

# The words the page shows next to the numbers (main spec 14.3, "How to read the tables").
GROUP_TEXT = {
    "fillable": {
        "label": "Fillable",
        "text": (
            "Fillable forms as published. The template starts from the form's own fields, and those "
            "fields are the answer key, so the seeded template finds every box by construction."
        ),
    },
    "digital": {"label": "Flat digital", "text": "Flat digital forms as published: printed lines and boxes, no form fields."},
    "scanned": {
        "label": "Scanned",
        "text": (
            "The fake-scan variants of the same forms: each page turned into a degraded image with no "
            "text layer and no form fields."
        ),
    },
    "all": {"label": "All sessions", "text": "Every session of the run, each form as published and as a fake scan."},
}  # fmt: skip
DETECTION_LABELS = {
    ("fillable", "as_published"): "Fillable, as published",
    ("fillable", "fake_scan"): "Fillable, fake scan",
    ("flat", "as_published"): "Flat digital, as published",
    ("flat", "fake_scan"): "Flat digital, fake scan",
    ("scanned", "as_published"): "Native scan",
}
DETECTOR_TEXT = {
    "FFDNet-L": {
        "label": "FFDNet-L, as the library runs it",
        "text": "PyTorch, with test-time augmentation. The library's detector for digital pages.",
    },
    "FFDetr": {"label": "FFDetr", "text": "The library's detector for scanned pages."},
    "FFDNet-L-onnx": {"label": "FFDNet-L in the browser", "text": "The published ONNX export, one pass."},
    "FFDNet-S-onnx": {
        "label": "FFDNet-S in the browser",
        "text": "The published ONNX export, one pass. This page's default.",
    },
}  # fmt: skip
METRIC_TEXT = {
    "recall_50": {
        "label": "Recall @ .5",
        "text": (
            "Share of the answer key's boxes that a proposed box (a template's or a detector's) covers "
            "closely: intersection over union of at least 0.5."
        ),
    },
    "recall_30": {"label": "Recall @ .3", "text": "The same, with a looser overlap of at least 0.3."},
    "precision_30": {
        "label": "Precision @ .3",
        "text": "Share of the proposed boxes that sit on a real box (overlap of at least 0.3).",
    },
    "path": {
        "label": "Path",
        "text": "Share of the boxes that take data whose template box is linked to the right schema path.",
    },
    "joint": {
        "label": "Joint",
        "text": "The path and its transforms both right (a date format, a slice of a string, a tick for true).",
    },
    "fill_acc": {
        "label": "Fill",
        "text": (
            "The template fills test records into the PDF, and each box is checked for the right value. "
            "Table cells count once per row and record, so tables weigh more here than in path."
        ),
    },
    "steps": {"label": "Steps", "text": "Agent steps per session, averaged."},
    "input_tokens": {"label": "Input tokens", "text": "Model input tokens per session, averaged."},
}  # fmt: skip
NOTES = {
    "averaging": (
        "Figures are micro-averaged over the answer key's boxes: every box counts once, so long forms "
        "weigh more than short ones."
    ),
    "errored_sessions": (
        "Agent-run rows leave out sessions that ended in an error, as the results files' own totals do."
    ),
    "seed": (
        "The seeded template is where every agent session starts: one box per form field and per "
        "detector box, linked to nothing. It scores zero on path, joint and fill by construction."
    ),
    "heldout_scan": "On the held-out split, scanned also includes one native scan: a paper form scanned to PDF.",
    "bare_detector": "Bare detectors: the model alone, before any seeding or agent.",
    "browser_models": (
        "Dev forms only. The page runs the published ONNX exports of FFDNet-S and FFDNet-L in one pass; "
        "the library runs FFDNet-L in PyTorch with test-time augmentation."
    ),
}  # fmt: skip

# main spec 14.3, "Read this first": 1 of 9 Haiku sessions finished; stated, not recomputed, because
# the held-out agent results are not read for anything but this status.
HELDOUT_STATUS = {
    "source": "docs/superpowers/specs/2026-10-20-form-projection-design.md, section 14.3",
    "sessions_planned": 9,
    "sessions_completed": 1,
    "luna_heldout": False,
    "note": (
        "The Anthropic account hit its API usage limit during the one held-out Haiku run. "
        "Of 9 held-out sessions, one finished (ics211, as published). Three were cut off mid-session, "
        "five never made a call, and one retry hit the same limit. There is no Luna held-out run "
        "(the OpenAI credits were already used up). No code or prompt changed after the dev rounds. "
        "The held-out agent numbers are one session, which says nothing about generalization."
    ),
}
CAVEATS = [
    "Dev results were tuned on over several rounds.",
    "Haiku varies about ±0.1 path per form between runs.",
]


def _ratio(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _load(name: str) -> Any:
    return json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))


def detection_totals(rows: list[dict]) -> list[dict]:
    """Micro-averaged bare-detector figures per form kind, variant and detector (rows with ground truth)."""
    groups: dict[tuple[str, str, str], dict[str, int]] = {}
    for r in rows:
        if not r.get("gt") or "matched@0.3" not in r:
            continue
        g = groups.setdefault((r["kind"], r["variant"], r["detector"]), dict.fromkeys(("gt", "pred", "m5", "m3"), 0))
        g["gt"] += r["gt"]
        g["pred"] += r["pred"]
        g["m5"] += r["matched@0.5"]
        g["m3"] += r["matched@0.3"]
    return [
        {
            "kind": kind,
            "variant": variant,
            "label": DETECTION_LABELS[(kind, variant)],
            "detector": detector,
            "recall_50": _ratio(g["m5"], g["gt"]),
            "recall_30": _ratio(g["m3"], g["gt"]),
            "precision_30": _ratio(g["m3"], g["pred"]),
            "gt": g["gt"],
        }
        for (kind, variant, detector), g in sorted(groups.items())
    ]


def _in_group(row: dict, group: str) -> bool:
    if group == "all":
        return True
    if group == "scanned":
        return row["variant"] == "fake_scan" or row["kind"] == "scanned"
    return row["variant"] == "as_published" and row["kind"] == ("fillable" if group == "fillable" else "flat")


def _completed(rows: list[dict]) -> list[dict]:
    """The sessions that finished: scored, and not ended in an error."""
    return [r for r in rows if r.get("detection") and r.get("mapping") and not r.get("error")]


def agent_group_rows(
    name: str,
    label: str,
    detail: str,
    *,
    headline: bool = False,
    only: set[tuple[str, str]] | None = None,
    sessions_of: str | None = None,
) -> list[dict]:
    """The figures of one agent results file per group (fillable, digital, scanned, all).

    Sessions that ended in an error are left out, as the results file's own totals do; ``only``
    keeps just those (form, variant) sessions, and ``sessions_of`` names the run they come from.
    Detection, path, joint and fill are micro-averaged over ground-truth fields; steps and input
    tokens are per-session means.
    """
    data = _load(name)
    run = data["meta"]["run"]
    model = data["meta"]["model"]
    if model.startswith("seed-only"):
        model = "none"
    ok = _completed(data["rows"])
    if only is not None:
        ok = [r for r in ok if (r["form"], r["variant"]) in only]
    out = []
    for group in GROUPS:
        rs = [r for r in ok if _in_group(r, group)]
        det5 = [r["detection"]["iou0.5"] for r in rs]
        det3 = [r["detection"]["iou0.3"] for r in rs]
        mapping = [r["mapping"] for r in rs]
        fills = [r["fill"] for r in rs if r.get("fill")]
        n_path = sum(m["n_path"] for m in mapping)
        out.append({
            "run": run,
            "model": model,
            "label": label,
            "detail": detail,
            "headline": headline,
            "sessions_of": sessions_of,
            "group": group,
            "sessions": len(rs),
            "recall_50": _ratio(sum(d["matched"] for d in det5), sum(d["gt"] for d in det5)),
            "precision_30": _ratio(sum(d.get("pred_matched", d["matched"]) for d in det3), sum(d["pred"] for d in det3)),
            "path": _ratio(sum(m["path_ok"] for m in mapping), n_path),
            "joint": _ratio(sum(m["joint_ok"] for m in mapping), n_path),
            "fill_acc": _ratio(sum(f["correct"] for f in fills), sum(f["units"] for f in fills)) if fills else None,
            "steps": round(sum(r.get("steps", 0) for r in rs) / len(rs), 1) if rs else 0,
            "input_tokens": round(sum(r.get("input_tokens", 0) for r in rs) / len(rs)) if rs else 0,
        })
    return out


def agent_rows_and_notes() -> tuple[list[dict], list[str], list[str]]:
    """The agent_dev rows, the notes on partial runs, and the result files read.

    A partial run's detail gets its finished and total session counts, and the earlier run it is
    compared with is added right after it, re-scored on the same finished sessions.
    """
    rows: list[dict] = []
    notes: list[str] = []
    files: list[str] = []
    for name, label, detail, headline in AGENT_RUNS:
        files.append(name)
        sessions = _load(name)["rows"]
        done = _completed(sessions)
        partial = PARTIAL_RUNS.get(name) if len(done) < len(sessions) else None
        if partial is None:
            rows += agent_group_rows(name, label, detail, headline=headline)
            continue
        n, total = len(done), len(sessions)
        rows += agent_group_rows(name, label, f"{detail}; {n} of {total} sessions", headline=headline)
        notes.append(
            f"{partial['name']} is partial: {partial['reason']} after {n} of {total} sessions, and the "
            f"{total - n} that failed are left out. Compare it with {partial['compare'][2]} on the same {n} sessions."
        )
        other, other_label, other_round = partial["compare"]
        rows += agent_group_rows(
            other,
            other_label,
            f"{other_round} on the same {n} sessions",
            only={(r["form"], r["variant"]) for r in done},
            sessions_of=_load(name)["meta"]["run"],
        )
        if other not in files:
            files.append(other)
    return rows, notes, files


def build_results() -> dict:
    """The contents of results.json."""
    onnx = [
        {k: row[k] for k in ("kind", "variant", "label", "detector", "recall_50", "recall_30", "precision_30")}
        for row in detection_totals(_load(BASELINE_ONNX))
    ]
    agent_dev, partial_notes, agent_files = agent_rows_and_notes()
    sources = [BASELINE_DEV, BASELINE_HELDOUT, BASELINE_ONNX, *agent_files]
    return {
        "generated_from": [f"eval/results/{name}" for name in sources],
        "detection": {
            "dev": detection_totals(_load(BASELINE_DEV)),
            "heldout": detection_totals(_load(BASELINE_HELDOUT)),
        },
        "browser_models": onnx,
        "agent_dev": agent_dev,
        "heldout_status": dict(HELDOUT_STATUS),
        "caveats": list(CAVEATS),
        "groups": GROUP_TEXT,
        "detectors": DETECTOR_TEXT,
        "metrics": METRIC_TEXT,
        "notes": {**NOTES, "partial_runs": partial_notes},
    }


def build_results_file(path: Path = RESULTS_JSON) -> dict:
    """Write results.json to ``path`` and return its contents."""
    results = build_results()
    write_json(path, results)
    return results


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    samples = sub.add_parser("samples", help="build the sample bundles (runs FFDNet-S; downloads it once)")
    samples.add_argument("--forms", default=",".join(DEFAULT_FORMS), help="comma-separated dev form ids")
    sub.add_parser("results", help="write results.json from eval/results/")
    args = ap.parse_args(argv)
    if args.command == "samples":
        ids = tuple(i.strip() for i in args.forms.split(",") if i.strip())
        missing = [i for i in ids if i not in TITLES]
        if missing:
            ap.error(f"no sample title for {missing}; add it to TITLES (and a record in demo/tools/sample_records/)")
        build_samples(ids)
    else:
        results = build_results_file()
        print(f"wrote {RESULTS_JSON.relative_to(ROOT)}: {len(results['agent_dev'])} agent rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
