"""fill_acc: probe records filled into a template, scored per ground-truth box (fill design §6)."""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import fill_score  # noqa: E402  (eval/fill_score.py)
import probe  # noqa: E402
import score  # noqa: E402
from formcast.render.fill import fill  # noqa: E402
from formcast.template.model import Column, Field, FormInfo, Source, Style, TableSegment, Template  # noqa: E402
from formcast.transforms import Transform  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "city": {"type": "string"},
        "dob": {"type": "string", "format": "date"},
        "status": {"enum": ["single", "married", "widowed"]},
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": {"drug": {"type": "string"}, "dose": {"type": "string"}}},
        },
    },
}

NAME = (100.0, 100.0, 300.0, 116.0)
INITIALS = (310.0, 100.0, 360.0, 116.0)  # GT: a slice of the name
CITY = (100.0, 120.0, 300.0, 136.0)
DOB = {"month": (100.0, 150.0, 130.0, 166.0), "day": (140.0, 150.0, 170.0, 166.0), "year": (180.0, 150.0, 240.0, 166.0)}
STATUS = {"single": (100.0, 180.0, 110.0, 190.0), "married": (150.0, 180.0, 160.0, 190.0),
          "widowed": (200.0, 180.0, 210.0, 190.0)}  # fmt: skip
ROW_TOP, ROW_H, ROWS = 300.0, 20.0, 3
DRUG_X, DOSE_X = (72.0, 300.0), (300.0, 540.0)

TEXT_UNITS = 6  # name, initials, city, dob month/day/year
CELL_UNITS = 2 * ROWS
CHOICES = 3


def _g(rect, type="text", path=None, transforms=(), value=None):
    return score.GTField(page=0, rect=rect, type=type, path=path, transforms=list(transforms), value=value)


def _gt(table=True):
    gt = [_g(NAME, path="name"), _g(INITIALS, path="name", transforms=["slice"]), _g(CITY, path="city")]
    gt += [_g(r, path="dob", transforms=["date_part"]) for r in DOB.values()]
    gt += [_g(r, "choice", "status", ["equals"], value=v) for v, r in STATUS.items()]
    if table:
        for i in range(ROWS):
            y = ROW_TOP + i * ROW_H
            gt += [_g((DRUG_X[0], y, DRUG_X[1], y + ROW_H), path="items[].drug"),
                   _g((DOSE_X[0], y, DOSE_X[1], y + ROW_H), path="items[].dose")]  # fmt: skip
    gt.append(_g((400.0, 40.0, 540.0, 60.0)))  # a box without a path claims nothing and isn't scored
    return gt


@pytest.fixture
def pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "Intake form", fontsize=12)
    page.insert_text((72, 112), "Name", fontsize=10)
    page.insert_text((72, 290), "Medications", fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


def _tpl(*fields, pages=1):
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint="x", page_count=pages, page_sizes=[(612.0, 792.0)] * pages, kinds=["digital"] * pages),
        fields=list(fields),
    )


def _t(op, **args):
    return Transform(op=op, args=args)


def _text(fid, rect, paths=None, *transforms, join=None, style=None):
    source = Source(paths=paths, transforms=list(transforms), join=join) if paths else None
    return Field(id=fid, type="text", page=0, rect=rect, source=source, style=style or Style())


def _box(fid, rect, path=None, when=None):
    return Field(id=fid, type="checkbox", page=0, rect=rect, source=Source(paths=[path], when=when) if path else None)


def _segment(columns, max_rows=ROWS):
    return TableSegment(page=0, rect=(72, ROW_TOP, 540, ROW_TOP + ROW_H * max_rows), row_top=ROW_TOP,
                        row_height=ROW_H, max_rows=max_rows, columns=columns)  # fmt: skip


def _table(*segments, mapped=True):
    return Field(id="meds", type="table", segments=list(segments), source=Source(paths=["items"]) if mapped else None)


def _cols():
    return [Column(key="drug", x=DRUG_X), Column(key="dose", x=DOSE_X)]


def _dob_fields(month=_t("date_part", part="month", pad=2), day=_t("date_part", part="day"),
                year=_t("date_part", part="year")):  # fmt: skip
    return [_text("dob_m", DOB["month"], ["dob"], month), _text("dob_d", DOB["day"], ["dob"], day),
            _text("dob_y", DOB["year"], ["dob"], year)]  # fmt: skip


def _status_boxes():
    return [_box(f"st_{v}", r, "status", _t("equals", value=v)) for v, r in STATUS.items()]


def _good(table=None, initials=None, dob=None, status=None):
    return _tpl(
        _text("name", NAME, ["name"]),
        initials or _text("initials", INITIALS, ["name"], _t("slice", start=0, end=3)),
        _text("city", CITY, ["city"]),
        *(dob or _dob_fields()),
        *(status or _status_boxes()),
        table or _table(_segment(_cols())),
    )


@pytest.fixture
def recs():
    records = probe.probe_records(SCHEMA, _gt(), seed="t")
    assert len(records) == 3 and all(len(r["items"]) == ROWS + 1 for r in records)
    return records


def _units(result, kind):
    return result[kind]["units"], result[kind]["correct"]


# ---------------------------------------------------------------- the sanity checks (§6.6)


def test_correct_template_scores_one(pdf, recs):
    r = fill_score.score_fill(_gt(), _good(), pdf, recs)
    assert r["fill_errors"] == []
    assert _units(r, "text") == (TEXT_UNITS * 3, TEXT_UNITS * 3)
    assert _units(r, "cell") == (CELL_UNITS * 3, CELL_UNITS * 3)
    assert _units(r, "choice") == (CHOICES, CHOICES)
    assert r["units"] == r["correct"] == TEXT_UNITS * 3 + CELL_UNITS * 3 + CHOICES
    assert r["fill_acc"] == 1.0
    assert r["rows_dropped_ok"] is True


def test_seeded_template_scores_zero(pdf, recs):
    seeded = _tpl(
        _text("a", NAME), _text("b", INITIALS), _text("c", CITY),
        *[_text(f"d{i}", rect) for i, rect in enumerate(DOB.values())],
        *[_box(f"s{i}", rect) for i, rect in enumerate(STATUS.values())],
        _table(_segment(_cols()), mapped=False),
    )  # fmt: skip
    r = fill_score.score_fill(_gt(), seeded, pdf, recs)
    assert r["units"] == TEXT_UNITS * 3 + CELL_UNITS * 3 + CHOICES
    assert r["correct"] == 0 and r["fill_acc"] == 0.0
    assert r["choice"]["correct"] == 0  # a never-ticked box is wrong: every option is on in some record


def test_always_on_checkbox_scores_zero_for_that_box(pdf, recs):
    boxes = _status_boxes()
    boxes[2] = _box("st_widowed", STATUS["widowed"], "name")  # no condition: ticked whenever name is set
    r = fill_score.score_fill(_gt(), _good(status=boxes), pdf, recs)
    assert _units(r, "choice") == (CHOICES, CHOICES - 1)
    assert r["text"]["correct"] == TEXT_UNITS * 3 and r["cell"]["correct"] == CELL_UNITS * 3


def test_table_per_column_loophole_caught(pdf, recs):
    # one segment per column: rows flow down the drug column's segment, then into the dose column's
    loophole = _good(table=_table(_segment([Column(key="drug", x=DRUG_X)]), _segment([Column(key="dose", x=DOSE_X)])))
    assert score.score_mapping(_gt(), loophole)["path_acc"] == 1.0
    bad = fill_score.score_fill(_gt(), loophole, pdf, recs)
    good = fill_score.score_fill(_gt(), _good(), pdf, recs)
    assert bad["cell"]["correct"] < good["cell"]["correct"]
    assert bad["cell"]["correct"] == ROWS * 3  # only the drug column (the first segment) is right
    assert bad["fill_acc"] < 1.0


# ---------------------------------------------------------------- text boxes


def test_date_part_provenance_accepts_any_format(pdf, recs):
    dob = _dob_fields(month=_t("date_format", format="%B"), day=_t("date_format", format="%d"),
                      year=_t("date_part", part="year2"))  # fmt: skip
    r = fill_score.score_fill(_gt(), _good(dob=dob), pdf, recs)
    assert _units(r, "text") == (TEXT_UNITS * 3, TEXT_UNITS * 3)
    # the whole date in the month box: every token is still a part of this path's date
    whole = _dob_fields(month=_t("date_format", format="%d %b %Y"))
    assert fill_score.score_fill(_gt(), _good(dob=whole), pdf, recs)["text"]["correct"] == TEXT_UNITS * 3
    # dates written without separators (comb-style "mmddyyyy" boxes)
    for fmt in ("%m%d%Y", "%m%d%y"):
        compact = _dob_fields(month=_t("date_format", format=fmt))
        assert fill_score.score_fill(_gt(), _good(dob=compact), pdf, recs)["text"]["correct"] == TEXT_UNITS * 3, fmt


def test_compact_date_words_split_into_own_date_parts_only():
    own = ["1987-05-14"]
    ok = lambda text: fill_score._provenance_ok(text, own, lambda: [])  # noqa: E731
    assert ok("05141987") and ok("051487") and ok("14051987") and ok("5141987") and ok("1987")
    assert not ok("05141988") and not ok("0514198") and not ok("051414")  # a wrong year, a cut year, a part twice


def test_wrong_path_token_fails_provenance(pdf, recs):
    other = _text("initials", INITIALS, ["city"], _t("slice", start=0, end=3))
    r = fill_score.score_fill(_gt(), _good(initials=other), pdf, recs)
    assert r["text"]["correct"] == (TEXT_UNITS - 1) * 3
    # the right path plus a token of another path's value fails too
    joined = _text("initials", INITIALS, ["name", "city"], _t("upper"), join=" ")
    r = fill_score.score_fill(_gt(), _good(initials=joined), pdf, recs)
    assert r["text"]["correct"] == (TEXT_UNITS - 1) * 3


def test_an_own_part_found_in_another_value_still_counts_but_a_fragment_does_not(pdf, recs):
    # Generated values can collide (a year shared by two dates, a number inside another number).
    # A word that is a whole part of the box's own value is its own; a mere fragment found in
    # another path's value is ambiguous and fails.
    recs = [dict(r) for r in recs]
    for r in recs:
        r["city"] = r["dob"][:4] + r["name"][:3]  # the dob year and the name's first 3 letters
    r = fill_score.score_fill(_gt(), _good(), pdf, recs)
    assert r["text"]["correct"] == (TEXT_UNITS - 1) * 3  # the year box is right; the initials slice is not


def test_plain_number_box_accepts_any_number_formatting(pdf):
    schema = {"type": "object", "properties": {"amount": {"type": "integer"}}}
    gt = [_g(NAME, path="amount")]
    recs = probe.probe_records(schema, gt, seed="t")

    def tpl(*transforms):
        t = _tpl(_text("a", NAME, ["amount"], *transforms))
        return t.model_copy(update={"schema_": schema})

    formatted = tpl(_t("number_format", decimals=2, prefix="$"))
    assert fill_score.score_fill(gt, formatted, pdf, recs)["correct"] == len(recs)
    cut = tpl(_t("slice", start=0, end=2))
    assert fill_score.score_fill(gt, cut, pdf, recs)["correct"] == 0


def _join_ok(text, own=("ABCDEF", "1987-05-14"), record_words=("abcdef", "1987", "05", "14", "qwerty")):
    g = score.GTField(page=0, rect=NAME, type="text", path="name", also_paths=["dob"], transforms=["join"])
    seen = fill_score.Observed(0, NAME, "n", text, None, "text")
    return fill_score._text_ok(g, seen, list(own), lambda: [], lambda: set(record_words))


def test_join_box_needs_every_own_value_and_no_other_record_word():
    assert _join_ok("ABCDEF 1987-05-14") and _join_ok("1987-05-14, ABCDEF")
    assert _join_ok("ABCDEF on 1987-05-14")  # a literal separator word is in no record value
    assert _join_ok("ABCDEF 05/14/1987") and _join_ok("ABCDEF May 14, 1987") and _join_ok("ABCDEF 05141987")
    assert _join_ok("WIDGET 57,509.00", own=("WIDGET", 57509), record_words=("widget", "57509"))
    assert not _join_ok("ABCDEF")  # a path missing
    assert not _join_ok("ABCDEF 1987-05-14 QWERTY")  # another path's value
    assert not _join_ok("ABCDEF ABCDEF 1987-05-14")  # stuffing: an own word twice
    assert not _join_ok("ABCDEF 05/14/1988")  # the wrong year


def test_join_box_with_a_literal_separator_scores_right(pdf, recs):
    gt = [score.GTField(page=0, rect=NAME, type="text", path="name", also_paths=["dob"], transforms=["join"])]
    on = _tpl(_text("j", NAME, ["name", "dob"], join=" on "))
    assert fill_score.score_fill(gt, on, pdf, recs)["correct"] == len(recs)
    extra = _tpl(_text("j", NAME, ["name", "dob", "city"], join=" "))
    assert fill_score.score_fill(gt, extra, pdf, recs)["correct"] == 0


def test_plain_text_box_needs_the_exact_value(pdf, recs):
    # no GT transforms: a slice of the right path is not the value
    gt = _gt()
    gt[1] = _g(INITIALS, path="name")  # this GT box now expects the whole name
    whole = _text("initials", INITIALS, ["name"])
    assert fill_score.score_fill(gt, _good(initials=whole), pdf, recs)["text"]["correct"] == TEXT_UNITS * 3
    sliced = _good()  # the initials field still slices
    assert fill_score.score_fill(gt, sliced, pdf, recs)["text"]["correct"] == (TEXT_UNITS - 1) * 3


def test_multiline_field_scores_the_gt_line_that_carries_the_path(pdf, recs):
    lines = [_g((100.0, 400.0 + 18 * i, 500.0, 416.0 + 18 * i), path="city" if i == 1 else None) for i in range(4)]
    tall = _tpl(_text("notes", (98.0, 398.0, 502.0, 470.0), ["city"], style=Style(multiline=True)))
    r = fill_score.score_fill(lines, tall, pdf, recs)
    assert _units(r, "text") == (3, 3)


def test_multiline_cover_credits_only_gt_boxes_without_transforms(pdf, recs):
    # one tall field showing the whole date can't stand in for stacked date-part boxes
    lines = [_g((100.0, 400.0 + 18 * i, 500.0, 416.0 + 18 * i), path="dob" if i else None,
                transforms=["date_part"] if i else ()) for i in range(4)]  # fmt: skip
    tall = _tpl(_text("dob", (98.0, 398.0, 502.0, 470.0), ["dob"], _t("date_format", format="%m/%d/%Y"),
                      style=Style(multiline=True)))  # fmt: skip
    r = fill_score.score_fill(lines, tall, pdf, recs)
    assert _units(r, "text") == (9, 0)


# ---------------------------------------------------------------- tables, errors, reading back


def test_rows_dropped_ok(pdf, recs):
    assert fill_score.score_fill(_gt(), _good(), pdf, recs)["rows_dropped_ok"] is True  # 4 probe rows, 3 slots
    roomy = _good(table=_table(_segment(_cols(), max_rows=ROWS + 1)))  # the extra row is written, not dropped
    assert fill_score.score_fill(_gt(), roomy, pdf, recs)["rows_dropped_ok"] is False
    unmapped = _good(table=_table(_segment(_cols()), mapped=False))
    assert fill_score.score_fill(_gt(), unmapped, pdf, recs)["rows_dropped_ok"] is False
    no_tables = probe.probe_records(SCHEMA, _gt(table=False), seed="t")
    assert fill_score.score_fill(_gt(table=False), _good(), pdf, no_tables)["rows_dropped_ok"] is None


def test_static_choice_boxes_are_counted(pdf, recs):
    assert fill_score.score_fill(_gt(), _good(), pdf, recs)["static_choice"] == 0
    never = _gt() + [_g((250.0, 180.0, 260.0, 190.0), "choice", "status", ["equals"], value="divorced")]
    assert fill_score.score_fill(never, _good(), pdf, recs)["static_choice"] == 1  # never on in any record


def test_no_records_raises(pdf):
    with pytest.raises(ValueError, match="record"):
        fill_score.score_fill(_gt(), _good(), pdf, [])


def test_fill_exception_counts_record_wrong(pdf, recs):
    broken = _good().model_copy(update={"form": FormInfo(fingerprint="x", page_count=2,
                                                         page_sizes=[(612.0, 792.0)] * 2, kinds=["digital"] * 2)})
    r = fill_score.score_fill(_gt(), broken, pdf, recs)
    assert r["units"] == TEXT_UNITS * 3 + CELL_UNITS * 3 + CHOICES
    assert r["correct"] == 0 and r["fill_acc"] == 0.0
    assert r["fill_errors"] and "page count" in r["fill_errors"][0]
    assert r["rows_dropped_ok"] is False


def test_read_written_reads_only_widgets_a_mapped_field_wrote(fillable_form_pdf):
    # the form's own "agree" checkbox is left alone by an unmapped field; only applicant_name is read
    t = Template(
        template_id="t", schema={"type": "object", "properties": {"n": {"type": "string"}}},
        form=FormInfo(fingerprint="x", page_count=1, page_sizes=[(612.0, 792.0)], kinds=["fillable"]),
        fields=[
            Field(id="n", type="text", page=0, rect=(120, 86, 300, 100), existing_widget="applicant_name",
                  source=Source(paths=["n"])),
            Field(id="a", type="checkbox", page=0, rect=(72, 140, 82, 150), existing_widget="agree"),
        ],
    )  # fmt: skip
    out, report = fill(t, fillable_form_pdf, {"n": "QWERTY"}, check_fingerprint=False)
    seen = fill_score.read_written(out, report)
    assert [(o.name, o.kind, o.text, o.on, o.page) for o in seen] == [("applicant_name", "text", "QWERTY", None, 0)]
    assert seen[0].rect == pytest.approx((120, 86, 300, 100))
