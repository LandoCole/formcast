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
    "records.json", "agent-template.json", "agent-scores.json",
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
TEXT = {"type": "string", "minLength": 1}
DETECTION_ROW = {
    "type": "object",
    "required": ["kind", "variant", "label", "detector", "recall_50", "recall_30", "precision_30", "gt"],
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string"}, "variant": {"type": "string"}, "label": TEXT, "detector": {"type": "string"},
        "recall_50": NUMBER, "recall_30": NUMBER, "precision_30": NUMBER, "gt": {"type": "integer", "minimum": 1},
    },
}  # fmt: skip
LABELLED = {
    "type": "object",
    "additionalProperties": {
        "type": "object", "required": ["label", "text"], "additionalProperties": False,
        "properties": {"label": TEXT, "text": TEXT},
    },
}  # fmt: skip
RESULTS_SCHEMA = {
    "type": "object",
    "required": ["generated_from", "detection", "browser_models", "agent_dev", "heldout_status", "caveats",
                 "groups", "detectors", "metrics", "notes"],
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
                "required": ["kind", "variant", "label", "detector", "recall_50", "recall_30", "precision_30"],
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"}, "variant": {"type": "string"}, "label": TEXT,
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
                "required": ["run", "model", "label", "detail", "headline", "sessions_of", "group", "sessions",
                             "recall_50", "precision_30", "path", "joint", "fill_acc", "steps", "input_tokens"],
                "additionalProperties": False,
                "properties": {
                    "run": {"type": "string"}, "model": {"type": "string"}, "label": TEXT, "detail": TEXT,
                    "headline": {"type": "boolean"}, "sessions_of": {"type": ["string", "null"]},
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
            "required": ["sessions_planned", "sessions_completed", "luna_heldout", "note", "source"],
            "additionalProperties": False,
            "properties": {"sessions_planned": {"type": "integer"}, "sessions_completed": {"type": "integer"},
                           "luna_heldout": {"type": "boolean"}, "note": TEXT, "source": TEXT},
        },  # fmt: skip
        "caveats": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        "groups": {**LABELLED, "required": ["fillable", "digital", "scanned", "all"]},
        "detectors": {**LABELLED, "required": ["FFDNet-L", "FFDetr", "FFDNet-L-onnx", "FFDNet-S-onnx"]},
        "metrics": {**LABELLED, "required": ["recall_50", "recall_30", "precision_30", "path", "joint", "fill_acc",
                                             "steps", "input_tokens"]},
        "notes": {
            "type": "object",
            "required": ["averaging", "errored_sessions", "seed", "heldout_scan", "partial_runs", "bare_detector",
                         "browser_models"],
            "additionalProperties": False,
            "properties": {
                "averaging": TEXT, "errored_sessions": TEXT, "seed": TEXT, "heldout_scan": TEXT,
                "bare_detector": TEXT, "browser_models": TEXT,
                "partial_runs": {"type": "array", "items": TEXT},
            },
        },
    },
}  # fmt: skip


@pytest.fixture(scope="module")
def results() -> dict:
    return bs.build_results()


def agent_row(results: dict, run: str, group: str, sessions_of: str | None = None) -> dict:
    return next(
        r for r in results["agent_dev"] if r["run"] == run and r["group"] == group and r["sessions_of"] == sessions_of
    )


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
    assert status["source"] == "docs/superpowers/specs/2026-10-20-form-projection-design.md, section 14.3"
    assert (ROOT / status["source"].split(",")[0]).is_file()
    assert results["caveats"] == [
        "Dev results were tuned on over several rounds.",  # spec 10.3; several Haiku dev rounds came first
        "Haiku varies about ±0.1 path per form between runs.",
    ]


def test_only_the_headline_run_is_marked(results):
    marked = {r["run"] for r in results["agent_dev"] if r["headline"]}
    assert marked == {"2026-10-21_defer-placement"}
    assert agent_row(results, "2026-10-21_defer-placement", "all")["detail"] == "current code"


def test_the_partial_run_count_comes_from_the_data(results, monkeypatch):
    rows = load("agent_openai_eval_dev_2026-10-21_round2.json")["rows"]
    errored = sum(1 for r in rows if r.get("error"))
    assert (len(rows), errored) == (24, 10)
    row = agent_row(results, "2026-10-21_round2", "all")
    assert row["detail"].endswith("14 of 24 sessions")
    assert results["notes"]["partial_runs"] == [
        "Luna 6, round 2 is partial: the OpenAI credits ran out after 14 of 24 sessions, and the 10 that "
        "failed are left out. Compare it with round 1 on the same 14 sessions."
    ]
    # the count follows the file: one more errored row changes the label and the note
    real_load = bs._load

    def one_more_error(name):
        data = real_load(name)
        if name.endswith("_round2.json"):
            first_ok = next(r for r in data["rows"] if not r.get("error"))
            first_ok["error"] = "FormcastError: test"
        return data

    monkeypatch.setattr(bs, "_load", one_more_error)
    changed = bs.build_results()
    assert agent_row(changed, "2026-10-21_round2", "all")["detail"].endswith("13 of 24 sessions")
    assert "after 13 of 24 sessions, and the 11 that failed" in changed["notes"]["partial_runs"][0]
    assert agent_row(changed, "2026-10-21_round1", "all", sessions_of="2026-10-21_round2")["sessions"] == 13


def test_round_one_on_the_round_two_sessions_matches_the_spec(results):
    # main spec 14.3: "Luna round 1" on the 14 paired sessions
    row = agent_row(results, "2026-10-21_round1", "all", sessions_of="2026-10-21_round2")
    assert row["sessions"] == 14
    assert row["detail"] == "round 1 on the same 14 sessions"
    assert (round(row["recall_50"], 2), round(row["precision_30"], 2)) == (0.38, 0.81)
    assert (round(row["path"], 2), round(row["joint"], 2), round(row["fill_acc"], 2)) == (0.55, 0.48, 0.50)
    assert (row["steps"], round(row["input_tokens"] / 1000)) == (13.1, 233)


def test_explanations_name_the_groups_and_the_exclusions(results):
    groups = results["groups"]
    assert "as published" in groups["fillable"]["text"] and "as published" in groups["digital"]["text"]
    assert "fake-scan" in groups["scanned"]["text"]
    assert "native scan" in results["notes"]["heldout_scan"]
    assert "ended in an error" in results["notes"]["errored_sessions"]
    labels = {r["label"] for r in results["detection"]["heldout"] if r["kind"] == "scanned"}
    assert labels == {"Native scan"}


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
    assert manifest["kind"] == entry["kind"]
    assert manifest["license"] == bs.license_of(form_id)
    if form_id in bs.LICENSES:
        assert "public domain" not in manifest["license"]
    else:
        assert manifest["license"] == "US federal government work (public domain)"

    stripped = pymupdf.open(folder / "form.pdf")
    published = pymupdf.open(ROOT / "forms" / "dev" / f"{form_id}.pdf")
    template = json.loads((folder / "agent-template.json").read_text())
    try:
        assert not stripped.is_encrypted  # pdf-lib cannot fill an encrypted file
        assert stripped.page_count == manifest["pages"]
        assert widgets(stripped) == 0
        # Auto map pairs the agent's fields with boxes by position, so they share the page geometry
        assert stripped.page_count == template["form"]["page_count"]
        assert [[p.rect.width, p.rect.height] for p in stripped] == [list(s) for s in template["form"]["page_sizes"]]

        originals = json.loads((folder / "original-fields.json").read_text())["pages"]
        for page in published:
            assert len(bs.own_fields(page)) == len(originals.get(str(page.number), []))
        detections = json.loads((folder / "detections.json").read_text())
        assert detections["model"] == "FFDNet-S-onnx"
        if entry["kind"] == "fillable":
            scored = sum(len(v) for v in originals.values())
            assert scored == len(score.load_gt(entry, ROOT / "forms" / "dev" / f"{form_id}.pdf"))
            assert scored > 0
            assert sorted(detections["pages"], key=int) == sorted(originals, key=int)
        else:  # a flat, printed form: no fields of its own, detections on every page
            assert originals == {}
            assert sorted(detections["pages"], key=int) == [str(n) for n in range(stripped.page_count)]
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
    records = json.loads((folder / "records.json").read_text())
    assert 2 <= len(records) <= 3
    assert len({r["title"] for r in records}) == len(records)
    for r in records:
        assert set(r) == {"title", "record"} and r["title"]
        jsonschema.validate(r["record"], schema)
    assert schema == json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text())
    assert template["schema"] == schema  # Auto map is offered while the template keeps this schema
    scores = json.loads((folder / "agent-scores.json").read_text())
    assert set(scores) == set(bs.AGENT_SCORE_KEYS)  # no local paths or harness fields
    assert scores["run"] == "2026-10-21_defer-placement"
    assert scores["form"] == form_id and scores["variant"] == "as_published"
    assert scores["model_label"] == "Haiku 5.5"
    assert (folder / "form.pdf").stat().st_size < 2 * 1024 * 1024
    return detections


def test_agent_scores_keep_the_label_and_the_sessions_scores():
    # the Auto map note names model_label; the rest is the session's row, without local paths
    assert bs.AGENT_SCORE_KEYS == (
        "run", "model_label", "form", "variant", "model", "detection", "mapping", "fill",
        "steps", "input_tokens", "output_tokens", "wall_s", "finished",
    )  # fmt: skip
    scores = bs.agent_scores("fw9")
    assert list(scores) == list(bs.AGENT_SCORE_KEYS)
    assert scores["mapping"]["path_acc"] == bs.agent_row("fw9")["mapping"]["path_acc"]
    fixture = json.loads((ROOT / "tests" / "web" / "fixtures" / "sample-bundle" / "agent-scores.json").read_text())
    assert set(fixture) == set(bs.AGENT_SCORE_KEYS)


@pytest.mark.parametrize("form_id", bs.DEFAULT_FORMS)
def test_the_committed_bundle_is_complete(form_id):
    check_bundle(bs.SAMPLES / form_id, form_id)


def test_the_committed_index_lists_the_bundles_in_order():
    index = json.loads((bs.SAMPLES / "index.json").read_text())
    assert [e["id"] for e in index] == list(bs.DEFAULT_FORMS)
    for e in index:
        manifest = json.loads((bs.SAMPLES / e["id"] / "manifest.json").read_text())
        assert e == {k: manifest[k] for k in ("id", "title", "revision", "source_url", "pages")}


def schema_of(form_id: str) -> dict:
    return json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text())


def test_the_agent_template_is_the_archived_one_with_only_its_digit_splits_corrected():
    for form_id in bs.DEFAULT_FORMS:
        archived = bs.archived_template(form_id)
        bundled = (bs.SAMPLES / form_id / "agent-template.json").read_bytes()
        assert bundled == bs.correct_digit_splits(archived, schema_of(form_id))
        if form_id != "fw9":
            assert bundled == archived, form_id  # the other samples have no position split to correct
            continue
        before, after = json.loads(archived), json.loads(bundled)
        changed = [
            (a["source"]["paths"], [t["op"] for t in a["source"]["transforms"]], b["source"]["transforms"])
            for a, b in zip(before["fields"], after["fields"])
            if a != b
        ]
        assert changed == [
            (["ssn"], ["slice"], [{"op": "split", "args": {"sep": "-", "index": 0}}]),
            (["ssn"], ["slice"], [{"op": "split", "args": {"sep": "-", "index": 1}}]),
            (["ssn"], ["slice"], [{"op": "split", "args": {"sep": "-", "index": 2}}]),
            (["ein"], ["slice"], [{"op": "split", "args": {"sep": "-", "index": 0}}]),
            (["ein"], ["slice"], [{"op": "split", "args": {"sep": "-", "index": 1}}]),
        ]
        assert {k: v for k, v in before.items() if k != "fields"} == {k: v for k, v in after.items() if k != "fields"}


def test_correct_digit_splits_leaves_other_slices_alone():
    schema = {"type": "object", "properties": {"ein": {"type": "string", "pattern": r"^\d{2}-\d{7}$"}, "code": {"type": "string"}}}

    def template(path, start, end):
        tr = [{"op": "slice", "args": {"start": start, "end": end}}]
        return json.dumps({"fields": [{"id": "f", "source": {"paths": [path], "transforms": tr}}]}).encode()

    for unchanged in (template("code", 0, 1), template("ein", 0, 3), template("ein", 1, 2)):
        assert bs.correct_digit_splits(unchanged, schema) == unchanged
    fixed = json.loads(bs.correct_digit_splits(template("ein", 2, 9), schema))
    assert fixed["fields"][0]["source"]["transforms"] == [{"op": "split", "args": {"sep": "-", "index": 1}}]


def test_the_corrected_w9_template_fills_the_whole_ein_and_ssn():
    from formcast.render.fill import fill
    from formcast.template.model import Template

    template = Template.model_validate_json((bs.SAMPLES / "fw9" / "agent-template.json").read_text())
    records = json.loads((bs.SAMPLES / "fw9" / "records.json").read_text())
    by_ein = next(r["record"] for r in records if "ein" in r["record"])
    by_ssn = next(r["record"] for r in records if "ssn" in r["record"])
    parts = {}
    for record in (by_ein, by_ssn):
        out, _ = fill(template, ROOT / "forms" / "dev" / "fw9.pdf", record, check_fingerprint=False)
        doc = pymupdf.open(stream=out, filetype="pdf")
        values = {w.field_name: w.field_value for w in doc[0].widgets() or []}
        doc.close()
        for f in template.fields:
            if f.source and f.source.paths in (["ein"], ["ssn"]) and f.source.paths[0] in record:
                parts.setdefault(f.source.paths[0], []).append(values[f.existing_widget])
    assert "".join(parts["ein"]) == by_ein["ein"].replace("-", "")
    assert parts["ein"] == by_ein["ein"].split("-")  # 12 | 3456789, no lost digit
    assert parts["ssn"] == by_ssn["ssn"].split("-")


def test_every_record_is_fictional_and_fits_its_schema():
    for form_id in bs.DEFAULT_FORMS:
        records = json.loads((bs.RECORDS / f"{form_id}.json").read_text())
        assert 2 <= len(records) <= 3, form_id
        schema = json.loads((ROOT / "forms" / "schemas" / f"{form_id}.json").read_text())
        for r in records:
            jsonschema.validate(r["record"], schema)
            text = json.dumps(r["record"])
            assert "example" in text.lower(), r["title"]
            for ssn in (v for k, v in walk(r["record"]) if k == "ssn"):
                assert ssn.startswith("000"), (r["title"], ssn)  # an area number that is never issued


def walk(value, key=None):
    """Every (key, value) pair in nested JSON, leaves included."""
    yield key, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, key)


def test_write_bundle_refuses_records_without_their_own_titles(tmp_path):
    pdfs = {"stripped": b"%PDF-1.7", "originals": {}, "pages": 1}
    schema = {"type": "object"}
    for records in ([], [{"title": "", "record": {}}], [{"title": "a", "record": {}}, {"title": "a", "record": {}}]):
        with pytest.raises(ValueError, match="own title"):
            bs.write_bundle(tmp_path, bundle_id="x", form_id="fw9", title="x", pdfs=pdfs, detections={},
                            schema=schema, records=records, template=b"{}", scores={})


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
