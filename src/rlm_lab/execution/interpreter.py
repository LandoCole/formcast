from __future__ import annotations

import ast
import json
import math
import re
import statistics
import textwrap
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from dspy.primitives.code_interpreter import CodeInterpreterError, FinalOutput

from rlm_lab.execution.events import EventRecorder
from rlm_lab.modeling.usage import sanitize_for_json


class _SubmitSignal(Exception):
    def __init__(self, output: dict[str, Any]):
        super().__init__("Submitted final output")
        self.output = output


class LocalPythonInterpreter:
    """Small in-process interpreter compatible with DSPy's CodeInterpreter protocol."""

    def __init__(
        self,
        recorder: EventRecorder,
        runner_name: str,
        tools: dict[str, Callable[..., str]] | None = None,
        output_fields: list[dict[str, Any]] | None = None,
    ) -> None:
        self.recorder = recorder
        self.runner_name = runner_name
        self.tools = dict(tools or {})
        self.output_fields = output_fields or []
        self._state: dict[str, Any] = {}
        self._reserved_names = {
            "__builtins__",
            "FINAL",
            "SUBMIT",
            "datetime",
            "json",
            "math",
            "re",
            "statistics",
            "textwrap",
        }

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        self._state.clear()

    def _safe_builtins(self, traced_print: Callable[..., None]) -> dict[str, Any]:
        return {
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "print": traced_print,
            "range": range,
            "round": round,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "zip": zip,
        }

    def _submit(self, *args: Any, **kwargs: Any) -> None:
        if kwargs:
            raise _SubmitSignal(kwargs)
        if len(args) == 1 and isinstance(args[0], dict):
            raise _SubmitSignal(args[0])
        output_names = [field["name"] for field in self.output_fields]
        if len(args) == 1 and len(output_names) == 1:
            raise _SubmitSignal({output_names[0]: args[0]})
        if len(args) == len(output_names) and output_names:
            raise _SubmitSignal(dict(zip(output_names, args, strict=True)))
        raise CodeInterpreterError(
            "SUBMIT requires keyword arguments, a dict, or positional arguments matching the output fields."
        )

    def _wrap_tool(self, name: str, tool: Callable[..., str]) -> Callable[..., str]:
        def wrapped(*args: Any, **kwargs: Any) -> str:
            self.recorder.emit(
                "tool.call.started",
                runner=self.runner_name,
                summary=name,
                payload={"tool": name, "args": sanitize_for_json(args), "kwargs": sanitize_for_json(kwargs)},
            )
            try:
                result = tool(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive branch
                self.recorder.emit(
                    "tool.call.failed",
                    runner=self.runner_name,
                    summary=f"{name} failed",
                    payload={"tool": name, "error": f"{type(exc).__name__}: {exc}"},
                )
                raise
            self.recorder.emit(
                "tool.call.completed",
                runner=self.runner_name,
                summary=name,
                payload={"tool": name, "result_preview": str(result)[:300]},
            )
            return result

        wrapped.__name__ = name
        wrapped.__doc__ = getattr(tool, "__doc__", "")
        return wrapped

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        outputs: list[str] = []
        user_variables = dict(variables or {})
        self.recorder.emit(
            "interpreter.execute.started",
            runner=self.runner_name,
            summary="Local interpreter",
            payload={"code": code},
        )

        def traced_print(*args: Any, **kwargs: Any) -> None:
            text = " ".join(str(arg) for arg in args)
            outputs.append(text)
            self.recorder.emit(
                "interpreter.print",
                runner=self.runner_name,
                summary=text[:80],
                payload={"text": text, "kwargs": sanitize_for_json(kwargs)},
            )

        globals_dict: dict[str, Any] = {
            "__builtins__": self._safe_builtins(traced_print),
            "FINAL": self._submit,
            "SUBMIT": self._submit,
            "datetime": datetime,
            "json": json,
            "math": math,
            "re": re,
            "statistics": statistics,
            "textwrap": textwrap,
        }
        globals_dict.update({name: self._wrap_tool(name, tool) for name, tool in self.tools.items()})
        globals_dict.update(self._state)
        globals_dict.update(user_variables)

        try:
            module = ast.parse(code, mode="exec")
            last_expr = None
            if module.body and isinstance(module.body[-1], ast.Expr):
                last_expr = ast.Expression(module.body.pop().value)

            if module.body:
                exec(compile(module, "<rlm-lab>", "exec"), globals_dict, globals_dict)
            if last_expr is not None:
                value = eval(compile(last_expr, "<rlm-lab>", "eval"), globals_dict, globals_dict)
                if value is not None:
                    outputs.append(str(value))
        except _SubmitSignal as submission:
            self._state = {
                key: value
                for key, value in globals_dict.items()
                if not key.startswith("__") and key not in self._reserved_names and key not in self.tools
            }
            final = FinalOutput(submission.output)
            self.recorder.emit(
                "interpreter.execute.completed",
                runner=self.runner_name,
                summary="Submitted final output",
                payload={"final_output": sanitize_for_json(final.output)},
            )
            return final
        except SyntaxError:
            raise
        except Exception as exc:
            self.recorder.emit(
                "interpreter.execute.failed",
                runner=self.runner_name,
                summary=f"{type(exc).__name__}",
                payload={"error": f"{type(exc).__name__}: {exc}", "code": code},
            )
            raise CodeInterpreterError(f"{type(exc).__name__}: {exc}") from exc

        self._state = {
            key: value
            for key, value in globals_dict.items()
            if not key.startswith("__") and key not in self._reserved_names and key not in self.tools
        }
        result = "\n".join(outputs).strip()
        self.recorder.emit(
            "interpreter.execute.completed",
            runner=self.runner_name,
            summary=result[:120] if result else "No output",
            payload={"result": result, "state_keys": sorted(self._state.keys())},
        )
        return result
