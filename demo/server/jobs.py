"""Jobs: ``build``, ``adapt``, ``revise`` and ``oneshot`` AI sessions and generic task jobs, one thread each, with progress and cancel.

    Jobs.start(kind, doc=..., schema=..., template=..., instruction=..., region=..., oneshot=..., provider_factory=...) -> Job
    Jobs.start_task(kind, runner, stages=((key, label), ...)) -> Job
    Jobs.get(job_id) -> Job            (KeyError if unknown or already forgotten)
    Jobs.cancel(job_id) -> Job

A ``Job``'s ``snapshot()`` is what ``GET /api/jobs/{id}`` returns:

    {id, kind, status, steps, template, summary, error, unplaceable, started, ended, stages, result, usage}

A ``oneshot`` job (``demo/server/oneshot.py``) runs the whole flow from a PDF and the person's data: it
may design the schema, maps the boxes (a build, whose steps appear in ``steps`` as usual) and maps the
data. ``stages`` lists its stages, each ``{key, label, status, detail}`` with status ``pending``,
``running``, ``done``, ``skipped`` or ``error``; ``result`` holds ``schema`` once known and ``data``
(records, the data mapping, warnings) once the data is mapped. Other kinds have no stages and an empty
result. ``usage`` counts the model tokens the job has used so far (a oneshot job's, in full).

A task job (``Jobs.start_task``) is the generic kind the extraction workbench uses (``extract-setup``,
``extract-line``, ``extract-run``): the caller gives the function to run, ``runner(job)``, and its stages. The runner
reports through the job (``stage``, ``set_result``, ``add_usage``) and polls ``job.cancel_requested``. Its return ends
the job ``done``; a ``SessionCancelled`` ends it ``cancelled``; a ``FormcastError`` ends it ``error`` with its message;
anything else ends it ``error`` with a generic message and the traceback goes to the log only. A task job takes one of
the same ``max_running`` slots as every other job.

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
from collections.abc import Callable, Iterable, Sequence
from typing import Literal

from demo.server.documents import Doc
from demo.server.oneshot import STAGES, OneShotSpec, run_oneshot
from formcast import FormcastError, SessionCancelled, StepEvent, Template, build_template, revise
from formcast.agent.session import SessionResult
from formcast.geometry import Rect
from formcast.llm import Provider, Usage

log = logging.getLogger("demo.server")

MAX_RUNNING = 2
KEEP_FINISHED = 20

Kind = Literal["build", "adapt", "revise", "oneshot"]
KINDS: tuple[str, ...] = ("build", "adapt", "revise", "oneshot")
#: the task jobs of the extraction workbench (``Jobs.start_task``); ``demo/server/extract_api.py`` starts them
EXTRACT_KINDS: tuple[str, ...] = ("extract-setup", "extract-line", "extract-run")

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

    def __init__(self, kind: str) -> None:
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
        self.stages: list[dict] = [{"key": k, "label": label, "status": "pending", "detail": ""} for k, label in STAGES] if kind == "oneshot" else []
        self.result: dict = {}
        self.usage = Usage()
        self._partial: dict | None = None  # a oneshot job's template so far
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
                "stages": [dict(st) for st in self.stages],
                "result": dict(self.result),
                "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
            }

    # the oneshot job's progress (demo/server/oneshot.py), set from its thread

    def stage(self, key: str, status: str, detail: str = "") -> None:
        with self._lock:
            for st in self.stages:
                if st["key"] == key:
                    st["status"], st["detail"] = status, detail

    def set_template(self, template: Template) -> None:
        dumped = template.model_dump(by_alias=True, mode="json")
        with self._lock:
            self._partial = dumped

    def set_result(self, **parts) -> None:
        with self._lock:
            self.result.update(parts)

    def add_usage(self, usage: Usage) -> None:
        with self._lock:
            self.usage = Usage(self.usage.input_tokens + usage.input_tokens, self.usage.output_tokens + usage.output_tokens)

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
        oneshot: OneShotSpec | None = None,
        provider_factory: Callable[[], Provider] | None,
    ) -> Job:
        """Start a job in its own thread. Raises ``JobsBusy`` when ``max_running`` jobs are already running.

        ``build`` needs ``schema``; ``adapt`` and ``revise`` need ``template`` (``adapt`` uses its schema);
        ``revise`` also needs ``instruction`` and may carry a ``region``; ``oneshot`` needs ``oneshot`` and is
        the one kind that may run without a ``provider_factory`` (a saved template filled from columns it knows).
        """
        if kind == "build" and schema is None:
            raise ValueError("a build job needs a schema")
        if kind in ("adapt", "revise") and template is None:
            raise ValueError(f"an {kind} job needs a template")
        if kind == "revise" and not instruction:
            raise ValueError("a revise job needs an instruction")
        if kind == "oneshot" and oneshot is None:
            raise ValueError("a oneshot job needs its spec")
        if kind != "oneshot" and provider_factory is None:
            raise ValueError(f"an {kind} job needs a provider")
        job = Job(kind)
        self._register(job)
        self._launch(job, self._run, job, doc, schema, template, instruction, region, oneshot, provider_factory)
        return job

    def _register(self, job: Job) -> None:
        """Keep ``job``, taking one of the ``max_running`` slots. Raises ``JobsBusy`` when none is free."""
        with self._lock:
            if sum(1 for j in self._jobs.values() if j.status == "running") >= self.max_running:
                raise JobsBusy(f"{self.max_running} AI jobs are already running; wait for one to finish or cancel it.")
            self._jobs[job.id] = job

    def _launch(self, job: Job, target: Callable[..., None], *args) -> None:
        thread = threading.Thread(target=target, args=args, name=f"job-{job.id[:8]}", daemon=True)
        try:
            thread.start()
        except BaseException:  # e.g. the process cannot make another thread: do not keep the slot
            self._finish(job, "error", error=_GENERIC_ERROR)
            raise

    def start_task(self, kind: str, runner: Callable[[Job], None], *, stages: Sequence[tuple[str, str]] = ()) -> Job:
        """Start ``runner(job)`` in its own thread as a job of ``kind``, with ``stages`` (``(key, label)``, all pending).

        The runner's return ends the job ``done``, ``SessionCancelled`` ``cancelled``, ``FormcastError`` ``error`` with
        its message (secrets removed), anything else ``error`` with a generic message (the traceback goes to the log).
        A stage still running when the job ends is closed as for every job (``_finish``).

        Raises:
            JobsBusy: ``max_running`` jobs are already running.
        """
        job = Job(kind)
        job.stages = [{"key": key, "label": label, "status": "pending", "detail": ""} for key, label in stages]
        self._register(job)
        self._launch(job, self._run_task, job, runner)
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

    def _run(self, job, doc, schema, template, instruction, region, oneshot, provider_factory) -> None:
        if job.kind == "oneshot":
            self._run_oneshot(job, doc, oneshot, provider_factory)
            return
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

    def _run_task(self, job: Job, runner: Callable[[Job], None]) -> None:
        try:
            runner(job)
            self._finish(job, "done")
        except SessionCancelled:
            self._finish(job, "cancelled", summary="cancelled")
        except FormcastError as exc:
            log.warning("job %s failed: %s", job.id[:8], self._redact(str(exc)))
            self._finish(job, "error", error=self._redact(str(exc)))
        except Exception:  # noqa: BLE001 - nothing may escape a job thread; the client gets a generic message
            log.error("job %s failed unexpectedly:\n%s", job.id[:8], self._redact(traceback.format_exc()))
            self._finish(job, "error", error=_GENERIC_ERROR)

    def _run_oneshot(self, job: Job, doc: Doc, spec: OneShotSpec, provider_factory) -> None:
        try:
            result = run_oneshot(job, doc, spec, provider_factory)
            if result is None or result.stop_reason == "finish":
                self._finish(job, "done", template=job._partial, summary=result.summary if result else "", unplaceable=list(result.unplaceable) if result else [])
            else:
                self._finish_from(job, result)
        except SessionCancelled:
            self._finish(job, "cancelled", template=job._partial, summary="cancelled")
        except FormcastError as exc:
            log.warning("job %s failed: %s", job.id[:8], self._redact(str(exc)))
            self._finish(job, "error", template=job._partial, error=self._redact(str(exc)))
        except Exception:  # noqa: BLE001 - nothing may escape a job thread; the client gets a generic message
            log.error("job %s failed unexpectedly:\n%s", job.id[:8], self._redact(traceback.format_exc()))
            self._finish(job, "error", template=job._partial, error=_GENERIC_ERROR)

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
                for st in job.stages:  # a stage still running stopped with the job
                    if st["status"] == "running":
                        st["status"] = "error" if status == "error" else "skipped" if status == "cancelled" else "done"
                    elif st["status"] == "pending" and status != "done":
                        st["status"] = "skipped"
                job.ended = time.time()
                job.status = status
            finished = sorted((j for j in self._jobs.values() if j.status != "running"), key=lambda j: j.ended or 0.0)
            for old in finished[: max(0, len(finished) - self.keep_finished)]:  # the ones that ended longest ago
                del self._jobs[old.id]

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text
