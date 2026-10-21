# Agent eval: seed-only (no model; untouched seeded template) (SEED), heldout, 2026-10-21_seed

- model: `seed-only (no model; untouched seeded template)` via `none`; tag: seed; commit: `6b01f29-dirty`; budget: {}
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
| ds11 | fillable | as_published | 98 | 98 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.85 (0.82) | 0.52 | 0.00 | 0.00 | 0.00 | 0.00 | 88 | 0 | 0 / 0 | 0 | seed_only | 0 |
| ds11 | fillable | fake_scan | 98 | 124 | 0.78 (0.62) | 0.61 | 1.00 | FFDetr | 0.79 (0.64) | 0.74 | 0.00 | 0.00 | 0.00 | 0.00 | 88 | 0 | 0 / 0 | 0 | seed_only | 0 |
| ics211 | fillable | as_published | 157 | 157 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.94 (0.94) | 0.96 | 0.00 | 0.00 | 0.00 | 0.00 | 148 | 0 | 0 / 0 | 0 | seed_only | 0 |
| ics211 | fillable | fake_scan | 157 | 137 | 0.83 (0.79) | 0.96 | 1.00 | FFDetr | 0.92 (0.91) | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 148 | 0 | 0 / 0 | 0 | seed_only | 0 |
| pacific_app | flat | as_published | 83 | 68 | 0.76 (0.34) | 0.93 | 0.98 | FFDNet-L | 0.76 (0.34) | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 74 | 0 | 0 / 0 | 0 | seed_only | 0 |
| pacific_app | flat | fake_scan | 83 | 74 | 0.84 (0.60) | 0.95 | 0.99 | FFDetr | 0.82 (0.53) | 0.96 | 0.00 | 0.00 | 0.00 | 0.00 | 74 | 0 | 0 / 0 | 0 | seed_only | 0 |
| uwosh_health | flat | as_published | 318 | 255 | 0.75 (0.75) | 0.93 | 0.28 | FFDNet-L | 0.75 (0.74) | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 286 | 0 | 0 / 0 | 0 | seed_only | 0 |
| uwosh_health | flat | fake_scan | 318 | 156 | 0.42 (0.27) | 0.87 | 0.64 | FFDetr | 0.33 (0.19) | 0.82 | 0.00 | 0.00 | 0.00 | 0.00 | 286 | 0 | 0 / 0 | 0 | seed_only | 0 |
| dagsboro_permit | scanned | as_published | 101 | 59 | 0.45 (0.41) | 0.76 | 0.98 | FFDetr | 0.31 (0.27) | 0.62 | 0.00 | 0.00 | 0.00 | 0.00 | 74 | 0 | 0 / 0 | 0 | seed_only | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 9 (0) | 0 | 1413 | 1128 | 0.72 (0.63) | 0.90 | 0.78 | 0.68 (0.59) | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
| as_published | 5 (0) | 0 | 757 | 637 | 0.79 (0.74) | 0.94 | 0.71 | 0.74 (0.69) | 0.82 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
| fake_scan | 4 (0) | 0 | 656 | 491 | 0.63 (0.49) | 0.84 | 0.88 | 0.60 (0.47) | 0.86 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
