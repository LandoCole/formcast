"""Entry points: ``build_template`` and ``revise``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from formcast.agent.session import Session, SessionBudget, SessionResult, StepEvent
from formcast.geometry import Rect
from formcast.llm import LLMConfig, Provider, Usage, make_provider
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import FormMap
from formcast.pagepass import map_pages
from formcast.pagepass.marks import render_marked_page
from formcast.template.model import Template, form_info_for, new_template

PdfInput = str | Path | bytes


def _page_images(pdf: PdfInput, template: Template) -> list[tuple[int, bytes]]:
    """A marked image of each page that has boxed fields, with those fields drawn on it."""
    boxed = [f for f in template.fields if f.page is not None and f.rect is not None]
    pages = sorted({f.page for f in boxed})
    return [(n, render_marked_page(pdf, n, [f for f in boxed if f.page == n])) for n in pages]


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
    call per page; ``on_step`` is not called during it) that the agent then checks and
    completes. The result's ``page_pass`` holds the pass and its ``usage`` includes the
    pass's tokens. With ``agent=False`` the run stops after the page pass (summary
    ``"page pass only"``, no steps). ``vision=False`` runs the agent alone, on text.
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
                page_images=_page_images(pdf, start),
            ).run()
        pp = map_pages(new_template(schema, fm, candidates=seed_candidates), fm, pdf, provider)
        if not agent:
            return SessionResult(
                template=pp.template, finished=True, summary="page pass only", steps=[], usage=pp.usage,
                stop_reason="finish", page_pass=pp,
            )  # fmt: skip
        result = Session(
            fm, schema, provider, template=pp.template, budget=budget, on_step=on_step,
            page_images=_page_images(pdf, pp.template), after_page_pass=True,
        ).run()
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
        on_step=on_step, page_images=_page_images(pdf, template) if vision else None,
    ).run()
