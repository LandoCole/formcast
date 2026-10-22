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
import datetime
import json
import sys
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
from formcast.errors import FormcastError  # noqa: E402

GENERATED_BY = "demo/tools/export_conformance.py"
DEFAULT_OUT_DIR = "tests/web/fixtures"

_MAX_SAFE_INT = 2**53


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


#: fixture file name -> function that builds its document
GROUPS: dict[str, Callable[[], dict]] = {
    "transforms.json": export_transforms,
    "schema.json": export_schema,
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
    stale: list[str] = []
    for name, build in GROUPS.items():
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
