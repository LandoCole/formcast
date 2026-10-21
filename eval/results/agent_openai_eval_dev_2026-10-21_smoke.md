# Agent eval: gpt-6-luna (OPENAI_EVAL), dev, 2026-10-21_smoke

- model: `gpt-6-luna` via `responses`; tag: smoke; commit: `546520a-dirty`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
- baseline file: `baseline_detection_2026-10-21.json`

Notes:

- Detection is scored on the manifest's scored pages that have at least one GT field (the baseline's pages); tables count one box per cell.
- Fillable forms as published: the template starts with one field per PDF widget (the real use case is mapping existing fields), and GT is the widgets, so agent-added boxes on blanks without a widget count as false positives.
- fake_scan: an image-only PDF of the baseline's seeded fake-scan renders (widgets stripped), so the agent faces the scanned-page path (deskew, FFDetr, OCR); on fillable forms it is a detection + mapping test.
- Baseline: bare-detector numbers from the baseline run on the same images: FFDNet-L (default for digital pages) for as_published digital/fillable forms, FFDetr (default for scanned pages) for fake_scan and scanned forms. On fillable as_published forms the baseline detects on the widget-stripped page.
- Mapping is over GT fields with a schema path. Transform credit uses equivalence groups {slice, split, char_at}, {date_part, date_format}, {equals, contains}; a template's join and when count as ops. joint = path and transforms right; exact = the matched field's source paths equal GT path + also_paths.
- Totals are micro-averaged over forms (sums of counts).

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 5 | 44,361 / 2,270 | 29 | finish | 3 |
| wadoc_auth | flat | as_published | 27 | 19 | 0.63 (0.63) | 0.89 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.88 | 0.25 | 0.71 | 0.82 | 17 | 4 | 28,654 / 1,731 | 21 | finish | 4 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 2 (0) | 2 | 49 | 44 | 0.80 (0.80) | 0.89 | 1.00 | 1.00 (1.00) | 0.94 | 0.92 | 0.50 | 0.81 | 0.88 | 9 | 73,015 / 4,001 | 49 |
| as_published | 2 (0) | 2 | 49 | 44 | 0.80 (0.80) | 0.89 | 1.00 | 1.00 (1.00) | 0.94 | 0.92 | 0.50 | 0.81 | 0.88 | 9 | 73,015 / 4,001 | 49 |
