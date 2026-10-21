# Agent eval: gpt-6-luna (OPENAI_EVAL), dev, 2026-10-21_check2b

- model: `gpt-6-luna` via `responses`; tag: check2b; commit: `9aae83a`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| grandview_app | flat | as_published | 323 | 247 | 0.68 (0.59) | 0.89 | 0.99 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.71 | 0.50 | 0.62 | 0.66 | 247 | 20 | 454,057 / 10,779 | 130 | finish | 2 |
| grandview_app | flat | fake_scan | 323 | 262 | 0.64 (0.50) | 0.78 | 1.00 | FFDetr | 0.59 (0.49) | 0.92 | 0.53 | 0.35 | 0.45 | 0.48 | 247 | 24 | 576,304 / 18,199 | 197 | finish | 11 |
| csudh_health | flat | as_published | 175 | 176 | 0.98 (0.97) | 0.98 | 1.00 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.97 | 0.96 | 0.95 | 0.97 | 155 | 25 | 575,206 / 15,288 | 174 | finish | 2 |
| csudh_health | flat | fake_scan | 175 | 162 | 0.71 (0.35) | 0.77 | 1.00 | FFDetr | 0.67 (0.31) | 0.89 | 0.61 | 0.69 | 0.61 | 0.61 | 155 | 34 | 774,357 / 26,705 | 303 | finish | 2 |
| menomonie_app | fillable | as_published | 56 | 58 | 1.00 (0.96) | 0.97 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 50 | 16 | 242,344 / 7,293 | 91 | finish | 2 |
| menomonie_app | fillable | fake_scan | 56 | 57 | 0.98 (0.84) | 0.93 | 1.00 | FFDetr | 0.98 (0.91) | 0.98 | 0.96 | 0.88 | 0.96 | 0.94 | 50 | 32 | 482,201 / 12,977 | 173 | finish | 2 |
| fw9 | fillable | as_published | 23 | 23 | 0.91 (0.91) | 0.91 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 | 20 | 15 | 229,094 / 6,291 | 89 | finish | 1 |
| fw9 | fillable | fake_scan | 23 | 22 | 0.57 (0.48) | 0.45 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.55 | 0.13 | 0.25 | 0.55 | 20 | 20 | 302,526 / 8,728 | 131 | finish | 1 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 8 (0) | 8 | 1154 | 1007 | 0.75 (0.62) | 0.85 | 1.00 | 0.76 (0.66) | 0.93 | 0.72 | 0.66 | 0.67 | 0.69 | 186 | 3,636,089 / 106,260 | 1288 |
| as_published | 4 (0) | 4 | 577 | 504 | 0.81 (0.76) | 0.93 | 1.00 | 0.86 (0.85) | 0.97 | 0.84 | 0.78 | 0.78 | 0.81 | 76 | 1,500,701 / 39,651 | 484 |
| fake_scan | 4 (0) | 4 | 577 | 503 | 0.69 (0.49) | 0.78 | 1.00 | 0.65 (0.47) | 0.89 | 0.60 | 0.53 | 0.55 | 0.58 | 110 | 2,135,388 / 66,609 | 804 |
