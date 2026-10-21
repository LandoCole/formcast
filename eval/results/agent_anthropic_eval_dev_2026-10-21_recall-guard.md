# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_recall-guard

- model: `claude-haiku-5-5` via `chat_completions`; tag: recall-guard; commit: `ea65013`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| wadoc_auth | flat | as_published | 27 | 19 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 7 | 89,947 / 8,412 | 52 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 8 | 114,487 / 16,668 | 86 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 5 | 52,689 / 5,308 | 27 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 24 | 0.95 (0.95) | 0.88 | 0.95 | FFDetr | 1.00 (1.00) | 0.88 | 0.89 | 1.00 | 0.89 | 0.89 | 9 | 15 | 199,516 / 37,865 | 192 | finish | 3 |
| ndguard | flat | as_published | 33 | 31 | 0.88 (0.76) | 0.94 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.90 | 0.81 | 0.79 | 0.72 | 29 | 31 | 514,771 / 79,551 | 404 | finish | 3 |
| ndguard | flat | fake_scan | 33 | 33 | 0.91 (0.64) | 0.91 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.93 | 0.81 | 0.83 | 0.76 | 29 | 10 | 155,365 / 18,605 | 96 | finish | 2 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.76 | 0.91 | 1.00 | 114 | 9 | 239,582 / 26,490 | 124 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 114 | 0.85 (0.83) | 0.97 | 0.97 | FFDetr | 0.94 (0.92) | 0.94 | 0.51 | 0.19 | 0.41 | 0.51 | 114 | 38 | 958,084 / 113,970 | 579 | finish | 19 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 6 | 158,173 / 14,013 | 71 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 195 | 0.84 (0.40) | 0.87 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.85 | 0.83 | 0.85 | 0.85 | 170 | 14 | 297,311 / 37,305 | 182 | finish | 2 |
| cms1500 | flat | as_published | 254 | 234 | 0.64 (0.62) | 0.69 | 0.96 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.42 | 0.22 | 0.38 | 0.42 | 227 | 39 | 1,164,125 / 128,072 | 648 | finish | 0 |
| cms1500 | flat | fake_scan | 254 | 27 | 0.06 (0.03) | 0.56 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.09 | 0.06 | 0.06 | 227 | 33 | 725,830 / 59,885 | 309 | finish | 94 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 50 | 11 | 199,532 / 28,375 | 140 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 57 | 0.82 (0.50) | 0.81 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.86 | 0.71 | 0.78 | 0.78 | 50 | 40 | 729,402 / 114,621 | 579 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.86 | 0.84 | 0.86 | 0.86 | 142 | 9 | 211,512 / 30,394 | 140 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 132 | 0.79 (0.42) | 0.96 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.82 | 0.62 | 0.65 | 0.82 | 142 | 15 | 362,591 / 45,110 | 219 | finish | 3 |
| grandview_app | flat | as_published | 323 | 256 | 0.76 (0.73) | 0.96 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.38 | 0.32 | 0.32 | 0.36 | 247 | 40 | 1,211,260 / 108,183 | 550 | budget | 0 |
| grandview_app | flat | fake_scan | 323 | 199 | 0.57 (0.44) | 0.93 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.28 | 0.38 | 0.25 | 0.27 | 247 | 40 | 1,181,718 / 84,257 | 421 | finish | 0 |
| csudh_health | flat | as_published | 175 | 178 | 1.00 (0.99) | 0.98 | 1.00 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.99 | 0.89 | 0.90 | 0.99 | 155 | 24 | 683,971 / 56,235 | 280 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 140 | 0.67 (0.34) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.48 | 0.55 | 0.47 | 0.47 | 155 | 39 | 1,048,969 / 98,762 | 496 | finish | 25 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 20 | 7 | 106,638 / 13,376 | 63 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 25 | 0.61 (0.48) | 0.56 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.47 | 0.60 | 0.65 | 20 | 9 | 154,429 / 22,836 | 110 | finish | 2 |
| fw4 | fillable | as_published | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 0.94 | 17 | 14 | 278,405 / 36,984 | 187 | finish | 3 |
| fw4 | fillable | fake_scan | 48 | 46 | 0.85 (0.79) | 0.89 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.88 | 0.56 | 0.76 | 0.76 | 17 | 23 | 403,069 / 36,075 | 189 | finish | 7 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 23 | 2906 | 2377 | 0.75 (0.64) | 0.91 | 0.99 | 0.75 (0.60) | 0.92 | 0.62 | 0.57 | 0.58 | 0.61 | 486 | 11,241,376 / 1,221,352 | 6144 |
| as_published | 12 (0) | 11 | 1453 | 1366 | 0.88 (0.87) | 0.93 | 0.99 | 0.85 (0.76) | 0.95 | 0.74 | 0.67 | 0.70 | 0.73 | 202 | 4,910,605 / 535,393 | 2686 |
| fake_scan | 12 (0) | 12 | 1453 | 1011 | 0.62 (0.42) | 0.89 | 0.99 | 0.65 (0.45) | 0.89 | 0.50 | 0.47 | 0.46 | 0.49 | 284 | 6,330,771 / 685,959 | 3458 |
