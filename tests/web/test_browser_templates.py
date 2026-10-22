"""Templates edited in the browser (``ops.js``) are valid templates for the Python library.

``build_templates.mjs`` starts from the browser-style seed for ``forms/dev/fw9.pdf`` page 0 (no
fingerprint) and applies ten scripted ops to each of three templates. Python must then load each
one, replay its history from the seed to the same fields, and fill the PDF with it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from formcast.render.fill import fill
from formcast.template import ops
from formcast.template.model import Template

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "tests" / "web" / "fixtures" / "fw9_seed.json"
PDF = ROOT / "forms" / "dev" / "fw9.pdf"

RECORD = {
    "legal_name": "  Ada Lovelace ",
    "business_name": "Analytical Engines",
    "tax_classification": "c_corporation",
    "has_foreign_partners": True,
    "ssn": "123-45-6789",
    "certified_on": "2026-10-21",
    "remit_email": "ada@example.com",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    out = tmp_path_factory.mktemp("browser_templates") / "templates.json"
    proc = subprocess.run(
        [node, "tests/web/build_templates.mjs", str(out)], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return {name: json.dumps(doc) for name, doc in json.loads(out.read_text(encoding="utf-8")).items()}


@pytest.fixture(scope="module")
def seed() -> Template:
    return Template.model_validate_json(SEED.read_text(encoding="utf-8"))


NAMES = ["names", "taxid", "choices"]


def test_the_seed_has_no_fingerprint_and_no_history(seed):
    assert seed.form.fingerprint is None
    assert seed.history == []
    assert len(seed.fields) == 23


@pytest.mark.parametrize("name", NAMES)
def test_python_loads_the_template(built, name):
    t = Template.model_validate_json(built[name])
    assert len(t.history) == 10
    assert {h.author for h in t.history} == {"human"}
    assert t.form.fingerprint is None


@pytest.mark.parametrize("name", NAMES)
def test_replaying_the_history_reproduces_the_fields(built, seed, name):
    t = Template.model_validate_json(built[name])
    replayed = ops.replay(seed, t.history)
    assert replayed.fields == t.fields
    assert replayed.history == t.history


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("data", [{}, RECORD], ids=["empty-record", "record"])
def test_fill_runs_without_a_fingerprint(built, name, data):
    t = Template.model_validate_json(built[name])
    out, report = fill(t, PDF, data, check_fingerprint=False)
    assert out.startswith(b"%PDF")
    assert report is not None


def test_the_scripts_use_every_edit_the_demo_makes(built):
    used = {h.op["op"] for name in NAMES for h in Template.model_validate_json(built[name]).history}
    assert used >= {"add_field", "remove_field", "move", "resize", "set_label", "set_type", "set_source", "set_style"}
