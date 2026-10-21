# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_round3-gen

- model: `claude-haiku-5-5` via `chat_completions`; tag: round3-gen; commit: `87d4bb4`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 19 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 5 | 56,496 / 5,920 | 31 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 7 | 89,202 / 12,886 | 66 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 23 | 1.00 (1.00) | 0.87 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 8 | 92,692 / 10,307 | 54 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 25 | 316,515 / 50,194 | 258 | finish | 3 |
| ndguard | flat | as_published | 33 | 33 | 0.85 (0.70) | 0.85 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.86 | 0.81 | 0.79 | 0.72 | 29 | 28 | 429,596 / 59,607 | 304 | finish | 1 |
| ndguard | flat | fake_scan | 33 | 33 | 0.88 (0.61) | 0.88 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.90 | 0.75 | 0.76 | 0.72 | 29 | 15 | 230,535 / 29,714 | 151 | finish | 5 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.95 | 0.98 | 0.99 | 114 | 7 | 178,409 / 19,985 | 101 | finish | 3 |
| i9 | fillable | fake_scan | 130 | 110 | 0.82 (0.78) | 0.97 | 1.00 | FFDetr | 0.94 (0.92) | 0.94 | 0.69 | 0.29 | 0.53 | 0.68 | 114 | 19 | 468,279 / 43,671 | 222 | finish | 2 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (0.35) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 5 | 120,000 / 10,719 | 53 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 172 | 0.81 (0.15) | 0.95 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.88 | 0.83 | 0.88 | 0.88 | 170 | 13 | 286,257 / 37,940 | 184 | finish | 2 |
| cms1500 | flat | as_published | 254 | 181 | 0.48 (0.21) | 0.67 | 0.99 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.44 | 0.25 | 0.39 | 0.44 | 227 | 40 | 1,241,690 / 85,956 | 452 | budget | 0 |
| cms1500 | flat | fake_scan | 254 | 22 | 0.05 (0.03) | 0.59 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.08 | 0.05 | 0.05 | 227 | 32 | 669,829 / 55,708 | 285 | finish | 101 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.94 | 0.98 | 0.96 | 50 | 11 | 200,680 / 28,451 | 138 | finish | 3 |
| menomonie_app | fillable | fake_scan | 56 | 55 | 0.96 (0.88) | 0.98 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.98 | 0.76 | 0.92 | 0.90 | 50 | 40 | 719,205 / 100,680 | 498 | finish | 8 |
| columbus_medhist | fillable | as_published | 160 | 153 | 0.96 (0.96) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.99 | 0.82 | 0.84 | 0.99 | 142 | 11 | 263,178 / 37,364 | 169 | finish | 3 |
| columbus_medhist | fillable | fake_scan | 160 | 152 | 0.92 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.82 | 0.62 | 0.65 | 0.82 | 142 | 20 | 469,142 / 56,733 | 264 | finish | 3 |
| grandview_app | flat | as_published | 323 | 293 | 0.77 (0.73) | 0.85 | 0.95 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.74 | 0.55 | 0.66 | 0.69 | 247 | 40 | 1,265,172 / 110,912 | 562 | finish | 7 |
| grandview_app | flat | fake_scan | 323 | 205 | 0.59 (0.46) | 0.92 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.38 | 0.36 | 0.32 | 0.37 | 247 | 34 | 952,291 / 71,463 | 373 | finish | 18 |
| csudh_health | flat | as_published | 175 | 176 | 0.99 (0.98) | 0.98 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.98 | 0.98 | 0.98 | 0.98 | 155 | 30 | 859,136 / 85,735 | 414 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 147 | 0.71 (0.37) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.54 | 0.60 | 0.51 | 0.54 | 155 | 37 | 983,936 / 108,248 | 526 | finish | 20 |
| fw9 | fillable | as_published | 23 | 25 | 1.00 (1.00) | 0.92 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 20 | 10 | 158,559 / 14,702 | 74 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 25 | 0.57 (0.43) | 0.52 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.60 | 0.40 | 0.55 | 0.60 | 20 | 11 | 186,604 / 23,663 | 116 | finish | 2 |
| fw4 | fillable | as_published | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 1.00 | 0.89 | 0.94 | 0.94 | 17 | 11 | 202,091 / 24,074 | 119 | finish | 3 |
| fw4 | fillable | fake_scan | 48 | 45 | 0.90 (0.83) | 0.96 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 1.00 | 1.00 | 1.00 | 1.00 | 17 | 20 | 351,458 / 36,234 | 184 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 23 | 2906 | 2350 | 0.74 (0.55) | 0.91 | 0.99 | 0.75 (0.60) | 0.92 | 0.70 | 0.61 | 0.64 | 0.68 | 479 | 10,790,952 / 1,120,866 | 5596 |
| as_published | 12 (0) | 11 | 1453 | 1340 | 0.85 (0.70) | 0.91 | 0.99 | 0.85 (0.76) | 0.95 | 0.83 | 0.74 | 0.78 | 0.82 | 206 | 5,067,699 / 493,732 | 2470 |
| fake_scan | 12 (0) | 12 | 1453 | 1010 | 0.64 (0.41) | 0.91 | 1.00 | 0.65 (0.45) | 0.89 | 0.56 | 0.48 | 0.50 | 0.54 | 273 | 5,723,253 / 627,134 | 3126 |

Re-scored: 2026-10-21 at commit 6b01f29-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields; model output is unchanged.
