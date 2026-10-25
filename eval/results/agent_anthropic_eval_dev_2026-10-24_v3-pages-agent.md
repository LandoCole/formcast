# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-24_v3-pages-agent, pipeline pages+agent

- model: `claude-haiku-5-5` via `chat_completions`; tag: v3; commit: `39cfb74`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
- baseline file: `baseline_detection_2026-10-21.json`

Notes:

- Detection is scored on the manifest's scored pages that have at least one GT field (the baseline's pages); tables count one box per cell.
- Multi-line answer areas are representation-neutral: a plain template text field with style.multiline or at least 1.8x the page's median GT text-box height also covers every unmatched GT text box at least 70% inside it (recall), counts once for precision, and gets path credit through the GT line box that carries the path. It only does so when at least two such GT boxes are stacked in one column inside it (horizontal overlap of 50% or more); a single loose box is scored by the normal IoU threshold.
- Fillable forms as published: the template starts with one field per PDF widget (the real use case is mapping existing fields), and GT is the widgets, so agent-added boxes on blanks without a widget count as false positives.
- fake_scan: an image-only PDF of the baseline's seeded fake-scan renders (widgets stripped), so the agent faces the scanned-page path (deskew, FFDetr, OCR); on fillable forms it is a detection + mapping test.
- Baseline: bare-detector numbers from the baseline run on the same images: FFDNet-L (default for digital pages) for as_published digital/fillable forms, FFDetr (default for scanned pages) for fake_scan and scanned forms. On fillable as_published forms the baseline detects on the widget-stripped page.
- Mapping is over GT fields with a schema path. Transform credit uses equivalence groups {slice, split, char_at}, {date_part, date_format}, {equals, contains}, and a when equals true also credits bool_to_check; a template's join and when count as ops. joint = path and transforms right; exact = the matched field's source paths equal GT path + also_paths.
- Totals are micro-averaged over forms (sums of counts). Sessions that ended in an error (infrastructure or harness) are listed in the per-form table but excluded from the totals' detection and mapping figures; their templates are partial or untouched seeds.
- The agent's form map carries the neutral source name 'form.pdf', not the file name.
- mapping_reachable: path/joint accuracy over GT fields that some seeded box (widget or detector candidate, before any edit) overlaps at IoU >= 0.3 — what a mapping-only step can reach.
- fill (fill_acc): the template is filled (formcast fill, real form fields) into the variant's PDF with K distinctive probe records (eval/probe.py), and every GT box with a path is checked for what its widget shows: a text or table-cell box once per record (the exact value; by provenance when GT has transforms), a choice box once across all records (right state in every record). fill_acc = correct units / units. It catches data landing in the wrong box, e.g. a table built as one segment per column, which path/joint score as right. A choice box whose expected state is the same in every probe record can't tell an always-on (or never-on) box from a right one; the json counts them as fill.static_choice.

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 4 | 60,284 / 4,726 | 25 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 6 | 94,706 / 7,268 | 40 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 4 | 60,657 / 5,820 | 30 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 4 | 60,833 / 6,164 | 34 | finish | 3 |
| ndguard | flat | as_published | 33 | 39 | 0.91 (0.85) | 0.77 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.97 | 0.88 | 0.90 | 0.78 | 0.83 | 29 | 5 | 96,293 / 17,167 | 84 | finish | 3 |
| ndguard | flat | fake_scan | 33 | 41 | 0.97 (0.76) | 0.78 | 0.97 | FFDetr | 0.76 (0.64) | 0.74 | 1.00 | 0.75 | 0.86 | 0.82 | 0.86 | 29 | 7 | 133,247 / 22,739 | 111 | finish | 6 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.79 | 0.92 | 0.94 | 1.00 | 114 | 8 | 333,283 / 34,632 | 146 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 130 | 0.95 (0.92) | 0.95 | 1.00 | FFDetr | 0.94 (0.92) | 0.94 | 0.97 | 0.29 | 0.73 | 0.91 | 0.96 | 114 | 28 | 989,230 / 77,241 | 422 | finish | 4 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 1.00 | 170 | 10 | 371,985 / 21,267 | 87 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 212 | 0.87 (0.42) | 0.83 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.93 | 0.90 | 0.93 | 0.47 | 0.93 | 170 | 10 | 295,369 / 30,123 | 146 | finish | 2 |
| cms1500 | flat | as_published | 254 | 152 | 0.54 (0.52) | 0.89 | 0.98 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.29 | 0.37 | 0.28 | 0.22 | 0.29 | 227 | 23 | 792,570 / 96,190 | 477 | finish | 37 |
| cms1500 | flat | fake_scan | 254 | 165 | 0.39 (0.09) | 0.59 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.43 | 0.11 | 0.40 | 0.43 | 0.43 | 227 | 21 | 607,992 / 70,064 | 375 | finish | 11 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.88 | 0.96 | 0.95 | 0.96 | 50 | 14 | 408,592 / 45,493 | 209 | finish | 3 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.98 | 0.82 | 0.94 | 0.94 | 0.94 | 50 | 12 | 344,995 / 33,190 | 167 | finish | 3 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 142 | 12 | 408,325 / 30,868 | 107 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.96 | 0.79 | 0.81 | 1.00 | 0.96 | 142 | 22 | 695,111 / 59,289 | 264 | finish | 3 |
| grandview_app | flat | as_published | 323 | 256 | 0.77 (0.74) | 0.97 | 0.98 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.77 | 0.60 | 0.70 | 0.41 | 0.72 | 247 | 21 | 858,803 / 76,717 | 327 | finish | 2 |
| grandview_app | flat | fake_scan | 323 | 224 | 0.63 (0.49) | 0.91 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.66 | 0.53 | 0.59 | 0.33 | 0.62 | 247 | 18 | 708,350 / 69,240 | 306 | finish | 13 |
| csudh_health | flat | as_published | 175 | 176 | 0.99 (0.99) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.99 | 0.73 | 0.75 | 0.93 | 0.98 | 155 | 18 | 627,800 / 83,923 | 371 | finish | 4 |
| csudh_health | flat | fake_scan | 175 | 155 | 0.74 (0.37) | 0.83 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.74 | 0.62 | 0.65 | 0.86 | 0.73 | 155 | 34 | 1,047,844 / 127,713 | 609 | finish | 2 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 1.00 | 20 | 9 | 182,610 / 11,022 | 58 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.47 | 0.60 | 0.92 | 0.65 | 20 | 6 | 160,763 / 20,825 | 104 | finish | 2 |
| fw4 | fillable | as_published | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 5 | 145,192 / 10,550 | 50 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 45 | 0.90 (0.83) | 0.96 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 1.00 | 0.78 | 0.88 | 0.85 | 0.88 | 17 | 7 | 187,404 / 15,129 | 86 | finish | 7 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 24 | 2906 | 2563 | 0.80 (0.67) | 0.91 | 0.99 | 0.75 (0.60) | 0.92 | 0.79 | 0.65 | 0.72 | 0.66 | 0.77 | 0.95 (0.87) | 308 | 9,672,238 / 977,360 | 4636 |
| as_published | 12 (0) | 12 | 1453 | 1297 | 0.86 (0.85) | 0.97 | 0.98 | 0.85 (0.76) | 0.95 | 0.82 | 0.74 | 0.76 | 0.69 | 0.80 | 0.93 (0.87) | 133 | 4,346,394 / 438,375 | 1971 |
| fake_scan | 12 (0) | 12 | 1453 | 1266 | 0.74 (0.48) | 0.85 | 0.99 | 0.65 (0.45) | 0.89 | 0.77 | 0.56 | 0.68 | 0.63 | 0.75 | 0.98 (0.87) | 175 | 5,325,844 / 538,985 | 2665 |

Page pass: 414,447 in / 226,292 out tokens (included in the totals above), 0 page(s) with an error or unparsed answer.

Cost at the built-in prices: $1.46.
