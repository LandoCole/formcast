from __future__ import annotations

import json
import re
import threading
import time

import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.llm import ImagePart, LLMConfig, LLMResponse, Usage
from formcast.pagemap.model import FormMap, PageMap
from formcast.pagepass import PageOutcome, PagePassResult, map_pages
from formcast.pagepass import run as run_mod
from formcast.template.model import Field, FormInfo, Template

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "rows": {
            "type": "array",
            "items": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}},
        },
    },
}


def _page(index: int) -> PageMap:
    return PageMap(
        index=index, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
        words=[], lines=[], rules=[], widgets=[], candidates=[],
    )  # fmt: skip


def _f(id, rect, page=0):
    return Field(id=id, type="text", page=page, rect=rect, origin="detector")


def _form(pages: int = 2) -> FormMap:
    return FormMap(pages=[_page(i) for i in range(pages)])


def _template(pages: int = 2, with_page1_boxes: bool = False) -> Template:
    fields = [
        _f("c0_1", (100, 100, 300, 120)),
        _f("c0_2", (100, 200, 200, 220)),
        _f("c0_3", (210, 200, 310, 220)),
    ]
    if with_page1_boxes:
        fields += [_f("c1_1", (100, 100, 200, 120), 1), _f("c1_2", (210, 100, 300, 120), 1)]
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=pages, page_sizes=[(612, 792)] * pages, kinds=["digital"] * pages),
        fields=fields,
    )


@pytest.fixture
def pdf(tmp_path):
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=612, height=792)
    path = tmp_path / "two_pages.pdf"
    doc.save(path)
    doc.close()
    return path


def _reply(page: int) -> str:
    return json.dumps({"mappings": [{"id": f"c{page}_1", "source": {"paths": ["name"]}}], "tables": []})


class FakeProvider:
    """Answers each call from ``replies[page]`` (text, or an Exception to raise) and records the calls."""

    def __init__(self, replies, delays=None, usage=Usage(10, 5)):
        self.replies = replies
        self.delays = delays or {}
        self.usage = usage
        self.calls: list[dict] = []
        self.closed = False
        self._lock = threading.Lock()

    def complete(self, messages, tools, *, max_output_tokens=None, timeout_s=None):
        text = "".join(getattr(p, "text", "") for p in messages[-1].content)
        page = int(re.search(r"page (\d+)\):", text).group(1))
        with self._lock:
            self.calls.append(
                {"page": page, "messages": messages, "tools": tools, "max": max_output_tokens, "timeout": timeout_s}
            )
        time.sleep(self.delays.get(page, 0))
        reply = self.replies[page]
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, tool_calls=[], usage=self.usage, raw={})

    def close(self):
        self.closed = True


def test_one_call_per_page_with_boxes_and_skipped_pages(pdf):
    provider = FakeProvider({0: _reply(0)})
    result = map_pages(_template(), _form(), pdf, provider)
    assert isinstance(result, PagePassResult)
    assert [c["page"] for c in provider.calls] == [0]
    assert [(o.page, o.status) for o in result.pages] == [(0, "ok"), (1, "skipped")]
    skipped = result.pages[1]
    assert isinstance(skipped, PageOutcome)
    assert (skipped.applied, skipped.rejected, skipped.text, skipped.error) == (0, [], "", None)
    assert skipped.usage == Usage()
    image = provider.calls[0]["messages"][-1].content[0]
    assert isinstance(image, ImagePart) and image.data.startswith(b"\x89PNG")
    assert provider.closed is False  # a ready provider stays the caller's


def test_pages_argument_limits_the_pass(pdf):
    provider = FakeProvider({0: _reply(0), 1: _reply(1)})
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, provider, pages=[1])
    assert [c["page"] for c in provider.calls] == [1]
    assert [o.page for o in result.pages] == [1]


def test_calls_ask_for_32000_output_tokens_and_300s_timeout(pdf):
    provider = FakeProvider({0: _reply(0)})
    map_pages(_template(), _form(), pdf, provider)
    assert run_mod.PAGE_PASS_MAX_OUTPUT_TOKENS == 32000 and run_mod.PAGE_PASS_TIMEOUT_S == 300
    (call,) = provider.calls
    assert (call["max"], call["timeout"], call["tools"]) == (32000, 300, [])


def test_page_answers_are_applied_with_page_pass_note(pdf):
    provider = FakeProvider({0: _reply(0)})
    template = _template()
    result = map_pages(template, _form(), pdf, provider)
    assert result.pages[0].applied == 1 and result.pages[0].rejected == []
    assert next(f for f in result.template.fields if f.id == "c0_1").source.paths == ["name"]
    last = result.template.history[-1]
    assert (last.author, last.note) == ("agent", "page pass p0")
    assert all(f.source is None for f in template.fields)  # the input template is untouched


def test_rejected_ops_are_recorded_per_page(pdf):
    bad = json.dumps({"mappings": [{"id": "c0_1", "source": {"paths": ["nope"]}}]})
    result = map_pages(_template(), _form(), pdf, FakeProvider({0: bad}))
    outcome = result.pages[0]
    assert (outcome.status, outcome.applied) == ("ok", 0)
    assert len(outcome.rejected) == 1 and "nope" in outcome.rejected[0]["reason"]


def test_provider_error_marks_the_page_and_keeps_its_boxes_unmapped(pdf):
    provider = FakeProvider({0: FormcastError("boom"), 1: _reply(1)})
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, provider)
    first, second = result.pages
    assert first.status == "error" and "boom" in first.error and first.applied == 0
    assert second.status == "ok"
    assert all(f.source is None for f in result.template.fields if f.page == 0)
    assert next(f for f in result.template.fields if f.id == "c1_1").source.paths == ["name"]


def test_a_page_that_fails_to_render_becomes_an_error(pdf):
    template = _template(pages=3)
    template.fields.append(_f("c2_1", (10, 10, 50, 30), 2))  # the PDF has only 2 pages
    result = map_pages(template, _form(3), pdf, FakeProvider({0: _reply(0)}))
    assert [o.status for o in result.pages] == ["ok", "skipped", "error"]
    assert "out of range" in result.pages[2].error


def test_unparsable_answer_marks_the_page_unparsed(pdf):
    result = map_pages(_template(), _form(), pdf, FakeProvider({0: "I cannot do that"}))
    outcome = result.pages[0]
    assert (outcome.status, outcome.text, outcome.applied) == ("unparsed", "I cannot do that", 0)
    assert outcome.usage == Usage(10, 5)  # the call still cost tokens
    assert all(f.source is None for f in result.template.fields)


def test_usage_is_summed_over_pages(pdf):
    provider = FakeProvider({0: _reply(0), 1: _reply(1)}, usage=Usage(100, 7))
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, provider)
    assert result.usage == Usage(200, 14)
    assert [o.usage for o in result.pages] == [Usage(100, 7), Usage(100, 7)]


def _table_reply(page: int, ids: list[tuple[str, str]]) -> str:
    return json.dumps({
        "mappings": [],
        "tables": [{
            "source_path": "rows",
            "columns": [{"key": "a", "transforms": []}, {"key": "b", "transforms": []}],
            "rows": [{"a": a, "b": b} for a, b in ids],
        }],
    })


def test_tables_across_pages_merge_and_are_attributed_to_their_first_page(pdf):
    replies = {0: _table_reply(0, [("c0_2", "c0_3")]), 1: _table_reply(1, [("c1_1", "c1_2")])}
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, FakeProvider(replies))
    tables = [f for f in result.template.fields if f.type == "table"]
    assert len(tables) == 1 and [s.page for s in tables[0].segments] == [0, 1]
    assert result.template.history[-1].note == "page pass"
    # the one add_table op counts against the first page of the table
    assert [o.applied for o in result.pages] == [1, 0]
    assert all(o.rejected == [] for o in result.pages)


def test_table_rejections_are_attributed_to_the_page_of_the_table(pdf):
    # the table cell row is not inside the page: it cannot be added, and the rejection shows on page 0
    replies = {0: json.dumps({"tables": [{
        "source_path": "not_a_path",
        "columns": [{"key": "a", "transforms": []}],
        "rows": [{"a": "c0_2"}],
    }]})}
    result = map_pages(_template(), _form(), pdf, FakeProvider(replies))
    assert len(result.pages[0].rejected) == 1
    assert result.pages[0].applied == 0


def test_results_do_not_depend_on_completion_order(pdf):
    replies = {0: _table_reply(0, [("c0_2", "c0_3")]), 1: _table_reply(1, [("c1_1", "c1_2")])}
    slow_first = FakeProvider(replies, delays={0: 0.3})
    parallel = map_pages(_template(with_page1_boxes=True), _form(), pdf, slow_first, max_parallel=4)
    serial = map_pages(_template(with_page1_boxes=True), _form(), pdf, FakeProvider(replies), max_parallel=1)
    assert [o.page for o in parallel.pages] == [0, 1]
    assert [f.model_dump() for f in parallel.template.fields] == [f.model_dump() for f in serial.template.fields]
    assert [(h.op.get("op"), h.note) for h in parallel.template.history] == [
        (h.op.get("op"), h.note) for h in serial.template.history
    ]


def test_llm_config_provider_is_created_and_closed(pdf, monkeypatch):
    provider = FakeProvider({0: _reply(0)})
    made = []
    monkeypatch.setattr(run_mod, "make_provider", lambda cfg: made.append(cfg) or provider)
    cfg = LLMConfig(model="m", base_url="http://x", api_key="k")
    map_pages(_template(), _form(), pdf, cfg)
    assert made == [cfg] and provider.closed is True


def test_llm_config_provider_is_closed_when_a_page_fails(pdf, monkeypatch):
    provider = FakeProvider({0: FormcastError("boom")})
    monkeypatch.setattr(run_mod, "make_provider", lambda cfg: provider)
    cfg = LLMConfig(model="m", base_url="http://x", api_key="k")
    result = map_pages(_template(), _form(), pdf, cfg)
    assert result.pages[0].status == "error" and provider.closed is True


# --- final review: two-argument providers, serial renders ------------------------------------------


class TwoArgProvider(FakeProvider):
    """A provider written against the plain ``complete(messages, tools)``."""

    def complete(self, messages, tools):
        return super().complete(messages, tools)


class TimeoutOnlyProvider(FakeProvider):
    def complete(self, messages, tools, timeout_s=None):
        return super().complete(messages, tools, timeout_s=timeout_s)


def test_a_two_argument_provider_is_called_without_the_limits_and_its_pages_succeed(pdf):
    provider = TwoArgProvider({0: _reply(0), 1: _reply(1)})
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, provider)
    assert [o.status for o in result.pages] == ["ok", "ok"] and [o.applied for o in result.pages] == [1, 1]
    assert [(c["max"], c["timeout"]) for c in provider.calls] == [(None, None), (None, None)]


def test_only_the_limit_keywords_the_provider_names_are_passed(pdf):
    provider = TimeoutOnlyProvider({0: _reply(0)})
    result = map_pages(_template(), _form(), pdf, provider)
    assert result.pages[0].status == "ok"
    assert [(c["max"], c["timeout"]) for c in provider.calls] == [(None, 300)]


def test_pages_are_rendered_one_by_one_on_the_calling_thread_before_any_call(pdf, monkeypatch):
    events: list[tuple[str, int]] = []
    threads: set[int] = set()
    real = run_mod.render_marked_page

    def render(pdf_, page, boxes):
        threads.add(threading.get_ident())
        events.append(("render", page))
        return real(pdf_, page, boxes)

    class Recording(FakeProvider):
        def complete(self, messages, tools, **kwargs):
            out = super().complete(messages, tools, **kwargs)
            with self._lock:
                events.append(("call", self.calls[-1]["page"]))
            return out

    monkeypatch.setattr(run_mod, "render_marked_page", render)
    result = map_pages(_template(with_page1_boxes=True), _form(), pdf, Recording({0: _reply(0), 1: _reply(1)}))
    assert [o.status for o in result.pages] == ["ok", "ok"]
    assert threads == {threading.get_ident()}
    assert events[:2] == [("render", 0), ("render", 1)] and sorted(events[2:]) == [("call", 0), ("call", 1)]
