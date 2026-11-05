"""formcast.extract.chat: a person's message made into proven edits of a template, against a scripted model."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from formcast.errors import FormcastError
from formcast.extract.chat import MAX_HISTORY, ChatArea, ChatTurn, _template_lines, propose_fix
from formcast.extract.edit import AddField, SetBox, SetReading
from formcast.extract.model import SampleRecord
from formcast.extract.page import DocText
from formcast.extract.proof import prove
from formcast.extract.samples import SamplePdf, read_samples, text_lines
from formcast.llm import ImagePart, LLMResponse, TextPart, Usage
from formcast.pagemap.build import DetectPolicy
from tests.extract.corpus import FIELD_RECTS, SHIFT, clients, hand_template, make_form, truth

NO_DETECTORS = dict(detectors={}, policy=DetectPolicy(digital=(), scanned=()))


class Scripted:
    """Answers each call with the next text; records what it was sent."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list] = []

    def complete(self, messages, tools, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(text=self.answers.pop(0), tool_calls=[], usage=Usage(10, 5), raw={})

    def close(self) -> None:
        pass


def setup(*kinds, shifts=None, seed=21):
    """The hand template with a sample of each kind ("flattened", "fillable"), the model's readings the truth."""
    people = clients(len(kinds), seed=seed)
    shifts = shifts or [0.0] * len(kinds)
    pdfs = [make_form(c, kind=k, shift=s) for c, k, s in zip(people, kinds, shifts)]
    records = [SampleRecord(file=f"s{n}.pdf", sha256=hashlib.sha256(p).hexdigest(), opinion=truth(c))
               for n, (p, c) in enumerate(zip(pdfs, people))]  # fmt: skip
    template = hand_template().model_copy(update={"samples": records})
    docs = read_samples([SamplePdf(f"s{n}.pdf", p) for n, p in enumerate(pdfs)], **NO_DETECTORS)
    return template, docs, people


def line_id(doc, text, page=0):
    """The id (``t<page>_<n>``) of the text line of ``doc`` that reads ``text``."""
    with DocText(doc.pdf) as pdf:
        for n, (line, _) in enumerate(text_lines(doc, pdf, page)):
            if line == text:
                return f"t{page}_{n}"
    raise AssertionError(f"no line {text!r}")


def answer(reply, *edits):
    return json.dumps({"reply": reply, "edits": list(edits)})


def sent(llm, call=0):
    """The text the user message of a call held."""
    return "\n".join(p.text for p in llm.calls[call][1].content if isinstance(p, TextPart))


def tax_copy(docs, people):
    """Edits that add ``investor.tin_copy``, read off the tax ID's line of sample 1, and its readings."""
    return [
        {"op": "add_field", "path": "investor.tin_copy", "title": "Taxpayer ID again", "type": "text"},
        {"op": "set_box", "path": "investor.tin_copy", "line": line_id(docs[0], people[0].tax_id), "sample": 1},
        *[{"op": "set_reading", "path": "investor.tin_copy", "sample": n, "value": c.tax_id}
          for n, c in enumerate(people, start=1)],
    ]  # fmt: skip


def test_a_line_id_becomes_the_answers_cell_and_the_preview_reads_it():
    template, docs, people = setup("flattened", "flattened")
    llm = Scripted(answer("Added a second taxpayer ID.", *tax_copy(docs, people)))
    out = propose_fix(template, docs, "read the taxpayer id again as its own field", llm, vision=False)
    assert out.reply == "Added a second taxpayer ID."
    assert isinstance(out.edits[0], AddField)
    box = out.edits[1]
    assert isinstance(box, SetBox) and box.sample is None and box.page == 0
    x0, y0, x1, y1 = box.rect  # the answer's cell: it holds the tax ID's line and stops short of its label
    assert x0 > 140 and y0 < FIELD_RECTS["tax_id"][1] + 6 and y1 > FIELD_RECTS["tax_id"][3] - 6
    assert [e.sample for e in out.edits[2:]] == [0, 1] and all(isinstance(e, SetReading) for e in out.edits[2:])
    assert out.preview["investor.tin_copy"].ready
    assert [s.read.value for s in out.preview["investor.tin_copy"].samples] == [c.tax_id for c in people]
    assert out.usage == Usage(10, 5) and len(llm.calls) == 1


def test_the_person_s_area_is_the_box_when_the_answer_says_area():
    template, docs, people = setup("flattened", "flattened", shifts=[0.0, SHIFT])
    x0, y0, x1, y1 = FIELD_RECTS["dob"]
    area = ChatArea(sample=1, page=0, rect=(x0, y0 + SHIFT, x1, y1 + SHIFT))  # where it is on the shifted copy
    llm = Scripted(answer("Moved the box to your area.", {"op": "set_box", "path": "investor.dob", "area": True}))
    out = propose_fix(template, docs, "the date of birth is here", llm, area=area, vision=False)
    (box,) = out.edits
    assert box.sample is None and box.page == 0
    assert box.rect == pytest.approx(FIELD_RECTS["dob"], abs=0.5)  # the layout's place
    assert set(out.preview) == {"investor.dob"}


def test_an_edit_the_template_refuses_is_asked_again_with_what_was_wrong():
    template, docs, people = setup("flattened", "flattened")
    bad = {"op": "set_box", "path": "investor.dob", "line": "t9_999", "sample": 1}
    good = {"op": "set_box", "path": "investor.dob", "line": line_id(docs[0], people[0].dob), "sample": 1}
    llm = Scripted(answer("Moved it.", bad), answer("Moved it.", good))
    out = propose_fix(template, docs, "fix the date of birth", llm, vision=False)
    assert len(llm.calls) == 2
    retry = llm.calls[1][-1].content
    assert "t9_999" in retry and "sample 1" in retry
    assert out.edits[0].rect[1] == pytest.approx(FIELD_RECTS["dob"][1] + 3, abs=3)


def test_a_line_on_a_fillable_page_is_refused_then_asked_again():
    template, docs, people = setup("fillable", "flattened")
    on_fillable = {"op": "set_box", "path": "investor.dob", "line": "t0_6", "sample": 1}  # sample 1 has no text lines
    on_flattened = {**on_fillable, "line": line_id(docs[1], people[1].dob), "sample": 2}
    llm = Scripted(answer("Moved it.", on_fillable), answer("Moved it.", on_flattened))
    out = propose_fix(template, docs, "fix the date of birth", llm, vision=False)
    assert len(llm.calls) == 2
    assert "area" in llm.calls[1][-1].content  # told how a box on a page with form fields is given
    assert out.edits[0].page == 0 and out.edits[0].sample is None


def test_many_edits_are_proven_together_and_the_preview_has_every_path():
    template, docs, people = setup("flattened", "flattened")
    edits = [
        *tax_copy(docs, people),
        {"op": "set_field", "path": "investor.dob", "title": "Born"},
        {"op": "set_labels", "path": "investor.legal_name", "labels": ["Investor legal name"], "side": "right"},
        {"op": "mark_absent", "path": "wire.bank"},
        {"op": "rename_field", "path": "address.street", "name": "street_line"},
    ]
    llm = Scripted(answer("Done.", *edits))
    out = propose_fix(template, docs, "several things", llm, vision=False)
    assert len(out.edits) == len(edits)
    assert set(out.preview) == {
        "investor.tin_copy", "investor.dob", "investor.legal_name", "wire.bank", "address.street_line",
    }  # fmt: skip
    assert out.preview["investor.tin_copy"].ready and out.preview["investor.dob"].ready
    assert out.preview["wire.bank"].absent and not out.preview["wire.bank"].ready


def test_more_than_three_turns_is_refused_before_any_model_call():
    template, docs, _ = setup("flattened")
    turn = ChatTurn(message="m", reply="r", changes="c", outcome="applied")
    llm = Scripted()
    with pytest.raises(FormcastError, match="3"):
        propose_fix(template, docs, "again", llm, history=[turn] * (MAX_HISTORY + 1), vision=False)
    with pytest.raises(FormcastError, match="message"):
        propose_fix(template, docs, "   ", llm, vision=False)
    with pytest.raises(FormcastError, match="sample"):
        propose_fix(template, docs, "here", llm, area=ChatArea(sample=1, page=0, rect=(0, 0, 9, 9)), vision=False)
    with pytest.raises(FormcastError, match="page"):
        propose_fix(template, docs, "here", llm, area=ChatArea(sample=0, page=7, rect=(0, 0, 9, 9)), vision=False)
    assert llm.calls == []


@pytest.mark.parametrize("key", ["message", "reply", "changes"])
def test_a_turn_holds_at_most_2000_characters_a_part(key):
    turn = {"message": "m", "reply": "r", "changes": "c", "outcome": "applied"}
    assert ChatTurn.model_validate({**turn, key: "x" * 2000})
    with pytest.raises(ValidationError, match="2000"):
        ChatTurn.model_validate({**turn, key: "x" * 2001})


def test_an_answer_unusable_twice_raises():
    template, docs, _ = setup("flattened")
    by_rect = {"op": "set_box", "path": "investor.dob", "page": 0, "rect": [240, 166, 400, 186]}
    llm = Scripted('{"edits": "none"}', answer("Moved it.", by_rect))
    with pytest.raises(FormcastError, match="could not be used: edit 1: .* never by a rect"):
        propose_fix(template, docs, "fix it", llm, vision=False)
    assert '"reply"' in llm.calls[1][-1].content  # the first time, told the answer's shape


def test_the_template_lines_stay_within_their_budget_ready_lines_cut_first():
    template, docs, people = setup("flattened", "flattened")
    wrong = template.samples[0].model_copy(update={"opinion": {**template.samples[0].opinion, "investor.tax_id": "1"}})
    readers = {p: r for p, r in template.readers.items() if p not in ("owners[2].name", "owners[3].name")}
    template = template.model_copy(update={"samples": [wrong, template.samples[1]], "readers": readers})
    proof = prove(template, [d.pdf for d in docs])
    full = _template_lines(template, proof)
    tax = next(line for line in full if line.startswith("investor.tax_id "))
    assert "NOT READY" in tax and "disagree" in tax and '"806-63-5608"' in tax
    assert any(line.startswith("owners[2..3].name: no reader") for line in full)  # rows with no reader share a line
    budget = len(tax) + 400
    cut = _template_lines(template, proof, budget=budget)
    assert sum(len(line) + 1 for line in cut) <= budget
    assert tax in cut  # the path that is not ready is kept
    assert len(cut) < len(full) and any("left out" in line for line in cut)


def test_the_message_sends_area_text_lines_and_earlier_turns():
    template, docs, people = setup("flattened", "flattened")
    x0, y0, x1, y1 = FIELD_RECTS["dob"]
    area = ChatArea(sample=0, page=0, rect=(x0, y0, x1, y1))
    history = [
        ChatTurn(message="first ask", reply="first reply", changes="set the box of investor.dob", outcome="discarded"),
        ChatTurn(message="second ask", reply="second reply", changes="nothing", outcome="pending"),
    ]
    llm = Scripted(answer("Nothing to change: it reads the date already.",))
    out = propose_fix(template, docs, "is the date right?", llm, area=area, history=history)
    assert out.edits == [] and out.preview == {}
    text = sent(llm)
    area_block = text.split("AREA:", 1)[1].split("EARLIER:", 1)[0]
    assert "sample 1, page 0" in area_block and line_id(docs[0], people[0].dob) in area_block
    assert line_id(docs[0], people[0].tax_id) not in area_block
    earlier = text.split("EARLIER:", 1)[1].split("MESSAGE:", 1)[0]
    assert earlier.index("first ask") < earlier.index("second ask") and "discarded" in earlier
    assert text.rstrip().endswith("is the date right?")
    assert text.index("SAMPLE 1") < text.index("SAMPLES: 2\n") < text.index("TEMPLATE:") < text.index("AREA:")
    images = [p for p in llm.calls[0][1].content if isinstance(p, ImagePart)]
    assert 1 <= len(images) <= 2
    assert sent(llm).startswith("Sample 1, page 0:")  # the area's page first


def test_the_setup_template_is_not_changed():
    template, docs, people = setup("flattened", "flattened")
    before = template.model_copy(deep=True)
    llm = Scripted(answer("Added it.", *tax_copy(docs, people), {"op": "remove_field", "path": "wire.bank"}))
    propose_fix(template, docs, "add it", llm, vision=False)
    assert template == before
