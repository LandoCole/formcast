# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_round3

- model: `claude-haiku-5-5` via `chat_completions`; tag: round3; commit: `1b29efc`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 19 | 0.93 (0.93) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.88 | 1.00 | 0.88 | 0.82 | 0.82 | 17 | 6 | 76,774 / 6,097 | 34 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 9 | 115,982 / 15,069 | 76 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 23 | 1.00 (1.00) | 0.87 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 6 | 66,734 / 5,290 | 28 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 24 | 0.95 (0.95) | 0.88 | 0.95 | FFDetr | 1.00 (1.00) | 0.88 | 0.89 | 1.00 | 0.89 | 0.89 | 0.89 | 9 | 10 | 128,217 / 16,668 | 86 | finish | 3 |
| ndguard | flat | as_published | 33 | 36 | 0.79 (0.64) | 0.72 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.79 | 0.62 | 0.69 | 0.66 | 0.62 | 29 | 12 | 179,813 / 32,213 | 162 | finish | 4 |
| ndguard | flat | fake_scan | 33 | 39 | 0.94 (0.70) | 0.79 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.93 | 0.75 | 0.79 | 0.73 | 0.76 | 29 | 28 | 450,405 / 58,864 | 299 | finish | 5 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.43 | 0.79 | 0.94 | 1.00 | 114 | 8 | 199,599 / 20,388 | 108 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 116 | 0.87 (0.83) | 0.97 | 0.97 | FFDetr | 0.94 (0.92) | 0.94 | 0.59 | 0.17 | 0.44 | 0.49 | 0.59 | 114 | 34 | 800,511 / 86,056 | 443 | finish | 14 |
| f8949 | fillable | as_published | 202 | 180 | 0.89 (0.24) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 8 | 200,516 / 14,545 | 78 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 194 | 0.86 (0.23) | 0.89 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.88 | 0.83 | 0.88 | 0.90 | 0.88 | 170 | 14 | 320,442 / 54,536 | 262 | finish | 2 |
| cms1500 | flat | as_published | 254 | 116 | 0.41 (0.22) | 0.89 | 0.97 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.30 | 0.24 | 0.28 | 0.27 | 0.30 | 227 | 40 | 1,171,187 / 97,679 | 499 | budget | 0 |
| cms1500 | flat | fake_scan | 254 | 25 | 0.06 (0.03) | 0.64 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.07 | 0.09 | 0.07 | 0.04 | 0.07 | 227 | 16 | 337,753 / 25,262 | 129 | finish | 81 |
| menomonie_app | fillable | as_published | 56 | 54 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.97 | 0.98 | 50 | 19 | 365,341 / 45,823 | 230 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.80 (0.52) | 0.77 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.66 | 0.65 | 0.58 | 0.46 | 0.58 | 50 | 39 | 677,411 / 84,377 | 426 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.99 | 0.84 | 0.85 | 0.98 | 0.99 | 142 | 10 | 248,210 / 25,189 | 115 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 131 | 0.79 (0.41) | 0.96 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.82 | 0.63 | 0.66 | 0.98 | 0.82 | 142 | 26 | 599,850 / 63,470 | 300 | finish | 3 |
| grandview_app | flat | as_published | 323 | 295 | 0.77 (0.75) | 0.84 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.64 | 0.50 | 0.57 | 0.51 | 0.59 | 247 | 39 | 1,133,347 / 99,235 | 504 | finish | 2 |
| grandview_app | flat | fake_scan | 323 | 200 | 0.57 (0.44) | 0.92 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.34 | 0.31 | 0.27 | 0.19 | 0.33 | 247 | 35 | 1,038,757 / 72,023 | 366 | finish | 8 |
| csudh_health | flat | as_published | 175 | 178 | 0.99 (0.99) | 0.98 | 1.00 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.99 | 0.90 | 0.91 | 0.95 | 0.99 | 155 | 23 | 630,537 / 60,868 | 293 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 152 | 0.75 (0.41) | 0.87 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.60 | 0.52 | 0.49 | 0.76 | 0.59 | 155 | 39 | 1,014,212 / 107,802 | 532 | finish | 17 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 1.00 | 20 | 16 | 267,115 / 26,374 | 130 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 24 | 0.61 (0.48) | 0.58 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.65 | 0.53 | 0.65 | 0.92 | 0.65 | 20 | 10 | 172,762 / 19,841 | 95 | finish | 2 |
| fw4 | fillable | as_published | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 0.93 | 0.94 | 17 | 12 | 231,927 / 22,091 | 119 | finish | 3 |
| fw4 | fillable | fake_scan | 48 | 47 | 0.88 (0.79) | 0.89 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.88 | 0.78 | 0.82 | 0.78 | 0.82 | 17 | 21 | 364,399 / 39,309 | 206 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 23 | 2906 | 2293 | 0.73 (0.55) | 0.92 | 0.99 | 0.75 (0.60) | 0.92 | 0.66 | 0.56 | 0.59 | 0.63 | 0.64 | 480 | 10,791,801 / 1,099,069 | 5520 |
| as_published | 12 (0) | 11 | 1453 | 1266 | 0.82 (0.69) | 0.94 | 1.00 | 0.85 (0.76) | 0.95 | 0.79 | 0.68 | 0.72 | 0.73 | 0.77 | 199 | 4,771,100 / 455,792 | 2300 |
| fake_scan | 12 (0) | 12 | 1453 | 1027 | 0.63 (0.40) | 0.89 | 0.99 | 0.65 (0.45) | 0.89 | 0.53 | 0.45 | 0.46 | 0.52 | 0.52 | 281 | 6,020,701 / 643,277 | 3220 |

Re-scored: 2026-10-21 at commit 33e4f50-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields and the fill-correctness metric (fill); model output is unchanged.
