"""The model's three jobs in the one-shot flow, each a single call that answers JSON.

    derive_schema(pdf, llm) -> SchemaResult          the form's own data schema, read off its pages
    map_table(schema, table, llm) -> MappingResult   a DataMapping from the table's columns to that schema
    extract_records(schema, text, llm) -> ExtractResult   records of that schema found in free text

What each call sends: ``derive_schema`` the page map's text and, with ``vision``, a marked image of
every page that has blanks (up to ``MAX_IMAGE_PAGES``: one per section of forms, then the pages with the
most blanks); pages with no blanks (the prose of a long
agreement) send only a short excerpt of their text. ``map_table`` the schema's paths and, per column, its name, type
and up to five sample values: never the rows themselves, which the mapping then turns into records
here. ``extract_records`` the schema and the whole text.

``llm`` is an :class:`LLMConfig` (the call creates and closes the provider) or a ready
:class:`Provider` (the caller keeps it). A reply that is not the JSON asked for is asked for once more,
with what was wrong; a second bad reply raises ``FormcastError``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pydantic

from formcast.data.mapping import DataMapping, check_mapping, target_paths, transforms_ref
from formcast.data.table import Table, profile
from formcast.errors import FormcastError
from formcast.llm import ImagePart, LLMConfig, Message, Provider, TextPart, Usage, make_provider
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import FormMap
from formcast.pagemap.summarize import summarize_page
from formcast.pagepass.marks import render_marked_page
from formcast.pagepass.run import _call_limits
from formcast.schema import REASON_KEY, flatten_schema
from formcast.template.model import new_template

PdfInput = str | Path | bytes

MAX_IMAGE_PAGES = 12
_PAGE_CHARS = 12000
_EXCERPT_CHARS = 600  # of a page with no blanks
_SECTION_GAP = 1  # pages without blanks inside one section of forms
_TEXT_CHARS = 60000
_SAMPLE_TABLE_COLUMNS = 300

DERIVE_SYSTEM = """\
You design the data schema of a blank form: a JSON Schema for one record holding everything a person
filling in this form would write on it. Software will map the form's boxes to your schema's paths and
then fill the form from records, so the schema must follow the form closely.

Rules:
- The root is {"type": "object", "title": <the form's name>, "properties": {...}}.
- Group fields as the form groups them, in nested objects named after the section (holder,
  business, co_signer, vehicle, ...). Use snake_case property names that say what the value is.
- One property per piece of information the form asks for. A single "Name" box is one property
  (name); separate First/Middle/Last boxes are separate properties. A value split over several small
  boxes (a date as MM DD YYYY, an SSN as 3-2-4 digits) is still one property.
- Types: "string"; {"type": "string", "format": "date"} for dates; "integer" or "number" for counts
  and amounts; "boolean" for a yes/no question (one property for its Yes and No boxes, and for a lone
  checkbox); {"type": "string", "enum": [...]} for a choose-one group of options (the printed option
  labels, in order); {"type": "array", "items": {"type": "string", "enum": [...]}} for a check-all-that-
  apply group.
- Rows of a table, or blocks printed again and again for several items (prior jobs, contacts,
  household members, line items): one property that is an array of objects, one sub-property per column,
  with "maxItems" set to the number of printed rows or blocks.
- Give every property a "description" with the form's printed label or question, in at most ten words.
  No "examples", "default" or "title" on properties; the schema can be long, so keep it compact.
- Leave out: signature boxes themselves (keep their dates), boxes for official or office use only,
  page numbers and form ids.
- Every blank on the form should be covered by some property; do not invent fields the form lacks.

Reply with one JSON object and nothing else: {"schema": {...}}
"""

#: How to tell a required field from an optional one, for any prompt that designs fields or boxes of a form. Each prompt
#: adds its own line on how to reply (the schema's "required" lists and an :data:`~formcast.schema.REASON_KEY` per
#: property; the page pass's list of required boxes).
REQUIRED_RULES = """\
Decide for each field (or box) of the form whether it is required or optional, by these rules in this order:
1. What the form says decides first. Wording such as "required", "an entry is required" or "must", or an asterisk
   the form explains as meaning required, makes it required. Wording such as "optional", "if any", "if applicable"
   or "if different", or "(optional)", makes it optional.
2. When the form is silent, use its structure and context. The fields that identify who or what the form is about
   and the answers the form exists to collect (a name on the first line, a date beside a signature) are required;
   supplementary, alternative and follow-up fields are optional.
3. A field that is one of several alternatives (one may be blank when another is given), or that is needed only
   under a condition (only when another answer is a certain one), is optional; put that rule in its reason.
4. When unsure, it is optional; put what is unclear in its reason.
"""

#: What ``derive_schema`` adds to :data:`DERIVE_SYSTEM` when asked to classify (extraction only; fill's prompt is
#: :data:`DERIVE_SYSTEM` alone).
_DERIVE_REQUIRED = f"""\

Required and optional:
{REQUIRED_RULES}\
List the names of an object's required properties in its "required" array. Give every property a "{REASON_KEY}":
one short line in plain words saying why it is required or optional, quoting the form's own wording when it has
some (the form says "if any").
"""

MAP_SYSTEM = """\
You map the columns of a user's data table to the paths of a form's data schema. Software applies
your rules to every row, with no further help, so each rule must produce exactly the value the
schema path wants.

A rule sets one schema path from ONE of:
- "column": a column's value, as it is;
- "template": text with {Column Name} placeholders, to join columns ("{First} {Last}",
  "{Street}, {City}, {State} {ZIP}");
- "value": a fixed value (only when the data clearly implies it for every row).
Then "transforms" (in order) reshape it:
{transforms}

Rules:
- Map every schema path the data has a value for; a column may feed several paths (a full name
  split into first and last), and several columns may feed one path (a template).
- Match the value to the schema type: dates must end as YYYY-MM-DD (use the date transform with the
  format the samples show); booleans true/false (map or boolean); an enum one of its listed values
  exactly (map each data value seen to an enum value); numbers as numbers.
- A list of records ("x[].col ... in table x") is set by index: "x[0].col", "x[1].col", ... for
  columns like "Vehicle 1", "Vehicle 2".
- Do not map a column to a path whose meaning differs (a holder's address is not a business's).
- Columns you leave out go in "unmapped_columns".

Reply with one JSON object and nothing else:
{"rules": [{"path": "a.b", "column": "Col", "transforms": [{"op": "date", "args": {"formats": ["%m/%d/%Y"]}}]}],
 "unmapped_columns": ["..."], "notes": "<one sentence on anything uncertain>"}
"""

EXTRACT_SYSTEM = """\
You read free text (notes, a message, a description) and write the data it gives as records of a
form's JSON Schema. Software fills the form from your records as they are.

Rules:
- Use only values the text states or makes certain. Leave out any property the text does not give;
  never invent a value or write a placeholder.
- Dates as YYYY-MM-DD, booleans as true/false, numbers as numbers, an enum as one of its values exactly.
- Follow the schema's nesting and property names exactly.
- Usually the text describes one record. When it clearly describes several separate ones (several
  people each needing their own form), give one record each.

Reply with one JSON object and nothing else: {"records": [{...}], "notes": "<one sentence on anything uncertain>"}
"""


CUT_OFF_RETRY = (
    "Your reply was cut off at the output limit. Reply again with the whole JSON object, keeping every "
    "field, but write it more compactly: descriptions of at most six words, no examples, no whitespace."
)


@dataclass
class SchemaResult:
    schema: dict
    usage: Usage
    text: str = ""  # the model's last raw answer


@dataclass
class MappingResult:
    mapping: DataMapping
    usage: Usage
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractResult:
    records: list[dict]
    usage: Usage
    notes: str = ""


# ---------------------------------------------------------------- calling


def _answer(text: str) -> dict | None:
    """The first JSON object in ``text``. Text after it (a stray extra brace, a sentence) is ignored, and an
    object a few closing braces short (a long schema's last line) is closed."""
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    body = text[start:].rstrip()
    for closing in ("", "}", "}}", "}}}"):
        try:
            value, _ = decoder.raw_decode(body + closing)
        except (json.JSONDecodeError, RecursionError):
            continue
        return value if isinstance(value, dict) else None
    return None


def _ask(llm: LLMConfig | Provider, messages: list[Message], check, *, long: bool = False):
    """Call the model; ``check(answer_dict)`` returns the result or raises ``ValueError`` (asked again once)."""
    owns = isinstance(llm, LLMConfig)
    provider: Provider = make_provider(llm) if owns else llm  # type: ignore[arg-type,assignment]
    usage = Usage()
    limits = _call_limits(provider) if long else {}
    try:
        for attempt in range(2):
            response = provider.complete(messages, [], **limits)
            usage = Usage(usage.input_tokens + response.usage.input_tokens, usage.output_tokens + response.usage.output_tokens)
            answer = _answer(response.text)
            limit = limits.get("max_output_tokens") or getattr(getattr(provider, "config", None), "max_output_tokens", 0)
            cut_off = answer is None and limit and response.usage.output_tokens >= limit - 64
            try:
                if answer is None:
                    raise ValueError("the reply was cut off at the output limit" if cut_off else "the reply was not one JSON object")
                return check(answer), usage, response.text
            except (ValueError, FormcastError, pydantic.ValidationError) as exc:
                problem = str(exc).splitlines()[0][:400]
                if attempt == 1:
                    raise FormcastError(f"the model's answer could not be used: {problem}") from None
                # A cut-off reply is not resent (it would fill the output again): ask for the same, shorter.
                again = CUT_OFF_RETRY if cut_off else f"That answer can't be used: {problem}. Reply again with the JSON object only."
                messages = [*messages, Message("assistant", "(cut off)" if cut_off else response.text or "(empty)"), Message("user", again)]
    finally:
        if owns:
            provider.close()
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- derive_schema


def derive_schema(
    pdf: PdfInput,
    llm: LLMConfig | Provider,
    *,
    form_map: FormMap | None = None,
    vision: bool = True,
    instructions: str = "",
    classify_required: bool = False,
) -> SchemaResult:
    """A JSON Schema for the data ``pdf`` collects, designed by the model from the form itself.

    ``instructions`` adds the user's own words (say, "name the holder section 'buyer'"). With ``classify_required``
    the model also decides which fields are required (the ``required`` lists) and gives each property a one-line
    reason (:data:`~formcast.schema.REASON_KEY`); without it the prompt is :data:`DERIVE_SYSTEM` alone.
    The schema is checked with :func:`formcast.flatten_schema` and must have at least one field.
    """
    fm = form_map if form_map is not None else build_form_map(pdf)
    seed = new_template({"type": "object", "properties": {}}, fm, candidates=True)
    pages = sorted(p.index for p in fm.pages)
    boxed = sorted({f.page for f in seed.fields if f.page is not None and f.rect is not None})
    counts = {n: sum(1 for f in seed.fields if f.page == n and f.rect is not None) for n in boxed}
    shown = _image_pages(counts) if boxed else pages[:MAX_IMAGE_PAGES]
    parts: list = []
    if vision:
        for n in shown[:MAX_IMAGE_PAGES]:
            boxes = [f for f in seed.fields if f.page == n and f.rect is not None]
            parts.append(TextPart(f"Page {n + 1}:"))
            parts.append(ImagePart(render_marked_page(pdf, n, boxes)))
        parts.append(TextPart(
            "The images show the pages with each blank outlined (blue: text box, green: checkbox); ignore "
            "the small id marks. The page map below has the exact printed text."
        ))  # fmt: skip
    parts.append(TextPart(f"PAGE MAP:\n{_page_text(fm, pages, set(boxed))}"))
    if instructions.strip():
        parts.append(TextPart(f"The user adds: {instructions.strip()}"))

    def check(answer: dict) -> dict:
        schema = answer.get("schema", answer if "properties" in answer else None)
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            raise ValueError('reply as {"schema": {"type": "object", "properties": {...}}}')
        schema.setdefault("type", "object")
        if not target_paths(schema):  # also checks the schema can be flattened (FormcastError)
            raise ValueError("the schema has no fields")
        return schema

    system = DERIVE_SYSTEM + _DERIVE_REQUIRED if classify_required else DERIVE_SYSTEM
    schema, usage, raw = _ask(llm, [Message("system", system), Message("user", parts)], check, long=True)
    return SchemaResult(schema, usage, raw)


def _image_pages(counts: dict[int, int]) -> list[int]:
    """Which pages with blanks the schema step shows, at most ``MAX_IMAGE_PAGES``, in page order.

    A long package's forms come in sections (a cover page, signature pages, a questionnaire, annexes),
    and a section with few blanks matters as much as one with many. So pages with blanks are grouped
    into runs (a gap of up to ``_SECTION_GAP`` pages without blanks continues a run); each run, the
    largest first, gets an image of its page with the most blanks; images left over go to the pages
    with the most blanks.
    """
    pages = sorted(counts)
    runs: list[list[int]] = []
    for n in pages:
        if runs and n - runs[-1][-1] <= _SECTION_GAP + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    runs.sort(key=lambda run: -sum(counts[n] for n in run))
    chosen = [max(run, key=lambda n: (counts[n], -n)) for run in runs][:MAX_IMAGE_PAGES]
    for n in sorted(pages, key=lambda n: (-counts[n], n)):
        if len(chosen) >= MAX_IMAGE_PAGES:
            break
        if n not in chosen:
            chosen.append(n)
    return sorted(chosen)


def _page_text(fm: FormMap, pages: list[int], boxed: set[int]) -> str:
    """The page map's text, pages with boxes first in the budget: a long document that is mostly prose
    (an agreement whose last pages are the forms) keeps its form pages whole, and the rest of its pages are
    cut to a short excerpt, kept for the names they define (the parties, the fund, the amounts).
    """
    budget = _TEXT_CHARS
    texts: dict[int, str] = {}
    for n in sorted(pages, key=lambda n: n not in boxed):  # pages with boxes first
        full = summarize_page(fm, n, max_chars=_PAGE_CHARS)
        if boxed and n not in boxed:
            full = full[:_EXCERPT_CHARS] + (" [...]" if len(full) > _EXCERPT_CHARS else "")
        texts[n] = full[: max(budget, 0)]
        budget -= len(texts[n])
    skipped = [n + 1 for n in pages if not texts[n]]
    out = "\n\n".join(texts[n] for n in pages if texts[n])
    if skipped:
        out += f"\n\n[pages {skipped[0]}-{skipped[-1]}: text left out, no blanks found there]"
    if boxed:
        out = f"Blanks were found on page(s) {', '.join(str(n + 1) for n in sorted(boxed))}; the rest are prose, shown in short.\n\n" + out
    return out


# ---------------------------------------------------------------- map_table


def _schema_lines(schema: dict) -> str:
    from formcast.agent.prompts import _schema_line  # not at module top: formcast.agent imports the page pass

    return "\n".join(_schema_line(sp) for sp in flatten_schema(schema) if sp.type != "object")


def map_table(schema: dict, table: Table, llm: LLMConfig | Provider, *, instructions: str = "") -> MappingResult:
    """A :class:`DataMapping` from ``table``'s columns to ``schema``, made by the model from column samples."""
    cols = profile(table)[:_SAMPLE_TABLE_COLUMNS]
    col_lines = "\n".join(
        f"- {json.dumps(c.name, ensure_ascii=False)}: {c.type}, {c.filled}/{len(table.rows)} filled; samples "
        + (" | ".join(json.dumps(s, ensure_ascii=False) for s in c.samples) or "(none)")
        for c in cols
    )
    text = (
        f"FORM SCHEMA PATHS:\n{_schema_lines(schema)}\n\n"
        f"DATA TABLE ({table.name or table.kind}, {len(table.rows)} rows) COLUMNS:\n{col_lines}\n"
    )
    if instructions.strip():
        text += f"\nThe user adds: {instructions.strip()}\n"
    warnings: list[str] = []

    def check(answer: dict) -> DataMapping:
        rules = answer.get("rules")
        if not isinstance(rules, list):
            raise ValueError('reply as {"rules": [...], "unmapped_columns": [...], "notes": "..."}')
        good = []
        for r in rules:
            try:
                good.append(DataMapping.model_validate({"rules": [r]}).rules[0])
            except pydantic.ValidationError as exc:
                warnings.append(f"a rule was left out ({exc.errors()[0]['msg']}): {json.dumps(r)[:120]}")
        notes = answer.get("notes") if isinstance(answer.get("notes"), str) else ""
        mapping, problems = check_mapping(DataMapping(rules=good, notes=notes), schema, table.columns)
        warnings.extend(problems)
        if not mapping.rules and table.columns:
            raise ValueError("no rule can be used: " + ("; ".join(problems[:5]) or "the rules list is empty"))
        return mapping

    system = MAP_SYSTEM.replace("{transforms}", transforms_ref())
    mapping, usage, _ = _ask(llm, [Message("system", system), Message("user", text)], check)
    return MappingResult(mapping, usage, warnings)


# ---------------------------------------------------------------- extract_records


def extract_records(schema: dict, text: str, llm: LLMConfig | Provider, *, max_records: int = 50) -> ExtractResult:
    """The records of ``schema`` that free ``text`` describes (usually one)."""
    if not text.strip():
        raise FormcastError("there is no text to read")
    body = (
        f"FORM SCHEMA (JSON Schema):\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"FORM SCHEMA PATHS:\n{_schema_lines(schema)}\n\n"
        f"TEXT:\n{text[:_TEXT_CHARS]}\n"
    )

    def check(answer: dict) -> tuple[list[dict], str]:
        records = answer.get("records")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ValueError('reply as {"records": [{...}], "notes": "..."}')
        notes = answer.get("notes") if isinstance(answer.get("notes"), str) else ""
        return records[:max_records], notes

    (records, notes), usage, _ = _ask(llm, [Message("system", EXTRACT_SYSTEM), Message("user", body)], check, long=True)
    return ExtractResult(records, usage, notes)
