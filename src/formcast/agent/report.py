"""The step report: what the model reads back after each ``run_python`` call."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from formcast.pagemap.model import FormMap
from formcast.template.coverage import Coverage
from formcast.template.model import Template

if TYPE_CHECKING:
    from formcast.agent.session import StepRecord

MAX_LISTED = 40
# Safety cap only: the sandbox already truncates stdout (8000 chars by default), and the
# api() listing alone is over 6000 chars, so this must stay above the sandbox limit.
MAX_STDOUT = 12000
MAX_OP_CHARS = 160
MAX_LABEL = 50


def _short_op(op: dict) -> str:
    """``<id> <op> {rest}`` on one line, cut to MAX_OP_CHARS."""
    rest = {k: v for k, v in op.items() if k not in ("op", "id")}
    text = f"{op.get('id', '-')} {op.get('op', '?')}"
    if rest:
        text += " " + json.dumps(rest, separators=(",", ":"))
    return text if len(text) <= MAX_OP_CHARS else text[: MAX_OP_CHARS - 3] + "..."


def _capped(items: list[str], what: str) -> list[str]:
    out = [f"  {x}" for x in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        out.append(f"  ... {len(items) - MAX_LISTED} more {what}")
    return out


def _candidate_labels(form_map: FormMap) -> dict[str, str]:
    out: dict[str, str] = {}
    for page in form_map.pages:
        lines = {ln.id: ln.text for ln in page.lines}
        for c in page.candidates:
            label = lines.get(c.label_line_id) if c.label_line_id is not None else None
            out[c.id] = " ".join(label.split())[:MAX_LABEL] if label else ""
    return out


def step_report(
    record: StepRecord,
    coverage: Coverage,
    *,
    template: Template,
    form_map: FormMap,
) -> str:
    """Plain-text report of one step plus the template's current coverage."""
    ex = record.exec
    out: list[str] = [f"STEP {record.n} REPORT"]

    stdout = ex.stdout if ex is not None else ""
    if len(stdout) > MAX_STDOUT:
        stdout = stdout[:MAX_STDOUT] + f"\n... [output truncated: {len(stdout) - MAX_STDOUT} more characters]"
    elif ex is not None and ex.truncated and "truncated" not in stdout[-200:]:
        stdout += "\n... [output truncated]"
    out.append("stdout:" + ("\n" + stdout.rstrip("\n") if stdout.strip() else " (none)"))
    if ex is not None and ex.error:
        out.append("error:\n" + ex.error)

    out.append(f"applied ops ({len(record.applied)}):")
    out.extend(_capped([_short_op(op) for op in record.applied], "ops"))
    if record.rejected:
        out.append(f"REJECTED ops ({len(record.rejected)}), fix and retry:")
        out.extend(
            _capped([f"{_short_op(r['op'])}\n    reason: {r['reason']}" for r in record.rejected], "rejected")
        )
    if record.warnings:
        out.append(f"warnings ({len(record.warnings)}):")
        out.extend(_capped(record.warnings, "warnings"))

    fields = template.fields
    with_source = sum(1 for f in fields if f.source and f.source.paths)
    mappable = coverage.mappable_count
    mapped = max(0, mappable - len(coverage.unmapped_paths))
    out.append(
        f"counts: fields: {len(fields)} ({with_source} with a source); "
        f"schema paths: {mapped} of {mappable} mapped"
    )

    out.append(f"unmapped schema paths ({len(coverage.unmapped_paths)}):")
    out.extend(_capped(coverage.unmapped_paths, "paths") or ["  (none)"])
    labels = _candidate_labels(form_map)
    unused = [f'{cid} "{labels.get(cid, "")}"' for cid in coverage.unused_candidates]
    out.append(f"unused candidates ({len(unused)}):")
    out.extend(_capped(unused, "candidates") or ["  (none)"])
    if coverage.unmapped_fields:
        shown = ", ".join(coverage.unmapped_fields[:MAX_LISTED])
        more = (
            f", ... {len(coverage.unmapped_fields) - MAX_LISTED} more"
            if len(coverage.unmapped_fields) > MAX_LISTED
            else ""
        )
        out.append(f"fields without a source ({len(coverage.unmapped_fields)}): {shown}{more}")
    if coverage.low_confidence:
        out.append(f"low-confidence fields: {', '.join(coverage.low_confidence[:MAX_LISTED])}")
    return "\n".join(out)
