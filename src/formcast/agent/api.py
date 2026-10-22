"""Entry points: ``build_template`` and ``revise``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from formcast.agent.session import Session, SessionBudget, SessionResult, StepEvent
from formcast.geometry import Rect
from formcast.llm import LLMConfig, Provider
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import FormMap
from formcast.template.model import Template, form_info_for

PdfInput = str | Path | bytes


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
    """
    fm = form_map if form_map is not None else build_form_map(pdf)
    start = None
    if start_from is not None:
        start = start_from.model_copy(deep=True)
        start.schema_ = schema
        start.form = form_info_for(fm)
    return Session(
        fm, schema, llm, template=start, budget=budget, seed_candidates=seed_candidates,
        on_step=on_step,
    ).run()


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
) -> SessionResult:
    """Apply a reviewer's ``instruction`` to ``template`` with the same agent loop.

    ``region`` is (page, rect) from the reviewer's highlight. New edits are
    appended to the template's history with author ``"agent"``. ``on_step`` as for
    :func:`build_template`.
    """
    fm = form_map if form_map is not None else build_form_map(pdf)
    return Session(
        fm, template.schema_, llm, template=template, instruction=instruction, region=region, budget=budget,
        on_step=on_step,
    ).run()
