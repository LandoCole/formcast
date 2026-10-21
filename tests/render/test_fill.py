"""fill() and to_smart_pdf(): one record into a PDF as real form fields, with a FillReport."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.match import fingerprint
from formcast.render.fill import fill, to_smart_pdf
from formcast.render.report import FillReport
from formcast.template.model import Column, Field, FormInfo, Source, Style, TableSegment, Template
from formcast.transforms import Transform

ROOT = Path(__file__).resolve().parents[2]
MENOMONIE = ROOT / "forms" / "dev" / "menomonie_app.pdf"

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "notes": {"type": "string"},
        "married": {"type": "boolean"},
        "status": {"enum": ["single", "married"]},
        "contact": {"type": "array", "items": {"enum": ["email", "phone"]}},
        "dob": {"type": "string", "format": "date"},
        "medications": {
            "type": "array",
            "items": {"type": "object", "properties": {"drug": {"type": "string"}, "dose": {"type": "string"}}},
        },
    },
}
TEXT = [{"at": (72, 60), "text": "Intake form"}]


def _tpl(pdf: bytes, *fields: Field, pages: int = 1) -> Template:
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint=fingerprint(pdf), page_count=pages, page_sizes=[(612.0, 792.0)] * pages,
                      kinds=["digital"] * pages),  # fmt: skip
        fields=list(fields),
    )


def _text(fid, path=None, rect=(100, 100, 300, 116), *, page=0, widget=None, style=None) -> Field:
    return Field(id=fid, type="text", page=page, rect=rect, existing_widget=widget,
                 source=Source(paths=[path]) if path else None, style=style or Style())  # fmt: skip


def _choice(fid, path, rect, *, type="checkbox", when=None, widget=None) -> Field:
    return Field(id=fid, type=type, page=0, rect=rect, existing_widget=widget,
                 source=Source(paths=[path], when=when) if path else None)  # fmt: skip


def _meds(max_rows=3, *, mapped=True, wrap=None) -> Field:
    seg = TableSegment(page=0, rect=(72, 300, 540, 300 + 20 * max_rows), row_top=300, row_height=20,
                       max_rows=max_rows,
                       columns=[Column(key="drug", x=(72, 300), wrap=wrap), Column(key="dose", x=(300, 540))])  # fmt: skip
    return Field(id="t_meds", type="table", segments=[seg],
                 source=Source(paths=["medications"]) if mapped else None)  # fmt: skip


def _flat_template(pdf: bytes) -> Template:
    return _tpl(
        pdf,
        _text("f_name", "name"),
        _choice("f_married", "married", (100, 130, 112, 142)),
        _choice("f_status_married", "status", (100, 160, 150, 174), type="choice_mark",
                when=Transform(op="equals", args={"value": "married"})),  # fmt: skip
        _choice("f_contact_email", "contact", (100, 190, 112, 202),
                when=Transform(op="contains", args={"value": "email"})),  # fmt: skip
        _meds(),
    )


def _values(out: bytes) -> dict:
    return {w.field_name: w.field_value for p in pymupdf.open(stream=out) for w in p.widgets()}


def _names(out: bytes) -> list[str]:
    return sorted(w.field_name for p in pymupdf.open(stream=out) for w in p.widgets())


def _codes(rep: FillReport) -> list[str]:
    return [w.code for w in rep.warnings]


def _snapshot(pdf: bytes, prefix: str) -> dict:
    """Object source and appearance streams of every widget (and its parent) named ``prefix*``."""
    doc = pymupdf.open(stream=pdf)
    out = {}
    for page in doc:
        for w in page.widgets():
            if not w.field_name.startswith(prefix):
                continue
            entry = [doc.xref_object(w.xref)]
            kind, val = doc.xref_get_key(w.xref, "Parent")
            if kind == "xref":
                entry.append(doc.xref_object(int(val.split()[0])))
            for key in ("AP/N", "AP/N/Off", "AP/N/Yes"):
                kind, val = doc.xref_get_key(w.xref, key)
                if kind == "xref":
                    entry.append(doc.xref_stream(int(val.split()[0])))
            out[(page.number, w.field_name, tuple(w.rect))] = entry
    return out


# ---------------------------------------------------------------- end to end


def test_fill_flat_form_end_to_end(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _flat_template(pdf)
    rec = {"name": "Ada Lovelace", "married": True, "status": "married", "contact": ["email"],
           "medications": [{"drug": "A", "dose": "1"}]}  # fmt: skip
    out, rep = fill(tpl, pdf, rec)
    values = _values(out)
    assert values["f_name"] == "Ada Lovelace" and values["t_meds_r0_drug"] == "A"
    assert values["t_meds_r0_dose"] == "1"
    assert values["f_married"] not in ("Off", None, False)
    assert values["f_status_married"] not in ("Off", None, False)
    assert values["f_contact_email"] not in ("Off", None, False)
    # every table slot exists, rows past the data are empty
    assert values["t_meds_r2_dose"] in ("", None)
    assert rep.paths["name"].rendered == {"f_name": "Ada Lovelace"} and rep.fingerprint_ok
    assert rep.paths["medications[].drug"].rendered == {"t_meds[0]": "A"}
    assert rep.paths["medications"].mapped and rep.paths["medications[].drug"].fields == ["t_meds"]
    assert rep.paths["dob"].mapped is False and rep.paths["dob"].has_value is False
    by_id = {f.id: f for f in rep.fields}
    assert by_id["f_name"].binding == "created" and by_id["f_name"].widget == "f_name"
    assert by_id["f_name"].value == "Ada Lovelace" and by_id["f_name"].font_size > 0
    assert by_id["f_married"].value is True
    (table,) = rep.tables
    assert (table.rows_provided, table.rows_written, table.rows_dropped) == (1, 1, 0)
    assert len(table.cells) == 6 and table.cells[0].widget == "t_meds_r0_drug"
    assert rep.counts["fields_written"] == 4 and rep.counts["cells_written"] == 2
    assert rep.gaps == []
    assert FillReport.from_json(rep.to_json()) == rep


def test_fill_accepts_path_and_bytes(make_pdf, tmp_path):
    pdf = make_pdf(text=TEXT)
    path = tmp_path / "blank.pdf"
    path.write_bytes(pdf)
    tpl = _tpl(pdf, _text("f_name", "name"))
    assert _values(fill(tpl, path, {"name": "x"})[0])["f_name"] == "x"
    assert _values(fill(tpl, str(path), {"name": "y"})[0])["f_name"] == "y"


def test_fill_fillable_sets_named_widgets_and_keeps_others(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "text", "name": "applicant", "rect": (100, 100, 300, 116)},
        {"type": "checkbox", "name": "agree", "rect": (100, 130, 112, 142), "on_state": "Yes_5"},
        {"type": "text", "name": "keep_text", "rect": (100, 200, 300, 216), "value": "keep"},
        {"type": "checkbox", "name": "keep_cb", "rect": (100, 230, 112, 242)},
        {"type": "text", "name": "keep_named", "rect": (100, 260, 300, 276), "value": "mine"},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _text("f_app", "name", (100, 100, 300, 116), widget="applicant"),
        _choice("f_agree", "married", (100, 130, 112, 142), widget="agree"),
        _text("f_unmapped", None, (100, 260, 300, 276), widget="keep_named"),  # unmapped: not touched
    )
    before = _snapshot(pdf, "keep")
    out, rep = fill(tpl, pdf, {"name": "Grace", "married": True})
    values = _values(out)
    assert values["applicant"] == "Grace" and values["agree"] == "Yes_5"
    assert _snapshot(out, "keep") == before
    assert _names(out) == _names(pdf)  # nothing created
    by_id = {f.id: f for f in rep.fields}
    assert by_id["f_app"].binding == "named" and by_id["f_app"].widget == "applicant"
    assert by_id["f_unmapped"].binding == "named" and by_id["f_unmapped"].value is None
    assert "f_unmapped" in rep.gaps


def test_existing_widget_font_max_len_and_flags_drive_fitting(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "text", "name": "cour", "rect": (100, 100, 220, 116), "font": "Cour"},
        {"type": "text", "name": "short", "rect": (100, 130, 300, 146), "maxlen": 4},
    ])  # fmt: skip
    text = "il" * 14
    # fits the 120pt-wide box in Helvetica at 9pt, but Courier is wider, so it must shrink
    assert pymupdf.get_text_length(text, fontname="Helv", fontsize=9) < 120 - 4
    assert pymupdf.get_text_length(text, fontname="Cour", fontsize=9) > 120 - 4
    tpl = _tpl(
        pdf,
        _text("f_cour", "name", (100, 100, 220, 116), widget="cour", style=Style(font_size=9)),
        _text("f_short", "notes", (100, 130, 300, 146), widget="short"),
    )
    out, rep = fill(tpl, pdf, {"name": text, "notes": "ABCDEFG"})
    by_id = {f.id: f for f in rep.fields}
    assert by_id["f_cour"].font_size < 9
    assert pymupdf.get_text_length(text, fontname="Cour", fontsize=by_id["f_cour"].font_size) <= 120 - 4 + 0.01
    assert _values(out)["short"] == "ABCD" and by_id["f_short"].value == "ABCD"
    assert [w.code for w in by_id["f_short"].warnings] == ["truncated"]
    assert by_id["f_short"].warnings[0].field == "f_short" and by_id["f_short"].warnings[0].path == "notes"


# ---------------------------------------------------------------- errors and validation


def test_fingerprint_mismatch_raises_unless_overridden(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"))
    tpl.form.fingerprint = "0123456789abcdef"
    with pytest.raises(FormcastError, match="fingerprint mismatch"):
        fill(tpl, pdf, {"name": "x"})
    with pytest.raises(FormcastError, match="fingerprint mismatch"):
        to_smart_pdf(tpl, pdf)
    out, rep = fill(tpl, pdf, {"name": "x"}, check_fingerprint=False)
    assert rep.fingerprint_ok is False and "fingerprint_mismatch" in _codes(rep)
    assert _values(out)["f_name"] == "x"
    assert to_smart_pdf(tpl, pdf, check_fingerprint=False)


def test_page_count_mismatch_raises(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"))
    tpl.form.page_count = 2
    with pytest.raises(FormcastError, match="page count mismatch: template has 2 pages, PDF has 1"):
        fill(tpl, pdf, {}, check_fingerprint=False)


def test_strict_raises_on_invalid_data(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"), _text("f_dob", "dob", (100, 130, 300, 146)))
    with pytest.raises(FormcastError, match="data does not match the schema: dob: "):
        fill(tpl, pdf, {"name": "x", "dob": "next Tuesday"}, strict=True)
    _, rep = fill(tpl, pdf, {"name": "x", "dob": "2024-01-31"}, strict=True)
    assert rep.strict is True and "invalid" not in _codes(rep)


def test_lenient_reports_invalid_and_fills_rest(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"), _text("f_dob", "dob", (100, 130, 300, 146)))
    out, rep = fill(tpl, pdf, {"name": "x", "dob": "next Tuesday"})
    values = _values(out)
    assert values["f_name"] == "x" and values["f_dob"] == "next Tuesday"
    assert rep.strict is False
    assert [w.code for w in rep.paths["dob"].warnings] == ["invalid"]
    assert "invalid" in _codes(rep)


def test_unmapped_data_reported(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"))
    _, rep = fill(tpl, pdf, {"name": "x", "notes": "remember this"})
    (w,) = [w for w in rep.warnings if w.code == "unmapped_data"]
    assert w.path == "notes"
    assert rep.paths["notes"].has_value and not rep.paths["notes"].mapped


def test_rows_dropped_top_level_warning(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _meds(max_rows=2))
    rows = [{"drug": d, "dose": "1"} for d in "ABC"]
    out, rep = fill(tpl, pdf, {"medications": rows})
    (table,) = rep.tables
    assert (table.rows_provided, table.rows_written, table.rows_dropped) == (3, 2, 1)
    assert table.rows_per_segment == [2]
    assert _codes(rep).count("rows_dropped") == 1
    assert _values(out)["t_meds_r1_drug"] == "B"


def test_overflow_kept_whole_and_reported(make_pdf):
    pdf = make_pdf(text=TEXT)
    long = "a value far too long for this little box " * 3
    tpl = _tpl(pdf, _text("f_name", "name", (100, 100, 160, 112), style=Style(min_font_size=5)))
    out, rep = fill(tpl, pdf, {"name": long})
    assert _values(out)["f_name"] == long
    (f,) = rep.fields
    assert f.value == long and f.font_size == 5
    assert [w.code for w in f.warnings] == ["overflow"]
    assert "overflow" in _codes(rep)


def test_shrunk_and_unencodable_reported(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name", (100, 100, 200, 116), style=Style(font_size=10)))
    _, rep = fill(tpl, pdf, {"name": "Nguyễn Thị Minh Khai Phường"})
    codes = [w.code for w in rep.fields[0].warnings]
    assert "shrunk" in codes and "unencodable" in codes


# ---------------------------------------------------------------- refill and shared fields


def test_refill_clears_previous_values(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _flat_template(pdf)
    first, _ = fill(tpl, pdf, {"name": "Ada", "married": True, "status": "married", "contact": ["email"],
                               "medications": [{"drug": "A", "dose": "1"}, {"drug": "B", "dose": "2"}]})  # fmt: skip
    second, rep = fill(tpl, first, {"married": False})
    values = {w.field_name: w.field_value for p in pymupdf.open(stream=second) for w in p.widgets()}
    assert values["f_name"] in ("", None) and values["f_married"] == "Off"
    assert values["f_status_married"] == "Off" and values["f_contact_email"] == "Off"
    assert all(values[f"t_meds_r{r}_{k}"] in ("", None) for r in range(3) for k in ("drug", "dose"))
    assert _names(second) == _names(first)  # every widget re-used, none stacked
    assert rep.fingerprint_ok
    assert {f.binding for f in rep.fields} == {"adopted"}
    assert "adopted" not in _codes(rep)  # its own earlier widgets (M5)
    # no trace of the first record anywhere in the output
    doc = pymupdf.open(stream=second)
    assert all("Ada" not in page.get_text() for page in doc)
    xrefs = range(1, doc.xref_length())
    assert all("Ada" not in doc.xref_object(x) for x in xrefs)
    assert all(b"Ada" not in (doc.xref_stream(x) or b"") for x in xrefs if doc.xref_is_stream(x))


def test_refill_with_fewer_rows_clears_later_rows(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _meds())
    first, _ = fill(tpl, pdf, {"medications": [{"drug": d, "dose": "1"} for d in "ABC"]})
    second, rep = fill(tpl, first, {"medications": [{"drug": "Z"}]})
    values = _values(second)
    assert values["t_meds_r0_drug"] == "Z" and values["t_meds_r0_dose"] in ("", None)
    assert values["t_meds_r1_drug"] in ("", None) and values["t_meds_r2_drug"] in ("", None)
    assert _names(second) == _names(first)


def test_shared_text_field_conflict_reported(make_pdf):
    # two kids of one text field "dup"; a field is named to each kid
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "text", "name": "dup", "rect": (100, 100, 300, 116)},
        {"type": "text", "name": "dup", "rect": (100, 400, 300, 416)},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _text("f1", "name", (100, 100, 300, 116), widget="dup"),
        _text("f2", "notes", (100, 400, 300, 416), widget="dup"),
    )
    _, rep = fill(tpl, pdf, {"name": "Ada", "notes": "Grace"})
    conflicts = [w for w in rep.warnings if w.code == "shared_field_conflict"]
    assert len(conflicts) == 1
    (c,) = conflicts
    assert c.field == "f2" and "f1" in c.message and "f2" in c.message and "dup" in c.message
    assert [w.code for w in {f.id: f for f in rep.fields}["f2"].warnings] == ["shared_field_conflict"]

    # identical values are harmless
    _, rep = fill(tpl, pdf, {"name": "Ada", "notes": "Ada"})
    assert "shared_field_conflict" not in _codes(rep)

    # one empty: the value wins regardless of order and nothing conflicts
    for rec in ({"name": "Ada"}, {"notes": "Ada"}):
        out, rep = fill(tpl, pdf, rec)
        assert _values(out)["dup"] == "Ada"
        assert "shared_field_conflict" not in _codes(rep)


def test_double_bound_checkbox_conflict_reported(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[{"type": "checkbox", "name": "agree", "rect": (100, 130, 112, 142)}])
    tpl = _tpl(
        pdf,
        _choice("c1", "married", (100, 130, 112, 142), widget="agree"),
        _choice("c2", "contact", (100, 130, 112, 142), widget="agree",
                when=Transform(op="contains", args={"value": "email"})),  # fmt: skip
    )
    _, rep = fill(tpl, pdf, {"married": True, "contact": ["phone"]})
    (c,) = [w for w in rep.warnings if w.code == "shared_field_conflict"]
    assert "c1" in c.message and "c2" in c.message
    _, rep = fill(tpl, pdf, {"married": True, "contact": ["email"]})
    assert "shared_field_conflict" not in _codes(rep)


# ---------------------------------------------------------------- tables over widgets, smart PDF


def test_table_over_fillable_cells_adopts_widgets(make_pdf):
    widgets = [
        {"type": "text", "name": f"Row{r + 1}_{k}", "rect": (x0 + 1, 300 + 20 * r + 1, x1 - 1, 320 + 20 * r - 1)}
        for r in range(3)
        for k, (x0, x1) in (("drug", (72, 300)), ("dose", (300, 540)))
    ]
    pdf = make_pdf(text=TEXT, widgets=widgets)
    tpl = _tpl(pdf, _meds())
    out, rep = fill(tpl, pdf, {"medications": [{"drug": "A", "dose": "1"}, {"drug": "B", "dose": "2"}]})
    assert _names(out) == _names(pdf)  # no new widgets
    values = _values(out)
    assert values["Row1_drug"] == "A" and values["Row2_dose"] == "2" and values["Row3_drug"] in ("", None)
    (table,) = rep.tables
    assert {c.binding for c in table.cells} == {"adopted"}
    assert table.cells[0].widget == "Row1_drug"


def test_to_smart_pdf_adds_empty_fields_only(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "text", "name": "keep_app", "rect": (100, 100, 300, 116), "value": "prefilled"},
        {"type": "text", "name": "keep_under", "rect": (100, 450, 300, 466), "value": "under"},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _text("f_app", "name", (100, 100, 300, 116), widget="keep_app"),
        _text("f_adopts", "notes", (98, 448, 302, 468)),  # adopts keep_under
        _text("f_new", None, (100, 500, 300, 516)),  # unmapped, created
        _choice("f_married", "married", (100, 130, 112, 142)),
        _choice("f_mark", "status", (100, 160, 150, 174), type="choice_mark",
                when=Transform(op="equals", args={"value": "married"})),  # fmt: skip
        _meds(),
    )
    before = _snapshot(pdf, "keep")
    out = to_smart_pdf(tpl, pdf)
    assert isinstance(out, bytes)
    values = _values(out)
    assert _snapshot(out, "keep") == before
    created = {"f_new", "f_married", "f_mark"} | {f"t_meds_r{r}_{k}" for r in range(3) for k in ("drug", "dose")}
    assert set(values) == created | {"keep_app", "keep_under"}
    assert all(values[n] in ("", None, "Off") for n in created)
    # a smart PDF can be filled afterwards like any blank
    tpl2 = tpl.model_copy(update={"form": tpl.form.model_copy(update={"fingerprint": fingerprint(out)})})
    filled, rep = fill(tpl2, out, {"name": "Ada", "medications": [{"drug": "A"}]})
    assert _values(filled)["keep_app"] == "Ada" and _values(filled)["t_meds_r0_drug"] == "A"
    assert _names(filled) == _names(out)


def test_unmapped_created_fields_created_empty_by_fill(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _text("f_name", "name"), _text("f_blank", None, (100, 500, 300, 516)), _meds(2, mapped=False))
    out, rep = fill(tpl, pdf, {"name": "x"})
    values = _values(out)
    assert values["f_blank"] in ("", None) and values["t_meds_r1_dose"] in ("", None)
    assert "f_blank" in rep.gaps and "t_meds" in rep.gaps


def test_wrapped_cell_is_multiline(make_pdf):
    pdf = make_pdf(text=TEXT)
    seg = TableSegment(page=0, rect=(72, 300, 540, 360), row_top=300, row_height=30, max_rows=2,
                       columns=[Column(key="drug", x=(72, 150), wrap=2), Column(key="dose", x=(150, 540))])  # fmt: skip
    tpl = _tpl(pdf, Field(id="t_meds", type="table", segments=[seg], source=Source(paths=["medications"])))
    out, rep = fill(tpl, pdf, {"medications": [{"drug": "acetaminophen extended release", "dose": "1"}]})
    doc = pymupdf.open(stream=out)
    (w,) = [w for w in doc[0].widgets() if w.field_name == "t_meds_r0_drug"]
    assert w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE
    assert w.field_value == "acetaminophen extended release"
    (empty,) = [w for w in doc[0].widgets() if w.field_name == "t_meds_r1_drug"]
    assert empty.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE  # typed into by hand later
    (dose,) = [w for w in doc[0].widgets() if w.field_name == "t_meds_r0_dose"]
    assert not dose.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE


def test_existing_widget_wraps_only_when_multiline(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[{"type": "text", "name": "box", "rect": (100, 100, 200, 140)}])
    long = "a sentence long enough to need several lines in this box"
    sizes = {}
    for label, style in (("own", Style()), ("explicit", Style(multiline=True))):
        tpl = _tpl(pdf, _text("f", "name", (100, 100, 200, 140), widget="box", style=style))
        out, rep = fill(tpl, pdf, {"name": long})
        sizes[label] = rep.fields[0].font_size
        (w,) = [w for w in pymupdf.open(stream=out)[0].widgets()]
        assert bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE) == (label == "explicit")
    # the widget's own single-line flag: shrink along one line; explicit multiline: wrap at a larger size
    assert sizes["explicit"] > sizes["own"]


# ---------------------------------------------------------------- report shape


def test_warnings_sorted_and_missing_collapsed(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(
        pdf,
        _text("f_b", "name", (100, 400, 300, 416)),
        _text("f_a", "name", (100, 100, 300, 116)),
        _text("f_c", "notes", (100, 200, 300, 216)),
        _text("f_long", "dob", (100, 600, 130, 612), style=Style(min_font_size=6)),
    )
    _, rep = fill(tpl, pdf, {"dob": "2024-01-31", "extra": 1})
    missing = [w for w in rep.warnings if w.code == "missing"]
    assert [w.path for w in missing] == ["name", "notes"]  # once per path
    assert missing[0].field == "f_a"  # the first box on the page
    per_field = {f.id: [w.code for w in f.warnings] for f in rep.fields}
    assert per_field["f_b"] == ["missing"] and per_field["f_a"] == ["missing"]

    def key(w):
        return (-1 if w.page is None else w.page, w.rect[1] if w.rect else -1, w.rect[0] if w.rect else -1)

    assert [key(w) for w in rep.warnings] == sorted(key(w) for w in rep.warnings)
    assert rep.warnings[0].page is None  # record-level first (unknown_keys)
    assert rep.counts["missing"] == 2
    assert set(rep.gaps) == {"f_a", "f_b", "f_c"}


@pytest.mark.slow
def test_corpus_fill_smoke():
    from formcast.pagemap.build import build_form_map
    from formcast.template.model import new_template

    schema = {"type": "object", "properties": {"choice": {"enum": ["Yes", "No"]}, "name": {"type": "string"}}}
    tpl = new_template(schema, build_form_map(MENOMONIE))
    kids = sorted((f for f in tpl.fields if f.existing_widget == "group_1"), key=lambda f: f.rect[0])
    assert len(kids) == 2
    for f, option in zip(kids, ("Yes", "No")):
        f.source = Source(paths=["choice"], when=Transform(op="equals", args={"value": option}))
    out, rep = fill(tpl, MENOMONIE, {"choice": "No"})
    assert rep.fingerprint_ok
    doc = pymupdf.open(stream=out)
    states = sorted((w.rect.x0, w.field_value) for p in doc for w in p.widgets() if w.field_name == "group_1")
    assert [v for _, v in states] == ["Off", "No"]
    second, _ = fill(tpl, out, {"choice": "Yes"})
    doc = pymupdf.open(stream=second)
    states = sorted((w.rect.x0, w.field_value) for p in doc for w in p.widgets() if w.field_name == "group_1")
    assert [v for _, v in states] == ["Yes_5", "Off"]


# ---------------------------------------------------------------- fix round 1


def _kid_states(out: bytes, name: str) -> list:
    """Each kid's visible state (/AS): PyMuPDF's field_value on a checkbox kid is the field's value."""
    doc = pymupdf.open(stream=out)
    kids = sorted((w for p in doc for w in p.widgets() if w.field_name == name), key=lambda w: (w.rect.y0, w.rect.x0))
    return [doc.xref_get_key(w.xref, "AS")[1].lstrip("/") for w in kids]


def _contains(v):
    return Transform(op="contains", args={"value": v})


def test_radio_kids_both_on_conflict_and_loser_reported_off(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "radio", "name": "grp", "rect": (100, 130, 112, 142), "on_state": "E"},
        {"type": "radio", "name": "grp", "rect": (150, 130, 162, 142), "on_state": "P"},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _choice("r1", "contact", (100, 130, 112, 142), type="radio_option", widget="grp", when=_contains("email")),
        _choice("r2", "contact", (150, 130, 162, 142), type="radio_option", widget="grp", when=_contains("phone")),
    )
    out, rep = fill(tpl, pdf, {"contact": ["email", "phone"]})
    assert _kid_states(out, "grp") == ["Off", "P"]
    by_id = {f.id: f for f in rep.fields}
    assert by_id["r1"].value is False and by_id["r2"].value is True  # the report tells the truth
    (c,) = [w for w in rep.warnings if w.code == "shared_field_conflict"]
    assert c.field == "r2" and "r1" in c.message and "r2" in c.message and "grp" in c.message
    # one option on: no conflict
    out, rep = fill(tpl, pdf, {"contact": ["email"]})
    assert _kid_states(out, "grp") == ["E", "Off"]
    assert "shared_field_conflict" not in _codes(rep)
    assert [f.value for f in rep.fields] == [True, False]


def test_shared_checkbox_with_distinct_on_states_conflicts(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "checkbox", "name": "sel", "rect": (100, 200, 112, 212), "on_state": "A"},
        {"type": "checkbox", "name": "sel", "rect": (150, 200, 162, 212), "on_state": "B"},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _choice("c1", "contact", (100, 200, 112, 212), widget="sel", when=_contains("email")),
        _choice("c2", "contact", (150, 200, 162, 212), widget="sel", when=_contains("phone")),
    )
    out, rep = fill(tpl, pdf, {"contact": ["email", "phone"]})
    assert _kid_states(out, "sel") == ["Off", "B"]
    assert [f.value for f in rep.fields] == [False, True]
    (c,) = [w for w in rep.warnings if w.code == "shared_field_conflict"]
    assert c.field == "c2" and "c1" in c.message
    _, rep = fill(tpl, pdf, {"contact": ["phone"]})
    assert "shared_field_conflict" not in _codes(rep)


def test_same_on_state_kids_share_a_value(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "checkbox", "name": "same", "rect": (100, 260, 112, 272)},
        {"type": "checkbox", "name": "same", "rect": (150, 260, 162, 272)},
    ])  # fmt: skip
    tpl = _tpl(
        pdf,
        _choice("s1", "contact", (100, 260, 112, 272), widget="same", when=_contains("email")),
        _choice("s2", "contact", (150, 260, 162, 272), widget="same", when=_contains("phone")),
    )
    out, rep = fill(tpl, pdf, {"contact": ["email", "phone"]})  # both on: harmless
    assert _kid_states(out, "same") == ["Yes", "Yes"]
    assert "shared_field_conflict" not in _codes(rep)
    # one on, one off: the kids share one value, so the "off" box shows on too; reported
    out, rep = fill(tpl, pdf, {"contact": ["phone"]})
    assert _kid_states(out, "same") == ["Yes", "Yes"]
    assert [f.value for f in rep.fields] == [True, True]
    (c,) = [w for w in rep.warnings if w.code == "shared_field_conflict"]
    assert c.field == "s2" and "s1" in c.message


def test_multi_path_field_warnings_reach_every_path(make_pdf):
    pdf = make_pdf(text=TEXT)
    joined = Field(id="f_both", type="text", page=0, rect=(100, 100, 130, 110), style=Style(min_font_size=6),
                   source=Source(paths=["name", "notes"], join=" "))  # fmt: skip
    lost = Field(id="f_lost", type="text", page=0, rect=(100, 200, 300, 216), existing_widget="nope",
                 source=Source(paths=["name", "notes"], join=" "))  # fmt: skip
    tpl = _tpl(pdf, joined, lost)
    _, rep = fill(tpl, pdf, {"name": "Ada Lovelace", "notes": "mathematician and writer"})
    for path in ("name", "notes"):
        codes = [(w.field, w.code) for w in rep.paths[path].warnings]
        assert ("f_both", "overflow") in codes and ("f_lost", "widget_not_found") in codes


def test_list_row_table_paths(make_pdf):
    pdf = make_pdf(text=TEXT)
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "array",
              "items": {"type": "string"}}}}}  # fmt: skip
    seg = TableSegment(page=0, rect=(72, 300, 540, 340), row_top=300, row_height=20, max_rows=2,
                       columns=[Column(key="0", x=(72, 300)), Column(key="1", x=(300, 320))])  # fmt: skip
    tpl = _tpl(pdf, Field(id="t", type="table", segments=[seg], source=Source(paths=["rows"])))
    tpl.schema_ = schema
    out, rep = fill(tpl, pdf, {"rows": [["a", "a long value for a narrow column"], [["x", "y"], "b"]]})
    assert _values(out)["t_r0_0"] == "a" and _values(out)["t_r1_0"] == "x, y"
    item = rep.paths["rows[]"]
    assert item.mapped and item.fields == ["t"]
    assert item.rendered == {"t[0][0]": "a", "t[0][1]": "a long value for a narrow column",
                             "t[1][0]": "x, y", "t[1][1]": "b"}  # fmt: skip
    codes = [(w.field, w.code) for w in item.warnings]
    assert ("t", "overflow") in codes and ("t", "list_joined") in codes  # fill's and resolve's cell warnings
    # every cell warning (fill's and resolve's) names a path the report lists
    assert all(w.path in rep.paths for t in rep.tables for c in t.cells for w in c.warnings)


def test_table_without_slots_is_a_gap(make_pdf):
    pdf = make_pdf(text=TEXT)
    seg = TableSegment(page=0, rect=(72, 300, 540, 340), row_top=300, row_height=20, max_rows=0,
                       columns=[Column(key="drug", x=(72, 300))])  # fmt: skip
    empty_cols = TableSegment(page=0, rect=(72, 400, 540, 440), row_top=400, row_height=20, max_rows=2, columns=[])
    tpl = _tpl(pdf, Field(id="t0", type="table", segments=[seg], source=Source(paths=["medications"])),
               Field(id="t1", type="table", segments=[empty_cols], source=Source(paths=["medications"])))  # fmt: skip
    _, rep = fill(tpl, pdf, {"medications": [{"drug": "A"}]})
    assert {"t0", "t1"} <= set(rep.gaps)
    assert [t.cells for t in rep.tables] == [[], []]


# ---------------------------------------------------------------- Ruling V: nothing visible -> not_shown


def _not_shown(rep: FillReport) -> list[tuple[str | None, str]]:
    return [(w.path, w.message) for w in rep.warnings if w.code == "not_shown"]


def test_enum_value_with_no_box_is_not_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    mark = _choice("f_status_married", "status", (100, 160, 150, 174), type="choice_mark",
                   when=Transform(op="equals", args={"value": "married"}))  # fmt: skip
    _, rep = fill(_tpl(pdf, mark), pdf, {"status": "single"})
    ((path, msg),) = _not_shown(rep)
    assert path == "status" and "'single'" in msg
    assert rep.warnings[0].severity == "warning" and rep.counts["not_shown"] == 1
    assert [w.code for w in rep.paths["status"].warnings] == ["not_shown"]
    _, rep = fill(_tpl(pdf, mark), pdf, {"status": "married"})
    assert rep.warnings == []


def test_multi_select_item_with_no_box_is_not_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    email = _choice("f_contact_email", "contact", (100, 190, 112, 202), when=_contains("email"))
    _, rep = fill(_tpl(pdf, email), pdf, {"contact": ["email", "phone"]})
    ((path, msg),) = _not_shown(rep)
    assert path == "contact" and "'phone'" in msg and "'email'" not in msg


def test_boolean_false_with_only_a_yes_box_is_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _choice("f_married", "married", (100, 130, 112, 142)))
    _, rep = fill(tpl, pdf, {"married": False})
    assert rep.warnings == []


def test_box_that_cannot_turn_on_is_not_shown(make_pdf):
    # C1 safety net: a form checkbox with no "on" appearance stays off; the True is reported
    pdf = make_pdf(text=TEXT, widgets=[{"type": "checkbox", "name": "cb", "rect": (100, 130, 112, 142)}])
    doc = pymupdf.open(stream=pdf)
    (w,) = doc[0].widgets()
    off = doc.xref_get_key(w.xref, "AP/N/Off")[1]
    doc.xref_set_key(w.xref, "AP", f"<</N<</Off {off}>>>>")
    pdf = doc.tobytes()
    tpl = _tpl(pdf, _choice("f_married", "married", (100, 130, 112, 142), widget="cb"))
    _, rep = fill(tpl, pdf, {"married": True})
    assert rep.fields[0].value is False
    ((path, msg),) = _not_shown(rep)
    assert path == "married" and "True" in msg


def test_escaped_on_state_box_is_shown(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "checkbox", "name": "cb", "rect": (100, 130, 112, 142), "on_state": "Yes#205"},
    ])  # fmt: skip
    tpl = _tpl(pdf, _choice("f_married", "married", (100, 130, 112, 142), widget="cb"))
    out, rep = fill(tpl, pdf, {"married": True})
    assert rep.fields[0].value is True and rep.warnings == []
    assert _kid_states(out, "cb") == ["Yes 5"]


def test_cell_emptied_by_its_transform_is_not_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    meds = _meds()
    meds.segments[0].columns[0].transforms = [Transform(op="slice", args={"start": 5})]
    _, rep = fill(_tpl(pdf, meds), pdf, {"medications": [{"drug": "Aspirin", "dose": "1"}, {"drug": "B", "dose": "2"}]})
    ((path, msg),) = _not_shown(rep)
    assert path == "medications[].drug" and "row 1" in msg and "'B'" in msg
    (cw,) = [w for c in rep.tables[0].cells for w in c.warnings if w.code == "not_shown"]
    assert cw.field == "t_meds" and cw.page == 0


def test_explained_values_are_not_also_not_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    tpl = _tpl(pdf, _meds(max_rows=1), _text("f_name", "name", widget="nope"))
    _, rep = fill(tpl, pdf, {"medications": [{"drug": d, "dose": "1"} for d in "AB"], "name": "x",
                             "notes": "unmapped"})  # fmt: skip
    assert sorted(_codes(rep)) == ["rows_dropped", "unmapped_data", "widget_not_found"]


def test_list_row_value_without_a_column_is_not_shown(make_pdf):
    pdf = make_pdf(text=TEXT)
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "array",
              "items": {"type": "string"}}}}}  # fmt: skip
    seg = TableSegment(page=0, rect=(72, 300, 540, 340), row_top=300, row_height=20, max_rows=2,
                       columns=[Column(key="0", x=(72, 300)), Column(key="1", x=(300, 540))])  # fmt: skip
    tpl = _tpl(pdf, Field(id="t", type="table", segments=[seg], source=Source(paths=["rows"])))
    tpl = tpl.model_copy(update={"schema_": schema})
    _, rep = fill(tpl, pdf, {"rows": [["a", "b", "c"]]})
    ((path, msg),) = _not_shown(rep)
    assert path == "rows[]" and "'c'" in msg


# ---------------------------------------------------------------- I2: unknown transforms raise


@pytest.mark.parametrize("where", ["transforms", "when", "column", "bad_args"])
def test_unknown_or_invalid_transform_raises(make_pdf, where):
    pdf = make_pdf(text=TEXT)
    bogus = Transform(op="no_such_op")
    if where == "transforms":
        f = _text("f_name", "name")
        f.source.transforms = [Transform(op="upper"), bogus]
    elif where == "when":
        f = _choice("f_married", "married", (100, 130, 112, 142), when=bogus)
    elif where == "column":
        f = _meds()
        f.segments[0].columns[1].transforms = [bogus]
    else:
        f = _text("f_name", "name")
        f.source.transforms = [Transform(op="slice", args={"start": "x"})]
    tpl = _tpl(pdf, f)
    with pytest.raises(FormcastError, match="f_name|f_married|t_meds"):
        fill(tpl, pdf, {"name": "x"})
    with pytest.raises(FormcastError):
        to_smart_pdf(tpl, pdf)


def test_data_dependent_transform_failure_stays_a_report_entry(make_pdf):
    pdf = make_pdf(text=TEXT)
    f = _text("f_dob", "dob")
    f.source.transforms = [Transform(op="date_format", args={"format": "%m/%d/%Y"})]
    _, rep = fill(_tpl(pdf, f), pdf, {"dob": "next Tuesday"})
    assert "transform_error" in _codes(rep)


# ---------------------------------------------------------------- I1: rotated pages


def _upright_lines(out: bytes, page: int = 0) -> dict[str, tuple[float, float]]:
    """Each text line's direction as the viewer sees it (after /Rotate)."""
    p = pymupdf.open(stream=out)[page]
    m = p.rotation_matrix
    rot = pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    out_ = {}
    for b in p.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            d = pymupdf.Point(ln["dir"]) * rot
            out_["".join(s["text"] for s in ln["spans"])] = (round(d.x, 3), round(d.y, 3))
    return out_


# On a /Rotate 90 page, a box that is wide on screen is tall in unrotated space.
ROT_RECT = {90: (100, 300, 118, 500), 270: (500, 300, 518, 500)}


@pytest.mark.parametrize("rot", [90, 270])
def test_created_text_on_rotated_page_reads_upright(make_pdf, rot):
    pdf = make_pdf(rotate=rot, text=[{"at": (150, 400), "text": "Name label", "rotate": rot}])
    out, rep = fill(_tpl(pdf, _text("f_name", "name", ROT_RECT[rot])), pdf, {"name": "Ada Lovelace"})
    lines = _upright_lines(out)
    assert lines["Name label"] == (1.0, 0.0)  # the form reads upright
    assert lines["Ada Lovelace"] == (1.0, 0.0)
    assert rep.warnings == [] and rep.fields[0].font_size >= 10


def test_existing_widget_with_mk_r_fits_along_its_text(make_pdf):
    pdf = make_pdf(rotate=90, text=[{"at": (150, 400), "text": "Name label", "rotate": 90}],
                   widgets=[{"type": "text", "name": "nm", "rect": ROT_RECT[90], "mk_r": 90}])  # fmt: skip
    out, rep = fill(_tpl(pdf, _text("f_name", "name", ROT_RECT[90], widget="nm")), pdf, {"name": "Ada Lovelace"})
    assert _upright_lines(out)["Ada Lovelace"] == (1.0, 0.0)
    assert rep.warnings == [] and rep.fields[0].font_size >= 10


def test_created_boxes_on_rotated_page(make_pdf):
    pdf = make_pdf(rotate=90, text=[{"at": (150, 400), "text": "Yes", "rotate": 90}])
    mark_rect = (138, 395, 156, 425)
    tpl = _tpl(pdf, _choice("f_married", "married", (200, 300, 212, 312)),
               _choice("f_status", "status", mark_rect, type="choice_mark",
                       when=Transform(op="equals", args={"value": "married"})))  # fmt: skip
    out, rep = fill(tpl, pdf, {"married": True, "status": "married"})
    doc = pymupdf.open(stream=out)
    by = {w.field_name: w for w in doc[0].widgets()}
    for name in ("f_married", "f_status"):
        assert doc.xref_get_key(by[name].xref, "MK/R") == ("int", "90")
    assert rep.warnings == []
    # the ellipse still lands in the mark's box
    page = doc[0]
    clip = pymupdf.Rect(mark_rect) * page.rotation_matrix
    blank = pymupdf.open(stream=pdf)[0]

    def dark(p):
        pix = p.get_pixmap(clip=clip, dpi=144)
        s, n = pix.samples, pix.n
        return sum(1 for i in range(0, len(s), n) if min(s[i : i + 3]) < 128)

    assert dark(page) > dark(blank) + 50
    assert _close_rect(by["f_status"].rect, mark_rect)


def _close_rect(a, b, tol=0.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ---------------------------------------------------------------- M3: combo display text


def test_combo_written_by_display_text_reports_the_export_value(make_pdf):
    pdf = make_pdf(text=TEXT, widgets=[
        {"type": "combo", "name": "st", "rect": (100, 100, 300, 116),
         "options": [["WI", "Wisconsin"], ["MN", "Minnesota"]]},
    ])  # fmt: skip
    tpl = _tpl(pdf, _text("f_state", "name", widget="st"))
    out, rep = fill(tpl, pdf, {"name": "Wisconsin"})
    assert _values(out)["st"] == "WI"
    assert rep.fields[0].value == "WI" and rep.paths["name"].rendered == {"f_state": "WI"}
    assert rep.warnings == []
