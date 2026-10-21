# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_round1

- model: `claude-haiku-5-5` via `chat_completions`; tag: round1; commit: `37835bb`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
- baseline file: `baseline_detection_2026-10-21.json`

Notes:

- Detection is scored on the manifest's scored pages that have at least one GT field (the baseline's pages); tables count one box per cell.
- Multi-line answer areas are representation-neutral: a plain template text field with style.multiline or at least 1.8x the page's median GT text-box height also covers every unmatched GT text box at least 70% inside it (recall), counts once for precision, and gets path credit through the GT line box that carries the path.
- Fillable forms as published: the template starts with one field per PDF widget (the real use case is mapping existing fields), and GT is the widgets, so agent-added boxes on blanks without a widget count as false positives.
- fake_scan: an image-only PDF of the baseline's seeded fake-scan renders (widgets stripped), so the agent faces the scanned-page path (deskew, FFDetr, OCR); on fillable forms it is a detection + mapping test.
- Baseline: bare-detector numbers from the baseline run on the same images: FFDNet-L (default for digital pages) for as_published digital/fillable forms, FFDetr (default for scanned pages) for fake_scan and scanned forms. On fillable as_published forms the baseline detects on the widget-stripped page.
- Mapping is over GT fields with a schema path. Transform credit uses equivalence groups {slice, split, char_at}, {date_part, date_format}, {equals, contains}, and a when equals true also credits bool_to_check; a template's join and when count as ops. joint = path and transforms right; exact = the matched field's source paths equal GT path + also_paths.
- Totals are micro-averaged over forms (sums of counts).

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| wadoc_auth | flat | as_published | 27 | 19 | 0.93 (0.93) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.88 | 0.25 | 0.71 | 0.82 | 17 | 6 | 73,061 / 4,853 | 27 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 0.25 | 0.82 | 0.94 | 17 | 6 | 61,821 / 13,232 | 63 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 0.50 | 0.89 | 1.00 | 9 | 6 | 74,934 / 4,818 | 26 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 23 | 0.91 (0.91) | 0.83 | 1.00 | FFDetr | 1.00 (1.00) | 0.88 | 0.78 | 0.50 | 0.67 | 0.78 | 9 | 10 | 104,356 / 17,621 | 91 | finish | 3 |
| ndguard | flat | as_published | 33 | 32 | 0.97 (0.79) | 1.00 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.97 | 0.62 | 0.76 | 0.83 | 29 | 6 | 74,988 / 17,294 | 83 | finish | 2 |
| ndguard | flat | fake_scan | 33 | 33 | 0.94 (0.73) | 0.91 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.93 | 0.62 | 0.72 | 0.76 | 29 | 7 | 92,425 / 18,568 | 97 | finish | 2 |
| i9 | fillable | as_published | 130 | 130 | 0.53 (0.53) | 0.53 | 0.87 | FFDNet-L | 0.95 (0.95) | 0.96 | 0.50 | 0.36 | 0.44 | 0.50 | 114 | 12 | 361,937 / 36,961 | 189 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 58 | 0.30 (0.27) | 0.67 | 0.97 | FFDetr | 0.94 (0.92) | 0.94 | 0.22 | 0.10 | 0.16 | 0.22 | 114 | 19 | 430,528 / 43,039 | 220 | finish | 16 |
| f8949 | fillable | as_published | 202 | 180 | 0.89 (0.24) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 0.24 | 0.74 | 1.00 | 170 | 14 | 374,813 / 16,195 | 84 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 154 | 0.73 (0.14) | 0.96 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.87 | 0.12 | 0.64 | 0.87 | 170 | 8 | 164,385 / 18,500 | 91 | finish | 2 |
| cms1500 | flat | as_published | 254 | 32 | 0.07 (0.06) | 0.59 | 1.00 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.08 | 0.06 | 0.07 | 0.07 | 227 | 17 | 468,383 / 30,732 | 166 | finish | 33 |
| cms1500 | flat | fake_scan | 254 | 18 | 0.04 (0.03) | 0.61 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.05 | 0.06 | 0.04 | 0.05 | 227 | 9 | 178,804 / 20,478 | 103 | finish | 4 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.64 | 0.76 | 0.56 | 0.60 | 50 | 9 | 189,186 / 17,315 | 84 | finish | 17 |
| menomonie_app | fillable | fake_scan | 56 | 48 | 0.54 (0.43) | 0.62 | 0.97 | FFDetr | 0.98 (0.91) | 0.98 | 0.48 | 0.29 | 0.36 | 0.40 | 50 | 17 | 287,719 / 32,957 | 178 | finish | 20 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 1.00 | 0.95 | 0.96 | 1.00 | 142 | 18 | 499,515 / 53,281 | 233 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 116 | 0.69 (0.23) | 0.96 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.77 | 0.57 | 0.60 | 0.77 | 142 | 12 | 290,549 / 24,093 | 119 | finish | 4 |
| grandview_app | flat | as_published | 323 | 110 | 0.32 (0.29) | 0.93 | 1.00 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.39 | 0.30 | 0.35 | 0.38 | 247 | 27 | 735,053 / 66,357 | 350 | finish | 5 |
| grandview_app | flat | fake_scan | 323 | 60 | 0.15 (0.09) | 0.80 | 0.98 | FFDetr | 0.59 (0.49) | 0.92 | 0.15 | 0.26 | 0.14 | 0.15 | 247 | 14 | 368,764 / 29,696 | 154 | finish | 6 |
| csudh_health | flat | as_published | 175 | 150 | 0.82 (0.81) | 0.95 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.88 | 0.77 | 0.77 | 0.88 | 155 | 24 | 625,394 / 64,625 | 314 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 116 | 0.51 (0.27) | 0.77 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.52 | 0.47 | 0.45 | 0.51 | 155 | 28 | 684,020 / 68,653 | 345 | finish | 11 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.80 | 0.93 | 0.80 | 0.80 | 20 | 6 | 87,542 / 16,872 | 83 | finish | 3 |
| fw9 | fillable | fake_scan | 23 | 22 | 0.30 (0.17) | 0.32 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.35 | 0.07 | 0.25 | 0.30 | 20 | 9 | 147,449 / 25,214 | 126 | finish | 2 |
| fw4 | fillable | as_published | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 1.00 | 17 | 12 | 231,543 / 26,676 | 135 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 20 | 0.33 (0.29) | 0.80 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.88 | 0.78 | 0.82 | 0.82 | 17 | 11 | 187,749 / 26,441 | 130 | finish | 3 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 24 | 2906 | 1656 | 0.50 (0.35) | 0.87 | 0.99 | 0.75 (0.60) | 0.92 | 0.52 | 0.42 | 0.43 | 0.51 | 307 | 6,794,918 / 694,471 | 3490 |
| as_published | 12 (0) | 12 | 1453 | 969 | 0.61 (0.50) | 0.90 | 0.99 | 0.85 (0.76) | 0.95 | 0.62 | 0.52 | 0.53 | 0.61 | 157 | 3,796,349 / 355,979 | 1773 |
| fake_scan | 12 (0) | 12 | 1453 | 687 | 0.40 (0.20) | 0.83 | 0.99 | 0.65 (0.45) | 0.89 | 0.43 | 0.32 | 0.34 | 0.41 | 150 | 2,998,569 / 338,492 | 1718 |
