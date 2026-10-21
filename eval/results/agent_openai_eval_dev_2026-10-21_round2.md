# Agent eval: gpt-6-luna (OPENAI_EVAL), dev, 2026-10-21_round2

- model: `gpt-6-luna` via `responses`; tag: round2; commit: `76cbfa2`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 19 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 4 | 35,234 / 2,104 | 31 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 8 | 89,075 / 3,040 | 47 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 7 | 76,567 / 2,635 | 42 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 13 | 0.55 (0.55) | 0.77 | 1.00 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 6 | 64,146 / 3,825 | 52 | finish | 3 |
| ndguard | flat | as_published | 33 | 38 | 0.91 (0.76) | 0.76 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.86 | 0.62 | 0.79 | 0.76 | 29 | 18 | 257,980 / 9,479 | 135 | finish | 2 |
| ndguard | flat | fake_scan | 33 | 38 | 0.94 (0.73) | 0.82 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.97 | 0.81 | 0.86 | 0.86 | 29 | 25 | 363,603 / 12,157 | 180 | finish | 1 |
| i9 | fillable | as_published | 130 | 123 | 0.95 (0.95) | 1.00 | 1.00 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 1.00 | 1.00 | 1.00 | 114 | 13 | 265,747 / 7,515 | 103 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 119 | 0.88 (0.85) | 0.96 | 1.00 | FFDetr | 0.94 (0.92) | 0.94 | 0.89 | 0.83 | 0.88 | 0.89 | 114 | 39 | 825,372 / 28,403 | 403 | finish | 2 |
| f8949 | fillable | as_published | 202 | 178 | 0.88 (0.23) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 9 | 173,185 / 2,911 | 50 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 181 | 0.84 (0.19) | 0.94 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.92 | 0.86 | 0.92 | 0.92 | 170 | 12 | 217,095 / 8,298 | 122 | finish | 2 |
| cms1500 | flat | as_published | 254 | 210 | 0.56 (0.33) | 0.67 | 0.96 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.32 | 0.32 | 0.27 | 0.31 | 227 | 32 | 765,519 / 21,286 | 326 | finish | 5 |
| cms1500 | flat | fake_scan | 254 | 34 | 0.07 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.07 | 0.11 | 0.07 | 0.07 | 227 | 40 | 656,450 / 15,807 | 259 | budget | 0 |
| menomonie_app | fillable | as_published | 56 | 58 | 1.00 (0.96) | 0.97 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 50 | 17 | 246,011 / 6,781 | 112 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 58 | 0.98 (0.86) | 0.95 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.90 | 0.94 | 0.90 | 0.88 | 50 | 25 | 336,995 / 8,197 | 144 | finish | 2 |
| columbus_medhist | fillable | as_published | 160 | 159 | 0.99 (0.99) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 142 | 24 | 480,076 / 14,991 | 241 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| columbus_medhist | fillable | fake_scan | 160 | 150 | 0.91 (0.47) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.81 | 0.79 | 0.81 | 0.81 | 142 | 11 | 214,702 / 9,773 | 139 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| grandview_app | flat | as_published | 323 | 249 | 0.76 (0.74) | 0.99 | 0.94 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 0 | 0 / 0 | 10 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| grandview_app | flat | fake_scan | 323 | 213 | 0.61 (0.48) | 0.93 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 1 | 11,341 / 89 | 21 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| csudh_health | flat | as_published | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 2 | 26,459 / 228 | 16 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| csudh_health | flat | fake_scan | 175 | 134 | 0.64 (0.31) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 0 | 0 / 0 | 10 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| fw9 | fillable | as_published | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 3 | 31,597 / 850 | 32 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 2 | 14,510 / 112 | 16 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| fw4 | fillable | as_published | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 4 | 55,297 / 3,963 | 67 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |
| fw4 | fillable | fake_scan | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 5 | 59,021 / 608 | 43 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.openai.com/v1/resp) | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 14 (10) | 13 | 1448 | 1113 | 0.69 (0.44) | 0.88 | 0.99 | 0.70 (0.61) | 0.90 | 0.68 | 0.59 | 0.66 | 0.67 | 307 | 5,265,982 / 163,052 | 2600 |
| as_published | 7 (5) | 7 | 724 | 651 | 0.80 (0.52) | 0.87 | 0.99 | 0.80 (0.79) | 0.93 | 0.74 | 0.67 | 0.72 | 0.73 | 133 | 2,413,672 / 72,743 | 1165 |
| fake_scan | 7 (5) | 6 | 724 | 462 | 0.59 (0.36) | 0.90 | 1.00 | 0.59 (0.44) | 0.87 | 0.61 | 0.52 | 0.60 | 0.60 | 174 | 2,852,310 / 90,309 | 1435 |

Excluded from the totals above (session ended in an error): columbus_medhist/as_published, columbus_medhist/fake_scan, grandview_app/as_published, grandview_app/fake_scan, csudh_health/as_published, csudh_health/fake_scan, fw9/as_published, fw9/fake_scan, fw4/as_published, fw4/fake_scan.

Re-scored: 2026-10-21 at commit 6b01f29-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields; model output is unchanged.
