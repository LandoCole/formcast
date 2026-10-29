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
from formcast.extract.__main__ import main
from tests.extract.corpus import hand_template
from tests.extract.fakes import Scripted
from tests.extract.test_aipass import ANSWER, failing, write_batch


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
    done = cli("run", template_file, corpus_dir, "--out", out, "--workers", "2", "--timeout", "60")
    summary = done.stderr.splitlines()[-1]
    assert "total 22" in summary and "no template 2" in summary and "unreadable 0" in summary
    assert "internal errors 0" in summary and "seconds" in summary
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


def test_a_negative_worker_count_exits_2(corpus_dir: Path, template_file: Path, tmp_path: Path):
    done = cli("run", template_file, corpus_dir, "--out", tmp_path / "out", "--workers", "-1", check=False)
    assert done.returncode == 2 and "--workers" in done.stderr and "Traceback" not in done.stderr
    assert cli("run", template_file, corpus_dir, "--out", tmp_path / "out", "--workers", "x", check=False).returncode == 2


@pytest.mark.parametrize("timeout", ["0", "-5", "soon"])
def test_a_timeout_that_is_not_a_positive_number_exits_2(corpus_dir: Path, template_file: Path, tmp_path: Path, timeout):
    done = cli("run", template_file, corpus_dir, "--out", tmp_path / "out", "--timeout", timeout, check=False)
    assert done.returncode == 2 and "--timeout" in done.stderr and "Traceback" not in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1 and not (tmp_path / "out").exists()


def test_an_out_or_to_path_of_the_wrong_kind_exits_2_in_one_line(
    corpus_dir: Path, template_file: Path, tmp_path: Path
):
    taken = tmp_path / "taken"
    taken.write_text("a file, not a folder")
    done = cli("run", template_file, corpus_dir, "--out", taken, check=False)
    assert done.returncode == 2 and "Traceback" not in done.stderr and len(done.stderr.strip().splitlines()) == 1
    out = tmp_path / "out"
    cli("run", template_file, corpus_dir, "--out", out, "--workers", "1")
    done = cli("export", out, "--template", template_file, "--format", "xlsx", "--to", tmp_path, check=False)
    assert done.returncode == 2 and "Traceback" not in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_export_of_a_folder_that_is_not_a_run_exits_2(template_file: Path, tmp_path: Path):
    done = cli("export", tmp_path, "--template", template_file, "--format", "csv", "--to", tmp_path / "x", check=False)
    assert done.returncode == 2 and "no run" in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_export_with_a_template_the_run_did_not_use_exits_2(corpus_dir: Path, template_file: Path, tmp_path: Path):
    out = tmp_path / "out"
    cli("run", template_file, corpus_dir, "--out", out, "--workers", "1")
    edited = tmp_path / "edited.json"
    edited.write_text(
        hand_template().model_copy(update={"absent": ["wire.bank"]}).model_dump_json(by_alias=True), encoding="utf-8"
    )
    done = cli("export", out, "--template", edited, "--format", "csv", "--to", tmp_path / "csv", check=False)
    assert done.returncode == 2 and "client_information" in done.stderr and "differs" in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1 and not (tmp_path / "csv").exists()


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


MODEL_VARIABLES = ("FORMCAST_EXTRACT_BASE_URL", "FORMCAST_EXTRACT_API_KEY", "FORMCAST_EXTRACT_MODEL")


def test_the_ai_command_needs_a_model_config(template_file: Path, tmp_path: Path, monkeypatch):
    for name in MODEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    done = cli("ai", tmp_path, "--template", template_file, check=False)
    assert done.returncode == 2 and "FORMCAST_EXTRACT_" in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1


def test_the_ai_command_on_a_folder_that_is_not_a_run_exits_2(template_file: Path, tmp_path: Path, monkeypatch):
    for name in MODEL_VARIABLES:
        monkeypatch.setenv(name, "set")
    done = cli("ai", tmp_path, "--template", template_file, check=False)
    assert done.returncode == 2 and "no run in" in done.stderr and len(done.stderr.strip().splitlines()) == 1


def test_the_ai_command_asks_about_the_failing_documents_and_says_how_many(
    template_file: Path, tmp_path: Path, monkeypatch, capsys
):
    out = write_batch(tmp_path)
    bad = failing(out)
    for name in MODEL_VARIABLES:
        monkeypatch.setenv(name, "set")
    model = Scripted(*[ANSWER] * len(bad))
    monkeypatch.setattr("formcast.extract.setup.make_provider", lambda config: model)
    assert main(["ai", str(out), "--template", str(template_file)]) == 0
    assert f"asked about {len(bad)} documents, {len(bad)} answered, 0 failed" in capsys.readouterr().err
    assert len(model.calls) == len(bad)
    assert main(["ai", str(out), "--template", str(template_file)]) == 0  # nothing left to ask
    assert "asked about 0 documents" in capsys.readouterr().err
