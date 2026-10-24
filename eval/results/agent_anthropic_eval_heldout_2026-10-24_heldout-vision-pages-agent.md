# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), heldout, 2026-10-24_heldout-vision-pages-agent, pipeline pages+agent

- model: `claude-haiku-5-5` via `chat_completions`; tag: heldout-vision; commit: `be192b8`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
- baseline file: `baseline_detection_2026-10-21_heldout.json`

Notes:

- Detection is scored on the manifest's scored pages that have at least one GT field (the baseline's pages); tables count one box per cell.
- Multi-line answer areas are representation-neutral: a plain template text field with style.multiline or at least 1.8x the page's median GT text-box height also covers every unmatched GT text box at least 70% inside it (recall), counts once for precision, and gets path credit through the GT line box that carries the path. It only does so when at least two such GT boxes are stacked in one column inside it (horizontal overlap of 50% or more); a single loose box is scored by the normal IoU threshold.
- Fillable forms as published: the template starts with one field per PDF widget (the real use case is mapping existing fields), and GT is the widgets, so agent-added boxes on blanks without a widget count as false positives.
- fake_scan: an image-only PDF of the baseline's seeded fake-scan renders (widgets stripped), so the agent faces the scanned-page path (deskew, FFDetr, OCR); on fillable forms it is a detection + mapping test.
- Baseline: bare-detector numbers from the baseline run on the same images: FFDNet-L (default for digital pages) for as_published digital/fillable forms, FFDetr (default for scanned pages) for fake_scan and scanned forms. On fillable as_published forms the baseline detects on the widget-stripped page.
- Mapping is over GT fields with a schema path. Transform credit uses equivalence groups {slice, split, char_at}, {date_part, date_format}, {equals, contains}, and a when equals true also credits bool_to_check; a template's join and when count as ops. joint = path and transforms right; exact = the matched field's source paths equal GT path + also_paths.
- Totals are micro-averaged over forms (sums of counts). Sessions that ended in an error (infrastructure or harness) are listed in the per-form table but excluded from the totals' detection and mapping figures; their templates are partial or untouched seeds.
- The agent's form map carries the neutral source name 'form.pdf', not the file name.
- mapping_reachable: path/joint accuracy over GT fields that some seeded box (widget or detector candidate, before any edit) overlaps at IoU >= 0.3 — what a mapping-only step can reach.
- fill (fill_acc): the template is filled (formcast fill, real form fields) into the variant's PDF with K distinctive probe records (eval/probe.py), and every GT box with a path is checked for what its widget shows: a text or table-cell box once per record (the exact value; by provenance when GT has transforms), a choice box once across all records (right state in every record). fill_acc = correct units / units. It catches data landing in the wrong box, e.g. a table built as one segment per column, which path/joint score as right. A choice box whose expected state is the same in every probe record can't tell an always-on (or never-on) box from a right one; the json counts them as fill.static_choice.

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ds11 | fillable | as_published | 98 | 98 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.85 (0.82) | 0.52 | 1.00 | 1.00 | 1.00 | 0.96 | 1.00 | 88 | 16 | 529,199 / 40,592 | 194 | finish | 7 |
| ds11 | fillable | fake_scan | 98 | 126 | 0.80 (0.63) | 0.62 | 1.00 | FFDetr | 0.79 (0.64) | 0.74 | 0.82 | 0.60 | 0.81 | 0.89 | 0.82 | 88 | 40 | 1,358,485 / 121,834 | 655 | finish | 10 |
| ics211 | fillable | as_published | 157 | 157 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.94 (0.94) | 0.96 | 1.00 | 0.40 | 0.94 | 1.00 | 1.00 | 148 | 6 | 178,657 / 8,317 | 36 | finish | 1 |
| ics211 | fillable | fake_scan | 157 | 158 | 0.94 (0.90) | 0.93 | 1.00 | FFDetr | 0.92 (0.91) | 0.92 | 0.93 | 0.00 | 0.87 | 0.99 | 0.93 | 148 | 18 | 568,350 / 53,187 | 280 | finish | 1 |
| pacific_app | flat | as_published | 83 | 76 | 0.81 (0.39) | 0.88 | 0.99 | FFDNet-L | 0.76 (0.34) | 0.93 | 0.78 | 0.67 | 0.76 | 0.31 | 0.78 | 74 | 12 | 356,160 / 60,583 | 267 | finish | 3 |
| pacific_app | flat | fake_scan | 83 | 80 | 0.89 (0.60) | 0.93 | 1.00 | FFDetr | 0.82 (0.53) | 0.96 | 0.84 | 0.71 | 0.80 | 0.58 | 0.84 | 74 | 21 | 631,153 / 74,713 | 389 | finish | 3 |
| uwosh_health | flat | as_published | 318 | 276 | 0.81 (0.80) | 0.93 | 0.95 | FFDNet-L | 0.75 (0.74) | 0.93 | 0.84 | 0.85 | 0.84 | 0.77 | 0.84 | 286 | 40 | 1,493,255 / 117,237 | 554 | finish | 16 |
| uwosh_health | flat | fake_scan | 318 | 192 | 0.50 (0.31) | 0.83 | 0.70 | FFDetr | 0.33 (0.19) | 0.82 | 0.51 | 0.47 | 0.48 | 0.49 | 0.51 | 286 | 28 | 944,766 / 82,655 | 418 | finish | 20 |
| dagsboro_permit | scanned | as_published | 101 | 106 | 0.88 (0.83) | 0.84 | 0.99 | FFDetr | 0.31 (0.27) | 0.62 | 0.91 | 0.88 | 0.88 | 0.88 | 0.89 | 74 | 11 | 333,967 / 34,000 | 169 | finish | 3 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 9 (0) | 9 | 1413 | 1269 | 0.80 (0.69) | 0.89 | 0.94 | 0.68 (0.59) | 0.83 | 0.80 | 0.67 | 0.78 | 0.70 | 0.80 | 0.99 (0.95) | 192 | 6,393,992 / 593,118 | 2961 |
| as_published | 5 (0) | 5 | 757 | 713 | 0.88 (0.82) | 0.94 | 0.98 | 0.74 (0.69) | 0.82 | 0.90 | 0.84 | 0.88 | 0.79 | 0.90 | 0.99 (0.97) | 85 | 2,891,238 / 260,729 | 1220 |
| fake_scan | 4 (0) | 4 | 656 | 556 | 0.70 (0.54) | 0.83 | 0.90 | 0.60 (0.47) | 0.86 | 0.70 | 0.48 | 0.66 | 0.59 | 0.70 | 0.97 (0.93) | 107 | 3,502,754 / 332,389 | 1741 |

Page pass: 195,912 in / 108,931 out tokens (included in the totals above), 0 page(s) with an error or unparsed answer.

Cost at the built-in prices: $0.94.
