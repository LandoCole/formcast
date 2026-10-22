"""demo/tools/build_showcase.py: the sample bundles and results.json.

The results tests read only committed files in eval/results/. Every test in this module runs with
``open``, ``Path.read_*`` and ``pymupdf.open`` set to fail on a path under forms/heldout/, so a
builder that touched the held-out split would be caught. The rebuild of the bundles runs FFDNet-S
(the weights come from Hugging Face on first use), so that one test is marked slow; the committed
bundles are checked by the fast tests.
"""

from __future__ import annotations

import builtins
import io
import json
import sys
from pathlib import Path

import jsonschema
import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo" / "tools"))

import build_showcase as bs  # noqa: E402
import score  # noqa: E402  (eval/score.py; build_showcase put eval/ on the path)

HELDOUT = (ROOT / "forms" / "heldout").resolve()
FILES = (
    "manifest.json", "form.pdf", "detections.json", "original-fields.json", "schema.json",
    "record.json", "published.pdf", "agent-template.json", "agent-scores.json",
)  # fmt: skip


@pytest.fixture(autouse=True)
def no_heldout(monkeypatch):
    """Fail any attempt to open something under forms/heldout/."""

    def guard(path):
        if isinstance(path, (str, Path)) and Path(path).resolve().is_relative_to(HELDOUT):
            raise AssertionError(f"opened held-out data: {path}")

    real_open, real_io_open, real_pymupdf_open = builtins.open, io.open, pymupdf.open

    def guarded(real):
        def wrapper(*args, **kwargs):
            guard(args[0] if args else kwargs.get("filename"))
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(builtins, "open", guarded(real_open))
    monkeypatch.setattr(io, "open", guarded(real_io_open))
    monkeypatch.setattr(pymupdf, "open", guarded(real_pymupdf_open))


def test_the_guard_catches_a_heldout_open():
    with pytest.raises(AssertionError, match="held-out"):
        (ROOT / "forms" / "heldout" / "anything.pdf").read_bytes()
    with pytest.raises(AssertionError, match="held-out"):
        pymupdf.open(ROOT / "forms" / "heldout" / "anything.pdf")


# ---------------------------------------------------------------- results.json


def load(name: str):
    return json.loads((ROOT / "eval" / "results" / name).read_text(encoding="utf-8"))


NUMBER = {"type": "number", "minimum": 0, "maximum": 1}
DETECTION_ROW = {
    "type": "object",
    "required": ["kind", "variant", "detector", "recall_50", "recall_30", "precision_30", "gt"],
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string"}, "variant": {"type": "string"}, "detector": {"type": "string"},
        "recall_50": NUMBER, "recall_30": NUMBER, "precision_30": NUMBER, "gt": {"type": "integer", "minimum": 1},
    },
}  # fmt: skip
RESULTS_SCHEMA = {
    "type": "object",
    "required": ["generated_from", "detection", "browser_models", "agent_dev", "heldout_status", "caveats"],
    "additionalProperties": False,
    "properties": {
        "generated_from": {"type": "array", "items": {"type": "string", "pattern": "^eval/results/"}, "minItems": 1},
        "detection": {
            "type": "object",
            "required": ["dev", "heldout"],
            "additionalProperties": False,
            "properties": {"dev": {"type": "array", "items": DETECTION_ROW, "minItems": 1},
                           "heldout": {"type": "array", "items": DETECTION_ROW, "minItems": 1}},
        },  # fmt: skip
        "browser_models": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["kind", "variant", "detector", "recall_50", "recall_30", "precision_30"],
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"}, "variant": {"type": "string"},
                    "detector": {"enum": ["FFDNet-L", "FFDNet-L-onnx", "FFDNet-S-onnx"]},
                    "recall_50": NUMBER, "recall_30": NUMBER, "precision_30": NUMBER,
                },
            },
        },  # fmt: skip
        "agent_dev": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["run", "model", "label", "group", "sessions", "recall_50", "precision_30", "path", "joint",
                             "fill_acc", "steps", "input_tokens"],
                "additionalProperties": False,
                "properties": {
                    "run": {"type": "string"}, "model": {"type": "string"}, "label": {"type": "string"},
                    "group": {"enum": ["fillable", "digital", "scanned", "all"]},
                    "sessions": {"type": "integer", "minimum": 0},
                    "recall_50": NUMBER, "precision_30": NUMBER, "path": NUMBER, "joint": NUMBER,
                    "fill_acc": {"anyOf": [NUMBER, {"type": "null"}]},
                    "steps": {"type": "number", "minimum": 0}, "input_tokens": {"type": "number", "minimum": 0},
                },
            },
        },  # fmt: skip
        "heldout_status": {
            "type": "object",
            "required": ["sessions_planned", "sessions_completed", "luna_heldout", "note"],
            "additionalProperties": False,
            "properties": {"sessions_planned": {"type": "integer"}, "sessions_completed": {"type": "integer"},
                           "luna_heldout": {"type": "boolean"}, "note": {"type": "string", "minLength": 1}},
        },  # fmt: skip
        "caveats": {"type": "array", "items": {"type": "string"}, "minItems": 2},
    },
}  # fmt: skip


@pytest.fixture(scope="module")
def results() -> dict:
    return bs.build_results()


def agent_row(results: dict, run: str, group: str) -> dict:
    return next(r for r in results["agent_dev"] if r["run"] == run and r["group"] == group)


def test_results_match_the_shape(results):
    jsonschema.validate(results, RESULTS_SCHEMA)


def test_every_source_file_exists(results):
    for name in results["generated_from"]:
        assert (ROOT / name).is_file(), name
    assert not any("heldout" in n and n.startswith("eval/results/agent_") for n in results["generated_from"])


def test_the_headline_row_equals_the_committed_totals(results):
    totals = load("agent_anthropic_eval_dev_2026-10-21_defer-placement.json")["totals"]["all"]
    row = agent_row(results, "2026-10-21_defer-placement", "all")
    assert row["sessions"] == totals["forms"] == 24
    assert row["recall_50"] == totals["detection"]["iou0.5"]["recall"]
    assert row["precision_30"] == totals["detection"]["iou0.3"]["precision"]
    assert row["path"] == totals["mapping"]["path_acc"]
    assert row["joint"] == totals["mapping"]["joint_acc"]
    assert row["fill_acc"] == totals["fill"]["fill_acc"]
    assert row["steps"] == round(totals["steps"] / 24, 1)
    assert row["input_tokens"] == round(totals["input_tokens"] / 24)
    # the figures main spec 14.3 and plan 3 ruling B quote
    assert (round(row["recall_50"], 2), round(row["path"], 2), round(row["joint"], 2)) == (0.66, 0.63, 0.55)
    assert round(row["fill_acc"], 2) == 0.58


def test_the_headline_groups_match_ruling_b(results):
    groups = {g: agent_row(results, "2026-10-21_defer-placement", g) for g in ("fillable", "digital", "scanned")}
    assert {g: r["sessions"] for g, r in groups.items()} == {"fillable": 7, "digital": 5, "scanned": 12}
    assert {g: round(r["fill_acc"], 2) for g, r in groups.items()} == {"fillable": 0.93, "digital": 0.58, "scanned": 0.45}


def test_round3_gen_matches_the_spec_table(results):
    row = agent_row(results, "2026-10-21_round3-gen", "all")
    assert (round(row["path"], 2), round(row["joint"], 2), round(row["fill_acc"], 2)) == (0.70, 0.64, 0.63)
    by_group = {g: round(agent_row(results, "2026-10-21_round3-gen", g)["fill_acc"], 2) for g in ("fillable", "digital", "scanned")}
    assert by_group == {"fillable": 0.98, "digital": 0.61, "scanned": 0.52}


def test_the_seed_row_has_no_mapping(results):
    row = agent_row(results, "2026-10-21_seed-fill", "all")
    assert (row["path"], row["joint"], row["fill_acc"], row["steps"], row["input_tokens"]) == (0, 0, 0, 0, 0)
    assert agent_row(results, "2026-10-21_seed-fill", "fillable")["recall_50"] == 1.0


def test_partial_luna_round_counts_only_finished_sessions(results):
    assert agent_row(results, "2026-10-21_round2", "all")["sessions"] == 14
    assert agent_row(results, "2026-10-21_round1", "all")["sessions"] == 24
    assert agent_row(results, "2026-10-21_round1", "all")["model"] == "gpt-6-luna"


def test_heldout_status_and_caveats(results):
    status = results["heldout_status"]
    assert status["sessions_planned"] == 9
    assert status["sessions_completed"] == 1
    assert status["luna_heldout"] is False
    assert "usage limit" in status["note"]
    assert results["caveats"] == [
        "Dev results were tuned on over four Haiku rounds.",
        "Haiku varies about ±0.1 path per form between runs.",
    ]


def test_detection_tables_come_from_the_baseline_files(results):
    dev = {(r["kind"], r["variant"], r["detector"]): r for r in results["detection"]["dev"]}
    # main spec 14.3: the bare FFDNet-L on fillable forms as published, 0.81 at IoU .5
    assert round(dev[("fillable", "as_published", "FFDNet-L")]["recall_50"], 2) == 0.81
    assert {r["split"] for r in load("baseline_detection_2026-10-21_heldout.json")} == {"heldout"}
    assert {r["kind"] for r in results["detection"]["heldout"]} == {"fillable", "flat", "scanned"}
    models = {(r["kind"], r["variant"], r["detector"]): r for r in results["browser_models"]}
    # the totals table in baseline_detection_2026-10-21_onnx.md
    assert round(models[("fillable", "as_published", "FFDNet-S-onnx")]["recall_50"], 2) == 0.79
    assert round(models[("flat", "fake_scan", "FFDNet-L-onnx")]["recall_30"], 2) == 0.33


def test_the_committed_results_json_is_current(results):
    assert json.loads(bs.RESULTS_JSON.read_text(encoding="utf-8")) == results


# ---------------------------------------------------------------- sample bundles


def widgets(doc: pymupdf.Document) -> int:
    return sum(len(list(page.widgets() or [])) for page in doc)


def check_bundle(folder: Path, form_id: str) -> dict:
    """Assert everything a bundle must satisfy; returns its detections."""
    assert sorted(p.name for p in folder.iterdir()) == sorted(FILES)
    for name in FILES:
        assert (folder / name).stat().st_size > 0, name
    manifest = json.loads((folder / "manifest.json").read_text())
    entry = bs.form_entry(form_id)
    assert manifest["id"] == form_id
    assert manifest["source_url"] == entry["source"]
    assert manifest["revision"] == entry["revision"]
    assert manifest["license"] == "US federal government work (public domain)"

    stripped = pymupdf.open(folder / "form.pdf")
    published = pymupdf.open(folder / "published.pdf")
    template = json.loads((folder / "agent-template.json").read_text())
    try:
        for doc in (stripped, published):
            assert not doc.is_encrypted  # pdf-lib cannot fill an encrypted file
            assert doc.page_count == manifest["pages"]
        assert widgets(stripped) == 0
        assert published.page_count == template["form"]["page_count"]
        assert [[p.rect.width, p.rect.height] for p in published] == [list(s) for s in template["form"]["page_sizes"]]

        originals = json.loads((folder / "original-fields.json").read_text())["pages"]
        scored = sum(len(v) for v in originals.values())
        assert scored == len(score.load_gt(entry, ROOT / "forms" / "dev" / f"{form_id}.pdf"))
        for page in published:  # the published file keeps its widgets
            assert len(bs.own_fields(page)) == len(originals.get(str(page.number), []))
        assert widgets(published) >= scored > 0

        detections = json.loads((folder / "detections.json").read_text())
        assert detections["model"] == "FFDNet-S-onnx"
        assert sorted(detections["pages"], key=int) == sorted(originals, key=int)
        for key, boxes in detections["pages"].items():
            width, height = stripped[int(key)].rect.width, stripped[int(key)].rect.height
            assert boxes
            for box in boxes:
                x0, y0, x1, y1 = box["rect"]
                assert 0 <= x0 < x1 <= width + 0.01 and 0 <= y0 < y1 <= height + 0.01, (key, box)
                assert box["kind"] in ("text", "choice", "signature")
                assert 0.3 <= box["conf"] <= 1
    finally:
        stripped.close()
        published.close()

    schema = json.loads((folder / "schema.json").read_text())
    jsonschema.validate(json.loads((folder / "record.json").read_text()), schema)
    assert schema == json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text())
    scores = json.loads((folder / "agent-scores.json").read_text())
    assert scores["run"] == "2026-10-21_defer-placement"
    assert scores["form"] == form_id and scores["variant"] == "as_published"
    assert scores["model_label"] == "Haiku 5.5"
    for name in ("form.pdf", "published.pdf"):
        assert (folder / name).stat().st_size < 2 * 1024 * 1024, name
    return detections


@pytest.mark.parametrize("form_id", bs.DEFAULT_FORMS)
def test_the_committed_bundle_is_complete(form_id):
    check_bundle(bs.SAMPLES / form_id, form_id)


def test_the_committed_index_lists_the_bundles_in_order():
    index = json.loads((bs.SAMPLES / "index.json").read_text())
    assert [e["id"] for e in index] == list(bs.DEFAULT_FORMS)
    for e in index:
        manifest = json.loads((bs.SAMPLES / e["id"] / "manifest.json").read_text())
        assert e == {k: manifest[k] for k in ("id", "title", "revision", "source_url", "pages")}


def test_the_agent_template_is_the_archived_one_byte_for_byte():
    for form_id in bs.DEFAULT_FORMS:
        assert (bs.SAMPLES / form_id / "agent-template.json").read_bytes() == bs.archived_template(form_id)


def test_every_record_is_fictional_and_fits_its_schema():
    for form_id in bs.DEFAULT_FORMS:
        record = json.loads((bs.RECORDS / f"{form_id}.json").read_text())
        jsonschema.validate(record, json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text()))
        text = json.dumps(record)
        assert "example" in text.lower()


def test_the_stripped_pdf_has_no_encryption_even_when_the_source_has_it(tmp_path):
    src = tmp_path / "locked.pdf"
    doc = pymupdf.open(ROOT / "forms" / "dev" / "fw9.pdf")
    doc.save(src, encryption=pymupdf.PDF_ENCRYPT_AES_128, owner_pw="owner", user_pw="")
    doc.close()
    pdfs = bs.split_pdf(src)
    assert pdfs["was_encrypted"] is True
    for name in ("published", "stripped"):
        out = pymupdf.open(stream=pdfs[name])
        assert not out.is_encrypted
        out.close()
    assert pdfs["originals"]  # the widgets were read through the empty user password


@pytest.mark.slow
def test_rebuilding_the_bundles_reproduces_the_committed_detections(tmp_path):
    entries = bs.build_samples(bs.DEFAULT_FORMS, out=tmp_path)
    assert [e["id"] for e in entries] == list(bs.DEFAULT_FORMS)
    for form_id in bs.DEFAULT_FORMS:
        detections = check_bundle(tmp_path / form_id, form_id)
        committed = json.loads((bs.SAMPLES / form_id / "detections.json").read_text())
        assert detections == committed
