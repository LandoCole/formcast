"""The AI pass: a model reads, in a finished run's documents, the required fields that failed.

    ai_pass(templates, out_dir, llm) -> AiPassSummary

Off unless called: ``run_batch`` and ``extract`` never call a model (a model is called only while a template is built
from samples, and here). For each document of the run that was read (no ``error``) with a *required* field
(:func:`~formcast.extract.levels.required_paths`) still ``missing`` or ``failed_check`` once the review decisions are
applied, the pass opens the document, shows it to the model as :func:`~formcast.extract.ask.read_values` does a
sample, and asks only for those fields: a document whose required fields were read, a document that was set aside,
and a field that is not required are never sent. Every other field of the document keeps what the template read.

The answers go to ``out_dir/ai.jsonl`` (:class:`~formcast.extract.batch.AiAnswer`), one line per field asked,
appended and flushed as each document is done; an answer of ``None`` is written too, so a later pass skips the
field. The run's own files are never changed. :class:`~formcast.extract.batch.Results` puts each answer on its field
as ``unsure`` by strategy ``"ai"`` with the reason :data:`~formcast.extract.batch.AI_REASON`: the model's value is
never ``found`` and counts as a problem until a person accepts, corrects or removes it (``Results.decide``), and a
review decision always wins over it. A second pass over the same run asks nothing it already asked.

Cost: one model call per document asked about (a long form is sent as text, and as page images when ``vision`` is
on), so the cost is the number of documents with a failing required field, not the size of the run. Documents are
asked side by side, ``max_parallel`` at a time, sharing one provider (made from an ``LLMConfig`` and closed at the
end; a ready :class:`~formcast.llm.Provider` is the caller's to close); this thread alone writes the file. A model
failure on one document (an answer that could not be used twice, a file that can no longer be read) is counted in
the summary and the pass goes on; that document is asked again by the next pass.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from formcast.errors import FormcastError
from formcast.extract.ask import Llm, read_values
from formcast.extract.batch import AI, AI_REASON, AiAnswer, Results, _cut, _whole_lines
from formcast.extract.levels import required_paths
from formcast.extract.model import ExtractionTemplate
from formcast.extract.samples import SamplePdf, read_samples
from formcast.extract.setup import _shared
from formcast.llm import Provider, Usage

__all__ = ["AI", "AI_REASON", "AiPassSummary", "ai_pass", "required_paths"]

#: A field the pass can ask about: the statuses of a read that found no usable value.
_FAILED = ("missing", "failed_check")


@dataclass(frozen=True)
class AiPassSummary:
    """What a pass did: the documents asked about, how many of them got at least one value, how many the model
    could not be used for (asked again by the next pass), and the tokens spent."""

    documents: int
    answered: int
    usage: Usage
    failed: int = 0


@dataclass(frozen=True)
class _Ask:
    doc_id: str
    file: str
    template: ExtractionTemplate
    paths: list[str]


def ai_pass(
    templates: list[ExtractionTemplate],
    out_dir: str | Path,
    llm: Llm,
    *,
    max_parallel: int = 4,
    vision: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> AiPassSummary:
    """Ask the model for the failing required fields of the run in ``out_dir`` (see the module).

    ``templates`` are the ones the run read with (the templates of other documents are not asked about);
    ``on_progress(done, total)`` is called from this thread after each document. Documents whose answers could not
    be had are counted in ``failed``.

    Raises:
        FormcastError: ``out_dir`` is not a run, or a template is not the run's or differs from it.
    """
    results = Results.load(out_dir)
    results.check_templates(templates)
    asks = _asks(results, templates)
    if not asks:
        return AiPassSummary(documents=0, answered=0, usage=Usage())
    ai_file = results.out_dir / AI
    _cut(ai_file, _whole_lines(ai_file, lambda offset, length, record: None))  # a torn last line goes before more
    answered = failed = done = 0
    usage = Usage()
    with _shared(llm) as provider, ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(asks)))) as pool:
        futures = {pool.submit(_read_one, ask, provider, vision): ask for ask in asks}
        with ai_file.open("a", encoding="utf-8") as sink:
            try:
                for future in as_completed(futures):
                    ask = futures[future]
                    try:
                        values, used = future.result()
                    except (FormcastError, OSError):  # the model's answer unusable twice, or the file gone
                        failed += 1
                    else:
                        usage = Usage(usage.input_tokens + used.input_tokens, usage.output_tokens + used.output_tokens)
                        answers = [AiAnswer(doc_id=ask.doc_id, path=p, value=_answer(values.get(p))) for p in ask.paths]
                        for answer in answers:
                            sink.write(answer.model_dump_json() + "\n")
                        sink.flush()
                        answered += any(a.value is not None for a in answers)
                    done += 1
                    if on_progress is not None:
                        on_progress(done, len(asks))
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    return AiPassSummary(documents=len(asks), answered=answered, usage=usage, failed=failed)


def _asks(results: Results, templates: list[ExtractionTemplate]) -> list[_Ask]:
    """The documents to ask about, in ``doc_id`` order, each with its failing required paths not asked before."""
    by_id = {t.template_id: (t, required_paths(t.schema_)) for t in templates}
    asks = []
    for doc in results.documents():
        if doc.error is not None or doc.template_id not in by_id:
            continue
        template, required = by_id[doc.template_id]
        asked = results.ai_answers(doc.doc_id)  # a path asked before is not asked again, whatever the answer
        paths = [
            f.path
            for f in results.effective(doc).fields
            if f.path in required and f.status in _FAILED and f.path not in asked
        ]
        if paths:
            asks.append(_Ask(doc.doc_id, doc.file, template, paths))
    return asks


def _read_one(ask: _Ask, provider: Provider, vision: bool) -> tuple[dict[str, Any], Usage]:
    """The model's reading of one document at its failing paths."""
    file = Path(ask.file)
    doc = read_samples([SamplePdf(file.name, file.read_bytes())], detect="off")[0]
    return read_values(ask.template.schema_, ask.paths, doc, 1, provider, vision=vision)


def _answer(value: Any) -> Any:
    """``value`` as the model gave it, but ``None`` for an answer of only white space."""
    return None if isinstance(value, str) and not value.strip() else value
