# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-24_check-rows-pages, pipeline pages

- model: `claude-haiku-5-5` via `chat_completions`; tag: check-rows; commit: `0613483`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 0 | 23,683 / 6,316 | 12 | finish | 0 |
| f8949 | fillable | fake_scan | 202 | 209 | 0.86 (0.42) | 0.83 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.92 | 0.88 | 0.92 | 0.47 | 0.92 | 170 | 0 | 17,959 / 9,272 | 23 | finish | 0 |
| cms1500 | flat | as_published | 254 | 147 | 0.52 (0.50) | 0.89 | 0.93 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 227 | 0 | 13,194 / 32,000 | 138 | finish | 0 |
| cms1500 | flat | fake_scan | 254 | 28 | 0.06 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.08 | 0.06 | 0.03 | 0.06 | 227 | 0 | 14,280 / 4,160 | 16 | finish | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 4 (0) | 4 | 912 | 586 | 0.57 (0.46) | 0.89 | 0.98 | 0.56 (0.46) | 0.88 | 0.43 | 0.34 | 0.43 | 0.31 | 0.43 | 0.74 (0.74) | 0 | 69,116 / 51,748 | 190 |
| as_published | 2 (0) | 2 | 456 | 349 | 0.73 (0.72) | 0.95 | 0.97 | 0.73 (0.71) | 0.93 | 0.43 | 0.33 | 0.43 | 0.40 | 0.43 | 0.59 (0.59) | 0 | 36,877 / 38,316 | 151 |
| fake_scan | 2 (0) | 2 | 456 | 237 | 0.41 (0.20) | 0.79 | 1.00 | 0.39 (0.20) | 0.81 | 0.43 | 0.35 | 0.43 | 0.21 | 0.43 | 1.00 (0.99) | 0 | 32,239 / 13,432 | 40 |

Page pass: 69,116 in / 51,748 out tokens (included in the totals above), 1 page(s) with an error or unparsed answer.

Cost at the built-in prices: $0.03.
