"""Batch runs: routing each PDF to a template, the resumable results file, review decisions and flattened rows."""

import dataclasses
import hashlib
import json
import time
from pathlib import Path

import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.extract import batch
from formcast.extract.batch import (
    ReviewDecision,
    Results,
    RunSummary,
    parse_no_template,
    route,
    run_batch,
)
from formcast.extract.layout import LAYOUT_MATCH, learn_layout
from formcast.extract.model import SampleRecord, SetupInfo
from formcast.match import fingerprint
from tests.extract import crashing
from tests.extract.conftest import write_corpus
from tests.extract.corpus import OWNER_ROWS, clients, hand_template, make_form, other_form


@pytest.fixture(scope="module")
def corpus_run(tmp_path_factory) -> Path:
    """The corpus folder run once with the hand template (in-process), for the tests that only read the results."""
    base = tmp_path_factory.mktemp("batch")
    folder = write_corpus(base / "corpus")
    run_batch([hand_template()], sorted(folder.glob("*.pdf")), base / "out", workers=1)
    return base / "out"


def write_problem_forms(folder: Path) -> list[Path]:
    """Three filled copies with one problem each: a bad tax ID, no tax ID, a country wording not in the map."""
    folder.mkdir(parents=True, exist_ok=True)
    base = clients(1, seed=5)[0]
    copies = {
        "bad_tin.pdf": dataclasses.replace(base, tax_id="8422-19037"),
        "no_tin.pdf": dataclasses.replace(base, tax_id=""),
        "mars.pdf": dataclasses.replace(base, country="Mars"),
    }
    paths = []
    for name, client in copies.items():
        (folder / name).write_bytes(make_form(client, kind="flattened"))
        paths.append(folder / name)
    return paths


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# --------------------------------------------------------------------- routing


def test_a_batch_routes_each_form_and_sets_the_others_aside(corpus_dir, tmp_path):
    seen: list[tuple[int, int]] = []
    summary = run_batch(
        [hand_template()], sorted(corpus_dir.glob("*.pdf")), tmp_path / "out", workers=2,
        on_progress=lambda done, total: seen.append((done, total)),
    )  # fmt: skip
    assert summary.total == 22 and summary.no_template == 2 and summary.unreadable == 0
    assert summary.complete + summary.with_problems == 20 and summary.seconds > 0
    assert seen == [(n, 22) for n in range(1, 23)]
    docs = {d.doc_id: d for d in Results.load(tmp_path / "out").documents()}
    assert sorted(docs) == [f"client_{n:05d}.pdf" for n in range(1, 23)]
    # filled fillable copies match by layout too: their wire paragraph is page text (Ruling R14)
    assert {docs[f"client_{n:05d}.pdf"].matched for n in range(1, 21)} == {"layout"}
    for n in (21, 22):
        other = docs[f"client_{n:05d}.pdf"]
        assert (other.matched, other.template_id, other.fields) == ("none", None, [])
        assert other.error == "no template fits (closest: Client information, 0.00)"
    run = json.loads((tmp_path / "out" / "run.json").read_text())
    template = hand_template()
    digest = hashlib.sha256(_plan_one_json(template).encode("utf-8")).hexdigest()
    assert run["templates"] == [{"template_id": "client_information", "name": "Client information", "sha256": digest}]
    assert batch.template_sha256(template) == digest
    assert run["root"] == str(corpus_dir.resolve()) and run["started"]


def _plan_one_json(template) -> str:
    """The JSON plan 1 wrote for a template: today's canonical dump without the two keys plan 2 added."""
    dumped = template.model_dump_json(by_alias=True)
    tail = ',"setup":null,"samples":[]}'
    assert template.setup is None and not template.samples and dumped.endswith(tail)
    return dumped[: -len(tail)] + "}"


def test_a_templates_sha256_is_the_one_plan_one_kept_and_ignores_its_setup_and_samples():
    template = hand_template()
    plan_one = hashlib.sha256(_plan_one_json(template).encode("utf-8")).hexdigest()
    assert batch.template_sha256(template) == plan_one  # a plan-1 run.json still resumes and exports
    built = template.model_copy(
        update={
            "setup": SetupInfo(start="ai"),
            "samples": [SampleRecord(file="a.pdf", sha256="0" * 64, opinion={"investor.tax_id": "84-2219037"})],
        }
    )  # fmt: skip
    assert batch.template_sha256(built) == plan_one  # setup and readings never change a read
    reread = template.model_copy(update={"absent": ["wire.bank"]})
    assert batch.template_sha256(reread) != plan_one


def test_route_picks_by_fingerprint_then_by_layout_and_names_the_closest():
    clients_form = hand_template()
    w9 = clients_form.model_copy(
        update={
            "template_id": "w9", "name": "W-9", "layout": learn_layout([], blank=other_form()),
            "form": clients_form.form.model_copy(update={"fingerprint": fingerprint(other_form())}),
        }
    )  # fmt: skip
    templates = [clients_form, w9]
    use, closest, score = route(templates, make_form(clients(1)[0], kind="flattened"))
    assert use is closest is clients_form and score >= LAYOUT_MATCH
    use, closest, score = route(templates, other_form())
    assert use is closest is w9 and score == 1.0
    use, closest, score = route(templates, make_form(None, kind="fillable"))  # the blank: the template's fingerprint
    assert use is closest is clients_form and score == 1.0
    use, closest, score = route([clients_form], other_form())
    assert use is None and closest is clients_form and score < LAYOUT_MATCH


def test_route_with_no_templates_has_no_closest_and_an_unreadable_pdf_raises():
    assert route([], other_form()) == (None, None, 0.0)
    with pytest.raises(FormcastError, match="cannot open PDF"):
        route([hand_template()], b"not a pdf")


def test_the_no_template_reason_parses_back_to_its_template_and_score():
    assert parse_no_template("no template fits (closest: Client information, 0.62)") == ("Client information", 0.62)
    assert parse_no_template("no template fits (closest: Form A, v2 (2024), 0.07)") == ("Form A, v2 (2024)", 0.07)
    assert parse_no_template("encrypted PDF") is None


# ---------------------------------------------------------------- resumable file


def test_a_killed_run_resumes_without_duplicates(corpus_dir, tmp_path):
    files = sorted(corpus_dir.glob("*.pdf"))
    out = tmp_path / "out"
    run_batch([hand_template()], files[:10], out, root=corpus_dir, workers=1)
    results = out / "results.jsonl"
    data = results.read_bytes()
    last = data.rstrip(b"\n").rsplit(b"\n", 1)[1]
    torn = json.loads(last)["doc_id"]
    results.write_bytes(data[: len(data) - len(last) // 2 - 1])  # killed while writing the 10th line
    assert len(list(Results.load(out).documents())) == 9  # the torn line is not a document

    seen: list[tuple[int, int]] = []
    summary = run_batch([hand_template()], files, out, workers=1, on_progress=lambda d, t: seen.append((d, t)))
    assert summary.total == 22
    assert seen == [(n, 22) for n in range(10, 23)]  # 9 were done; the torn one and the 12 new ones are read
    rows = lines(results)  # every line parses: nothing was appended after the torn part
    assert sorted(r["doc_id"] for r in rows) == sorted(f.name for f in files)
    again = next(d for d in Results.load(out).documents() if d.doc_id == torn)
    assert again.error is None and again.matched == "layout" and again.fields


def test_a_rerun_over_a_finished_run_reads_nothing(corpus_run):
    before = (corpus_run / "results.jsonl").read_bytes()
    folder = corpus_run.parent / "corpus"
    seen: list[tuple[int, int]] = []
    summary = run_batch(
        [hand_template()], sorted(folder.glob("*.pdf")), corpus_run, on_progress=lambda d, t: seen.append((d, t))
    )
    assert seen == [] and summary.total == 22
    assert (corpus_run / "results.jsonl").read_bytes() == before


def test_an_unreadable_file_is_recorded_and_the_run_goes_on(tmp_path):
    folder = tmp_path / "in"
    (folder / "sub").mkdir(parents=True)
    (folder / "good.pdf").write_bytes(make_form(clients(1)[0], kind="fillable"))
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    (folder / "sub" / "empty.pdf").write_bytes(b"")
    doc = pymupdf.open()
    doc.new_page()
    (folder / "locked.pdf").write_bytes(doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o"))
    paths = [folder / "broken.pdf", folder / "sub" / "empty.pdf", folder / "missing.pdf", folder / "locked.pdf",
             folder / "good.pdf"]  # fmt: skip
    summary = run_batch([hand_template()], paths, tmp_path / "out", workers=2)
    assert (summary.total, summary.unreadable, summary.no_template) == (5, 4, 0)
    results = Results.load(tmp_path / "out")
    errors = {d.doc_id: d.error for d in results.documents()}
    assert errors == {
        "broken.pdf": "cannot open PDF",
        "sub/empty.pdf": "cannot open PDF",
        "missing.pdf": "cannot open PDF",
        "locked.pdf": "encrypted PDF",
        "good.pdf": None,
    }
    set_aside = {row["doc_id"]: row for row in results.set_aside()}
    assert set_aside["locked.pdf"] == {
        "doc_id": "locked.pdf", "file": str((folder / "locked.pdf").resolve()), "reason": "encrypted PDF",
        "closest_template": None, "closest_score": None,
    }  # fmt: skip
    assert [row["doc_id"] for row in results.rows()] == ["good.pdf"]


@pytest.mark.parametrize("slow", [False, True], ids=["quick-progress", "slow-progress"])
def test_a_file_that_kills_its_worker_is_recorded_and_the_run_goes_on(tmp_path, monkeypatch, slow):
    folder = tmp_path / "in"
    folder.mkdir()
    names = [f"{c}.pdf" for c in "abcdefghij"]  # more than the 8 in flight, so the run goes on in a new pool
    for name, client in zip(names, clients(len(names))):
        (folder / name).write_bytes(make_form(client, kind="fillable"))
    monkeypatch.setattr(batch, "_work", crashing.work)
    out = tmp_path / "out"
    seen: list[tuple[int, int]] = []

    def progress(done: int, total: int) -> None:
        seen.append((done, total))
        if slow and done <= 3:  # busy while d.pdf kills its worker: the next submit meets the broken pool
            time.sleep(0.6)

    summary = run_batch([hand_template()], [folder / n for n in names], out, workers=2, on_progress=progress)
    assert (summary.total, summary.unreadable) == (10, 1)
    assert seen == [(n, 10) for n in range(1, 11)]
    rows = lines(out / "results.jsonl")
    assert sorted(r["doc_id"] for r in rows) == names  # every file once
    by_id = {r["doc_id"]: r for r in rows}
    assert (by_id[crashing.CRASH_ON]["error"], by_id[crashing.CRASH_ON]["fields"]) == (batch.CRASHED, [])
    assert all(r["error"] is None and r["fields"] for d, r in by_id.items() if d != crashing.CRASH_ON)

    seen.clear()
    run_batch([hand_template()], [folder / n for n in names], out, workers=2, on_progress=lambda d, t: seen.append(1))
    assert seen == [] and len(lines(out / "results.jsonl")) == 10  # the resume does not meet the crash again


def test_a_file_that_hangs_is_recorded_after_the_timeout_and_the_run_goes_on(tmp_path, monkeypatch):
    folder = tmp_path / "in"
    folder.mkdir()
    names = [f"{c}.pdf" for c in "abcdefghij"]  # h.pdf hangs; more than the 8 in flight
    for name, client in zip(names, clients(len(names))):
        (folder / name).write_bytes(make_form(client, kind="fillable"))
    monkeypatch.setattr(batch, "_work", crashing.hang)
    out = tmp_path / "out"
    started = time.perf_counter()
    summary = run_batch([hand_template()], [folder / n for n in names], out, workers=2, timeout=5.0)
    assert time.perf_counter() - started < 120  # the hang (an hour) was cut short, in the pool and alone
    assert (summary.total, summary.unreadable, summary.internal_errors) == (10, 1, 0)
    rows = lines(out / "results.jsonl")
    assert sorted(r["doc_id"] for r in rows) == names  # every file once
    by_id = {r["doc_id"]: r for r in rows}
    assert (by_id[crashing.HANG_ON]["error"], by_id[crashing.HANG_ON]["fields"]) == (batch.HUNG, [])
    assert batch.HUNG == "cannot read PDF: the reader hung"
    assert all(r["error"] is None and r["fields"] for d, r in by_id.items() if d != crashing.HANG_ON)


def test_a_timeout_must_be_positive(tmp_path):
    for timeout in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(FormcastError, match="timeout"):
            run_batch([hand_template()], [], tmp_path / "out", timeout=timeout)


def test_a_resume_with_other_templates_or_another_root_is_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.pdf").write_bytes(b"")
    run_batch([hand_template()], [tmp_path / "a" / "x.pdf"], tmp_path / "out", workers=1)
    other = hand_template().model_copy(update={"template_id": "other"})
    with pytest.raises(FormcastError, match="other templates"):
        run_batch([other], [tmp_path / "a" / "x.pdf"], tmp_path / "out", workers=1)
    with pytest.raises(FormcastError, match="root"):
        run_batch([hand_template()], [tmp_path / "a" / "x.pdf"], tmp_path / "out", root=tmp_path, workers=1)
    with pytest.raises(FormcastError, match="not under the run's root"):
        run_batch([hand_template()], [tmp_path / "y.pdf"], tmp_path / "out", workers=1)
    with pytest.raises(FormcastError, match="at least one template"):
        run_batch([], [tmp_path / "a" / "x.pdf"], tmp_path / "new", workers=1)


def test_a_resume_with_an_edited_template_is_refused_naming_it(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.pdf").write_bytes(make_form(clients(1)[0], kind="fillable"))
    template = hand_template()
    run_batch([template], [tmp_path / "a" / "x.pdf"], tmp_path / "out", workers=1)
    edited = template.model_copy(update={"absent": ["wire.bank"]})  # the same id, other content
    with pytest.raises(FormcastError, match="client_information"):
        run_batch([edited], [tmp_path / "a" / "x.pdf"], tmp_path / "out", workers=1)
    run_batch([hand_template()], [tmp_path / "a" / "x.pdf"], tmp_path / "out", workers=1)  # the same one resumes
    results = Results.load(tmp_path / "out")
    results.check_templates([hand_template()])
    with pytest.raises(FormcastError, match="client_information.*differs"):
        results.check_templates([edited])
    with pytest.raises(FormcastError, match="other.*not a template of this run"):
        results.check_templates([template.model_copy(update={"template_id": "other"})])


def test_internal_errors_are_counted_apart_from_unreadable_files(tmp_path, monkeypatch):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "good.pdf").write_bytes(make_form(clients(1)[0], kind="fillable"))
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4 not a pdf")
    monkeypatch.setattr(batch, "_extract_open", lambda *args, **kwargs: 1 / 0)  # a fault of the code
    summary = run_batch([hand_template()], [folder / "good.pdf", folder / "broken.pdf"], tmp_path / "out", workers=1)
    assert (summary.total, summary.internal_errors, summary.unreadable, summary.complete) == (2, 1, 1, 0)
    errors = {d.doc_id: d.error for d in Results.load(tmp_path / "out").documents()}
    assert errors["good.pdf"].startswith("internal error: ZeroDivisionError") and errors["broken.pdf"] == "cannot open PDF"


def test_loading_a_folder_with_no_run_is_refused(tmp_path):
    with pytest.raises(FormcastError, match="no run"):
        Results.load(tmp_path)


# -------------------------------------------------------------------- review


def test_review_decisions_apply_and_survive_a_rerun(tmp_path):
    paths = write_problem_forms(tmp_path / "in")
    out = tmp_path / "out"
    run_batch([hand_template()], paths, out, workers=1)
    results = Results.load(out)
    doc = next(d for d in results.documents() if d.doc_id == "bad_tin.pdf")
    before = {f.path: f for f in doc.fields}["investor.tax_id"]
    assert (before.status, before.value) == ("failed_check", "8422-19037")

    results.decide(ReviewDecision(doc_id="bad_tin.pdf", path="investor.tax_id", decision="value", value="84-2219037"))
    results.decide(ReviewDecision(doc_id="no_tin.pdf", path="investor.tax_id", decision="not_in_document"))
    results.decide(ReviewDecision(doc_id="mars.pdf", path="investor.country", decision="accept"))
    assert len((out / "reviews.jsonl").read_text().splitlines()) == 3

    run_batch([hand_template()], paths, out, workers=1)  # a rerun keeps the decisions
    results = Results.load(out)
    effective = {d.doc_id: {f.path: f for f in results.effective(d).fields} for d in results.documents()}
    tax_id = effective["bad_tin.pdf"]["investor.tax_id"]
    assert (tax_id.status, tax_id.value, tax_id.strategy, tax_id.raw) == ("found", "84-2219037", "review", "8422-19037")
    assert effective["no_tin.pdf"]["investor.tax_id"].status == "absent"
    country = effective["mars.pdf"]["investor.country"]
    assert (country.status, country.value) == ("found", "Mars")
    assert list(results.problems()) == []
    assert results.summary().with_problems == 0 and results.summary().complete == 3
    row = next(r for r in results.rows() if r["doc_id"] == "bad_tin.pdf")
    assert row["investor.tax_id"] == "84-2219037"


def test_the_latest_decision_for_a_path_wins(tmp_path):
    paths = write_problem_forms(tmp_path / "in")
    run_batch([hand_template()], paths[:1], tmp_path / "out", workers=1)
    results = Results.load(tmp_path / "out")
    results.decide(ReviewDecision(doc_id="bad_tin.pdf", path="investor.tax_id", decision="value", value="11-1111111"))
    results.decide(ReviewDecision(doc_id="bad_tin.pdf", path="investor.tax_id", decision="value", value="84-2219037"))
    doc = next(Results.load(tmp_path / "out").documents())
    tax_id = {f.path: f for f in results.effective(doc).fields}["investor.tax_id"]
    assert tax_id.value == "84-2219037"


def test_a_decision_for_an_unknown_document_or_field_or_with_nothing_to_accept_is_refused(tmp_path):
    paths = write_problem_forms(tmp_path / "in")
    run_batch([hand_template()], paths, tmp_path / "out", workers=1)
    results = Results.load(tmp_path / "out")
    with pytest.raises(FormcastError, match="no document"):
        results.decide(ReviewDecision(doc_id="nope.pdf", path="investor.tax_id", decision="accept"))
    with pytest.raises(FormcastError, match="no field"):
        results.decide(ReviewDecision(doc_id="mars.pdf", path="investor.fax", decision="accept"))
    with pytest.raises(FormcastError, match="nothing to accept"):
        results.decide(ReviewDecision(doc_id="no_tin.pdf", path="investor.tax_id", decision="accept"))
    with pytest.raises(ValueError, match="a value"):
        ReviewDecision(doc_id="mars.pdf", path="investor.country", decision="value")
    assert not (tmp_path / "out" / "reviews.jsonl").exists()


def test_problems_filter_by_path_and_status(tmp_path):
    paths = write_problem_forms(tmp_path / "in")
    run_batch([hand_template()], paths, tmp_path / "out", workers=1)
    results = Results.load(tmp_path / "out")

    def found(**filters) -> set[tuple[str, str, str]]:
        return {(d.doc_id, f.path, f.status) for d, f in results.problems(**filters)}

    assert found() == {
        ("bad_tin.pdf", "investor.tax_id", "failed_check"),
        ("no_tin.pdf", "investor.tax_id", "missing"),
        ("mars.pdf", "investor.country", "failed_check"),
    }
    assert found(path="investor.tax_id") == {
        ("bad_tin.pdf", "investor.tax_id", "failed_check"), ("no_tin.pdf", "investor.tax_id", "missing"),
    }  # fmt: skip
    assert found(status="failed_check") == {
        ("bad_tin.pdf", "investor.tax_id", "failed_check"), ("mars.pdf", "investor.country", "failed_check"),
    }  # fmt: skip
    assert found(path="investor.tax_id", status="missing") == {("no_tin.pdf", "investor.tax_id", "missing")}
    results.decide(ReviewDecision(doc_id="no_tin.pdf", path="investor.tax_id", decision="value", value="84-2219037"))
    assert found(path="investor.tax_id") == {("bad_tin.pdf", "investor.tax_id", "failed_check")}
    with pytest.raises(FormcastError, match="not a problem status"):
        list(results.problems(status="found"))
    summary = results.summary()
    assert (summary.total, summary.complete, summary.with_problems) == (3, 1, 2)


# ---------------------------------------------------------------------- rows


def test_rows_flatten_repeated_items_and_keep_dotted_paths(corpus_run):
    results = Results.load(corpus_run)
    rows = {r["doc_id"]: r for r in results.rows()}
    statuses = {r["doc_id"]: r for r in results.status_rows()}
    assert len(rows) == 20 and rows.keys() == statuses.keys()
    people = clients(20)
    for n, client in enumerate(people, start=1):
        row, status = rows[f"client_{n:05d}.pdf"], statuses[f"client_{n:05d}.pdf"]
        assert row["investor.tax_id"] == client.tax_id
        assert row["owners_1_name"] == client.owners[0][0] and row["owners_1_percent"] == client.owners[0][1]
        assert status["investor.tax_id"] == "found" and status["owners_1_name"] == "found"
        for i in range(len(client.owners) + 1, OWNER_ROWS + 1):
            assert row[f"owners_{i}_name"] is None and status[f"owners_{i}_name"] == "absent"
    first = rows["client_00001.pdf"]
    assert list(first)[:5] == ["doc_id", "file", "template", "matched", "extracted_at"]
    assert (first["template"], first["matched"]) == ("client_information", "layout")
    assert first["file"].endswith("client_00001.pdf") and first["extracted_at"]
    assert not any("[" in column for column in first)
    assert list(first) == list(statuses["client_00001.pdf"])


def test_failed_documents_are_not_rows_and_carry_their_closest_template(corpus_run):
    results = Results.load(corpus_run)
    names = {r["doc_id"] for r in results.rows()} | {r["doc_id"] for r in results.status_rows()}
    assert {"client_00021.pdf", "client_00022.pdf"}.isdisjoint(names)
    assert [(r["doc_id"], r["reason"], r["closest_template"], r["closest_score"]) for r in results.set_aside()] == [
        ("client_00021.pdf", "no template fits", "Client information", 0.0),
        ("client_00022.pdf", "no template fits", "Client information", 0.0),
    ]
    summary = results.summary()
    assert isinstance(summary, RunSummary) and (summary.total, summary.no_template) == (22, 2)
