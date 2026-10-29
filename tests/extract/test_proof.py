"""The proof: the readers' results on the samples compared with the model's readings of them, with no model call."""

import dataclasses
import hashlib

import pytest

from formcast.data.mapping import Transform
from formcast.errors import FormcastError
from formcast.extract.model import SampleRecord
from formcast.extract.proof import learn_maps, prove, same_value, sample_marks
from tests.extract.corpus import FIELD_RECTS, INVESTOR_TYPES, TYPE_BOXES, clients, hand_template, make_form, truth


def _with_samples(template, pdfs, opinions):
    records = [SampleRecord(file=f"s{i}.pdf", sha256=hashlib.sha256(p).hexdigest(), opinion=o)
               for i, (p, o) in enumerate(zip(pdfs, opinions))]  # fmt: skip
    return template.model_copy(update={"samples": records})


def test_same_value_ignores_form_but_not_content():
    assert same_value("84-2219037", "842219037") and same_value("Trust", "trust")
    assert same_value("1,000", 1000) and same_value(1000.0, "1000") and same_value(None, None)
    assert same_value(True, True) and not same_value(True, False)
    assert not same_value("Ana Ruiz", "Ben Ruiz") and not same_value("x", None)


def test_same_value_never_takes_a_number_for_text_that_only_looks_like_it():
    assert not same_value("1,5", 15) and not same_value("12.7", 127) and not same_value("02108", 2108)
    assert same_value("$1,200.50", 1200.5) and same_value("40%", "40") and same_value(["a", "b"], ["A", "b"])
    assert not same_value(["ab"], ["a", "b"]) and not same_value(None, "") and not same_value(0, None)


def test_readers_that_match_the_models_reading_are_ready_on_both_kinds():
    people = clients(4, seed=7)
    pdfs = [make_form(p, kind=k) for p, k in zip(people, ("fillable", "flattened", "fillable", "flattened"))]
    t = _with_samples(hand_template(), pdfs, [truth(p) for p in people])  # truth: concrete path -> value
    proof = prove(t, pdfs)
    tax = next(f for f in proof.fields if f.path == "investor.tax_id")
    assert tax.ready and [s.outcome for s in tax.samples] == ["agree"] * 4 and tax.count == (4, 4)
    assert proof.ready == proof.total


def test_a_reader_that_reads_another_value_disagrees_and_is_not_ready():
    p = clients(1, seed=7)[0]
    pdf = make_form(p, kind="fillable")
    wrong = {**truth(p), "investor.legal_name": "Someone Else"}
    f = next(f for f in prove(_with_samples(hand_template(), [pdf], [wrong]), [pdf]).fields
             if f.path == "investor.legal_name")  # fmt: skip
    assert (f.ready, f.samples[0].outcome) == (False, "disagree")


def test_blank_on_both_sides_is_missing_and_a_reader_that_misses_a_value_disagrees():
    p = dataclasses.replace(clients(1, seed=7)[0], street="")
    pdf = make_form(p, kind="flattened")
    proof = prove(_with_samples(hand_template(), [pdf], [truth(p)]), [pdf])
    street = next(f for f in proof.fields if f.path == "address.street")
    assert street.samples[0].outcome == "missing" and not street.ready  # nothing agreed anywhere
    missed = {**truth(p), "address.street": "1 Main Street"}
    street = next(f for f in prove(_with_samples(hand_template(), [pdf], [missed]), [pdf]).fields
                  if f.path == "address.street")  # fmt: skip
    assert street.samples[0].outcome == "disagree"


def test_a_failed_check_the_model_agrees_with_does_not_block_ready():
    p = dataclasses.replace(clients(1, seed=7)[0], tax_id="8422-19037")
    pdf = make_form(p, kind="fillable")
    f = next(f for f in prove(_with_samples(hand_template(), [pdf], [truth(p)]), [pdf]).fields
             if f.path == "investor.tax_id")  # fmt: skip
    assert (f.samples[0].outcome, f.ready) == ("failed_check", True)


def _country_proof(clean, printed, opinion):
    p = dataclasses.replace(clients(1, seed=7)[0], country=printed)
    pdf = make_form(p, kind="fillable")
    t = hand_template()
    t.readers["investor.country"].clean = clean
    t = _with_samples(t, [pdf], [{**truth(p), "investor.country": opinion}])
    return next(f for f in prove(t, [pdf]).fields if f.path == "investor.country")


def test_a_reader_whose_cleaning_turns_the_printed_answer_into_another_value_disagrees():
    # the form says "CA", the model read "CA"; a wrong map makes the readers' value "US" from the same raw text
    wrong_map = [Transform(op="map", args={"values": {"CA": "US"}})]
    f = _country_proof(wrong_map, "CA", "CA")
    assert (f.samples[0].read.raw, f.samples[0].read.value) == ("CA", "US")
    assert (f.samples[0].outcome, f.ready) == ("disagree", False)


def test_a_model_that_gives_the_printed_text_agrees_when_the_readers_cleaning_makes_it_their_value():
    p = dataclasses.replace(clients(1, seed=7)[0], dob="04/19/2011")
    for kind in ("fillable", "flattened"):
        pdf = make_form(p, kind=kind)
        for opinion in ("04/19/2011", "2011-04-19"):  # the printed text, and the value itself
            t = _with_samples(hand_template(), [pdf], [{**truth(p), "investor.dob": opinion}])
            f = next(f for f in prove(t, [pdf]).fields if f.path == "investor.dob")
            assert (f.samples[0].read.raw, f.samples[0].read.value) == ("04/19/2011", "2011-04-19")
            assert (f.samples[0].outcome, f.ready) == ("agree", True), (kind, opinion)
    wrong_day = {**truth(p), "investor.dob": "04/18/2011"}
    pdf = make_form(p, kind="flattened")
    f = next(f for f in prove(_with_samples(hand_template(), [pdf], [wrong_day]), [pdf]).fields
             if f.path == "investor.dob")  # fmt: skip
    assert f.samples[0].outcome == "disagree"


def test_a_failed_check_counts_only_when_the_model_read_the_same_bad_answer():
    # the form holds "Estados Unidos" (not an allowed answer); a model that read "US" from it disagrees
    f = _country_proof([], "Estados Unidos", "US")
    assert (f.samples[0].read.status, f.samples[0].outcome) == ("failed_check", "disagree")
    f = _country_proof([], "Estados Unidos", "Estados Unidos")
    assert (f.samples[0].read.status, f.samples[0].outcome, f.ready) == ("failed_check", "failed_check", True)


def test_absent_paths_are_absent_and_left_out_of_the_total():
    p = clients(1, seed=7)[0]
    pdf = make_form(p, kind="fillable")
    t = _with_samples(hand_template(), [pdf], [truth(p)])
    t = t.model_copy(update={"absent": ["wire.bank"], "readers": {k: v for k, v in t.readers.items() if k != "wire.bank"}})
    proof = prove(t, [pdf])
    bank = next(f for f in proof.fields if f.path == "wire.bank")
    assert bank.absent and not bank.ready and bank.samples[0].outcome == "absent"
    assert proof.total == len([f for f in proof.fields if not f.absent])


def test_prove_refuses_pdfs_that_are_not_the_templates_samples():
    p = clients(2, seed=7)
    pdfs = [make_form(x, kind="fillable") for x in p]
    t = _with_samples(hand_template(), pdfs[:1], [truth(p[0])])
    with pytest.raises(FormcastError, match="sample"):
        prove(t, pdfs[1:])
    with pytest.raises(FormcastError, match="sample"):
        prove(t, pdfs)


def test_learn_maps_adds_a_wording_the_model_answered_with_an_allowed_answer():
    # printed as it is (Client.country is free text); the hand map lacks it, and no later strategy reads it otherwise
    # ("United States of America" would not do: the Country label's read stops at "United States", which maps to US)
    p = dataclasses.replace(clients(1, seed=7)[0], country="Estados Unidos")
    pdf = make_form(p, kind="fillable")
    t = _with_samples(hand_template(), [pdf], [{**truth(p), "investor.country": "US"}])
    before = next(f for f in prove(t, [pdf]).fields if f.path == "investor.country")
    assert before.samples[0].outcome in ("disagree", "failed_check")
    learned = learn_maps(t, prove(t, [pdf]))
    after = next(f for f in prove(learned, [pdf]).fields if f.path == "investor.country")
    assert after.samples[0].outcome == "agree" and after.samples[0].read.value == "US"
    assert t.readers["investor.country"].clean[0].args["values"].get("Estados Unidos") is None  # a copy


def test_learn_maps_puts_a_map_first_when_the_reader_has_none_and_learns_nothing_two_readings_contradict():
    people = [dataclasses.replace(c, country="America") for c in clients(2, seed=7)]
    pdfs = [make_form(c, kind="fillable") for c in people]
    t = hand_template()
    t.readers["investor.country"].clean = []
    contradicting = _with_samples(t, pdfs, [{**truth(people[0]), "investor.country": "US"},
                                            {**truth(people[1]), "investor.country": "CA"}])  # fmt: skip
    assert learn_maps(contradicting, prove(contradicting, pdfs)).readers["investor.country"].clean == []
    one = _with_samples(t, pdfs[:1], [{**truth(people[0]), "investor.country": "US"}])
    clean = learn_maps(one, prove(one, pdfs[:1])).readers["investor.country"].clean
    assert [(c.op, c.args) for c in clean] == [("map", {"values": {"America": "US"}})]


def test_sample_marks_give_the_read_place_and_the_editable_boxes():
    p = clients(1, seed=7)[0]
    pdf = make_form(p, kind="flattened")
    marks = sample_marks(hand_template(), pdf)
    kinds = {(m.path, m.kind) for m in marks}
    assert ("investor.tax_id", "read") in kinds and ("investor.tax_id", "box") in kinds
    assert ("investor.tax_id", "label") in kinds
    assert {m.answer for m in marks if m.path == "investor.type" and m.kind == "option"} == set(INVESTOR_TYPES)


def test_sample_marks_move_boxes_with_the_page_and_find_the_option_widgets():
    p = clients(1, seed=7)[0]
    plain = sample_marks(hand_template(), make_form(p, kind="fillable"))
    shifted = sample_marks(hand_template(), make_form(p, kind="flattened", shift=12))
    box = next(m for m in shifted if (m.path, m.kind) == ("investor.tax_id", "box"))
    assert box.rect[1] == pytest.approx(FIELD_RECTS["tax_id"][1] + 12, abs=0.5)
    options = [(m.answer, m.page, m.rect) for m in plain if (m.path, m.kind) == ("investor.type", "option")]
    # each option twice: the widget of the first choice read, then the drawn box of the second
    expected = [(v, 0, pytest.approx(TYPE_BOXES[v][1], abs=0.5)) for v in INVESTOR_TYPES]
    assert options == expected + expected
