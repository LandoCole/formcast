"""Named, pure, chainable value transforms.

A template stores transforms as data (``Transform(op, args)``); ``apply_chain``
runs them over the source values of a field. Every transform is a plain function
``fn(value, **args)`` registered under a name together with a JSON Schema for its
args (so a chain can be validated without running it, and listed in an LLM prompt).

Chain semantics: ``apply_chain`` receives one value per source path. A transform
registered with ``takes_list=True`` (``join``) consumes that list. Any other
transform applies to the list's only element, or is mapped over the elements when
there are several. A source value that is itself a list (e.g. a multi-select) is
a single value, not something to map over.

Missing data: every built-in passes ``None`` through unchanged (and the date and
number ops also ``""``, which real records use for absent values) except ``default``
(replaces ``None`` and ``""``), ``bool_to_check`` (``None`` -> ``False``) and
``equals`` / ``contains`` (``None`` -> ``False``).
"""

from __future__ import annotations

import copy
import datetime
import functools
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

from formcast.errors import FormcastError


class Transform(BaseModel):
    """One step of a transform chain: an op name and its arguments."""

    model_config = ConfigDict(extra="forbid")

    op: str
    args: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Entry:
    fn: Callable[..., Any]
    schema: dict
    validator: jsonschema.protocols.Validator
    doc: str
    takes_list: bool


_REGISTRY: dict[str, _Entry] = {}


def register_transform(
    name: str,
    fn: Callable[..., Any],
    arg_schema: dict,
    *,
    takes_list: bool = False,
    replace: bool = False,
) -> None:
    """Register ``fn(value, **args)`` as transform ``name``.

    ``arg_schema`` is a JSON Schema (an object schema) validated against a
    transform's ``args``. Args are passed as keywords, so an arg named ``value``
    needs the input parameter to be positional-only (``def fn(item, /, *, value)``). The first line of ``fn.__doc__`` becomes the one-line
    doc shown by ``list_transforms``. With ``takes_list=True`` the function gets
    the whole list of source values instead of a single value. Re-registering an
    existing name is an error unless ``replace=True``.
    """
    if name in _REGISTRY and not replace:
        raise FormcastError(f"transform {name!r} is already registered")
    try:
        validator_cls = jsonschema.validators.validator_for(arg_schema, default=jsonschema.Draft202012Validator)
        validator_cls.check_schema(arg_schema)
    except jsonschema.SchemaError as e:
        raise FormcastError(f"invalid arg_schema for transform {name!r}: {e.message}") from e
    doc = ((fn.__doc__ or "").strip().splitlines() or [""])[0].strip()
    _REGISTRY[name] = _Entry(fn, copy.deepcopy(arg_schema), validator_cls(arg_schema), doc, takes_list)


def validate_chain(chain: list[Transform]) -> list[str]:
    """Return a list of error messages (empty when the chain is valid)."""
    errors: list[str] = []
    for step in chain:
        entry = _REGISTRY.get(step.op)
        if entry is None:
            errors.append(f"unknown transform {step.op!r}")
            continue
        for err in sorted(entry.validator.iter_errors(step.args), key=lambda e: list(e.absolute_path)):
            where = "".join(f"[{p!r}]" for p in err.absolute_path)
            errors.append(f"bad args for transform {step.op!r}{' at args' + where if where else ''}: {err.message}")
    return errors


def apply_chain(values: list[Any], chain: list[Transform]) -> Any:
    """Run ``chain`` over the source ``values`` and return the result.

    Raises FormcastError if the chain is invalid or a transform rejects a value.
    Returns ``None`` when there are no source values at all; a single resulting
    value is returned bare, several are returned as a list.
    """
    errors = validate_chain(chain)
    if errors:
        raise FormcastError("; ".join(errors))

    current: Any = list(values)
    multi = True  # ``current`` is the list of source values (vs. one value)
    for step in chain:
        entry = _REGISTRY[step.op]
        if entry.takes_list:
            if multi:
                # a lone source value that is itself a list (e.g. multi-select) is what gets joined
                lone_list = len(current) == 1 and isinstance(current[0], list)
                current = _call(step, entry, current[0] if lone_list else current)
            else:
                current = _call(step, entry, current if isinstance(current, list) else [current])
            multi = False
        elif multi and len(current) == 1:
            current, multi = _call(step, entry, current[0]), False
        elif multi:
            current = [_call(step, entry, v) for v in current]
        else:
            current = _call(step, entry, current)

    if multi:
        if not current:
            return None
        return current[0] if len(current) == 1 else current
    return current


def _call(step: Transform, entry: _Entry, value: Any) -> Any:
    try:
        return entry.fn(value, **step.args)
    except FormcastError:
        raise
    except Exception as e:  # a custom transform misbehaving must not crash callers with a bare error
        raise FormcastError(f"transform {step.op!r} failed on {value!r}: {e}") from e


def list_transforms() -> list[dict]:
    """Registered transforms as ``{"name", "args_schema", "doc"}`` dicts (for the system prompt)."""
    return [
        {"name": name, "args_schema": copy.deepcopy(e.schema), "doc": e.doc}
        for name, e in _REGISTRY.items()
    ]


# --- helpers ---------------------------------------------------------------


def _nullsafe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Make ``fn(value, ...)`` return None for a None value; keep its docstring."""

    @functools.wraps(fn)
    def wrapper(value: Any, **args: Any) -> Any:
        return None if value is None else fn(value, **args)

    return wrapper


def _blanksafe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Like _nullsafe, and also pass "" through unchanged (absent data in real records)."""

    @functools.wraps(fn)
    def wrapper(value: Any, **args: Any) -> Any:
        return value if value is None or value == "" else fn(value, **args)

    return wrapper


def to_text(value: Any) -> str:
    """Render a scalar as text; lists and dicts are an error (they need a transform first)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        raise FormcastError(f"expected text but got {type(value).__name__}: {value!r}")
    return str(value)


_text = to_text  # historical private name, kept so existing callers keep working


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _to_date(value: Any) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    raise FormcastError(f"invalid date {value!r}: expected an ISO 'YYYY-MM-DD' string or datetime.date")


_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise FormcastError(f"invalid number {value!r}")
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, float):
            d = Decimal(repr(value))
        elif isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
            d = Decimal(value.strip())
        else:
            raise FormcastError(f"invalid number {value!r}")
    except InvalidOperation as e:
        raise FormcastError(f"invalid number {value!r}") from e
    if not d.is_finite():
        raise FormcastError(f"invalid number {value!r}")
    return d


_FALSE_WORDS = {"", "false", "no", "n", "0", "off"}


# --- built-in transforms ---------------------------------------------------


@_nullsafe
def _split(value: Any, sep: str, index: int) -> str:
    """Split text on `sep` and take part number `index` (0-based, negative allowed; "" if absent)."""
    parts = _text(value).split(sep)
    return parts[index] if -len(parts) <= index < len(parts) else ""


@_nullsafe
def _slice(value: Any, start: int = 0, end: int | None = None) -> Any:
    """Take characters (or list items) from `start` up to but not including `end`."""
    if isinstance(value, (list, tuple)):
        return list(value[start:end])
    return _text(value)[start:end]


@_nullsafe
def _char_at(value: Any, index: int) -> str:
    """Take the single character at `index` (0-based, negative allowed; "" if out of range)."""
    s = _text(value)
    return s[index] if -len(s) <= index < len(s) else ""


def _join(values: list[Any], sep: str) -> str | None:
    """Join all source values with `sep`, skipping missing and empty ones (None if all are)."""
    parts = [_text(v) for v in values if v is not None and v != ""]
    return sep.join(parts) if parts else None


@_nullsafe
def _upper(value: Any) -> str:
    """Convert text to UPPER CASE."""
    return _text(value).upper()


@_nullsafe
def _lower(value: Any) -> str:
    """Convert text to lower case."""
    return _text(value).lower()


@_nullsafe
def _title(value: Any) -> str:
    """Convert text to Title Case (each word and hyphenated part capitalized)."""
    return re.sub(r"[^\s\-]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), _text(value))


@_nullsafe
def _strip(value: Any) -> str:
    """Remove leading and trailing whitespace."""
    return _text(value).strip()


@_blanksafe
def _date_format(value: Any, format: str) -> str:
    """Format an ISO date (YYYY-MM-DD) with a strftime pattern, e.g. "%m/%d/%Y"."""
    return _to_date(value).strftime(format)


@_blanksafe
def _date_part(value: Any, part: str, pad: int = 0) -> str:
    """Take month, day, year (4 digits) or year2 (2 digits) of an ISO date, zero-padded to `pad`."""
    d = _to_date(value)
    number = {"month": d.month, "day": d.day, "year": d.year, "year2": d.year % 100}[part]
    width = max(pad, 2 if part == "year2" else 0)
    return str(number).zfill(width)


@_blanksafe
def _number_format(value: Any, decimals: int = 2, thousands: str = ",", prefix: str = "") -> str:
    """Format a number with fixed `decimals` (rounded half up), a thousands separator and a prefix."""
    d = _to_decimal(value)
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, len(d.as_tuple().digits) + decimals + 10)
        try:
            q = d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
        except InvalidOperation as e:
            raise FormcastError(f"cannot format number {value!r}") from e
    whole, _, frac = f"{abs(q):f}".partition(".")
    out = f"{int(whole):,}".replace(",", thousands) + (f".{frac}" if frac else "")
    return ("-" if q < 0 else "") + prefix + out


def bool_to_check(value: Any) -> bool:
    """True to tick a checkbox for a truthy value; false, no, 0, empty and missing leave it unticked."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_WORDS
    return bool(value)


_bool_to_check = bool_to_check  # historical private name


def _equals(item: Any, /, *, value: Any) -> bool:
    """True when the input equals `value` exactly (for ticking a checkbox on a matching option)."""
    return False if item is None else item == value


def _contains(item: Any, /, *, value: Any) -> bool:
    """True when `value` is an item of the input list or a substring of the input text."""
    if item is None:
        return False
    if isinstance(item, str):
        return isinstance(value, str) and value in item
    if isinstance(item, (list, tuple, set, dict)):
        return value in item
    raise FormcastError(f"contains expects a list or text but got {type(item).__name__}: {item!r}")


def _default(item: Any, /, *, value: Any) -> Any:
    """Use `value` when the input is missing or an empty string."""
    return value if item is None or item == "" else item


def _decorate(value: Any, text: str, before: bool) -> Any:
    if value is None or value == "":
        return value  # blank stays blank so decorations don't print on empty fields
    return text + _text(value) if before else _text(value) + text


def _prefix(item: Any, /, *, value: str) -> Any:
    """Put fixed text before the input (blank input stays blank)."""
    return _decorate(item, value, True)


def _suffix(item: Any, /, *, value: str) -> Any:
    """Put fixed text after the input (blank input stays blank)."""
    return _decorate(item, value, False)


def _obj(properties: dict[str, dict], required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_INT = {"type": "integer"}
_STR = {"type": "string"}
_ANY: dict = {}

for _name, _fn, _schema, _kw in [
    ("split", _split, _obj({"sep": {"type": "string", "minLength": 1}, "index": _INT}, ("sep", "index")), {}),
    ("join", _join, _obj({"sep": _STR}, ("sep",)), {"takes_list": True}),
    ("slice", _slice, _obj({"start": {**_INT, "default": 0}, "end": _INT}), {}),
    ("char_at", _char_at, _obj({"index": _INT}, ("index",)), {}),
    ("upper", _upper, _obj({}), {}),
    ("lower", _lower, _obj({}), {}),
    ("title", _title, _obj({}), {}),
    ("strip", _strip, _obj({}), {}),
    ("date_format", _date_format, _obj({"format": {"type": "string", "minLength": 1}}, ("format",)), {}),
    (
        "date_part",
        _date_part,
        _obj(
            {
                "part": {"enum": ["month", "day", "year", "year2"]},
                "pad": {"type": "integer", "minimum": 0, "default": 0},
            },
            ("part",),
        ),
        {},
    ),
    (
        "number_format",
        _number_format,
        _obj(
            {
                "decimals": {"type": "integer", "minimum": 0, "maximum": 12, "default": 2},
                "thousands": {"type": "string", "default": ","},
                "prefix": {"type": "string", "default": ""},
            }
        ),
        {},
    ),
    ("bool_to_check", _bool_to_check, _obj({}), {}),
    ("equals", _equals, _obj({"value": _ANY}, ("value",)), {}),
    ("contains", _contains, _obj({"value": _ANY}, ("value",)), {}),
    ("default", _default, _obj({"value": _ANY}, ("value",)), {}),
    ("prefix", _prefix, _obj({"value": _STR}, ("value",)), {}),
    ("suffix", _suffix, _obj({"value": _STR}, ("value",)), {}),
]:
    register_transform(_name, _fn, _schema, **_kw)
del _name, _fn, _schema, _kw
