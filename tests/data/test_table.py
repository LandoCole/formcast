"""formcast.data.table: reading a user's data (CSV, TSV, Excel, JSON, pasted text) and its own schema."""

from __future__ import annotations

import datetime as dt
import io
import json

import pytest

from formcast.data import data_schema, profile, read_table, sniff_text
from formcast.errors import FormcastError

CSV = b'Name,DOB,Salary,Citizen?\nAnn Lee,03/14/1990,"$41,200",Y\nBo Chen,1985-01-02,58900,N\n'


def test_a_csv_reads_as_columns_and_rows_with_quoted_commas_kept():
    t = read_table(CSV, "people.csv")
    assert t.kind == "csv"
    assert t.columns == ["Name", "DOB", "Salary", "Citizen?"]
    assert t.rows[0] == {"Name": "Ann Lee", "DOB": "03/14/1990", "Salary": "$41,200", "Citizen?": "Y"}
    assert len(t.rows) == 2


def test_blank_and_repeated_headers_get_names_and_empty_cells_are_none():
    t = read_table(b"a,,a\n1,,\n", "x.csv")
    assert t.columns == ["a", "column_2", "a_2"]
    assert t.rows == [{"a": "1", "column_2": None, "a_2": None}]


def test_a_semicolon_or_tab_file_is_sniffed():
    assert read_table(b"a;b\n1;2\n", "x.csv").rows == [{"a": "1", "b": "2"}]
    assert read_table(b"a\tb\n1\t2\n", "x.tsv").rows == [{"a": "1", "b": "2"}]


def test_windows_1252_text_is_read():
    assert read_table("name\nJosé\n".encode("cp1252"), "x.csv").rows == [{"name": "José"}]


def test_json_records_are_flattened_into_dotted_columns():
    data = [{"applicant": {"name": "Ann", "tags": ["a", "b"]}, "refs": [{"phone": "1"}, {"phone": "2"}]}]
    t = read_table(json.dumps(data).encode(), "x.json")
    assert t.kind == "json"
    assert t.columns == ["applicant.name", "applicant.tags", "refs[0].phone", "refs[1].phone"]
    assert t.rows[0]["applicant.tags"] == ["a", "b"]


def test_one_json_object_or_an_object_holding_records_is_a_table():
    assert len(read_table(b'{"name": "Ann"}', "x.json").rows) == 1
    assert len(read_table(b'{"records": [{"a": 1}, {"a": 2}]}', "x.json").rows) == 2


@pytest.mark.parametrize("data", [b"[1, 2]", b"[]", b'"text"'])
def test_json_that_is_not_records_is_refused_plainly(data):
    with pytest.raises(FormcastError, match="not a list of records"):
        read_table(data, "x.json")


def test_old_xls_is_refused_with_what_to_do():
    with pytest.raises(FormcastError, match=r"\.xlsx or \.csv"):
        read_table(b"\xd0\xcf\x11\xe0", "x.xls")


def test_an_excel_sheet_keeps_numbers_and_turns_dates_into_iso_text():
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Applicants"
    sheet.append(["Name", "Born", "Children", "Rate"])
    sheet.append(["Ann", dt.datetime(1990, 3, 14), 2.0, 12.5])
    buf = io.BytesIO()
    book.save(buf)
    t = read_table(buf.getvalue(), "people.xlsx")
    assert (t.kind, t.sheet) == ("xlsx", "Applicants")
    assert t.rows == [{"Name": "Ann", "Born": "1990-03-14", "Children": 2, "Rate": 12.5}]


def test_pasted_text_that_is_a_table_or_json_becomes_a_table():
    assert sniff_text('First,Last,Salary\nAnn,Lee,"$41,200"\n').rows[0]["Salary"] == "$41,200"
    assert sniff_text('[{"a": 1}]').columns == ["a"]
    assert sniff_text("a\tb\tc\n1\t2\t3").columns == ["a", "b", "c"]
    assert sniff_text("a,b\n1,2\n3,4").columns == ["a", "b"]


@pytest.mark.parametrize(
    "text",
    [
        "My name is Ann Lee, and I live in Menomonie, WI.",
        "Hi, I am Priya.\nCall me at 555, ok?",  # two lines of prose with a comma each
        "This line is a long sentence that happens to have, somewhere in the middle of it, commas, a, few\nx,y,z,w,v",
    ],
)
def test_prose_stays_text(text):
    assert sniff_text(text) is None


def test_profile_types_each_column_and_keeps_samples():
    cols = {c.name: c for c in profile(read_table(CSV, "x.csv"))}
    assert cols["Name"].type == "string"
    assert cols["DOB"].type == "date"
    assert cols["Salary"].type == "string"  # "$41,200" is not a plain number
    assert cols["Citizen?"].samples == ["Y", "N"]
    zips = profile(read_table(b"zip\n02134\n54751\n", "x.csv"))[0]
    assert zips.type == "string"  # a leading zero: a code, not a number


def test_data_schema_is_a_json_schema_of_the_columns():
    schema = data_schema(read_table(b"name,age,when\nAnn,30,2020-01-02\n", "people.csv"))
    assert schema["title"] == "people.csv"
    assert schema["properties"]["age"]["type"] == "integer"
    assert schema["properties"]["when"] == {"type": "string", "format": "date", "examples": ["2020-01-02"]}
