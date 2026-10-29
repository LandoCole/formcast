"""build_setup and add_line: a template built from samples in stages, with a fake model that reads the corpus."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import formcast.extract.setup as setup_mod
from formcast.errors import FormcastError, SessionCancelled
from formcast.extract.model import concrete_paths
from formcast.extract.prompts import READERS_SYSTEM
from formcast.extract.proof import Proof, learn_maps, prove
from formcast.extract.read import extract
from formcast.extract.samples import SamplePdf
from formcast.extract.setup import MAX_SAMPLES, STAGES, SetupSpec, StageEvent, add_line, build_setup
from formcast.llm import LLMConfig, LLMResponse
from formcast.pagemap.build import DetectPolicy
from formcast.schema import flatten_schema
from tests.extract.conftest import corpus_clients
from tests.extract.corpus import SHIFT, Client, assert_no_wrong_found, clients, hand_template, make_form
from tests.extract.fakes import CorpusModel

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))
CORPUS_SCHEMA = hand_template().schema_


@dataclass(frozen=True)
class Samples:
    pdfs: list[SamplePdf]
    clients: dict[str, Client]


@pytest.fixture(scope="module")
def five_samples() -> Samples:
    """Five corpus copies: three fillable, two flattened, the last of them pushed down the page by ``SHIFT``."""
    people = clients(5, seed=31)
    kinds = [("fillable", 0.0), ("fillable", 0.0), ("fillable", 0.0), ("flattened", 0.0), ("flattened", SHIFT)]
    pdfs = [
        SamplePdf(f"sample_{n}.pdf", make_form(client, kind=kind, shift=shift))
        for n, (client, (kind, shift)) in enumerate(zip(people, kinds), start=1)
    ]
    return Samples(pdfs, {pdf.file: client for pdf, client in zip(pdfs, people)})


def assert_proven(proof: Proof) -> None:
    """Every field some sample has is ready, and no sample disagrees (a field no sample fills cannot be proven)."""
    for field in proof.fields:
        assert not any(s.outcome == "disagree" for s in field.samples), field
        if not field.absent and any(s.outcome != "missing" for s in field.samples):
            assert field.ready, field


def no_unreadable(warnings: list[str]) -> None:
    """No warning says a path was left with no way to read it (dropped alternative wordings are fine)."""
    assert not [w for w in warnings if "no way to read it" in w or "found no place" in w], warnings


def test_ai_start_builds_a_ready_template_that_reads_the_corpus_with_no_wrong_value(corpus_dir, five_samples):
    model = CorpusModel(five_samples.clients)
    events: list[StageEvent] = []
    spec = SetupSpec(name="Client info")
    result = build_setup(five_samples.pdfs, spec, model, on_stage=events.append, **NO_DETECTORS)
    assert [e.key for e in events if e.status == "done"] == [key for key, _ in STAGES]
    assert [e.status for e in events] == ["running", "done"] * len(STAGES)
    check = events[-1]
    assert check.detail == f"{result.proof.ready} of {result.proof.total} fields ready"
    assert_proven(result.proof)
    assert result.proof.ready > 0
    no_unreadable(result.warnings)
    assert result.template.template_id == "client_info" and result.template.setup.start == "ai"
    assert result.template.schema_ == CORPUS_SCHEMA
    assert len(result.template.samples) == 5 and len(result.samples) == 5
    assert all(len(s.sha256) == 64 for s in result.template.samples)
    calls = sum(model.calls.values())
    assert model.calls["derive"] == 1 and model.calls["values"] == 5
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10 * calls, 5 * calls)
    for f, client in corpus_clients(corpus_dir):
        assert_no_wrong_found(extract(result.template, f, file=f.name), client)


def test_lines_start_records_which_paths_each_line_became(five_samples):
    model = CorpusModel(five_samples.clients)
    spec = SetupSpec(name="x", template_id="wires", start="lines", lines=["tax ID", "wire details", "  "])
    r = build_setup(five_samples.pdfs, spec, model, **NO_DETECTORS)
    setup = r.template.setup
    assert setup.start == "lines" and setup.lines == ["tax ID", "wire details"]
    assert setup.line_paths == {"tax ID": ["investor.tax_id"], "wire details": ["wire.bank", "wire.account"]}
    leaves = {sp.path for sp in flatten_schema(r.template.schema_) if sp.type != "object"}
    assert leaves == {"investor.tax_id", "wire.bank", "wire.account"}
    assert set(r.template.readers) == leaves and r.template.template_id == "wires"
    assert_proven(r.proof)
    no_unreadable(r.warnings)


def test_destination_start_marks_the_fields_this_form_lacks_absent(five_samples):
    dest = {"type": "object", "properties": {**CORPUS_SCHEMA["properties"], "subscription": {"type": "object",
            "properties": {"amount": {"type": "number"}}}, "signers": {"type": "array", "maxItems": 2,
            "items": {"type": "object", "properties": {"name": {"type": "string"}}}}}}  # fmt: skip
    model = CorpusModel(five_samples.clients)
    spec = SetupSpec(name="x", start="destination", destination=dest, destination_name="sub")
    r = build_setup(five_samples.pdfs, spec, model, **NO_DETECTORS)
    assert set(r.template.absent) == {"subscription.amount", "signers[0].name", "signers[1].name"}
    assert r.template.schema_ == dest and r.template.setup.destination == "sub"
    assert r.proof.total == len([f for f in r.proof.fields if not f.absent])
    assert not set(r.template.absent) & set(r.template.readers)
    assert all("subscription.amount" not in s.opinion for s in r.template.samples)  # absent paths are not asked
    assert_proven(r.proof)


def test_an_unreadable_sample_fails_before_any_model_call(five_samples):
    model = CorpusModel(five_samples.clients)
    with pytest.raises(FormcastError, match="broken.pdf"):
        build_setup(
            [*five_samples.pdfs[:2], SamplePdf("broken.pdf", b"%PDF-1.7 nope")], SetupSpec(name="x"), model,
            **NO_DETECTORS,
        )  # fmt: skip
    assert model.calls == {}


def test_detect_off_skips_the_detect_stage(five_samples):
    events: list[StageEvent] = []
    model = CorpusModel(five_samples.clients)
    r = build_setup(
        five_samples.pdfs, SetupSpec(name="x", detect="off"), model, on_stage=events.append, **NO_DETECTORS
    )
    detect = [e for e in events if e.key == "detect"]
    assert [e.status for e in detect] == ["skipped"]
    assert r.template.setup.detect == "off"


def test_a_cancel_from_on_stage_stops_the_build(five_samples):
    model = CorpusModel(five_samples.clients)

    def on_stage(event: StageEvent) -> None:
        if event.key == "fields" and event.status == "running":
            raise SessionCancelled("cancelled")

    with pytest.raises(SessionCancelled):
        build_setup(five_samples.pdfs, SetupSpec(name="x"), model, on_stage=on_stage, **NO_DETECTORS)
    assert model.calls == {}


def test_more_than_twenty_samples_or_none_are_refused():
    model = CorpusModel({})
    with pytest.raises(FormcastError, match="1 to 20 samples"):
        build_setup([], SetupSpec(name="x"), model, **NO_DETECTORS)
    too_many = [SamplePdf(f"{n}.pdf", b"") for n in range(MAX_SAMPLES + 1)]
    with pytest.raises(FormcastError, match="1 to 20 samples"):
        build_setup(too_many, SetupSpec(name="x"), model, **NO_DETECTORS)
    assert model.calls == {}


def test_a_spec_with_no_name_no_lines_or_no_destination_is_refused():
    with pytest.raises(ValueError, match="name"):
        SetupSpec(name="  ")
    with pytest.raises(ValueError, match="line"):
        SetupSpec(name="x", start="lines", lines=["", " "])
    with pytest.raises(ValueError, match="destination"):
        SetupSpec(name="x", start="destination")


class Forgetful(CorpusModel):
    """The corpus model, but its reader proposals leave out ``wire.account``."""

    def complete(self, messages, tools, **kwargs):
        reply = super().complete(messages, tools, **kwargs)
        if messages[0].content != READERS_SYSTEM:
            return reply
        answer = json.loads(reply.text)
        answer["readers"] = [r for r in answer["readers"] if r["path"] != "wire.account"]
        return LLMResponse(text=json.dumps(answer), tool_calls=[], usage=reply.usage, raw={})


def test_a_path_the_model_proposes_nothing_for_is_warned_of_and_gets_no_reader(five_samples):
    r = build_setup(five_samples.pdfs, SetupSpec(name="x"), Forgetful(five_samples.clients), **NO_DETECTORS)
    assert "wire.account: the model proposed no way to read it" in r.warnings
    assert "wire.account" not in r.template.readers
    account = next(f for f in r.proof.fields if f.path == "wire.account")
    assert not account.ready  # the model read an account on every sample, and no reader did


class Unmapped(CorpusModel):
    """The corpus model, but its proposal for ``investor.country`` has no clean steps (no map of the wordings)."""

    def complete(self, messages, tools, **kwargs):
        reply = super().complete(messages, tools, **kwargs)
        if messages[0].content != READERS_SYSTEM:
            return reply
        answer = json.loads(reply.text)
        for r in answer["readers"]:
            if r["path"] == "investor.country":
                r.pop("clean", None)
        return LLMResponse(text=json.dumps(answer), tool_calls=[], usage=reply.usage, raw={})


def test_a_build_only_suggests_the_wordings_it_saw_and_learn_maps_applies_them_when_accepted(five_samples):
    r = build_setup(five_samples.pdfs, SetupSpec(name="x"), Unmapped(five_samples.clients), **NO_DETECTORS)
    assert r.template.readers["investor.country"].clean == []  # nothing learned behind the person's back
    country = next(f for f in r.proof.fields if f.path == "investor.country")
    assert not country.ready and country.suggested == {"Canada": "CA", "United States": "US"}
    accepted = learn_maps(r.template, r.proof)
    assert next(f for f in prove(accepted, [p.pdf for p in five_samples.pdfs]).fields
                if f.path == "investor.country").ready  # fmt: skip


def test_a_model_config_opens_one_provider_for_the_whole_build_and_closes_it(monkeypatch, five_samples):
    made: list[CorpusModel] = []
    closed: list[bool] = []

    class Closing(CorpusModel):
        def close(self) -> None:
            closed.append(True)

    def make(config):
        made.append(Closing(five_samples.clients))
        return made[-1]

    monkeypatch.setattr(setup_mod, "make_provider", make)
    config = LLMConfig(base_url="https://example.test/v1/", api_key="sk-test", model="m-1")
    build_setup(five_samples.pdfs, SetupSpec(name="x"), config, **NO_DETECTORS)
    assert len(made) == 1 and closed == [True] and made[0].calls["values"] == 5


def test_add_line_adds_fields_their_readers_and_their_readings_then_reproves(five_samples):
    model = CorpusModel(five_samples.clients)
    r = build_setup(five_samples.pdfs, SetupSpec(name="x", start="lines", lines=["tax ID"]), model, **NO_DETECTORS)
    before = dict(model.calls)
    added = add_line(r.template, "wire details", r.samples, model)
    assert model.calls["fields"] == before["fields"] + 1 and model.calls["values"] == before["values"] + 5
    assert {"wire.bank", "wire.account"} <= set(added.template.readers)
    assert "investor.tax_id" in added.template.readers
    assert all("wire.bank" in s.opinion and "investor.tax_id" in s.opinion for s in added.template.samples)
    setup = added.template.setup
    assert setup.lines == ["tax ID", "wire details"]
    assert setup.line_paths["wire details"] == ["wire.bank", "wire.account"]
    assert set(concrete_paths(added.template.schema_)) == {"investor.tax_id", "wire.bank", "wire.account"}
    assert [f.path for f in added.proof.fields if f.ready] == ["investor.tax_id", "wire.bank", "wire.account"]
    assert_proven(added.proof)
    no_unreadable(added.warnings)
    assert added.samples == r.samples


def test_add_line_refuses_samples_that_are_not_the_templates_before_any_model_call(five_samples):
    model = CorpusModel(five_samples.clients)
    r = build_setup(five_samples.pdfs, SetupSpec(name="x", start="lines", lines=["tax ID"]), model, **NO_DETECTORS)
    before = dict(model.calls)
    with pytest.raises(FormcastError, match="not the template's samples"):
        add_line(r.template, "wire details", r.samples[:4], model)
    assert model.calls == before
