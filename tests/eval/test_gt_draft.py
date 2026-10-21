import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import gt_draft  # noqa: E402  (eval/gt_draft.py)
from formcast.geometry import points_to_norm  # noqa: E402

PAGE = (0, 0, 612, 792)
NAME_BOX = (120.0, 86.0, 300.0, 100.0)
YES_BOX = (72.0, 140.0, 82.0, 150.0)
DATE_BOX = (72.0, 198.0, 250.0, 212.0)


class FakeDetector:
    def __init__(self, name, boxes):
        self.name = name
        self.boxes = [(kind, points_to_norm(rect, PAGE), conf) for kind, rect, conf in boxes]

    def detect(self, image):
        return list(self.boxes)


def _detectors():
    nudged = (NAME_BOX[0] + 2, NAME_BOX[1], NAME_BOX[2] + 2, NAME_BOX[3])
    return {
        "FFDNet-L": FakeDetector("FFDNet-L", [("text", NAME_BOX, 0.9), ("choice", YES_BOX, 0.8)]),
        "FFDetr": FakeDetector("FFDetr", [("text", nudged, 0.7), ("text", DATE_BOX, 0.6)]),
    }


def test_draft_is_the_deduped_union_of_both_detectors_with_labels(flat_form_pdf):
    draft = gt_draft.draft({"id": "fl", "kind": "flat"}, flat_form_pdf, detectors=_detectors())
    fields = draft["fields"]
    assert draft["form"] == "fl"
    assert len(fields) == 3  # the two name boxes overlap and collapse to the surer one
    by_type = sorted((f["type"], f["detector"]) for f in fields)
    assert by_type == [("choice", "FFDNet-L"), ("text", "FFDNet-L"), ("text", "FFDetr")]
    name = next(f for f in fields if f["rect"][1] == pytest.approx(86, abs=0.5))
    assert name["label"] == "Name:" and name["rect"][0] == pytest.approx(120, abs=0.5)
    yes = next(f for f in fields if f["type"] == "choice")
    assert yes["option"] == "Yes"
    assert all(set(f) >= {"page", "rect", "type", "label"} for f in fields)


def test_draft_only_covers_scored_pages(flat_form_pdf):
    form = {"id": "fl", "kind": "flat", "scored_pages": []}
    assert gt_draft.draft(form, flat_form_pdf, detectors=_detectors())["fields"] == []


def test_write_draft_writes_json_and_one_overlay_per_page(flat_form_pdf, tmp_path):
    form = {"id": "fl", "kind": "flat"}
    draft = gt_draft.draft(form, flat_form_pdf, detectors=_detectors())
    out = gt_draft.write_draft(draft, flat_form_pdf, gt_dir=tmp_path / "gt", overlay_dir=tmp_path / "ov")
    assert json.loads(out.read_text()) == draft
    assert out.name == "fl.draft.json"
    assert [p.name for p in sorted((tmp_path / "ov").iterdir())] == ["fl_p0_draft.png"]


def test_overlay_of_a_gt_file(flat_form_pdf, tmp_path):
    gt = {"form": "fl", "fields": [{"page": 0, "rect": list(NAME_BOX), "type": "text", "label": "Name"}]}
    paths = gt_draft.render_overlays(gt, flat_form_pdf, tmp_path, suffix="gt", color=(0, 160, 0))
    assert [p.name for p in paths] == ["fl_p0_gt.png"]


def test_draft_refuses_fillable_forms(fillable_form_pdf):
    with pytest.raises(SystemExit, match="widgets"):
        gt_draft.draft({"id": "ff", "kind": "fillable"}, fillable_form_pdf, detectors=_detectors())
