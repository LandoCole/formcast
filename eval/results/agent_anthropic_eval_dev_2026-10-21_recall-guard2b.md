# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_recall-guard2b

- model: `claude-haiku-5-5` via `chat_completions`; tag: recall-guard2b; commit: `6552696`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.88 | 1.00 | 0.88 | 0.82 | 17 | 7 | 100,714 / 10,891 | 58 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 0.88 | 1.00 | 0.88 | 0.82 | 17 | 7 | 92,305 / 14,882 | 73 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 5 | 54,674 / 5,028 | 25 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 0.95 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 9 | 113,044 / 24,342 | 122 | finish | 3 |
| ndguard | flat | as_published | 33 | 43 | 0.97 (0.70) | 0.74 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.97 | 0.81 | 0.86 | 0.79 | 29 | 28 | 468,198 / 53,910 | 276 | finish | 1 |
| ndguard | flat | fake_scan | 33 | 40 | 0.94 (0.76) | 0.78 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.90 | 0.69 | 0.76 | 0.76 | 29 | 14 | 243,882 / 50,249 | 243 | finish | 8 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.74 | 0.90 | 1.00 | 114 | 9 | 243,233 / 20,216 | 100 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 130 | 0.95 (0.92) | 0.95 | 0.94 | FFDetr | 0.94 (0.92) | 0.94 | 0.54 | 0.33 | 0.46 | 0.53 | 114 | 40 | 943,836 / 112,008 | 561 | budget | 0 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 10 | 252,512 / 14,581 | 77 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 209 | 0.87 (0.42) | 0.84 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.85 | 0.14 | 0.61 | 0.85 | 170 | 23 | 492,079 / 49,219 | 248 | finish | 2 |
| cms1500 | flat | as_published | 254 | 192 | 0.63 (0.51) | 0.84 | 0.98 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.37 | 0.29 | 0.34 | 0.37 | 227 | 40 | 1,194,641 / 82,538 | 429 | budget | 0 |
| cms1500 | flat | fake_scan | 254 | 35 | 0.07 (0.04) | 0.49 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.06 | 0.05 | 0.06 | 227 | 39 | 819,479 / 61,205 | 326 | finish | 0 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.94 | 0.98 | 0.96 | 50 | 30 | 517,652 / 38,153 | 198 | finish | 3 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.90 | 0.71 | 0.84 | 0.82 | 50 | 38 | 655,049 / 107,098 | 541 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.85 | 0.66 | 0.69 | 0.85 | 142 | 10 | 227,355 / 36,603 | 159 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.81 | 0.61 | 0.64 | 0.81 | 142 | 40 | 942,168 / 127,765 | 590 | finish | 3 |
| grandview_app | flat | as_published | 323 | 258 | 0.76 (0.74) | 0.96 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.49 | 0.46 | 0.43 | 0.45 | 247 | 40 | 1,168,330 / 92,344 | 476 | finish | 10 |
| grandview_app | flat | fake_scan | 323 | 219 | 0.62 (0.48) | 0.91 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.30 | 0.25 | 0.23 | 0.29 | 247 | 40 | 1,137,722 / 79,279 | 399 | finish | 45 |
| csudh_health | flat | as_published | 175 | 178 | 0.99 (0.99) | 0.98 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.88 | 0.81 | 0.79 | 0.88 | 155 | 40 | 1,104,674 / 113,331 | 544 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 139 | 0.66 (0.33) | 0.83 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.46 | 0.50 | 0.45 | 0.46 | 155 | 40 | 1,001,861 / 115,276 | 563 | budget | 0 |
| fw9 | fillable | as_published | 23 | 24 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.90 | 0.93 | 0.85 | 0.90 | 20 | 7 | 109,734 / 12,288 | 59 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.47 | 0.60 | 0.65 | 20 | 10 | 168,895 / 25,491 | 120 | finish | 2 |
| fw4 | fillable | as_published | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 0.94 | 17 | 10 | 185,876 / 29,892 | 151 | finish | 3 |
| fw4 | fillable | fake_scan | 48 | 45 | 0.90 (0.83) | 0.96 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 1.00 | 0.89 | 0.94 | 0.94 | 17 | 10 | 167,614 / 27,240 | 134 | finish | 6 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 21 | 2906 | 2455 | 0.78 (0.66) | 0.92 | 0.99 | 0.75 (0.60) | 0.92 | 0.62 | 0.51 | 0.55 | 0.61 | 546 | 12,405,527 / 1,303,829 | 6472 |
| as_published | 12 (0) | 11 | 1453 | 1344 | 0.88 (0.85) | 0.95 | 0.99 | 0.85 (0.76) | 0.95 | 0.74 | 0.65 | 0.68 | 0.72 | 236 | 5,627,593 / 509,775 | 2552 |
| fake_scan | 12 (0) | 10 | 1453 | 1111 | 0.67 (0.47) | 0.88 | 0.99 | 0.65 (0.45) | 0.89 | 0.50 | 0.38 | 0.42 | 0.49 | 310 | 6,777,934 / 794,054 | 3920 |
