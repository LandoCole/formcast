import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import score  # noqa: E402  (eval/score.py)
from formcast.template.model import (  # noqa: E402
    Column,
    Field,
    FormInfo,
    Source,
    Style,
    TableSegment,
    Template,
)
from formcast.transforms import Transform  # noqa: E402

A = (10.0, 10.0, 110.0, 30.0)
B = (10.0, 50.0, 110.0, 70.0)
C = (10.0, 90.0, 110.0, 110.0)
FAR = (300.0, 300.0, 400.0, 320.0)


def _shift(r, dx):
    return (r[0] + dx, r[1], r[2] + dx, r[3])


# ---------- score_detection ----------


def test_detection_recall_and_precision_are_exact():
    gt = [score.GTField(0, r, "text") for r in (A, B, C)]
    boxes = [("text", 0, _shift(A, 2)), ("text", 0, _shift(B, 2)), ("text", 0, FAR)]
    out = score.score_detection(gt, boxes, 0.5)
    assert (out["gt"], out["pred"], out["matched"]) == (3, 3, 2)
    assert out["recall"] == pytest.approx(2 / 3)
    assert out["precision"] == pytest.approx(2 / 3)
    assert out["missed_by_type"] == {"text": 1, "choice": 0, "signature": 0}


def test_detection_iou_threshold_is_inclusive_and_respected():
    gt = [score.GTField(0, A, "text")]
    box = [("text", 0, _shift(A, 50))]  # IoU = 50 / 150 = 1/3
    assert score.score_detection(gt, box, 0.3)["matched"] == 1
    assert score.score_detection(gt, box, 0.5)["matched"] == 0


def test_detection_type_accuracy_counts_matched_boxes_with_the_right_type():
    gt = [score.GTField(0, A, "text"), score.GTField(0, B, "choice"), score.GTField(0, C, "signature")]
    boxes = [("text", 0, A), ("text", 0, B), ("signature", 0, C)]
    out = score.score_detection(gt, boxes, 0.5)
    assert out["matched"] == 3
    assert out["type_ok"] == 2
    assert out["type_acc"] == pytest.approx(2 / 3)


def test_detection_matches_one_to_one_by_best_iou_and_only_on_the_same_page():
    gt = [score.GTField(0, A, "text")]
    two_on_it = [("text", 0, _shift(A, 20)), ("text", 0, _shift(A, 5))]
    out = score.score_detection(gt, two_on_it, 0.3)
    assert out["matched"] == 1 and out["precision"] == pytest.approx(0.5)
    assert score.score_detection(gt, [("text", 1, A)], 0.3)["matched"] == 0


def test_detection_counts_misses_by_type_and_survives_empty_inputs():
    gt = [score.GTField(0, A, "choice"), score.GTField(0, B, "text")]
    out = score.score_detection(gt, [], 0.3)
    assert out["missed_by_type"] == {"text": 1, "choice": 1, "signature": 0}
    assert out["recall"] == 0.0 and out["precision"] == 0.0 and out["type_acc"] == 0.0
    assert score.score_detection([], [("text", 0, A)], 0.3)["recall"] == 0.0


# ---------- template helpers ----------


def _template(fields):
    return Template(
        template_id="t",
        schema={},
        form=FormInfo(fingerprint="x", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]),
        fields=fields,
    )


def _field(fid, rect, paths=None, transforms=(), type="text", page=0):
    source = Source(paths=paths, transforms=[Transform(op=t) for t in transforms]) if paths else None
    return Field(id=fid, type=type, page=page, rect=rect, source=source)


def _table(rect_cols, source_path="items", row_top=100.0, row_height=20.0, max_rows=2, col_transforms=None):
    cols = [
        Column(key=k, x=x, transforms=[Transform(op=t) for t in (col_transforms or {}).get(k, [])])
        for k, x in rect_cols.items()
    ]
    seg = TableSegment(
        page=0, rect=(50, 90, 400, 200), row_top=row_top, row_height=row_height, max_rows=max_rows, columns=cols
    )
    return Field(id="tbl", type="table", source=Source(paths=[source_path]), segments=[seg])


def test_template_boxes_map_field_types_and_expand_table_cells():
    t = _template(
        [
            _field("a", A, type="text"),
            _field("b", B, type="checkbox"),
            _field("c", C, type="radio_option"),
            _field("d", (10, 130, 20, 140), type="choice_mark"),
            _field("e", (10, 150, 110, 170), type="signature"),
            _table({"name": (50, 150), "qty": (150, 200)}),
        ]
    )
    boxes = score.template_boxes(t)
    assert [b[0] for b in boxes[:5]] == ["text", "choice", "choice", "choice", "signature"]
    cells = boxes[5:]
    assert len(cells) == 4  # 2 columns x 2 rows
    assert all(kind == "text" and page == 0 for kind, page, _ in cells)
    assert (50.0, 100.0, 150.0, 120.0) in [r for _, _, r in cells]
    assert (150.0, 120.0, 200.0, 140.0) in [r for _, _, r in cells]


# ---------- score_mapping ----------


def test_mapping_counts_correct_path_and_wrong_transform_separately():
    gt = [
        score.GTField(0, A, "text", path="person.first", transforms=["upper"]),
        score.GTField(0, B, "text", path="person.dob", transforms=["date_format"]),
        score.GTField(0, C, "text", path="person.ssn"),
        score.GTField(0, FAR, "text", path="person.zip"),  # nothing detected here
        score.GTField(0, (500, 500, 560, 520), "text"),  # no path: not scored
    ]
    t = _template(
        [
            _field("f1", A, ["person.first"], ["upper"]),  # right path, right transform
            _field("f2", B, ["person.dob"], ["upper"]),  # right path, wrong transform
            _field("f3", C, ["person.last"]),  # wrong path
        ]
    )
    out = score.score_mapping(gt, t)
    assert out["n_path"] == 4 and out["n_transform"] == 2
    assert out["path_acc"] == pytest.approx(2 / 4)
    assert out["transform_acc"] == pytest.approx(1 / 2)
    assert out["matched"] == 3 and out["matched_rate"] == pytest.approx(3 / 4)


def test_mapping_accepts_any_of_a_multi_path_source_and_extra_transforms():
    gt = [score.GTField(0, A, "text", path="p.b", transforms=["join"])]
    t = _template([_field("f", A, ["p.a", "p.b"], ["trim", "join"])])
    out = score.score_mapping(gt, t)
    assert out["path_acc"] == 1.0 and out["transform_acc"] == 1.0


def test_mapping_unmapped_template_field_scores_wrong_not_missing():
    gt = [score.GTField(0, A, "text", path="p.a", transforms=["upper"])]
    out = score.score_mapping(gt, _template([_field("f", A)]))
    assert out["matched"] == 1 and out["path_acc"] == 0.0 and out["transform_acc"] == 0.0


def test_mapping_with_no_scorable_gt_reports_zero_counts():
    out = score.score_mapping([score.GTField(0, A, "text")], _template([_field("f", A, ["p.a"])]))
    assert out["n_path"] == 0 and out["path_acc"] == 0.0 and out["transform_acc"] == 0.0


def test_mapping_table_cells_match_when_geometry_and_column_path_agree():
    gt = [
        score.GTField(0, (50, 100, 150, 120), "text", path="items[].name", transforms=["upper"]),
        score.GTField(0, (150, 100, 200, 120), "text", path="items[].qty"),
        score.GTField(0, (50, 120, 150, 140), "text", path="items[].name", transforms=["upper"]),
        score.GTField(0, (150, 120, 200, 140), "text", path="items[].price"),  # wrong column key
    ]
    t = _template([_table({"name": (50, 150), "qty": (150, 200)}, col_transforms={"name": ["upper"]})])
    out = score.score_mapping(gt, t)
    assert out["n_path"] == 4
    assert out["path_acc"] == pytest.approx(3 / 4)
    assert out["n_transform"] == 2 and out["transform_acc"] == 1.0


def test_mapping_table_cell_with_the_wrong_source_path_does_not_match():
    gt = [score.GTField(0, (50, 100, 150, 120), "text", path="rows[].name")]
    t = _template([_table({"name": (50, 150)}, source_path="items")])
    out = score.score_mapping(gt, t)
    assert out["matched"] == 0 and out["path_acc"] == 0.0


def test_mapping_table_gt_is_not_matched_by_a_plain_field():
    gt = [score.GTField(0, A, "text", path="items[].name")]
    out = score.score_mapping(gt, _template([_field("f", A, ["items[].name"])]))
    assert out["matched"] == 0 and out["path_acc"] == 0.0


# ---------- load_gt ----------


def _write_gt(gt_dir: Path, form_id: str, body: dict) -> None:
    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / f"{form_id}.json").write_text(json.dumps(body))


def test_load_gt_fillable_uses_widgets_enriched_by_widget_paths(fillable_form_pdf, tmp_path):
    gt_dir = tmp_path / "gt"
    _write_gt(
        gt_dir,
        "ff",
        {
            "form": "ff",
            "fields": [{"page": 0, "rect": [0, 0, 5, 5], "type": "text"}],  # ignored for fillable forms
            "widget_paths": {"applicant_name": {"path": "person.name", "transforms": ["upper"]}},
            "notes": "",
        },
    )
    gt = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=gt_dir)
    by_type = {g.type: g for g in gt}
    assert set(by_type) == {"text", "choice"}
    assert by_type["text"].path == "person.name" and by_type["text"].transforms == ["upper"]
    assert by_type["text"].label == "applicant_name"
    assert by_type["text"].rect == pytest.approx((120, 86, 300, 100))
    assert by_type["choice"].path is None and by_type["choice"].transforms == []


def test_load_gt_fillable_without_a_gt_file_still_returns_widgets(fillable_form_pdf, tmp_path):
    gt = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=tmp_path / "none")
    assert len(gt) == 2 and all(g.path is None for g in gt)


def test_load_gt_fillable_without_widgets_is_none(flat_form_pdf, tmp_path):
    assert score.load_gt({"id": "x", "kind": "fillable"}, flat_form_pdf, gt_dir=tmp_path) is None


def test_load_gt_flat_reads_fields_and_none_when_missing(flat_form_pdf, tmp_path):
    gt_dir = tmp_path / "gt"
    assert score.load_gt({"id": "fl", "kind": "flat"}, flat_form_pdf, gt_dir=gt_dir) is None
    _write_gt(
        gt_dir,
        "fl",
        {
            "form": "fl",
            "fields": [
                {"page": 0, "rect": [120, 86, 300, 100], "type": "text", "label": "Name", "path": "p.name",
                 "transforms": ["upper"]},
                {"page": 0, "rect": [72, 140, 82, 150], "type": "choice"},
            ],
            "widget_paths": {},
            "notes": "n",
        },
    )
    gt = score.load_gt({"id": "fl", "kind": "flat"}, flat_form_pdf, gt_dir=gt_dir)
    assert gt[0] == score.GTField(0, (120, 86, 300, 100), "text", "Name", "p.name", ["upper"])
    assert gt[1] == score.GTField(0, (72, 140, 82, 150), "choice")


def test_load_gt_reads_value_from_fields_and_widget_paths(flat_form_pdf, fillable_form_pdf, tmp_path):
    gt_dir = tmp_path / "gt"
    _write_gt(
        gt_dir,
        "fl",
        {
            "form": "fl",
            "fields": [
                {"page": 0, "rect": [72, 140, 82, 150], "type": "choice", "path": "p.sex", "value": "female"},
                {"page": 0, "rect": [72, 160, 82, 170], "type": "choice", "path": "p.ok", "value": False},
                {"page": 0, "rect": [72, 180, 82, 190], "type": "choice", "path": "p.ok2"},
            ],
        },
    )
    gt = score.load_gt({"id": "fl", "kind": "flat"}, flat_form_pdf, gt_dir=gt_dir)
    assert [g.value for g in gt] == ["female", False, None]
    assert score.GTField(0, A, "text").value is None

    _write_gt(
        gt_dir,
        "ff",
        {"form": "ff", "widget_paths": {"agree": {"path": "p.agree", "value": True}}, "notes": ""},
    )
    ff = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=gt_dir)
    assert {g.label: g.value for g in ff} == {"applicant_name": None, "agree": True}


def test_load_gt_widget_value_object_is_keyed_by_the_widgets_on_state(fillable_form_pdf, tmp_path):
    # widgets that share a field name (a radio group) take their value from the on-state name
    gt_dir = tmp_path / "gt"
    _write_gt(
        gt_dir,
        "ff",
        {"form": "ff", "widget_paths": {"agree": {"path": "p.agree", "value": {"Yes": "yes", "Other": "x"}}}},
    )
    ff = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=gt_dir)
    assert {g.label: g.value for g in ff}["agree"] == "yes"
    _write_gt(gt_dir, "ff", {"form": "ff", "widget_paths": {"agree": {"path": "p.agree", "value": {"No": False}}}})
    ff = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=gt_dir)
    assert {g.label: g.value for g in ff}["agree"] is None


def test_gt_entries_keys_name_the_gt_file_entry(flat_form_pdf, fillable_form_pdf, tmp_path):
    gt_dir = tmp_path / "gt"
    _write_gt(
        gt_dir,
        "fl",
        {"form": "fl", "fields": [{"page": 0, "rect": [0, 0, 5, 5], "type": "text"},
                                  {"page": 1, "rect": [0, 0, 5, 5], "type": "text"},
                                  {"page": 0, "rect": [72, 140, 82, 150], "type": "choice"}]},
    )
    # page 1 is not scored: the key keeps the index in the file
    entries = score.gt_entries({"id": "fl", "kind": "flat", "scored_pages": [0]}, flat_form_pdf, gt_dir=gt_dir)
    assert [(k, g.type) for k, g, _ in entries] == [("fields[0]", "text"), ("fields[2]", "choice")]
    entries = score.gt_entries({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=tmp_path / "none")
    assert [(k, st) for k, _, st in entries] == [("applicant_name", None), ("agree", "Yes")]


def test_load_gt_rejects_an_unknown_type(flat_form_pdf, tmp_path):
    _write_gt(tmp_path, "fl", {"form": "fl", "fields": [{"page": 0, "rect": [0, 0, 5, 5], "type": "radio"}]})
    with pytest.raises(ValueError, match="fl.json"):
        score.load_gt({"id": "fl", "kind": "flat"}, flat_form_pdf, gt_dir=tmp_path)


def test_load_gt_fillable_skips_push_buttons(fillable_form_pdf, tmp_path):
    doc = pymupdf.open(fillable_form_pdf)
    button = pymupdf.Widget()
    button.field_name = "print_form"
    button.field_type = pymupdf.PDF_WIDGET_TYPE_BUTTON
    button.rect = pymupdf.Rect(400, 86, 480, 104)
    doc[0].add_widget(button)
    path = tmp_path / "with_button.pdf"
    doc.save(path)
    doc.close()
    gt = score.load_gt({"id": "ff", "kind": "fillable"}, path, gt_dir=tmp_path / "none")
    assert sorted(g.label for g in gt) == ["agree", "applicant_name"]


def test_load_gt_reads_also_paths(fillable_form_pdf, flat_form_pdf, tmp_path):
    _write_gt(tmp_path, "ff", {"form": "ff", "widget_paths": {
        "applicant_name": {"path": "p.first", "also_paths": ["p.last"], "transforms": ["join"]}}})
    gt = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=tmp_path)
    name = next(g for g in gt if g.label == "applicant_name")
    assert (name.path, name.also_paths, name.transforms) == ("p.first", ["p.last"], ["join"])
    assert next(g for g in gt if g.label == "agree").also_paths == []

    _write_gt(tmp_path, "fl", {"form": "fl", "fields": [
        {"page": 0, "rect": [120, 86, 300, 100], "type": "text", "path": "p.first", "also_paths": ["p.last"]},
        {"page": 0, "rect": [72, 140, 82, 150], "type": "choice"}]})
    gt = score.load_gt({"id": "fl", "kind": "flat"}, flat_form_pdf, gt_dir=tmp_path)
    assert [g.also_paths for g in gt] == [["p.last"], []]


def test_load_gt_widget_paths_type_overrides_the_widget_type(fillable_form_pdf, tmp_path):
    _write_gt(tmp_path, "ff", {"form": "ff", "widget_paths": {"applicant_name": {"type": "signature"}}})
    gt = score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=tmp_path)
    by_label = {g.label: g for g in gt}
    assert by_label["applicant_name"].type == "signature" and by_label["applicant_name"].path is None
    assert by_label["agree"].type == "choice"


def test_load_gt_rejects_an_unknown_widget_paths_type(fillable_form_pdf, tmp_path):
    _write_gt(tmp_path, "ff", {"form": "ff", "widget_paths": {"applicant_name": {"type": "radio"}}})
    with pytest.raises(ValueError, match="ff.json"):
        score.load_gt({"id": "ff", "kind": "fillable"}, fillable_form_pdf, gt_dir=tmp_path)


def test_scored_pages_default_to_all_pages():
    assert score.scored_pages({"id": "x"}, 3) == [0, 1, 2]
    assert score.scored_pages({"id": "x", "scored_pages": [2, 0]}, 3) == [0, 2]


def test_scored_pages_rejects_a_page_outside_the_document():
    with pytest.raises(ValueError, match="x"):
        score.scored_pages({"id": "x", "scored_pages": [3]}, 3)


def test_load_gt_keeps_only_scored_pages(fillable_form_pdf, flat_form_pdf, tmp_path):
    doc = pymupdf.open(fillable_form_pdf)
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_name = "page_two"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(100, 100, 300, 120)
    page.add_widget(w)
    path = tmp_path / "two_pages.pdf"
    doc.save(path)
    doc.close()
    form = {"id": "ff", "kind": "fillable"}
    assert {g.page for g in score.load_gt(form, path, gt_dir=tmp_path)} == {0, 1}
    gt = score.load_gt({**form, "scored_pages": [1]}, path, gt_dir=tmp_path)
    assert [g.label for g in gt] == ["page_two"]
    assert score.load_gt({**form, "scored_pages": [0]}, path, gt_dir=tmp_path)[0].page == 0


# ---------- transform equivalence, joint and exact-path-set metrics ----------


@pytest.mark.parametrize(
    "gt_op, field_op",
    [("slice", "split"), ("char_at", "slice"), ("date_part", "date_format"), ("equals", "contains"),
     ("contains", "equals"), ("split", "char_at")],
)
def test_mapping_credits_an_equivalent_transform(gt_op, field_op):
    gt = [score.GTField(0, A, "text", path="p.a", transforms=[gt_op])]
    out = score.score_mapping(gt, _template([_field("f", A, ["p.a"], [field_op])]))
    assert out["transform_acc"] == 1.0


@pytest.mark.parametrize("gt_op, field_op", [("date_part", "slice"), ("equals", "upper"), ("join", "upper")])
def test_mapping_does_not_credit_a_transform_from_another_group(gt_op, field_op):
    gt = [score.GTField(0, A, "text", path="p.a", transforms=[gt_op])]
    out = score.score_mapping(gt, _template([_field("f", A, ["p.a"], [field_op])]))
    assert out["transform_acc"] == 0.0


def test_mapping_equivalence_keeps_multiplicity():
    gt = [score.GTField(0, A, "text", path="p.a", transforms=["slice", "split"])]
    one = score.score_mapping(gt, _template([_field("f", A, ["p.a"], ["char_at"])]))
    two = score.score_mapping(gt, _template([_field("f", A, ["p.a"], ["char_at", "slice"])]))
    assert one["transform_acc"] == 0.0 and two["transform_acc"] == 1.0


def test_mapping_counts_a_when_condition_and_a_source_join_as_ops():
    gt = [
        score.GTField(0, A, "choice", path="p.kind", transforms=["equals"]),
        score.GTField(0, B, "text", path="p.first", also_paths=["p.last"], transforms=["join"]),
    ]
    box = Field(id="f1", type="checkbox", page=0, rect=A,
                source=Source(paths=["p.kind"], when=Transform(op="contains", args={"value": "x"})))
    name = Field(id="f2", type="text", page=0, rect=B, source=Source(paths=["p.first", "p.last"], join=" "))
    out = score.score_mapping(gt, _template([box, name]))
    assert out["transform_acc"] == 1.0


def test_mapping_joint_needs_path_and_transforms_and_exact_needs_the_whole_path_set():
    gt = [
        score.GTField(0, A, "text", path="p.dob", transforms=["date_format"]),  # path ok, transform wrong
        score.GTField(0, B, "text", path="p.first", also_paths=["p.last"], transforms=["join"]),  # all ok
        score.GTField(0, C, "text", path="p.ssn"),  # path ok, extra source path
        score.GTField(0, FAR, "text", path="p.zip"),  # no field here
    ]
    t = _template([
        _field("f1", A, ["p.dob"], ["upper"]),
        Field(id="f2", type="text", page=0, rect=B, source=Source(paths=["p.last", "p.first"], join=" ")),
        _field("f3", C, ["p.ssn", "p.zip"]),
    ])  # fmt: skip
    out = score.score_mapping(gt, t)
    assert (out["path_ok"], out["transform_ok"], out["joint_ok"], out["exact_ok"]) == (3, 1, 2, 2)
    assert out["joint_acc"] == pytest.approx(2 / 4)
    assert out["exact_path_set_acc"] == pytest.approx(2 / 4)
    assert out["path_acc"] == pytest.approx(3 / 4)


def test_mapping_table_cell_is_exact_when_gt_has_no_also_paths():
    gt = [score.GTField(0, (50, 100, 150, 120), "text", path="items[].name")]
    out = score.score_mapping(gt, _template([_table({"name": (50, 150)})]))
    assert out["joint_acc"] == 1.0 and out["exact_path_set_acc"] == 1.0


# ---------- bool_to_check == when equals true ----------


def _when_box(value, op="equals", rect=A):
    return Field(id="w", type="checkbox", page=0, rect=rect,
                 source=Source(paths=["p.ok"], when=Transform(op=op, args={"value": value})))


def test_mapping_credits_bool_to_check_with_a_when_equals_true():
    gt = [score.GTField(0, A, "choice", path="p.ok", transforms=["bool_to_check"])]
    assert score.score_mapping(gt, _template([_when_box(True)]))["transform_acc"] == 1.0
    assert score.score_mapping(gt, _template([_field("f", A, ["p.ok"], ["bool_to_check"], type="checkbox")]))[
        "transform_acc"] == 1.0


def test_mapping_when_equals_true_still_credits_an_equals_gt():
    gt = [score.GTField(0, A, "choice", path="p.ok", transforms=["equals"])]
    assert score.score_mapping(gt, _template([_when_box(True)]))["transform_acc"] == 1.0


def test_mapping_bool_to_check_is_not_credited_by_other_when_values():
    gt = [score.GTField(0, A, "choice", path="p.ok", transforms=["bool_to_check"])]
    assert score.score_mapping(gt, _template([_when_box(False)]))["transform_acc"] == 0.0
    assert score.score_mapping(gt, _template([_when_box("yes")]))["transform_acc"] == 0.0


def test_transforms_match_uses_each_field_op_once():
    # one when-equals-true op cannot satisfy both an equals and a bool_to_check GT op
    assert not score.transforms_match(["equals", "bool_to_check"], [score.WHEN_EQUALS_TRUE])
    assert score.transforms_match(["equals", "bool_to_check"], [score.WHEN_EQUALS_TRUE, "contains"])


# ---------- multi-line answer areas ----------

# Five ruled lines, 14pt tall, 16.8pt apart: a multi-line answer area.
LINES = [(36.0, 300.0 + 16.8 * i, 576.0, 314.0 + 16.8 * i) for i in range(5)]
AREA = (36.0, 299.0, 576.0, 380.0)


def _lines_gt():
    gt = [score.GTField(0, LINES[0], "text", path="p.notes")]
    gt += [score.GTField(0, r, "text") for r in LINES[1:]]
    gt.append(score.GTField(0, (36.0, 500.0, 200.0, 514.0), "text", path="p.name"))
    return gt


def _detect(gt, t, thr=0.5):
    return score.score_template_detection(gt, t, thr)


def test_a_tall_text_field_covers_every_line_box_inside_it():
    gt = _lines_gt()
    t = _template([_field("big", AREA, ["p.notes"]), _field("n", (36.0, 500.0, 200.0, 514.0), ["p.name"])])
    out = _detect(gt, t)
    assert (out["gt"], out["matched"], out["pred"], out["pred_matched"]) == (6, 6, 2, 2)
    assert out["recall"] == 1.0 and out["precision"] == 1.0
    m = score.score_mapping(gt, t)
    assert (m["n_path"], m["path_ok"], m["joint_ok"], m["exact_ok"]) == (2, 2, 2, 2)


def test_per_line_fields_score_the_same_as_one_tall_field():
    gt = _lines_gt()
    t = _template([_field("l0", LINES[0], ["p.notes"])] + [_field(f"l{i}", r) for i, r in enumerate(LINES[1:], 1)]
                  + [_field("n", (36.0, 500.0, 200.0, 514.0), ["p.name"])])
    out = _detect(gt, t)
    assert out["recall"] == 1.0 and out["precision"] == 1.0
    assert score.score_mapping(gt, t)["path_ok"] == 2


def test_a_multiline_style_field_covers_lines_even_when_short():
    gt = [score.GTField(0, LINES[0], "text", path="p.notes"), score.GTField(0, LINES[1], "text")]
    two = (36.0, 302.0, 576.0, 327.0)  # 25pt: under 1.8 x 14pt
    plain = _template([_field("f", two, ["p.notes"])])
    styled = _template([Field(id="f", type="text", page=0, rect=two, source=Source(paths=["p.notes"]),
                              style=Style(multiline=True))])
    assert _detect(gt, plain, 0.5)["matched"] == 0
    assert _detect(gt, styled, 0.5)["matched"] == 2


def test_lines_mostly_outside_a_tall_field_are_not_covered():
    gt = _lines_gt()
    half = (36.0, 299.0, 576.0, 350.0)  # lines 0-2 inside; 3 and 4 outside
    out = _detect(gt, _template([_field("big", half, ["p.notes"])]))
    assert out["matched"] == 3


def test_a_tall_field_does_not_cover_choice_boxes_or_other_pages():
    gt = [score.GTField(0, LINES[0], "text"), score.GTField(0, LINES[1], "text"), score.GTField(0, LINES[2], "text"),
          score.GTField(0, (40.0, 340.0, 50.0, 350.0), "choice"), score.GTField(1, LINES[0], "text")]
    out = _detect(gt, _template([_field("big", AREA)]))
    assert out["matched"] == 3


def test_a_line_matched_by_its_own_field_is_not_claimed_by_a_tall_one():
    gt = _lines_gt()[:5]
    t = _template([_field("big", AREA), _field("l0", LINES[0], ["p.notes"])])
    m = score.score_mapping(gt, t)
    assert m["path_ok"] == 1  # LINES[0] matched by l0 (best IoU), not swallowed by big
    out = _detect(gt, t)
    assert out["matched"] == 5 and out["pred_matched"] == 2


def test_table_cells_never_cover_lines():
    gt = [score.GTField(0, (50.0, 100.0, 150.0, 110.0), "text"), score.GTField(0, (50.0, 110.0, 150.0, 120.0), "text")]
    t = _template([_table({"name": (50, 150)}, row_top=100.0, row_height=40.0, max_rows=1)])
    assert _detect(gt, t)["matched"] == 0


def test_score_detection_without_multiline_flags_is_one_to_one():
    gt = _lines_gt()
    out = score.score_detection(gt, [("text", 0, AREA)], 0.5)
    assert out["matched"] == 0 and out["pred_matched"] == 0


def test_a_single_loose_tall_box_is_not_a_multiline_field_cover():
    # one 14pt GT line; a 30pt box that holds it but only has IoU .47 with it
    gt = [score.GTField(0, LINES[0], "text", path="p.notes")]
    loose = (36.0, 300.0, 576.0, 330.0)
    out = _detect(gt, _template([_field("big", loose, ["p.notes"])]), 0.5)
    assert out["matched"] == 0 and out["pred_matched"] == 0
    assert _detect(gt, _template([_field("big", loose, ["p.notes"])]), 0.3)["matched"] == 1  # plain IoU still applies


def test_a_tall_box_over_boxes_side_by_side_is_not_a_multiline_cover():
    # two GT boxes in the same row (different columns): nothing is stacked
    gt = [score.GTField(0, (36.0, 300.0, 200.0, 314.0), "text"), score.GTField(0, (300.0, 300.0, 460.0, 314.0), "text")]
    tall = (30.0, 298.0, 470.0, 340.0)
    assert _detect(gt, _template([_field("big", tall)]), 0.5)["matched"] == 0


def test_a_tall_box_over_two_stacked_lines_is_a_cover():
    gt = [score.GTField(0, LINES[0], "text"), score.GTField(0, LINES[1], "text")]
    assert _detect(gt, _template([_field("big", (36.0, 299.0, 576.0, 332.0))]), 0.5)["matched"] == 2


def test_a_linked_cell_scores_once_with_its_fields_box_and_type():
    linked = _field("w0_0", (50, 108, 150, 120))
    box = _field("w0_1", (160, 108, 170, 118), type="checkbox")
    table = _table({"name": (50, 150), "done": (150, 200)})
    table.segments[0].columns[0].fields = ["w0_0"]
    table.segments[0].columns[1].fields = ["w0_1", None]
    boxes = score.template_boxes(_template([linked, box, table]))
    assert len(boxes) == 4  # the two linked fields count only as their cells
    assert ("text", 0, (50, 108, 150, 120)) in boxes and ("choice", 0, (160, 108, 170, 118)) in boxes
    assert ("text", 0, (150.0, 120.0, 200.0, 140.0)) in boxes  # row 1 of "done" is computed
    gt = [score.GTField(0, (50, 108, 150, 120), "text", path="items[].name")]
    assert score.score_mapping(gt, _template([linked, box, table]))["path_acc"] == 1.0
