"""The agent loop: model writes Python, the sandbox runs it, the parent applies its ops.

One *step* is one model reply. A ``run_python`` call executes in the persistent
sandbox; the edit ops it recorded are applied in order to the authoritative
template (each validated by :func:`formcast.template.ops.apply`), the sandbox
gets the new template, and a step report goes back as the tool result. The loop
ends at ``finish``, when the budget runs out, or on a provider/sandbox error.

Context sent each turn: the system prompt, the first user message, a one-line
summary per older step, and the last ``VERBATIM_STEPS`` steps verbatim (of which
all but the latest ``FULL_OUTPUT_STEPS`` have their printed output cut to
``OLDER_STDOUT`` characters). Steps
are dropped whole (an assistant reply together with its tool results), so
tool calls and tool results always stay paired.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Literal

from formcast.agent import prompts
from formcast.agent.report import step_report
from formcast.errors import FormcastError, OpError, SessionCancelled
from formcast.geometry import Rect
from formcast.llm import LLMConfig, LLMResponse, Message, Provider, ToolCall, ToolSpec, Usage, make_provider
from formcast.pagemap.model import FormMap
from formcast.pagemap.summarize import summarize
from formcast.sandbox import ExecResult, Sandbox
from formcast.sandbox.worker import api_text
from formcast.schema import flatten_schema
from formcast.template.coverage import Coverage, coverage
from formcast.template.model import Template, new_template
from formcast.template.ops import OpContext, apply

VERBATIM_STEPS = 6
# Of the verbatim steps, only the latest FULL_OUTPUT_STEPS keep their printed output in
# full; older ones keep OLDER_STDOUT characters of it (the rest of their report stays).
FULL_OUTPUT_STEPS = 2
OLDER_STDOUT = 1500
OLDER_NOTE = "older step: output shortened, keep what you need in variables"
MAX_RAW_ARGS_ECHO = 300

TOOLS = [
    ToolSpec(
        name="run_python",
        description=(
            "Run Python in the persistent sandbox (fm, schema, tpl, the edit ops, transforms() and "
            "api() are preloaded; variables persist between calls). Returns the step report: "
            "output, applied and rejected ops, warnings and current coverage."
        ),
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to run."}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        name="finish",
        description="End the session when coverage can't improve. List schema paths you could not place.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What the template covers, briefly."},
                "unplaceable": {
                    "type": "array",
                    "description": "Schema paths with no place on the form, each with the reason.",
                    "items": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
                        "required": ["path", "reason"],
                    },
                },
            },
            "required": ["summary", "unplaceable"],
        },
    ),
]
_TOOL_NAMES = [t.name for t in TOOLS]


@dataclass(frozen=True)
class SessionBudget:
    max_steps: int = 40
    max_input_tokens: int = 1_500_000
    max_output_tokens: int = 200_000
    wall_clock_s: float = 900


@dataclass
class StepRecord:
    """One model reply. ``exec`` is None for replies that ran no code (``note`` says why)."""

    n: int
    code: str
    exec: ExecResult | None
    applied: list[dict]
    rejected: list[dict]
    warnings: list[str]
    usage: Usage
    note: str = ""


@dataclass(frozen=True)
class StepEvent:
    """What ``on_step`` is told after each model reply (the step report's counts, token use so far)."""

    step: int
    ops_applied: int
    ops_rejected: int
    unmapped_paths: int  # mappable schema paths no field references
    unfilled_blanks: int  # detector candidates no field covers
    input_tokens: int  # cumulative, this session
    output_tokens: int
    finished: bool  # the model's finish was accepted: this is the last event
    summary: str  # the model's finish summary on the last event, else ""


@dataclass
class SessionResult:
    template: Template
    finished: bool
    summary: str
    steps: list[StepRecord]
    usage: Usage
    stop_reason: Literal["finish", "budget", "error", "cancelled"]
    unplaceable: list[dict] = field(default_factory=list)
    error: str | None = None  # set when stop_reason == "error"


def _hit_token_limit(raw: dict[str, Any]) -> bool:
    """Whether a raw provider reply says it stopped at the output token limit."""
    try:
        choices = raw.get("choices")
        if isinstance(choices, list) and choices and choices[0].get("finish_reason") == "length":
            return True
        details = raw.get("incomplete_details") or {}
        return raw.get("status") == "incomplete" and details.get("reason") == "max_output_tokens"
    except AttributeError:
        return False


FINISH_REFUSED = (
    "finish not accepted: this step had errors/rejected ops — read the report and fix them, "
    "or explain them in unplaceable, then call finish again."
)


MAX_UNACCOUNTED_SHOWN = 25


def _unaccounted_message(missing: list[str], steps_left: int) -> str:
    shown = ", ".join(missing[:MAX_UNACCOUNTED_SHOWN])
    if len(missing) > MAX_UNACCOUNTED_SHOWN:
        shown += f", ... {len(missing) - MAX_UNACCOUNTED_SHOWN} more"
    return (
        f"finish not accepted yet: {len(missing)} schema path(s) are neither mapped nor listed in "
        f"unplaceable: {shown}. Look for a place for each (fm.find_text with its key words, the fields "
        "still without a source), map what you find, and list the rest in unplaceable with the reason. "
        f"You have {steps_left} steps left; call finish again when done."
    )


def _accounted(path: str, listed: set[str]) -> bool:
    """Whether ``path`` is named in finish's unplaceable list, directly or through its table."""
    for entry in listed:
        base = entry.removesuffix("*").removesuffix(".").removesuffix("[]")
        if base and (path == base or path.startswith(base + "[]") or path.startswith(base + ".")):
            return True
    return False


def _had_problems(record: StepRecord) -> bool:
    ex = record.exec
    return bool(record.rejected) or (ex is not None and (ex.error is not None or ex.timed_out))


def _unplaceable(value: Any) -> list[dict]:
    """Normalize finish's ``unplaceable`` argument leniently into [{path, reason}]."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            out.append({"path": str(item.get("path", "")), "reason": str(item.get("reason", ""))})
        elif isinstance(item, str):
            out.append({"path": item, "reason": ""})
    return out


class Session:
    """One agent run over ``form_map`` for ``schema``.

    ``llm`` is an :class:`LLMConfig` (the session creates and closes the
    provider) or a ready :class:`Provider` (the caller keeps ownership). With
    ``template`` the run starts from it; ``instruction`` and ``region`` (page,
    rect) go into the first message, as for a reviewer's revision request.
    Without ``template`` the run starts from a fresh one: a field per widget
    and, with ``seed_candidates`` (the default), one per detector candidate.
    Up to ``unaccounted_refusals`` times (not for a reviewer instruction) a
    ``finish`` is refused while mappable schema paths are neither mapped nor
    listed in its ``unplaceable``, unless fewer than 2 steps are left.

    ``on_step`` is called with a :class:`StepEvent` after every model reply. It may
    raise :class:`SessionCancelled` to stop the run: the sandbox is closed and the
    result has ``finished=False``, ``summary="cancelled"`` and the working template.
    An exception of another kind propagates (after the sandbox is closed).
    """

    def __init__(
        self,
        form_map: FormMap,
        schema: dict,
        llm: LLMConfig | Provider,
        *,
        template: Template | None = None,
        instruction: str | None = None,
        region: tuple[int, Rect] | None = None,
        budget: SessionBudget = SessionBudget(),
        sandbox_factory=Sandbox,
        seed_candidates: bool = True,
        unaccounted_refusals: int = 1,
        on_step: Callable[[StepEvent], None] | None = None,
    ):
        self.form_map = form_map
        self.schema = schema
        self.schema_paths = flatten_schema(schema)
        self.llm = llm
        fresh = template is None
        # A fresh template starts with one field per widget and per detector candidate, so the
        # agent confirms, maps, fixes or removes them rather than redrawing every box.
        self.template = template if not fresh else new_template(schema, form_map, candidates=seed_candidates)
        self.instruction = instruction
        self.region = region
        self.budget = budget
        self.sandbox_factory = sandbox_factory
        self.on_step = on_step
        self._cov: Coverage | None = None  # coverage as of the last run_python step
        self.context = OpContext(
            schema_paths=self.schema_paths,
            page_sizes=[(p.width, p.height) for p in sorted(form_map.pages, key=lambda p: p.index)],
            widget_names=[w.name for p in form_map.pages for w in p.widgets],
        )
        self._refusals_left = unaccounted_refusals if instruction is None else 0
        self.steps: list[StepRecord] = []
        self.usage = Usage()
        self._groups: list[list[Message]] = []  # messages of each step, in order
        # Per group: the same messages with shortened printed output, for older verbatim steps.
        self._short_groups: list[list[Message]] = []
        self._system = Message(role="system", content=prompts.SYSTEM_PROMPT)
        self._first = Message(
            role="user",
            content=prompts.first_user_message(
                summarize(form_map), self.schema_paths, instruction, region, template=self.template,
                fresh=fresh, api_reference=api_text(),
            ),
        )

    # -- public ----------------------------------------------------------------

    def run(self) -> SessionResult:
        owns_provider = isinstance(self.llm, LLMConfig)
        provider: Provider | None = None
        sandbox = None
        try:
            provider = make_provider(self.llm) if owns_provider else self.llm  # type: ignore[arg-type]
            sandbox = self.sandbox_factory()
            try:
                sandbox.start(self.form_map, self.schema_paths, self.template)
            except FormcastError as e:
                return self._result("error", error=f"sandbox: {e}")
            return self._loop(provider, sandbox)
        finally:
            try:
                if sandbox is not None:
                    sandbox.close()
            finally:
                if owns_provider and provider is not None:
                    provider.close()

    # -- loop ------------------------------------------------------------------

    def _loop(self, provider: Provider, sandbox: Sandbox) -> SessionResult:
        started = time.monotonic()
        while True:
            if self._over_budget(started):
                return self._result("budget")
            try:
                response = provider.complete(self._messages(), TOOLS)
            except Exception as e:  # noqa: BLE001 - provider errors (after its retries) end the run
                return self._result("error", error=f"{type(e).__name__}: {e}")
            self.usage = Usage(
                self.usage.input_tokens + response.usage.input_tokens,
                self.usage.output_tokens + response.usage.output_tokens,
            )
            n = len(self.steps) + 1
            try:
                finished = self._handle(n, response, sandbox)
            except FormcastError as e:  # the sandbox itself failed (not the model's code)
                return self._result("error", error=f"sandbox: {e}")
            if self.on_step is not None:
                try:
                    self.on_step(self._event(n, finished))
                except SessionCancelled:
                    return self._result("cancelled", summary="cancelled")
            if finished is not None:
                summary, unplaceable = finished
                return self._result("finish", summary=summary, unplaceable=unplaceable)

    def _event(self, n: int, finished: tuple[str, list[dict]] | None) -> StepEvent:
        step = self.steps[-1]
        if self._cov is None:  # no run_python yet: the template is still the starting one
            self._cov = coverage(self.template, self.form_map, self.schema_paths)
        return StepEvent(
            step=n,
            ops_applied=len(step.applied),
            ops_rejected=len(step.rejected),
            unmapped_paths=len(self._cov.unmapped_paths),
            unfilled_blanks=len(self._cov.unused_candidates),
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            finished=finished is not None,
            summary=finished[0] if finished is not None else "",
        )

    def _over_budget(self, started: float) -> bool:
        b = self.budget
        return (
            len(self.steps) >= b.max_steps
            or self.usage.input_tokens >= b.max_input_tokens
            or self.usage.output_tokens >= b.max_output_tokens
            or time.monotonic() - started >= b.wall_clock_s
        )

    def _handle(self, n: int, response: LLMResponse, sandbox: Sandbox) -> tuple[str, list[dict]] | None:
        """Process one reply; returns (summary, unplaceable) when the model finished."""
        if not response.tool_calls:
            # Hand-built assistant message: a reply without a tool call may carry only reasoning
            # items (cut off at the token limit), which the Responses API refuses to take back alone.
            self._groups.append([
                Message(role="assistant", content=response.text or "(no tool call)"),
                Message(role="user", content=prompts.no_tool_call_message(_hit_token_limit(response.raw))),
            ])  # fmt: skip
            self._short_groups.append(self._groups[-1])
            self._record(n, response, note="no tool call")
            return None

        group = [response.to_message()]
        self._groups.append(group)
        results: list[str] = []  # one per tool call, in order (ids may repeat or be empty)
        short_results: list[tuple[int, str]] = []  # (result index, report with shortened output)
        record: StepRecord | None = None
        finish_call: ToolCall | None = None
        finish_index = -1
        notes: list[str] = []
        hit_limit = _hit_token_limit(response.raw)
        for i, call in enumerate(response.tool_calls):
            problem = self._call_problem(call, hit_limit)
            if problem:
                results.append(f"Error: {problem}")
                notes.append(problem)
            elif call.name == "finish":
                if finish_call is None:
                    finish_call, finish_index = call, i
                results.append("Finished.")
            elif record is not None:
                results.append(
                    "Skipped: only one run_python call per reply is run; put all the code in one call."
                )
            else:
                record = self._run_code(n, call.arguments["code"], response, sandbox)  # type: ignore[index]
                cov = self._cov = coverage(self.template, self.form_map, self.schema_paths)
                kwargs = {
                    "template": self.template, "form_map": self.form_map, "budget": (n, self.budget.max_steps),
                }  # fmt: skip
                results.append(step_report(record, cov, **kwargs))
                short_results.append(
                    (i, step_report(record, cov, **kwargs, max_stdout=OLDER_STDOUT, stdout_note=OLDER_NOTE))
                )
        if finish_call is not None and record is not None and _had_problems(record):
            # Finishing blind on a step whose code failed or whose ops were rejected would drop
            # those edits silently: refuse, and let the model read the report first.
            results[finish_index] = FINISH_REFUSED
            notes.append("finish refused")
            finish_call = None
        if finish_call is not None and self._refusals_left > 0:
            # Models tend to stop with many paths unmapped and most of the budget left: ask
            # once for a search for each path, or a reason it has no place on the form.
            listed = {u["path"] for u in _unplaceable((finish_call.arguments or {}).get("unplaceable"))}
            unmapped = coverage(self.template, self.form_map, self.schema_paths).unmapped_paths
            missing = [p for p in unmapped if not _accounted(p, listed)]
            steps_left = self.budget.max_steps - n
            if missing and steps_left >= 2:
                self._refusals_left -= 1
                results[finish_index] = _unaccounted_message(missing, steps_left)
                notes.append("finish refused: unaccounted paths")
                finish_call = None
        group.extend(
            Message(role="tool", content=text, tool_call_id=c.id)
            for c, text in zip(response.tool_calls, results, strict=True)
        )
        short = dict(short_results)
        self._short_groups.append(
            [group[0]]
            + [
                Message(role="tool", content=short.get(i, text), tool_call_id=c.id)
                for i, (c, text) in enumerate(zip(response.tool_calls, results, strict=True))
            ]
        )
        if record is None:
            self._record(n, response, note="finish" if finish_call else "; ".join(notes) or "no code run")
        if finish_call is not None:
            args = finish_call.arguments or {}
            return str(args.get("summary") or ""), _unplaceable(args.get("unplaceable"))
        return None

    def _call_problem(self, call: ToolCall, hit_token_limit: bool = False) -> str | None:
        if call.name not in _TOOL_NAMES:
            return f"unknown tool {call.name!r}; available tools: {', '.join(_TOOL_NAMES)}."
        if call.arguments is None:
            raw = call.raw_arguments
            shown = raw if len(raw) <= MAX_RAW_ARGS_ECHO else raw[:MAX_RAW_ARGS_ECHO] + "..."
            return (
                f"the arguments for {call.name} are not a valid JSON object: {shown!r}. "
                "Send them again as JSON." + (prompts.TOKEN_LIMIT_NOTE if hit_token_limit else "")
            )
        if call.name == "run_python" and not isinstance(call.arguments.get("code"), str):
            return 'run_python needs a "code" string argument.'
        return None

    def _run_code(self, n: int, code: str, response: LLMResponse, sandbox: Sandbox) -> StepRecord:
        result = sandbox.exec(code)
        applied: list[dict] = []
        rejected: list[dict] = []
        warnings: list[str] = []
        for op in result.ops:  # also the ops recorded before an error in the code
            try:
                self.template, op_warnings = apply(
                    self.template, op, author="agent", note=f"step {n}", context=self.context
                )
            except OpError as e:
                rejected.append({"op": op, "reason": e.reason})
                continue
            applied.append(op)
            warnings.extend(op_warnings)
        if result.ops:
            sandbox.update_template(self.template)
        record = StepRecord(
            n=n, code=code, exec=result, applied=applied, rejected=rejected, warnings=warnings,
            usage=response.usage, note="run_python",
        )  # fmt: skip
        self.steps.append(record)
        return record

    def _record(self, n: int, response: LLMResponse, *, note: str) -> None:
        self.steps.append(
            StepRecord(
                n=n, code="", exec=None, applied=[], rejected=[], warnings=[], usage=response.usage, note=note
            )
        )

    # -- context -----------------------------------------------------------------

    def _compressed_line(self, record: StepRecord) -> str:
        if record.exec is not None:
            line = f"step {record.n}: {len(record.applied)} ops applied, {len(record.rejected)} rejected"
            return line + (" (code raised an error)" if record.exec.error else "")
        note = record.note if len(record.note) <= 100 else record.note[:97] + "..."
        return f"step {record.n}: {note}"

    def _messages(self) -> list[Message]:
        messages = [self._system, self._first]
        old = len(self._groups) - VERBATIM_STEPS
        if old > 0:
            lines = [self._compressed_line(r) for r in self.steps[:old]]
            messages.append(Message(role="user", content="Earlier steps (shortened):\n" + "\n".join(lines)))
        full_from = len(self._groups) - FULL_OUTPUT_STEPS
        for i in range(max(0, old), len(self._groups)):
            messages.extend(self._groups[i] if i >= full_from else self._short_groups[i])
        return messages

    def _result(
        self,
        stop_reason: Literal["finish", "budget", "error", "cancelled"],
        *,
        summary: str = "",
        unplaceable: list[dict] | None = None,
        error: str | None = None,
    ) -> SessionResult:
        return SessionResult(
            template=self.template,
            finished=stop_reason == "finish",
            summary=summary,
            steps=list(self.steps),
            usage=self.usage,
            stop_reason=stop_reason,
            unplaceable=unplaceable or [],
            error=error,
        )
