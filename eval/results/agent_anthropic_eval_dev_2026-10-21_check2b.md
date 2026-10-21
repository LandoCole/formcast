# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_check2b

- model: `claude-haiku-5-5` via `chat_completions`; tag: check2b; commit: `9aae83a`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| grandview_app | flat | as_published | 323 | 239 | 0.70 (0.63) | 0.94 | 1.00 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.70 | 0.53 | 0.62 | 0.65 | 247 | 39 | 1,134,255 / 94,807 | 479 | finish | 4 |
| grandview_app | flat | fake_scan | 323 | 207 | 0.60 (0.46) | 0.94 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.36 | 0.38 | 0.32 | 0.35 | 247 | 34 | 944,458 / 80,144 | 417 | finish | 8 |
| csudh_health | flat | as_published | 175 | 171 | 0.97 (0.97) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.97 | 0.82 | 0.83 | 0.95 | 155 | 18 | 465,307 / 43,511 | 210 | finish | 6 |
| csudh_health | flat | fake_scan | 175 | 141 | 0.68 (0.35) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.56 | 0.46 | 0.46 | 0.56 | 155 | 37 | 981,548 / 99,042 | 487 | finish | 12 |
| menomonie_app | fillable | as_published | 56 | 54 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 50 | 11 | 200,140 / 27,703 | 135 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 58 | 0.98 (0.86) | 0.95 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.98 | 0.82 | 0.94 | 0.92 | 50 | 15 | 249,331 / 41,753 | 209 | finish | 5 |
| fw9 | fillable | as_published | 23 | 24 | 1.00 (1.00) | 0.96 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 20 | 10 | 166,915 / 22,127 | 109 | finish | 2 |
| fw9 | fillable | fake_scan | 23 | 23 | 0.57 (0.39) | 0.57 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.60 | 0.47 | 0.55 | 0.60 | 20 | 8 | 133,787 / 15,385 | 78 | finish | 2 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 8 | 1154 | 917 | 0.74 (0.62) | 0.93 | 1.00 | 0.76 (0.66) | 0.93 | 0.67 | 0.58 | 0.59 | 0.64 | 172 | 4,275,741 / 424,472 | 2125 |
| as_published | 4 (0) | 4 | 577 | 488 | 0.82 (0.79) | 0.97 | 1.00 | 0.86 (0.85) | 0.97 | 0.83 | 0.72 | 0.74 | 0.80 | 78 | 1,966,617 / 188,148 | 934 |
| fake_scan | 4 (0) | 4 | 577 | 429 | 0.66 (0.46) | 0.89 | 0.99 | 0.65 (0.47) | 0.89 | 0.50 | 0.45 | 0.44 | 0.49 | 94 | 2,309,124 / 236,324 | 1192 |
