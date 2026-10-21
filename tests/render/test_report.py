from typing import get_args

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
