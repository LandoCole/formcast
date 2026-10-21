# Agent eval: gpt-6-luna (OPENAI_EVAL), dev, 2026-10-21_check2a

- model: `gpt-6-luna` via `responses`; tag: check2a; commit: `aa37fa6`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| grandview_app | flat | as_published | 323 | 261 | 0.70 (0.59) | 0.87 | 1.00 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.76 | 0.57 | 0.67 | 0.70 | 247 | 40 | 1,061,283 / 26,018 | 455 | finish | 2 |
| grandview_app | flat | fake_scan | 323 | 264 | 0.40 (0.28) | 0.48 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.35 | 0.39 | 0.31 | 0.34 | 247 | 40 | 950,250 / 17,383 | 321 | finish | 18 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.94 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.93 | 0.97 | 1.00 | 114 | 31 | 584,365 / 11,001 | 219 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 125 | 0.95 (0.92) | 0.98 | 0.97 | FFDetr | 0.94 (0.92) | 0.94 | 0.93 | 0.69 | 0.85 | 0.93 | 114 | 40 | 792,858 / 21,735 | 423 | budget | 0 |
| cms1500 | flat | as_published | 254 | 188 | 0.54 (0.33) | 0.72 | 0.97 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.47 | 0.31 | 0.43 | 0.47 | 227 | 40 | 1,016,857 / 27,737 | 546 | budget | 0 |
| cms1500 | flat | fake_scan | 254 | 164 | 0.22 (0.06) | 0.35 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.16 | 0.10 | 0.14 | 0.16 | 227 | 35 | 657,235 / 20,063 | 384 | finish | 1 |
| wadoc_auth | flat | as_published | 27 | 19 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 6 | 61,170 / 2,359 | 46 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 19 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 17 | 20 | 220,987 / 6,326 | 143 | finish | 4 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 6 | 1468 | 1170 | 0.58 (0.46) | 0.72 | 0.98 | 0.60 (0.55) | 0.92 | 0.56 | 0.42 | 0.51 | 0.54 | 252 | 5,345,005 / 132,622 | 2538 |
| as_published | 4 (0) | 3 | 734 | 598 | 0.71 (0.59) | 0.86 | 0.98 | 0.72 (0.70) | 0.92 | 0.70 | 0.52 | 0.65 | 0.68 | 117 | 2,723,675 / 67,115 | 1267 |
| fake_scan | 4 (0) | 3 | 734 | 572 | 0.46 (0.34) | 0.57 | 0.98 | 0.48 (0.40) | 0.92 | 0.41 | 0.32 | 0.37 | 0.40 | 135 | 2,621,330 / 65,507 | 1270 |

Re-scored: 2026-10-21 at commit 6b01f29-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields; model output is unchanged.
