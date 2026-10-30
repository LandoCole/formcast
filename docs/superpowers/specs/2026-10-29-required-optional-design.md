# Required and optional fields: design

Date: 2026-10-29. Builds on PDF extraction (docs/superpowers/specs/2026-10-28-pdf-extraction-design.md) and the
fill workbench.

## Why

A person reviewing extracted data, or a filled form, has to know which gaps matter. Today they cannot tell:

- **Extraction.** Every field with no value is `missing` and counts as a problem, so an optional blank (a W-9's
  "Business name, if different", its exemption codes, its account numbers) makes a document "with problems" exactly
  like a missing taxpayer name. On the W-9 test set (forms/extract/fw9) nearly every copy would need review. The
  JSON-Schema `required` list exists (the AI proposes it, the setup screen has a Required checkbox) but only the AI
  pass and a few check messages use it.
- **Fill.** A fill template's boxes have no notion of required at all. The fill report lists every box that came out
  empty as one flat "gap" line, so a required signature-date box left empty looks the same as an unused "Suite" box.

The goal: **required or optional is a first-class property of every field, in extraction and in fill.** The agent
classifies each field and says why; the person can change it; review and the fill report put required problems first
and treat optional ones with less scrutiny.

## Decisions (the person's)

- **Two levels only: Required and Optional.** No conditional rules ("required when X", "one of A or B") for now.
- **Either/or fields are both Optional.** When the document asks for one of several fields but not all (a W-9's SSN or
  EIN), the agent marks each of them Optional, and its reason says so ("one of SSN or EIN is needed; either may be
  blank"). The same for a field needed only under some condition (the W-9's LLC letter): Optional, with the condition
  in the reason.
- **The agent decides, with a reason; the person's choice wins.** Each field gets a level and a one-line reason the
  person can see. The person can change the level; their choice replaces the agent's.
- **A blank optional field is not a problem.** A document is *complete* when its required fields have no problems.
- **Review splits into two sections**: required problems (reviewed first), and optional ones (blank or doubtful
  optional fields; less scrutiny, never blocking "complete").
- **Extraction first, then fill**, in one branch.

## How the agent classifies

The same rules for extraction fields and fill boxes, in the prompts that design them:

1. **What the document says** decides first: "required", "an entry is required", "must", an asterisk explained as
   required → Required; "optional", "if any", "if applicable", "if different", "(optional)" → Optional.
2. **Structure and context** when the document is silent: the fields that identify the subject of the document and
   the answers the document exists to collect (a name on line 1, a tax classification, a signature date) are
   Required; supplementary, alternative or follow-up fields are Optional.
3. **Either/or and conditional** fields are Optional, with the rule in the reason (see Decisions).
4. **When unsure, Optional**, with the reason saying what is unclear. (A wrongly optional field still shows in the
   optional section; a wrongly required one makes false alarms on every copy.)

The reason is one short line in plain words, quoting the form's own wording when there is one ("the form says 'An
entry is required'"; "the form says 'if any'"; "one of SSN or EIN is needed; either may be blank").

## Extraction

**Where it is stored.** The level stays where it is today: the parent object's JSON-Schema `required` list (inside an
item of a repeated group, the item schema's). The reason is a new annotation on the field's property,
`"x-required-reason": "<text>"` (JSON Schema ignores unknown keywords). A field is required exactly when
`formcast.extract.aipass.required_paths` says so (the one existing definition: the field's own object lists it; a
parent group's level is not inherited). Moving that function to a neutral module is the builder's call.

**AI.** Both prompts that design extraction fields classify:
- the FIELDS prompt (formcast.extract.prompts, the *I'll list them* start and add-a-line);
- DERIVE_SYSTEM (formcast.data.ai.derive_schema, the *AI suggests* start). Fill's one-shot uses the same prompt, so
  the rules are an extraction-only addendum: `derive_schema` gains a keyword (e.g. `classify_required=False`) that
  appends them; extraction's setup passes it, fill does not, and fill's prompt text stays exactly as it is.
Each asks for an `x-required-reason` on every property it proposes (Required or Optional). Fields that come from a
destination schema keep that schema's `required`; their reason is "from the destination's schema" unless the
destination schema has one. (Builder-check ruling: the AI-suggests start must classify, or the W-9 check below cannot
pass; forking the rules into an addendum keeps fill unchanged.)

**The person.** The setup screen's field inspector keeps its Required checkbox and shows the reason under it. Changing
the checkbox sends `set_field` with `required`; when the edit gives no `required_reason`, the server sets the reason
to "Set by you". `SetField` gains `required_reason: str | None`. The setup's field list marks required fields (a small
"Required" tag or glyph; the builder picks, consistent with the list's existing state glyphs).

**Reading and statuses.** Field statuses do not change (`missing` is still "no value"). What changes is what counts
as a problem:

- A **required problem** is a required field whose effective status (decisions and AI answers applied) is `missing`,
  `failed_check` or `unsure`.
- An **optional problem** is an optional field with one of those statuses (a blank optional field is an optional
  problem of status `missing`).
- A document is **complete** when it has no required problem (it may have optional ones), and **with problems** when
  it has at least one.

**Where the run learns the levels.** `run_batch` writes each template's required concrete paths into run.json
(`"required": {"<template_id>": ["investor.tax_id", "owners[0].name", ...]}`), next to the sha256 it already keeps (the
template, and so its levels, cannot change under a run). `Results` reads them. A run.json with no `"required"` key
(a run made before this change) treats every field as required, so old runs count exactly as they did.

**RunSummary** keeps `complete` and `with_problems` (with the new meaning) and gains `optional_only`: the complete
documents that still have optional problems. `Results.field_counts` per path gains `required: bool`. `Results.page`
gains `level: "required" | "optional" | None` (None = both, as today): a document is listed when it has a problem of
that level (and matching `path` and `status`), and only that level's problem fields come with it. The cached
per-filter lists (plan 3's G1) key on the level too.

**AI pass.** Unchanged: it already asks only for required fields.

**Server.** `GET /api/runs/{r}/problems` takes `level` (`required` | `optional`; absent = both); each problem carries
`required: bool`. `GET /api/runs/{r}` returns each field's `required` and the summary's `optional_only`. `Run.table`
(to-data) counts documents with *required* problems as with problems.

**Review screen.**
- The documents list has two sections, shown as a switch above it: **Required · N** (default) and **Optional · M**,
  N and M being document counts. Each lists the documents with problems of its level and shows only that level's
  cards. The status and field filters apply inside the chosen section. Set aside stays as it is.
- The Optional section has a one-line note: "Optional fields: blanks and doubtful answers on fields a copy may leave
  empty. They never stop a document from being complete." Each optional card shows the field's reason.
- Stats: Documents / Complete / Required problems / Optional only / Set aside / Run time.
- The By field table gets a Required column (a mark) and lists required fields first.
- Send to fill and export are unchanged (unsettled fields are left blank, as today).

## Fill

**Where it is stored.** `Field` (src/formcast/template/model.py) gains `required: bool = False` and
`required_reason: str = ""` and `required_by: Literal["agent", "human"] | None = None` (who set the level last).
Defaults keep every existing template valid. A new op `set_required(id, required: bool, reason: str = "")` sets the
three (`required_by` from the op's author: "human" for a person, "agent" for the page pass and the agent loop; a
person's toggle sends reason "Set by you"). **The person's choice wins:** an agent `set_required` on a field whose
`required_by` is "human" is refused with a warning naming the box (the template is unchanged), so a revision cannot
undo a person's toggle. It is added in Python
(op_models, ops) and in the browser engine's JS mirror (demo/web/core/ops.js: BROWSER_OPS, OP_SPECS, HANDLERS; the
field literals in demo/web/engines/browser/seed.js and ops.js addField emit the two new keys so Python and JS dumps
stay identical), with conformance cases (demo/tools/conformance_cases.py). Table fields are classified as a whole.

**AI.** The page pass (formcast.pagepass) classifies the boxes of each page it maps: its reply gains
`"required": [{"id": "<box id>", "reason": "..."}]` listing the required boxes; `mapping_ops` emits `set_required` for
each (every box not listed stays Optional, the default). **Tables:** the reply cannot name a table (it is made after
the mappings, by `table_ops`, with a new id), so a table is required when any box it absorbs is listed, with that
box's reason; a box absorbed into a table is never set required (its source is cleared, and it would otherwise be a
required gap in every report). The rules are the classification rules above, applied to the
form being filled (its printed cues, what the form is for). The agent loop's prompt is told `set_required` exists and
when to use it (a revision that finds a required box the page pass missed).

**The person.** The Selected box inspector (demo/web/core/inspector.js) gets a "Required" checkbox with the reason
under it, emitting `set_required`.

**Fill report.** `FillReport` gains `required_gaps: list[str]`: the gaps (as today: boxes that came out empty) whose
field is required. `gaps` keeps every gap. The JS fill report (core/fill/report.js) does the same. The report panel
(core/report.js) shows "Required boxes left empty: …" first and prominently, then "Other empty boxes: …" in place of
today's single "No data field: …" line, and the counts tiles gain "required left empty".

## Non-goals

- Conditional or either/or rules; "required when" and "one of" groups (named as the next step if optional sections
  turn out to hide real problems).
- Changing field statuses, the export formats, or what Send to fill passes.
- Classifying requiredness at review time, or per document; the level is the template's.
- Making the fill report refuse or block a fill with required gaps.

## Anti-goals

- A required problem shown as optional, or hidden. (Old runs with no levels count every field as required.)
- Fill changing behaviour for a person who never looks at required: a template with no required boxes fills and
  reports exactly as today, except the gap line's wording.
- Python and JS fill engines disagreeing (the conformance tests pin both).

## Test data

forms/extract/fw9 (20 W-9s with an answer key) is the real-form check: once built with the AI, its line 1 name and tax
classification should come out Required; SSN, EIN, LLC letter, business name, exemption codes, account numbers and the
requester box Optional; and most copies should be complete with optional notes, not "with problems".

## Left to the builder

File layout, names of helpers, the exact copy of reasons the code writes, the glyph for required fields in lists, the
order of tasks.
