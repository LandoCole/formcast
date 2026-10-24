# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-24_final-rest-agent, pipeline agent

- model: `claude-haiku-5-5` via `chat_completions`; tag: final-rest; commit: `39cfb74`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.94 | 0.98 | 0.95 | 0.96 | 50 | 13 | 244,236 / 29,083 | 143 | finish | 3 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.98 | 0.94 | 0.98 | 0.97 | 0.96 | 50 | 29 | 524,098 / 62,976 | 430 | finish | 2 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.99 | 0.98 | 0.98 | 0.98 | 0.99 | 142 | 9 | 207,606 / 28,518 | 123 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.80 | 0.57 | 0.60 | 0.93 | 0.80 | 142 | 22 | 539,816 / 48,103 | 344 | finish | 3 |
| grandview_app | flat | as_published | 323 | 259 | 0.78 (0.74) | 0.98 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.54 | 0.38 | 0.46 | 0.37 | 0.49 | 247 | 40 | 1,166,029 / 100,765 | 618 | finish | 12 |
| grandview_app | flat | fake_scan | 323 | 219 | 0.62 (0.48) | 0.91 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.20 | 0.25 | 0.14 | 0.08 | 0.20 | 247 | 39 | 1,071,954 / 72,839 | 360 | finish | 40 |
| csudh_health | flat | as_published | 175 | 178 | 0.99 (0.99) | 0.98 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.98 | 0.91 | 0.92 | 0.93 | 0.98 | 155 | 34 | 930,426 / 93,324 | 435 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 134 | 0.64 (0.31) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.30 | 0.41 | 0.30 | 0.32 | 0.30 | 155 | 38 | 1,032,184 / 109,546 | 516 | finish | 15 |
| fw9 | fillable | as_published | 23 | 24 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.90 | 0.93 | 0.85 | 0.85 | 0.90 | 20 | 6 | 90,059 / 10,176 | 50 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.47 | 0.60 | 0.92 | 0.65 | 20 | 18 | 314,804 / 43,860 | 209 | finish | 2 |
| fw4 | fillable | as_published | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 10 | 176,829 / 13,896 | 70 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 45 | 0.90 (0.83) | 0.96 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 23 | 397,390 / 38,164 | 186 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 12 (0) | 12 | 1570 | 1367 | 0.82 (0.69) | 0.94 | 0.99 | 0.81 (0.62) | 0.95 | 0.63 | 0.62 | 0.57 | 0.57 | 0.62 | 0.74 (0.67) | 281 | 6,695,431 / 651,250 | 3482 |
| as_published | 6 (0) | 6 | 785 | 726 | 0.91 (0.89) | 0.98 | 0.99 | 0.90 (0.74) | 0.97 | 0.81 | 0.79 | 0.76 | 0.72 | 0.79 | 0.87 (0.81) | 112 | 2,815,185 / 275,762 | 1438 |
| fake_scan | 6 (0) | 6 | 785 | 641 | 0.73 (0.50) | 0.90 | 0.99 | 0.73 (0.50) | 0.91 | 0.46 | 0.45 | 0.39 | 0.42 | 0.45 | 0.59 (0.50) | 169 | 3,880,246 / 375,488 | 2044 |

Cost at the built-in prices: $1.00.
