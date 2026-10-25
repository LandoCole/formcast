"""AI jobs: ``build``, ``adapt`` and ``revise`` agent sessions, one thread each, with progress and cancel.

    Jobs.start(kind, doc=..., schema=..., template=..., instruction=..., region=..., provider_factory=...) -> Job
    Jobs.get(job_id) -> Job            (KeyError if unknown or already forgotten)
    Jobs.cancel(job_id) -> Job

A ``Job``'s ``snapshot()`` is what ``GET /api/jobs/{id}`` returns:

    {id, kind, status, steps, template, summary, error, unplaceable, started, ended}

``status`` is ``running``, then one of ``done`` (the model finished), ``cancelled`` (stopped by a cancel
request) or ``error`` (the model call or the sandbox failed, the budget ran out, or something unexpected
happened; ``error`` says so in plain words). ``steps`` has one ``StepEvent`` dict per model reply so far.
``template`` is ``null`` while the job runs (the library reports the template only when the session ends)
and afterwards the final template, or for ``cancelled`` and ``error`` the partial one: whatever the agent
had applied, including the step that was running when it stopped. It is ``null`` only when the session
never started (e.g. the form map could not be built). ``started`` and ``ended`` are Unix seconds.

Limits and lifetime:
  * At most ``MAX_RUNNING`` (2) jobs run at once; a third ``start`` raises ``JobsBusy`` (429 ``busy``).
  * Jobs live in memory only. Running jobs are never dropped; once finished, the last ``KEEP_FINISHED`` (20)
    are kept so the browser can fetch a result after the job ends, and older ones are forgotten.
  * Cancel sets a flag; the session checks it after each model reply, and a build also after its page pass
    (step 0, reported once every page's call has returned), so a cancel takes effect when the current model
    call, or the whole page pass, returns (a call already in flight is not interrupted). A cancel that arrives while the
    final reply (the one that finishes the session) is being handled does not undo it: the job is ``done``.

Locks: a job takes the document's form map once (``Doc.form_map()``, which takes ``PYMUPDF_LOCK`` only while
it builds) and then runs the whole session WITHOUT holding ``PYMUPDF_LOCK`` or any document lock, so fills,
page images and edits on the same document stay responsive while a job runs. The library takes
``PYMUPDF_LOCK`` (its own ``PDF_LOCK``) briefly for each marked page image it renders, one page at a time,
holding no other lock.

The text of a provider's error is never shown to the client and the traceback of an unexpected one goes to
the server log only, with the configured API key removed.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Literal

from demo.server.documents import Doc
from formcast import FormcastError, SessionCancelled, StepEvent, Template, build_template, revise
from formcast.agent.session import SessionResult
from formcast.geometry import Rect
from formcast.llm import Provider

log = logging.getLogger("demo.server")

MAX_RUNNING = 2
KEEP_FINISHED = 20

Kind = Literal["build", "adapt", "revise"]
KINDS: tuple[str, ...] = ("build", "adapt", "revise")

_GENERIC_ERROR = "The job stopped because of an unexpected error. The server log has the details."
_PROVIDER_ERROR = (
    "The model request failed ({kind}), so the job stopped. Check the model endpoint, key and model name in "
    "your settings. The template so far is kept."
)
_BUDGET_ERROR = "The job used up its step, token or time budget before it finished. The template so far is kept."


class JobsBusy(Exception):
    """Two jobs are already running."""


class Job:
    """One AI run. Read it with ``snapshot()``; only the job's own thread changes it."""

    def __init__(self, kind: Kind) -> None:
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status: Literal["running", "done", "cancelled", "error"] = "running"
        self.steps: list[dict] = []
        self.template: dict | None = None
        self.summary = ""
        self.error: str | None = None
        self.unplaceable: list[dict] = []
        self.started = time.time()
        self.ended: float | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "steps": list(self.steps),
                "template": self.template,
                "summary": self.summary,
                "error": self.error,
                "unplaceable": list(self.unplaceable),
                "started": self.started,
                "ended": self.ended,
            }

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        """Ask the session to stop at its next step."""
        self._cancel.set()

    def _on_step(self, event: StepEvent) -> None:
        """Record the step; stop the session if a cancel was requested, unless this step finished it."""
        with self._lock:
            self.steps.append(dataclasses.asdict(event))
        if self.cancel_requested and not event.finished:
            raise SessionCancelled("cancelled")


class Jobs:
    def __init__(
        self,
        *,
        max_running: int = MAX_RUNNING,
        keep_finished: int = KEEP_FINISHED,
        secrets: Iterable[str] = (),
    ) -> None:
        self.max_running = max_running
        self.keep_finished = keep_finished
        self._secrets = tuple(s for s in secrets if s)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ public

    def start(
        self,
        kind: Kind,
        *,
        doc: Doc,
        schema: dict | None = None,
        template: Template | None = None,
        instruction: str | None = None,
        region: tuple[int, Rect] | None = None,
        provider_factory: Callable[[], Provider],
    ) -> Job:
        """Start a job in its own thread. Raises ``JobsBusy`` when ``max_running`` jobs are already running.

        ``build`` needs ``schema``; ``adapt`` and ``revise`` need ``template`` (``adapt`` uses its schema);
        ``revise`` also needs ``instruction`` and may carry a ``region``.
        """
        if kind == "build" and schema is None:
            raise ValueError("a build job needs a schema")
        if kind in ("adapt", "revise") and template is None:
            raise ValueError(f"an {kind} job needs a template")
        if kind == "revise" and not instruction:
            raise ValueError("a revise job needs an instruction")
        job = Job(kind)
        with self._lock:
            if sum(1 for j in self._jobs.values() if j.status == "running") >= self.max_running:
                raise JobsBusy(f"{self.max_running} AI jobs are already running; wait for one to finish or cancel it.")
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job, doc, schema, template, instruction, region, provider_factory),
            name=f"job-{job.id[:8]}",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:  # e.g. the process cannot make another thread: do not keep the slot
            self._finish(job, "error", error=_GENERIC_ERROR)
            raise
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def cancel(self, job_id: str) -> Job:
        """Ask a running job to stop at its next step. A finished job is left as it is."""
        job = self.get(job_id)
        job.request_cancel()
        return job

    # ------------------------------------------------------------ the job thread

    def _run(self, job, doc, schema, template, instruction, region, provider_factory) -> None:
        provider: Provider | None = None
        try:
            try:
                form_map = doc.form_map()  # cached; the first call may take seconds and takes PYMUPDF_LOCK itself
                if job.cancel_requested:  # cancelled while the form map was building: no model call at all
                    start = template.model_dump(by_alias=True, mode="json") if template is not None else None
                    self._finish(job, "cancelled", template=start, summary="cancelled")
                    return
                provider = provider_factory()
                if job.kind == "build":
                    result = build_template(
                        schema, doc.bytes, provider, form_map=form_map, on_step=job._on_step
                    )  # fmt: skip
                elif job.kind == "adapt":
                    result = build_template(
                        template.schema_, doc.bytes, provider, start_from=template, form_map=form_map,
                        on_step=job._on_step,
                    )  # fmt: skip
                else:
                    result = revise(
                        template, doc.bytes, instruction, provider, region=region, form_map=form_map,
                        on_step=job._on_step,
                    )  # fmt: skip
            finally:
                if provider is not None:
                    try:
                        provider.close()
                    except Exception:  # noqa: BLE001 - closing must not hide the job's own outcome
                        log.warning("job %s: closing the provider failed", job.id[:8])
            self._finish_from(job, result)
        except FormcastError as exc:  # the library's own plain message (a schema or template it cannot use)
            log.warning("job %s failed: %s", job.id[:8], self._redact(str(exc)))
            self._finish(job, "error", error=self._redact(str(exc)))
        except Exception:  # noqa: BLE001 - nothing may escape a job thread; the client gets a generic message
            log.error("job %s failed unexpectedly:\n%s", job.id[:8], self._redact(traceback.format_exc()))
            self._finish(job, "error", error=_GENERIC_ERROR)

    def _finish_from(self, job: Job, result: SessionResult) -> None:
        dumped = result.template.model_dump(by_alias=True, mode="json")
        unplaceable = list(result.unplaceable)
        if result.stop_reason == "finish":
            self._finish(job, "done", template=dumped, summary=result.summary, unplaceable=unplaceable)
        elif result.stop_reason == "cancelled":
            self._finish(job, "cancelled", template=dumped, summary=result.summary, unplaceable=unplaceable)
        elif result.stop_reason == "budget":
            self._finish(job, "error", template=dumped, error=_BUDGET_ERROR)
        else:
            detail = result.error or ""
            log.warning("job %s: the session stopped on an error: %s", job.id[:8], self._redact(detail))
            if detail.startswith("sandbox:"):  # the library's own words; no provider text in them
                message = f"The code sandbox failed ({self._redact(detail)}). The template so far is kept."
            else:
                kind = detail.split(":", 1)[0].strip() or "error"
                message = _PROVIDER_ERROR.format(kind=kind)
            self._finish(job, "error", template=dumped, error=message)

    def _finish(
        self,
        job: Job,
        status: Literal["done", "cancelled", "error"],
        *,
        template: dict | None = None,
        summary: str = "",
        error: str | None = None,
        unplaceable: list[dict] | None = None,
    ) -> None:
        with self._lock:
            with job._lock:
                job.template, job.summary, job.error = template, summary, error
                job.unplaceable = unplaceable or []
                job.ended = time.time()
                job.status = status
            finished = sorted((j for j in self._jobs.values() if j.status != "running"), key=lambda j: j.ended or 0.0)
            for old in finished[: max(0, len(finished) - self.keep_finished)]:  # the ones that ended longest ago
                del self._jobs[old.id]

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text
