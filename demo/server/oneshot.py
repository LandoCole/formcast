"""The one-shot flow: a PDF and the person's data in, a mapped template and records of its schema out.

    run_oneshot(job, doc, spec, provider_factory)   the ``oneshot`` job's body (see jobs.py)
    map_dataset(schema, dataset, provider, saved)   data -> records of ``schema``; also used by the fill route

Stages, each reported on the job as it starts and ends (``job.stages``):

    form     read the PDF (its form map: widgets, detected boxes, text)
    schema   the model designs the form's data schema from its pages     (skipped with a schema or template)
    boxes    the page pass and, with ``check``, the agent map the boxes   (skipped with a template)
    data     read the data: a table, or free text                        (skipped without data)
    match    map the data onto the schema                                 (skipped without data)

How data becomes records (``map_dataset``), cheapest first:
  * ``direct``: every column is named by a schema path (``applicant.name``, ``refs[0].phone``), as in a
    file exported from a saved template's schema. No model call.
  * ``saved``: a data mapping saved with the template reads columns this table has. No model call.
  * ``model``: the model writes a data mapping from the column names and samples; the mapping then turns
    every row into a record here (the rows themselves are not sent).
  * ``text``: free text; the model writes the records itself.

A failure in the data stages does not lose the template: the job ends ``done`` with the template and
``result.data.error`` saying what went wrong with the data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from demo.server.datasets import Dataset
from demo.server.documents import Doc
from formcast import FormcastError, SessionCancelled, Template, build_template
from formcast.data import DataMapping, derive_schema, direct_mapping, extract_records, map_table, records_from, target_paths
from formcast.llm import Provider, Usage

log = logging.getLogger("demo.server")

MAX_RECORDS = 500  # records sent back to the browser; the fill route has no such limit
STAGES = (
    ("form", "Read the form"),
    ("schema", "Design the form's data schema"),
    ("boxes", "Map the form's boxes to the schema"),
    ("data", "Read your data"),
    ("match", "Map your data to the schema"),
)


@dataclass
class OneShotSpec:
    schema: dict | None = None  # given: no schema stage
    template: Template | None = None  # given (a saved template): no schema or boxes stage
    dataset: Dataset | None = None
    check: bool = True  # the agent checks and completes the page pass
    instructions: str = ""  # the person's own words, passed to every model call
    saved_mappings: list[DataMapping] = field(default_factory=list)


@dataclass
class DataResult:
    mode: str  # direct, saved, model or text
    records: list[dict]
    titles: list[str]
    mapping: DataMapping | None = None
    warnings: list[str] = field(default_factory=list)
    notes: str = ""
    usage: Usage = field(default_factory=Usage)

    def to_json(self, dataset: Dataset, limit: int = MAX_RECORDS) -> dict[str, Any]:
        return {
            "dataset": {"id": dataset.id, "name": dataset.name, "kind": dataset.kind,
                        "rows": len(dataset.table.rows) if dataset.table is not None else None},
            "mode": self.mode,
            "mapping": self.mapping.dump() if self.mapping is not None else None,
            "records": self.records[:limit],
            "titles": self.titles[:limit],
            "total": len(self.records),
            "warnings": self.warnings,
            "notes": self.notes,
        }  # fmt: skip


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(a.input_tokens + b.input_tokens, a.output_tokens + b.output_tokens)


def _titles(records: list[dict]) -> list[str]:
    """A short label per record: its first two text values, those under a key with "name" in it first."""
    out = []
    for i, rec in enumerate(records):
        named: list[str] = []
        other: list[str] = []

        def walk(node: Any, key: str = "") -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, str(k))
            elif isinstance(node, list):
                for v in node[:1]:
                    walk(v, key)
            elif isinstance(node, str) and node.strip() and len(node) <= 60:
                (named if "name" in key.lower() else other).append(node.strip())

        walk(rec)
        values = (named + other)[:2]
        out.append(f"{i + 1}. {' · '.join(values)}" if values else f"Record {i + 1}")
    return out


def map_dataset(
    schema: dict,
    dataset: Dataset,
    provider: Provider | None,
    saved: list[DataMapping] = (),  # type: ignore[assignment]
    instructions: str = "",
) -> DataResult:
    """Records of ``schema`` from ``dataset`` (see the module docstring). ``provider`` None: no model calls."""
    if dataset.kind == "text":
        if provider is None:
            raise FormcastError("reading free text needs AI, which is off")
        found = extract_records(schema, dataset.text, provider)
        return DataResult("text", found.records, _titles(found.records), notes=found.notes, usage=found.usage)
    table = dataset.table
    assert table is not None
    columns = table.columns
    direct = direct_mapping(schema, columns)
    if direct.rules and not direct.unmapped_columns:
        mapping, mode, usage, warnings = direct, "direct", Usage(), []
    else:
        fits = [m for m in saved if m.fits(columns)]
        exact = [m for m in fits if set(m.columns) == set(columns)]
        if exact or fits:
            mapping, mode, usage, warnings = (exact or fits)[0], "saved", Usage(), []
        elif provider is not None:
            made = map_table(schema, table, provider, instructions=instructions)
            mapping, mode, usage, warnings = made.mapping, "model", made.usage, made.warnings
        elif direct.rules:
            mapping, mode, usage = direct, "direct", Usage()
            warnings = [f"AI is off, so only the columns named by a schema path were used ({len(direct.rules)} of {len(columns)})"]
        else:
            raise FormcastError(
                "AI is off and no column is named by one of the form's schema paths; download the schema and name "
                "the columns by its paths, or turn AI on"
            )
    records = records_from(mapping, schema, table)
    return DataResult(mode, records, _titles(records), mapping, warnings, mapping.notes, usage)


# ------------------------------------------------------------------ the job


def run_oneshot(job, doc: Doc, spec: OneShotSpec, provider_factory: Callable[[], Provider] | None):
    """The ``oneshot`` job (called on the job's thread by ``Jobs``). Returns the session result of the boxes
    stage, or ``None`` when there was none (a saved template); ``job.template`` and ``job.result`` are set as it goes.
    """
    provider: Provider | None = None
    session = None
    try:
        job.stage("form", "running")
        form_map = doc.form_map()
        job.stage("form", "done", f"{doc.page_count} page{'s' if doc.page_count != 1 else ''}")
        if job.cancel_requested:
            raise SessionCancelled("cancelled")
        provider = provider_factory() if provider_factory is not None else None

        schema = spec.template.schema_ if spec.template is not None else spec.schema
        if schema is None:
            if provider is None:
                raise FormcastError("designing a schema needs AI, which is off; pick a stored schema")
            job.stage("schema", "running")
            derived = derive_schema(doc.bytes, provider, form_map=form_map, instructions=spec.instructions)
            schema = derived.schema
            job.add_usage(derived.usage)
            n = len(target_paths(schema))
            job.stage("schema", "done", f"{n} fields" + (f" · {schema['title']}" if schema.get("title") else ""))
        else:
            job.stage("schema", "skipped", "from the saved template" if spec.template is not None else "the schema you picked")
        job.set_result(schema=schema)
        if job.cancel_requested:
            raise SessionCancelled("cancelled")

        if spec.template is not None:
            job.stage("boxes", "skipped", "from the saved template")
            job.set_template(spec.template)
        else:
            if provider is None:
                raise FormcastError("mapping the boxes needs AI, which is off")
            job.stage("boxes", "running")
            session = build_template(schema, doc.bytes, provider, form_map=form_map, on_step=job._on_step, agent=spec.check)
            job.add_usage(session.usage)
            job.set_template(session.template)
            mapped = sum(1 for f in session.template.fields if f.source is not None and f.source.paths)
            if session.stop_reason != "finish":
                return session  # cancelled, out of budget or failed: Jobs reports it, the template so far kept
            job.stage("boxes", "done", f"{mapped} of {len(session.template.fields)} boxes linked")

        if spec.dataset is None:
            job.stage("data", "skipped", "no data given")
            job.stage("match", "skipped")
            return session
        ds = spec.dataset
        job.stage("data", "done", _describe(ds))
        job.stage("match", "running")
        try:
            data = map_dataset(schema, ds, provider, spec.saved_mappings, spec.instructions)
        except FormcastError as exc:
            job.stage("match", "error", str(exc))
            job.set_result(data={"dataset": {"id": ds.id, "name": ds.name, "kind": ds.kind}, "error": str(exc)})
            return session
        job.add_usage(data.usage)
        job.set_result(data=data.to_json(ds))
        how = {"direct": "columns named by schema path", "saved": "a saved mapping", "model": "AI mapping", "text": "AI read the text"}
        used = f"{len(data.mapping.rules)} fields set from {len(ds.table.columns) - len(data.mapping.unmapped_columns)} of {len(ds.table.columns)} columns · " if data.mapping and ds.table else ""
        job.stage("match", "done", f"{used}{len(data.records)} record{'s' if len(data.records) != 1 else ''} · {how[data.mode]}")
        return session
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - closing must not hide the job's own outcome
                log.warning("job %s: closing the provider failed", job.id[:8])


def _describe(ds: Dataset) -> str:
    if ds.table is None:
        return f"free text, {len(ds.text):,} characters"
    t = ds.table
    what = {"csv": "table", "xlsx": "Excel sheet", "json": "JSON records"}[t.kind]
    return f"{what}, {len(t.rows)} row{'s' if len(t.rows) != 1 else ''} × {len(t.columns)} columns"
