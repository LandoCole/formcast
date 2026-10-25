"""Entry points: ``build_template`` and ``revise``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from formcast.agent.session import Session, SessionBudget, SessionResult, StepEvent
from formcast.errors import SessionCancelled
from formcast.geometry import Rect
from formcast.llm import LLMConfig, Provider, Usage, make_provider
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import FormMap
from formcast.pagepass import PagePassResult, map_pages
from formcast.pagepass.marks import render_marked_page
from formcast.schema import flatten_schema
from formcast.template.coverage import coverage
from formcast.template.model import Template, form_info_for, new_template

PdfInput = str | Path | bytes


def _page_images(pdf: PdfInput, template: Template, form_map: FormMap) -> list[tuple[int, bytes]]:
    """A marked image of each page of the PDF that has boxed fields, with those fields drawn on it.

    Fields on a page the PDF does not have (a template from a longer revision) get no image.
    """
    boxed = [f for f in template.fields if f.page is not None and f.rect is not None]
    pages = sorted({f.page for f in boxed} & set(range(len(form_map.pages))))
    return [(n, render_marked_page(pdf, n, [f for f in boxed if f.page == n])) for n in pages]


def _page_pass_event(pp: PagePassResult, form_map: FormMap, schema: dict, *, finished: bool) -> StepEvent:
    """The one progress event after the page pass (step 0): its ops, the coverage it left and its tokens."""
    cov = coverage(pp.template, form_map, flatten_schema(schema))
    return StepEvent(
        step=0,
        ops_applied=sum(o.applied for o in pp.pages),
        ops_rejected=sum(len(o.rejected) for o in pp.pages),
        unmapped_paths=len(cov.unmapped_paths),
        unfilled_blanks=len(cov.unused_candidates),
        input_tokens=pp.usage.input_tokens,
        output_tokens=pp.usage.output_tokens,
        finished=finished,
        summary="page pass",
    )


def build_template(
    schema: dict,
    pdf: PdfInput,
    llm: LLMConfig | Provider,
    *,
    start_from: Template | None = None,
    form_map: FormMap | None = None,
    budget: SessionBudget = SessionBudget(),
    seed_candidates: bool = True,
    on_step: Callable[[StepEvent], None] | None = None,
    vision: bool = True,
    agent: bool = True,
) -> SessionResult:
    """Run an agent session that builds a template for ``pdf`` and ``schema``.

    ``form_map`` skips building the page map (pass one you already built).
    With ``start_from`` the agent begins from that template's fields and history
    (for example to adapt it to a new revision of the form); the result embeds
    ``schema`` and this PDF's form info (fingerprint, page sizes, kinds).
    Otherwise the template starts with one field per widget and, with
    ``seed_candidates``, one per detector candidate (ids as in the form map).
    ``on_step`` is called with a :class:`StepEvent` after every model reply; raising
    :class:`formcast.errors.SessionCancelled` from it stops the session cleanly with
    ``finished=False`` and the template so far.

    With ``vision`` (the default) the agent also sees a marked image of each page, and a
    fresh template is first mapped by a page pass (:func:`formcast.map_pages`, one vision
    call per page) that the agent then checks and completes. ``on_step`` is not called during
    the pass; once it is done, ``on_step`` gets one event for it, ``step=0`` and summary
    ``"page pass"`` (``finished`` only with ``agent=False``), and raising
    :class:`formcast.errors.SessionCancelled` from it stops the run there (``stop_reason``
    ``"cancelled"``, the page pass's template). When no page's call succeeded, the agent starts
    as from a fresh template, without the page-pass note. The result's ``page_pass`` holds the
    pass and its ``usage`` includes the pass's tokens (``budget``'s token caps count the agent's
    alone). With ``agent=False`` the run stops after the page pass (summary ``"page pass only"``,
    no steps). ``vision=False`` runs the agent alone, on text.
    """
    if not agent and (not vision or start_from is not None):
        raise ValueError("agent=False needs a page pass: vision=True and no start_from")
    fm = form_map if form_map is not None else build_form_map(pdf)
    start = None
    if start_from is not None:
        start = start_from.model_copy(deep=True)
        start.schema_ = schema
        start.form = form_info_for(fm)
    if not vision:
        return Session(
            fm, schema, llm, template=start, budget=budget, seed_candidates=seed_candidates,
            on_step=on_step,
        ).run()
    owns_provider = isinstance(llm, LLMConfig)
    provider: Provider = make_provider(llm) if owns_provider else llm  # type: ignore[arg-type,assignment]
    try:
        if start is not None:  # already mapped: no page pass
            return Session(
                fm, schema, provider, template=start, budget=budget, on_step=on_step,
                page_images=_page_images(pdf, start, fm),
            ).run()
        pp = map_pages(new_template(schema, fm, candidates=seed_candidates), fm, pdf, provider)
        if on_step is not None:
            try:
                on_step(_page_pass_event(pp, fm, schema, finished=not agent))
            except SessionCancelled:
                return SessionResult(
                    template=pp.template, finished=False, summary="cancelled", steps=[], usage=pp.usage,
                    stop_reason="cancelled", page_pass=pp,
                )  # fmt: skip
        if not agent:
            return SessionResult(
                template=pp.template, finished=True, summary="page pass only", steps=[], usage=pp.usage,
                stop_reason="finish", page_pass=pp,
            )  # fmt: skip
        images = _page_images(pdf, pp.template, fm)
        if any(o.status == "ok" for o in pp.pages):
            session = Session(
                fm, schema, provider, template=pp.template, budget=budget, on_step=on_step, page_images=images,
                after_page_pass=True,
            )  # fmt: skip
        else:  # no page answered: the template is the fresh one, so the agent starts as if no pass ran
            session = Session(
                fm, schema, provider, budget=budget, seed_candidates=seed_candidates, on_step=on_step,
                page_images=images,
            )  # fmt: skip
        result = session.run()
    finally:
        if owns_provider:
            provider.close()
    result.page_pass = pp
    result.usage = Usage(
        pp.usage.input_tokens + result.usage.input_tokens, pp.usage.output_tokens + result.usage.output_tokens
    )
    return result


def revise(
    template: Template,
    pdf: PdfInput,
    instruction: str,
    llm: LLMConfig | Provider,
    *,
    region: tuple[int, Rect] | None = None,
    budget: SessionBudget = SessionBudget(max_steps=15),
    form_map: FormMap | None = None,
    on_step: Callable[[StepEvent], None] | None = None,
    vision: bool = True,
) -> SessionResult:
    """Apply a reviewer's ``instruction`` to ``template`` with the same agent loop.

    ``region`` is (page, rect) from the reviewer's highlight. New edits are
    appended to the template's history with author ``"agent"``. ``on_step`` as for
    :func:`build_template`. With ``vision`` the agent also sees a marked image of
    each page that has boxed fields.
    """
    fm = form_map if form_map is not None else build_form_map(pdf)
    return Session(
        fm, template.schema_, llm, template=template, instruction=instruction, region=region, budget=budget,
        on_step=on_step, page_images=_page_images(pdf, template, fm) if vision else None,
    ).run()
