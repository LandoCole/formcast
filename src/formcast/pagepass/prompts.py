"""The page-pass prompt: one page's image, its marks and its text map, as messages for the model."""

from __future__ import annotations

from formcast.llm import ImagePart, Message, TextPart
from formcast.pagemap.model import FormMap
from formcast.pagemap.summarize import summarize_page
from formcast.required_rules import REQUIRED_RULES
from formcast.schema import flatten_schema
from formcast.template.model import Template
from formcast.transforms import _REGISTRY

SYSTEM_PROMPT = """\
You map the boxes of one page of a blank form to the paths of a data schema. Software will later
fill the form from a data record using only your mapping, so each box must get the schema path(s)
whose value belongs in it, shaped the way the box asks for it.

Rules:
- Only map a box you can justify from the form's printed text. Leave a box unmapped when no schema
  path fits it, or when it is not a real blank.
- Choices: each option's checkbox gets the schema path with a `when` condition, e.g.
  {"op": "equals", "args": {"value": "yes"}} for one value of an enum, {"op": "contains", "args":
  {"value": "x"}} for one item of a multi-select list. For a yes/no boolean, the "yes" box uses
  when equals true (or the bool_to_check transform) and the "no" box when equals false.
- Schema dates are ISO (YYYY-MM-DD): a date box needs date_format with the format the form shows,
  or date_part for separate month/day/year boxes.
- A box whose label names several values (a full name, a city/state/ZIP line) takes all of those
  paths in printed order, with "join": " ". A list of strings written into one box needs the join
  transform.
- A box for an initial takes char_at 0; a box for part of a number (area code, a group of digits)
  takes slice.
- Tables: a schema path that is a list of records ("array of object (table)"; its columns appear as
  "x[].col ... in table x") is filled through a table when the page prints rows for those records.
  Give each such table under "tables": the list path, its columns (key = the path after "x[].", with
  transforms like a mapping's), and one row per printed row naming that row's box for each column
  (leave out a column the row has no box for). A box used in a table must not also be in "mappings".
  Records printed as repeated blocks (each record spans several printed lines, like stacked copies of
  the same blanks): one table per printed line of the block, all with the same list path, each with
  one row per record block, in record order. Every printed line of a record block that has boxes gets
  its own table; a block is done only when every line with boxes for that record is in a table.
  A record value split over several boxes on one printed line (an area code and a number, a month and
  a year) gets one table per part, all with the same list path and column key, each column with the
  transform that picks its part (slice, date_part).
  A table's rows are only the printed rows meant for record values: leave out boxes in a heading row,
  a row of column letters or numbers, and a totals row. A box drawn in the empty space of a column
  heading cell (under the heading's text) is not a row: the first row starts below the heading's
  bottom rule.

Required and optional: besides the mapping, list the boxes of this page that a filled copy of the form must
have filled in. Every box you do not list is optional. List a required box whether or not you map it, with a
short reason (the form's own words, or the rule you applied).
""" + REQUIRED_RULES + """\
A box in a table may be listed too: one listed box makes the whole table required.

Reply with one JSON object and nothing else:
{"mappings": [{"id": "<box id>", "source": {"paths": ["a.b"], "transforms": [{"op": "...", "args": {...}}],
 "join": null, "when": null}}],
 "tables": [{"source_path": "x", "columns": [{"key": "col", "transforms": []}], "rows": [{"col": "<box id>"}]}],
 "required": [{"id": "<box id>", "reason": "..."}]}
List only boxes you map in "mappings" ("tables" and "required" may be empty). Use only box ids from the list given, and only
schema paths from the list given.
"""

_NOTE_START = "The image shows this page with every box outlined (blue: text box, green: checkbox). "
_NOTE_END = (
    " Use the image to understand the form as a person reading it would; use the page map for exact "
    "wording. Answer with full box ids."
)
IMAGE_NOTE = (
    _NOTE_START
    + "The number drawn in or next to a box is the part of its id after the underscore: on this page {example}."
    + _NOTE_END
)
# When two boxes would share a mark, the marks are the full ids.
IMAGE_NOTE_FULL_IDS = _NOTE_START + "The text drawn in or next to a box is its id: on this page {example}." + _NOTE_END


def transforms_ref() -> str:
    """One line per registered transform: name, argument names and the start of its doc."""
    lines = []
    for name, e in _REGISTRY.items():
        args = ", ".join(e.schema.get("properties", {})) or "no args"
        lines.append(f"- {name}({args}): {' '.join(e.doc.split())[:160]}")
    return "\n".join(lines)


def page_messages(
    template: Template, form_map: FormMap, page: int, image: bytes, marks: dict[str, str]
) -> list[Message]:
    """The system and user messages for one page's call: image, note on the marks, then the page text."""
    from formcast.agent.prompts import _schema_line  # not at module top: formcast.agent will import this package

    fields = [f for f in template.fields if f.page == page]
    first = next((f.id for f in fields if f.id in marks), None)
    example = f"a box marked {marks[first]} is {first}" if first else "no box has a mark"
    note = IMAGE_NOTE_FULL_IDS if first and marks[first] == first else IMAGE_NOTE
    paths = "\n".join(_schema_line(sp) for sp in flatten_schema(template.schema_))
    boxes = "\n".join(f"- {f.id}: {f.type}" for f in fields)
    text = (
        f"SCHEMA PATHS:\n{paths}\n\nTRANSFORMS:\n{transforms_ref()}\n\n"
        f"BOXES ON THIS PAGE ({len(fields)}; ids as in the page map):\n{boxes}\n\n"
        f"PAGE MAP (extracted text and geometry, page {page}):\n{summarize_page(form_map, page)}\n"
    )
    return [
        Message("system", SYSTEM_PROMPT),
        Message("user", [ImagePart(image), TextPart(note.format(example=example)), TextPart(text)]),
    ]
