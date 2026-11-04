# Field places, and finding blanks beside form fields

Date: 2026-11-04. Scope: PDF data extraction (the setup, the run's review, the export) and the fill template made
from an extraction template. Destination fill templates are unchanged.

## Why

An extraction template is also the map for pasting the data back onto a blank copy. Long agreements write one datum
in many places: the EB-5 subscription package shows the subscriber's name on 17 pages, the date on 12. Today a field
reads one place (a reused form field's first copy), the review marks only that place, and nothing checks that the
other places agree. Separately, a page that has any form field is never searched for blanks, so a page that mixes
form fields with printed blanks loses the blanks.

## Decisions (the user's)

- A field lists **every place** it is written: "page 2", "page 8", and on a page with several, "page 5, 1st",
  "page 5, 2nd" (reading order: top to bottom, then left to right).
- Each place is read. When places **disagree**, the field goes to review as a conflict listing each place's value;
  until a person decides, the first place's value is the field's value.
- The export keeps **one column per field**, and adds a **places sheet**: document, field, page, instance, value.
- Extraction setup gets an option: use the **form fields only**, or **also find blanks that aren't form fields**.
  The second runs the field detector (FFDNet) on pages that have form fields too, and drops every blank it finds
  over a form field, so a line is never extracted twice.
- While the detector runs, the setup panel and the jobs sidebar show **which page it is scanning**.

## Design

**Template.** `Reader.places`: the further places the same datum is written, each a single-location read: a form
field (`widget`, names and an optional page), a `box`, or a `choice` (check marks). The `strategies` stay the ways
to read the field's first place. A form field reused on several pages needs no entry: every copy of a form field a
field reads is one of its places automatically.

**Reading.** `FieldResult.places`: every place, first place first: page, rect, its instance on that page, the raw
text and the cleaned value. A place whose value differs from the first place's (a blank place counts as different)
makes the field `unsure`, with the reason `places disagree: page 30 (2nd) reads "…"`.

**Setup.** The schema's designer is told that an answer the form repeats is one field. The model, writing a
field's reader, also lists the other places on the sample that hold the same datum (form field ids and blanks).
Then, by code and one more model call:

- Fields whose first read is the very same form field (the same names, the same page) are merged into the first
  (not for a destination's fixed fields).
- Every filled form field no field reads is sent to the model, which answers for each: a place of a field, a new
  field (named for what it is), or not data. Each one skipped is in the setup's warnings, so a miss is never silent.
- Once proven, a field that failed and holds the same answer as a ready field, written in one of that field's
  places, is folded into it (a date read from its month box alone becomes a place of the date). Only the same
  answer in the same spot folds: a province that says "Beijing" beside a city "Beijing" never does.
- A read-only form field with no actions is the form's own printing (the "/" between a date's boxes, a printed
  "20" before the year): it is not shown to the model as a value, never kept in a reader, never an unread field.

**Review.** The sample's marks include every place, each labelled with its page and instance. The field's panel
lists its places with their values; clicking one shows its page. In a run's review, a card of a field written in
several places lists them; clicking one shows its page and puts its value in the value box.

**Export.** One column per field, as now. Every format gets a `places` part (an XLSX sheet; a file in the CSV
and Parquet folders): document, column, page, instance, value, and whether it matches the field's value.

**Fill.** The fill template made from an extraction template maps each place that is a form field to the field's
path, so filling writes every place.

**Detection beside form fields.** `SetupSpec.find_blanks` (`"form_fields"` or `"all"`; default `"form_fields"`, as
today). With `"all"` the pages the `detect` mode picks include pages that have form fields; there the detector's
blanks that overlap a form field (half the smaller box) are dropped. Each page scanned reports its progress, which
the server shows as the detect stage's detail ("page 12 of 35"), in the setup panel and the sidebar alike.

## Not in scope

The Fill flow's own detection of a destination form (it still skips pages with form fields). Saved templates keep
working unchanged: they have no `places`, and their reused form fields get their copies automatically.
