# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_defer-placement

- model: `claude-haiku-5-5` via `chat_completions`; tag: defer-placement; commit: `866942f`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 11 | 145,754 / 12,323 | 65 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 6 | 70,525 / 10,937 | 53 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 6 | 68,731 / 6,202 | 33 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | FFDetr | 1.00 (1.00) | 0.88 | 0.89 | 1.00 | 0.89 | 0.89 | 9 | 10 | 129,390 / 20,875 | 105 | finish | 3 |
| ndguard | flat | as_published | 33 | 43 | 0.94 (0.76) | 0.72 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.93 | 0.81 | 0.83 | 0.76 | 29 | 13 | 218,219 / 28,801 | 141 | finish | 1 |
| ndguard | flat | fake_scan | 33 | 41 | 0.97 (0.76) | 0.78 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.97 | 0.81 | 0.90 | 0.83 | 29 | 8 | 130,781 / 12,579 | 64 | finish | 4 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.69 | 0.89 | 1.00 | 114 | 12 | 323,768 / 29,646 | 144 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 130 | 0.95 (0.92) | 0.95 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.56 | 0.38 | 0.49 | 0.54 | 114 | 40 | 933,170 / 102,958 | 522 | finish | 9 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 0.24 | 0.74 | 1.00 | 170 | 7 | 169,102 / 11,117 | 54 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 209 | 0.87 (0.42) | 0.84 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.85 | 0.14 | 0.61 | 0.85 | 170 | 15 | 330,940 / 41,397 | 199 | finish | 2 |
| cms1500 | flat | as_published | 254 | 170 | 0.60 (0.52) | 0.90 | 0.95 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.41 | 0.26 | 0.38 | 0.41 | 227 | 32 | 948,449 / 77,651 | 382 | finish | 40 |
| cms1500 | flat | fake_scan | 254 | 28 | 0.06 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.04 | 0.05 | 0.04 | 0.04 | 227 | 20 | 422,783 / 44,862 | 219 | finish | 86 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 50 | 9 | 161,199 / 27,181 | 132 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.62 | 0.71 | 0.54 | 0.54 | 50 | 30 | 515,563 / 69,620 | 347 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.85 | 0.68 | 0.71 | 0.85 | 142 | 10 | 242,530 / 24,380 | 112 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.80 | 0.62 | 0.65 | 0.80 | 142 | 18 | 425,533 / 60,725 | 277 | finish | 3 |
| grandview_app | flat | as_published | 323 | 256 | 0.77 (0.74) | 0.97 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.62 | 0.44 | 0.56 | 0.56 | 247 | 40 | 1,190,647 / 111,324 | 557 | finish | 3 |
| grandview_app | flat | fake_scan | 323 | 222 | 0.63 (0.49) | 0.92 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.26 | 0.40 | 0.25 | 0.26 | 247 | 39 | 1,075,785 / 77,642 | 390 | finish | 61 |
| csudh_health | flat | as_published | 175 | 176 | 0.99 (0.99) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.99 | 0.99 | 0.99 | 0.99 | 155 | 15 | 413,693 / 54,976 | 254 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 139 | 0.67 (0.34) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.40 | 0.38 | 0.34 | 0.39 | 155 | 40 | 1,048,653 / 123,308 | 591 | budget | 0 |
| fw9 | fillable | as_published | 23 | 24 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 20 | 10 | 160,079 / 19,714 | 97 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.47 | 0.60 | 0.65 | 20 | 8 | 137,586 / 14,441 | 72 | finish | 2 |
| fw4 | fillable | as_published | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 11 | 209,022 / 19,180 | 99 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 46 | 0.90 (0.83) | 0.93 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.94 | 0.89 | 0.94 | 0.94 | 17 | 21 | 396,655 / 50,949 | 251 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 23 | 2906 | 2427 | 0.78 (0.66) | 0.93 | 0.98 | 0.75 (0.60) | 0.92 | 0.63 | 0.50 | 0.55 | 0.62 | 431 | 9,868,557 / 1,052,788 | 5159 |
| as_published | 12 (0) | 12 | 1453 | 1318 | 0.88 (0.85) | 0.97 | 0.98 | 0.85 (0.76) | 0.95 | 0.79 | 0.62 | 0.71 | 0.77 | 176 | 4,251,193 / 422,495 | 2071 |
| fake_scan | 12 (0) | 11 | 1453 | 1109 | 0.68 (0.47) | 0.88 | 0.98 | 0.65 (0.45) | 0.89 | 0.48 | 0.38 | 0.40 | 0.47 | 255 | 5,617,364 / 630,293 | 3088 |
