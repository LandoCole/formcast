"""Consistency of the corpus ground truth (forms/gt) and schemas (forms/schemas).

Every manifest form must have both files; each existing file is checked.
"""

import functools
import json
import sys
from pathlib import Path

import pymupdf
import pytest
from jsonschema.validators import validator_for

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import score  # noqa: E402  (eval/score.py)
from formcast.schema import flatten_schema  # noqa: E402
from formcast.transforms import list_transforms  # noqa: E402
from gt_choice_values import options_of  # noqa: E402  (eval/gt_choice_values.py)

REPO = Path(__file__).resolve().parents[2]
FORMS = REPO / "forms"
GT_DIR = FORMS / "gt"
SCHEMA_DIR = FORMS / "schemas"
MANIFEST = json.loads((FORMS / "manifest.json").read_text())["forms"]
BY_ID = {f["id"]: f for f in MANIFEST}
TYPES = {"text", "choice", "signature"}
OPS = {t["name"] for t in list_transforms()}

GT_IDS = sorted(p.stem for p in GT_DIR.glob("*.json") if not p.name.endswith(".draft.json"))
SCHEMA_IDS = sorted(p.stem for p in SCHEMA_DIR.glob("*.json"))


def _gt(form_id: str) -> dict:
    return json.loads((GT_DIR / f"{form_id}.json").read_text())


def _schema(form_id: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{form_id}.json").read_text())


def _pdf(form_id: str) -> Path:
    form = BY_ID[form_id]
    return FORMS / form["split"] / f"{form_id}.pdf"


def _scored(form: dict) -> list[int]:
    return form.get("scored_pages", list(range(form["pages"])))


def _mappings(gt: dict) -> list[tuple[str, dict]]:
    """(where, entry) for every field or widget_paths entry that names schema paths."""
    out = [(f"fields[{i}]", f) for i, f in enumerate(gt.get("fields", []))]
    out += [(f"widget_paths[{name!r}]", m) for name, m in gt.get("widget_paths", {}).items()]
    return out


# ---------- manifest ----------


@pytest.mark.parametrize("form", MANIFEST, ids=lambda f: f["id"])
def test_manifest_scored_pages_are_valid_page_indexes(form):
    pages = form.get("scored_pages")
    if pages is None:
        return
    assert pages and len(set(pages)) == len(pages)
    assert all(isinstance(p, int) and 0 <= p < form["pages"] for p in pages)


@pytest.mark.parametrize("form", MANIFEST, ids=lambda f: f["id"])
def test_every_manifest_form_has_gt_and_schema(form):
    assert (GT_DIR / f"{form['id']}.json").exists()
    assert (SCHEMA_DIR / f"{form['id']}.json").exists()


# ---------- schemas ----------


@pytest.mark.parametrize("form_id", SCHEMA_IDS)
def test_schema_is_a_valid_json_schema(form_id):
    assert form_id in BY_ID, "schema for a form not in the manifest"
    schema = _schema(form_id)
    validator_for(schema).check_schema(schema)
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert flatten_schema(schema)


# ---------- ground truth ----------


@pytest.mark.parametrize("form_id", GT_IDS)
def test_gt_file_shape(form_id):
    assert form_id in BY_ID, "GT for a form not in the manifest"
    gt = _gt(form_id)
    assert gt["form"] == form_id
    assert isinstance(gt.get("notes", ""), str)
    assert (SCHEMA_DIR / f"{form_id}.json").exists(), "GT without a schema"
    if BY_ID[form_id]["kind"] == "fillable":
        assert not gt.get("fields"), "fillable GT boxes come from the widgets"
    else:
        assert not gt.get("widget_paths"), "widget_paths are for fillable forms"


@pytest.mark.parametrize("form_id", GT_IDS)
def test_gt_rects_are_on_scored_pages(form_id):
    form = BY_ID[form_id]
    scored = set(_scored(form))
    doc = pymupdf.open(_pdf(form_id))
    try:
        assert doc.page_count == form["pages"]
        for i, f in enumerate(_gt(form_id).get("fields", [])):
            assert f["type"] in TYPES, (i, f)
            assert f["page"] in scored, (i, f)
            x0, y0, x1, y1 = f["rect"]
            w, h = doc[f["page"]].rect.width, doc[f["page"]].rect.height
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (i, f)
    finally:
        doc.close()


@pytest.mark.parametrize("form_id", [i for i in GT_IDS if BY_ID.get(i, {}).get("kind") != "fillable"])
def test_flat_and_scanned_gt_cover_every_scored_page(form_id):
    pages = {f["page"] for f in _gt(form_id)["fields"]}
    assert set(_scored(BY_ID[form_id])) <= pages


@pytest.mark.parametrize("form_id", GT_IDS)
def test_gt_paths_resolve_in_the_schema(form_id):
    paths = {p.path for p in flatten_schema(_schema(form_id))}
    for where, entry in _mappings(_gt(form_id)):
        named = ([entry["path"]] if "path" in entry else []) + list(entry.get("also_paths", []))
        if entry.get("also_paths"):
            assert "path" in entry, where
        for path in named:
            assert path in paths, f"{where}: {path!r} not in the schema"
        assert set(entry.get("transforms", [])) <= OPS, where
        if entry.get("transforms"):
            assert "path" in entry, where


@pytest.mark.parametrize("form_id", [i for i in GT_IDS if BY_ID.get(i, {}).get("kind") == "fillable"])
def test_widget_paths_name_widgets_of_the_form(form_id):
    doc = pymupdf.open(_pdf(form_id))
    try:
        names = {w.field_name for page in doc for w in page.widgets() or []}
    finally:
        doc.close()
    missing = sorted(set(_gt(form_id).get("widget_paths", {})) - names)
    assert not missing


@pytest.mark.parametrize("form_id", [i for i in GT_IDS if BY_ID.get(i, {}).get("kind") == "fillable"])
def test_widget_paths_type_overrides_are_valid_types(form_id):
    for name, entry in _gt(form_id).get("widget_paths", {}).items():
        assert entry.get("type", "text") in TYPES, name


# ---------- choice-box values ----------


@functools.cache
def _all_gt_entries() -> list[tuple[str, str, score.GTField]]:
    """(form id, entry key, GT field) of every GT choice box that names a path, dev and held-out."""
    out = []
    for form in MANIFEST:
        for key, g, _ in score.gt_entries(form, _pdf(form["id"])) or []:
            if g.type == "choice" and g.path:
                out.append((form["id"], key, g))
    return out


def _schema_paths(form_id: str) -> dict:
    return {p.path: p for p in flatten_schema(_schema(form_id))}


def _value_problem(sp, value) -> str | None:
    """Why ``value`` cannot be a choice box's value on schema path ``sp``, else None."""
    if sp.type == "boolean":
        return None if value is None or isinstance(value, bool) else f"{value!r} is not true/false"
    if value is None:
        return "no value"
    options = options_of(sp)
    if options is None:  # an open-ended path (no enum): the box's own text
        return None if isinstance(value, str) and value.strip() else f"{value!r} is not a text"
    return None if value in options else f"{value!r} not in {options}"


def test_present_choice_values_are_valid():
    problems = []
    for form_id, key, g in _all_gt_entries():
        if g.value is not None and (why := _value_problem(_schema_paths(form_id)[g.path], g.value)):
            problems.append(f"{form_id} {key} {g.label!r}: {why}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("form_id", [i for i in GT_IDS if BY_ID.get(i, {}).get("kind") == "fillable"])
def test_widget_value_objects_are_keyed_by_the_widgets_on_state_names(form_id):
    doc = pymupdf.open(_pdf(form_id))
    try:
        states: dict[str, set[str]] = {}
        for page in doc:
            for w in page.widgets() or []:
                states.setdefault(w.field_name, set()).add(w.on_state())
    finally:
        doc.close()
    for name, entry in _gt(form_id).get("widget_paths", {}).items():
        if isinstance(entry.get("value"), dict):
            assert set(entry["value"]) <= states[name], (name, sorted(states[name]))


@pytest.mark.xfail(strict=True, reason="labels added in Task 8 step 4")
def test_choice_entries_have_valid_value():
    problems = []
    group_size = {}
    for form_id, key, _ in _all_gt_entries():
        group_size[form_id, key] = group_size.get((form_id, key), 0) + 1
    for form_id, key, g in _all_gt_entries():
        sp = _schema_paths(form_id)[g.path]
        why = _value_problem(sp, g.value)
        if why is None and g.value is None and group_size[form_id, key] > 1:
            why = "widgets sharing a field name need a value each"  # a Yes/No pair must say which is which
        if why:
            problems.append(f"{form_id} {key} {g.label!r}: {why}")
    assert not problems, f"{len(problems)} choice boxes\n" + "\n".join(problems[:20])
