"""The page pass: one vision call per page, whose answers are applied to the template as edit ops."""

from __future__ import annotations

import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from formcast.llm import LLMConfig, Message, Provider, Usage, make_provider
from formcast.pagemap.model import FormMap
from formcast.pagepass.answers import TableSpec, apply_ops, mapping_ops, parse_answer, table_ops
from formcast.pagepass.marks import marks_for, render_marked_page
from formcast.pagepass.prompts import page_messages
from formcast.schema import flatten_schema
from formcast.template.model import Field, Template
from formcast.template.ops import op_context

PdfInput = str | Path | bytes

# A dense page's reply can run past the agent's default output limit and 120 s.
PAGE_PASS_MAX_OUTPUT_TOKENS = 32000
PAGE_PASS_TIMEOUT_S = 300


@dataclass
class PageOutcome:
    """What happened on one page: the call's result and how many of its ops were applied or rejected."""

    page: int
    status: Literal["ok", "error", "unparsed", "skipped"]
    applied: int = 0
    rejected: list[dict] = field(default_factory=list)  # [{"op": op, "reason": str}]
    usage: Usage = field(default_factory=Usage)
    seconds: float = 0.0
    text: str = ""  # the model's raw answer
    error: str | None = None


@dataclass
class PagePassResult:
    template: Template
    pages: list[PageOutcome]
    usage: Usage


def _boxes(template: Template, page: int) -> list[Field]:
    return [f for f in template.fields if f.page == page and f.rect is not None]


def _call_limits(provider: Provider) -> dict[str, int]:
    """The page pass's output-token and timeout limits, as the keywords ``provider.complete`` accepts.

    A provider written against the two-argument ``complete(messages, tools)`` gets neither.
    """
    limits = {"max_output_tokens": PAGE_PASS_MAX_OUTPUT_TOKENS, "timeout_s": PAGE_PASS_TIMEOUT_S}
    try:
        params = inspect.signature(provider.complete).parameters.values()
    except (TypeError, ValueError):  # no signature to read: call it the plain way
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return limits
    by_name = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    named = {p.name for p in params if p.kind in by_name}
    return {k: v for k, v in limits.items() if k in named}


def _page_request(template: Template, form_map: FormMap, pdf: PdfInput, page: int) -> list[Message]:
    """Render one page's marked image and build its messages."""
    boxes = _boxes(template, page)
    marks = marks_for(boxes)
    image = render_marked_page(pdf, page, boxes)
    return page_messages(template, form_map, page, image, marks)


def _error(page: int, e: Exception, seconds: float) -> PageOutcome:
    return PageOutcome(page, "error", seconds=seconds, error=str(e) or type(e).__name__)


def _call_page(provider: Provider, page: int, messages: list[Message], limits: dict[str, int]) -> PageOutcome:
    """Ask for one page; an exception of any kind becomes an ``error`` outcome."""
    start = time.monotonic()
    try:
        response = provider.complete(messages, [], **limits)
    except Exception as e:  # noqa: BLE001 - a failed page keeps its boxes unmapped; the pass goes on
        return _error(page, e, time.monotonic() - start)
    return PageOutcome(
        page, "ok", usage=response.usage, seconds=time.monotonic() - start, text=response.text
    )


def _table_op_page(template: Template, op: dict, previous: int) -> int:
    """The page a table op counts against: the first segment's page, or the page of the box being cleared.

    A ``set_required`` always follows its ``add_table`` and counts against the same page (``previous``).
    """
    if op["op"] == "add_table":
        return op["segments"][0]["page"]
    if op["op"] == "set_required":
        return previous
    return next(f.page for f in template.fields if f.id == op["id"])


def map_pages(
    template: Template,
    form_map: FormMap,
    pdf: PdfInput,
    llm: LLMConfig | Provider,
    *,
    pages: list[int] | None = None,
    max_parallel: int = 4,
) -> PagePassResult:
    """Map the template's seeded boxes page by page with a vision model.

    One call per page that has boxes (``pages`` limits which pages are considered); pages
    without boxes are ``skipped``. The marked page images are rendered first, one after another
    (under :data:`formcast.pagemap.extract.PDF_LOCK`); then the calls run in parallel, up to
    ``max_parallel``, each with a 32,000-token output limit and a 300 s timeout when the provider's
    ``complete`` takes those keywords (a two-argument ``complete(messages, tools)`` is called
    without them). A page that cannot be rendered is an ``error``. Answers are
    applied afterwards in page order, so the result does not depend on which call finished first:
    every page's plain mappings, then the tables of all pages (those continuing across pages
    merged into one). A failed or unparsable page keeps its boxes unmapped; a rejected op is
    recorded in its page's outcome, never raised. The boxes an answer lists as required are marked with
    ``set_required`` (a table when one of its boxes is listed). Table ops count against the page of their first
    segment, and a box-clearing op against the box's page. ``llm`` is an :class:`LLMConfig` (the
    pass creates and closes the provider) or a ready :class:`Provider` (the caller keeps ownership).
    """
    in_scope = sorted({p.index for p in form_map.pages} if pages is None else set(pages))
    outcomes = {p: PageOutcome(p, "skipped") for p in in_scope}
    todo = [p for p in in_scope if _boxes(template, p)]
    owns_provider = isinstance(llm, LLMConfig)
    provider: Provider = make_provider(llm) if owns_provider else llm  # type: ignore[arg-type,assignment]
    try:
        # Render every page first, one at a time (MuPDF is single-threaded); only the calls run in parallel.
        requests: dict[int, tuple[list[Message], float]] = {}
        for p in todo:
            start = time.monotonic()
            try:
                requests[p] = (_page_request(template, form_map, pdf, p), time.monotonic() - start)
            except Exception as e:  # noqa: BLE001 - a page that cannot be rendered fails alone
                outcomes[p] = _error(p, e, time.monotonic() - start)
        limits = _call_limits(provider)
        with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
            futures = {p: pool.submit(_call_page, provider, p, msgs, limits) for p, (msgs, _) in requests.items()}
            for p, f in futures.items():
                outcomes[p] = f.result()
                outcomes[p].seconds += requests[p][1]
    finally:
        if owns_provider:
            provider.close()

    context = op_context(form_map, flatten_schema(template.schema_))
    specs: list[TableSpec] = []
    for page in todo:
        outcome = outcomes[page]
        if outcome.status != "ok":
            continue
        answer = parse_answer(outcome.text)
        if answer is None:
            outcome.status = "unparsed"
            continue
        ops, page_specs = mapping_ops(template, answer, page)
        template, outcome.applied, outcome.rejected = apply_ops(template, ops, page, context)
        specs.extend(page_specs)

    # Tables last: they read the sources the mappings set to decide which boxes to clear.
    page, table_added = 0, False
    for op in table_ops(template, specs):
        if op["op"] == "set_required" and not table_added:
            continue  # its table was rejected: that rejection is already recorded
        page = _table_op_page(template, op, page)
        template, applied, rejected = apply_ops(template, [op], None, context)
        if op["op"] == "add_table":
            table_added = applied == 1
        outcomes[page].applied += applied
        outcomes[page].rejected.extend(rejected)

    usage = Usage(
        sum(o.usage.input_tokens for o in outcomes.values()), sum(o.usage.output_tokens for o in outcomes.values())
    )
    return PagePassResult(template, [outcomes[p] for p in in_scope], usage)
