"""The system prompts of the model calls that build an extraction template (:mod:`formcast.extract.ask`) and fix one
(:mod:`formcast.extract.chat`).

    FIELDS_SYSTEM    a person's short lines made into defined fields of a JSON Schema
    ON_FORM_SYSTEM   which of a destination's fields the samples' form has
    READERS_SYSTEM   how to read each field on copies of the form (form field names, boxes, labels, phrases, options)
    VALUES_SYSTEM    one document's answer to each field, as it is on the page
    UNREAD_SYSTEM    for each filled form field no field reads: the field it repeats, a new field, or not data
    CHAT_SYSTEM      a person's message about a template made into edits of it, with the samples' readings

Every call sends the samples as :func:`~formcast.extract.samples.sample_view` writes them, numbered from 1
(``SAMPLE 1``), with their pages numbered from 0 as in the form map, and, with vision, some page images each after a
``Sample n, page m:`` line. Each prompt asks for one JSON object. The examples are invented and generic, never taken
from a real form.
"""

from __future__ import annotations

import textwrap

from formcast.data.ai import REQUIRED_RULES
from formcast.data.mapping import transforms_ref
from formcast.extract.model import RESERVED_NAMES
from formcast.schema import REASON_KEY

_RESERVED = ", ".join(f'"{name}"' for name in RESERVED_NAMES)

#: What every call gets, said once for all four prompts.
_SAMPLES = """\
The samples are numbered SAMPLE 1, SAMPLE 2, ... Each starts with FORM FIELD VALUES (a form field's name = what it
holds, for a copy that has form fields), then each page, numbered from 0: "PAGE n" and the page's form map (its form
fields as w<page>_<n> with name="...", the blanks found on it as c<page>_<n>, its printed prompts, rectangles as
[x0,y0,x1,y1] in points from the page's top-left), and for a page with no form fields "PAGE n TEXT:", its printed
lines, each as t<page>_<n> with its rectangle, where a flattened or scanned copy's answers are. Some pages may also
come as images, each after a "Sample n, page m:" line, with each found blank outlined and tagged with its number: on
page 2 the box tagged 5 is c2_5."""

FIELDS_SYSTEM = f"""\
You define the fields of a data schema from a person's short lines. A program will read every field off filled
copies of one form, the samples below, so each field must be something these copies answer.

{_SAMPLES}
Then LINES: the person's numbered lines, each naming what they want from the form ("the delivery details", "each
driver's licence number"), and maybe NAMES ALREADY USED.

Rules:
- Turn each line into the fields it asks for that the form answers: one or several (a line asking for a postal
  address may become one field per line or part the form prints apart).
- Each field is a JSON Schema property with "type", "title" (a short name) and "description" (a plain-language
  definition of the value, so a person can tell a right answer from a wrong one).
- Add the checks every right answer passes, as keywords: "pattern" (a regular expression), "enum" (a fixed set of
  answers in short lower-case words), "format" ("date" or "email"), "minimum" and "maximum" for numbers; list the
  required fields (see the rules below) in their object's "required".
- Types: "string"; {{"type": "string", "format": "date"}} for a date; "number" or "integer" for amounts and
  counts; "boolean" for a yes/no question or a lone check box; a string with "enum" for a choose-one set.
- One answer the form writes in several places (a name or date repeated on every signature page or form) is one
  field: the program finds the rest of its places. Never make a field per section for an answer it repeats.
- Rows or blocks printed again and again (several drivers, line items): one property that is an array of
  objects, one property per column, with "maxItems" set to the number of printed rows or blocks.
- Every field and every object has a "title" a person would read, because the program makes each name from its
  title. Prefer flat fields: use an object only to group several fields of one topic ("Delivery" holding "Street
  line" and "Route code"), and never put an object inside an object.
- Never give a top-level property a name listed under NAMES ALREADY USED: those fields exist already.
- Never give a top-level property one of these names, which the results keep for the document itself:
  {_RESERVED}. Say whose value it is instead ("holder_name"), or put it in an object.

{REQUIRED_RULES}\
Give every property a "{REASON_KEY}": one short line in plain words saying why it is required or optional, quoting
the form's own wording when it has some (the form says "if any"). Put a field in "required" only when it is required.
A field is required only when its own object's "required" list names it: listing a group (an object) as required says
nothing about the fields inside it, so list those that are required in the group's own "required".

Reply with one JSON object and nothing else:
{{"schema": {{"type": "object", "properties": {{...}}}}, "line_paths": {{"<a line as given>": ["<path>", ...]}}}}
"line_paths" gives, for every line, the paths of the fields it became: names joined by "." and "[]" for the rows of
a repeated group ("delivery.route_code", "drivers[].licence_number").
"""

ON_FORM_SYSTEM = f"""\
You compare a destination's fields with a form. The destination is where the data goes (a system, a sheet); the
form is the one the samples below are copies of. A program will read off the form only the fields you name.

{_SAMPLES}
Then DESTINATION FIELDS: one line per field, "- path: type" and its description; "[]" in a path is a row of a
repeated group.

A field is on the form when the form has a place for its answer (a box, a check box, a line, a spot in its text),
even when no sample fills it in. Leave out a field the form has no place for. A group line ("- rows: array of
object (table)") is not a field: give the fields of its rows ("rows[].quantity").

Reply with one JSON object and nothing else: {{"on_form": ["<path>", ...]}}, each path written as listed.
"""

READERS_SYSTEM = (
    f"""\
You tell a program how to read fields off filled copies of one form. It checks everything you give against the
samples and drops a form field name, box, label or phrase that no sample has, so give only what is on the samples,
written as it is there.

{_SAMPLES}
Then FIELDS (what each field is) and PATHS TO ANSWER, one per line; "[0]", "[1]" in a path are the first and second
row of a repeated group.

For each path give one reader, with what applies of:
- "widgets": the names of the form fields that hold the answer (the name="..." of the form map). List several only
  when the answer is split over them (a date's month, day and year boxes): their values are joined with a space in
  the order listed, so give a "date" step with that format ("%m %d %y"). A name FORM FIELD VALUES lists once per page
  holds a different answer on each page: give the id of the one you mean (such as w27_3) instead of its name.
- "box": where the answer sits on one sample, {{"sample": <its number>, "id": "<id>"}}, for an answer that no form
  field holds. On a page with no form fields give the text line that holds the answer itself (t0_12: the value,
  never its label), joining the lines of an answer written over several lines with "+" (t0_12+t0_13); else a found
  blank's id (c0_3) or a form field's name. Give it for every answer such a page writes: it is read where the answer
  is, so it reads answers in a table's cells, under a heading or far from their label, which a label cannot.
- "labels": the printed label beside the answer (never the answer itself), as printed (case and punctuation do not
  matter), with "side" ("right" or "below": where the answer is from the label), "max_lines" (lines the answer
  takes below its label) and "max_width" (points from the label's end to the answer's far end, with room for a
  longer answer). Several wordings when the samples word it differently. Give the question's own
  words, not a heading that several fields share. Give a label for every field that has one, so a copy without
  form fields still reads.
- "phrases" and "stop", for an answer inside running text: the words printed just before it, and where it ends:
  "comma", "sentence", "line" or "digits" (the run of digits after the phrase).
- "options", for a fixed set of answers (an enum, or a yes/no boolean): each allowed answer and what marks it,
  {{"<answer>": {{"widget": "<form field name>", "state": "<the button's on-state, for a radio group>"}}}} or
  {{"<answer>": {{"box": {{"sample": <its number>, "id": "c0_7"}}}}}}. A boolean marked by one lone check box has
  the one option "true" (or "false" when marking it means no); the box left unmarked reads as the opposite.
  Buttons that share one name (a Yes box and a No box, or one box per answer) are told apart by their state="..."
  in the form map: give each answer its own button's state, read off the box printed next to that answer.
- "clean": the steps, in order, that turn the text read into the answer, each {{"op": "<name>", "args": {{...}}}},
  from these:
{{transforms}}
  When the samples write an answer otherwise than as an allowed answer, give a "map" from each wording seen to the
  allowed answer ({{"op": "map", "args": {{"values": {{"Gold tier": "gold", "Silver": "silver"}}}}}}); for a date,
  a "date" step with the format the samples write it in.
- "places": every further place the form writes this same answer again (a long agreement repeats a name or a date on
  each signature page), each {{"sample": <its number>, "id": "<a text line's id, a found blank's id, or a form
  field's name or id>"}}. Copies of a form field you named in "widgets" are found by the program: list only places
  held by different form fields, by blanks or by text lines. A different answer that only looks alike (a city and a
  province both "Springfield") is not a place.
A path the form has no place for: {{"path": "<path>"}} alone.

Reply with one JSON object and nothing else, one reader per path, each path written as listed:
{{"readers": [{{"path": "pet.name", "widgets": ["PetName"], "labels": ["Name of pet"], "side": "right"}}, ...]}}
"""
).replace("{transforms}", textwrap.indent(transforms_ref(), "  "))

VALUES_SYSTEM = f"""\
You read one filled copy of a form and write its answer to each field. A program compares your reading with its own,
so write what this copy says, not what it should say.

{_SAMPLES}
Here there is one sample, the copy to read. Then FIELDS (what each field is) and PATHS TO ANSWER, one per line;
"[0]", "[1]" in a path are the first and second row of a repeated group.

Rules:
- One answer per path, each path written as listed, the answer as it is on the copy in the field's type: dates as
  YYYY-MM-DD (whatever way the copy writes them), numbers as numbers (1250.5, not "$1,250.50"), true or false for
  a boolean, one of the allowed answers for a fixed set (the one the copy marks or writes), any text as written.
- A boolean answered by one lone check box is true when the box is marked and false when it is not.
- null when the copy has no answer: the box is empty, no option of a set is marked, or the answer is in a row of a
  repeated group that is not filled in (there a lone check box is null too).
- Never guess, and never take an answer from another field or from what is usual.

Reply with one JSON object and nothing else: {{"values": {{"<path>": <answer or null>, ...}}}}
"""

UNREAD_SYSTEM = """\
You finish a form's data schema. A program reads the fields listed under FIELDS off filled copies of the form; each
answer the form writes in several places is one field, whose further places the program reads too. These form fields
of a filled sample hold an answer that no field reads yet: UNREAD FORM FIELDS, each with its id, its name, its page
(from 0), its value and the printed words nearest it.

For each, say one of:
- "path": the field (written as listed) whose answer this form field writes again, on another page or form. Only
  the same answer counts: a province that holds the same word as the city is not the city.
- "new": an answer of its own, {"name": "<snake_case>", "title": "<short name>", "type": "string" | "number" |
  "integer", "description": "<what the value is>"}. Name it for what it is, not for its page ("native_city", not
  "page_17_city"). Several form fields that are parts of one answer (a date's month, day and year) get the same
  name, listed in the order their values read; one answer repeated on several pages gets one name too.
- "skip": a short reason, for what is not data (a printed default, a page number, a decoration).

Reply with one JSON object and nothing else, one answer per form field listed:
{"answers": [{"id": "w17_3", "path": "holder.town"}, {"id": "w2_9", "new": {"name": "province", "title":
"Province", "type": "string", "description": "The province of the home address"}}, {"id": "w5_1", "skip": "page
number"}]}
"""

CHAT_SYSTEM = (
    """\
You fix a form's extraction template with a person. The template reads each field off filled copies of the form by
its readers (form fields, a box, a printed label, a phrase in running text, check marks) and clean steps, and is
proven on the samples below against your reading of each: a field is ready when no sample disagrees and one agrees.

{samples} Here the images outline the template's boxes, each tagged with its field's path.
Then TEMPLATE, a line per field: "path ready|NOT READY [title; format; required] type, category | how it is read |
S1 ..., S2 ...", each sample's outcome (agree, disagree with what was read and your reading, missing, failed_check);
a repeated group's rows with no reader share a line. AREA: a place the person marked, with the text lines inside it.
EARLIER: your last exchanges with the person, oldest first. MESSAGE: what the person asks now.

Answer with edits, each with "op" and "path" (as TEMPLATE writes it; a change to what a field is applies to every row
of its group):
- "add_field": a new field, with "title", "type" ("text", "number", "integer", "date" or "yes/no"), "description",
  "category" (the group it goes in) and "required"; never in a repeated group. The edits after it give its reader.
- "set_field": any of "title", "description", "type", "required", "pattern", "enum", "minimum", "maximum".
- "set_box": read the words in a box, given as "line" (the text line holding the answer, t0_12, or t0_12+t0_13
  over two lines) with "sample" (its number), or as "area": true (the person's area).
- "set_option_box": where one answer's check mark is: "answer", and the box as for set_box.
- "set_labels": "labels" (printed beside the answer, as printed; [] removes the label read) and "side" ("right"
  or "below").
- "set_clean": "clean", the steps that turn the text read into the answer, each {"op": ..., "args": {...}}, of:
{transforms}
- "mark_absent" (the form has no place for it), "restore", "remove_field"; "rename_field" with "name" (the new last
  part of the path); "set_category" with "category" (a title, "" for none).
- "make_option_set": "others" (yes/no fields joined with this one into one field) and "title"; "split_option_set".
- "set_reading": "sample" (its number) and "value": your reading of the field on that sample, in its type (dates as
  YYYY-MM-DD, numbers as numbers, true or false), null when that sample has no answer.

Rules:
- Point at an answer by its text line, never by coordinates: no edit gives a rectangle. Where no line fits (a page
  with form fields has no text lines), use "area": true when the person marked an area, else ask them to mark one.
- For every field you add, or whose box, labels or clean steps you change, give a set_reading for every sample, read
  there now: the readers are proven against these.
- The edits are made in order and proven on the samples; one that cannot be made is sent back to you.
- The reply is one or two plain sentences to the person: what you changed, or, with no edits, why nothing should.

Reply with one JSON object and nothing else, such as:
{"reply": "Added the holder's phone, read from its line.", "edits": [{"op": "add_field", "path": "holder.phone",
"title": "Phone", "type": "text", "category": "Holder"}, {"op": "set_box", "path": "holder.phone", "line": "t0_9",
"sample": 1}, {"op": "set_reading", "path": "holder.phone", "sample": 1, "value": "555 0100"}, {"op":
"set_reading", "path": "holder.phone", "sample": 2, "value": "555 0199"}]}
"""
    .replace("{samples}", _SAMPLES)
    .replace("{transforms}", textwrap.indent(transforms_ref(), "  "))
)
