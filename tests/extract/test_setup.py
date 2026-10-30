"""build_setup and add_line: a template built from samples in stages, with a fake model that reads the corpus."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

import pytest

import formcast.extract.setup as setup_mod
from formcast.errors import FormcastError, SessionCancelled
from formcast.extract.batch import route
from formcast.extract.edit import SetBox, apply_edit
from formcast.extract.model import BoxRead, concrete_paths
from formcast.extract.prompts import READERS_SYSTEM
from formcast.extract.proof import Proof, learn_maps, prove, sample_marks
from formcast.extract.read import extract
from formcast.extract.samples import SamplePdf
from formcast.extract.setup import MAX_SAMPLES, STAGES, SetupSpec, StageEvent, add_line, build_setup
from formcast.llm import LLMConfig, LLMResponse
from formcast.match import PdfFeatures
from formcast.pagemap.build import DetectPolicy
from formcast.schema import REASON_KEY, flatten_schema
from tests.extract.conftest import corpus_clients
from tests.extract.corpus import (
    FIELD_RECTS,
    SHIFT,
    Client,
    assert_no_wrong_found,
    clients,
    hand_template,
    make_form,
    truth,
)
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


def _without_reasons(node):
    """``node`` (a schema) with every ``x-required-reason`` taken out."""
    if isinstance(node, dict):
        return {k: _without_reasons(v) for k, v in node.items() if k != REASON_KEY}
    if isinstance(node, list):
        return [_without_reasons(v) for v in node]
    return node


def test_destination_start_marks_the_fields_this_form_lacks_absent(five_samples):
    dest = {"type": "object", "properties": {**CORPUS_SCHEMA["properties"], "subscription": {"type": "object",
            "properties": {"amount": {"type": "number"}}}, "signers": {"type": "array", "maxItems": 2,
            "items": {"type": "object", "properties": {"name": {"type": "string"}}}}}}  # fmt: skip
    model = CorpusModel(five_samples.clients)
    spec = SetupSpec(name="x", start="destination", destination=dest, destination_name="sub")
    r = build_setup(five_samples.pdfs, spec, model, **NO_DETECTORS)
    assert set(r.template.absent) == {"subscription.amount", "signers[0].name", "signers[1].name"}
    assert _without_reasons(r.template.schema_) == dest and r.template.setup.destination == "sub"
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


def test_a_cancel_from_on_page_stops_the_build_as_a_cancel_before_any_model_call(five_samples):
    model = CorpusModel(five_samples.clients)

    def on_page(i, progress):
        raise SessionCancelled("cancelled by the person")

    with pytest.raises(SessionCancelled, match="^cancelled by the person$"):
        build_setup(five_samples.pdfs, SetupSpec(name="x"), model, on_page=on_page, **NO_DETECTORS)
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


def test_every_public_name_of_setup_is_a_public_name_of_the_package():
    import formcast.extract as package

    assert set(setup_mod.__all__) <= set(package.__all__)
    assert package.MAX_SAMPLES == MAX_SAMPLES


def test_the_built_templates_form_has_the_blind_fingerprint_so_a_fillable_copy_routes_by_it(five_samples):
    r = build_setup(five_samples.pdfs, SetupSpec(name="x"), CorpusModel(five_samples.clients), **NO_DETECTORS)
    first = five_samples.clients["sample_1.pdf"]  # the first fillable sample, whose form the template records
    assert r.template.form.fingerprint == PdfFeatures(five_samples.pdfs[0].pdf).fingerprint_blind
    # another client's answers in the form fields (the wire paragraph is page text: the same bank and account)
    other = dataclasses.replace(clients(1, seed=77)[0], bank=first.bank, account=first.account)
    assert other.legal_name != first.legal_name and other.tax_id != first.tax_id
    copy = make_form(other, kind="fillable")
    use, _, score = route([r.template], copy)
    assert use is r.template and score == 1.0
    got = extract(r.template, copy)
    assert got.matched == "fingerprint"
    assert_no_wrong_found(got, other)


def test_the_setup_keeps_how_the_samples_were_read_and_shown(five_samples):
    spec = SetupSpec(name="x", detect="manual", pages={3: [0]}, ocr="never", vision=False, sample_how="the first five")
    r = build_setup(five_samples.pdfs, spec, CorpusModel(five_samples.clients), **NO_DETECTORS)
    setup = r.template.setup
    assert (setup.detect, setup.pages, setup.ocr, setup.vision) == ("manual", {3: [0]}, "never", False)
    assert setup.sample_how == "the first five"
    again = type(r.template).model_validate_json(r.template.model_dump_json(by_alias=True))
    assert again.setup == setup  # the page numbers' keys come back as ints from JSON


def test_a_box_drawn_where_a_shifted_samples_marks_show_it_reads_an_unshifted_copy(five_samples):
    r = build_setup(five_samples.pdfs, SetupSpec(name="x"), CorpusModel(five_samples.clients), **NO_DETECTORS)
    pdfs = [p.pdf for p in five_samples.pdfs]
    shifted = 4  # sample_5: flattened and pushed down the page by SHIFT
    marks = sample_marks(r.template, pdfs[shifted])
    mark = next(m for m in marks if (m.path, m.kind) == ("investor.dob", "box"))
    x0, y0, x1, y1 = FIELD_RECTS["dob"]
    assert mark.rect == pytest.approx((x0, y0 + SHIFT, x1, y1 + SHIFT), abs=0.5)  # where it is on that sample
    # the person moves the box a little on that sample (wider to the right), as the workbench sends it
    drawn = (mark.rect[0], mark.rect[1], mark.rect[2] + 20, mark.rect[3])
    edit = SetBox(path="investor.dob", page=mark.page, rect=drawn, sample=shifted)
    edited = apply_edit(r.template, edit, pdfs=pdfs)
    box = next(s for s in edited.readers["investor.dob"].strategies if isinstance(s, BoxRead))
    assert box.rect == pytest.approx((x0, y0, x1 + 20, y1), abs=0.5)  # in the layout's place
    assert next(f for f in prove(edited, pdfs).fields if f.path == "investor.dob").ready
    other = clients(1, seed=78)[0]
    got = next(f for f in extract(edited, make_form(other, kind="flattened")).fields if f.path == "investor.dob")
    assert (got.status, got.strategy, got.value) == ("found", "box", truth(other)["investor.dob"])


def test_the_ai_start_classifies_required(five_samples, monkeypatch):
    seen = []
    real = setup_mod.derive_schema

    def spy(*args, **kwargs):
        seen.append(kwargs.get("classify_required"))
        return real(*args, **kwargs)

    model = CorpusModel(five_samples.clients)
    leveled = json.loads(json.dumps(CORPUS_SCHEMA))
    leveled["properties"]["investor"]["properties"]["tax_id"][REASON_KEY] = "the form says 'An entry is required'"
    model.schema = leveled
    monkeypatch.setattr(setup_mod, "derive_schema", spy)
    r = build_setup(five_samples.pdfs, SetupSpec(name="x"), model, **NO_DETECTORS)
    assert seen == [True]
    investor = r.template.schema_["properties"]["investor"]
    assert "tax_id" in investor["required"]
    assert investor["properties"]["tax_id"][REASON_KEY] == "the form says 'An entry is required'"


def test_destination_fields_keep_their_levels_with_the_destination_reason(five_samples):
    dest = {"type": "object", "required": ["investor"], "properties": {
        "investor": {"type": "object", "required": ["tax_id"], "properties": {
            "tax_id": {"type": "string"},
            "name": {"type": "string", REASON_KEY: "the destination says so"}}},
        "signers": {"type": "array", "maxItems": 2, "items": {"type": "object", "required": ["name"],
            "properties": {"name": {"type": "string"}}}}}}  # fmt: skip
    spec = SetupSpec(name="x", start="destination", destination=dest, destination_name="sub")
    r = build_setup(five_samples.pdfs, spec, CorpusModel(five_samples.clients), **NO_DETECTORS)
    schema = r.template.schema_
    investor = schema["properties"]["investor"]
    assert schema["required"] == ["investor"] and investor["required"] == ["tax_id"]
    assert investor["properties"]["tax_id"][REASON_KEY] == "from the destination's schema"
    assert investor["properties"]["name"][REASON_KEY] == "the destination says so"
    item = schema["properties"]["signers"]["items"]
    assert item["required"] == ["name"] and item["properties"]["name"][REASON_KEY] == "from the destination's schema"
    assert REASON_KEY not in investor and REASON_KEY not in schema["properties"]["signers"]  # groups carry none
    assert REASON_KEY not in dest["properties"]["investor"]["properties"]["tax_id"]  # the spec's own is left alone
