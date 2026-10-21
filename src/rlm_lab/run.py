from __future__ import annotations

import argparse
import json
from pathlib import Path

from rlm_lab.execution.runners import LabRunner, RunOptions
from rlm_lab.execution.scenarios import SCENARIO_SETS
from rlm_lab.execution.store import RunStore
from rlm_lab.modeling.config import ModelConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the DSPy.RLM exploratory lab from the command line.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt_parser = subparsers.add_parser("prompt", help="Run a single prompt.")
    prompt_parser.add_argument("--prompt", required=True)
    prompt_parser.add_argument("--runner", choices=["baseline", "rlm", "both"], default="both")
    prompt_parser.add_argument("--corpus-dir")
    prompt_parser.add_argument("--top-k", type=int, default=4)
    prompt_parser.add_argument("--max-iterations", type=int, default=6)
    prompt_parser.add_argument("--max-llm-calls", type=int, default=6)

    scenario_parser = subparsers.add_parser("scenario", help="Run a named scenario set.")
    scenario_parser.add_argument("--set", choices=sorted(SCENARIO_SETS), default="starter")
    scenario_parser.add_argument("--runner", choices=["baseline", "rlm", "both"], default="both")
    scenario_parser.add_argument("--corpus-dir")
    scenario_parser.add_argument("--top-k", type=int, default=4)
    scenario_parser.add_argument("--max-iterations", type=int, default=6)
    scenario_parser.add_argument("--max-llm-calls", type=int, default=6)

    export_parser = subparsers.add_parser("export", help="Export saved run summaries.")
    export_parser.add_argument("--format", choices=["csv", "json"], default="csv")
    export_parser.add_argument("--output", required=True)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    store = RunStore(Path("runs"))

    if args.command == "export":
        path = store.export_summary(Path(args.output), format_name=args.format)
        print(path)
        return

    runner = LabRunner(run_store=store, model_config=ModelConfig.from_env())

    prompts = [args.prompt] if args.command == "prompt" else SCENARIO_SETS[args.set]
    traces = []
    for prompt in prompts:
        trace = runner.run(
            RunOptions(
                prompt=prompt,
                runner_mode=args.runner,
                corpus_dir=Path(args.corpus_dir).expanduser() if args.corpus_dir else None,
                retrieval_top_k=args.top_k,
                max_iterations=args.max_iterations,
                max_llm_calls=args.max_llm_calls,
            )
        )
        traces.append(trace)

    payload = [
        {
            "run_id": trace.run_id,
            "runner_mode": trace.runner_mode,
            "results": {
                name: {
                    "success": result.success,
                    "total_tokens": result.metrics.total_tokens,
                    "duration_ms": result.metrics.duration_ms,
                    "answer": result.answer,
                    "error": result.error,
                }
                for name, result in trace.results.items()
            },
        }
        for trace in traces
    ]
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
