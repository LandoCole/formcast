"""PDFs filled in the browser (``core/fill/``, pdf-lib) read back correctly with PyMuPDF.

``fill_to_disk.mjs`` fills the generated rotated-page and crop-box fixtures with the hand templates
(the probe, Latin-1 and non-Latin records). Every widget the browser created must sit at its
template rect (within 0.5 pt), which pins page rotation and crop-box handling, and hold the value
its report states.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests" / "web" / "fixtures" / "fill.json").read_text(encoding="utf-8"))
CASES = [c for c in FIXTURE["cases"] if c["id"].split("/")[0] in ("rotated90", "cropbox", "cropbox-table")]
TOLERANCE = 0.5


def _template(name: str) -> dict:
    stored = FIXTURE["templates"][name]
    if "template" in stored:
        return stored["template"]
    return json.loads((ROOT / stored["file"]).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def filled(tmp_path_factory) -> dict[str, tuple[bytes, dict]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    out = tmp_path_factory.mktemp("browser_fill")
    ids = [c["id"] for c in CASES]
    proc = subprocess.run([node, "tests/web/fill_to_disk.mjs", str(out), *ids], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return {
        id: ((out / f"{n}.pdf").read_bytes(), json.loads((out / f"{n}.json").read_text(encoding="utf-8")))
        for n, id in enumerate(ids)
    }


def test_cases_cover_rotation_crop_box_and_non_latin_records():
    assert {c["input"]["pdf"].rsplit("/", 1)[1] for c in CASES} == {"rotated90.pdf", "cropbox.pdf"}
    text = json.dumps([c["input"]["data"] for c in CASES], ensure_ascii=False)
    for s in ("Sí", "José", "Zürich", "北京"):
        assert s in text


def _expected(report: dict, template: dict) -> list[tuple[str, tuple, object]]:
    """``(widget name, rect, value)`` for every widget the browser created."""
    rects = {f["id"]: f["rect"] for f in template["fields"] if f["type"] != "table"}
    out = [(f["widget"], tuple(rects[f["id"]]), f["value"]) for f in report["fields"] if f["binding"] == "created"]
    out += [(c["widget"], tuple(c["rect"]), c["value"]) for t in report["tables"] for c in t["cells"]
            if c["binding"] == "created"]  # fmt: skip
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_widgets_sit_at_the_template_rects_and_hold_the_reported_values(filled, case):
    pdf, report = filled[case["id"]]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[0]
    assert page.rotation == (90 if case["input"]["pdf"].endswith("rotated90.pdf") else 0)
    widgets = {w.field_name: w for w in page.widgets()}
    expected = _expected(report, _template(case["input"]["template"]))
    assert expected and len(widgets) == len(expected)
    for name, rect, value in expected:
        w = widgets[name]
        got = (w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1)
        assert all(abs(a - b) <= TOLERANCE for a, b in zip(got, rect)), f"{name}: {got} vs {rect}"
        if isinstance(value, bool):
            assert w.field_type_string == "CheckBox", name
            assert (w.field_value not in (False, "Off", "")) == value, f"{name}: {w.field_value!r}"
        else:
            assert w.field_type_string == "Text", name
            assert (w.field_value or None) == value, f"{name}: {w.field_value!r} vs {value!r}"
