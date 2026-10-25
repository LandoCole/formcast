# Agent eval: claude-haiku-5-5 (ANTHROPIC_EVAL), dev, 2026-10-23_final-agent, pipeline agent

- model: `claude-haiku-5-5` via `chat_completions`; tag: final; commit: `849f144`; budget: {'max_steps': 40, 'max_input_tokens': 1500000, 'max_output_tokens': 200000, 'wall_clock_s': 900}
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
| wadoc_auth | flat | as_published | 27 | 27 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 16 | 203,875 / 16,506 | 86 | finish | 4 |
| wadoc_auth | flat | fake_scan | 27 | 26 | 0.96 (0.81) | 1.00 | 1.00 | FFDetr | 0.96 (0.44) | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.94 | 17 | 12 | 167,258 / 23,357 | 114 | finish | 4 |
| coleman_pir | fillable | as_published | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 6 | 70,378 / 8,267 | 41 | finish | 3 |
| coleman_pir | fillable | fake_scan | 22 | 25 | 1.00 (1.00) | 0.88 | 1.00 | FFDetr | 1.00 (1.00) | 0.88 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9 | 20 | 259,565 / 37,872 | 193 | finish | 3 |
| ndguard | flat | as_published | 33 | 40 | 0.94 (0.76) | 0.78 | 1.00 | FFDNet-L | 0.67 (0.64) | 0.69 | 1.00 | 0.75 | 0.86 | 0.82 | 0.83 | 29 | 16 | 265,178 / 37,029 | 182 | finish | 6 |
| ndguard | flat | fake_scan | 33 | 40 | 0.94 (0.76) | 0.78 | 1.00 | FFDetr | 0.76 (0.64) | 0.74 | 0.97 | 0.81 | 0.86 | 0.82 | 0.83 | 29 | 10 | 162,950 / 20,691 | 98 | finish | 2 |
| i9 | fillable | as_published | 130 | 130 | 1.00 (1.00) | 1.00 | 0.93 | FFDNet-L | 0.95 (0.95) | 0.96 | 1.00 | 0.83 | 0.94 | 0.37 | 1.00 | 114 | 14 | 377,212 / 23,122 | 110 | finish | 2 |
| i9 | fillable | fake_scan | 130 | 129 | 0.95 (0.92) | 0.95 | 0.93 | FFDetr | 0.94 (0.92) | 0.94 | 0.43 | 0.24 | 0.38 | 0.38 | 0.42 | 114 | 35 | 867,270 / 106,624 | 535 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| f8949 | fillable | as_published | 202 | 202 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.98) | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 1.00 | 170 | 5 | 125,781 / 9,669 | 48 | finish | 2 |
| f8949 | fillable | fake_scan | 202 | 209 | 0.87 (0.42) | 0.84 | 1.00 | FFDetr | 0.83 (0.44) | 0.83 | 0.85 | 0.83 | 0.85 | 0.91 | 0.85 | 170 | 12 | 265,773 / 44,522 | 212 | finish | 2 |
| cms1500 | flat | as_published | 254 | 147 | 0.52 (0.50) | 0.89 | 0.95 | FFDNet-L | 0.52 (0.50) | 0.83 | 0.16 | 0.25 | 0.16 | 0.11 | 0.16 | 227 | 32 | 953,542 / 84,470 | 417 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| cms1500 | flat | fake_scan | 254 | 28 | 0.06 (0.03) | 0.50 | 1.00 | FFDetr | 0.04 (0.01) | 0.61 | 0.06 | 0.09 | 0.06 | 0.03 | 0.06 | 227 | 18 | 357,740 / 30,936 | 150 | finish | 98 |
| menomonie_app | fillable | as_published | 56 | 56 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 50 | 3 | 38,886 / 9,010 | 43 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| menomonie_app | fillable | fake_scan | 56 | 56 | 0.98 (0.89) | 0.98 | 0.98 | FFDetr | 0.98 (0.91) | 0.98 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 50 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| columbus_medhist | fillable | as_published | 160 | 160 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 0.99 (0.29) | 0.99 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 142 | 0 | 0 / 0 | 0 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| columbus_medhist | fillable | fake_scan | 160 | 159 | 0.96 (0.49) | 0.97 | 1.00 | FFDetr | 0.97 (0.49) | 0.97 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 142 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| grandview_app | flat | as_published | 323 | 249 | 0.76 (0.74) | 0.99 | 0.94 | FFDNet-L | 0.76 (0.74) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 0 | 0 / 0 | 0 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| grandview_app | flat | fake_scan | 323 | 213 | 0.61 (0.48) | 0.93 | 0.99 | FFDetr | 0.59 (0.49) | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 247 | 0 | 0 / 0 | 2 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| csudh_health | flat | as_published | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | FFDNet-L | 0.98 (0.98) | 0.99 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| csudh_health | flat | fake_scan | 175 | 134 | 0.64 (0.31) | 0.84 | 0.98 | FFDetr | 0.67 (0.31) | 0.89 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 155 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| fw9 | fillable | as_published | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| fw9 | fillable | fake_scan | 23 | 28 | 0.61 (0.48) | 0.50 | 1.00 | FFDetr | 0.57 (0.48) | 0.48 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 20 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| fw4 | fillable | as_published | 48 | 48 | 1.00 (1.00) | 1.00 | 1.00 | FFDNet-L | 1.00 (1.00) | 0.98 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |
| fw4 | fillable | fake_scan | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | FFDetr | 0.88 (0.81) | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 17 | 0 | 0 / 0 | 1 | error (EXCLUDED from totals: FormcastError: LLM request to https://api.anthropic.com/v1/c) | 0 |

## Totals (micro-averaged)

| variant | forms (failed) | finished | GT | boxes | recall@.3 (.5) | precision@.3 | type acc | baseline recall@.3 (.5) | baseline precision@.3 | path | transform | joint | fill | exact | reachable path (joint) | steps | tokens in / out | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 10 (14) | 10 | 952 | 752 | 0.71 (0.60) | 0.90 | 0.99 | 0.68 (0.56) | 0.90 | 0.70 | 0.59 | 0.68 | 0.56 | 0.68 | 0.98 (0.95) | 199 | 4,115,408 / 452,075 | 2240 |
| as_published | 5 (7) | 5 | 414 | 424 | 1.00 (0.98) | 0.97 | 0.98 | 0.95 (0.94) | 0.96 | 1.00 | 0.91 | 0.97 | 0.80 | 0.98 | 1.00 (0.97) | 92 | 2,034,852 / 188,073 | 930 |
| fake_scan | 5 (7) | 5 | 538 | 328 | 0.50 (0.30) | 0.82 | 1.00 | 0.47 (0.27) | 0.82 | 0.47 | 0.39 | 0.46 | 0.42 | 0.46 | 0.94 (0.93) | 107 | 2,080,556 / 264,002 | 1309 |

Cost at the built-in prices: $0.64.

Excluded from the totals above (session ended in an error or skipped): i9/fake_scan, cms1500/as_published, menomonie_app/as_published, menomonie_app/fake_scan, columbus_medhist/as_published, columbus_medhist/fake_scan, grandview_app/as_published, grandview_app/fake_scan, csudh_health/as_published, csudh_health/fake_scan, fw9/as_published, fw9/fake_scan, fw4/as_published, fw4/fake_scan.
