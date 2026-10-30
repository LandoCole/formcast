from typing import get_args

from formcast.match import fingerprint
from formcast.render.fill import fill
from formcast.render.report import (
    SEVERITY,
    CellReport,
    FieldReport,
    FillReport,
    FillWarning,
    PathReport,
    TableReport,
    WarningCode,
)
from formcast.template.model import Field, FormInfo, Source, Template


def test_make_fills_severity():
    assert FillWarning.make("overflow", "x").severity == "warning"
    assert FillWarning.make("shrunk", "x").severity == "info"


def test_severity_covers_every_code_and_info_set():
    codes = set(get_args(WarningCode))
    assert set(SEVERITY) == codes
    info = {c for c, s in SEVERITY.items() if s == "info"}
    assert info == {"shrunk", "adopted", "font_substituted", "list_joined", "unknown_keys"}


def test_report_json_roundtrip():
    w = FillWarning.make("overflow", "too long", path="name", field="f1", page=0, rect=(1.0, 2.0, 3.0, 4.0))
    cell = CellReport(row=0, key="drug", segment=0, page=0, rect=(72.0, 200.0, 250.0, 218.0),
                      binding="created", widget="t_r0_drug", widget_rect=(72.0, 200.0, 250.0, 218.0),
                      value="D0", font_size=9.5, warnings=[w])  # fmt: skip
    r = FillReport(
        template_id="t",
        fingerprint_ok=True,
        strict=False,
        fields=[FieldReport(id="f1", type="text", page=0, paths=["name"], binding="named", widget="w",
                            value="A", font_size=10.0, warnings=[w]),
                FieldReport(id="f2", type="checkbox", page=0, paths=["agree"], value=True)],  # fmt: skip
        tables=[TableReport(id="t_meds", path="medications", rows_provided=1, rows_written=1, rows_dropped=0,
                            rows_per_segment=[1], cells=[cell])],  # fmt: skip
        paths={"name": PathReport(path="name", mapped=True, has_value=True, fields=["f1"],
                                  rendered={"f1": "A", "f2": False, "t[0]": None}, warnings=[w])},  # fmt: skip
        gaps=["f9"],
        warnings=[w],
        counts={"fields_written": 1, "fields_empty": 0, "cells_written": 1, "overflow": 1},
    )
    again = FillReport.from_json(r.to_json())
    assert again == r
    assert again.paths["name"].rendered["f2"] is False
    assert again.fields[1].value is True


# ------------------------------------------------------------- required gaps

_REQ_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "code": {"type": "string"},
                                                "note": {"type": "string"}}}
_REQ_TEXT = [{"at": (72, 60), "text": "Intake form"}]


def _req_template(pdf: bytes, *fields: Field) -> Template:
    return Template(template_id="t", schema=_REQ_SCHEMA,
                    form=FormInfo(fingerprint=fingerprint(pdf), page_count=1, page_sizes=[(612.0, 792.0)],
                                  kinds=["digital"]),
                    fields=list(fields))  # fmt: skip


def _req_field(fid: str, path: str | None, y: float, *, required: bool = False) -> Field:
    return Field(id=fid, type="text", page=0, rect=(100, y, 300, y + 16), required=required,
                 source=Source(paths=[path]) if path else None)  # fmt: skip


def test_required_gaps_are_the_empty_required_boxes(make_pdf):
    pdf = make_pdf(text=_REQ_TEXT)
    tpl = _req_template(pdf, _req_field("f_name", "name", 100, required=True),
                        _req_field("f_code", "code", 140, required=True), _req_field("f_note", "note", 180))
    _, rep = fill(tpl, pdf, {"name": "Ada"})
    assert rep.gaps == ["f_code", "f_note"]
    assert rep.required_gaps == ["f_code"]
    assert rep.counts["required_left_empty"] == 1
    assert FillReport.from_json(rep.to_json()) == rep


def test_an_unmapped_required_box_is_a_required_gap(make_pdf):
    pdf = make_pdf(text=_REQ_TEXT)
    tpl = _req_template(pdf, _req_field("f_name", "name", 100), _req_field("f_stray", None, 140, required=True))
    _, rep = fill(tpl, pdf, {"name": "Ada"})
    assert rep.gaps == ["f_stray"] and rep.required_gaps == ["f_stray"]
    assert rep.counts["required_left_empty"] == 1


def test_a_template_with_no_required_boxes_reports_as_before(make_pdf):
    pdf = make_pdf(text=_REQ_TEXT)
    tpl = _req_template(pdf, _req_field("f_name", "name", 100), _req_field("f_code", "code", 140))
    _, rep = fill(tpl, pdf, {"name": "Ada"})
    assert rep.gaps == ["f_code"] and rep.required_gaps == []
    assert "required_left_empty" not in rep.counts
