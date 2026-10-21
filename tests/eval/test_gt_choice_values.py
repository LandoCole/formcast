import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import gt_choice_values as gcv  # noqa: E402  (eval/gt_choice_values.py)
from formcast.schema import flatten_schema  # noqa: E402

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["single", "married", "not_married", "widowed"]},
        "likes": {"type": "array", "items": {"type": "string", "enum": ["cats", "dogs", "birds"]}},
        "notes": {"type": "array", "items": {"type": "string"}},
        "smokes": {"type": "boolean"},
        "agree": {"type": "boolean"},
        "name": {"type": "string"},
    },
}
SP = {p.path: p for p in flatten_schema(SCHEMA)}


# ---------- text matching ----------


def test_tokens_split_punctuation_case_and_plurals():
    assert gcv.tokens("Not_Married (see below)") == ["not", "married", "see", "below"]
    assert gcv.tokens("DD214, ngbForm22") == ["dd", "214", "ngb", "form", "22"]
    assert gcv.tokens("Dogs") == ["dog"] and gcv.tokens("class") == ["class"]


def test_options_of_covers_enum_item_enum_boolean_and_open_paths():
    assert gcv.options_of(SP["status"]) == ["single", "married", "not_married", "widowed"]
    assert gcv.options_of(SP["likes"]) == ["cats", "dogs", "birds"]
    assert gcv.options_of(SP["smokes"]) == [True, False]
    assert gcv.options_of(SP["notes"]) is None and gcv.options_of(SP["name"]) is None


def test_match_option_needs_all_words_and_prefers_the_more_specific_option():
    opts = SP["status"].enum
    assert gcv.match_option("Single", opts)[0] == "single"
    assert gcv.match_option("Married", opts)[0] == "married"
    assert gcv.match_option("Not married", opts)[0] == "not_married"  # beats the "married" it contains
    assert gcv.match_option("Single or married", opts)[0] is None  # two options fit
    assert gcv.match_option("Divorced", opts)[0] is None


def test_match_initial_needs_a_letter_naming_exactly_one_option():
    assert gcv.match_initial("M", ["male", "female"])[0] == "male"
    assert gcv.match_initial("S", ["single", "separated"])[0] is None
    assert gcv.match_initial("1", ["one", "two"])[0] is None


@pytest.mark.parametrize(
    "text, expected",
    [("Yes", True), ("NO", False), ("Y", True), ("n", False), ("Anxiety/Stress YES", True),
     ("Do you smoke? No", False), ("No known allergies to anything at all", None),
     ("Yes No", None), ("Maybe", None), ("", None)],
)
def test_match_yes_no(text, expected):
    assert gcv.match_yes_no(text)[0] is expected


# ---------- text near a box ----------


def _w(x0, x1, text, y0=100.0, y1=110.0):
    return (x0, y0, x1, y1, text, 0, 0, 0)


def test_nearby_text_gives_each_label_to_the_nearer_box():
    a, b = (72.0, 100.0, 82.0, 110.0), (150.0, 100.0, 160.0, 110.0)
    # labels on the right:  [] Yes     [] No
    right_layout = [_w(86, 105, "Yes"), _w(164, 180, "No")]
    assert gcv.nearby_text(right_layout, a, [b]) == {"right": "Yes", "left": ""}
    assert gcv.nearby_text(right_layout, b, [a]) == {"right": "No", "left": ""}
    # labels on the left:  Yes []     No []
    left_layout = [_w(50, 68, "Yes"), _w(125, 146, "No")]
    assert gcv.nearby_text(left_layout, a, [b]) == {"right": "", "left": "Yes"}
    assert gcv.nearby_text(left_layout, b, [a]) == {"right": "", "left": "No"}


def test_nearby_text_stops_at_a_wide_gap_and_other_lines():
    a = (72.0, 100.0, 82.0, 110.0)
    words = [_w(86, 105, "Single"), _w(110, 130, "parent"), _w(300, 330, "far"), _w(86, 105, "below", 130, 140)]
    assert gcv.nearby_text(words, a, [])["right"] == "Single parent"


def test_nearby_text_takes_a_glyph_joined_to_its_label():
    a, b = (72.0, 100.0, 82.0, 110.0), (150.0, 100.0, 160.0, 110.0)
    words = [_w(72, 100, "☐Yes"), _w(150, 178, "☐No"), _w(120, 122, "☐")]
    assert gcv.nearby_text(words, a, [b])["right"] == "☐Yes"
    assert gcv.nearby_text(words, b, [a])["right"] == "☐No"


# ---------- proposals ----------


def test_propose_uses_label_then_on_state_then_nearby_words():
    near = {"right": "Dogs", "left": ""}
    assert gcv.propose(SP["likes"], "Pets: Cats", None, near)[:2] == ("cats", "high")  # label beats the words
    assert gcv.propose(SP["likes"], "", "Birds", near)[:2] == ("birds", "high")
    assert gcv.propose(SP["likes"], "", None, near)[:2] == ("dogs", "nearby")
    assert gcv.propose(SP["likes"], "", None, {"right": "", "left": "Cats"})[:2] == ("cats", "nearby")
    assert gcv.propose(SP["likes"], "", None, {"right": "", "left": ""})[0] is None


def test_propose_leaves_disagreeing_sources_null():
    value, conf, evidence = gcv.propose(SP["likes"], "Cats", "Dogs", {"right": "", "left": ""})
    assert value is None and conf is None and "disagree" in evidence


def test_propose_booleans_from_yes_no_and_ignoring_a_lone_checkbox_default():
    near = {"right": "", "left": ""}
    assert gcv.propose(SP["smokes"], "Smoke? No", None, near)[:2] == (False, "high")
    assert gcv.propose(SP["smokes"], "Smoke? YES", None, near)[:2] == (True, "high")
    assert gcv.propose(SP["smokes"], "Do you smoke", "Yes", near)[0] is None  # "Yes" is the default on-state
    assert gcv.propose(SP["smokes"], "Do you smoke", "Yes", near, shared=True)[:2] == (True, "high")
    assert gcv.propose(SP["smokes"], "Do you smoke", "No", near)[:2] == (False, "high")
    assert gcv.propose(SP["smokes"], "Smoker", None, near)[0] is None  # no Yes/No: the box means true


def test_propose_open_paths_use_the_boxs_own_text():
    assert gcv.propose(SP["notes"], "Notes: Hernia", None, {"right": "x", "left": ""})[:2] == ("Hernia", "text")
    assert gcv.propose(SP["notes"], "", None, {"right": "Hernia repair", "left": ""})[:2] == ("Hernia repair", "text")
    assert gcv.propose(SP["notes"], "", None, {"right": "", "left": ""})[0] is None


# ---------- drafting a form ----------


def _flat_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Marital status", fontsize=11)
    for x, label in ((72, "Single"), (150, "Married"), (230, "Widowed")):
        page.draw_rect(pymupdf.Rect(x, 120, x + 10, 130))
        page.insert_text((x + 14, 129), label, fontsize=10)
    for x, label in ((72, "Yes"), (150, "No")):
        page.draw_rect(pymupdf.Rect(x, 160, x + 10, 170))
        page.insert_text((x + 14, 169), label, fontsize=10)
    page.draw_rect(pymupdf.Rect(72, 200, 82, 210))  # no words near
    doc.new_page(width=612, height=792)
    path = tmp_path / "f.pdf"
    doc.save(path)
    return path


def _entry(rect, path=None, **kw):
    d = {"page": 0, "rect": list(rect), "type": "choice", "label": ""}
    if path:
        d["path"] = path
    d.update(kw)
    return d


def test_draft_values_for_a_flat_form(tmp_path):
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    fields = [
        {"page": 1, "rect": [0, 0, 5, 5], "type": "text"},  # fields[0], not scored, shifts the indexes
        _entry((72, 120, 82, 130), "status"),
        _entry((150, 120, 160, 130), "status"),
        _entry((230, 120, 240, 130), "status", value="widowed"),  # already labeled: left out
        _entry((72, 160, 82, 170), "smokes"),
        _entry((150, 160, 160, 170), "smokes"),
        _entry((72, 200, 82, 210), "status"),  # nothing near
        _entry((300, 300, 310, 310)),  # no path
        {"page": 0, "rect": [72, 60, 200, 74], "type": "text", "path": "name"},
    ]
    (gt_dir / "f.json").write_text(json.dumps({"form": "f", "fields": fields}))
    form = {"id": "f", "kind": "flat", "scored_pages": [0]}
    draft = gcv.draft_values(form, _flat_pdf(tmp_path), SCHEMA, gt_dir=gt_dir)
    assert sorted(draft) == ["fields[1]", "fields[2]", "fields[4]", "fields[5]", "fields[6]"]
    assert draft["fields[1]"]["proposed"] == "single" and draft["fields[1]"]["confidence"] == "nearby"
    assert draft["fields[2]"]["proposed"] == "married"
    assert draft["fields[4]"]["proposed"] is True and draft["fields[5]"]["proposed"] is False
    assert draft["fields[6]"]["proposed"] is None and draft["fields[6]"]["confidence"] is None
    e = draft["fields[1]"]
    assert (e["kind"], e["page"], e["path"], e["path_type"], e["needs_value"]) == ("fields", 0, "status", "enum", True)
    assert e["options"] == ["single", "married", "not_married", "widowed"] and e["transforms"] == []
    assert e["rect"] == [72.0, 120.0, 82.0, 130.0] and "Single" in e["evidence"]
    assert draft["fields[4]"]["needs_value"] is False and draft["fields[4]"]["options"] == [True, False]
    assert gcv.summarize(draft) == {"need": 3, "high": 0, "nearby": 2, "text": 0, "none": 1, "bool": 2, "bool_yes_no": 2}


def test_draft_values_for_a_fillable_form_keys_by_widget_name_and_ignores_the_field_name_label(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 129), "Pets: Cats", fontsize=10)
    for name, x in (("agree", 72), ("pet_box", 150)):
        w = pymupdf.Widget()
        w.field_name = name
        w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = pymupdf.Rect(x, 120, x + 10, 130)
        page.add_widget(w)
    pdf = tmp_path / "w.pdf"
    doc.save(pdf)
    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    (gt_dir / "w.json").write_text(json.dumps({
        "form": "w",
        "widget_paths": {"agree": {"path": "agree"}, "pet_box": {"path": "likes", "transforms": ["equals"]}},
    }))
    draft = gcv.draft_values({"id": "w", "kind": "fillable"}, pdf, SCHEMA, gt_dir=gt_dir)
    assert sorted(draft) == ["agree", "pet_box"]
    assert draft["agree"]["kind"] == "widget_paths" and draft["agree"]["needs_value"] is False
    assert draft["agree"]["proposed"] is None  # a lone check box's default "Yes" on-state says nothing
    assert draft["agree"]["on_state"] == "Yes" and "label" in draft["agree"]
    assert draft["pet_box"]["transforms"] == ["equals"]
    # "Pets: Cats" sits between the boxes, nearer pet_box, so it is that box's left text only
    assert draft["pet_box"]["near_text"] == {"right": "", "left": "Pets: Cats"}
    assert draft["pet_box"]["proposed"] == "cats" and draft["pet_box"]["confidence"] == "nearby"
    assert draft["agree"]["near_text"] == {"right": "", "left": ""}


def test_draft_values_groups_widgets_that_share_a_name(tmp_path, monkeypatch):
    # two widgets of one radio group, as score.gt_entries reports them
    gt_a = gcv.score.GTField(0, (72, 120, 82, 130), "choice", "sex", "smokes")
    gt_b = gcv.score.GTField(0, (150, 120, 160, 130), "choice", "sex", "smokes")
    monkeypatch.setattr(gcv.score, "gt_entries", lambda *a, **k: [("sex", gt_a, "Yes"), ("sex", gt_b, "No")])
    draft = gcv.draft_values({"id": "x", "kind": "fillable"}, _flat_pdf(tmp_path), SCHEMA)
    assert list(draft) == ["sex"]
    assert draft["sex"]["proposed"] == {"Yes": True, "No": False}
    assert [w["on_state"] for w in draft["sex"]["widgets"]] == ["Yes", "No"]
    assert draft["sex"]["confidence"] == "high"


def test_cli_writes_a_draft_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gcv, "DRAFT_DIR", tmp_path / "drafts")
    assert gcv.main(["--form", "i9", "--write-draft"]) == 0
    out = json.loads((tmp_path / "drafts" / "i9.values.json").read_text())
    assert out and all("proposed" in e and "evidence" in e and e["path"] for e in out.values())
    assert "i9:" in capsys.readouterr().out
