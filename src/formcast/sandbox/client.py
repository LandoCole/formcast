"""Parent side of the sandbox: spawns and talks to the worker process."""

from __future__ import annotations

import collections
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import formcast
from formcast.errors import FormcastError
from formcast.pagemap.model import FormMap
from formcast.sandbox import protocol
from formcast.schema import SchemaPath
from formcast.template.model import Template

STARTUP_TIMEOUT_S = 60.0
STDERR_TAIL_LINES = 40
# How often the parent reads the worker's memory while it waits for an answer (see _Worker.request).
MEMORY_POLL_S = 0.2
# The only parent environment variables the worker sees (the parent may hold API
# keys in os.environ, and user code can reach the worker's os module).
ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT")


def _worker_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST or k.startswith("LC_")}
    package_root = str(Path(formcast.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH")) if p)
    return env


class _RusageInfoV0(ctypes.Structure):
    """macOS ``struct rusage_info_v0`` (``<sys/resource.h>``), as ``proc_pid_rusage`` fills it."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
            "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime",
        )
    ]  # fmt: skip


def _darwin_rusage():
    """libproc's ``proc_pid_rusage``, or None when it can't be loaded."""
    try:
        fn = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RusageInfoV0)]
    fn.restype = ctypes.c_int
    return fn


_PROC_PID_RUSAGE = _darwin_rusage() if sys.platform == "darwin" else None


def memory_bytes(pid: int) -> int | None:
    """The memory process ``pid`` holds now: its physical footprint on macOS (what Activity Monitor shows), its
    resident set on Linux; None where it can't be read (another OS, or the process is gone)."""
    if _PROC_PID_RUSAGE is not None:
        info = _RusageInfoV0()
        return int(info.ri_phys_footprint) if _PROC_PID_RUSAGE(pid, 0, ctypes.byref(info)) == 0 else None
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


@dataclass
class ExecResult:
    """What one ``exec`` produced.

    ``ops`` are the edit ops recorded during the exec, in call order (also when
    the code raised midway: the ones recorded before the error). ``error`` is a
    traceback limited to the user's code, or None. On a timeout the worker is
    restarted, ``ops`` and ``stdout`` are empty and ``error`` says the
    namespace was reset.
    """

    stdout: str
    truncated: bool
    ops: list[dict] = field(default_factory=list)
    error: str | None = None
    timed_out: bool = False


class _Worker:
    """One worker process with reader threads for its stdout and stderr."""

    def __init__(self, memory_mb: int):
        self.memory_mb = memory_mb
        # -P: do not put the current directory on sys.path (it could shadow modules).
        self.proc = subprocess.Popen(
            [sys.executable, "-P", "-m", "formcast.sandbox.worker", "--memory-mb", str(memory_mb)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_worker_env(),
        )
        self.seq = 0
        self.responses: queue.Queue[dict | None] = queue.Queue()
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        self.threads = [
            threading.Thread(target=self._read_responses, args=(self.proc.stdout,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(self.proc.stderr,), daemon=True),
        ]
        for t in self.threads:
            t.start()

    def _read_responses(self, stream: IO[bytes]) -> None:
        for line in iter(stream.readline, b""):
            try:
                self.responses.put(protocol.decode(line))
            except ValueError:
                self.responses.put({"ok": False, "error": f"bad protocol line: {line[:200]!r}"})
        self.responses.put(None)  # EOF

    def _read_stderr(self, stream: IO[bytes]) -> None:
        for line in iter(stream.readline, b""):
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip("\n"))

    def request(self, message: dict[str, Any], timeout: float) -> tuple[str, dict | None]:
        """Send one request; ``("ok", response)``, ``("timeout", None)``, ``("exited", None)`` or ``("memory", None)``.

        Responses whose ``seq`` is not this request's (left over from a request
        the caller stopped waiting for) are discarded. While it waits, the worker's
        memory is read every ``MEMORY_POLL_S``: past ``memory_mb`` the worker is
        killed and the answer is ``"memory"``. That is the limit on macOS, which
        does not enforce the worker's ``RLIMIT_AS``; elsewhere it backs it up.
        """
        self.seq += 1
        seq = self.seq
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(protocol.encode({**message, "seq": seq}))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return "exited", None
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            try:
                response = self.responses.get(timeout=max(0.0, min(left, MEMORY_POLL_S)))
            except queue.Empty:
                if self._over_memory():
                    self.proc.kill()
                    return "memory", None
                if left <= MEMORY_POLL_S:
                    return "timeout", None
                continue
            if response is None:
                return "exited", None
            if response.get("seq") == seq:
                return "ok", response

    def _over_memory(self) -> bool:
        used = memory_bytes(self.proc.pid)
        return used is not None and used > self.memory_mb * 1024 * 1024

    def describe_exit(self) -> str:
        try:
            code = self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            code = None
        tail = "\n".join(list(self.stderr_tail)[-5:])
        return f"exit code {code}" + (f"; stderr:\n{tail}" if tail else "")

    def stop(self, *, graceful: bool) -> None:
        if graceful and self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for t in self.threads:
            t.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class Sandbox:
    """A persistent Python worker for the agent's code.

    Use as a context manager, or call :meth:`close`. :meth:`start` must come
    before :meth:`exec`. See :mod:`formcast.sandbox.worker` for the namespace
    and the restrictions on the code.
    """

    def __init__(self, timeout_s: float = 20, max_stdout: int = 8000, memory_mb: int = 1024):
        self.timeout_s = timeout_s
        self.max_stdout = max_stdout
        self.memory_mb = memory_mb
        self._worker: _Worker | None = None
        self._context: dict[str, Any] | None = None

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def _proc(self) -> subprocess.Popen | None:
        return self._worker.proc if self._worker else None

    def start(self, form_map: FormMap, schema_paths: list[SchemaPath], template: Template) -> None:
        """Spawn the worker and build its namespace from this context."""
        self._context = {
            "form_map": form_map.model_dump(mode="json"),
            "schema": [sp.model_dump(mode="json") for sp in schema_paths],
            "template": json.loads(template.to_json()),
        }
        self._spawn()

    def update_template(self, template: Template) -> None:
        """Replace the snapshot behind ``tpl`` (and the base for new field ids)."""
        context = self._require_started()
        context["template"] = json.loads(template.to_json())
        status, response = self._request(
            {"cmd": protocol.UPDATE_TEMPLATE, "template": context["template"]}, STARTUP_TIMEOUT_S
        )
        if status != "ok":
            self._spawn()  # the fresh worker gets the new template from the context
        elif not response or not response.get("ok"):
            raise FormcastError(f"sandbox worker rejected the template: {(response or {}).get('error')}")

    def exec(self, code: str) -> ExecResult:
        """Run ``code`` in the persistent namespace."""
        self._require_started()
        status, response = self._request({"cmd": protocol.EXEC, "code": code}, self.timeout_s)
        if status == "timeout":
            self._spawn()
            return ExecResult(
                stdout="",
                truncated=False,
                error=(
                    f"Timed out after {self.timeout_s:g}s. The worker was killed and restarted, so "
                    "the namespace was reset: variables, functions and imports from earlier steps "
                    "are gone (fm, schema, tpl, the edit ops, transforms() and api() are still "
                    "there). Ops and output from this step were discarded."
                ),
                timed_out=True,
            )
        if status == "memory":
            self._spawn()
            return ExecResult(
                stdout="",
                truncated=False,
                error=(
                    f"Stopped: the code used more than {self.memory_mb:,} MB of memory. The worker was killed and "
                    "restarted, so the namespace was reset: variables, functions and imports from earlier steps are "
                    "gone (fm, schema, tpl, the edit ops, transforms() and api() are still there). Ops and output from "
                    "this step were discarded."
                ),
            )
        if status == "exited":
            assert self._worker is not None
            detail = self._worker.describe_exit()
            self._spawn()
            return ExecResult(
                stdout="",
                truncated=False,
                error=(
                    f"The worker process died ({detail}). It was restarted, so the namespace was "
                    "reset: variables, functions and imports from earlier steps are gone. Ops and "
                    "output from this step were discarded."
                ),
            )
        assert response is not None
        if not response.get("ok"):
            raise FormcastError(f"sandbox worker error: {response.get('error')}")
        return ExecResult(
            stdout=response["stdout"],
            truncated=response["truncated"],
            ops=response["ops"],
            error=response["error"],
        )

    def close(self) -> None:
        """Stop the worker. Safe to call more than once."""
        self._context = None
        if self._worker is not None:
            self._worker.stop(graceful=True)
            self._worker = None

    def _require_started(self) -> dict[str, Any]:
        if self._context is None:
            raise FormcastError("sandbox not started: call start() first")
        if self._worker is None:  # killed by an interrupted request: start over from the context
            self._spawn()
        return self._context

    def _request(self, message: dict[str, Any], timeout: float) -> tuple[str, dict | None]:
        assert self._worker is not None
        try:
            return self._worker.request(message, timeout)
        except BaseException:
            # Interrupted mid-request (e.g. KeyboardInterrupt): the worker may still
            # be busy with it, so drop it; the next call respawns from the context.
            self._worker.stop(graceful=False)
            self._worker = None
            raise

    def _spawn(self) -> None:
        """(Re)start the worker fresh from the last context."""
        if self._worker is not None:
            self._worker.stop(graceful=False)
            self._worker = None
        assert self._context is not None
        worker = _Worker(self.memory_mb)
        status, response = worker.request(
            {"cmd": protocol.INIT, **self._context, "max_stdout": self.max_stdout}, STARTUP_TIMEOUT_S
        )
        if status != "ok" or not response or not response.get("ok"):
            detail = (response or {}).get("error") or {
                "exited": worker.describe_exit, "memory": lambda: f"it used more than {self.memory_mb:,} MB"
            }.get(status, lambda: "no answer in time")()
            worker.stop(graceful=False)
            raise FormcastError(f"sandbox worker failed to start: {detail}")
        self._worker = worker
