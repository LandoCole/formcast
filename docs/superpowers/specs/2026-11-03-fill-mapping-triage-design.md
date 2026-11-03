# Fill: mapping triage

## Intent

Formcast replaces a person who reads data in one format (a PDF, a CSV, a spreadsheet, extraction results) and types
it into another (a PDF form). Extract takes the data out of forms; Fill puts it into one. In Fill the AI does the
person's translation: it decides which piece of the data goes into which field of the form. The person's job
becomes checking that translation, not doing it.

Today Fill shows the AI's mapping as a read-only table in a folded part of the fill drawer, with one line naming the
columns it did not use. The form fields that got nothing are only known after a fill, as box ids. The person can't
change a match, add a missing one or say that a match is right, except by editing each record's JSON by hand, which
neither carries over to other records nor is saved.

This design gives Fill one place to triage the mapping: what the AI matched, what of the form is left, what of the
data is left; and the actions to confirm, change, remove, connect and type a value.

## Goals

1. One view, three lists: **Matched** (form field ← where its value comes from), **Form fields with no data**, and
   **Your data not used**, with counts. It opens on its own when data is mapped.
2. The person can confirm a match, change where it comes from, remove it, connect a leftover column to a leftover
   form field, and type a value for a form field (for this record, or for every record).
3. Each match says who made it (the AI, a saved mapping, the column names themselves, or a person) and whether a
   person confirmed it.
4. Form fields are named by their titles, with their schema path secondary; picking one shows its box on the page.
5. Every change refills the PDF on show, so the person sees the effect at once. No model is called for any change.
6. *Save* keeps the edited mapping, confirmations included, with the template, so the next file with the same columns
   opens with the same matches and the same confirmations.

## Non-goals

- Free text (data the AI read into records itself). There are no columns to match; the view says so and the record's
  JSON stays the way to change it.
- Editing transforms (split, date, map and the rest) by hand. A changed match takes the column as it is; the AI's
  transforms stay on the matches the AI made.
- Templates (``{Street}, {City}``) made by a person. A person's match reads one column, or is a typed value.
- Changing which box a form field goes to. The Schema column already does that (drag a field onto a box).
- New data sources (databases, APIs). Files and pasted text, as today.
- Making extraction results map better (sending the extracted fields' titles and definitions to the AI). The
  extraction handoff lands in this view like any other table.

## Anti-goals

- A modal the person must finish before they can see the filled PDF.
- A second copy of the mapping in the browser that can disagree with the server's records: the server turns the
  mapping into records, as it does today.
- Losing a person's work silently: a confirmed or hand-made match is never replaced by the AI's answer.

## Grammar

Objects: a **form field** (a leaf of the template's schema with at least one box), a **datapoint** (a column of the
data), a **match** (a rule of the data mapping: one form field, its source, its transforms) and a **typed value** (a
value a person entered for one form field of one record).

| Object     | Actions                                                   |
|------------|-----------------------------------------------------------|
| Match      | confirm, unconfirm, change source, remove                 |
| Form field | show on page, match to a datapoint, type a value          |
| Datapoint  | match to a form field                                     |
| Typed value| use for every record (becomes a match with a fixed value), clear |
| Mapping    | confirm all, undo the last change, save (with the template) |

A match's origin is derived: a person's when it carries ``manual``; otherwise the mapping's ``source`` (``model``,
``direct``) or ``saved`` when the mapping came from the store. Confirmed is stored on the match.

## Structure

- **Where it lives.** The left column of the Fill screen gets a switch between *Form fields* (the schema list, as
  today) and *Mapping* (this view). The switch shows only when the data on hand is a table; mapping data opens it
  on *Mapping*. The fill drawer's read-only *Data mapping* fold goes away; its summary line becomes a link that
  shows the Mapping view. The fill drawer keeps the record picker, Fill, Download and the report.
- **The selected item** shows in the Mapping view itself (a row expands), not in the right column, which stays the
  selected box's.
- **Picking a form field** (a matched one or a leftover) turns the page to its first box and selects it, as the
  Schema list's pick does now.
- **Data flow.** The browser holds the mapping it shows (``app.data.mapping``). A change edits that copy and posts it
  with the dataset id and the template's schema to a new route, ``POST /api/data/{id}/map``. The server checks the
  mapping against the schema and the columns (``check_mapping``), turns every row into a record (``records_from``) and
  answers the same shape the one-shot job answers for its data, with ``mode`` kept. The browser replaces
  ``app.data`` and refills. Typed values for one record are kept in the browser and laid over that record when it is
  filled; they are not saved.
- **Library.** ``Rule`` gains two optional fields, ``confirmed`` and ``manual`` (both absent unless true, so
  existing saved mappings and fixtures are unchanged, and applying a mapping ignores them). An answer from the model
  has both dropped.

## Behaviour

- Counts at the top: *12 matched (5 confirmed) · 3 form fields with no data · 2 columns not used*, and *Confirm all*.
- **Matched** rows: the form field's title (its path in small type), ← the source (a column's name, a template, or a
  fixed value), the value it gives for the record on show, its origin as a small tag (AI, saved, by name, you), and a
  ✓ button for confirm. Expanded: *Change to* a column select, *Remove*. Required form fields first, then the
  schema's order.
- **Form fields with no data**: title, required tag, and expanded: *Match to* a column select (unused columns first),
  and *Type a value* with *This record* / *Every record*.
- **Your data not used**: the column's name, two samples, and expanded: *Match to* a select of the form fields with no
  data first, then every form field (choosing a matched one replaces its source, after saying so).
- A form field set by a match whose value is empty for the record on show is still *matched*; its value cell says
  *empty in this record*.
- *Undo* takes back the last mapping change (the mapping is its own history; template undo is unchanged).
- Saving: the edited mapping is saved whenever there is one and it was not purely ``direct`` with no person's edit.
- The AI's own leftovers (``unmapped_columns``) are shown as the not-used list only through the computed one: a
  column is not used when no match reads it.

## Testing

- Library: the two fields round-trip through ``dump`` and are absent when not set; ``apply_mapping`` ignores them;
  the model's answer has them dropped.
- Server: ``POST /api/data/{id}/map`` answers records made by the mapping; refuses free text, an unknown dataset and a
  mapping that is not one; drops a rule for a path the schema lacks with a warning.
- Browser (Playwright, the one-shot flow with the scripted model): the Mapping view opens with the three lists and
  counts; confirming marks the row and the count; changing a source, connecting a leftover column and removing a
  match refill the PDF with the new value; a typed value fills this record only, *Every record* makes it a match;
  picking a form field selects its box; Save keeps the edits and a reopened template with the same columns shows them.
- Node: the pure parts (the three lists from a mapping, a schema, the template's linked paths and the columns; the
  edits) are tested without a page.

## Left to the builder

The look of rows and tags, the wording of hints, keyboard handling within the lists, and whether the column select
also offers *a fixed value*.
