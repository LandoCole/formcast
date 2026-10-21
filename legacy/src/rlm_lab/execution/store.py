from __future__ import annotations

import csv
import json
from pathlib import Path

from rlm_lab.execution.events import ExperimentTrace, RunSummary


class RunStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _trace_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def _event_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.events.jsonl"

    def save(self, trace: ExperimentTrace) -> None:
        self._trace_path(trace.run_id).write_text(trace.model_dump_json(indent=2), encoding="utf-8")
        with self._event_path(trace.run_id).open("w", encoding="utf-8") as handle:
            for event in trace.events:
                handle.write(event.model_dump_json())
                handle.write("\n")

    def load_trace(self, run_id: str) -> ExperimentTrace:
        payload = json.loads(self._trace_path(run_id).read_text(encoding="utf-8"))
        return ExperimentTrace.model_validate(payload)

    def list_runs(self, limit: int = 100) -> list[RunSummary]:
        traces: list[RunSummary] = []
        for path in sorted(self.runs_dir.glob("*.json"), reverse=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            trace = ExperimentTrace.model_validate(payload)
            traces.append(
                RunSummary(
                    run_id=trace.run_id,
                    created_at=trace.created_at,
                    runner_mode=trace.runner_mode,
                    model=trace.model,
                    prompt_preview=trace.prompt[:120],
                    result_count=len(trace.results),
                )
            )
            if len(traces) >= limit:
                break
        return traces

    def export_summary(self, output_path: Path, format_name: str = "csv") -> Path:
        traces = [self.load_trace(summary.run_id) for summary in self.list_runs(limit=10_000)]
        rows = []
        for trace in traces:
            for runner_name, result in trace.results.items():
                rows.append(
                    {
                        "run_id": trace.run_id,
                        "created_at": trace.created_at.isoformat(),
                        "runner_mode": trace.runner_mode,
                        "runner": runner_name,
                        "model": trace.model,
                        "success": result.success,
                        "duration_ms": result.metrics.duration_ms,
                        "total_tokens": result.metrics.total_tokens,
                        "prompt_tokens": result.metrics.prompt_tokens,
                        "completion_tokens": result.metrics.completion_tokens,
                        "answer_preview": (result.answer or "")[:120],
                    }
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if format_name == "json":
            output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            return output_path

        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
            if rows:
                writer.writeheader()
                writer.writerows(rows)
        return output_path
