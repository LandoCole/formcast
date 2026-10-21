# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_round2

- model: `claude-haiku-5-5` via `chat_completions`; tag: round2; commit: `76cbfa2`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 19 | 0.96 (0.96) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.94 | 1.00 | 0.94 | 0.88 | 17 | 5 | 53,192 / 5,535 | 28 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 10 | 135,026 / 15,381 | 80 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 5 | 50,827 / 6,492 | 34 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 23 | 1.00 (1.00) | 0.87 | 0.95 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 0.50 | 0.89 | 1.00 | 9 | 6 | 68,520 / 11,885 | 59 | finish | 3 |
| ndguard | flat | as_published | 33 | 33 | 0.88 (0.70) | 0.88 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.90 | 0.81 | 0.79 | 0.72 | 29 | 25 | 405,546 / 45,182 | 226 | finish | 1 |
| ndguard | flat | fake_scan | 33 | 33 | 0.94 (0.70) | 0.94 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.97 | 0.81 | 0.86 | 0.79 | 29 | 7 | 102,628 / 19,676 | 95 | finish | 2 |
| i9 | fillable | as_published | 130 | 127 | 0.98 (0.98) | 1.00 | 1.00 | FFDNet-L | 0.95 (0.95) | 0.96 | 0.90 | 0.67 | 0.81 | 0.90 | 114 | 11 | 301,534 / 27,709 | 137 | finish | 7 |
| i9 | fillable | fake_scan | 130 | 116 | 0.85 (0.78) | 0.96 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.29 | 0.17 | 0.23 | 0.28 | 114 | 26 | 594,220 / 72,252 | 362 | finish | 15 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (0.35) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 9 | 222,835 / 21,831 | 108 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 183 | 0.84 (0.23) | 0.92 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.88 | 0.14 | 0.64 | 0.88 | 170 | 17 | 359,001 / 60,890 | 300 | finish | 2 |
| cms1500 | flat | as_published | 254 | 164 | 0.56 (0.38) | 0.87 | 0.95 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.33 | 0.26 | 0.30 | 0.33 | 227 | 34 | 984,285 / 80,034 | 422 | finish | 39 |
| cms1500 | flat | fake_scan | 254 | 18 | 0.04 (0.02) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.04 | 0.05 | 0.04 | 0.04 | 227 | 10 | 192,213 / 21,994 | 108 | finish | 57 |
| menomonie_app | fillable | as_published | 56 | 54 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.94 | 0.98 | 0.96 | 50 | 12 | 213,282 / 34,292 | 173 | finish | 3 |
| menomonie_app | fillable | fake_scan | 56 | 58 | 0.75 (0.54) | 0.72 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.72 | 0.65 | 0.62 | 0.64 | 50 | 36 | 618,628 / 83,970 | 438 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 150 | 0.94 (0.94) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.85 | 0.66 | 0.69 | 0.85 | 142 | 7 | 146,604 / 35,305 | 149 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 127 | 0.76 (0.39) | 0.96 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.80 | 0.60 | 0.63 | 0.80 | 142 | 21 | 507,750 / 44,377 | 227 | finish | 3 |
| grandview_app | flat | as_published | 323 | 241 | 0.62 (0.58) | 0.81 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.61 | 0.55 | 0.55 | 0.56 | 247 | 38 | 1,197,677 / 90,098 | 468 | finish | 11 |
| grandview_app | flat | fake_scan | 323 | 199 | 0.57 (0.44) | 0.92 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.17 | 0.21 | 0.11 | 0.17 | 247 | 14 | 376,585 / 31,742 | 159 | finish | 0 |
| csudh_health | flat | as_published | 175 | 172 | 0.97 (0.97) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.99 | 0.89 | 0.90 | 0.98 | 155 | 37 | 983,484 / 96,413 | 467 | finish | 4 |
| csudh_health | flat | fake_scan | 175 | 143 | 0.64 (0.32) | 0.78 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.57 | 0.59 | 0.54 | 0.55 | 155 | 40 | 1,025,083 / 104,458 | 514 | finish | 15 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 20 | 6 | 93,960 / 13,716 | 70 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 24 | 0.57 (0.43) | 0.54 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.60 | 0.33 | 0.50 | 0.60 | 20 | 12 | 206,457 / 34,363 | 168 | finish | 2 |
| fw4 | fillable | as_published | 48 | 51 | 1.00 (1.00) | 0.94 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 0.94 | 17 | 10 | 178,816 / 19,430 | 98 | finish | 2 |
| fw4 | fillable | fake_scan | 48 | 46 | 0.88 (0.81) | 0.91 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.88 | 0.89 | 0.88 | 0.88 | 17 | 12 | 206,279 / 24,186 | 127 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 24 | 2906 | 2252 | 0.71 (0.54) | 0.91 | 0.99 | 0.75 (0.60) | 0.92 | 0.61 | 0.52 | 0.54 | 0.60 | 410 | 9,224,432 / 1,001,211 | 5015 |
| as_published | 12 (0) | 12 | 1453 | 1263 | 0.82 (0.69) | 0.94 | 0.99 | 0.85 (0.76) | 0.95 | 0.76 | 0.67 | 0.70 | 0.74 | 199 | 4,832,042 / 476,037 | 2379 |
| fake_scan | 12 (0) | 12 | 1453 | 989 | 0.61 (0.39) | 0.88 | 0.99 | 0.65 (0.45) | 0.89 | 0.46 | 0.37 | 0.38 | 0.45 | 211 | 4,392,390 / 525,174 | 2636 |
