"""The agent's system prompt, first user message and corrective messages.

Everything here is generic: no form names, labels or layouts from any corpus
form. Examples use made-up labels.
"""

from __future__ import annotations

import inspect

from formcast.geometry import Rect, intersect
from formcast.pagemap.query import FormMapQuery
from formcast.schema import SchemaPath
from formcast.template.model import Field, Template
from formcast.template.op_models import OP_NAMES

NO_TOOL_CALL = "Reply with a run_python or finish tool call."
TOKEN_LIMIT_NOTE = (
    " Your last reply hit the output token limit before it made a tool call: write shorter code "
    "and spread the work over several steps."
)

_FM_METHODS = ", ".join(
    f"fm.{name}"
    for name, _ in inspect.getmembers(FormMapQuery, inspect.isfunction)
    if not name.startswith("_")
)
_OPS = ", ".join(OP_NAMES)

SYSTEM_PROMPT = f"""\
# Role
You turn a blank PDF form into a field template for a data schema. A template lists every
place on the form where a value is written (a field: page, rect, type, label) and says which
schema path fills it (its source). Software later fills the form from data using only the
template, so each field must sit exactly on its blank and map the right path. You cannot see
the page image: you work from extracted text and geometry, by writing Python that runs in a
sandbox. Call `run_python` to run code and `finish` when you are done.

# Coordinates and the page map
- Rects are [x0, y0, x1, y1] in PDF points (1/72 inch), origin at the top-left of the
  unrotated page; y grows downward. Pages are numbered from 0.
- Ids: `c<page>_<n>` detected candidate, `l<page>_<n>` text line, `w<page>_<n>` existing PDF
  widget, `r<page>_<n>` rule (a drawn line or a run of underscores), `t<page>_<n>` word.
- Candidates come from a learned field detector: kind `text`, `choice` (a checkbox or radio
  button) or `signature`, with a confidence `conf`. Each may carry its linked label line and
  how it sits (left, above, inside, below).
- Summary notation (only in the form summary; query results don't carry these keys):
  `sub="A|B"` lists small captions printed just under a box (get them with
  fm.sublabels_under(id)); `right="..."` is the text starting just right of a box, often a
  choice box's option (get it with fm.right_of(id)); `+rule` marks a line followed by a rule
  (a blank to write on); `+rule_above` marks a caption printed right under a rule, as under a
  signature or date line (the blank is the rule above it).
- "Unlabeled prompts" are text lines that look like a request for input (ending in ":", "#"
  or "?", or marked `+rule` / `+rule_above`) but have no field box nearby: likely blanks the
  detector missed.
- Page kinds: `fillable` (has its own widgets; no detection was run), `digital` (real text
  layer) and `scanned` (text from OCR, which can be misspelled or misplaced).

# The sandbox
Variables persist between steps. Preloaded names:
- `fm`: read-only queries over the page map, returning plain dicts: {_FM_METHODS}.
- `schema`: the schema paths (path, type, description, enum, item_type, in_table).
- `tpl`: a read-only snapshot of the current template as a dict (refreshed every step).
- edit ops: {_OPS}. Each call is recorded and applied after your code finishes, in order;
  add_field and add_table return the new field id right away, so later calls can use it.
- `transforms()`: the value transforms usable in a source (date parts, splitting, joining,
  formatting, equals/contains conditions and more).
- `api()`: the full reference with signatures, also given at the end of the first message;
  `print(api())` shows it again and `help(fn)` shows one function. Check it instead of guessing
  signatures.
A source looks like {{"paths": ["a.b"], "transforms": [{{"op": "date_part", "args": {{"part": "month"}}}}]}};
several paths into one field add "join": " "; a choice box adds a "when" condition such as
{{"op": "equals", "args": {{"value": "yes"}}}}. Field types: text, checkbox, radio_option,
signature, choice_mark (a mark drawn on or next to printed option text) and table.

# Working method
1. The template starts with one unmapped field per existing widget (id `w<page>_<n>`) and per
   detector candidate (id `c<page>_<n>`): each field has the id of its item in the form summary.
   Work on these fields first: map each real blank with set_source and fix its label; move,
   resize or set_type it when the geometry says so; remove_field the ones that are not blanks
   (pre-printed content, decoration, duplicates). Reinterpret where needed: a short line printed
   right before option text is a box to mark (a checkbox), not a text blank. Use add_field only
   for blanks no field covers yet.
2. Add the fields the detector missed, using text cues: prompts ending in ":", "#" or "?", lines
   followed by a rule or empty space, captions printed under a rule (signature and date lines),
   and option words with an empty box or blank beside them. Use rules and nearby boxes for the
   exact geometry. A text field spans the writable area: from just after its label to the end
   of its rule or cell, with its bottom edge on the rule.
3. When one long line or box has small sub-labels under (or inside) it, split it into one field
   per sub-label, each spanning that sub-label's part of the line.
4. Mark choices: each option gets its own checkbox or radio_option field on its box, with a
   `when` condition on the schema path. For a yes/no boolean the "yes" box uses when equals true
   (or bool_to_check) and the "no" box when equals false. For "circle one" style options with no
   box, use a choice_mark field over the option text.
5. Build tables from repeated rows or grids of cells with add_table: rows from the repeated
   row geometry, columns from the cell edges or column headings; column keys are paths relative
   to one row. A table that continues on another page gets another segment. Records printed as
   repeated blocks (each record spans several lines, like stacked copies of the same set of
   blanks): add one table per line of the block, all with the same source_path, each with one
   segment per record block (max_rows=1, in record order), so row i of every table lands in
   block i. When table cells replace existing widget or candidate fields, remove exactly the
   fields the cells cover (the step report warns which ones they are).
6. Split blanks that hold parts of one value (separate boxes for month, day and year, or one
   box per character) and use transforms to fill each part.
7. Give every field a short label from its printed text. Keep a field for every blank the form
   asks the user to fill, even when no schema path fits (leave its source empty).
8. Map every schema path you can, shaping values to what the box asks for:
   - schema dates are ISO (YYYY-MM-DD): a date box needs date_format with the format the form
     shows (a printed hint such as mm/dd/yyyy, else the usual convention of the form's
     language), or date_part for separate month/day/year boxes;
   - a box whose label names several values (a full name as last, first and middle; a city,
     state and postal code line) takes all of those paths, in the printed order, with "join";
     a list of strings written into one box needs the join transform;
   - a box for an initial takes char_at 0; a box for part of a number (area code, a group of
     digits) takes slice.
   Never map a path to a field you cannot justify from nearby text.
9. Fillable forms: map the widget fields with set_source and fix their labels; do not add
   duplicates on top of them.

# The step report
After each run_python call you get a report: your printed output (truncated when long), errors,
the ops applied (with field ids), ops rejected with the reason, warnings (such as overlapping
fields), field and mapping counts, the schema paths still unmapped and the candidates no field
covers yet. Fix rejected ops using the reason. Use the unmapped paths and unused candidates to
pick the next work. Older steps are later shortened to one line, so keep anything you need in
Python variables, and read `tpl` for the current state.

# Finishing
Call `finish` when coverage can't improve any more: give a short summary, and list each
unplaceable schema path with the reason (for example: the form has no place for it). Before
that, search for every unmapped path's key words (fm.find_text) and look at the fields still
without a source: most forms have a place for most paths. Do not stop early to save effort; the
step report shows how much of the step budget is left.

# Do not
- Do not guess a position without evidence for it: candidates, widgets, rules, label and
  caption positions are evidence.
- Do not print whole page maps or long lists: filter and print only what you need.
- Keep steps small: a few related queries or a few dozen ops per step, then read the report.
"""


def _fmt_num(v: float) -> str:
    return f"{round(v, 1):g}"


def format_rect(rect: Rect) -> str:
    return "[" + ", ".join(_fmt_num(v) for v in rect) + "]"


def _schema_line(sp: SchemaPath) -> str:
    kind = sp.type
    if sp.type == "array" and sp.item_type:
        kind = f"array of {sp.item_type}" + (" (table)" if sp.item_type == "object" else "")
    parts = [f"- {sp.path}: {kind}"]
    if sp.enum:
        parts.append("values " + " | ".join(sp.enum))
    if sp.in_table:
        parts.append(f"in table {sp.in_table}")
    line = "; ".join(parts)
    if sp.description:
        line += f" — {' '.join(sp.description.split())}"
    return line


def _template_note(template: Template) -> str:
    fields = template.fields
    mapped = sum(1 for f in fields if f.source and f.source.paths)
    lines = [f"CURRENT TEMPLATE: {len(fields)} field(s), {mapped} with a source. `tpl` has the details."]
    lines.extend(_field_line(f) for f in fields[:60])
    if len(fields) > 60:
        lines.append(f"  ... {len(fields) - 60} more")
    return "\n".join(lines)


def _field_line(f: Field) -> str:
    where = f"page {f.page} {format_rect(f.rect)}" if f.rect is not None else f"{len(f.segments)} segment(s)"
    src = ",".join(f.source.paths) if f.source and f.source.paths else "-"
    widget = f" widget={f.existing_widget!r}" if f.existing_widget else ""
    return f"  {f.id} {f.type} {where} label={f.label!r} source={src}{widget}"


def _field_regions(f: Field) -> list[tuple[int, Rect]]:
    if f.page is not None and f.rect is not None:
        return [(f.page, f.rect)]
    return [(seg.page, seg.rect) for seg in f.segments]


def _region_fields(template: Template, page: int, rect: Rect) -> str:
    """Template fields whose box (or any table segment) overlaps the region."""
    hits = [
        f
        for f in template.fields
        if any(p == page and intersect(tuple(r), tuple(rect)) is not None for p, r in _field_regions(f))
    ]
    if not hits:
        return "TEMPLATE FIELDS IN THE REGION: none."
    return f"TEMPLATE FIELDS IN THE REGION ({len(hits)}):\n" + "\n".join(_field_line(f) for f in hits)


def starting_note(template: Template) -> str:
    """One paragraph on a fresh template's seeded fields (instead of listing them all)."""
    widgets = sum(1 for f in template.fields if f.origin == "widget")
    detected = sum(1 for f in template.fields if f.origin == "detector")
    if not widgets and not detected:
        return "STARTING TEMPLATE: empty (the form has no widgets and no detector candidates)."
    kinds = []
    if widgets:
        kinds.append(f"{widgets} for the existing widgets (ids w<page>_<n>)")
    if detected:
        kinds.append(f"{detected} for the detector candidates (ids c<page>_<n>)")
    return (
        f"STARTING TEMPLATE: {len(template.fields)} unmapped field(s), " + " and ".join(kinds) + ". "
        "Each field has the id of its item in the form map below and sits on its rect. Map, fix or "
        "remove them; `tpl` has the details."
    )


def first_user_message(
    summary_text: str,
    schema_paths: list[SchemaPath],
    instruction: str | None,
    region: tuple[int, Rect] | None,
    *,
    template: Template | None = None,
    fresh: bool = False,
    api_reference: str | None = None,
) -> str:
    """The opening user message: task, schema paths, optional instruction/region, form map.

    ``template`` is the starting template. A ``fresh`` one (seeded with a field per
    widget and candidate) gets a one-paragraph note; otherwise its fields are listed.
    """
    parts: list[str] = []
    if instruction:
        parts.append(
            "REVIEWER INSTRUCTION (make the changes it asks for, keep the rest of the template, "
            f"then call finish):\n{instruction.strip()}"
        )
    else:
        parts.append("Build the field template for this form and map the schema paths below.")
    if region is not None:
        page, rect = region
        parts.append(
            f"REGION: the reviewer highlighted page {page}, rect {format_rect(rect)}. Focus on the "
            f"template fields and the form content in or near it. fm.region({page}, "
            f"{format_rect(rect)}) lists the text lines, rules, candidates and widgets there."
        )
        if template is not None:
            parts.append(_region_fields(template, page, rect))
    if template is not None and fresh:
        parts.append(starting_note(template))
    elif template is not None and template.fields:
        parts.append(_template_note(template))
    lines = [_schema_line(sp) for sp in schema_paths]
    parts.append(f"SCHEMA PATHS ({len(schema_paths)}):\n" + ("\n".join(lines) if lines else "(none)"))
    parts.append(summary_text)
    if api_reference:
        parts.append("API REFERENCE (print(api()) shows it again):\n" + api_reference)
        parts.append("Work through the form step by step.")
    else:
        parts.append("Start with print(api()), then work through the form step by step.")
    return "\n\n".join(parts)


def no_tool_call_message(hit_token_limit: bool) -> str:
    return NO_TOOL_CALL + (TOKEN_LIMIT_NOTE if hit_token_limit else "")
