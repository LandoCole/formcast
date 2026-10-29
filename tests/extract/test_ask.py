"""The four model calls of setup (fields from lines, on-form fields, readers, one document's values), against fakes."""

from __future__ import annotations

import json
import threading

import pytest

from formcast.errors import FormcastError
from formcast.extract.ask import (
    READER_CHUNK,
    fields_from_lines,
    fields_on_form,
    propose_readers,
    read_values,
)
from formcast.extract.layout import learn_layout
from formcast.extract.model import concrete_paths
from formcast.extract.readers import BoxRef, ReaderProposal, resolve
from formcast.extract.samples import SamplePdf, read_samples
from formcast.llm import ImagePart, LLMResponse, TextPart, Usage
from formcast.pagemap.build import DetectPolicy
from tests.extract.corpus import clients, hand_template, make_form
from tests.extract.fakes import CorpusModel, Scripted, asked_paths, user_text
from tests.extract.test_read import truth

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))
SCHEMA = hand_template().schema_
PEOPLE = clients(2, seed=21)


@pytest.fixture(scope="module")
def sample_docs():
    """A fillable sample (a.pdf) and a flattened one (b.pdf) of the corpus form."""
    pdfs = [
        SamplePdf("a.pdf", make_form(PEOPLE[0], kind="fillable")),
        SamplePdf("b.pdf", make_form(PEOPLE[1], kind="flattened")),
    ]
    return read_samples(pdfs, **NO_DETECTORS)


class Echo:
    """Answers each readers call with a bare proposal for every asked path; records what it was sent."""

    def __init__(self) -> None:
        self.calls: list[list] = []
        self._lock = threading.Lock()

    def complete(self, messages, tools, **kwargs):
        with self._lock:
            self.calls.append(list(messages))
        text = json.dumps({"readers": [{"path": p} for p in asked_paths(user_text(messages[1]))]})
        return LLMResponse(text=text, tool_calls=[], usage=Usage(10, 5), raw={})

    def close(self) -> None:
        pass


def test_fields_from_lines_sends_the_lines_and_the_sample_and_returns_a_checked_schema(sample_docs):
    reply = {"schema": {"type": "object", "properties": {"wire": {"type": "object", "properties": {
        "bank": {"type": "string", "title": "Bank", "description": "The sending bank."}}}}},
        "line_paths": {"wire details": ["wire.bank"]}}  # fmt: skip
    llm = Scripted(json.dumps(reply))
    got = fields_from_lines(["wire details"], sample_docs, llm)
    assert got.line_paths == {"wire details": ["wire.bank"]} and got.usage.input_tokens == 10
    assert got.schema == reply["schema"]
    text = " ".join(p.text for p in llm.calls[0][1].content if isinstance(p, TextPart))
    assert "1. wire details" in text and "SAMPLE 1" in text
    assert any(isinstance(p, ImagePart) for p in llm.calls[0][1].content)
    # images of the first sample with form fields and of the first without, each after its line
    labels = [p.text for p in llm.calls[0][1].content if isinstance(p, TextPart) and p.text.startswith("Sample ")]
    assert "Sample 1, page 0:" in labels and "Sample 2, page 0:" in labels


def test_a_line_path_that_is_not_in_the_schema_or_a_name_that_exists_is_asked_again(sample_docs):
    bad = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["b"]}}
    good = {"schema": {"type": "object", "properties": {"b": {"type": "string"}}}, "line_paths": {"x": ["b"]}}
    llm = Scripted(json.dumps(bad), json.dumps(good))
    assert fields_from_lines(["x"], sample_docs, llm).line_paths == {"x": ["b"]}
    assert "can't be used" in str(llm.calls[1][-1].content)
    clash = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["a"]}}
    with pytest.raises(FormcastError, match="could not be used"):
        fields_from_lines(["x"], sample_docs, Scripted(json.dumps(clash), json.dumps(clash)), existing=["a"])


def test_a_line_with_no_fields_or_a_schema_that_is_not_valid_is_asked_again(sample_docs):
    one = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["a"]}}
    llm = Scripted(json.dumps(one), json.dumps({**one, "line_paths": {"x": ["a"], "y": ["a"]}}))
    assert fields_from_lines(["x", "y"], sample_docs, llm).line_paths == {"x": ["a"], "y": ["a"]}
    assert "'y'" in str(llm.calls[1][-1].content)
    broken = {"schema": {"type": "object", "properties": {"a": {"type": "string", "pattern": "("}}},
              "line_paths": {"x": ["a"]}}  # fmt: skip
    with pytest.raises(FormcastError, match="could not be used"):
        fields_from_lines(["x"], sample_docs, Scripted(json.dumps(broken), json.dumps(broken)))


def test_no_lines_are_refused_before_any_call(sample_docs):
    llm = Scripted()
    with pytest.raises(FormcastError, match="no lines"):
        fields_from_lines(["  "], sample_docs, llm)
    assert llm.calls == []


def test_fields_on_form_keeps_only_paths_of_the_schema(sample_docs):
    bad, good = {"on_form": ["investor.tax_id", "made.up"]}, {"on_form": ["investor.tax_id"]}
    llm = Scripted(json.dumps(bad), json.dumps(good))
    assert fields_on_form(SCHEMA, sample_docs, llm) == (["investor.tax_id"], Usage(20, 10))
    assert "made.up" in str(llm.calls[1][-1].content)
    text = " ".join(p.text for p in llm.calls[0][1].content if isinstance(p, TextPart))
    assert "- owners[].name: string" in text and "- investor.tax_id: string" in text


def test_a_group_is_not_a_field_on_the_form(sample_docs):
    llm = Scripted(json.dumps({"on_form": ["owners"]}), json.dumps({"on_form": ["owners[].name", "wire.bank"]}))
    assert fields_on_form(SCHEMA, sample_docs, llm)[0] == ["owners[].name", "wire.bank"]  # in the schema's order


def test_propose_readers_chunks_paths_and_merges_the_answers(sample_docs):
    schema = {"type": "object", "properties": {f"f{i:02d}": {"type": "string"} for i in range(READER_CHUNK + 1)}}
    paths = concrete_paths(schema)
    llm = Echo()
    proposals, usage = propose_readers(schema, paths, sample_docs, llm)
    assert [p.path for p in proposals] == paths and usage == Usage(20, 10)
    sent = sorted(len(asked_paths(user_text(call[1]))) for call in llm.calls)
    assert sent == [1, READER_CHUNK]


def test_propose_readers_asks_for_concrete_paths_and_shows_their_fields(sample_docs):
    llm = Echo()
    propose_readers(SCHEMA, ["owners[1].name", "wire.bank"], sample_docs, llm)
    text = user_text(llm.calls[0][1])
    assert asked_paths(text) == ["owners[1].name", "wire.bank"]
    assert "- owners[].name: string" in text and "- wire.bank: string" in text and "investor.tax_id" not in text


def test_a_proposal_for_a_path_not_asked_is_asked_again(sample_docs):
    llm = Scripted(
        json.dumps({"readers": [{"path": "wire.bank"}, {"path": "wire.account"}]}),
        json.dumps({"readers": [{"path": "wire.bank", "phrases": ["sent from"]}]}),
    )
    proposals, _ = propose_readers(SCHEMA, ["wire.bank"], sample_docs, llm)
    assert proposals == [ReaderProposal(path="wire.bank", phrases=["sent from"])]
    assert "wire.account" in str(llm.calls[1][-1].content)


def test_a_box_names_its_sample_by_the_number_the_views_show(sample_docs):
    reply = {"readers": [{"path": "investor.type", "box": {"sample": 2, "id": "c0_1"},
                          "options": {"trust": {"box": {"sample": 1, "id": "c0_4"}}}}]}  # fmt: skip
    proposals, _ = propose_readers(SCHEMA, ["investor.type"], sample_docs, Scripted(json.dumps(reply)))
    assert proposals[0].box == BoxRef(sample=1, id="c0_1")
    assert proposals[0].options["trust"].box == BoxRef(sample=0, id="c0_4")


def test_read_values_fills_unasked_keys_with_none_and_drops_unknown_ones(sample_docs):
    schema = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "string"}}}
    llm = Scripted(json.dumps({"values": {"a": 1, "zzz": 2}}))
    values, usage = read_values(schema, ["a", "b"], sample_docs[1], 2, llm)
    assert values == {"a": 1, "b": None} and usage == Usage(10, 5)
    text = user_text(llm.calls[0][1])
    assert "SAMPLE 2 (b.pdf, flattened" in text and "SAMPLE 1" not in text and "a.pdf" not in text
    labels = [p.text for p in llm.calls[0][1].content if isinstance(p, TextPart) and p.text.startswith("Sample ")]
    assert labels and all(label.startswith("Sample 2, page ") for label in labels)


def test_values_that_name_no_asked_path_are_asked_again(sample_docs):
    nested = {"values": {"wire": {"bank": "First Bank"}}}
    llm = Scripted(json.dumps(nested), json.dumps({"values": {"wire.bank": "First Bank"}}))
    assert read_values(SCHEMA, ["wire.bank", "wire.account"], sample_docs[0], 1, llm)[0] == {
        "wire.bank": "First Bank", "wire.account": None,
    }  # fmt: skip
    assert "no key of values" in str(llm.calls[1][-1].content)


def test_nothing_to_ask_makes_no_call(sample_docs):
    llm = Scripted()
    assert propose_readers(SCHEMA, [], sample_docs, llm) == ([], Usage())
    assert read_values(SCHEMA, [], sample_docs[0], 1, llm) == ({}, Usage())
    assert llm.calls == []


def test_vision_false_sends_no_images(sample_docs):
    lines = {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}, "line_paths": {"x": ["a"]}}
    llm = Scripted(
        json.dumps(lines),
        json.dumps({"on_form": ["wire.bank"]}),
        json.dumps({"readers": [{"path": "wire.bank"}]}),
        json.dumps({"values": {"wire.bank": "x"}}),
    )
    fields_from_lines(["x"], sample_docs, llm, vision=False)
    fields_on_form(SCHEMA, sample_docs, llm, vision=False)
    propose_readers(SCHEMA, ["wire.bank"], sample_docs, llm, vision=False)
    read_values(SCHEMA, ["wire.bank"], sample_docs[0], 1, llm, vision=False)
    assert len(llm.calls) == 4
    assert all(not any(isinstance(p, ImagePart) for p in call[1].content) for call in llm.calls)


def test_the_corpus_model_answers_each_call_from_the_corpus(sample_docs):
    model = CorpusModel({"a.pdf": PEOPLE[0], "b.pdf": PEOPLE[1]})
    got = fields_from_lines(["tax ID", "wire details"], sample_docs, model)
    assert got.line_paths == {"tax ID": ["investor.tax_id"], "wire details": ["wire.bank", "wire.account"]}
    assert concrete_paths(got.schema) == ["investor.tax_id", "wire.bank", "wire.account"]
    dest = {"type": "object", "properties": {**SCHEMA["properties"], "extra": {"type": "string"}}}
    on_form, _ = fields_on_form(dest, sample_docs, model)
    assert "extra" not in on_form and "owners[].percent" in on_form and "investor.type" in on_form
    paths = concrete_paths(SCHEMA)
    proposals, _ = propose_readers(SCHEMA, paths, sample_docs, model)
    readers, warnings = resolve(proposals, sample_docs, learn_layout([d.pdf for d in sample_docs]), SCHEMA)
    assert set(readers) == set(paths) and not any("no way to read" in w for w in warnings)
    values, _ = read_values(SCHEMA, paths, sample_docs[1], 2, model)
    assert values == {p: truth(PEOPLE[1]).get(p) for p in paths}
    assert model.calls == {"fields": 1, "on_form": 1, "readers": 1, "values": 1}
    assert model() is model
