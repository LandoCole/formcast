# Agent eval: gpt-6-luna (OPENAI_EVAL), dev, 2026-10-21_round1

- model: `gpt-6-luna` via `responses`; tag: round1; commit: `37835bb`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 19 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.25 | 0.82 | 0.94 | 0.94 | 17 | 4 | 29,165 / 2,291 | 26 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.78) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 5 | 47,094 / 3,854 | 44 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 6 | 58,223 / 2,300 | 33 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 24 | 1.00 (1.00) | 0.83 | 1.00 | FFDetr | 1.00 (1.00) | 0.88 | 0.89 | 0.50 | 0.78 | 0.89 | 0.89 | 9 | 7 | 83,422 / 5,250 | 60 | finish | 3 |
| ndguard | flat | as_published | 33 | 31 | 0.97 (0.79) | 1.00 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 1.00 | 0.62 | 0.79 | 0.87 | 0.90 | 29 | 9 | 139,366 / 6,294 | 72 | finish | 1 |
| ndguard | flat | fake_scan | 33 | 31 | 0.88 (0.70) | 0.94 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.93 | 0.69 | 0.76 | 0.78 | 0.83 | 29 | 9 | 127,211 / 5,273 | 59 | finish | 1 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 0.39 | 0.36 | 0.35 | 0.37 | 0.39 | 114 | 12 | 280,891 / 7,164 | 82 | finish | 27 |
| i9 | fillable | fake_scan | 130 | 42 | 0.25 (0.22) | 0.79 | 1.00 | FFDetr | 0.94 (0.92) | 0.94 | 0.20 | 0.14 | 0.16 | 0.18 | 0.20 | 114 | 34 | 654,096 / 16,143 | 192 | finish | 28 |
| f8949 | fillable | as_published | 202 | 180 | 0.89 (0.24) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 7 | 162,669 / 3,988 | 46 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 164 | 0.79 (0.13) | 0.98 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.89 | 0.19 | 0.66 | 0.90 | 0.89 | 170 | 22 | 367,576 / 8,125 | 103 | finish | 2 |
| cms1500 | flat | as_published | 254 | 199 | 0.50 (0.17) | 0.63 | 1.00 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.38 | 0.19 | 0.31 | 0.32 | 0.37 | 227 | 24 | 574,268 / 19,096 | 203 | finish | 5 |
| cms1500 | flat | fake_scan | 254 | 182 | 0.25 (0.11) | 0.31 | 0.98 | FFDetr | 0.04 (0.01) | 0.61 | 0.17 | 0.13 | 0.15 | 0.12 | 0.16 | 227 | 21 | 348,169 / 11,121 | 124 | finish | 2 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.64 | 0.82 | 0.58 | 0.52 | 0.62 | 50 | 6 | 84,746 / 6,092 | 64 | finish | 14 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.96 (0.86) | 0.93 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.60 | 0.71 | 0.54 | 0.51 | 0.58 | 50 | 17 | 305,187 / 10,634 | 116 | finish | 14 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.86 | 0.64 | 0.68 | 0.96 | 0.86 | 142 | 12 | 246,715 / 8,389 | 93 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 145 | 0.88 (0.46) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.96 | 0.96 | 0.96 | 1.00 | 0.96 | 142 | 40 | 773,683 / 20,069 | 232 | budget | 0 |
| grandview_app | flat | as_published | 323 | 228 | 0.39 (0.33) | 0.52 | 1.00 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.47 | 0.47 | 0.43 | 0.36 | 0.45 | 247 | 22 | 559,514 / 11,379 | 133 | finish | 2 |
| grandview_app | flat | fake_scan | 323 | 246 | 0.35 (0.26) | 0.44 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.36 | 0.36 | 0.32 | 0.24 | 0.35 | 247 | 11 | 269,310 / 10,091 | 103 | finish | 2 |
| csudh_health | flat | as_published | 175 | 162 | 0.90 (0.89) | 0.97 | 1.00 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.97 | 0.82 | 0.84 | 0.90 | 0.97 | 155 | 25 | 559,098 / 16,247 | 182 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 158 | 0.78 (0.37) | 0.86 | 0.99 | FFDetr | 0.67 (0.31) | 0.89 | 0.78 | 0.67 | 0.66 | 0.82 | 0.78 | 155 | 22 | 466,037 / 16,416 | 182 | finish | 3 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 20 | 11 | 182,080 / 7,140 | 84 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 22 | 0.48 (0.17) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.40 | 0.13 | 0.35 | 0.54 | 0.40 | 20 | 12 | 204,613 / 5,636 | 70 | finish | 2 |
| fw4 | fillable | as_published | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 12 | 202,752 / 5,137 | 60 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 50 | 0.90 (0.81) | 0.86 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.94 | 0.67 | 0.76 | 0.78 | 0.82 | 17 | 21 | 398,475 / 10,861 | 129 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 23 | 2906 | 2404 | 0.66 (0.45) | 0.78 | 0.99 | 0.75 (0.60) | 0.92 | 0.62 | 0.55 | 0.54 | 0.60 | 0.61 | 371 | 7,124,360 / 218,990 | 2492 |
| as_published | 12 (0) | 12 | 1453 | 1265 | 0.75 (0.58) | 0.85 | 0.99 | 0.85 (0.76) | 0.95 | 0.68 | 0.60 | 0.61 | 0.65 | 0.67 | 150 | 3,079,487 / 95,517 | 1079 |
| fake_scan | 12 (0) | 11 | 1453 | 1139 | 0.57 (0.32) | 0.71 | 1.00 | 0.65 (0.45) | 0.89 | 0.56 | 0.49 | 0.48 | 0.55 | 0.55 | 221 | 4,044,873 / 123,473 | 1413 |

Re-scored: 2026-10-21 at commit 33e4f50-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields and the fill-correctness metric (fill); model output is unchanged.
