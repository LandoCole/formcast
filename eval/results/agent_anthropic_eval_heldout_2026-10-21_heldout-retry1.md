# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), heldout, 2026-10-21_heldout-retry1

- model: `claude-haiku-5-5` via `chat_completions`; tag: heldout-retry1; commit: `25e4809-dirty`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pacific_app | flat | as_published | 83 | 68 | 0.76 (0.34) | 0.93 | 0.98 | FFDNet-L | 0.76 (0.34) | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 74 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 0 (1) | 0 | 0 | 0 | – | – | – | – | – | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 1 |
| as_published | 0 (1) | 0 | 0 | 0 | – | – | – | – | – | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 1 |

Excluded from the totals above (session ended in an error): pacific_app/as_published.

Re-scored: 2026-10-21 at commit 6b01f29-dirty: the saved templates were scored again with the stacked-lines rule for multi-line fields; model output is unchanged.
