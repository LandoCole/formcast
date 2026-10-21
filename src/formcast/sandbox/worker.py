"""Sandbox worker: runs the agent's Python in a persistent namespace.

Started by :class:`~formcast.sandbox.client.Sandbox` as
``python -m formcast.sandbox.worker``; it speaks :mod:`formcast.sandbox.protocol`
over stdin/stdout.

The namespace (see :func:`api_text` for what the model is told) holds ``fm``
(a :class:`~formcast.pagemap.query.FormMapQuery`), ``schema``, ``tpl`` (a
deep-copied plain-dict snapshot of the template), one function per edit op,
``transforms()``, ``api()``, ``help()`` and some pre-imported modules. Those
names are restored at the start of every exec, so rebinding or mutating them
lasts one step at most. Edit-op calls are only *recorded* here; the parent
validates and applies them against the authoritative template.

Restrictions on the user's code: its own builtins dict without ``open``,
``exec``, ``eval``, ``compile``, ``input``, ``globals``, ``locals``, ``vars``,
``breakpoint`` (and with ``__import__`` replaced by an allowlist importer);
``RLIMIT_AS`` where the OS enforces it (not macOS). The worker's own modules
are imported before any of this applies.

This is **not a security boundary against a hostile model** (per the spec), only
a guard against accidents. Known escape routes left open on purpose include
attribute walks such as ``json.__builtins__``, ``typing.sys``, a preloaded
function's ``__globals__``, and ``object.__subclasses__()``.
"""

from __future__ import annotations

import argparse
import builtins
import collections
import copy
import inspect
import itertools
import json
import linecache
import math
import os
import re
import statistics
import sys
import traceback
import types
import typing
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from formcast.pagemap.query import FormMapQuery
from formcast.sandbox import protocol
from formcast.template.op_models import OP_CLASSES
from formcast.transforms import list_transforms

ALLOWED_IMPORTS = frozenset({
    "re", "math", "statistics", "itertools", "collections", "json",
    "functools", "string", "dataclasses", "typing",
})  # fmt: skip
PRELOADED_MODULES = {
    "re": re, "math": math, "statistics": statistics,
    "itertools": itertools, "collections": collections, "json": json,
}  # fmt: skip

_SAFE_BUILTIN_NAMES = (
    "abs all any ascii bin bool bytearray bytes callable chr classmethod complex delattr dict "
    "dir divmod enumerate filter float format frozenset getattr hasattr hash hex id int "
    "isinstance issubclass iter len list map max min next object oct ord pow print property "
    "range repr reversed round set setattr slice sorted staticmethod str sum super tuple type "
    "zip Ellipsis NotImplemented __build_class__"
).split()
SAFE_BUILTINS: dict[str, Any] = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
SAFE_BUILTINS.update({
    name: obj
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
})

MAX_ERROR_LINES = 15
MAX_ERROR_LINE_CHARS = 500
_STEP_PREFIX = "<step-"


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level:
        raise ImportError("relative imports are not allowed")
    if name.partition(".")[0] not in ALLOWED_IMPORTS:
        allowed = ", ".join(sorted(ALLOWED_IMPORTS))
        raise ImportError(f"import of {name!r} is not allowed (allowed: {allowed})")
    return builtins.__import__(name, globals, locals, fromlist, level)


# --- edit-op functions -------------------------------------------------------


def _tname(annotation: Any) -> str:
    """Short, readable type name for the API listing."""
    if annotation is type(None):
        return "None"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return " | ".join(repr(a) for a in args)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_tname(a) for a in args)
    if origin is tuple:
        if args == (float, float, float, float):
            return "[x0, y0, x1, y1]"
        return "[" + ", ".join(_tname(a) for a in args) + "]"
    if origin is list:
        return f"list[{_tname(args[0])}]" if args else "list"
    if origin is dict:
        return "dict"
    if annotation is typing.Any:
        return "any"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _nested_models(annotation: Any, found: dict[str, type[BaseModel]]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation.__name__ not in found:
            found[annotation.__name__] = annotation
            for f in annotation.model_fields.values():
                _nested_models(f.annotation, found)
        return
    for arg in get_args(annotation):
        _nested_models(arg, found)


def _param_text(name: str, field) -> str:
    text = f"{name}: {_tname(field.annotation)}"
    if not field.is_required():
        text += f" = {field.get_default(call_default_factory=True)!r}"
    return text


class _OpSpec:
    """Argument names of one edit op, read off its pydantic model."""

    def __init__(self, cls: type[BaseModel]):
        self.name: str = cls.model_fields["op"].default
        self.fields = {n: f for n, f in cls.model_fields.items() if n != "op"}
        self.required = [n for n, f in self.fields.items() if f.is_required()]
        id_field = self.fields.get("id")
        # Ops on an existing field take its id as the (only) positional argument;
        # ops that create a field take an optional id, pre-assigned when absent.
        self.positional_id = id_field is not None and id_field.is_required()
        self.creates = id_field is not None and not id_field.is_required()
        # An op on an existing field with exactly one other required argument (set_label's
        # label, set_source's source, ...) also takes that argument as the second positional.
        others = [n for n in self.required if n != "id"]
        self.positional_value = others[0] if self.positional_id and len(others) == 1 else None
        rest = [
            _param_text(n, f)
            for n, f in self.fields.items()
            if not (self.positional_id and n == "id") and n != self.positional_value
        ]
        head = ["id"] if self.positional_id else []
        if self.positional_value:
            head.append(_param_text(self.positional_value, self.fields[self.positional_value]))
        self.signature = "(" + ", ".join(head + (["*", *rest] if rest else [])) + ")"
        self.doc = f"{self.name}{self.signature}\nRecords a {self.name!r} edit op; returns the field id."

    def bind(self, args: tuple, kwargs: dict) -> dict:
        if args:
            if not self.positional_id:
                raise TypeError(f"{self.name}() takes keyword arguments only: {self.name}{self.signature}")
            limit = 2 if self.positional_value else 1
            if len(args) > limit:
                what = f"the field id and {self.positional_value}" if self.positional_value else "the field id"
                raise TypeError(
                    f"{self.name}() takes only {what} positionally; pass the rest by name: "
                    f"{self.name}{self.signature}"
                )
            if "id" in kwargs:
                raise TypeError(f"{self.name}() got the id both positionally and as id=")
            if len(args) == 2:
                if self.positional_value in kwargs:
                    raise TypeError(
                        f"{self.name}() got {self.positional_value} both positionally and by name"
                    )
                kwargs = {self.positional_value: args[1], **kwargs}
            kwargs = {"id": args[0], **kwargs}
        unknown = [k for k in kwargs if k not in self.fields]
        if unknown:
            raise TypeError(
                f"{self.name}() got unexpected argument(s): {', '.join(unknown)}; "
                f"valid arguments: {', '.join(self.fields)}"
            )
        missing = [k for k in self.required if k not in kwargs]
        if missing:
            raise TypeError(
                f"{self.name}() missing required argument(s): {', '.join(missing)}; "
                f"usage: {self.name}{self.signature}"
            )
        return kwargs


OP_SPECS = [_OpSpec(cls) for cls in OP_CLASSES]


# --- API listing ------------------------------------------------------------


def _method_lines() -> list[str]:
    lines = []
    for name, fn in inspect.getmembers(FormMapQuery, inspect.isfunction):
        if name.startswith("_"):
            continue
        params = []
        for p in list(inspect.signature(fn).parameters.values())[1:]:
            text = p.name
            annotated = p.annotation is not inspect.Parameter.empty
            if annotated:
                text += f": {p.annotation}"
            if p.default is not inspect.Parameter.empty:
                text += f" = {p.default!r}" if annotated else f"={p.default!r}"
            params.append(text)
        doc = (inspect.getdoc(fn) or "").split("\n", 1)[0]
        lines.append(f"  fm.{name}({', '.join(params)})" + (f"  # {doc}" if doc else ""))
    return lines


def _schema_type(prop: dict) -> str:
    if "enum" in prop:
        return " | ".join(json.dumps(v) for v in prop["enum"])
    return prop.get("type", "any")


def _transform_lines() -> list[str]:
    lines = []
    for t in list_transforms():
        schema = t["args_schema"]
        required = set(schema.get("required", []))
        params = []
        for key, prop in schema.get("properties", {}).items():
            text = f"{key}: {_schema_type(prop)}"
            if key not in required:
                text += f" = {json.dumps(prop['default'])}" if "default" in prop else " (optional)"
            params.append(text)
        doc = t["doc"].split("\n", 1)[0]
        lines.append(f"  {t['name']}({', '.join(params)})  # {doc}")
    return lines


def api_text() -> str:
    """The compact reference the model reads via ``print(api())``."""
    nested: dict[str, type[BaseModel]] = {}
    for spec in OP_SPECS:
        for f in spec.fields.values():
            _nested_models(f.annotation, nested)
    parts = [
        "Namespace (variables persist between steps; the names below are restored every step):",
        "  fm           form map queries (below). Items are dicts with id, type, page and rect",
        "               [x0, y0, x1, y1] in PDF points, origin top-left.",
        "  schema       list of schema path dicts: path, type, description, enum, item_type, in_table",
        "  tpl          snapshot of the current template as a plain dict (read-only: editing it",
        "               changes nothing; use the edit ops)",
        "  transforms() list of value transforms as dicts: name, args_schema, doc",
        "  api()        this text; help(obj) shows one function's signature and doc",
        "  modules      re, math, statistics, itertools, collections, json are pre-imported;",
        f"               importable: {', '.join(sorted(ALLOWED_IMPORTS))}",
        "",
        "Form map queries:",
        *_method_lines(),
        "",
        "Edit ops: each call is recorded and applied after the step (in call order); errors from",
        "applying them are reported then. Each returns the field id; add_field/add_table assign",
        "a new id (f1, f2, ...) when none is given, so later calls in the step can use it.",
        *[f"  {spec.name}{spec.signature}" for spec in OP_SPECS],
        "",
        "Types used by the edit ops:",
        *[
            f"  {name} = {{{', '.join(_param_text(n, f) for n, f in model.model_fields.items())}}}"
            for name, model in nested.items()
        ],
        "",
        "Transforms (use as {\"op\": name, \"args\": {...}} in a Source's transforms or when):",
        *_transform_lines(),
    ]
    return "\n".join(parts)


# --- execution --------------------------------------------------------------


class _Capture:
    """``sys.stdout`` stand-in that keeps the first ``limit`` characters."""

    def __init__(self, limit: int):
        self.limit = limit
        self.parts: list[str] = []
        self.kept = 0
        self.total = 0

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            raise TypeError(f"write() argument must be str, not {type(s).__name__}")
        self.total += len(s)
        room = self.limit - self.kept
        if room > 0:
            part = s[:room]
            self.parts.append(part)
            self.kept += len(part)
        return len(s)

    def flush(self) -> None:
        pass

    def result(self) -> tuple[str, bool]:
        text = "".join(self.parts)
        if self.total <= self.limit:
            return text, False
        return text + f"\n... [output truncated: {self.total - self.limit} more characters]", True


def _format_error(exc: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename.startswith(_STEP_PREFIX)]
    text = "".join(traceback.format_exception_only(type(exc), exc))
    if frames:
        text = "Traceback (most recent call last):\n" + "".join(traceback.format_list(frames)) + text
    lines = [
        ln if len(ln) <= MAX_ERROR_LINE_CHARS else ln[:MAX_ERROR_LINE_CHARS] + " ..."
        for ln in text.rstrip("\n").splitlines()
    ]
    return "\n".join(lines[-MAX_ERROR_LINES:])


_FIELD_ID = re.compile(r"f(\d+)")


class Session:
    """One namespace plus the context it was built from."""

    def __init__(self, form_map: dict, schema: list[dict], template: dict, max_stdout: int):
        self.fm = FormMapQuery(form_map)
        self.schema = schema
        self.template = template
        self.max_stdout = max_stdout
        self.assigned: set[str] = set()  # ids handed out (or given) by add ops in this worker
        self.ops: list[dict] = []
        self.step = 0
        self.ns: dict[str, Any] = {}
        self.op_functions = {spec.name: self._op_function(spec) for spec in OP_SPECS}
        self.api_text = api_text()

    def update_template(self, template: dict) -> None:
        self.template = template

    def next_id(self) -> str:
        ids = [f.get("id") for f in self.template.get("fields", [])] + list(self.assigned)
        nums = [int(m.group(1)) for i in ids if isinstance(i, str) and (m := _FIELD_ID.fullmatch(i))]
        return f"f{max(nums, default=0) + 1}"

    def _op_function(self, spec: _OpSpec):
        def op(*args, **kwargs):
            kwargs = spec.bind(args, kwargs)
            if spec.creates and kwargs.get("id") is None:
                kwargs["id"] = self.next_id()
            ordered = {"op": spec.name, **({"id": kwargs["id"]} if "id" in kwargs else {}), **kwargs}
            try:
                record = json.loads(json.dumps(ordered))  # snapshot, and checks transportability
            except (TypeError, ValueError) as e:
                raise TypeError(
                    f"{spec.name}() arguments must be JSON values (numbers, strings, lists, dicts, "
                    f"True/False/None): {e}"
                ) from None
            if spec.creates and isinstance(record["id"], str):
                self.assigned.add(record["id"])
            self.ops.append(record)
            return record.get("id")

        op.__name__ = op.__qualname__ = spec.name
        op.__doc__ = spec.doc
        return op

    def _help(self, obj: Any = None) -> None:
        if obj is None:
            print(self.api_text)
            return
        try:
            sig = str(inspect.signature(obj))
        except (TypeError, ValueError):
            sig = ""
        name = getattr(obj, "__name__", type(obj).__name__)
        doc = inspect.getdoc(obj) or ""
        if doc.startswith(name + "("):  # the edit ops' docs start with their signature
            print(doc)
        else:
            print(f"{name}{sig}\n{doc}".rstrip())

    def _refresh_namespace(self) -> None:
        api = self.api_text
        user_builtins = dict(SAFE_BUILTINS, __import__=_restricted_import, help=self._help)
        self.ns.update(
            __builtins__=user_builtins,
            __name__="__sandbox__",
            fm=self.fm,
            schema=copy.deepcopy(self.schema),
            tpl=copy.deepcopy(self.template),
            transforms=list_transforms,
            api=lambda: api,
            **PRELOADED_MODULES,
            **self.op_functions,
        )

    def run(self, code: str) -> dict:
        self.ops = []
        self.step += 1
        filename = f"{_STEP_PREFIX}{self.step}>"
        linecache.cache[filename] = (len(code), None, code.splitlines(keepends=True), filename)
        self._refresh_namespace()
        capture = _Capture(self.max_stdout)
        error = None
        saved = sys.stdout
        sys.stdout = capture
        try:
            exec(compile(code, filename, "exec", dont_inherit=True), self.ns)
        except BaseException as e:  # noqa: BLE001 - SystemExit/KeyboardInterrupt from user code too
            error = _format_error(e)
        finally:
            sys.stdout = saved
        stdout, truncated = capture.result()
        return {"ok": True, "stdout": stdout, "truncated": truncated, "ops": self.ops, "error": error}


# --- main loop --------------------------------------------------------------


def _limit_memory(memory_mb: int) -> None:
    try:
        import resource

        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = memory_mb * 1024 * 1024
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ImportError, ValueError, OSError):
        pass  # not supported here (e.g. Windows, or macOS which rejects/ignores RLIMIT_AS)


def _handle(session: Session | None, message: dict) -> tuple[Session | None, dict]:
    cmd = message.get("cmd")
    if cmd == protocol.INIT:
        session = Session(message["form_map"], message["schema"], message["template"], message["max_stdout"])
        return session, {"ok": True}
    if session is None:
        return None, {"ok": False, "error": f"{cmd!r} before init"}
    if cmd == protocol.UPDATE_TEMPLATE:
        session.update_template(message["template"])
        return session, {"ok": True}
    if cmd == protocol.EXEC:
        return session, session.run(message["code"])
    return session, {"ok": False, "error": f"unknown command {cmd!r}"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="formcast.sandbox.worker")
    parser.add_argument("--memory-mb", type=int, default=1024)
    args = parser.parse_args(argv)

    # Keep the stdout pipe for protocol messages only; anything else written to
    # fd 1 or sys.stdout (outside a captured exec) goes to stderr.
    proto = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    _limit_memory(args.memory_mb)

    session: Session | None = None
    for line in sys.stdin.buffer:
        seq = None
        try:
            message = protocol.decode(line)
            seq = message.get("seq")
            session, response = _handle(session, message)
        except Exception as e:  # noqa: BLE001 - report, keep serving
            response = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        proto.write(protocol.encode({**response, "seq": seq}))


if __name__ == "__main__":
    main()
