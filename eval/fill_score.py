"""Fill correctness (``fill_acc``): fill a template with probe records and check each GT box.

The mapping scorer credits a box when its rect and schema path are right; it can't see where
data really lands (a table built as one segment per column flows rows down the first column's
segment, then into the next). This scorer fills the template with the distinctive probe records
of :mod:`probe` (fill design §6), reads back what the filled PDF's widgets show, and checks it
against every ground-truth box with a path:

* units: a (text box, record) pair, a (table-cell box, record) pair, and a choice box scored
  once across all records (right state in every record);
* observed: only widgets that a mapped template field or cell wrote (the report names them by
  widget name and rect), read from the output PDF; a checkbox or radio kid's state is its /AS;
* matching: per record, observed widgets to all GT boxes with ``score._match`` (one-to-one IoU
  >= 0.3 on the same page, then the multi-line rule for unmatched GT text boxes without format
  transforms); a GT box with no widget is wrong;
* a text box without GT transforms ("join" aside) must show the value (casefolded, whitespace
  collapsed; a number in any number formatting); with ``also_paths`` or a list value, every own
  value must show and other words must be in no record value (``_join_ok``); a box with GT
  transforms is checked by provenance (``_provenance_ok``);
* ``rows_dropped_ok``: every table whose probe rows exceed its GT rows reports exactly that
  excess as ``rows_dropped`` (None when no table has an excess).

A ``fill`` that raises makes every unit of that record wrong; its message goes to ``fill_errors``.
"""

from __future__ import annotations

import calendar
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # eval scripts do not rely on the editable install

import pymupdf  # noqa: E402

import score  # noqa: E402
from formcast.geometry import Rect  # noqa: E402
from formcast.render.fill import fill  # noqa: E402
from formcast.render.report import FillReport  # noqa: E402
from formcast.schema import ARRAY_ITEM, flatten_schema, format_path, is_array_item, parse_path  # noqa: E402
from formcast.template.model import Template  # noqa: E402
from probe import free_text_options, gt_rows, table_rows_in_gt  # noqa: E402

IOU_THR = 0.3
#: A report's widget rect and the PDF widget's rect agree within this many points.
RECT_TOLERANCE = 1.0
_BUTTONS = (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON)
_WORD = re.compile(r"[^\W_]+")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTH_NAMES = {
    name.casefold(): m
    for m in range(1, 13)
    for name in (calendar.month_name[m], calendar.month_abbr[m], *(["Sept"] if m == 9 else []))
}


@dataclass
class Observed:
    page: int
    rect: Rect
    name: str | None
    text: str | None
    on: bool | None
    kind: Literal["text", "choice"]
    #: Beyond the brief's fields, for the multi-line rule (``score.multiline_flags``):
    cell: bool = False  # written by a table cell (cells are never multi-line fields)
    multiline: bool = False  # a text widget with the multi-line flag


# ---------------------------------------------------------------- reading back


def read_written(pdf_bytes: bytes, report: FillReport) -> list[Observed]:
    """The widgets of ``pdf_bytes`` that a mapped template field or table cell wrote, per ``report``.

    A widget is picked by the name and rect the report gives (page from the field or cell),
    once even when several targets share it. Text, combo and list widgets give ``text`` (their
    field value; "" when empty); check boxes and radio buttons give ``on`` from the widget's own
    /AS (a kid's field value is the whole field's).
    """
    wanted: dict[tuple[int, str], list[tuple[Rect, bool]]] = {}
    for f in report.fields:
        if f.paths and f.widget is not None and f.widget_rect is not None and f.page is not None:
            wanted.setdefault((f.page, f.widget), []).append((f.widget_rect, False))
    for t in report.tables:
        if not t.path:
            continue  # an unmapped table writes no data
        for c in t.cells:
            if c.widget is not None and c.widget_rect is not None:
                wanted.setdefault((c.page, c.widget), []).append((c.widget_rect, True))
    out: list[Observed] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            for w in page.widgets() or []:
                rect = (w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1)
                hits = [cell for r, cell in wanted.get((page.number, w.field_name), []) if _same_rect(r, rect)]
                if not hits:
                    continue
                if w.field_type in _BUTTONS:
                    kind, state = doc.xref_get_key(w.xref, "AS")
                    out.append(Observed(page.number, rect, w.field_name, None, kind == "name" and state != "/Off",
                                        "choice", cell=all(hits)))  # fmt: skip
                else:
                    multiline = bool((w.field_flags or 0) & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
                    text = w.field_value if isinstance(w.field_value, str) else ""
                    out.append(Observed(page.number, rect, w.field_name, text, None, "text", cell=all(hits),
                                        multiline=multiline))  # fmt: skip
    finally:
        doc.close()
    return out


def _same_rect(a: Rect, b: Rect) -> bool:
    return all(abs(x - y) <= RECT_TOLERANCE for x, y in zip(a, b))


# ---------------------------------------------------------------- record values


def _value_at(record: Any, path: str, row: int | None) -> Any:
    """The record's value at a schema path; the first ``[]`` takes item ``row`` (None past the end)."""
    cur = record
    used_row = False
    for seg in parse_path(path):
        if is_array_item(seg):
            if used_row or not isinstance(cur, list):
                return cur if isinstance(cur, list) else None  # a nested array: the whole list
            if row is None or row >= len(cur):
                return None
            cur, used_row = cur[row], True
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def _leaves(value: Any, segments: tuple = (), row: int | None = None):
    """(schema path, row, scalar) of every scalar in a record; the row is the item index in the
    outermost array."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, (*segments, k), row)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, (*segments, ARRAY_ITEM), i if row is None else row)
    elif value is not None:
        yield format_path(list(segments)), row, value


def _is_own(path: str, row: int | None, own_paths: list[str], own_row: int | None) -> bool:
    """Whether a record leaf belongs to a box's own value: one of its paths (or an item under it),
    in the box's row for a table-cell path."""
    for o in own_paths:
        if path == o or path.startswith(o + "[]") or path.startswith(o + "."):
            if "[]" not in o or row == own_row:
                return True
    return False


def _others(leaves: list, shared: set[str], own_paths: list[str], row: int | None) -> list[str]:
    """Casefolded text of every distinctive record value that isn't the box's own."""
    return [str(v).casefold() for p, r, v in leaves if p not in shared and not _is_own(p, r, own_paths, row)]


def _texts(value: Any) -> list[str]:
    """A value as display text, one per scalar (lists: one per item); missing values give none."""
    if value is None:
        return []
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    text = value if isinstance(value, str) else str(value)
    return [text] if text.strip() else []


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _words(texts: list[str]) -> Counter:
    return Counter(w for t in texts for w in _WORD.findall(t.casefold()))


# ---------------------------------------------------------------- per-box checks


def _date_parts(texts: list[str]) -> list[tuple[int, int, int]]:
    return [tuple(int(x) for x in m.groups()) for t in texts if (m := _ISO_DATE.match(t))]


def _date_pieces(date: tuple[int, int, int]) -> dict[str, set[str]]:
    """The digit strings a date part can be written as: month and day 1-2 digits, year 4 or 2."""
    y, m, d = date
    return {"m": {str(m), f"{m:02d}"}, "d": {str(d), f"{d:02d}"}, "y": {f"{y:04d}", f"{y % 100:02d}"}}


def _date_splits(word: str, date: tuple[int, int, int], parts: frozenset[str]) -> list[frozenset[str]]:
    """Each set of ``parts`` of ``date`` that ``word`` is written as exactly: a month name, or a
    digit word that splits entirely into distinct parts in any order ("05141987", "051487")."""
    month = _MONTH_NAMES.get(word)
    if month is not None:
        return [frozenset("m")] if month == date[1] and "m" in parts else []
    if not word.isdigit():
        return []
    pieces = _date_pieces(date)
    out: list[frozenset[str]] = []

    def split(rest: str, left: frozenset[str], used: frozenset[str]) -> None:
        if not rest:
            if used and used not in out:
                out.append(used)
            return
        for part in left:
            for text in pieces[part]:
                if rest.startswith(text):
                    split(rest[len(text) :], left - {part}, used | {part})

    split(word, parts, frozenset())
    return out


def _from_date(word: str, dates: list[tuple[int, int, int]]) -> bool:
    """A word that is a day, month (number or name) or year (4 or 2 digits) of one of ``dates``,
    or several of them written together without separators."""
    return any(_date_splits(word, d, frozenset("mdy")) for d in dates)


def _provenance_ok(observed: str, own_values: list[Any], others) -> bool:
    """Fill design §6.3: a non-empty box whose every word comes from its own value(s), and no word
    of 3+ characters that comes from another path's value in the record.

    A word comes from the own value when it is a whole part of it (a word of its text, a day, month
    or year of an own date in any format, also run together as in "05141987", a thousands group of
    an own number, or zeros: a number's decimals) or a fragment of it (a substring, casefolded). Generated values can share a whole
    part (two dates with one year), so only a fragment is checked against the other paths' values
    (``others()``: their casefolded texts).
    """
    words = _WORD.findall(observed.casefold())
    if not words:
        return False
    own = [t for v in own_values for t in _texts(v)]
    numbers = [v for v in own_values if _is_number(v)]
    whole = set(_words(own)) | set(_words([f"{n:,}" for n in numbers]))
    dates = _date_parts(own)
    own_cf = [t.casefold() for t in own]
    for w in words:
        if w in whole or _from_date(w, dates) or (numbers and set(w) == {"0"}):
            continue
        if not any(w in t for t in own_cf):
            return False
        if len(w) >= 3 and any(w in o for o in others()):
            return False
    return True


def _same_number(text: str, value: Any) -> bool:
    """``text`` is ``value`` written as a number (currency sign, thousands commas and decimals allowed)."""
    if not _is_number(value):
        return False
    try:
        return Decimal(text.strip().lstrip("$€£").strip().replace(",", "")) == Decimal(str(value))
    except InvalidOperation:
        return False


def _take(bag: Counter, words: Counter) -> bool:
    """Remove ``words`` from ``bag`` when all are there."""
    if any(bag[w] < n for w, n in words.items()):
        return False
    bag.subtract(words)
    return True


def _take_date(bag: Counter, date: tuple[int, int, int]) -> bool:
    """Remove words that write ``date``'s day, month and year (any format, any order) from ``bag``."""

    def search(left: frozenset[str]) -> bool:
        if not left:
            return True
        for w in [w for w, n in bag.items() if n > 0]:
            for used in _date_splits(w, date, left):
                bag[w] -= 1
                if search(left - used):
                    return True
                bag[w] += 1
        return False

    return search(frozenset("mdy"))


def _take_value(bag: Counter, value: Any) -> bool:
    """Remove the words that show ``value`` from ``bag``: its own words, else (a date) its day,
    month and year in any format, else (a number) its thousands groups; a list item by item."""
    if isinstance(value, list):
        return all(_take_value(bag, v) for v in value)
    text = _texts(value)
    if not text or _take(bag, _words(text)):
        return True
    dates = _date_parts(text)
    if dates:
        return _take_date(bag, dates[0])
    if _is_number(value):
        return _take(bag, _words([f"{value:,}"]))
    return False


def _join_ok(observed: str, own_values: list[Any], record_words) -> bool:
    """A join (or list) box: every own value shows (``_take_value``), and every other word is
    decoration found in no record value (a separator like "on", a number's decimal zeros)."""
    bag = _words([observed])
    if not all(_take_value(bag, v) for v in own_values):
        return False
    numeric = any(_is_number(v) for v in own_values)
    extra = [w for w, n in bag.items() if n > 0 and not (numeric and set(w) == {"0"})]
    return not set(extra) & record_words()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_ops(g: score.GTField) -> list[str]:
    """GT transforms that format a value ("join" only combines values)."""
    return [op for op in g.transforms if op != "join"]


def _text_ok(g: score.GTField, observed: Observed | None, own_values: list[Any], others, record_words) -> bool:
    """Whether a text or cell box shows its own value(s). ``others`` gives the other paths'
    casefolded texts (provenance) and ``record_words`` every word of the record (joins); both are
    callables, computed only when needed."""
    if observed is None or observed.text is None:
        return False
    own = [t for v in own_values for t in _texts(v)]
    if not own:
        return not observed.text.strip()  # nothing expected: the box must be empty
    if _format_ops(g):
        return _provenance_ok(observed.text, own_values, others)
    if len(own_values) == 1 and not isinstance(own_values[0], list):
        # a number is its value in any number formatting (GT can leave number_format unlabelled)
        return _norm(observed.text) == _norm(own[0]) or _same_number(observed.text, own_values[0])
    return _join_ok(observed.text, own_values, record_words)


def _same(a: Any, b: Any) -> bool:
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def expected_on(g: score.GTField, value: Any) -> bool:
    """Whether choice box ``g`` should be ticked for a record whose value at its path is ``value``."""
    if g.value is None:
        return bool(value)
    if isinstance(value, list):
        return any(_same(g.value, v) for v in value)
    return _same(g.value, value)


# ---------------------------------------------------------------- scoring


def _unit_kind(g: score.GTField) -> str:
    if g.type == "choice":
        return "choice"
    return "cell" if any(is_array_item(s) for s in parse_path(g.path)) else "text"


def _shared_paths(template: Template, gt: list[score.GTField]) -> set[str]:
    """Paths whose values aren't distinctive (given, not generated): enums, multi-selects,
    booleans and Ruling P free-text options. They are left out of the provenance "other path" check."""
    paths = flatten_schema(template.schema_)
    out = {p.path for p in paths if p.enum or p.type in ("boolean", "enum") or p.item_type in ("boolean", "enum")}
    out |= set(free_text_options(gt, paths))
    return out | {p + "[]" for p in out}


def score_fill(gt: list[score.GTField], template: Template, pdf: str | Path | bytes, records: list[dict]) -> dict:
    """Fill ``template`` into ``pdf`` with each probe record and score what lands in every GT box
    with a path (see the module docstring). Every fill runs with ``check_fingerprint=False``.

    ``static_choice`` counts choice boxes whose expected state is the same in every record: they
    can't tell an always-on (or never-on) box from a right one.

    Raises:
        ValueError: when ``records`` is empty (every choice box would score right).
    """
    if not records:
        raise ValueError("score_fill needs at least one probe record")
    pdf_bytes = pdf if isinstance(pdf, bytes) else Path(pdf).read_bytes()
    rows = gt_rows(gt)
    gt_table_rows = table_rows_in_gt(gt)
    shared = _shared_paths(template, gt)
    scored = [i for i, g in enumerate(gt) if g.path]
    kinds = {i: _unit_kind(gt[i]) for i in scored}
    counts = {k: {"units": 0, "correct": 0} for k in ("text", "choice", "cell")}
    choice_right = {i: True for i in scored if kinds[i] == "choice"}
    choice_states: dict[int, set[bool]] = {i: set() for i in choice_right}
    errors: list[str] = []
    dropped_ok: bool | None = None
    match_cache: dict[tuple, dict[int, int]] = {}

    for k, rec in enumerate(records):
        try:
            out, report = fill(template, pdf_bytes, rec, check_fingerprint=False)
        except Exception as e:  # noqa: BLE001 - any failure makes this record's units wrong
            msg = f"{type(e).__name__}: {e}"
            if msg not in errors:
                errors.append(msg)
            out = report = None
        observed = read_written(out, report) if report is not None else []
        matched = _matched(gt, observed, match_cache)
        leaves = list(_leaves(rec))
        words = _lazy(lambda leaves=leaves: set(_words([str(v) for _, _, v in leaves])))
        for i in scored:
            g = gt[i]
            obs = observed[matched[i]] if i in matched else None
            row = rows.get(i)
            if kinds[i] == "choice":
                want = expected_on(g, _value_at(rec, g.path, row))
                choice_states[i].add(want)
                choice_right[i] &= obs is not None and obs.on is not None and obs.on == want
                continue
            own_paths = [g.path, *g.also_paths]
            own_values = [_value_at(rec, p, row) for p in own_paths]
            others = lambda own=own_paths, row=row: _others(leaves, shared, own, row)  # noqa: E731
            counts[kinds[i]]["units"] += 1
            counts[kinds[i]]["correct"] += _text_ok(g, obs, own_values, others, words)
        dropped_ok = _rows_dropped_check(rec, report, gt_table_rows, dropped_ok)

    counts["choice"] = {"units": len(choice_right), "correct": sum(choice_right.values())}
    units = sum(c["units"] for c in counts.values())
    correct = sum(c["correct"] for c in counts.values())
    return {
        "units": units,
        "correct": correct,
        "fill_acc": score._ratio(correct, units),
        **counts,
        "static_choice": sum(len(states) == 1 for states in choice_states.values()),
        "rows_dropped_ok": dropped_ok,
        "fill_errors": errors,
    }


def _lazy(make):
    """A callable computing ``make()`` once, on first call."""
    cache: list = []

    def get():
        if not cache:
            cache.append(make())
        return cache[0]

    return get


def _matched(gt: list[score.GTField], observed: list[Observed], cache: dict) -> dict[int, int]:
    """GT index -> observed index, cached per widget set: ``score._match`` (IoU, then the
    multi-line rule), keeping multi-line cover only for GT boxes without format transforms, so one
    tall box showing a whole date can't stand in for stacked date-part boxes."""
    key = tuple((o.page, o.rect, o.kind, o.cell, o.multiline) for o in observed)
    if key not in cache:
        boxes = [(o.page, o.rect) for o in observed]
        items = [score._Item(o.kind, o.page, o.rect, [], [], is_cell=o.cell, multiline_style=o.multiline)
                 for o in observed]  # fmt: skip
        by_iou = dict(score._greedy_match([(g.page, g.rect) for g in gt], boxes, IOU_THR))
        full = score._match(gt, boxes, IOU_THR, multiline=score.multiline_flags(gt, items))
        cache[key] = {gi: pi for gi, pi in full.items() if gi in by_iou or not _format_ops(gt[gi])}
    return cache[key]


def _rows_dropped_check(rec: dict, report: FillReport | None, gt_table_rows: dict[str, int],
                        so_far: bool | None) -> bool | None:  # fmt: skip
    """``so_far`` and-ed with this record's check: each table with more probe rows than GT rows
    reports the excess as ``rows_dropped`` (every report table on that path; none at all fails)."""
    for table, n_gt in gt_table_rows.items():
        provided = _value_at(rec, table, None)
        excess = (len(provided) if isinstance(provided, list) else 0) - n_gt
        if excess <= 0:
            continue
        tables = [t for t in report.tables if t.path == table] if report is not None else []
        ok = bool(tables) and all(t.rows_dropped == excess for t in tables)
        so_far = ok if so_far is None else so_far and ok
    return so_far
