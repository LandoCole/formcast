"""The opt-in AI pass: a finished run's documents whose required fields failed, asked about those fields only.

Each test writes its own batch folder (copies of the corpus form, two of them with the tax ID left blank, and one of
another form) and runs the hand template over it in this process, so the set of documents with a failing required
field is never empty; the model is a scripted fake.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from formcast.errors import FormcastError
from formcast.extract.aipass import AI_REASON, AiPassSummary, ai_pass, required_paths
from formcast.extract.batch import AI, REVIEWS, Results, ReviewDecision, run_batch
from tests.extract.corpus import clients, hand_template, make_form, other_form
from tests.extract.fakes import Scripted, asked_paths, user_text

TAX_ID = "investor.tax_id"
ANSWER = json.dumps({"values": {TAX_ID: "12-3456789"}})


def write_batch(tmp_path: Path) -> Path:
    """A run of the hand template over six filled copies (two with a blank tax ID) and a form it does not fit."""
    people = clients(6, seed=3)
    people[1] = dataclasses.replace(people[1], tax_id="")
    people[4] = dataclasses.replace(people[4], tax_id="")
    folder = tmp_path / "in"
    folder.mkdir()
    for n, client in enumerate(people, start=1):
        (folder / f"copy_{n}.pdf").write_bytes(make_form(client, kind="fillable" if n % 2 else "flattened"))
    (folder / "other.pdf").write_bytes(other_form())
    out = tmp_path / "out"
    run_batch([hand_template()], sorted(folder.glob("*.pdf")), out, workers=1)
    return out


def failing(out: Path) -> set[str]:
    """The documents of the run with a required field ``missing`` or ``failed_check`` (decisions applied)."""
    required = required_paths(hand_template().schema_)
    found = {d.doc_id for d, f in Results.load(out).problems() if f.path in required and f.status != "unsure"}
    assert found, "the batch folder must hold documents whose required fields failed"
    return found


def field(out: Path, doc_id: str, path: str = TAX_ID):
    results = Results.load(out)
    doc = next(d for d in results.documents() if d.doc_id == doc_id)
    return next(f for f in results.effective(doc).fields if f.path == path)


# ------------------------------------------------------------------ required paths


def test_required_paths_are_the_leaves_named_in_their_parents_required():
    assert required_paths(hand_template().schema_) == {TAX_ID}
    schema = {
        "type": "object",
        "required": ["name", "owner", "partners"],
        "properties": {
            "name": {"type": "string"},
            "note": {"type": "string"},
            "owner": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}, "nick": {"type": "string"}},
            },
            "partners": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}, "share": {"type": "string"}},
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    assert required_paths(schema) == {"name", "owner.id", "partners[0].name", "partners[1].name"}


# ------------------------------------------------------------------ the pass


def test_documents_with_failing_required_fields_are_asked_about_only_those_fields(tmp_path):
    out = write_batch(tmp_path)
    bad = failing(out)
    assert len(bad) >= 2
    model = Scripted(*[ANSWER] * len(bad))
    summary = ai_pass([hand_template()], out, model, vision=False)
    assert summary == AiPassSummary(documents=len(bad), answered=len(bad), failed=0, usage=summary.usage)
    assert summary.usage.input_tokens == 10 * len(bad)
    assert len(model.calls) == len(bad)
    for call in model.calls:
        assert asked_paths(user_text(call[1])) == [TAX_ID]  # only the failing field, not the document's others
    f = field(out, sorted(bad)[0])
    assert (f.status, f.strategy, f.value, f.raw) == ("unsure", "ai", "12-3456789", "12-3456789")
    assert f.reasons == [AI_REASON]
    assert (f.page, f.rect) == (None, None)
    results = Results.load(out)
    fine = next(d for d in results.documents() if d.doc_id not in bad and d.error is None)
    assert results.effective(fine).fields == fine.fields  # a document that read fine is unchanged


def test_a_second_pass_asks_nothing_again(tmp_path):
    out = write_batch(tmp_path)
    bad = failing(out)
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(bad)), vision=False)
    written = (out / AI).read_text()
    nobody = Scripted()  # raises if it is called
    summary = ai_pass([hand_template()], out, nobody, vision=False)
    assert (summary.documents, summary.answered, summary.failed) == (0, 0, 0)
    assert nobody.calls == [] and (out / AI).read_text() == written


def test_an_answer_of_null_is_recorded_so_the_next_pass_skips_the_document(tmp_path):
    out = write_batch(tmp_path)
    bad = failing(out)
    nothing = json.dumps({"values": {TAX_ID: None}})
    summary = ai_pass([hand_template()], out, Scripted(*[nothing] * len(bad)), vision=False)
    assert (summary.documents, summary.answered) == (len(bad), 0)
    answers = [json.loads(line) for line in (out / AI).read_text().splitlines()]
    assert sorted(a["doc_id"] for a in answers) == sorted(bad) and all(a["value"] is None for a in answers)
    assert field(out, sorted(bad)[0]).status in ("missing", "failed_check")  # nothing was made up
    assert ai_pass([hand_template()], out, Scripted(), vision=False).documents == 0


def test_a_model_failure_on_one_document_is_counted_and_the_pass_goes_on(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    # one worker, so the documents are asked in order: the first gets two unusable replies (a retry), then none left
    model = Scripted("not json", "still not json", *[ANSWER] * (len(bad) - 1))
    summary = ai_pass([hand_template()], out, model, max_parallel=1, vision=False)
    assert (summary.documents, summary.answered, summary.failed) == (len(bad), len(bad) - 1, 1)
    assert field(out, bad[0]).status in ("missing", "failed_check")
    assert all(field(out, d).status == "unsure" for d in bad[1:])
    again = Scripted(ANSWER)  # the failed document is asked again by the next pass, the answered ones are not
    assert ai_pass([hand_template()], out, again, vision=False).documents == 1


def test_a_review_decision_wins_over_the_ai_answer(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(bad)), vision=False)
    results = Results.load(out)
    results.decide(ReviewDecision(doc_id=bad[0], path=TAX_ID, decision="value", value="98-7654321"))
    results.decide(ReviewDecision(doc_id=bad[1], path=TAX_ID, decision="not_in_document"))
    f = field(out, bad[0])
    assert (f.status, f.strategy, f.value) == ("found", "review", "98-7654321")
    assert field(out, bad[1]).status == "absent"


def test_an_accept_is_checked_against_the_ai_answer_and_keeps_it(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(bad)), vision=False)
    Results.load(out).decide(ReviewDecision(doc_id=bad[0], path=TAX_ID, decision="accept"))
    f = field(out, bad[0])
    assert (f.status, f.value, f.reasons) == ("found", "12-3456789", [])


def test_an_accept_of_a_field_the_ai_gave_nothing_for_is_still_refused(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    nothing = json.dumps({"values": {TAX_ID: None}})
    ai_pass([hand_template()], out, Scripted(*[nothing] * len(bad)), vision=False)
    with pytest.raises(FormcastError, match="nothing to accept"):
        Results.load(out).decide(ReviewDecision(doc_id=bad[0], path=TAX_ID, decision="accept"))


def test_the_ai_answer_stays_a_problem_until_reviewed(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    before = Results.load(out).summary().with_problems
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(bad)), vision=False)
    results = Results.load(out)
    assert results.summary().with_problems == before  # an answer from the model settles nothing
    unsure = [(d.doc_id, f.value) for d, f in results.problems(path=TAX_ID, status="unsure")]
    assert unsure == [(doc_id, "12-3456789") for doc_id in bad]
    assert {r[TAX_ID] for r in results.status_rows() if r["doc_id"] in bad} == {"unsure"}
    results.decide(ReviewDecision(doc_id=bad[0], path=TAX_ID, decision="accept"))
    assert Results.load(out).summary().with_problems == before - 1
    assert (out / REVIEWS).is_file()


def test_a_torn_last_line_of_the_answers_is_ignored_and_cut_before_the_next_append(tmp_path):
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    first = Scripted("not json", "still not json", *[ANSWER] * (len(bad) - 1))  # the first document is not answered
    ai_pass([hand_template()], out, first, max_parallel=1, vision=False)
    with (out / AI).open("a", encoding="utf-8") as f:
        f.write('{"doc_id": "copy_1.pdf", "pa')
    assert field(out, bad[1]).status == "unsure"  # the whole lines still apply
    ai_pass([hand_template()], out, Scripted(ANSWER), vision=False)  # asks the one left, cutting the torn line first
    assert all(json.loads(line) for line in (out / AI).read_text().splitlines())
    assert all(field(out, d).status == "unsure" for d in bad)


def test_the_pass_refuses_a_template_the_run_did_not_read_with(tmp_path):
    out = write_batch(tmp_path)
    changed = hand_template().model_copy(update={"name": "Another name"})
    nobody = Scripted()
    with pytest.raises(FormcastError, match="differs|not a template"):
        ai_pass([changed], out, nobody, vision=False)
    assert nobody.calls == [] and not (out / AI).exists()


# ------------------------------------------------------------------ an accept stands on its own


def append_line(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def accepted_ai_value(tmp_path) -> tuple[Path, str]:
    out = write_batch(tmp_path)
    bad = sorted(failing(out))
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(bad)), vision=False)
    Results.load(out).decide(ReviewDecision(doc_id=bad[0], path=TAX_ID, decision="accept"))
    return out, bad[0]


def test_accepting_an_ai_answer_is_recorded_as_the_value_the_person_saw(tmp_path):
    out, _ = accepted_ai_value(tmp_path)
    recorded = [json.loads(line) for line in (out / REVIEWS).read_text().splitlines()]
    assert [(r["decision"], r["value"]) for r in recorded] == [("value", "12-3456789")]


def test_an_accepted_ai_answer_stays_whatever_a_later_ai_answer_says(tmp_path):
    out, doc_id = accepted_ai_value(tmp_path)
    append_line(out / AI, {"doc_id": doc_id, "path": TAX_ID, "value": "00-0000000", "at": "later"})
    f = field(out, doc_id)
    assert (f.status, f.value, f.strategy) == ("found", "12-3456789", "review")


def test_an_accepted_ai_answer_stays_when_the_answers_file_is_gone(tmp_path):
    out, doc_id = accepted_ai_value(tmp_path)
    (out / AI).unlink()
    f = field(out, doc_id)
    assert (f.status, f.value, f.strategy) == ("found", "12-3456789", "review")


def test_an_accept_of_a_field_that_has_no_value_now_is_not_shown_as_found(tmp_path):
    out = write_batch(tmp_path)
    doc_id = sorted(failing(out))[0]  # its tax ID is missing, with no value
    append_line(out / REVIEWS, {"doc_id": doc_id, "path": TAX_ID, "decision": "accept", "value": None, "at": "then"})
    f = field(out, doc_id)
    assert (f.status, f.value) == ("missing", None)
    assert Results.load(out).summary().with_problems == len(failing(out))


def test_an_accept_of_a_read_value_is_not_replaced_by_a_later_ai_answer(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    bad_tin = dataclasses.replace(clients(1, seed=5)[0], tax_id="8422-19037")  # read, but fails the pattern
    (folder / "bad_tin.pdf").write_bytes(make_form(bad_tin, kind="flattened"))
    out = tmp_path / "out"
    run_batch([hand_template()], [folder / "bad_tin.pdf"], out, workers=1)
    Results.load(out).decide(ReviewDecision(doc_id="bad_tin.pdf", path=TAX_ID, decision="accept"))
    append_line(out / AI, {"doc_id": "bad_tin.pdf", "path": TAX_ID, "value": "00-0000000", "at": "later"})
    f = field(out, "bad_tin.pdf")
    assert (f.status, f.value, f.reasons) == ("found", "8422-19037", [])
    assert f.strategy != "ai"


def test_an_accept_over_a_typed_value_never_turns_into_an_ai_answer_the_person_did_not_see(tmp_path):
    out = write_batch(tmp_path)
    doc_id = sorted(failing(out))[0]  # its tax ID is missing; the AI answers one, the person types another
    ai_pass([hand_template()], out, Scripted(*[ANSWER] * len(failing(out))), vision=False)
    results = Results.load(out)
    results.decide(ReviewDecision(doc_id=doc_id, path=TAX_ID, decision="value", value="98-7654321"))
    with pytest.raises(FormcastError, match="nothing to accept"):  # the read has no value, the AI's was never shown
        results.decide(ReviewDecision(doc_id=doc_id, path=TAX_ID, decision="accept"))
    recorded = [json.loads(line) for line in (out / REVIEWS).read_text().splitlines()]
    assert [(r["decision"], r["value"]) for r in recorded] == [("value", "98-7654321")]
    f = field(out, doc_id)
    assert (f.status, f.value, f.strategy) == ("found", "98-7654321", "review")
