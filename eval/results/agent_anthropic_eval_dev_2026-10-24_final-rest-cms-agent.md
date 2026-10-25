# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-24_final-rest-cms-agent, pipeline agent

- model: `claude-haiku-5-5` via `chat_completions`; tag: final-rest-cms; commit: `39cfb74`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
- mapping_reachable: path/joint accuracy over GT fields that some seeded box (widget or detector candidate, before any edit) overlaps at IoU >= 0.3 — what a mapping-only step can reach.
- fill (fill_acc): the template is filled (formcast fill, real form fields) into the variant's PDF with K distinctive probe records (eval/probe.py), and every GT box with a path is checked for what its widget shows: a text or table-cell box once per record (the exact value; by provenance when GT has transforms), a choice box once across all records (right state in every record). fill_acc = correct units / units. It catches data landing in the wrong box, e.g. a table built as one segment per column, which path/joint score as right. A choice box whose expected state is the same in every probe record can't tell an always-on (or never-on) box from a right one; the json counts them as fill.static_choice.

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cms1500 | flat | as_published | 254 | 172 | 0.61 (0.50) | 0.90 | 0.97 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.39 | 0.25 | 0.36 | 0.35 | 0.39 | 227 | 39 | 1,154,145 / 102,786 | 514 | finish | 3 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 1 (0) | 1 | 254 | 172 | 0.61 (0.50) | 0.90 | 0.97 | 0.52 (0.50) | 0.83 | 0.39 | 0.25 | 0.36 | 0.35 | 0.39 | 0.60 (0.54) | 39 | 1,154,145 / 102,786 | 514 |
| as_published | 1 (0) | 1 | 254 | 172 | 0.61 (0.50) | 0.90 | 0.97 | 0.52 (0.50) | 0.83 | 0.39 | 0.25 | 0.36 | 0.35 | 0.39 | 0.60 (0.54) | 39 | 1,154,145 / 102,786 | 514 |

Cost at the built-in prices: $0.17.
