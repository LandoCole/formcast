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


# --- grid ------------------------------------------------------------------------


def _grid_map(cells):
    from formcast.pagemap.model import Candidate, FormMap, PageMap

    cands = [Candidate(id=f"c0_{i}", kind="text", rect=r, conf=0.9, detector="d") for i, r in enumerate(cells)]
    page = PageMap(index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                   words=[], lines=[], rules=[], widgets=[], candidates=cands)
    return FormMapQuery(FormMap(pages=[page]))


def _cells(cols, top, pitch, height, rows, jitter=0.0):
    out = []
    for r in range(rows):
        for x0, x1 in cols:
            j = jitter if (r + int(x0)) % 2 else -jitter
            out.append((x0 + j, top + r * pitch + j, x1 - j, top + r * pitch + height + j))
    return out


def test_grid_of_detected_cells_gives_the_add_table_geometry():
    cols = [(50.0, 200.0), (205.0, 300.0), (305.0, 420.0)]
    q = _grid_map(_cells(cols, top=400.0, pitch=20.0, height=16.0, rows=4, jitter=0.6))
    g = q.grid([f"c0_{i}" for i in range(12)])
    assert g["page"] == 0 and g["max_rows"] == 4 and g["regular"] is True
    assert g["row_top"] == pytest.approx(400.0, abs=1) and g["row_height"] == pytest.approx(20.0, abs=0.5)
    assert len(g["columns"]) == 3
    assert g["columns"][1] == pytest.approx([205.0, 300.0], abs=1)
    x0, y0, x1, y1 = g["rect"]
    assert x0 <= 50.0 and x1 >= 419 and y0 <= 400 and y1 >= g["row_top"] + 4 * g["row_height"] - 0.01
    # every input box is placed in its row and column
    cell = {(c["row"], c["col"]): c["id"] for c in g["cells"]}
    assert len(cell) == 12 and cell[(0, 0)] == "c0_0" and cell[(3, 2)] == "c0_11"
    _serializes(g)


def test_grid_flags_uneven_rows_and_accepts_rects():
    rects = _cells([(50.0, 200.0)], top=100.0, pitch=20.0, height=16.0, rows=2)
    rects.append((50.0, 170.0, 200.0, 186.0))  # third row 50pt below the second
    g = _grid_map([]).grid([list(r) for r in rects], page=0)
    # the third row is 3.5 pitches below the second: not on the grid's pitch
    assert g["row_height"] == pytest.approx(20.0, abs=0.5) and g["regular"] is False
    assert [c["id"] for c in g["cells"]] == [None, None, None]


def test_grid_needs_boxes_on_one_page():
    q = _grid_map(_cells([(50.0, 200.0)], top=100.0, pitch=20.0, height=16.0, rows=2))
    with pytest.raises(ValueError):
        q.grid([])


def test_grid_rows_and_columns_ignore_one_oversized_box():
    cols = [(50.0, 200.0), (205.0, 300.0)]
    cells = _cells(cols, top=400.0, pitch=24.0, height=24.0, rows=3)
    cells[1] = (205.0, 392.0, 330.0, 424.0)  # one detection taller and wider than its cell
    g = _grid_map(cells).grid([f"c0_{i}" for i in range(6)])
    assert g["row_top"] == pytest.approx(400.0, abs=1) and g["row_height"] == pytest.approx(24.0, abs=0.5)
    assert g["columns"][1] == pytest.approx([205.0, 300.0], abs=1)


def test_grid_indexes_rows_by_position_when_middle_rows_have_no_box():
    cols = [(50.0, 200.0), (205.0, 300.0)]
    cells = [c for c in _cells(cols, top=100.0, pitch=20.0, height=16.0, rows=6) if not 140 <= c[1] < 200]
    g = _grid_map(cells).grid([f"c0_{i}" for i in range(len(cells))])  # rows 0, 1 and 5 only
    assert g["row_height"] == pytest.approx(20.0, abs=0.5) and g["row_top"] == pytest.approx(100.0, abs=0.5)
    assert g["max_rows"] == 6 and g["missing_rows"] == [2, 3, 4] and g["regular"] is True
    assert sorted({c["row"] for c in g["cells"]}) == [0, 1, 5]
    assert g["rect"][3] >= 100.0 + 6 * 20.0 - 0.5


def test_grid_reports_a_box_over_two_columns_instead_of_merging_them():
    cols = [(100.0, 195.0), (205.0, 300.0)]
    cells = _cells(cols, top=100.0, pitch=20.0, height=16.0, rows=3)
    cells.append((100.0, 160.0, 300.0, 176.0))  # one box over both columns, in a fourth row
    g = _grid_map(cells).grid([f"c0_{i}" for i in range(len(cells))])
    assert g["columns"] == [pytest.approx([100.0, 195.0], abs=0.5), pytest.approx([205.0, 300.0], abs=0.5)]
    assert g["spanning"] == [{"row": 3, "id": "c0_6", "rect": [100.0, 160.0, 300.0, 176.0]}]
    assert len(g["cells"]) == 6 and g["max_rows"] == 4


def test_grid_column_fields_list_each_columns_box_ids_by_row():
    cols = [(50.0, 200.0), (205.0, 300.0)]
    cells = [c for c in _cells(cols, top=100.0, pitch=20.0, height=16.0, rows=3) if c[1] < 120 or c[0] < 204]
    g = _grid_map(cells).grid([f"c0_{i}" for i in range(len(cells))])  # row 1 and 2 lack the 2nd column
    assert g["column_fields"] == [["c0_0", "c0_2", "c0_3"], ["c0_1", None, None]]
    _serializes(g)
