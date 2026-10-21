# Agent eval: seed-only (no model; untouched seeded template) (SEED), dev, 2026-10-21_seed-fill

- model: `seed-only (no model; untouched seeded template)` via `none`; tag: seed-fill; commit: `33e4f50-dirty`; budget: {}
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
- fill (fill_acc): the template is filled (formcast fill, real form fields) into the variant's PDF with K distinctive probe records (eval/probe.py), and every GT box with a path is checked for what its widget shows: a text or table-cell box once per record (the exact value; by provenance when GT has transforms), a choice box once across all records (right state in every record). fill_acc = correct units / units. It catches data landing in the wrong box, e.g. a table built as one segment per column, which path/joint score as right. A choice box whose expected state is the same in every probe record can't tell an always-on (or never-on) box from a right one; the json counts them as fill.static_choice.

## Per form

| form | kind | variant | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline det | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | mapped GT | steps | tokens in / out | wall s | stop | unplaceable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 0 | seed_only | 0 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 0.92 | FFDetr | 0.96 (0.44) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 0 | seed_only | 0 |
| coleman_pir | fillable | as_published | 22 | 22 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 9 | 0 | 0 / 0 | 0 | seed_only | 0 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | FFDetr | 1.00 (1.00) | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 9 | 0 | 0 / 0 | 0 | seed_only | 0 |
| ndguard | flat | as_published | 33 | 32 | 0.67 (0.64) | 0.69 | 0.77 | FFDNet-L | 0.67 (0.64) | 0.69 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 29 | 0 | 0 / 0 | 0 | seed_only | 0 |
| ndguard | flat | fake_scan | 33 | 34 | 0.76 (0.64) | 0.74 | 0.76 | FFDetr | 0.76 (0.64) | 0.74 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 29 | 0 | 0 / 0 | 0 | seed_only | 0 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 114 | 0 | 0 / 0 | 0 | seed_only | 0 |
| i9 | fillable | fake_scan | 130 | 129 | 0.95 (0.92) | 0.95 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 114 | 0 | 0 / 0 | 0 | seed_only | 0 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 170 | 0 | 0 / 0 | 0 | seed_only | 0 |
| f8949 | fillable | fake_scan | 202 | 206 | 0.86 (0.42) | 0.84 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 170 | 0 | 0 / 0 | 0 | seed_only | 0 |
| cms1500 | flat | as_published | 254 | 147 | 0.52 (0.50) | 0.89 | 0.93 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 227 | 0 | 0 / 0 | 0 | seed_only | 0 |
| cms1500 | flat | fake_scan | 254 | 28 | 0.06 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 227 | 0 | 0 / 0 | 0 | seed_only | 0 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 50 | 0 | 0 / 0 | 0 | seed_only | 0 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 50 | 0 | 0 / 0 | 0 | seed_only | 0 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 142 | 0 | 0 / 0 | 0 | seed_only | 0 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 142 | 0 | 0 / 0 | 0 | seed_only | 0 |
| grandview_app | flat | as_published | 323 | 249 | 0.76 (0.74) | 0.99 | 0.94 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 0 | 0 / 0 | 0 | seed_only | 0 |
| grandview_app | flat | fake_scan | 323 | 213 | 0.61 (0.48) | 0.93 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 0 | 0 / 0 | 0 | seed_only | 0 |
| csudh_health | flat | as_published | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 0 | 0 / 0 | 0 | seed_only | 0 |
| csudh_health | flat | fake_scan | 175 | 134 | 0.64 (0.31) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 0 | 0 / 0 | 0 | seed_only | 0 |
| fw9 | fillable | as_published | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 0 | 0 / 0 | 0 | seed_only | 0 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 0 | 0 / 0 | 0 | seed_only | 0 |
| fw4 | fillable | as_published | 48 | 48 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 0 | seed_only | 0 |
| fw4 | fillable | fake_scan | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 0 | seed_only | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 (0) | 0 | 2906 | 2351 | 0.76 (0.65) | 0.93 | 0.97 | 0.75 (0.60) | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
| as_published | 12 (0) | 0 | 1453 | 1269 | 0.85 (0.84) | 0.98 | 0.97 | 0.85 (0.76) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
| fake_scan | 12 (0) | 0 | 1453 | 1082 | 0.66 (0.46) | 0.89 | 0.98 | 0.65 (0.45) | 0.89 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0 / 0 | 0 |
