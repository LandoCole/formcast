# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-21_check3a

- model: `claude-haiku-5-5` via `chat_completions`; tag: check3a; commit: `606b753`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| grandview_app | flat | as_published | 323 | 248 | 0.71 (0.66) | 0.92 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.58 | 0.48 | 0.52 | 0.53 | 247 | 37 | 1,114,622 / 92,601 | 480 | finish | 5 |
| grandview_app | flat | fake_scan | 323 | 215 | 0.62 (0.48) | 0.93 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.21 | 0.34 | 0.20 | 0.21 | 247 | 35 | 969,010 / 78,512 | 400 | finish | 64 |
| cms1500 | flat | as_published | 254 | 147 | 0.44 (0.29) | 0.77 | 0.94 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.25 | 0.14 | 0.24 | 0.25 | 227 | 37 | 1,087,939 / 87,418 | 452 | finish | 76 |
| cms1500 | flat | fake_scan | 254 | 25 | 0.06 (0.03) | 0.56 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.05 | 0.04 | 0.06 | 227 | 20 | 418,117 / 46,802 | 238 | finish | 7 |
| f8949 | fillable | as_published | 202 | 180 | 0.89 (0.24) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 170 | 6 | 143,004 / 10,996 | 57 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 173 | 0.51 (0.20) | 0.59 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.52 | 0.12 | 0.40 | 0.52 | 170 | 16 | 353,000 / 57,060 | 274 | finish | 2 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.95 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.76 | 0.91 | 1.00 | 114 | 10 | 267,426 / 22,867 | 114 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 109 | 0.82 (0.80) | 0.98 | 1.00 | FFDetr | 0.94 (0.92) | 0.94 | 0.68 | 0.17 | 0.49 | 0.68 | 114 | 33 | 797,302 / 73,031 | 378 | finish | 6 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 8 | 1818 | 1227 | 0.59 (0.42) | 0.88 | 0.98 | 0.66 (0.58) | 0.91 | 0.47 | 0.33 | 0.42 | 0.46 | 194 | 5,150,420 / 469,287 | 2393 |
| as_published | 4 (0) | 4 | 909 | 705 | 0.72 (0.51) | 0.92 | 0.98 | 0.77 (0.75) | 0.94 | 0.64 | 0.49 | 0.60 | 0.62 | 90 | 2,612,991 / 213,882 | 1103 |
| fake_scan | 4 (0) | 4 | 909 | 522 | 0.46 (0.34) | 0.81 | 1.00 | 0.54 (0.40) | 0.88 | 0.31 | 0.18 | 0.24 | 0.31 | 104 | 2,537,429 / 255,405 | 1290 |
