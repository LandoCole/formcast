"""Placeholder records from ``placeholderRecord`` (JS) validate against the schemas they came from.

Only the dev-split form schemas are used (``forms/manifest.json`` ``split == "dev"``); held-out schemas
are never opened.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _dev_schema_files() -> list[Path]:
    manifest = json.loads((ROOT / "forms" / "manifest.json").read_text(encoding="utf-8"))
    return [ROOT / "forms" / "schemas" / f"{f['id']}.json" for f in manifest["forms"] if f["split"] == "dev"]


@pytest.fixture(scope="module")
def records() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    files = _dev_schema_files()
    proc = subprocess.run(
        [node, "tests/web/print_placeholders.mjs", *map(str, files)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_there_are_dev_schemas():
    assert len(_dev_schema_files()) >= 10


@pytest.mark.parametrize("path", _dev_schema_files(), ids=lambda p: p.stem)
def test_record_validates_against_its_schema(path, records):
    schema = json.loads(path.read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    validator = cls(schema, format_checker=cls.FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(records[path.name]), key=lambda e: list(map(str, e.absolute_path)))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
