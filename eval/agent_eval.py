"""Agent eval: ``build_template`` on corpus forms, scored against ground truth.

For each form, variant and model the harness builds a template with the agent
and scores the final template:

* detection: the template's boxes against GT (``score.score_detection``) at
  IoU 0.3 and 0.5, tables expanded to one box per cell, next to the baseline
  detector's numbers for the same form and variant;
* mapping: ``score.score_mapping`` (path, transform with equivalence groups,
  joint path+transform, exact path set);
* steps, tokens, wall time, stop reason and the unplaceable count.

Inputs:

* ``as_published``: the PDF as published. Fillable forms keep their widgets,
  so the template starts with one field per widget and the agent maps them
  (the real use case). Flat and scanned forms are used as they are.
* ``fake_scan`` (not for scanned forms): an image-only PDF built from the
  same seeded fake-scan renders the baseline uses (widgets stripped, 144 dpi,
  ``baseline_detection.fake_scan``), so the agent goes down the scanned-page
  path: deskew, the scanned-page detector and OCR. On fillable forms this is a
  detection + mapping test.

Scored pages are the manifest's ``scored_pages`` that have at least one GT
field, as in the baseline. GT always comes from the original PDF.

Baseline detector compared against: FFDNet-L (the default detector for
digital pages) for ``as_published`` digital and fillable forms, FFDetr (the
default for scanned pages) for ``fake_scan`` variants and scanned forms. The
numbers are the bare-detector rows of ``baseline_detection_<stamp>.json``.

Outputs (``<run>`` is ``<stamp>`` or ``<stamp>_<tag>``):

* ``eval/results/agent_<modelenv>_<split>_<run>.{json,md}``: per-form rows and
  micro-averaged split totals (committed);
* ``eval/results/templates/<run>/<modelenv>/<form>_<variant>.json`` (git-ignored; the
  committed runs' templates are archived in ``eval/results/templates.tar.gz``, extract
  with ``tar -xzf eval/results/templates.tar.gz`` from the repo root; step logs are local-only);
* ``eval/results/logs/<run>/...``: per-session step logs (model text, code,
  step reports as the model saw them), form maps and fake-scan inputs;
* ``eval/results/overlays/agent/<run>/<modelenv>/<form>_<variant>_p<n>.png``:
  previews with GT drawn in green.

Secrets: only ``<PREFIX>_*`` lines of ``.env`` for the requested ``*_EVAL``
prefixes are loaded; key values are never printed and are scrubbed from every
file written.

Usage:
    python eval/agent_eval.py --split dev --model-env ANTHROPIC_EVAL,OPENAI_EVAL
        [--forms id,...] [--variant as_published|fake_scan|both] [--jobs 3]
        [--stamp YYYY-MM-DD] [--tag round1] [--max-steps N]

No-model modes (no API calls):
    python eval/agent_eval.py --split dev|heldout --seed-only [--forms ...] [--variant ...] [--tag ...]
        scores the untouched seeded template (``new_template(..., candidates=True)``: one field per
        widget and per detector candidate, before any agent step) per form/variant, written to
        ``agent_seed_<split>_<run>.{json,md}``; it is the floor the agent's edits are measured against.
    python eval/agent_eval.py --rescore eval/results/agent_<...>.json [--baseline-stamp STAMP]
        re-scores the saved templates of a results file with the current scorer and rewrites its
        json/md in place (baseline columns are recomputed from ``baseline_detection_<STAMP>.json``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import multiprocessing
import os
import re
import subprocess
import sys
import time
import traceback
import zlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))  # do not rely on the editable install
sys.path.insert(0, str(HERE))

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import baseline_detection  # noqa: E402
import score  # noqa: E402
from formcast.llm import LLMConfig, LLMResponse, Message, ToolSpec, make_provider  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.pagemap.ocr import render  # noqa: E402

FORMS = ROOT / "forms"
RESULTS = ROOT / "eval" / "results"
# The saved templates are committed as one archive (the directory itself is git-ignored).
TEMPLATES_ARCHIVE = RESULTS / "templates.tar.gz"
ENV_PATH = ROOT / ".env"
DPI = baseline_detection.DPI
IOUS = (0.3, 0.5)
IOU_KEYS = tuple(f"iou{t}" for t in IOUS)
VARIANTS = ("as_published", "fake_scan")
PREVIEW_DPI = 110
GT_COLOR = (0, 170, 0)
DEFAULT_BASELINE_STAMP = "2026-10-21"

# The agent sees the form map's source name in its summary; in the eval every input gets the
# same neutral name so the file name (a form id) cannot cue prior knowledge of a public form.
ANON_SOURCE_NAME = "form.pdf"


NOTES = [
    "Detection is scored on the manifest's scored pages that have at least one GT field (the baseline's pages); "
    "tables count one box per cell.",
    "Multi-line answer areas are representation-neutral: a plain template text field with style.multiline or "
    "at least 1.8x the page's median GT text-box height also covers every unmatched GT text box at least 70% "
    "inside it (recall), counts once for precision, and gets path credit through the GT line box that carries "
    "the path. It only does so when at least two such GT boxes are stacked in one column inside it (horizontal "
    "overlap of 50% or more); a single loose box is scored by the normal IoU threshold.",
    "Fillable forms as published: the template starts with one field per PDF widget (the real use case is "
    "mapping existing fields), and GT is the widgets, so agent-added boxes on blanks without a widget count as "
    "false positives.",
    "fake_scan: an image-only PDF of the baseline's seeded fake-scan renders (widgets stripped), so the agent "
    "faces the scanned-page path (deskew, FFDetr, OCR); on fillable forms it is a detection + mapping test.",
    "Baseline: bare-detector numbers from the baseline run on the same images: FFDNet-L (default for digital "
    "pages) for as_published digital/fillable forms, FFDetr (default for scanned pages) for fake_scan and "
    "scanned forms. On fillable as_published forms the baseline detects on the widget-stripped page.",
    "Mapping is over GT fields with a schema path. Transform credit uses equivalence groups {slice, split, "
    "char_at}, {date_part, date_format}, {equals, contains}, and a when equals true also credits bool_to_check; "
    "a template's join and when count as ops. joint = "
    "path and transforms right; exact = the matched field's source paths equal GT path + also_paths.",
    "Totals are micro-averaged over forms (sums of counts). Sessions that ended in an error (infrastructure or "
    "harness) are listed in the per-form table but excluded from the totals' detection and mapping figures; "
    "their templates are partial or untouched seeds.",
    f"The agent's form map carries the neutral source name {ANON_SOURCE_NAME!r}, not the file name.",
]


# ---------- secrets ----------


def load_eval_env(prefixes: Iterable[str], path: Path = ENV_PATH) -> list[str]:
    """Copy ``<PREFIX>_*`` lines of ``path`` into os.environ (existing values win); returns the names.

    Only ``*_EVAL`` prefixes are allowed, so nothing but the eval configs is
    ever read from ``.env``. Values are never printed.

    Raises:
        ValueError: for a prefix that does not end in ``_EVAL``.
    """
    prefixes = list(prefixes)
    bad = [p for p in prefixes if not re.fullmatch(r"[A-Z0-9_]+_EVAL", p)]
    if bad:
        raise ValueError(f"model env prefixes must end in _EVAL, got {bad}")
    wanted = tuple(f"{p}_" for p in prefixes)
    loaded: list[str] = []
    if not path.is_file():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if not key.startswith(wanted):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
        loaded.append(key)
    return loaded


class Redactor:
    """Replaces every secret value with ``***`` in strings, recursively through dicts and lists."""

    def __init__(self, secrets: Iterable[str]):
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def scrub(self, value):
        if isinstance(value, str):
            for s in self.secrets:
                value = value.replace(s, "***")
            return value
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(v) for v in value]
        return value


def _redactor_for(prefixes: Iterable[str]) -> Redactor:
    return Redactor(os.environ.get(f"{p}_API_KEY", "") for p in prefixes)


def _write(path: Path, text: str, redactor: Redactor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redactor.scrub(text))


def _write_json(path: Path, data, redactor: Redactor) -> None:
    _write(path, json.dumps(redactor.scrub(data), indent=1, default=str), redactor)


# ---------- inputs ----------


def fake_scan_pdf(form_id: str, pdf_path) -> bytes:
    """Image-only PDF of every page: widgets stripped, 144 dpi render, the baseline's seeded fake scan.

    Pages keep their size; the image (grayscale PNG, lossless) covers the page.

    Raises:
        ValueError: for a rotated page (not in the corpus; GT would not line up).
    """
    src = open_pdf(pdf_path)
    out = pymupdf.open()
    try:
        for page in src:
            if page.rotation:
                raise ValueError(f"{form_id} page {page.number} is rotated; fake scans assume rotation 0")
            for w in list(page.widgets() or []):
                page.delete_widget(w)
            seed = zlib.crc32(f"{form_id}:{page.number}".encode())
            image = baseline_detection.fake_scan(render(page, DPI), seed=seed)
            buf = io.BytesIO()
            image.convert("L").save(buf, format="PNG")
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=buf.getvalue())
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()
        src.close()


# ---------- scoring ----------


def detection_scores(gt: list[score.GTField], template) -> dict:
    """``score_detection`` of the template's boxes at each IoU, on the pages that have GT."""
    pages = {g.page for g in gt}
    out = {}
    for thr, key in zip(IOUS, IOU_KEYS):
        s = score.score_template_detection(gt, template, thr, pages=pages)
        out[key] = {**s, **{k: round(s[k], 4) for k in ("recall", "precision", "type_acc")}}
    return out


def baseline_detector(kind: str, variant: str) -> str:
    """The detector a form/variant is compared with: FFDetr for scans, FFDNet-L otherwise."""
    return "FFDetr" if variant == "fake_scan" or kind == "scanned" else "FFDNet-L"


def _det_totals(parts: list[dict]) -> dict:
    gt = sum(p["gt"] for p in parts)
    pred = sum(p["pred"] for p in parts)
    matched = sum(p["matched"] for p in parts)
    # one multi-line field can match several GT boxes; baseline rows are one to one
    pred_matched = sum(p.get("pred_matched", p["matched"]) for p in parts)
    type_ok = sum(p["type_ok"] for p in parts)
    return {
        "gt": gt, "pred": pred, "matched": matched, "pred_matched": pred_matched, "type_ok": type_ok,
        "recall": round(score._ratio(matched, gt), 4),
        "precision": round(score._ratio(pred_matched, pred), 4),
        "type_acc": round(score._ratio(type_ok, matched), 4),
    }  # fmt: skip


def baseline_scores(rows: list[dict], form_id: str, variant: str, detector: str) -> dict | None:
    """Baseline detection for one form/variant/detector, summed over its scored pages; None without rows."""
    mine = [
        r for r in rows
        if r["form"] == form_id and r["variant"] == variant and r["detector"] == detector and "matched@0.3" in r
    ]  # fmt: skip
    if not mine:
        return None
    out: dict = {"detector": detector, "pages": sorted(r["page"] for r in mine)}
    for thr, key in zip(IOUS, IOU_KEYS):
        out[key] = _det_totals(
            [{"gt": r["gt"], "pred": r["pred"], "matched": r[f"matched@{thr}"], "type_ok": r[f"type_ok@{thr}"]}
             for r in mine]
        )  # fmt: skip
    return out


_MAPPING_COUNTS = ("n_path", "n_transform", "matched", "path_ok", "transform_ok", "joint_ok", "exact_ok")


def _mapping_totals(parts: list[dict]) -> dict:
    c = {k: sum(p[k] for p in parts) for k in _MAPPING_COUNTS}
    r = score._ratio
    return {
        **c,
        "path_acc": round(r(c["path_ok"], c["n_path"]), 4),
        "transform_acc": round(r(c["transform_ok"], c["n_transform"]), 4),
        "joint_acc": round(r(c["joint_ok"], c["n_path"]), 4),
        "exact_path_set_acc": round(r(c["exact_ok"], c["n_path"]), 4),
    }


def totals(rows: list[dict]) -> dict:
    """Micro-averaged totals per variant and over all rows ("all").

    Rows without scores (a harness error or no GT) and rows whose session ended
    in an error (``error`` set: cut off by the provider, partial template) count as
    ``failed`` and are left out of the detection, baseline and mapping figures;
    ``errored`` lists them as "form/variant". ``baseline`` sums the scored rows that have baseline numbers (None when none do).
    """
    groups = {"all": rows}
    for v in VARIANTS:
        if any(r["variant"] == v for r in rows):
            groups[v] = [r for r in rows if r["variant"] == v]
    out = {}
    for name, rs in groups.items():
        ok = [r for r in rs if r.get("detection") and r.get("mapping") and not r.get("error")]
        based = [r for r in ok if r.get("baseline")]
        out[name] = {
            "forms": len(ok),
            "failed": len(rs) - len(ok),
            "errored": [f"{r.get('form')}/{r['variant']}" for r in rs if r.get("error")],
            "finished": sum(bool(r.get("finished")) for r in rs),
            "detection": {k: _det_totals([r["detection"][k] for r in ok]) for k in IOU_KEYS},
            "baseline": (
                {"forms": len(based), **{k: _det_totals([r["baseline"][k] for r in based]) for k in IOU_KEYS}}
                if based else None
            ),
            "mapping": _mapping_totals([r["mapping"] for r in ok]),
            "steps": sum(r.get("steps", 0) for r in rs),
            "input_tokens": sum(r.get("input_tokens", 0) for r in rs),
            "output_tokens": sum(r.get("output_tokens", 0) for r in rs),
            "wall_s": round(sum(r.get("wall_s", 0.0) for r in rs), 1),
        }  # fmt: skip
    return out


# ---------- the model, recorded ----------


class RecordingProvider:
    """Wraps a provider and keeps every reply plus the tool results that came back for it.

    The session sends the previous reply's tool results (the step report) as
    the messages after the last assistant message, so call ``i + 1`` carries
    the report for reply ``i``. The caller owns and closes it.
    """

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []
        self.first_user_message: str | None = None

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        if self.first_user_message is None:
            self.first_user_message = next((m.content for m in messages if m.role == "user"), None)
        last = max((i for i, m in enumerate(messages) if m.role == "assistant"), default=None)
        feedback = [m.content for m in messages[last + 1 :]] if last is not None else []
        started = time.monotonic()
        response = self.inner.complete(messages, tools)
        self.calls.append({
            "seconds": round(time.monotonic() - started, 2),
            "feedback": feedback,
            "text": response.text,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments,
                 "raw_arguments": c.raw_arguments if c.arguments is None else None}
                for c in response.tool_calls
            ],
            "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        })  # fmt: skip
        return response

    def reports(self) -> list[str | None]:
        """Per reply, the tool results the model got back for it (None for the last reply)."""
        return [
            "\n---\n".join(self.calls[i + 1]["feedback"]) if i + 1 < len(self.calls) else None
            for i in range(len(self.calls))
        ]

    def close(self) -> None:
        self.inner.close()


# ---------- jobs (run in worker processes) ----------


def _run_name(stamp: str, tag: str | None) -> str:
    if not tag:
        return stamp
    return f"{stamp}_{re.sub(r'[^A-Za-z0-9_.-]+', '-', tag).strip('-')}"


def anonymized(fm):
    """``fm`` with the neutral source name (the library keeps real names; only the eval hides them)."""
    return fm.model_copy(update={"source_name": ANON_SOURCE_NAME})


def prepare(form: dict, variant: str, run_dir: str) -> dict:
    """Write the input PDF (fake scans) and the form map for one form/variant; returns their paths."""
    from formcast.pagemap.build import build_form_map

    started = time.monotonic()
    original = FORMS / form["split"] / f"{form['id']}.pdf"
    inputs = Path(run_dir) / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    if variant == "fake_scan":
        pdf_path = inputs / f"{form['id']}_fake_scan.pdf"
        pdf_path.write_bytes(fake_scan_pdf(form["id"], original))
    else:
        pdf_path = original
    fm = anonymized(build_form_map(pdf_path))
    fm_path = inputs / f"{form['id']}_{variant}.formmap.json"
    fm_path.write_text(fm.model_dump_json())
    return {
        "pdf_path": str(pdf_path),
        "formmap_path": str(fm_path),
        "formmap_s": round(time.monotonic() - started, 1),
        "page_kinds": [p.kind for p in fm.pages],
        "text_sources": [p.text_source for p in fm.pages],
        "candidates": sum(len(p.candidates) for p in fm.pages),
    }


def score_row(row: dict, t, form: dict):
    """Fill ``row``'s detection, mapping and scored pages from template ``t``; returns the GT (None without any)."""
    gt = score.load_gt(form, FORMS / form["split"] / f"{form['id']}.pdf")
    if gt:
        row["detection"] = detection_scores(gt, t)
        row["mapping"] = {
            k: (round(v, 4) if isinstance(v, float) else v) for k, v in score.score_mapping(gt, t).items()
        }
        row["scored_pages"] = sorted({g.page for g in gt})
    return gt


def _draw_gt(png: bytes, page: pymupdf.Page, gt: list[score.GTField]) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    zoom = PREVIEW_DPI / 72
    for g in gt:
        r = pymupdf.Rect(g.rect) * page.rotation_matrix
        r.normalize()
        draw.rectangle([r.x0 * zoom - 2, r.y0 * zoom - 2, r.x1 * zoom + 2, r.y1 * zoom + 2], outline=GT_COLOR, width=2)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _step_log(result, recorder: RecordingProvider) -> list[dict]:
    reports = recorder.reports()
    steps = []
    for i, s in enumerate(result.steps):
        call = recorder.calls[i] if i < len(recorder.calls) else {}
        ex = s.exec
        steps.append({
            "n": s.n, "note": s.note, "model_text": call.get("text"), "tool_calls": call.get("tool_calls"),
            "code": s.code, "stdout": ex.stdout if ex else None, "error": ex.error if ex else None,
            "timed_out": ex.timed_out if ex else None, "applied": s.applied, "rejected": s.rejected,
            "warnings": s.warnings, "usage": {"input_tokens": s.usage.input_tokens,
                                              "output_tokens": s.usage.output_tokens},
            "report": reports[i] if i < len(reports) else None,
        })  # fmt: skip
    return steps


def run_one(job: dict) -> dict:
    """One agent session on one form/variant with one model; writes its files and returns its result row."""
    form, variant, prefix = job["form"], job["variant"], job["model_env"]
    redactor = _redactor_for([prefix])
    row: dict = {
        "form": form["id"], "split": form["split"], "kind": form["kind"], "variant": variant,
        "model_env": prefix, "detection": None, "baseline": job.get("baseline"), "mapping": None,
    }  # fmt: skip
    stem = f"{form['id']}_{variant}"
    tag_dir = prefix.lower()
    log_path = Path(job["run_dir"]) / tag_dir / f"{stem}.json"
    try:
        from formcast import build_template, preview
        from formcast.agent.session import SessionBudget
        from formcast.pagemap.model import FormMap
        from formcast.schema import flatten_schema
        from formcast.template.coverage import coverage

        prep = job["prepared"]
        row.update({k: prep[k] for k in ("formmap_s", "page_kinds", "text_sources", "candidates")})
        cfg = LLMConfig.from_env(prefix)
        row["model"] = cfg.model
        schema = json.loads((FORMS / "schemas" / f"{form['id']}.json").read_text())
        fm = FormMap.model_validate_json(Path(prep["formmap_path"]).read_text())
        budget = SessionBudget(**({"max_steps": job["max_steps"]} if job.get("max_steps") else {}))
        recorder = RecordingProvider(make_provider(cfg))
        started = time.monotonic()
        try:
            result = build_template(schema, prep["pdf_path"], recorder, form_map=fm, budget=budget)
        finally:
            recorder.close()
        wall = time.monotonic() - started
        t = result.template
        cov = coverage(t, fm, flatten_schema(schema))
        row.update({
            "steps": len(result.steps), "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens, "wall_s": round(wall, 1),
            "stop_reason": result.stop_reason, "finished": result.finished, "error": result.error,
            "unplaceable": len(result.unplaceable), "fields": len(t.fields),
            "fields_with_source": sum(1 for f in t.fields if f.source and f.source.paths),
            "schema_mapped": cov.mappable_count - len(cov.unmapped_paths), "schema_mappable": cov.mappable_count,
        })  # fmt: skip

        template_path = Path(job["templates_dir"]) / tag_dir / f"{stem}.json"
        _write(template_path, t.to_json(), redactor)
        row["template_path"] = str(template_path.relative_to(ROOT))

        gt = score_row(row, t, form)

        overlay_dir = Path(job["overlays_dir"]) / tag_dir
        overlay_dir.mkdir(parents=True, exist_ok=True)
        doc = open_pdf(prep["pdf_path"])
        try:
            for pno, png in enumerate(preview(t, prep["pdf_path"], dpi=PREVIEW_DPI, show_candidates=fm)):
                page_gt = [g for g in gt or [] if g.page == pno]
                (overlay_dir / f"{stem}_p{pno}.png").write_bytes(_draw_gt(png, doc[pno], page_gt))
        finally:
            doc.close()

        _write_json(log_path, {
            "form": form["id"], "variant": variant, "model_env": prefix, "model": cfg.model,
            "stop_reason": result.stop_reason, "error": result.error, "summary": result.summary,
            "unplaceable": result.unplaceable,
            "usage": {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens},
            "first_user_message": recorder.first_user_message, "steps": _step_log(result, recorder),
        }, redactor)  # fmt: skip
        row["log_path"] = str(log_path.relative_to(ROOT))
    except Exception as e:  # noqa: BLE001 - one broken session must not end the run
        row["error"] = redactor.scrub(f"harness: {type(e).__name__}: {e}")
        _write(log_path.with_suffix(".error.txt"), traceback.format_exc(), redactor)
    return row


# ---------- report ----------


def _pct(x) -> str:
    return "–" if x is None else f"{x:.2f}"


def _det_cell(d: dict | None) -> tuple[str, str, str]:
    """(recall@.3 (.5), precision@.3, type acc@.3)."""
    if not d:
        return "–", "–", "–"
    a, b = d["iou0.3"], d["iou0.5"]
    return f"{a['recall']:.2f} ({b['recall']:.2f})", f"{a['precision']:.2f}", f"{a['type_acc']:.2f}"


def render_markdown(meta: dict, rows: list[dict], tot: dict) -> str:
    title = f"# Agent eval: {meta['model']} ({meta['model_env']}), {meta['split']}, {meta['run']}"
    lines = [
        title, "",
        f"- model: `{meta['model']}` via `{meta['api_format']}`; tag: {meta['tag'] or '–'}; "
        f"commit: `{meta['commit']}`; budget: {meta['budget']}",
        f"- baseline file: `{meta['baseline_file']}`",
        "", "Notes:", "",
        *[f"- {n}" for n in NOTES],
        "", "## Per form", "",
        "| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | "
        "baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | mapped GT | "
        "steps | tokens in / out | wall s | stop | unplaceable |",
        "|" + "---|" * 21,
    ]  # fmt: skip
    for r in rows:
        if r.get("detection") is None:
            err = (r.get("error") or "no ground truth").replace("|", "/").replace("\n", " ")[:120]
            lines.append(f"| {r['form']} | {r['kind']} | {r['variant']} | – | – | – | – | – | – | – | – | – | – | – "
                         f"| – | – | {r.get('steps', '–')} | – | – | {r.get('stop_reason', 'error')} | {err} |")
            continue
        d, m, b = r["detection"], r["mapping"], r.get("baseline")
        rec, prec, tacc = _det_cell(d)
        brec, bprec, _ = _det_cell(b)
        stop = r["stop_reason"] + (f" (EXCLUDED from totals: {r['error'][:60]})" if r.get("error") else "")
        lines.append(
            f"| {r['form']} | {r['kind']} | {r['variant']} | {d['iou0.3']['gt']} | {d['iou0.3']['pred']} | {rec} | "
            f"{prec} | {tacc} | {b['detector'] if b else '–'} | {brec} | {bprec} | {_pct(m['path_acc'])} | "
            f"{_pct(m['transform_acc']) if m['n_transform'] else '–'} | {_pct(m['joint_acc'])} | "
            f"{_pct(m['exact_path_set_acc'])} | {m['n_path']} | {r['steps']} | "
            f"{r['input_tokens']:,} / {r['output_tokens']:,} | {r['wall_s']:.0f} | {stop} | {r['unplaceable']} |"
        )
    lines += [
        "", "## Totals (micro-averaged)", "",
        "| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | "
        "baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | "
        "tokens in / out | wall s |",
        "|" + "---|" * 17,
    ]  # fmt: skip
    for name, t in tot.items():
        d, m, b = t["detection"], t["mapping"], t["baseline"]
        rec, prec, tacc = _det_cell(d) if t["forms"] else ("–", "–", "–")
        brec, bprec, _ = _det_cell(b)
        lines.append(
            f"| {name} | {t['forms']} ({t['failed']}) | {t['finished']} | {d['iou0.3']['gt']} | {d['iou0.3']['pred']} | "
            f"{rec} | {prec} | {tacc} | {brec} | {bprec} | {_pct(m['path_acc'])} | {_pct(m['transform_acc'])} | "
            f"{_pct(m['joint_acc'])} | {_pct(m['exact_path_set_acc'])} | {t['steps']} | "
            f"{t['input_tokens']:,} / {t['output_tokens']:,} | {t['wall_s']:.0f} |"
        )
    errored = tot["all"].get("errored") if "all" in tot else None
    if errored:
        lines += ["", f"Excluded from the totals above (session ended in an error): {', '.join(errored)}."]
    if meta.get("rescored"):
        lines += ["", f"Re-scored: {meta['rescored']}"]
    return "\n".join(lines) + "\n"


# ---------- main ----------


def _git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _split_list(values: list[str] | None) -> list[str]:
    return [v.strip() for value in values or [] for v in value.split(",") if v.strip()]


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", choices=["dev", "heldout"], help="required unless --rescore")
    ap.add_argument("--model-env", action="append",
                    help="LLM config env prefix, e.g. ANTHROPIC_EVAL; repeatable or comma-separated "
                         "(required unless --seed-only or --rescore)")
    ap.add_argument("--seed-only", action="store_true",
                    help="score the untouched seeded template of each form/variant; no model, no API calls")
    ap.add_argument("--rescore", metavar="RESULTS_JSON", default=None,
                    help="re-score the saved templates of a results file with the current scorer; no API calls")
    ap.add_argument("--forms", default=None, help="comma-separated form ids (default: the whole split)")
    ap.add_argument("--variant", default="as_published", choices=["as_published", "fake_scan", "both"])
    ap.add_argument("--jobs", type=int, default=3, help="sessions run in parallel processes")
    ap.add_argument("--stamp", default=None, help="date stamp for result files (default: today)")
    ap.add_argument("--tag", default=None, help="free-text run label, recorded and appended to file names")
    ap.add_argument("--max-steps", type=int, default=None, help="override SessionBudget.max_steps")
    ap.add_argument("--baseline-stamp", default=DEFAULT_BASELINE_STAMP)
    args = ap.parse_args(argv)
    args.model_env = _split_list(args.model_env)
    if args.rescore and args.seed_only:
        ap.error("--rescore and --seed-only are separate modes")
    if not args.rescore and not args.split:
        ap.error("--split is required")
    if not args.rescore and not args.seed_only and not args.model_env:
        ap.error("--model-env is required (or use --seed-only / --rescore)")
    if args.jobs < 1:
        ap.error("--jobs must be at least 1")
    return args


def select_forms(manifest: list[dict], split: str, ids: str | None) -> list[dict]:
    """Manifest entries of ``split``, limited to ``ids`` (comma list) when given.

    Raises:
        ValueError: for an id that is not a form of ``split``.
    """
    forms = [f for f in manifest if f["split"] == split]
    if not ids:
        return forms
    wanted = _split_list([ids])
    by_id = {f["id"]: f for f in forms}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise ValueError(f"not {split} forms: {missing}")
    return [by_id[i] for i in wanted]


def variants_for(form: dict, variant: str) -> list[str]:
    """The variants run for ``form``: fake scans only of forms that are not scanned already."""
    wanted = VARIANTS if variant == "both" else (variant,)
    return [v for v in wanted if v == "as_published" or form["kind"] != "scanned"]


def _load_baseline_rows(stamp: str) -> tuple[Path, list[dict]]:
    path = RESULTS / f"baseline_detection_{stamp}.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    if not rows:
        print(f"warning: no baseline rows in {path.name}; baseline columns stay empty", flush=True)
    return path, rows


def rescore_main(args) -> None:
    """Re-score the saved templates of a results file in place (no model, no API calls)."""
    from formcast.template.model import Template

    path = Path(args.rescore)
    if not path.is_absolute():
        path = ROOT / path
    data = json.loads(path.read_text())
    meta, rows = data["meta"], data["rows"]
    missing = [r["template_path"] for r in rows if r.get("template_path") and not (ROOT / r["template_path"]).is_file()]
    if missing and TEMPLATES_ARCHIVE.is_file():
        archive = TEMPLATES_ARCHIVE.relative_to(ROOT) if TEMPLATES_ARCHIVE.is_relative_to(ROOT) else TEMPLATES_ARCHIVE
        raise SystemExit(
            f"{len(missing)} of {len(rows)} saved template(s) are missing (first: {missing[0]}); the committed "
            f"templates are archived in {archive}: extract them from the repo root with `tar -xzf {archive}`, "
            "then re-run --rescore (nothing was rewritten)"
        )
    baseline_file, baseline_rows = _load_baseline_rows(args.baseline_stamp)
    manifest = {f["id"]: f for f in json.loads((FORMS / "manifest.json").read_text())["forms"]}
    done = 0
    for r in rows:
        tp = r.get("template_path")
        if not tp or not (ROOT / tp).is_file():
            print(f"  no saved template for {r['form']} {r['variant']}; row left as it was", flush=True)
            continue
        form = manifest[r["form"]]
        r["baseline"] = baseline_scores(baseline_rows, form["id"], r["variant"],
                                        baseline_detector(form["kind"], r["variant"]))
        r["detection"] = r["mapping"] = None
        score_row(r, Template.from_json((ROOT / tp).read_text()), form)
        done += 1
    tot = totals(rows)
    meta.update({
        "notes": NOTES, "baseline_file": baseline_file.name,
        "rescored": f"{dt.date.today().isoformat()} at commit {_git_commit()}: the saved templates were scored again "
                    "with the stacked-lines rule for multi-line fields; model output is unchanged.",
    })  # fmt: skip
    _write_json(path, {"meta": meta, "rows": rows, "totals": tot}, Redactor([]))
    _write(path.with_suffix(".md"), render_markdown(meta, rows, tot), Redactor([]))
    print(f"re-scored {done} of {len(rows)} rows; wrote {path.relative_to(ROOT)} and .md", flush=True)


def seed_main(args) -> None:
    """Score the seeded template of each form/variant, with no agent step (no model, no API calls)."""
    from formcast.pagemap.model import FormMap
    from formcast.template.model import new_template

    stamp = args.stamp or dt.date.today().isoformat()
    run = _run_name(stamp, args.tag)
    manifest = json.loads((FORMS / "manifest.json").read_text())["forms"]
    forms = select_forms(manifest, args.split, args.forms)
    pairs = [(f, v) for f in forms for v in variants_for(f, args.variant)]
    if not pairs:
        raise SystemExit("nothing to run (fake_scan is not run on scanned forms)")
    run_dir = RESULTS / "logs" / f"seed_{run}"
    baseline_file, baseline_rows = _load_baseline_rows(args.baseline_stamp)
    ctx = multiprocessing.get_context("spawn")
    print(f"preparing {len(pairs)} form/variant inputs with {args.jobs} jobs", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as pool:
        futures = {(f["id"], v): pool.submit(prepare, f, v, str(run_dir)) for f, v in pairs}
        prepared = {k: fut.result() for k, fut in futures.items()}
    rows = []
    for f, v in pairs:
        prep = prepared[(f["id"], v)]
        schema = json.loads((FORMS / "schemas" / f"{f['id']}.json").read_text())
        fm = FormMap.model_validate_json(Path(prep["formmap_path"]).read_text())
        row = {
            "form": f["id"], "split": f["split"], "kind": f["kind"], "variant": v, "model_env": "SEED",
            "model": "seed-only", "detection": None, "mapping": None,
            "baseline": baseline_scores(baseline_rows, f["id"], v, baseline_detector(f["kind"], v)),
            "stop_reason": "seed_only", "finished": False, "error": None, "steps": 0, "input_tokens": 0,
            "output_tokens": 0, "wall_s": 0.0, "unplaceable": 0,
            **{k: prep[k] for k in ("formmap_s", "page_kinds", "text_sources", "candidates")},
        }  # fmt: skip
        t = new_template(schema, fm, candidates=True)
        row["fields"] = len(t.fields)
        score_row(row, t, f)
        rows.append(row)
        d, m = row["detection"]["iou0.3"], row["detection"]["iou0.5"]
        print(f"  {f['id']} {v}: seeded recall@.5={m['recall']} recall@.3={d['recall']} precision@.3={d['precision']}",
              flush=True)
    tot = totals(rows)
    meta = {
        "model_env": "SEED", "model": "seed-only (no model; untouched seeded template)", "api_format": "none",
        "split": args.split, "variant": args.variant, "forms": [f["id"] for f in forms], "stamp": stamp,
        "tag": args.tag, "run": run, "commit": _git_commit(), "budget": {}, "baseline_file": baseline_file.name,
        "jobs": args.jobs, "argv": sys.argv[1:], "notes": NOTES,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }  # fmt: skip
    base = RESULTS / f"agent_seed_{args.split}_{run}"
    _write_json(base.with_suffix(".json"), {"meta": meta, "rows": rows, "totals": tot}, Redactor([]))
    _write(base.with_suffix(".md"), render_markdown(meta, rows, tot), Redactor([]))
    print(f"wrote {base.with_suffix('.json').relative_to(ROOT)} and .md", flush=True)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.rescore:
        return rescore_main(args)
    if args.seed_only:
        return seed_main(args)
    from formcast.agent.session import SessionBudget

    load_eval_env(args.model_env)
    redactor = _redactor_for(args.model_env)
    configs = {p: LLMConfig.from_env(p) for p in args.model_env}  # fail fast on a missing config

    stamp = args.stamp or dt.date.today().isoformat()
    run = _run_name(stamp, args.tag)
    manifest = json.loads((FORMS / "manifest.json").read_text())["forms"]
    forms = select_forms(manifest, args.split, args.forms)
    pairs = [(f, v) for f in forms for v in variants_for(f, args.variant)]
    if not pairs:
        raise SystemExit("nothing to run (fake_scan is not run on scanned forms)")

    run_dir = RESULTS / "logs" / run
    templates_dir = RESULTS / "templates" / run
    overlays_dir = RESULTS / "overlays" / "agent" / run
    baseline_file = RESULTS / f"baseline_detection_{args.baseline_stamp}.json"
    baseline_rows = json.loads(baseline_file.read_text()) if baseline_file.exists() else []
    if not baseline_rows:
        print(f"warning: no baseline rows in {baseline_file.name}; baseline columns stay empty", flush=True)

    ctx = multiprocessing.get_context("spawn")
    print(f"preparing {len(pairs)} form/variant inputs with {args.jobs} jobs", flush=True)
    prepared: dict[tuple[str, str], dict] = {}
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as pool:
        futures = {(f["id"], v): pool.submit(prepare, f, v, str(run_dir)) for f, v in pairs}
        for key, fut in futures.items():
            try:
                prepared[key] = fut.result()
                print(f"  prepared {key[0]} {key[1]} in {prepared[key]['formmap_s']}s", flush=True)
            except Exception as e:  # noqa: BLE001
                prepared[key] = {"error": f"prepare: {type(e).__name__}: {e}"}
                print(f"  FAILED to prepare {key[0]} {key[1]}: {type(e).__name__}: {e}", flush=True)

    jobs = []
    for f, v in pairs:
        det = baseline_detector(f["kind"], v)
        for prefix in args.model_env:
            jobs.append({
                "form": f, "variant": v, "model_env": prefix, "prepared": prepared[(f["id"], v)],
                "baseline": baseline_scores(baseline_rows, f["id"], v, det), "max_steps": args.max_steps,
                "run_dir": str(run_dir), "templates_dir": str(templates_dir), "overlays_dir": str(overlays_dir),
            })  # fmt: skip
    print(f"running {len(jobs)} sessions with {args.jobs} jobs", flush=True)
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as pool:
        futures = []
        for job in jobs:
            if "error" in job["prepared"]:
                rows.append({"form": job["form"]["id"], "split": job["form"]["split"], "kind": job["form"]["kind"],
                             "variant": job["variant"], "model_env": job["model_env"], "detection": None,
                             "mapping": None, "baseline": job["baseline"], "error": job["prepared"]["error"]})
                continue
            futures.append(pool.submit(run_one, job))
        for fut in futures:
            row = fut.result()
            rows.append(row)
            d = (row.get("detection") or {}).get("iou0.3") or {}
            m = row.get("mapping") or {}
            print(f"  {row['model_env']} {row['form']} {row['variant']}: stop={row.get('stop_reason')} "
                  f"steps={row.get('steps')} recall@.3={d.get('recall')} precision@.3={d.get('precision')} "
                  f"path={m.get('path_acc')} joint={m.get('joint_acc')} tokens={row.get('input_tokens')}/"
                  f"{row.get('output_tokens')} wall={row.get('wall_s')}s"
                  + (f" error={redactor.scrub(row['error'])}" if row.get("error") else ""), flush=True)

    order = {(f["id"], v): i for i, (f, v) in enumerate(pairs)}
    budget = SessionBudget(**({"max_steps": args.max_steps} if args.max_steps else {}))
    commit = _git_commit()
    for prefix in args.model_env:
        cfg = configs[prefix]
        mine = sorted((r for r in rows if r["model_env"] == prefix), key=lambda r: order[(r["form"], r["variant"])])
        tot = totals(mine)
        meta = {
            "model_env": prefix, "model": cfg.model, "api_format": cfg.api_format,
            "base_url": cfg.base_url.split("?")[0], "split": args.split, "variant": args.variant,
            "forms": [f["id"] for f in forms], "stamp": stamp, "tag": args.tag, "run": run, "commit": commit,
            "budget": vars(budget), "baseline_file": baseline_file.name, "jobs": args.jobs,
            "argv": sys.argv[1:] if argv is None else list(argv), "notes": NOTES,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }  # fmt: skip
        base = RESULTS / f"agent_{prefix.lower()}_{args.split}_{run}"
        _write_json(base.with_suffix(".json"), {"meta": meta, "rows": mine, "totals": tot}, redactor)
        _write(base.with_suffix(".md"), render_markdown(meta, mine, tot), redactor)
        print(f"wrote {base.with_suffix('.json').relative_to(ROOT)} and .md", flush=True)


if __name__ == "__main__":
    main()
