"""The command line, run as a user runs it: ``python -m formcast.extract`` in a real subprocess.

A subprocess also proves the module is safe to import again: the pool's workers start by spawn on macOS and import
the main module afresh, so anything at its top level would run in every worker.
"""

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

import formcast
from tests.extract.corpus import hand_template


SRC = str(Path(formcast.__file__).resolve().parent.parent)  # the code under test, whether or not it is installed


def cli(*args: object, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")]))}
    done = subprocess.run(
        [sys.executable, "-m", "formcast.extract", *map(str, args)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    if check:
        assert done.returncode == 0, done.stderr
    return done


@pytest.fixture
def template_file(tmp_path: Path) -> Path:
    path = tmp_path / "client_information.json"
    path.write_text(hand_template().model_dump_json(by_alias=True), encoding="utf-8")
    return path


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_scan_prints_the_main_group_and_its_examples(corpus_dir: Path):
    out = cli("scan", corpus_dir).stdout
    assert "group 1: 20 documents" in out and "13 fillable" in out and "7 flattened" in out and "2 pages" in out
    assert "group 2: 2 documents" in out
    assert out.count(".pdf") >= 5 and "unreadable" not in out


def test_scan_lists_unreadable_files_and_takes_first_page(corpus_dir: Path):
    (corpus_dir / "broken.pdf").write_bytes(b"not a pdf")
    out = cli("scan", corpus_dir, "--first-page").stdout
    assert "group 1: 20 documents" in out
    assert "unreadable (1)" in out and "broken.pdf" in out


def test_run_then_export_csv(corpus_dir: Path, template_file: Path, tmp_path: Path):
    out = tmp_path / "out"
    done = cli("run", template_file, corpus_dir, "--out", out, "--workers", "2")
    summary = done.stderr.splitlines()[-1]
    assert "total 22" in summary and "no template 2" in summary and "unreadable 0" in summary
    assert "seconds" in summary
    assert (out / "results.jsonl").is_file() and (out / "run.json").is_file()

    cli("export", out, "--template", template_file, "--format", "csv", "--to", tmp_path / "csv")
    values = read_csv(tmp_path / "csv" / "values.csv")
    assert len(values) == 20  # the 2 other forms are set aside, never value rows
    assert all(r["investor.legal_name"] for r in values)
    aside = read_csv(tmp_path / "csv" / "set_aside.csv")
    assert len(aside) == 2 and {r["reason"] for r in aside} == {"no template fits"}
    assert "owners_1_name" in {r["column"] for r in read_csv(tmp_path / "csv" / "fields.csv")}


def test_run_again_resumes_without_rereading(corpus_dir: Path, template_file: Path, tmp_path: Path):
    out = tmp_path / "out"
    cli("run", template_file, corpus_dir, "--out", out, "--workers", "1")
    before = (out / "results.jsonl").read_text()
    again = cli("run", template_file, corpus_dir, "--out", out, "--workers", "1")
    assert (out / "results.jsonl").read_text() == before
    assert "total 22" in again.stderr.splitlines()[-1]


def test_missing_template_exits_2_naming_the_file(corpus_dir: Path, tmp_path: Path):
    done = cli("run", tmp_path / "nope.json", corpus_dir, "--out", tmp_path / "out", check=False)
    assert done.returncode == 2
    assert "nope.json" in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_bad_template_json_exits_2_naming_the_file(corpus_dir: Path, tmp_path: Path):
    bad = tmp_path / "bad_template.json"
    bad.write_text('{"template_id": 5}', encoding="utf-8")
    done = cli("run", bad, corpus_dir, "--out", tmp_path / "out", check=False)
    assert done.returncode == 2
    assert "bad_template.json" in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_bad_arguments_exit_2(tmp_path: Path):
    assert cli("scan", tmp_path / "no_such_folder", check=False).returncode == 2
    assert cli("frobnicate", check=False).returncode == 2
    no_template = cli("export", tmp_path, "--format", "csv", "--to", tmp_path / "x", check=False)
    assert no_template.returncode == 2


def test_export_of_a_folder_that_is_not_a_run_exits_2(template_file: Path, tmp_path: Path):
    done = cli("export", tmp_path, "--template", template_file, "--format", "csv", "--to", tmp_path / "x", check=False)
    assert done.returncode == 2 and "no run" in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_export_parquet_without_the_extra_exits_2(corpus_dir: Path, template_file: Path, tmp_path: Path):
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pyarrow is installed")
    out = tmp_path / "out"
    cli("run", template_file, corpus_dir, "--out", out, "--workers", "1")
    done = cli("export", out, "--template", template_file, "--format", "parquet", "--to", tmp_path / "p", check=False)
    assert done.returncode == 2 and "formcast[parquet]" in done.stderr
