"""Export: a finished run as xlsx, Parquet or CSV, with the values, statuses, field definitions and set-aside list."""

import csv
import sys
from pathlib import Path

import pytest

from formcast.errors import FormcastError
from formcast.extract.batch import Results, run_batch
from formcast.extract.export import FIELD_COLUMNS, SET_ASIDE_COLUMNS, _checks, _kind, export
from formcast.extract.model import ExtractionTemplate
from formcast.schema import flatten_schema
from tests.extract.conftest import write_corpus
from tests.extract.corpus import OWNER_ROWS, clients, hand_template


@pytest.fixture(scope="module")
def finished(tmp_path_factory) -> Results:
    """The corpus (20 filled forms, 2 of another form) run once with the hand template."""
    base = tmp_path_factory.mktemp("export")
    folder = write_corpus(base / "corpus")
    run_batch([hand_template()], sorted(folder.glob("*.pdf")), base / "out", workers=1)
    return Results.load(base / "out")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Stub:
    """A finished run with hand-made rows, for the cells a PDF cannot easily produce."""

    def __init__(self, rows, status_rows=None, set_aside=()):
        self._rows, self._status, self._aside = rows, status_rows or [], set_aside

    def rows(self):
        return iter(self._rows)

    def status_rows(self):
        return iter(self._status)

    def set_aside(self):
        return iter(self._aside)


def doc_row(**cells):
    return {"doc_id": "a.pdf", "file": "/x/a.pdf", "template": "client_information", "matched": "layout",
            "extracted_at": "2026-10-28T00:00:00", **cells}  # fmt: skip


# ------------------------------------------------------------------------ xlsx


def test_xlsx_has_values_status_fields_and_set_aside_sheets(finished, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    written = export(finished, [hand_template()], tmp_path / "result.xlsx", format="xlsx")
    assert written == [tmp_path / "result.xlsx"]
    book = openpyxl.load_workbook(written[0])
    assert book.sheetnames == ["values", "status", "fields", "set_aside"]
    values = list(book["values"].iter_rows(values_only=True))
    header = list(values[0])
    assert header[:5] == ["doc_id", "file", "template", "matched", "extracted_at"]
    assert len(values) == 21  # header + 20 documents; the 2 other forms are not rows
    by_id = {row[0]: dict(zip(header, row)) for row in values[1:]}
    people = clients(20)
    assert by_id["client_00001.pdf"]["investor.tax_id"] == people[0].tax_id
    assert by_id["client_00001.pdf"]["owners_1_name"] == people[0].owners[0][0]
    status = list(book["status"].iter_rows(values_only=True))
    assert list(status[0]) == header and len(status) == 21
    assert dict(zip(header, status[1]))["investor.tax_id"] == "found"
    fields = list(book["fields"].iter_rows(values_only=True))
    assert list(fields[0]) == list(FIELD_COLUMNS)
    assert [row[0] for row in fields[1:]] == header[5:]  # one definition per value column, in column order
    aside = list(book["set_aside"].iter_rows(values_only=True))
    assert list(aside[0]) == list(SET_ASIDE_COLUMNS)
    assert [(row[0], row[2], row[3], row[4]) for row in aside[1:]] == [
        ("client_00021.pdf", "no template fits", "Client information", 0.0),
        ("client_00022.pdf", "no template fits", "Client information", 0.0),
    ]


def test_xlsx_keeps_text_that_looks_like_a_formula_as_text(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    run = Stub([doc_row(**{"investor.legal_name": "=1+1", "owners_1_name": "bad\x00name"})])
    export(run, [hand_template()], tmp_path / "x.xlsx", format="xlsx")
    sheet = openpyxl.load_workbook(tmp_path / "x.xlsx")["values"]
    cells = {head.value: cell for head, cell in zip(sheet[1], sheet[2])}
    assert cells["investor.legal_name"].value == "=1+1" and cells["investor.legal_name"].data_type == "s"
    assert cells["owners_1_name"].value == "badname"  # a control character xlsx cannot hold is dropped


# ----------------------------------------------------------------------- parquet


def test_parquet_round_trips_values(finished, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    written = export(finished, [hand_template()], tmp_path / "pq", format="parquet")
    assert [p.name for p in written] == ["values.parquet", "status.parquet", "fields.parquet", "set_aside.parquet"]
    values = pq.read_table(tmp_path / "pq" / "values.parquet")
    assert values.num_rows == 20
    assert pq.read_table(tmp_path / "pq" / "status.parquet").column_names == values.column_names
    assert pq.read_table(tmp_path / "pq" / "set_aside.parquet").num_rows == 2
    first = values.to_pylist()[0]
    assert first["investor.tax_id"] == clients(20)[0].tax_id and first["doc_id"] == "client_00001.pdf"
    assert pq.read_table(tmp_path / "pq" / "fields.parquet").column_names == list(FIELD_COLUMNS)


def test_parquet_columns_that_mix_types_are_text(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    run = Stub([doc_row(**{"investor.legal_name": 5}), doc_row(**{"investor.legal_name": "Dana"})])
    export(run, [hand_template()], tmp_path / "pq", format="parquet")
    table = pq.read_table(tmp_path / "pq" / "values.parquet")
    assert table.column("investor.legal_name").to_pylist() == ["5", "Dana"]
    assert str(pq.read_table(tmp_path / "pq" / "set_aside.parquet").schema.field("closest_score").type) == "double"


# --------------------------------------------------------------------------- csv


def test_csv_writes_four_files(finished, tmp_path):
    written = export(finished, [hand_template()], tmp_path / "csv", format="csv")
    assert [p.name for p in written] == ["values.csv", "status.csv", "fields.csv", "set_aside.csv"]
    assert all(p.parent == tmp_path / "csv" for p in written)
    values = read_csv(written[0])
    assert len(values) == 20 and values[0]["doc_id"] == "client_00001.pdf"
    people = clients(20)
    assert values[0]["owners_1_name"] == people[0].owners[0][0]
    assert values[0][f"owners_{OWNER_ROWS}_name"] == ""  # no such owner: empty, and `absent` in the status file
    status = read_csv(written[1])
    assert len(status) == 20 and status[0][f"owners_{OWNER_ROWS}_name"] == "absent"
    assert list(values[0]) == list(status[0])
    aside = read_csv(written[3])
    assert [(r["doc_id"], r["reason"], r["closest_template"], r["closest_score"]) for r in aside] == [
        ("client_00021.pdf", "no template fits", "Client information", "0.0"),
        ("client_00022.pdf", "no template fits", "Client information", "0.0"),
    ]
    names = {r["doc_id"] for r in values} | {r["doc_id"] for r in status}
    assert names.isdisjoint({"client_00021.pdf", "client_00022.pdf"})


def test_an_unreadable_file_is_set_aside_with_its_error(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    run_batch([hand_template()], [folder / "broken.pdf"], tmp_path / "out", workers=1)
    export(Results.load(tmp_path / "out"), [hand_template()], tmp_path / "csv", format="csv")
    assert read_csv(tmp_path / "csv" / "values.csv") == []
    [row] = read_csv(tmp_path / "csv" / "set_aside.csv")
    assert row["doc_id"] == "broken.pdf" and row["reason"] == "cannot open PDF"
    assert (row["closest_template"], row["closest_score"]) == ("", "")


def test_values_columns_follow_the_schema_with_repeated_items_in_order(tmp_path):
    export(Stub([]), [hand_template()], tmp_path / "csv", format="csv")
    header = (tmp_path / "csv" / "values.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header[:5] == ["doc_id", "file", "template", "matched", "extracted_at"]
    assert header[5:] == [
        "investor.legal_name", "investor.tax_id", "investor.dob", "investor.type", "investor.country",
        "address.street", "address.city_state_zip",
        *[f"owners_{i}_{leaf}" for i in range(1, OWNER_ROWS + 1) for leaf in ("name", "percent")],
        "wire.bank", "wire.account",
    ]  # fmt: skip


# ------------------------------------------------------------------------ fields


def test_fields_describe_each_column_with_its_checks(tmp_path):
    export(Stub([]), [hand_template()], tmp_path / "csv", format="csv")
    fields = {r["column"]: r for r in read_csv(tmp_path / "csv" / "fields.csv")}
    assert list(fields)[:2] == ["investor.legal_name", "investor.tax_id"]
    tin = fields["investor.tax_id"]
    assert tin["path"] == "investor.tax_id" and tin["title"] == "Taxpayer ID" and tin["type"] == "string"
    assert tin["definition"] == "The investor's EIN (12-3456789) or SSN (123-45-6789)."
    assert tin["checks"] == r"required; pattern ^\d{2}-\d{7}$|^\d{3}-\d{2}-\d{4}$"
    kind = fields["investor.type"]
    assert kind["type"] == "enum" and kind["checks"] == "enum: individual, joint, trust, llc"
    dob = fields["investor.dob"]
    assert dob["type"] == "date" and dob["checks"] == "format: date"
    owner = fields["owners_2_name"]
    assert owner["path"] == "owners[1].name" and owner["title"] == "Owner name" and owner["checks"] == ""
    assert fields["investor.legal_name"]["checks"] == ""


def test_checks_name_every_limit_and_when_a_field_is_required():
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 99}}},
            "b": {"type": "object", "required": ["s"], "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 9}}},
            "rows": {"type": "array", "maxItems": 2, "items": {
                "type": "object", "required": ["v"], "properties": {"v": {"type": "number", "exclusiveMinimum": 0}}}},
            "tags": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": ["x", "y"]}},
        },
    }  # fmt: skip
    got = {sp.path: _checks(schema, sp) for sp in flatten_schema(schema)}
    assert got["a.n"] == "required; minimum: 0; maximum: 99"
    assert got["b.s"] == "required when b is given; min length: 2; max length: 9"
    assert got["rows[].v"] == "required in each item of rows; exclusive minimum: 0"
    assert got["tags"] == "enum: x, y; min items: 1"


# ----------------------------------------------------------------------- guards


def test_a_missing_extra_names_it(monkeypatch, tmp_path):
    for name in ("pyarrow", "pyarrow.parquet"):
        monkeypatch.setitem(sys.modules, name, None)  # `import pyarrow` now raises ImportError
    with pytest.raises(FormcastError, match="parquet extra") as caught:
        export(Stub([]), [hand_template()], tmp_path / "pq", format="parquet")
    assert str(caught.value) == "Parquet export needs the parquet extra: pip install 'formcast[parquet]'"
    assert not (tmp_path / "pq").exists()  # nothing written before the failure


def test_a_missing_xlsx_extra_names_it(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(FormcastError, match=r"xlsx extra: pip install 'formcast\[xlsx\]'"):
        export(Stub([]), [hand_template()], tmp_path / "x.xlsx", format="xlsx")
    assert not (tmp_path / "x.xlsx").exists()


def test_an_unknown_format_and_a_file_where_a_folder_goes_are_refused(tmp_path):
    with pytest.raises(FormcastError, match="unknown export format"):
        export(Stub([]), [hand_template()], tmp_path / "x", format="ods")  # type: ignore[arg-type]
    (tmp_path / "taken").write_text("x")
    with pytest.raises(FormcastError, match="is a file"):
        export(Stub([]), [hand_template()], tmp_path / "taken", format="csv")


def test_a_document_read_with_a_template_not_given_is_refused(tmp_path):
    run = Stub([doc_row(**{"other.column": "x"})])
    with pytest.raises(FormcastError, match="other.column"):
        export(run, [hand_template()], tmp_path / "csv", format="csv")


def test_value_columns_of_several_templates_are_their_union(tmp_path):
    base = hand_template()
    schema = {"type": "object", "properties": {"extra": {"type": "string", "title": "Extra", "description": "d"}}}
    second = ExtractionTemplate.model_validate(
        {**base.model_dump(by_alias=True), "template_id": "second", "schema": schema, "readers": {}, "absent": []}
    )
    export(Stub([]), [base, second], tmp_path / "csv", format="csv")
    header = (tmp_path / "csv" / "values.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header[-1] == "extra" and header.count("investor.tax_id") == 1


def template_with(schema: dict, template_id: str = "other") -> ExtractionTemplate:
    """The hand template with its schema (and no readers) replaced."""
    base = hand_template()
    return ExtractionTemplate.model_validate(
        {**base.model_dump(by_alias=True), "template_id": template_id, "schema": schema, "readers": {}, "absent": []}
    )


def test_with_names_each_part_but_the_fields_has_a_name_column_after_doc_id_in_every_format(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    run = Stub(
        [doc_row(doc_id="0.pdf"), doc_row(doc_id="1.pdf")],
        [doc_row(doc_id="0.pdf"), doc_row(doc_id="1.pdf")],
        [{"doc_id": "2.pdf", "file": "/x/2.pdf", "reason": "no template fits", "closest_template": "C", "closest_score": 0.5}],
    )
    names = {"0.pdf": "Acme W-9.pdf", "2.pdf": "scan.pdf"}  # 1.pdf has no name: it is named by its doc_id
    written = export(run, [hand_template()], tmp_path / "csv", format="csv", names=names)
    values, status, fields, aside = (read_csv(p) for p in written)
    for part in (values, status):
        assert list(part[0])[:3] == ["doc_id", "name", "file"]
        assert [(r["doc_id"], r["name"]) for r in part] == [("0.pdf", "Acme W-9.pdf"), ("1.pdf", "1.pdf")]
    assert list(aside[0])[:3] == ["doc_id", "name", "file"] and aside[0]["name"] == "scan.pdf"
    assert "name" not in [f["column"] for f in fields]
    book = openpyxl.load_workbook(export(run, [hand_template()], tmp_path / "r.xlsx", format="xlsx", names=names)[0])
    for sheet in ("values", "status", "set_aside"):
        assert list(next(book[sheet].iter_rows(values_only=True)))[:2] == ["doc_id", "name"]
    assert [row[1] for row in book["values"].iter_rows(min_row=2, values_only=True)] == ["Acme W-9.pdf", "1.pdf"]
    without = read_csv(export(run, [hand_template()], tmp_path / "plain", format="csv")[0])
    assert "name" not in without[0]  # no names, no column


def test_with_names_a_field_named_name_is_refused_as_a_document_column(tmp_path):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with pytest.raises(FormcastError, match="'name'"):
        export(Stub([]), [template_with(schema)], tmp_path / "csv", format="csv", names={})
    assert export(Stub([]), [template_with(schema)], tmp_path / "plain", format="csv")  # without names it is a field


def test_a_field_named_like_a_document_column_is_refused_before_anything_is_written(tmp_path):
    for name in ("doc_id", "file", "template", "matched", "extracted_at"):
        schema = {"type": "object", "properties": {name: {"type": "string"}}}
        with pytest.raises(FormcastError, match=f"'{name}'"):
            export(Stub([]), [template_with(schema)], tmp_path / "csv", format="csv")
    assert not (tmp_path / "csv").exists()


def test_templates_that_make_one_column_from_different_paths_or_types_are_refused(tmp_path):
    def schema(**props):
        return {"type": "object", "properties": {k: {"type": t} for k, t in props.items()}}

    other_type = [template_with(schema(size="integer"), "a"), template_with(schema(size="string"), "b")]
    with pytest.raises(FormcastError, match="'size'"):
        export(Stub([]), other_type, tmp_path / "one", format="csv")
    # owners[0].name and a top-level owners_1_name both make the column owners_1_name
    nested = {"type": "object", "properties": {"owners": {"type": "array", "maxItems": 1, "items": {
        "type": "object", "properties": {"name": {"type": "string"}}}}}}  # fmt: skip
    with pytest.raises(FormcastError, match="owners_1_name"):
        export(Stub([]), [template_with(nested, "a"), template_with(schema(owners_1_name="string"), "b")],
               tmp_path / "two", format="csv")  # fmt: skip
    assert not (tmp_path / "one").exists() and not (tmp_path / "two").exists()
    same = [template_with(schema(size="integer"), "a"), template_with(schema(size="integer"), "b")]
    export(Stub([]), same, tmp_path / "ok", format="csv")  # agreeing templates share the column


# ------------------------------------------------------------ parquet column types


def test_parquet_kinds_follow_the_values():
    assert _kind([True, None, False]) == "bool"
    assert _kind([1, None, -(2**62)]) == "int"
    assert _kind([1.5, None, 2.0]) == "float"
    assert _kind([1, 2.5]) == "float"
    assert _kind([None, None]) == "string" and _kind([]) == "string"
    assert _kind(["a", None]) == "string" and _kind([True, 1]) == "string" and _kind(["a", 1]) == "string"
    # a whole number a 64-bit column or an exact double cannot hold is text, never a crash or lost digits
    assert _kind([2**70, 1]) == "string" and _kind([2**63, 1]) == "string" and _kind([-(2**63)]) == "string"
    assert _kind([2**53 + 1, 0.5]) == "string" and _kind([2**53, 0.5]) == "float"


def test_parquet_columns_are_typed_from_the_values(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    run = Stub([
        doc_row(**{"investor.legal_name": True, "address.street": 1, "address.city_state_zip": 1.5}),
        doc_row(**{"investor.legal_name": False, "address.street": 2, "address.city_state_zip": 2.5}),
    ])  # fmt: skip
    export(run, [hand_template()], tmp_path / "pq", format="parquet")
    schema = pq.read_table(tmp_path / "pq" / "values.parquet").schema
    types = {name: str(schema.field(name).type) for name in schema.names}
    assert types["investor.legal_name"] == "bool"
    assert types["address.street"] == "int64" and types["address.city_state_zip"] == "double"
    assert types["investor.tax_id"] == "string"  # all empty
    assert types["doc_id"] == "string"


def test_parquet_writes_a_whole_number_too_big_for_64_bits_as_text(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    run = Stub([doc_row(**{"wire.account": 2**70}), doc_row(**{"wire.account": 1}), doc_row(**{"wire.account": None})])
    export(run, [hand_template()], tmp_path / "pq", format="parquet")
    table = pq.read_table(tmp_path / "pq" / "values.parquet")
    assert table.column("wire.account").to_pylist() == [str(2**70), "1", None]
