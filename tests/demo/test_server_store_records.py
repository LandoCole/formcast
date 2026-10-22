"""demo/server app layer: settings, template store, record sources, and the generated demo data.

Settings tests always pass a mapping, so the repository's real ``.env`` is never read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from demo.server import settings as settings_mod
from demo.server.records import CsvRecords, MultiRecords, RecordSourceError, SqliteRecords
from demo.server.settings import Settings, read_dotenv
from demo.server.store import FolderTemplateStore, OverlayTemplateStore, ShippedTemplateError, StoredTemplateError
from formcast import Template, fill

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "demo" / "data"
FORMS = ("fw9", "wadoc_auth", "menomonie_app")


# ------------------------------------------------------------------ settings


def test_settings_defaults_from_an_empty_mapping():
    s = Settings.from_env({})
    assert (s.host, s.port) == ("127.0.0.1", 8765)
    assert (s.reuse_threshold, s.adapt_threshold) == (0.95, 0.60)
    assert (s.max_upload_bytes, s.max_json_bytes) == (50 * 2**20, 20 * 2**20)
    assert s.data_dir == DATA
    assert s.llm is None


def test_settings_read_the_mapping_and_never_the_dotenv_file(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a mapping was passed, so .env must not be read")

    monkeypatch.setattr(settings_mod, "read_dotenv", boom)
    s = Settings.from_env({
        "FORMCAST_DEMO_BASE_URL": "https://llm.example.com/v1", "FORMCAST_DEMO_API_KEY": "sk-secret-123",
        "FORMCAST_DEMO_MODEL": "m-1", "FORMCAST_DEMO_API_FORMAT": "responses",
        "FORMCAST_DEMO_HOST": "0.0.0.0", "FORMCAST_DEMO_PORT": "9000", "FORMCAST_DEMO_DATA_DIR": "/tmp/somewhere",
    })  # fmt: skip
    assert (s.host, s.port) == ("0.0.0.0", 9000)
    assert s.data_dir == Path("/tmp/somewhere").resolve()
    assert (s.llm.base_url, s.llm.model, s.llm.api_format) == ("https://llm.example.com/v1", "m-1", "responses")
    assert s.llm.api_key == "sk-secret-123"
    assert "sk-secret-123" not in repr(s)


@pytest.mark.parametrize("missing", ["BASE_URL", "API_KEY", "MODEL"])
def test_llm_is_none_unless_url_key_and_model_are_all_set(missing):
    env = {"FORMCAST_DEMO_BASE_URL": "https://x.example.com", "FORMCAST_DEMO_API_KEY": "k", "FORMCAST_DEMO_MODEL": "m"}
    del env[f"FORMCAST_DEMO_{missing}"]
    assert Settings.from_env(env).llm is None
    assert Settings.from_env({**env, f"FORMCAST_DEMO_{missing}": "  "}).llm is None


def test_a_bad_port_is_an_error_that_does_not_echo_values():
    with pytest.raises(ValueError, match="FORMCAST_DEMO_PORT") as e:
        Settings.from_env({"FORMCAST_DEMO_PORT": "eighty"})
    assert "eighty" not in str(e.value)


def test_dotenv_parser(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# comment\n\nA=1\nexport B="two words"\nC = \'3\'\nnot a line\nD=\n', encoding="utf-8")
    assert read_dotenv(p) == {"A": "1", "B": "two words", "C": "3", "D": ""}
    assert read_dotenv(tmp_path / "missing") == {}


def test_from_env_without_a_mapping_overlays_the_environment_on_the_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "read_dotenv", lambda path: {"FORMCAST_DEMO_PORT": "1111", "FORMCAST_DEMO_HOST": "h"})
    monkeypatch.setitem(os.environ, "FORMCAST_DEMO_PORT", "2222")
    s = Settings.from_env()
    assert (s.host, s.port) == ("h", 2222)


# ------------------------------------------------------------------ template store


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_json((DATA / "templates" / "wadoc_auth.json").read_text(encoding="utf-8"))


def test_store_round_trip(tmp_path, template):
    store = FolderTemplateStore(tmp_path / "templates")  # created on first put
    assert store.list() == []
    store.put("Release form v1", template)
    store.put("a", template)
    listed = store.list()
    assert [t["name"] for t in listed] == ["Release form v1", "a"]
    assert listed[0]["template_id"] == template.template_id
    assert listed[0]["page_count"] == template.form.page_count
    assert listed[0]["updated"].endswith("+00:00")
    assert store.get("a") == template
    store.delete("a")
    assert [t["name"] for t in store.list()] == ["Release form v1"]
    with pytest.raises(KeyError):
        store.get("a")
    with pytest.raises(KeyError):
        store.delete("a")
    assert sorted(p.name for p in (tmp_path / "templates").iterdir()) == ["Release form v1.json"]


@pytest.mark.parametrize(
    "name", ["../x", "/etc/x", "a/b", "a\\b", "", ".hidden", "-x", " x", "x\n", "a" * 81, "..", "é", "名前", "a\x00b", "x/../y"]
)
def test_store_rejects_bad_names(tmp_path, template, name):
    store = FolderTemplateStore(tmp_path)
    for call in (lambda: store.put(name, template), lambda: store.get(name), lambda: store.delete(name)):
        with pytest.raises(ValueError, match="bad template name"):
            call()
    assert list(tmp_path.iterdir()) == []


def test_store_accepts_the_longest_plain_name(tmp_path, template):
    store = FolderTemplateStore(tmp_path)
    store.put("a" * 80, template)
    assert store.get("a" * 80) == template


def test_a_crash_mid_write_leaves_the_old_file_intact(tmp_path, template, monkeypatch):
    store = FolderTemplateStore(tmp_path)
    store.put("t", template)
    before = (tmp_path / "t.json").read_bytes()
    changed = template.model_copy(update={"template_id": "other"})

    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        store.put("t", changed)
    monkeypatch.undo()
    assert (tmp_path / "t.json").read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["t.json"]  # no temp file left behind
    assert store.get("t") == template


def test_listing_skips_files_that_are_not_templates(tmp_path, template):
    store = FolderTemplateStore(tmp_path)
    store.put("good", template)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "other.json").write_text('{"hello": 1}', encoding="utf-8")
    (tmp_path / ".tmp-x.part").write_text("partial", encoding="utf-8")
    (tmp_path / "bad..name!.json").write_text(template.to_json(), encoding="utf-8")
    assert [t["name"] for t in store.list()] == ["good"]


def test_an_invalid_stored_file_raises_its_own_error_not_value_error(tmp_path, template):
    store = FolderTemplateStore(tmp_path)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "other.json").write_text('{"hello": 1}', encoding="utf-8")
    (tmp_path / "latin.json").write_bytes("caf\xe9".encode("cp1252"))
    for name in ("broken", "other", "latin"):
        with pytest.raises(StoredTemplateError, match=name) as e:
            store.get(name)
        assert not isinstance(e.value, ValueError)
    with pytest.raises(ValueError, match="bad template name"):  # a bad name is still a bad request
        store.get("../broken")
    with pytest.raises(KeyError):
        store.get("absent")


# ------------------------------------------------------------------ record sources

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "zip": {"type": "string"},
        "age": {"type": "integer"},
        "score": {"type": "number"},
        "member": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "address": {"type": "object", "properties": {"city": {"type": "string"}, "geo": {"type": "object", "properties": {"lat": {"type": "number"}}}}},
    },
}  # fmt: skip


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "records").mkdir()
    (tmp_path / "schemas" / "person.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (tmp_path / "schemas" / "empty.json").write_text("{}", encoding="utf-8")
    return tmp_path


def write_csv(data_dir, text, schema="person"):
    (data_dir / "records" / f"{schema}.csv").write_text(text, encoding="utf-8")


def test_csv_builds_nested_objects_and_reads_json_cells(data_dir):
    write_csv(
        data_dir,
        "id,title,name,zip,age,score,member,tags,address.city,address.geo.lat\n"
        'p1,Ada,Ada Example,02134,36,9.5,true,"[""a"", ""b""]",Boston,42.35\n',
    )
    src = CsvRecords(data_dir)
    assert src.list("person") == [{"id": "p1", "title": "Ada"}]
    assert src.get("person", "p1") == {
        "name": "Ada Example", "zip": "02134", "age": 36, "score": 9.5, "member": True,
        "tags": ["a", "b"], "address": {"city": "Boston", "geo": {"lat": 42.35}},
    }  # fmt: skip
    assert set(src.schemas()) == {"person", "empty"}
    assert src.list("empty") == []  # a schema with no records file


def test_csv_empty_cells_are_omitted_and_title_falls_back_to_id(data_dir):
    write_csv(data_dir, "id,name,age,tags\np1,,,\np2,Bo,,\n")
    src = CsvRecords(data_dir)
    assert src.get("person", "p1") == {}
    assert src.get("person", "p2") == {"name": "Bo"}
    assert src.list("person") == [{"id": "p1", "title": "p1"}, {"id": "p2", "title": "p2"}]


def test_csv_malformed_cells_stay_strings_so_fill_can_report_them(data_dir):
    write_csv(data_dir, 'id,age,member,tags,address\np1,abc,maybe,"[""a"", oops","{""city"": 1"\n')
    rec = CsvRecords(data_dir).get("person", "p1")
    assert rec == {"age": "abc", "member": "maybe", "tags": '["a", oops', "address": '{"city": 1'}


def test_csv_a_string_field_is_not_parsed_as_json(data_dir):
    write_csv(data_dir, 'id,name\np1,"[draft]"\np2,"{}"\n')
    src = CsvRecords(data_dir)
    assert src.get("person", "p1") == {"name": "[draft]"}
    assert src.get("person", "p2") == {"name": "{}"}


def test_csv_extra_columns_become_extra_keys(data_dir):
    write_csv(data_dir, "id,name,nickname,address.county\np1,Ada,Countess,Suffolk\n")
    assert CsvRecords(data_dir).get("person", "p1") == {
        "name": "Ada", "nickname": "Countess", "address": {"county": "Suffolk"}
    }  # fmt: skip


def test_csv_columns_that_cannot_be_placed_keep_their_dotted_name(data_dir):
    write_csv(data_dir, "id,name,name.first,address,address.city,a..b\np1,Ada,A,Boston,Cambridge,x\n")
    assert CsvRecords(data_dir).get("person", "p1") == {
        "name": "Ada", "name.first": "A", "address": "Boston", "address.city": "Cambridge", "a..b": "x"
    }  # fmt: skip


def test_csv_a_nested_column_before_its_parent_column_is_not_clobbered(data_dir):
    write_csv(data_dir, "id,address.city,address\np1,Boston,Cambridge\n")
    assert CsvRecords(data_dir).get("person", "p1") == {"address": {"city": "Boston"}}


def test_csv_short_long_and_idless_rows_do_not_crash(data_dir):
    write_csv(data_dir, "id,name,age\np1,Ada\np2,Bo,3,surplus,cells\n,Nameless,4\n")
    src = CsvRecords(data_dir)
    assert [r["id"] for r in src.list("person")] == ["p1", "p2"]
    assert src.get("person", "p1") == {"name": "Ada"}
    assert src.get("person", "p2") == {"name": "Bo", "age": 3}


def test_csv_needs_an_id_column(data_dir):
    write_csv(data_dir, "name\nAda\n")
    with pytest.raises(ValueError, match="id column"):
        CsvRecords(data_dir).list("person")


def test_unknown_schema_or_id_raises_key_error_and_odd_schema_names_cannot_escape(data_dir):
    write_csv(data_dir, "id,name\np1,Ada\n")
    (data_dir / "secret.csv").write_text("id\nx\n", encoding="utf-8")
    src = CsvRecords(data_dir)
    for bad in ("nope", "../secret", "/etc/passwd", ""):
        with pytest.raises(KeyError):
            src.list(bad)
        with pytest.raises(KeyError):
            src.get(bad, "p1")
    with pytest.raises(KeyError):
        src.get("person", "p9")


def make_db(path, rows):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE person (id TEXT, title TEXT, name TEXT, zip TEXT, age INTEGER, score REAL, member INTEGER, tags TEXT, "address.city" TEXT, extra TEXT)')
    db.executemany("INSERT INTO person VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_sqlite_reads_typed_columns_json_text_and_omits_nulls(data_dir):
    make_db(data_dir / "records.sqlite", [
        ("p1", "Ada", "Ada Example", "02134", 36, 9.5, 1, '["a", "b"]', "Boston", None),
        ("p2", None, None, None, None, None, 0, "[oops", None, "extra value"),
    ])  # fmt: skip
    src = SqliteRecords(data_dir / "records.sqlite", data_dir / "schemas")
    assert src.list("person") == [{"id": "p1", "title": "Ada"}, {"id": "p2", "title": "p2"}]
    assert src.get("person", "p1") == {
        "name": "Ada Example", "zip": "02134", "age": 36, "score": 9.5, "member": True,
        "tags": ["a", "b"], "address": {"city": "Boston"},
    }  # fmt: skip
    assert src.get("person", "p2") == {"member": False, "tags": "[oops", "extra": "extra value"}
    assert src.list("empty") == []  # a schema with no table
    with pytest.raises(KeyError):
        src.get("person", "p9")


def test_sqlite_is_read_only_and_tolerates_a_missing_file(data_dir):
    missing = SqliteRecords(data_dir / "nope.sqlite", data_dir / "schemas")
    assert missing.list("person") == []
    make_db(data_dir / "records.sqlite", [("p1", None, "Ada", None, None, None, None, None, None, None)])
    src = SqliteRecords(data_dir / "records.sqlite", data_dir / "schemas")
    assert src.get("person", "p1") == {"name": "Ada"}
    assert not (data_dir / "records.sqlite-journal").exists()


def test_multi_records_concatenates(data_dir):
    write_csv(data_dir, "id,name\nc1,Csv One\n")
    make_db(data_dir / "records.sqlite", [("s1", None, "Sql One", None, None, None, None, None, None, None)])
    multi = MultiRecords(CsvRecords(data_dir), SqliteRecords(data_dir / "records.sqlite", data_dir / "schemas"))
    assert set(multi.schemas()) == {"person", "empty"}
    assert [r["id"] for r in multi.list("person")] == ["c1", "s1"]
    assert multi.get("person", "s1") == {"name": "Sql One"}
    assert multi.get("person", "c1") == {"name": "Csv One"}
    with pytest.raises(KeyError):
        multi.get("person", "zz")
    with pytest.raises(KeyError):
        multi.list("nope")


def test_unreadable_csv_files_raise_record_source_error(data_dir):
    cases = {
        "cp1252": "id,name\np1,Jos\xe9\n".encode("cp1252"),
        "empty": b"",
    }
    for raw in cases.values():
        (data_dir / "records" / "person.csv").write_bytes(raw)
        with pytest.raises(RecordSourceError, match="person.csv"):
            CsvRecords(data_dir).list("person")
        with pytest.raises(RecordSourceError):
            CsvRecords(data_dir).get("person", "p1")


def test_csv_parser_errors_become_record_source_errors(data_dir, monkeypatch):
    import csv as csv_mod

    write_csv(data_dir, "id,name\np1,Ada\n")

    class Boom:
        fieldnames = ["id"]

        def __init__(self, *a, **k):
            pass

        def __iter__(self):
            raise csv_mod.Error("field larger than field limit (131072)")

    monkeypatch.setattr(csv_mod, "DictReader", Boom)
    with pytest.raises(RecordSourceError, match="not valid CSV"):
        CsvRecords(data_dir).list("person")


def test_a_non_sqlite_file_raises_record_source_error(data_dir):
    (data_dir / "records.sqlite").write_bytes(b"this is not a database" * 50)
    with pytest.raises(RecordSourceError, match="records.sqlite"):
        SqliteRecords(data_dir / "records.sqlite", data_dir / "schemas").list("person")


def test_multi_records_isolates_a_failing_source_and_reports_it(data_dir):
    make_db(data_dir / "records.sqlite", [("s1", None, "Sql One", None, None, None, None, None, None, None)])
    (data_dir / "records" / "person.csv").write_bytes("id,name\np1,Jos\xe9\n".encode("cp1252"))
    multi = MultiRecords(CsvRecords(data_dir), SqliteRecords(data_dir / "records.sqlite", data_dir / "schemas"))
    assert multi.problems() == []
    assert [r["id"] for r in multi.list("person")] == ["s1"]  # the CSV is skipped, the database answers
    assert multi.get("person", "s1") == {"name": "Sql One"}
    problems = multi.problems()
    assert [(p["schema"], p["source"]) for p in problems] == [("person", "CsvRecords")]
    assert "person.csv" in problems[0]["detail"]
    with pytest.raises(RecordSourceError, match="person.csv"):  # the id might be in the broken file
        multi.get("person", "p1")
    write_csv(data_dir, "id,name\np1,Ada\n")  # fixed on disk: the problem clears on the next read
    assert [r["id"] for r in multi.list("person")] == ["p1", "s1"]
    assert multi.problems() == []


def test_overlay_saves_locally_and_never_writes_the_shipped_folder(tmp_path, template):
    shipped, local = tmp_path / "templates", tmp_path / "local" / "templates"
    FolderTemplateStore(shipped).put("fw9", template)
    before = (shipped / "fw9.json").read_bytes()
    store = OverlayTemplateStore(local, shipped)
    mine = template.model_copy(update={"template_id": "mine"})
    store.put("fw9", mine)  # the name of a shipped template, as the Save dialog suggests after Reuse
    store.put("extra", template)
    assert (shipped / "fw9.json").read_bytes() == before and sorted(p.name for p in shipped.iterdir()) == ["fw9.json"]
    assert sorted(p.name for p in local.iterdir()) == ["extra.json", "fw9.json"]
    assert store.get("fw9").template_id == "mine"  # local wins
    listed = store.list()
    assert [(t["name"], t["template_id"]) for t in listed] == [("extra", template.template_id), ("fw9", "mine")]


def test_overlay_lists_both_folders_and_refuses_to_delete_a_shipped_template(tmp_path, template):
    shipped, local = tmp_path / "templates", tmp_path / "local" / "templates"
    FolderTemplateStore(shipped).put("shipped one", template)
    store = OverlayTemplateStore(local, shipped)  # the local folder does not exist yet
    assert [t["name"] for t in store.list()] == ["shipped one"]
    store.put("mine", template)
    assert [t["name"] for t in store.list()] == ["mine", "shipped one"]
    with pytest.raises(ShippedTemplateError, match="came with the demo"):
        store.delete("shipped one")
    assert store.get("shipped one") == template
    store.put("shipped one", template.model_copy(update={"template_id": "over"}))
    store.delete("shipped one")  # removes the local copy; the shipped one shows again
    assert store.get("shipped one").template_id == template.template_id
    store.delete("mine")
    with pytest.raises(KeyError):
        store.delete("mine")
    for call in (lambda: store.get("../x"), lambda: store.put("../x", template), lambda: store.delete("../x")):
        with pytest.raises(ValueError, match="bad template name"):
            call()


def test_server_tests_copy_the_demo_data_without_a_developers_local_saves(tmp_path, template, demo_data_copier):
    src = tmp_path / "data"
    FolderTemplateStore(src / "templates").put("fw9", template)
    FolderTemplateStore(src / "local" / "templates").put("my save", template)
    (src / "forms" / "local").mkdir(parents=True)  # only the top-level local/ is left out
    demo_data_copier(src, tmp_path / "copy")
    assert sorted(p.relative_to(tmp_path / "copy").as_posix() for p in (tmp_path / "copy").rglob("*")) == [
        "forms", "forms/local", "templates", "templates/fw9.json",
    ]  # fmt: skip


# ------------------------------------------------------------------ the committed demo data


def _sources():
    return CsvRecords(DATA), SqliteRecords(DATA / "records.sqlite", DATA / "schemas")


def test_demo_data_has_the_dev_forms_only():
    manifest = {f["id"]: f for f in json.loads((ROOT / "forms" / "manifest.json").read_text())["forms"]}
    assert sorted(p.stem for p in (DATA / "forms").glob("*.pdf")) == sorted(FORMS)
    assert all(manifest[f]["split"] == "dev" for f in FORMS)
    assert sorted(p.stem for p in (DATA / "templates").glob("*.json")) == sorted(FORMS)


@pytest.mark.parametrize("form", FORMS)
def test_demo_records_validate_and_csv_matches_sqlite(form):
    csv_src, sql_src = _sources()
    schema = csv_src.schemas()[form]
    summaries = csv_src.list(form)
    assert len(summaries) == 5
    assert summaries == sql_src.list(form)
    for summary in summaries:
        record = csv_src.get(form, summary["id"])
        jsonschema.validate(record, schema)
        assert record == sql_src.get(form, summary["id"])
        assert "id" not in record and "title" not in record
        text = json.dumps(record)
        assert "example" in text.lower()
        assert "@" not in text or "@example.com" in text


@pytest.mark.parametrize("form", FORMS)
def test_demo_template_fills_its_form_with_the_fingerprint_check_on(form):
    csv_src, _ = _sources()
    template = Template.from_json((DATA / "templates" / f"{form}.json").read_text(encoding="utf-8"))
    assert template.schema_ == csv_src.schemas()[form]
    pdf = (DATA / "forms" / f"{form}.pdf").read_bytes()
    record = csv_src.get(form, csv_src.list(form)[0]["id"])
    out, report = fill(template, pdf, record, check_fingerprint=True)
    assert out.startswith(b"%PDF")
    assert report.fingerprint_ok
    assert report.counts["fields_written"] > 0


def test_a_malformed_record_fills_with_a_report_not_an_exception(tmp_path):
    shutil_data = tmp_path / "data"
    (shutil_data / "schemas").mkdir(parents=True)
    (shutil_data / "records").mkdir()
    (shutil_data / "schemas" / "menomonie_app.json").write_bytes((DATA / "schemas" / "menomonie_app.json").read_bytes())
    (shutil_data / "records" / "menomonie_app.csv").write_text(
        'id,position_applied_for,applicant.first_name,applicant.last_name,certifications,favourite_colour\n'
        'bad,Clerk,Ada,Example,"[""CPR"", oops",teal\n',
        encoding="utf-8",
    )
    record = CsvRecords(shutil_data).get("menomonie_app", "bad")
    template = Template.from_json((DATA / "templates" / "menomonie_app.json").read_text(encoding="utf-8"))
    _, report = fill(template, (DATA / "forms" / "menomonie_app.pdf").read_bytes(), record)
    codes = {w.code for w in report.warnings}
    assert {"invalid", "unknown_keys"} <= codes
