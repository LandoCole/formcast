import json

import pytest

from formcast.geometry import points_to_norm
from formcast.pagemap.build import build_form_map
from formcast.pagemap.query import FormMapQuery

PAGE = (0.0, 0.0, 612.0, 792.0)
NAME_BOX = (120.0, 86.0, 300.0, 100.0)
YES_BOX = (72.0, 140.0, 82.0, 150.0)
ADDRESS_BOX = (130.0, 288.0, 500.0, 302.0)


class FakeDetector:
    def __init__(self, name, boxes):
        self.name = name
        self.boxes = [(kind, points_to_norm(rect, PAGE), conf) for kind, rect, conf in boxes]

    def detect(self, image):
        return list(self.boxes)


@pytest.fixture
def fm(address_form_pdf):
    det = FakeDetector(
        "FFDNet-L",
        [("text", NAME_BOX, 0.9), ("choice", YES_BOX, 0.4), ("text", ADDRESS_BOX, 0.8)],
    )
    return FormMapQuery(build_form_map(address_form_pdf, detectors={"FFDNet-L": det}))


def _texts(items):
    return [i["text"] for i in items]


def _serializes(value):
    assert json.loads(json.dumps(value)) == value


def test_every_item_carries_id_page_and_rect(fm):
    results = [
        fm.words(0), fm.lines(0), fm.candidates(), fm.rules(0), fm.widgets(),
        fm.find_text("name"), fm.right_of("c0_0"), fm.near(NAME_BOX, 0, 50),
    ]
    for items in results:
        _serializes(items)
        for item in items:
            assert {"id", "page", "rect", "type"} <= item.keys()
            assert len(item["rect"]) == 4


def test_lines_and_words(fm):
    lines = fm.lines(0)
    assert "Address" in _texts(lines)
    name = next(line for line in lines if line["text"] == "Name:")
    assert name["id"].startswith("l0_")
    assert name["font_size"] == 12
    words = fm.words(0)
    assert any(w["text"] == "Street" and w["line_id"].startswith("l0_") for w in words)


def test_find_text_substring_is_case_insensitive_and_regex_works(fm):
    assert _texts(fm.find_text("NAME")) == ["Name:"]
    assert _texts(fm.find_text(r"^(street|city)$", regex=True)) == ["Street", "City"]
    assert fm.find_text("name", page=5) == []


def test_right_of_a_label_finds_the_candidate_and_the_rule(fm):
    label = fm.find_text("Name:")[0]
    found = fm.right_of(label, max_dist=100)
    types = [(i["type"], i["id"]) for i in found]
    assert ("candidate", "c0_0") in types
    assert any(t == "rule" for t, _ in types)
    assert all(i["dist"] <= 100 for i in found)
    assert found == sorted(found, key=lambda i: i["dist"])


def test_left_of_above_below(fm):
    assert _texts(fm.left_of("c0_0", types=["line"])) == ["Name:"]
    assert [c["id"] for c in fm.above("c0_2", types=["candidate"])] == ["c0_0"]
    assert fm.above("c0_2", types=["candidate"], max_dist=100) == []
    below = fm.below(NAME_BOX, 0, types=["candidate"])
    assert [c["id"] for c in below] == ["c0_2"]


def test_near_uses_rect_distance(fm):
    ids = [i["id"] for i in fm.near(YES_BOX, 0, 10)]
    assert "c0_1" in ids
    assert all(i["dist"] <= 10 for i in fm.near(YES_BOX, 0, 10))


def test_candidates_filters(fm):
    assert [c["id"] for c in fm.candidates()] == ["c0_0", "c0_1", "c0_2"]
    assert [c["id"] for c in fm.candidates(kind="choice")] == ["c0_1"]
    assert [c["id"] for c in fm.candidates(min_conf=0.5)] == ["c0_0", "c0_2"]
    name = fm.candidates(page=0)[0]
    assert name["label"] == "Name:" and name["label_relation"] == "left"


def test_rules_orientation(fm):
    h = fm.rules(0, orientation="h")
    assert h and all(r["orientation"] == "h" for r in h)
    assert fm.rules(0, orientation="v") == [
        r for r in fm.rules(0) if r["orientation"] == "v"
    ]


def test_region_groups_items(fm):
    region = fm.region(0, (60, 280, 520, 320))
    assert set(region) == {"lines", "rules", "candidates", "widgets"}
    assert {"Address", "Street", "City"} <= set(_texts(region["lines"]))
    assert [c["id"] for c in region["candidates"]] == ["c0_2"]
    assert region["widgets"] == []
    _serializes(region)


def test_label_for(fm):
    label = fm.label_for("c0_0")
    assert label["text"] == "Name:" and label["relation"] == "left"
    assert fm.label_for("c0_1") is None
    with pytest.raises(KeyError):
        fm.label_for("c9_9")


def test_sublabels_under_address_line(fm):
    rule = next(r for r in fm.rules(0) if r["rect"][0] == 130 and r["rect"][2] == 500)
    assert _texts(fm.sublabels_under(rule, 0)) == ["Street", "City"]
    assert _texts(fm.sublabels_under("c0_2")) == ["Street", "City"]
    assert fm.sublabels_under(NAME_BOX, 0) == []


def test_widgets_on_fillable_form(fillable_form_pdf):
    q = FormMapQuery(build_form_map(fillable_form_pdf, detectors={}))
    widgets = q.widgets()
    assert [(w["name"], w["kind"]) for w in widgets] == [
        ("applicant_name", "text"), ("agree", "checkbox"),
    ]
    assert q.label_for(widgets[0]["id"])["text"] == "Name:"
    assert q.pages()[0]["kind"] == "fillable"


def test_query_accepts_a_form_map_dict(fm, address_form_pdf):
    again = FormMapQuery(fm.form_map.model_dump(mode="json"))
    assert again.candidates() == fm.candidates()


def test_unknown_reference_is_a_clear_error(fm):
    with pytest.raises(KeyError):
        fm.right_of("zz1")
    with pytest.raises(ValueError, match="page"):
        fm.right_of((0, 0, 10, 10))
