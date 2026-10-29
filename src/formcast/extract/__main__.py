"""The command line: ``python -m formcast.extract scan | run | export | ai``.

    python -m formcast.extract scan DIR [--first-page]
    python -m formcast.extract run TEMPLATE.json [TEMPLATE.json ...] DIR --out OUT [--workers N] [--timeout S]
    python -m formcast.extract export OUT --template TEMPLATE.json [...] --format xlsx|parquet|csv --to PATH
    python -m formcast.extract ai OUT --template TEMPLATE.json [...]

``scan`` groups the PDFs under ``DIR`` by layout and prints each group (size, fillable and flattened, pages, three
example files) and the files it could not read. ``run`` reads every PDF under ``DIR`` with the templates into ``OUT``
(resuming a run already there), writes progress to stderr every 100 documents, and ends with the run's summary;
``--timeout`` is the seconds a document may take before it is set aside as hung (120; ``--workers 1`` reads in
this process, with no timeout and no crash isolation).
``export`` writes the finished run as a workbook (``xlsx``) or a folder of files (``parquet``, ``csv``).
``ai`` is the one command that calls a model: it asks it for the required fields that failed in the run's documents
(:func:`~formcast.extract.aipass.ai_pass`), one call per document with such a field, and stores the answers in
``OUT/ai.jsonl``, where every one reads as ``unsure`` until it is reviewed. The model comes from the environment
(``FORMCAST_EXTRACT_BASE_URL``, ``_API_KEY``, ``_MODEL``, and ``_API_FORMAT`` when it is not ``chat_completions``);
a run with nothing to ask costs no call. It ends with a line of the documents asked about, answered and failed, and
the tokens spent.

``export`` takes the templates again with ``--template`` instead of reading them back from ``OUT``: ``run.json``
records only each template's id, name and sha256, and the field definitions need the schema. Pass the same templates
as the run: a template the run did not use, or one whose content differs from the run's (its sha256), is refused.

A bad argument, an unreadable or invalid template, any other :class:`FormcastError` (a missing extra, a column
clash) or a file error (``--out`` or ``--to`` is a file, or not writable) prints one line to stderr and exits with 2;
success exits with 0. The pool's workers start by spawn on macOS and import this module again, so everything runs
from ``main`` behind ``if __name__ == "__main__"``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from formcast.errors import FormcastError
from formcast.extract.batch import TIMEOUT, Results, RunSummary, run_batch
from formcast.extract.export import export
from formcast.extract.model import ExtractionTemplate
from formcast.extract.scan import ScanGroup, sample, scan

EXAMPLES = 3  # example files printed per group
PROGRESS_EVERY = 100
AI_PROGRESS_EVERY = 10  # the AI pass asks about few documents, each a model call
MODEL_PREFIX = "FORMCAST_EXTRACT"  # the environment variables of the model: FORMCAST_EXTRACT_BASE_URL, ...


class _Parser(argparse.ArgumentParser):
    """Argument errors exit with 2 and one line, like every other failure here."""

    def error(self, message: str) -> None:
        self.exit(2, f"{self.prog}: error: {message}\n")


def _workers(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = -1
    if n < 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not a worker count (0 for one per CPU, or 1 or more)")
    return n


def _timeout(text: str) -> float:
    try:
        seconds = float(text)
    except ValueError:
        seconds = 0.0
    if not seconds > 0 or seconds == float("inf"):
        raise argparse.ArgumentTypeError(f"{text!r} is not a timeout (a positive number of seconds)")
    return seconds


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m formcast.extract",
        description="Read filled PDFs of one layout; only the ai command calls a model.",
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    p = commands.add_parser("scan", help="group a folder of PDFs by layout")
    p.add_argument("dir", type=Path, help="folder of PDFs (searched recursively)")
    p.add_argument("--first-page", action="store_true", help="read only each document's first page (faster)")

    p = commands.add_parser("run", help="read a folder of PDFs with templates")
    p.add_argument("templates", type=Path, nargs="+", metavar="TEMPLATE.json", help="extraction template files")
    p.add_argument("dir", type=Path, help="folder of PDFs (searched recursively)")
    p.add_argument("--out", type=Path, required=True, help="output folder; a run already there is resumed")
    p.add_argument("--workers", type=_workers, default=0, help="worker processes (default: one per CPU)")
    p.add_argument(
        "--timeout", type=_timeout, default=TIMEOUT,
        help=f"seconds a document may take before it is set aside as hung (default {TIMEOUT:g}; not with --workers 1)",
    )  # fmt: skip

    p = commands.add_parser("export", help="write a finished run as xlsx, Parquet or CSV")
    p.add_argument("out", type=Path, help="the run's output folder")
    p.add_argument("--template", type=Path, nargs="+", required=True, help="the templates the run used")
    p.add_argument("--format", choices=("xlsx", "parquet", "csv"), required=True)
    p.add_argument("--to", type=Path, required=True, help="the workbook (xlsx) or the folder (parquet, csv) to write")

    p = commands.add_parser("ai", help="ask a model for the required fields that failed in a finished run")
    p.add_argument("out", type=Path, help="the run's output folder")
    p.add_argument(
        "--template", type=Path, nargs="+", action="extend", required=True, help="the templates the run used"
    )
    return parser


def _pdfs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise FormcastError(f"{folder} is not a folder")
    found = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf" and p.is_file())
    if not found:
        raise FormcastError(f"no PDFs in {folder}")
    return found


def _template(path: Path) -> ExtractionTemplate:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FormcastError(f"cannot read template {path}: {exc.strerror or exc}") from None
    try:
        return ExtractionTemplate.model_validate_json(text)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        detail = f"{where}: {first['msg']}" if where else first["msg"]
        raise FormcastError(f"{path} is not an extraction template: {detail}") from None


def _print_group(group: ScanGroup, folder: Path) -> None:
    close = "" if group.closeness is None else f", closeness {group.closeness:.2f} to group 1"
    print(
        f"group {group.id}: {len(group.files)} documents, {group.fillable} fillable, {group.flattened} flattened, "
        f"{group.page_count} {'page' if group.page_count == 1 else 'pages'}{close}"
    )
    for file in sample(group, min(EXAMPLES, len(group.files))):
        print(f"  {_relative(file, folder)}")


def _relative(file: str, folder: Path) -> str:
    try:
        return Path(file).relative_to(folder).as_posix()
    except ValueError:
        return file


def _scan(args: argparse.Namespace) -> None:
    report = scan(_pdfs(args.dir), first_page_only=args.first_page)
    read = sum(len(g.files) for g in report.groups)
    print(f"scanned {read + len(report.unreadable)} documents in {report.seconds:.1f} s")
    for group in report.groups:
        _print_group(group, args.dir)
    if report.unreadable:
        print(f"unreadable ({len(report.unreadable)}):")
        for file, reason in report.unreadable:
            print(f"  {_relative(file, args.dir)}: {reason}")


def _summary_line(s: RunSummary) -> str:
    return (
        f"total {s.total}, complete {s.complete}, with problems {s.with_problems}, no template {s.no_template}, "
        f"unreadable {s.unreadable}, internal errors {s.internal_errors}, {s.seconds:.1f} seconds"
    )


def _run(args: argparse.Namespace) -> None:
    templates = [_template(p) for p in args.templates]
    files = _pdfs(args.dir)

    def progress(done: int, total: int) -> None:
        if done % PROGRESS_EVERY == 0 and done != total:
            print(f"{done} of {total} documents", file=sys.stderr, flush=True)

    summary = run_batch(
        templates, files, args.out, root=args.dir, workers=args.workers, timeout=args.timeout, on_progress=progress
    )
    print(_summary_line(summary), file=sys.stderr, flush=True)


def _export(args: argparse.Namespace) -> None:
    templates = [_template(p) for p in args.template]
    if args.format == "xlsx" and args.to.is_dir():  # saving over a folder fails inside openpyxl, with noise
        raise FormcastError(f"{args.to} is a folder; xlsx export writes a file")
    results = Results.load(args.out)
    results.check_templates(templates)  # the templates the run read with, unchanged
    for path in export(results, templates, args.to, format=args.format):
        print(path)


def _ai(args: argparse.Namespace) -> None:
    from formcast.extract.aipass import ai_pass  # a model client is not needed by the other commands
    from formcast.llm import LLMConfig

    llm = LLMConfig.from_env(MODEL_PREFIX)
    templates = [_template(p) for p in args.template]

    def progress(done: int, total: int) -> None:
        if done % AI_PROGRESS_EVERY == 0 and done != total:
            print(f"{done} of {total} documents", file=sys.stderr, flush=True)

    s = ai_pass(templates, args.out, llm, on_progress=progress)
    print(
        f"asked about {s.documents} documents, {s.answered} answered, {s.failed} failed, "
        f"{s.usage.input_tokens} tokens in, {s.usage.output_tokens} tokens out",
        file=sys.stderr, flush=True,
    )  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; the exit code (0, or 2 with a line on stderr for bad arguments or a ``FormcastError``)."""
    args = _parser().parse_args(argv)
    try:
        {"scan": _scan, "run": _run, "export": _export, "ai": _ai}[args.command](args)
    except (FormcastError, OSError) as exc:  # OSError: --out or --to is a file, unwritable, and the like
        print(f"{_parser().prog}: {' '.join(str(exc).split())}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
