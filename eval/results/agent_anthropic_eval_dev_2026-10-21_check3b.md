# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_check3b

- model: `claude-haiku-5-5` via `chat_completions`; tag: check3b; commit: `2f795db`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| grandview_app | flat | as_published | 323 | 254 | 0.75 (0.73) | 0.96 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.60 | 0.48 | 0.54 | 0.55 | 247 | 40 | 1,210,322 / 138,130 | 716 | finish | 3 |
| grandview_app | flat | fake_scan | 323 | 198 | 0.54 (0.41) | 0.88 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.49 | 0.38 | 0.40 | 0.45 | 247 | 40 | 1,229,701 / 100,737 | 514 | finish | 17 |
| cms1500 | flat | as_published | 254 | 142 | 0.50 (0.48) | 0.89 | 0.94 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.11 | 0.14 | 0.11 | 0.11 | 227 | 31 | 872,489 / 67,000 | 358 | finish | 43 |
| cms1500 | flat | fake_scan | 254 | 25 | 0.06 (0.03) | 0.56 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.08 | 0.06 | 0.06 | 227 | 14 | 271,901 / 25,790 | 134 | finish | 70 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (0.38) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 8 | 198,835 / 16,008 | 83 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 174 | 0.81 (0.21) | 0.94 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.88 | 0.14 | 0.64 | 0.88 | 170 | 14 | 296,495 / 52,480 | 253 | finish | 2 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.76 | 0.91 | 1.00 | 114 | 10 | 274,489 / 26,726 | 134 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 123 | 0.92 (0.90) | 0.98 | 0.92 | FFDetr | 0.94 (0.92) | 0.94 | 0.48 | 0.17 | 0.39 | 0.47 | 114 | 39 | 926,936 / 108,104 | 556 | finish | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 8 | 1818 | 1248 | 0.65 (0.47) | 0.94 | 0.98 | 0.66 (0.58) | 0.91 | 0.52 | 0.34 | 0.46 | 0.51 | 196 | 5,281,168 / 534,975 | 2748 |
| as_published | 4 (0) | 4 | 909 | 728 | 0.77 (0.62) | 0.96 | 0.99 | 0.77 (0.75) | 0.94 | 0.60 | 0.48 | 0.57 | 0.59 | 89 | 2,556,135 / 247,864 | 1292 |
| fake_scan | 4 (0) | 4 | 909 | 520 | 0.52 (0.33) | 0.91 | 0.98 | 0.54 (0.40) | 0.88 | 0.45 | 0.21 | 0.35 | 0.43 | 107 | 2,725,033 / 287,111 | 1456 |
