# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_check2a

- model: `claude-haiku-5-5` via `chat_completions`; tag: check2a; commit: `aa37fa6`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
- fill (fill_acc): the template is filled (formcast fill, real form fields) into the variant's PDF with K distinctive probe records (eval/probe.py), and every GT box with a path is checked for what its widget shows: a text or table-cell box once per record (the exact value; by provenance when GT has transforms), a choice box once across all records (right state in every record). fill_acc = correct units / units. It catches data landing in the wrong box, e.g. a table built as one segment per column, which path/joint score as right. A choice box whose expected state is the same in every probe record can't tell an always-on (or never-on) box from a right one; the json counts them as fill.static_choice.

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| grandview_app | flat | as_published | 323 | 243 | 0.71 (0.65) | 0.94 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.65 | 0.40 | 0.57 | 0.44 | 0.60 | 247 | 39 | 1,147,695 / 93,688 | 481 | finish | 5 |
| grandview_app | flat | fake_scan | 323 | 200 | 0.56 (0.44) | 0.91 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.30 | 0.33 | 0.26 | 0.14 | 0.28 | 247 | 33 | 889,803 / 85,104 | 438 | finish | 5 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.67 | 0.88 | 0.94 | 1.00 | 114 | 8 | 193,549 / 24,202 | 113 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 112 | 0.85 (0.82) | 0.98 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.54 | 0.14 | 0.40 | 0.46 | 0.54 | 114 | 32 | 744,514 / 96,186 | 494 | finish | 14 |
| cms1500 | flat | as_published | 254 | 135 | 0.49 (0.47) | 0.93 | 0.99 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.17 | 0.25 | 0.16 | 0.12 | 0.17 | 227 | 35 | 1,050,654 / 76,729 | 405 | finish | 9 |
| cms1500 | flat | fake_scan | 254 | 32 | 0.06 (0.02) | 0.50 | 0.88 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.04 | 0.04 | 0.03 | 0.05 | 227 | 29 | 538,642 / 55,779 | 290 | finish | 0 |
| wadoc_auth | flat | as_published | 27 | 19 | 0.96 (0.96) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.94 | 1.00 | 0.94 | 0.88 | 0.88 | 17 | 6 | 74,552 / 5,946 | 32 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 6 | 68,783 / 10,108 | 49 | finish | 4 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 8 | 1468 | 890 | 0.57 (0.52) | 0.93 | 0.97 | 0.60 (0.55) | 0.92 | 0.41 | 0.29 | 0.36 | 0.26 | 0.39 | 188 | 4,708,192 / 447,742 | 2301 |
| as_published | 4 (0) | 4 | 734 | 527 | 0.69 (0.66) | 0.95 | 0.98 | 0.72 (0.70) | 0.92 | 0.55 | 0.38 | 0.48 | 0.38 | 0.52 | 88 | 2,466,450 / 200,565 | 1030 |
| fake_scan | 4 (0) | 4 | 734 | 363 | 0.45 (0.38) | 0.90 | 0.97 | 0.48 (0.40) | 0.92 | 0.27 | 0.19 | 0.23 | 0.15 | 0.26 | 100 | 2,241,742 / 247,177 | 1271 |

Re-scored: 2026-10-21 at commit 33e4f50: the saved templates were scored again with the stacked-lines rule for multi-line fields and the fill-correctness metric (fill); model output is unchanged.
