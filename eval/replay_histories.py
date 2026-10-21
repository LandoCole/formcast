"""Offline what-if: re-apply saved agent edit histories under the current edit ops and score them.

No model is called. For each saved template of a results file, the session's starting
template (the seed: ``new_template(..., candidates=True)`` over the saved form map) gets
the agent's recorded ops again, one by one, with the current ``formcast.template.ops``
validation (author "agent", the session's ``OpContext``). Ops the current code refuses
are dropped and counted, the rest apply, and the result is scored like a session.

Modes (rows are written for each):

* ``original``: the saved final template as it is (sanity check: matches the results file);
* ``guard``: the replay; it assumes the agent never retries a refused op, so it bounds
  what the removal guard alone does to a run with the old prompt;
* ``guard+link``: the replay, then every computed cell of a table that holds a kept,
  unmapped seeded field (at least ``score``'s 70% inside the cell) is linked to that
  field (``Column.fields``);
* ``link-instead``: as ``guard+link``, but a removal of a seeded field that sits in a cell
  of a table already built (at least 70% inside it) is skipped even when the ops would
  allow it, so that field is linked too: the new prompt's instruction (link the fields a
  table holds instead of removing them), followed every time.

This is a counterfactual on old behavior, not a measurement of the agent with the new
prompt: an agent that reads the refusals would act differently from step to step.

Usage:
    python eval/replay_histories.py eval/results/agent_<...>.json --formmaps <dir with
        <form>_<variant>.formmap.json> [--out eval/results/replay_<name>.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import score  # noqa: E402
from formcast.errors import OpError  # noqa: E402
from formcast.geometry import contained_fraction  # noqa: E402
from formcast.pagemap.model import FormMap  # noqa: E402
from formcast.schema import flatten_schema  # noqa: E402
from formcast.template.model import Template, linked_cells, new_template, table_cells  # noqa: E402
from formcast.template.ops import OpContext, apply  # noqa: E402

FORMS = ROOT / "forms"
MODES = ("original", "guard", "guard+link", "link-instead")


def session_context(fm: FormMap, schema: dict) -> OpContext:
    """The ``OpContext`` an agent session builds (agent/session.py)."""
    return OpContext(
        schema_paths=flatten_schema(schema),
        page_sizes=[(p.width, p.height) for p in sorted(fm.pages, key=lambda p: p.index)],
        widget_names=[w.name for p in fm.pages for w in p.widgets],
    )


def _in_a_table_cell(t: Template, fid: str) -> bool:
    f = next((f for f in t.fields if f.id == fid), None)
    if f is None or f.type == "table" or f.origin not in ("widget", "detector"):
        return False
    return any(
        c.field is None and c.page == f.page and contained_fraction(f.rect, c.rect) >= score.MULTILINE_CONTAINED
        for table in t.fields if table.type == "table" for c in table_cells(t, table)
    )  # fmt: skip


def replay_guarded(
    seed: Template, saved: Template, ctx: OpContext, keep_table_cells: bool = False
) -> tuple[Template, Counter]:
    """``saved.history`` re-applied on ``seed``; refused ops are skipped and counted per op name.

    With ``keep_table_cells`` a removal of a seeded field inside a table cell is skipped
    too (counted as ``kept_for_link``)."""
    t, refused = seed, Counter()
    for entry in saved.history:
        if keep_table_cells and entry.op.get("op") == "remove_field" and _in_a_table_cell(t, entry.op.get("id")):
            refused["kept_for_link"] += 1
            continue
        try:
            t, _ = apply(t, entry.op, author=entry.author, note=entry.note, context=ctx)
        except OpError:
            refused[entry.op.get("op", "?")] += 1
    return t, refused


def link_kept_fields(t: Template) -> tuple[Template, int]:
    """Link each table cell that holds a kept, unmapped seeded field to that field (one field per cell)."""
    t = t.model_copy(deep=True)
    taken = set(linked_cells(t))
    n = 0
    for table in [f for f in t.fields if f.type == "table"]:
        for cell in table_cells(t, table):
            if cell.field is not None:
                continue
            seg = table.segments[cell.segment]
            hits = [
                g for g in t.fields
                if g.type != "table" and g.origin in ("widget", "detector") and g.id not in taken
                and not (g.source and g.source.paths) and g.page == cell.page
                and contained_fraction(g.rect, cell.rect) >= score.MULTILINE_CONTAINED
            ]  # fmt: skip
            if not hits:
                continue
            g = max(hits, key=lambda g: contained_fraction(g.rect, cell.rect))
            col = cell.column
            col.fields = (col.fields + [None] * seg.max_rows)[: seg.max_rows]
            col.fields[cell.row] = g.id
            taken.add(g.id)
            n += 1
    return t, n


def _group(row: dict) -> str:
    if row["variant"] == "fake_scan" or row["kind"] == "scanned":
        return "scanned"
    return "fillable" if row["kind"] == "fillable" else "digital"


def _scores(gt, t: Template) -> dict:
    pages = {g.page for g in gt}
    out = {}
    for thr in (0.3, 0.5):
        s = score.score_template_detection(gt, t, thr, pages=pages)
        out[f"iou{thr}"] = {k: s[k] for k in ("gt", "pred", "matched", "pred_matched")}
    out["mapping"] = {k: v for k, v in score.score_mapping(gt, t).items() if not isinstance(v, float)}
    return out


def _sum(parts: list[dict]) -> dict:
    out: dict = {}
    for key in ("iou0.3", "iou0.5", "mapping"):
        counts = Counter()
        for p in parts:
            counts.update(p[key])
        out[key] = dict(counts)
    r = score._ratio
    d5, d3, m = out["iou0.5"], out["iou0.3"], out["mapping"]
    out["summary"] = {
        "recall@.5": round(r(d5["matched"], d5["gt"]), 4),
        "recall@.3": round(r(d3["matched"], d3["gt"]), 4),
        "precision@.3": round(r(d3["pred_matched"], d3["pred"]), 4),
        "path": round(r(m["path_ok"], m["n_path"]), 4),
        "joint": round(r(m["joint_ok"], m["n_path"]), 4),
        "exact": round(r(m["exact_ok"], m["n_path"]), 4),
    }
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("results", help="an agent_<...>.json results file whose templates are extracted")
    ap.add_argument("--formmaps", required=True, help="directory with <form>_<variant>.formmap.json (the run's inputs)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    results = json.loads(Path(args.results).read_text())
    manifest = {f["id"]: f for f in json.loads((FORMS / "manifest.json").read_text())["forms"]}
    rows = []
    for r in results["rows"]:
        if r.get("error") or not r.get("template_path"):
            continue
        form = manifest[r["form"]]
        key = f"{r['form']}_{r['variant']}"
        fm = FormMap.model_validate_json((Path(args.formmaps) / f"{key}.formmap.json").read_text())
        schema = json.loads((FORMS / "schemas" / f"{r['form']}.json").read_text())
        saved = Template.from_json((ROOT / r["template_path"]).read_text())
        ctx = session_context(fm, schema)
        seed = new_template(schema, fm, candidates=True)
        guarded, refused = replay_guarded(seed, saved, ctx)
        linked, n_linked = link_kept_fields(guarded)
        instead, _ = replay_guarded(seed, saved, ctx, keep_table_cells=True)
        instead, n_instead = link_kept_fields(instead)
        gt = score.load_gt(form, FORMS / form["split"] / f"{r['form']}.pdf")
        row = {
            "form": r["form"], "variant": r["variant"], "kind": r["kind"], "group": _group(r),
            "refused": dict(refused), "linked": n_linked, "linked_instead": n_instead,
            "seed": _scores(gt, seed), "original": _scores(gt, saved),
            "guard": _scores(gt, guarded), "guard+link": _scores(gt, linked),
            "link-instead": _scores(gt, instead),
        }  # fmt: skip
        rows.append(row)
        s = {m: row[m]["iou0.5"]["matched"] for m in ("seed", *MODES)}
        print(f"{key:34} recall@.5 matched {s}  refused {dict(refused)}  linked {n_linked}", flush=True)

    totals = {}
    for group in ("fillable", "digital", "scanned", "all"):
        mine = [x for x in rows if group == "all" or x["group"] == group]
        totals[group] = {"sessions": len(mine), **{m: _sum([x[m] for x in mine]) for m in ("seed", *MODES)}}
    print()
    print(f"{'group':9} {'mode':11} " + " ".join(f"{k:>12}" for k in totals["all"]["seed"]["summary"]))
    for group, tot in totals.items():
        for m in ("seed", *MODES):
            print(f"{group:9} {m:11} " + " ".join(f"{v:12.2f}" for v in tot[m]["summary"].values()))
    out = {
        "source": args.results,
        "note": "offline counterfactual: saved agent histories re-applied under the current ops; no model calls",
        "rows": rows,
        "totals": totals,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
