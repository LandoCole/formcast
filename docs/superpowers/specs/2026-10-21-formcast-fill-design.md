# formcast Plan 2: Fill — Design

Date: 2026-10-21
Status: draft for user review
Parent spec: `docs/superpowers/specs/2026-10-20-form-projection-design.md` (the "main spec"). This document replaces main-spec §7.8 (the `fill` and `to_smart_pdf` parts) and §7.9, and adds the fill-correctness metric from §11. Everything else in the main spec still holds.

## 0. Read first / decide

These are the places where my call changes what you get. Please read them first. They're listed most important first.

1. **Text that doesn't fit is kept whole, not cut (§5.5).** The main spec said "truncated with a warning". Because output keeps real form fields, I keep the full value in the field, shrink it to `min_font_size`, and report `overflow`. Anyone who opens the field sees all the text. On paper, or after you flatten it yourself, the end is cut off at the box edge. The one exception: a box with a hard character limit (comb boxes, or a form field with a maximum length) can't hold more, so the value is cut there and reported as `truncated`. **Decide:** is keeping the full value right, or do you want it cut to what's visible so print and screen match?
2. **Only Latin text renders reliably (§5.5).** PDF form fields display text with the 14 standard PDF fonts, which cover Western European characters only. Names in Vietnamese, Chinese, Arabic and so on are stored in the field correctly, but how they *look* depends on the viewer, and they may show as blanks or `?`. Fill reports `unencodable` for every such value. Fixing this means embedding a Unicode font in the form. That's doable later but fiddly with PyMuPDF. **Decide:** acceptable for now?
3. **Bad data doesn't stop the fill (§5.4).** Data that breaks the schema, such as a date in the wrong format or a missing required value, is reported per path as `invalid`, and the fill carries on with everything else. Pass `strict=True` to get an error instead. **Decide:** is lenient the right default for your use?
4. **Signature fields take typed text only (§5.3).** A signature field becomes a text field holding the transformed value (for example a typed name). Image signatures aren't supported: an image can't be a form-field value, and drawing it onto the page is flattening, which you've ruled out. **Decide:** fine for now?
5. **Fill reuses the form's own boxes under template fields (§5.3, "adoption").** When a template field or table cell sits on top of a real form field the template doesn't otherwise use, fill writes into that form field instead of stacking a new one on top. This handles the Plan 1 agent's habit of replacing per-cell widgets with a table (the I-9 and the 8949). It's a geometric rule, not a form-specific one.
6. **The fill metric needs new ground-truth labels (§6).** To score checkboxes, each choice box in `forms/gt/*.json` needs the option value it stands for (about 640 boxes across 17 forms, many of them plain yes/no). The plan drafts these automatically and has a subagent review them, as in Plan 1 T14. It's real labeling effort, and it touches the held-out GT, but only to label it, never to tune.

Calls I made that you can skim: §8.

## 1. Intent

Plan 1 builds a template: where each piece of data goes on a form. Plan 2 uses it. Given a template, the blank PDF and one data record, `fill` puts the data onto the form, returns the PDF, and reports exactly what happened: what was written where, what was shrunk or didn't fit, which data had nowhere to go, and which boxes got nothing. No model is called.

The filled PDF is usually **emailed or uploaded somewhere else** (user). It is the finished product, so the report is what lets someone check it before it goes out.

**User decision (2026-10-21): no flatten mode.** Fill always produces a PDF with real, editable form fields holding the data. If a flat PDF is needed, the user flattens it outside formcast (for example by printing to PDF). A `flatten` mode can be added later if it becomes a requirement. The API leaves room for it (§3).

## 2. Goals, non-goals, anti-goals

**Goals**
- `fill(template, pdf, data)` produces a fillable PDF with every mapped value in place, for all three kinds of form (fillable, digital flat, scanned).
- `to_smart_pdf(template, pdf)` produces the same PDF with empty fields, so a person can fill it by hand in any PDF viewer.
- Every mapping type from main spec §2 works at fill time: date split, join, split/slice/char_at, case and number/date formatting, boolean checkboxes, enum pick-one, multi-select, and tables flowing across segments and pages.
- Text fitting: shrink to fit, wrap multi-line boxes, respect comb boxes. Anything that still doesn't fit is reported.
- A structured `FillReport` returned with every fill.
- A **fill-correctness metric** in the eval harness that scores what lands in each ground-truth box. It closes the table-per-column loophole (main spec §14.3, "Known scoring caveats") and gets re-run over the saved Plan 1 templates with no model calls.
- Close Plan 1's parked items (§7).

**Non-goals**
- Flattening (user decision, above).
- Image signatures; cryptographic signatures (main spec non-goal).
- Unicode font embedding (see §0 item 2).
- `similarity()`, the `revise` UI and the demo app (Plan 3).
- Changing agent behavior or prompts. The Plan 1 held-out freeze (tag `plan1-heldout-frozen`) only pins agent code. Plan 2 touches the template model, ops validation, rendering and eval, which the freeze allows.

**Anti-goals** (failure even if tests pass)
- **Silent loss.** A value present in the data that ends up nowhere visible, without a report entry, is a bug. That covers data under an unmapped path, dropped table rows, truncated text, failed transforms and widgets that can't be found.
- **Form-specific code.** Nothing branches on a form's name, labels or layout. The same rule as Plan 1.
- **Metrics that can be gamed by shape.** The fill metric must check *what landed where*, not box geometry or path labels.
- **Breaking the original form.** On fillable forms, widgets the template doesn't use are left exactly as they were.

## 3. Public API

```python
pdf_bytes, report = fill(template, pdf, data, *, check_fingerprint=True, strict=False)
pdf_bytes = to_smart_pdf(template, pdf, *, check_fingerprint=True)
pngs = preview(template, pdf, data=None, ...)  # existing; with data, renders the filled PDF under the boxes
```

- `pdf` is a path or bytes, as everywhere else. `data` is one record (a dict) matching `template.schema`.
- `to_smart_pdf` is `fill` with no data. Both share one code path. It returns bytes only, because with no data there's nothing to report beyond placement problems, and those raise or are logged through the same warnings type (builder's choice of exactly how to surface them).
- **Room for flatten later:** a `mode` keyword is *not* added now. If flatten arrives later it becomes `mode="fields"|"flatten"`, with `"fields"` as the default, so existing callers don't change.
- `fill`, `to_smart_pdf` and `FillReport` are exported from `formcast` (lazy, like the rest).
- **Errors that raise** (`FormcastError`): the PDF can't be opened or is encrypted; the page count doesn't match the template; the fingerprint doesn't match (unless `check_fingerprint=False`, in which case the report records `fingerprint_mismatch`); the template refers to an unknown transform; and, with `strict=True`, data that fails schema validation. Everything else is a report entry, never an exception.

## 4. Fill pipeline

1. Open the PDF, check the page count and fingerprint (main spec §7.10).
2. Validate `data` against the embedded schema with `formcast.schema.validate_data`. Each error is attached to its schema path in the report as `invalid`. With `strict=True`, raise instead.
3. **Resolve every field's value** (§5.4): look up the source paths, apply `""`-as-missing, run the transform chain, then apply `when`.
4. **Bind every field to a widget** (§5.3): an existing widget (named, or adopted by geometry) or a new one.
5. **Fit and write** each value (§5.5), generating appearance streams so the PDF looks right in every viewer and flattens correctly with outside tools.
6. Flow table rows through segments (§5.6).
7. Build the report (§5.7). Return `doc.tobytes()` (with garbage collection/compression, builder's choice) and the report.

## 5. Design

### 5.1 Field types and what each becomes

| template type | value it takes | becomes in the output PDF |
|---|---|---|
| `text` | string after transforms | text widget (multi-line flag when `style.multiline`; comb flag + max length when `style.comb`) |
| `checkbox` | on/off | checkbox widget |
| `radio_option` | on/off | existing radio kid on fillable forms; a checkbox widget (circle style) when fill creates it (§8) |
| `signature` | string (typed) | text widget (§0 item 4) |
| `choice_mark` | on/off | a checkbox widget covering the option text, whose "on" look is an ellipse around it and whose "off" look is empty (§8) |
| `table` | list of rows | one text widget per written cell (§5.6) |

### 5.2 Value resolution — what "missing" means

- Each source path is looked up in the record. Absent keys, `None`, and the empty string `""` are all **missing** (Plan 1 ruling: real records use `""` for absent values). Whitespace-only strings are also missing (my call, §8).
- **Multi-select (Plan 1 ruling):** a path whose value is an array contributes **one list value**, so `contains("x")` on that path tests list membership. An empty list is missing.
- **Text fields:** `apply_chain(values, source.transforms)` and then, if `source.join` is set, values are joined (this is the existing `Source.join`). A list left over after transforms (an unjoined multi-select on a text field) is rendered as its items joined by `", "` with an info-level `list_joined` note (my call, §8). `source.when` never appears on text fields: ops validation only allows it on choice fields, and Plan 2 keeps it that way.
- **Choice fields** (`checkbox`, `radio_option`, `choice_mark`): on if `when` (applied after `transforms`) is truthy. With no `when`, the transformed value is passed through `bool_to_check`. Missing data means off, recorded as `missing`. An off box is a legitimate result, not a warning.
- **Transform errors** (for example `date_format` on `"next Tuesday"`): the field is left empty and the report records `transform_error` with the message. The fill continues.
- **Unmapped data:** every schema path that has a value in the record but no field mapped to it is reported as `unmapped_data`, a top-level warning. This is the "data with nowhere to go" case.

### 5.3 Binding fields to widgets

In priority order:

1. **Named existing widget** (`field.existing_widget` set). Find the widgets on `field.page` with that name. Several widgets can share one name: PyMuPDF yields one per kid of a radio or checkbox group (main spec §7.8). In that case **pick the kid whose rect best overlaps the field's rect** (highest IoU, ties broken by centre distance), and turn it on with that kid's own on-state from `button_states()`, never a guessed `"Yes"`. If no same-named widget overlaps the field's rect at all, report `widget_not_found` and leave the field empty. If the widget's kind doesn't suit the field type (a text widget behind a `checkbox` field, say, which can happen after `set_type`, Plan 1 parked item), report `type_mismatch` and skip the field.
2. **Adopted existing widget** (no `existing_widget`, but a real widget sits under the field). An existing widget is adopted when (a) no other template field names it, (b) its kind suits the field type, and (c) its centre lies inside the field's rect (or the table cell's rect) and it is the only such candidate (if several qualify, the one with the highest IoU). An adopted widget is written exactly like a named one, and the report lists it as `adopted`, with the widget's name, so a reviewer can see it happened. This is §0 item 5. It's what makes a table drawn over a fillable form's per-cell widgets fill those widgets instead of stacking new ones on top.
3. **New widget**, created at the field's rect (or cell rect) on the field's page, in unrotated page space converted the same way `preview` does. Names are deterministic and readable: the field id for plain fields, and `<table id>_r<row>_<column key>` for table cells, with a numeric suffix if the name is already taken in the PDF. The exact scheme is the builder's, but it must be stable across fills of the same template so an app can read values back by name. The report records each field's widget name.

- **Existing widgets the template doesn't use** are left untouched: value, appearance, flags.
- **Existing values:** if the input PDF already has values in widgets that fill writes to, fill overwrites them. A field whose data is missing *clears* a named or adopted widget, so that refilling a filled PDF never leaves stale data behind (my call, §8).
- **Fonts on existing widgets:** keep the widget's own font where PyMuPDF can (base-14 fonts). Only the size changes when fitting. A widget whose font PyMuPDF can't regenerate gets Helvetica, recorded as an info-level `font_substituted` note.

### 5.4 Data validation

- Validation runs once over the whole record with `validate_data`. Messages carry concrete locations (`medications[2].dose`), which the report maps back to schema paths (`medications[].dose`) by stripping indices.
- Non-strict (default): an invalid value is still resolved and transformed if it can be. If a transform then fails, that's `transform_error`. If it renders, the field gets it and the path carries `invalid`. This keeps "report everything, drop nothing" (§0 item 3).
- Extra keys in the record that aren't in the schema are ignored for filling. They're listed once as an info-level `unknown_keys` note, unless the schema forbids extras, in which case validation already reports them.

### 5.5 Text fitting

Fill computes an explicit font size for every text widget it writes, rather than leaving size 0 ("auto") to the viewer. That way the size is known, reported, and the same in every viewer and in an outside flattener.

- **Single line:** start at `style.font_size` if it's a number. If it's `"auto"`, start at the largest size whose line height fits the box height (capped at 12 pt; builder may tune the cap and padding). Shrink in 0.5 pt steps until the text width fits the box width minus padding, measured with PyMuPDF's text-length function for the widget's font. If it fits only below the starting size, report `shrunk` (info) with the final size. If it doesn't fit even at `style.min_font_size`, write the **full** value at `min_font_size` and report `overflow` (warning), noting that the end will be cut off when printed or flattened (§0 item 1).
- **Multi-line** (`style.multiline`, or a table column with `wrap` > 1): word-wrap at the box width and shrink until the wrapped lines fit the box height. A table cell with `wrap = n` is limited to n lines. Past `min_font_size` it's the same `overflow` rule. Plan 2 implements `Column.wrap`, which Plan 1 reserved in the model.
- **Comb** (`style.comb = n`): the widget gets the comb flag and max length `n`. A value longer than `n` characters is cut to `n` and reported as `truncated` (warning), because a comb box physically can't hold more. Existing widgets with a max length get the same rule.
- **Characters outside the font's encoding:** the standard 14 fonts cover WinAnsi (Western European) only. A value with other characters is still stored in the field, and fill reports `unencodable` (warning) listing the characters. See §0 item 2.
- **Alignment:** `style.align` maps to the widget's quadding.

### 5.6 Tables

- Rows flow through `segments` in order. Segment k takes up to `max_rows` rows, and row r of a segment sits at `row_top + r * row_height`, with cells spanning each column's `x` range and the row's height.
- Each cell's value is the row's value for the column `key` (object rows by property name, list rows by index), passed through the column's `transforms`. Missing cell values leave the cell empty without a warning, because sparse rows are normal.
- Rows left after the last segment are **dropped**: the report records rows provided, written and dropped, and any drop is also a top-level `rows_dropped` warning (main spec §7.9).
- Each cell is bound like a plain field (§5.3), so on fillable forms cells adopt the per-cell widgets under them.
- A table whose path has no value or an empty list writes nothing and is reported as `missing`.

### 5.7 Fill report

A pydantic model `FillReport` (JSON via `.to_json()`, like `Template`). The exact field names are the builder's, but it carries:

- **Header:** `template_id`, `fingerprint_ok` (bool), `strict` (bool), and counts: fields written, fields empty, warnings by code.
- **Per schema path** (every path in the flattened schema): `mapped` (bool), `has_value` (bool), `fields` (ids), `rendered` (per field: the final text, or `true`/`false` for choice fields), and `warnings`.
- **Per field:** id, type, widget name and how it was bound (`named` / `adopted` / `created`), final value or state, font size used, and warnings.
- **Per table:** rows provided, written and dropped, and per segment the rows written.
- **Form-side gaps:** template fields with no source, and mapped fields whose data was missing, so a reviewer sees which boxes on the form came out empty.
- **`warnings`:** one flat list of everything, each `{code, severity, path?, field?, message}`, sorted by page and then position, so an app can show it as a checklist.

Codes and severities (severity is `warning` unless marked info):
`missing`, `invalid`, `transform_error`, `unmapped_data`, `shrunk` (info), `overflow`, `truncated`, `unencodable`, `rows_dropped`, `widget_not_found`, `type_mismatch`, `adopted` (info), `font_substituted` (info), `list_joined` (info), `unknown_keys` (info), `fingerprint_mismatch`.

- `missing` is recorded per field. The top-level list includes it only for mapped paths, once per path, not once per box, so a blank optional section doesn't produce a page of noise.
- The report never contains values from other records and never logs data anywhere. It's returned to the caller only.

### 5.8 `preview` with data

`preview(template, pdf, data=...)` (main spec §7.8) renders the output of `fill` and draws the boxes over it, so a reviewer sees filled values and field outlines together. Small addition to the existing `formcast.render.preview`.

## 6. Fill-correctness metric (eval)

**What it must catch:** a template whose boxes and paths look right but whose data lands in the wrong place. The known case is a table built as one segment per column: rows flow down the first column's segment, then into the next column's segment, so values end up in the wrong cells, while today's scorer (rect plus column path) calls it correct.

**Approach: fill with probe records and check what lands in each ground-truth box.** It needs no OCR and no model, because our output keeps real fields: we read widget values and rects straight back from the filled PDF.

1. **Probe records** (`eval/probe.py`, generic, driven by the schema only). For each form, K records (K = the largest enum or multi-select option count in its schema, at least 2, no cap, so every option is "on" in at least one record; fills take milliseconds) where every value is **distinctive**, so you can tell where it came from:
   - strings: unique uppercase letter tokens (`QXJV`, …), honouring `maxLength`
   - dates (`format: date`): distinct dates whose day, month and year differ across paths
   - numbers and integers: distinct values within `minimum`/`maximum`
   - booleans: alternate true/false across records
   - enums: record k picks option k mod n
   - multi-select arrays: record k picks options k and k+1 mod n
   - tables: as many rows as the GT has rows for that table, **plus one** where the schema's `maxItems` allows it, every cell a distinct token (the extra row checks that the report counts it as dropped; tables capped by `maxItems` skip that check)
   - where the schema has a `pattern` or a `format` the generator can't satisfy (17 patterns in the corpus), a per-form override file `forms/probes/<id>.json` supplies values for those paths. Every probe record must pass `validate_data`.
2. **New GT label: the option value of each choice box.** Each GT entry that is a choice box (transforms `equals`/`contains`/`when`) gets `value`: the enum option (or list item) it stands for. Boolean checkboxes (`bool_to_check`) need no label. A drafting script proposes values from the nearest label text and, on fillable forms, the widget's on-state name. A subagent reviews each form. This is §0 item 6.
3. **Expected content per GT box**, for each probe record:
   - plain text box (no GT transforms): the path's value. With `also_paths` (joins), all of those values' tokens, in any order.
   - text box with GT transforms (split, slice, char_at, date and number formatting, case): **provenance** match, so the template's choice of format args isn't penalized: the box must be non-empty, every token in it must come from this path's value (a date's day, month or year in any format; a substring of a string token, compared case-insensitively), and no token of 3 or more characters may come from any other path's value. Single characters and 1–2 digit numbers can't be told apart across paths, so they only need to occur in this path's value. The generator gives each date path a different day, month and year where the calendar allows, to keep this check sharp.
   - choice box: scored **once per box across all K records**, not once per record: correct only if its state is right in every record. Every option is on in at least one record, so a box that is never ticked (an unmapped template) scores wrong, and so does one that is always ticked.
   - table cell box: GT boxes of one table column are ordered by page, then top to bottom, to give row indexes. The box for row i must hold row i's token for that column.
4. **Observed content:** fill the template with each probe record, read every widget (name, rect, value, on/off) from the output PDF, and match widgets to GT boxes with the same greedy IoU ≥ 0.3 matching the detection scorer uses, plus `eval/score.py`'s existing multi-line rule for tall multi-line widgets (the GT line that carries the path is the one scored). Only widgets that a template field wrote count, so a fillable form's untouched widgets earn nothing. A GT box with no matched widget scores as wrong.
5. **Metric:** `fill_acc` = correct units / all units, over GT boxes with a path, where a unit is a (text or table-cell box, record) pair or a choice box (scored across all records, step 3). It's reported per group (fillable / digital / scanned / all) alongside `path` and `joint`, plus sub-figures for text, choice and table cells, and a check that `rows_dropped` in the report equals the number of extra probe rows.
6. **Sanity checks, as tests:**
   - a seeded template (no paths) scores 0
   - a synthetic table-per-column template scores 1.0 on the old path metric and low on `fill_acc` (the loophole regression test)
   - a correct synthetic template on a generated form scores 1.0
7. **Re-score the archive:** run `fill_acc` over every saved Plan 1 template in `eval/results/templates.tar.gz` (no model calls) and add the results to main spec §14.3, including how much the table loophole inflated `path`/`joint` on table-heavy forms.

## 7. Plan 1 parked items, closed here

- **NaN in `set_style`:** `Style` rejects non-finite or non-positive `font_size` and `min_font_size`, and `min_font_size` greater than a numeric `font_size`. Both `set_style` and template loading reject them, so a saved template can't hold NaN. Add a round-trip test.
- **Stale main-spec §8 sketch:** update it to `result = build_template(...)`, `tpl = result.template`, and the new `fill` signature (no `mode`).
- **Radio kids sharing `existing_widget`:** handled at fill by rect and on-state (§5.3 rule 1).
- **`set_type` on a widget-linked field:** when `OpContext` has the form map, `set_type` (and `link_widget`) reject a type that doesn't suit the linked widget's kind. Without a form map, fill reports `type_mismatch` (§5.3) as the safety net.
- **Multi-select ruling:** one list value per path (§5.2).
- **`""` as missing:** applied at the source lookup for every field type (§5.2), not only in date and number transforms.

## 8. Calls I made (you can override)

- Whitespace-only strings count as missing, like `""`.
- An unjoined list on a text field renders as `", "`-joined, with an info note.
- Missing data clears a named or adopted widget, so refilling never leaves stale values behind.
- `radio_option` fields that fill creates become circle-style checkboxes, not a true radio group, because PyMuPDF's support for creating radio groups is unreliable. A recipient could tick two options in a group fill created. Existing radio groups on fillable forms stay real radio groups. If the builder finds PyMuPDF creates radio groups reliably, use them.
- `choice_mark` ("circle one") becomes a checkbox widget over the option text with a hand-built appearance: an ellipse when on, nothing when off. That way the smart PDF has something a person can click, and software reading field values (including the eval) sees the choice. If a spike shows PyMuPDF can't keep a custom appearance through `update()`, the fallback is an ellipse annotation drawn when on, with that limitation recorded as a ruling.
- Fill sets explicit font sizes and builds appearance streams itself, rather than relying on the viewer's "regenerate appearances" flag.
- `to_smart_pdf` returns bytes only, as the main spec sketch says.

## 9. Left to the builder

- Exact pydantic names for `FillReport` and its parts; module layout (suggested: `formcast/render/fill.py`, `formcast/render/fit.py`, `formcast/render/widgets.py`, `formcast/render/report.py`; `eval/probe.py`, `eval/fill_score.py`).
- Padding, the auto-size cap and the shrink step.
- New-widget naming scheme within the rules in §5.3.
- How `to_smart_pdf` surfaces placement problems (for example a returned list of warnings via a keyword, or logging).
- PyMuPDF specifics: verify radio-kid on-state handling, comb and max-length flags, appearance generation, and a custom `choice_mark` appearance with a short spike test before building on them. Record any surprise as a ruling.

## 10. Testing

- **Offline unit tests** for value resolution (missing rules, multi-select, `when`, transform errors), fitting (shrink, wrap, comb, overflow, unencodable), binding (named, radio kid by rect, adoption, type mismatch, not found), table flow across segments and pages (including dropped rows), report contents, and a fill → reopen → read-values round trip.
- **Fixtures:** synthetic PDFs built in the tests (a flat page, a page with text, checkbox and radio widgets, a rotated page, a cropbox offset from the mediabox), plus a few corpus forms marked slow.
- **Rotation and cropbox:** a field placed on a `/Rotate 90` page lands on the right spot in the output (reuse Plan 1's coordinate helpers).
- **Viewer check (manual, once):** open a filled fillable form, a filled flat form and a smart PDF in macOS Preview and one other viewer, and confirm that values show, fields are editable, and print-to-PDF keeps the values. Record the result in the plan's results section.
- **Eval:** `fill_acc` on dev and held-out from the saved templates (§6 step 7). No model calls are needed for any Plan 2 task.
