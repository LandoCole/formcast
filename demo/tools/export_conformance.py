"""Write the conformance fixtures that hold the JavaScript ports to the Python library.

Every case in ``conformance_cases.py`` is run through the real ``formcast`` code and the
answer (or the error message) is stored in ``tests/web/fixtures/<group>.json``. The browser
modules are tested against those files, so Python stays the reference.

    python demo/tools/export_conformance.py            # rewrite the fixtures
    python demo/tools/export_conformance.py --check    # exit 1 and name any stale file

Each group is one function that returns one fixture document; add a group by writing a
function and listing it in ``GROUPS``. Output is deterministic: the code builds every
dict in a fixed key order, no timestamps, ``indent=1``. Keys are deliberately not sorted,
because the order of a schema's ``properties`` decides the order of the flattened paths.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import json
import shutil
import sys
import types
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))  # run this checkout's formcast, not an installed copy
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conformance_cases as cases  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from formcast import schema as schema_mod  # noqa: E402
from formcast import transforms as transforms_mod  # noqa: E402
from formcast.errors import FormcastError, OpError  # noqa: E402
from formcast.render.fill import fill as fill_pdf  # noqa: E402
from formcast.render import report as report_mod  # noqa: E402
from formcast.render import values as values_mod  # noqa: E402
from formcast.template import ops as ops_mod  # noqa: E402
from formcast.template.model import Template  # noqa: E402

GENERATED_BY = "demo/tools/export_conformance.py"
OPS_CLOCK = "2026-10-21T00:00:00Z"  # the timestamp every recorded history entry carries
DEFAULT_OUT_DIR = "tests/web/fixtures"

_MAX_SAFE_INT = 2**53

#: Inputs of the fill group, committed beside the fixtures: archived templates and generated PDFs.
FILL_INPUTS = ROOT / DEFAULT_OUT_DIR
#: Report codes the browser writer never produces (they need MuPDF or a JSON Schema validator).
BROWSER_SKIPS = frozenset({"font_substituted", "adopted", "invalid"})


# ------------------------------------------------------------------- helpers


def _plain(value: Any) -> Any:
    """Make a Python result JSON-ready: dates as ISO strings, Decimals as strings, models dumped."""
    if isinstance(value, BaseModel):
        return _plain(value.model_dump())
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def _check_lossless(value: Any, where: str) -> None:
    """Refuse case inputs that JSON would change on the way to JavaScript."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INT:
            raise ValueError(f"{where}: integer {value} is beyond 2**53; use a numeric string")
    elif isinstance(value, float):
        if value.is_integer() and abs(value) < 1e16:
            raise ValueError(f"{where}: float {value!r} would read back as an integer in JavaScript")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_lossless(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_lossless(v, f"{where}[{i}]")
    else:
        raise ValueError(f"{where}: {type(value).__name__} is not a JSON value")


def _check_unique_ids(all_cases: list[dict]) -> None:
    seen: set[str] = set()
    for case in all_cases:
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']!r}")
        seen.add(case["id"])


def _record(case: dict, input_: dict, run: Callable[[], Any], errors: tuple[type[Exception], ...]) -> dict:
    """Run one case and return ``{"id", "kind", "input", "output" | "error"}``."""
    _check_lossless(input_, case["id"])
    out: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "input": input_}
    try:
        out["output"] = _plain(run())
    except errors as e:
        out["error"] = str(e)
    return out


# -------------------------------------------------------------------- groups


def export_transforms() -> dict:
    """Fixture for ``transforms.js``: chains applied and validated, plus the catalogue."""
    _check_unique_ids(cases.TRANSFORM_CASES)
    records = []
    for case in cases.TRANSFORM_CASES:
        chain = [transforms_mod.Transform(op=s["op"], args=s["args"]) for s in case["chain"]]
        if case["kind"] == "apply":
            input_ = {"values": case["values"], "chain": case["chain"]}
            records.append(_record(case, input_, lambda c=chain, v=case["values"]: transforms_mod.apply_chain(v, c),
                                   (FormcastError,)))
        else:
            input_ = {"chain": case["chain"]}
            records.append(_record(case, input_, lambda c=chain: transforms_mod.validate_chain(c), ()))
    listing = transforms_mod.list_transforms()
    catalogue = [
        {
            "name": e["name"],
            "args": e["args_schema"],
            "takesList": transforms_mod._REGISTRY[e["name"]].takes_list,
            "doc": e["doc"],
        }
        for e in listing
    ]
    return {"generated_by": GENERATED_BY, "catalogue": catalogue, "cases": records}


def _segments(raw: list) -> list[str]:
    return [schema_mod.ARRAY_ITEM if s == cases.A_ITEM else s for s in raw]


def _plain_segments(segments: list[str]) -> list:
    return [cases.A_ITEM if schema_mod.is_array_item(s) else str(s) for s in segments]


def export_schema() -> dict:
    """Fixture for ``schema.js``: ``flatten_schema``, ``parse_path`` and ``format_path``."""
    all_cases = cases.SCHEMA_CASES + cases.dev_schema_cases(ROOT)
    _check_unique_ids(all_cases)
    records = []
    for case in all_cases:
        kind = case["kind"]
        if kind == "flatten":
            records.append(_record(case, {"schema": case["schema"]},
                                   lambda s=case["schema"]: schema_mod.flatten_schema(s), (FormcastError,)))
        elif kind == "parse_path":
            records.append(_record(case, {"path": case["path"]},
                                   lambda p=case["path"]: _plain_segments(schema_mod.parse_path(p)), (ValueError,)))
        elif kind == "format_path":
            records.append(_record(case, {"segments": case["segments"]},
                                   lambda s=case["segments"]: schema_mod.format_path(_segments(s)), ()))
        else:
            raise ValueError(f"unknown schema case kind {kind!r}")
    return {"generated_by": GENERATED_BY, "cases": records}


@contextlib.contextmanager
def _fixed_clock():
    """Make ``ops.apply`` stamp history entries with ``OPS_CLOCK`` (it reads ``datetime.datetime.now``)."""
    stamp = types.SimpleNamespace(isoformat=lambda: OPS_CLOCK)
    fake = types.SimpleNamespace(UTC=datetime.UTC, datetime=types.SimpleNamespace(now=lambda tz=None: stamp))
    real = ops_mod.datetime
    ops_mod.datetime = fake
    try:
        yield
    finally:
        ops_mod.datetime = real


def export_ops() -> dict:
    """Fixture for ``ops.js``: the browser edit ops applied to small templates through ``ops.apply``."""
    _check_unique_ids(cases.OPS_CASES)
    templates = {name: Template.model_validate(raw) for name, raw in cases.OPS_TEMPLATES.items()}
    records = []
    for case in cases.OPS_CASES:
        input_ = {"template": case["template"], "ops": case["ops"], "author": case["author"], "note": case["note"]}

        def run(case=case) -> dict:
            t, warnings = templates[case["template"]], []
            for op in case["ops"]:
                t, warnings = ops_mod.apply(t, op, author=case["author"], note=case["note"], context=None)
            dumped = t.model_dump(by_alias=True)
            return {"fields": dumped["fields"], "history": dumped["history"], "warnings": warnings}

        with _fixed_clock():
            records.append(_record(case, input_, run, (OpError,)))
    stored = {name: t.model_dump(by_alias=True) for name, t in templates.items()}
    return {"generated_by": GENERATED_BY, "at": OPS_CLOCK, "templates": _plain(stored), "cases": records}


def export_values() -> dict:
    """Fixture for ``values.js``: ``resolve`` on small templates and records.

    The browser cannot validate a record, so every case must be schema-valid: the export fails if
    Python reports a record-level ``invalid``. Warnings are sorted by ``(code, path, field)`` so the
    comparison does not depend on how each side orders them.
    """
    _check_unique_ids(cases.VALUES_CASES)
    templates = {name: Template.model_validate(raw) for name, raw in cases.VALUES_TEMPLATES.items()}
    records = []
    for case in cases.VALUES_CASES:
        input_ = {"template": case["template"], "data": case["data"]}

        def run(case=case) -> dict:
            fields, tables, warnings = values_mod.resolve(templates[case["template"]], case["data"])
            invalid = [w.message for w in warnings if w.code == "invalid"]
            if invalid:
                raise ValueError(f"{case['id']}: the record is not schema-valid: {invalid}")
            return {
                "fields": {k: dataclasses.asdict(v) for k, v in fields.items()},
                "tables": {k: dataclasses.asdict(v) for k, v in tables.items()},
                "warnings": sorted(warnings, key=lambda w: (w.code, w.path or "", w.field or "")),
            }

        records.append(_record(case, input_, run, ()))
    stored = {name: t.model_dump(by_alias=True) for name, t in templates.items()}
    return {"generated_by": GENERATED_BY, "severity": dict(report_mod.SEVERITY), "templates": _plain(stored),
            "cases": records}


def make_fill_pdf(spec: dict) -> bytes:
    """One Letter page with the case labels, rotated and/or with a crop box, or a dev form saved
    without its encryption (``FILL_PDFS``)."""
    import pymupdf

    if "source" in spec:
        return pymupdf.open(ROOT / spec["source"]).tobytes(garbage=3, deflate=True, no_new_id=True)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in cases.FILL_PDF_LABELS:
        page.insert_text((x, y), text, fontname="helv", fontsize=9)
    if spec["cropbox"] is not None:
        page.set_cropbox(pymupdf.Rect(spec["cropbox"]))
    if spec["rotation"]:
        page.set_rotation(spec["rotation"])
    doc.set_metadata({})
    return doc.tobytes(no_new_id=True, garbage=3)


def _fill_input_problems(spec: dict, data: bytes) -> str | None:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    if "source" in spec:
        source = pymupdf.open(ROOT / spec["source"])
        if doc.is_encrypted or doc.page_count != source.page_count:
            return f"encrypted or not a copy of {spec['source']}"
        return None
    page = doc[0]
    if page.rotation != spec["rotation"]:
        return f"rotation {page.rotation}, expected {spec['rotation']}"
    want = spec["cropbox"] or [0, 0, 612, 792]
    if [round(v, 3) for v in page.cropbox] != [float(v) for v in want]:
        return f"crop box {list(page.cropbox)}, expected {want}"
    return None


def ensure_fill_inputs(check: bool) -> list[str]:
    """Make sure the fill group's committed inputs exist; return the names that are missing or wrong.

    Archived templates are copied once from the extracted archive (``tar -xzf
    eval/results/templates.tar.gz``) and the PDFs are generated once; with ``check`` nothing is
    written, so the check never depends on the git-ignored extraction (plan Ruling 1).
    """
    bad: list[str] = []
    for name, source in cases.FILL_ARCHIVED.items():
        dest = FILL_INPUTS / "templates" / f"{name}.json"
        if dest.exists():
            continue
        if check:
            bad.append(str(dest.relative_to(ROOT)))
            continue
        src = ROOT / source
        if not src.exists():
            raise SystemExit(f"{source} is missing; run `tar -xzf eval/results/templates.tar.gz` first")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        print(f"copied {dest.relative_to(ROOT)}")
    for name, spec in cases.FILL_PDFS.items():
        dest = FILL_INPUTS / "pdfs" / name
        if dest.exists():
            problem = _fill_input_problems(spec, dest.read_bytes())
            if problem is None:
                continue
            if check:
                bad.append(f"{dest.relative_to(ROOT)} ({problem})")
                continue
        elif check:
            bad.append(str(dest.relative_to(ROOT)))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(make_fill_pdf(spec))
        print(f"wrote {dest.relative_to(ROOT)}")
    return bad


def _fill_templates() -> tuple[dict[str, Template], dict[str, dict]]:
    """Validated templates by name, and how the fixture stores each (a file path or the dump)."""
    templates: dict[str, Template] = {}
    stored: dict[str, dict] = {}
    for name in cases.FILL_ARCHIVED:
        path = FILL_INPUTS / "templates" / f"{name}.json"
        templates[name] = Template.model_validate_json(path.read_text(encoding="utf-8"))
        stored[name] = {"file": str(path.relative_to(ROOT))}
    for name, raw in cases.FILL_TEMPLATES.items():
        dumped = _plain(Template.model_validate(raw).model_dump(by_alias=True))
        templates[name] = Template.model_validate(dumped)  # fill what JavaScript reads: every style key set
        stored[name] = {"template": dumped}
    return templates, stored


def export_fill() -> dict:
    """Fixture for ``fill/index.js``: ``fill(..., check_fingerprint=False)`` on dev and generated PDFs.

    Recorded per case: each field's ``{id, binding, widget, value}``, each table cell's ``{table, row,
    key, value}``, and the top-level warning codes with their counts, without the codes the browser
    never produces (``BROWSER_SKIPS``). Records must be schema-valid, as for the values group.
    """
    _check_unique_ids(cases.FILL_CASES)
    templates, stored = _fill_templates()
    records = []
    for case in cases.FILL_CASES:
        input_ = {"template": case["template"], "pdf": case["pdf"], "data": case["data"]}

        def run(case=case) -> dict:
            pdf = (ROOT / case["pdf"]).read_bytes()
            _, report = fill_pdf(templates[case["template"]], pdf, case["data"], check_fingerprint=False)
            invalid = [w.message for w in report.warnings if w.code == "invalid" and w.page is None]
            if invalid:
                raise ValueError(f"{case['id']}: the record is not schema-valid: {invalid}")
            codes: dict[str, int] = {}
            for w in report.warnings:
                if w.code not in BROWSER_SKIPS:
                    codes[w.code] = codes.get(w.code, 0) + 1
            return {
                "fields": [{"id": f.id, "binding": f.binding, "widget": f.widget, "value": f.value} for f in report.fields],
                "cells": [{"table": t.id, "row": c.row, "key": c.key, "value": c.value}
                          for t in report.tables for c in t.cells],
                "codes": dict(sorted(codes.items())),
            }

        records.append(_record(case, input_, run, ()))
    return {"generated_by": GENERATED_BY, "skipped_codes": sorted(BROWSER_SKIPS), "templates": stored,
            "cases": records}


#: fixture file name -> function that builds its document
GROUPS: dict[str, Callable[[], dict]] = {
    "transforms.json": export_transforms,
    "schema.json": export_schema,
    "ops.json": export_ops,
    "values.json": export_values,
    "fill.json": export_fill,
}


# ---------------------------------------------------------------------- main


def render(doc: dict) -> str:
    """Deterministic JSON text for a fixture document."""
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main(out_dir: str | Path = DEFAULT_OUT_DIR, check: bool = False) -> int:
    """Write the fixtures to ``out_dir`` (or, with ``check``, compare and write nothing).

    Returns 0 when the files were written or are up to date, 1 when ``check`` finds stale or
    missing files (their names are printed to stderr).
    """
    out = Path(out_dir)
    if not out.is_absolute():
        out = ROOT / out
    missing_inputs = ensure_fill_inputs(check)
    stale: list[str] = list(missing_inputs)
    for name, build in GROUPS.items():
        if name == "fill.json" and missing_inputs:
            stale.append(name)  # its inputs are missing; nothing to compare against
            continue
        text = render(build())
        path = out / name
        if check:
            if not path.exists() or path.read_bytes() != text.encode("utf-8"):
                stale.append(name)
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    if stale:
        print("stale conformance fixtures (run demo/tools/export_conformance.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare in memory; exit 1 if any fixture is stale")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    sys.exit(main(args.out_dir, check=args.check))
