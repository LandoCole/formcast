# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-23_final-pages, pipeline pages

- model: `claude-haiku-5-5` via `chat_completions`; tag: final; commit: `849f144`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 0 | 5,677 / 1,509 | 6 | finish | 0 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 0.92 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 0 | 5,597 / 1,849 | 9 | finish | 0 |
| coleman_pir | fillable | as_published | 22 | 22 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 0 | 5,537 / 1,020 | 5 | finish | 0 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 0 | 5,404 / 1,339 | 8 | finish | 0 |
| ndguard | flat | as_published | 33 | 32 | 0.67 (0.64) | 0.69 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.66 | 0.56 | 0.55 | 0.49 | 0.52 | 29 | 0 | 6,288 / 2,647 | 16 | finish | 0 |
| ndguard | flat | fake_scan | 33 | 34 | 0.76 (0.64) | 0.74 | 0.76 | FFDetr | 0.76 (0.64) | 0.74 | 0.72 | 0.62 | 0.66 | 0.53 | 0.59 | 29 | 0 | 6,392 / 2,642 | 15 | finish | 0 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 1.00 | 1.00 | 0.40 | 1.00 | 114 | 0 | 27,251 / 9,686 | 19 | finish | 0 |
| i9 | fillable | fake_scan | 130 | 129 | 0.95 (0.92) | 0.95 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.96 | 0.81 | 0.92 | 0.33 | 0.96 | 114 | 0 | 29,049 / 9,478 | 23 | finish | 0 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 0 | 23,481 / 6,471 | 14 | finish | 0 |
| f8949 | fillable | fake_scan | 202 | 209 | 0.86 (0.42) | 0.83 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.92 | 0.88 | 0.92 | 0.47 | 0.92 | 170 | 0 | 17,757 / 7,839 | 19 | finish | 0 |
| cms1500 | flat | as_published | 254 | 172 | 0.61 (0.50) | 0.90 | 0.98 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.59 | 0.38 | 0.55 | 0.54 | 0.59 | 227 | 0 | 13,093 / 25,660 | 104 | finish | 0 |
| cms1500 | flat | fake_scan | 254 | 28 | 0.06 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.09 | 0.06 | 0.02 | 0.06 | 227 | 0 | 14,078 / 4,223 | 17 | finish | 0 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.82 | 0.94 | 0.47 | 0.94 | 50 | 0 | 18,501 / 5,215 | 10 | finish | 0 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.98 | 0.76 | 0.92 | 0.46 | 0.92 | 50 | 0 | 17,652 / 5,508 | 12 | finish | 0 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.99 | 0.99 | 0.99 | 0.98 | 0.99 | 142 | 0 | 21,832 / 13,395 | 24 | finish | 0 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.82 | 0.65 | 0.68 | 0.98 | 0.82 | 142 | 0 | 20,461 / 12,067 | 26 | finish | 0 |
| grandview_app | flat | as_published | 323 | 255 | 0.76 (0.74) | 0.96 | 0.98 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.77 | 0.50 | 0.65 | 0.40 | 0.70 | 247 | 0 | 36,591 / 25,927 | 47 | finish | 0 |
| grandview_app | flat | fake_scan | 323 | 222 | 0.62 (0.49) | 0.90 | 0.96 | FFDetr | 0.59 (0.49) | 0.92 | 0.64 | 0.53 | 0.57 | 0.27 | 0.60 | 247 | 0 | 35,074 / 24,129 | 44 | finish | 0 |
| csudh_health | flat | as_published | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.97 | 0.89 | 0.90 | 0.93 | 0.96 | 155 | 0 | 21,808 / 14,896 | 26 | finish | 0 |
| csudh_health | flat | fake_scan | 175 | 134 | 0.64 (0.31) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.57 | 0.60 | 0.54 | 0.56 | 0.55 | 155 | 0 | 19,754 / 20,278 | 46 | finish | 0 |
| fw9 | fillable | as_published | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.95 | 0.93 | 0.95 | 0.92 | 0.95 | 20 | 0 | 6,815 / 2,176 | 9 | finish | 0 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.60 | 0.47 | 0.60 | 0.85 | 0.60 | 20 | 0 | 16,485 / 4,146 | 17 | finish | 0 |
| fw4 | fillable | as_published | 48 | 48 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.78 | 0.88 | 0.85 | 0.88 | 17 | 0 | 17,972 / 2,308 | 7 | finish | 0 |
| fw4 | fillable | fake_scan | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.94 | 0.78 | 0.88 | 0.90 | 0.88 | 17 | 0 | 16,646 / 2,452 | 11 | finish | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 24 | 2906 | 2394 | 0.76 (0.65) | 0.93 | 0.98 | 0.75 (0.60) | 0.92 | 0.75 | 0.66 | 0.70 | 0.57 | 0.73 | 0.96 (0.90) | 0 | 409,195 / 206,860 | 534 |
| as_published | 12 (0) | 12 | 1453 | 1300 | 0.87 (0.84) | 0.97 | 0.98 | 0.85 (0.76) | 0.95 | 0.86 | 0.76 | 0.81 | 0.70 | 0.84 | 0.98 (0.92) | 0 | 204,846 / 110,910 | 289 |
| fake_scan | 12 (0) | 12 | 1453 | 1094 | 0.66 (0.46) | 0.88 | 0.97 | 0.65 (0.45) | 0.89 | 0.64 | 0.55 | 0.60 | 0.44 | 0.62 | 0.94 (0.88) | 0 | 204,349 / 95,950 | 246 |

Page pass: 409,195 in / 206,860 out tokens (included in the totals above), 0 page(s) with an error or unparsed answer.

Cost at the built-in prices: $0.14.
