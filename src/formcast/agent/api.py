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


# A long document (a subscription package: dozens of pages of agreement with questionnaires between)
# would resend an image of every page with boxes at every agent step. Past this many pages the agent
# checking a page pass sees the pages the pass left most boxes unlinked on, and page-map text only
# for pages with boxes. Shorter documents are unchanged.
LONG_DOCUMENT_PAGES = 12
MAX_AGENT_IMAGE_PAGES = 12


def _boxed_pages(template: Template, form_map: FormMap) -> set[int]:
    """The pages with a boxed field or a table segment on them."""
    pages = {f.page for f in template.fields if f.page is not None and f.rect is not None}
    pages |= {seg.page for f in template.fields for seg in (getattr(f, "segments", None) or [])}
    return pages & set(range(len(form_map.pages)))


def _focus(template: Template, form_map: FormMap) -> set[int] | None:
    """For a long document, the pages whose images the agent gets after a page pass; None for every boxed page.

    The ``MAX_AGENT_IMAGE_PAGES`` pages with the most unlinked boxes (ties: more boxes, then earlier).
    """
    boxed = _boxed_pages(template, form_map)
    if len(form_map.pages) <= LONG_DOCUMENT_PAGES or len(boxed) <= MAX_AGENT_IMAGE_PAGES:
        return None
    fields = [f for f in template.fields if f.page in boxed and f.rect is not None]

    def rank(n: int) -> tuple[int, int, int]:
        on = [f for f in fields if f.page == n]
        unlinked = sum(1 for f in on if not (f.source and f.source.paths))
        return (-unlinked, -len(on), n)

    return set(sorted(boxed, key=rank)[:MAX_AGENT_IMAGE_PAGES])


def _page_images(
    pdf: PdfInput, template: Template, form_map: FormMap, *, pages: set[int] | None = None
) -> list[tuple[int, bytes]]:
    """A marked image of each page of the PDF that has boxed fields (of ``pages`` only, when given), with
    those fields drawn on it.

    Fields on a page the PDF does not have (a template from a longer revision) get no image.
    """
    boxed = [f for f in template.fields if f.page is not None and f.rect is not None]
    want = {f.page for f in boxed} & set(range(len(form_map.pages)))
    if pages is not None:
        want &= pages
    return [(n, render_marked_page(pdf, n, [f for f in boxed if f.page == n])) for n in sorted(want)]


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
        focus = _focus(pp.template, fm)
        images = _page_images(pdf, pp.template, fm, pages=focus)
        if any(o.status == "ok" for o in pp.pages):
            session = Session(
                fm, schema, provider, template=pp.template, budget=budget, on_step=on_step, page_images=images,
                after_page_pass=True, focus_pages=_boxed_pages(pp.template, fm) if focus is not None else None,
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
