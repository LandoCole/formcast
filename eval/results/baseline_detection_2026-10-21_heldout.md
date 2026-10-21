# Baseline detection, 2026-10-21 (held-out split)

CommonForms detectors only, no agent. Recall/precision at IoU 0.3 (IoU 0.5 recall in parentheses). Forms without ground truth show detection counts only.

| split | form | kind | variant | detector | pages | GT | detected | recall@.3 (.5) | precision@.3 | type ok | missed text / choice |
|---|---|---|---|---|---|---|---|---|---|---|---|
| heldout | ds11 | fillable | as_published | FFDNet-L | 2 | 98 | 161 | 0.85 (0.82) | 0.52 | 0.98 | 7 / 8 |
| heldout | ds11 | fillable | as_published | FFDetr | 2 | 98 | 124 | 0.78 (0.72) | 0.61 | 1.00 | 7 / 15 |
| heldout | ds11 | fillable | fake_scan | FFDNet-L | 2 | 98 | 84 | 0.77 (0.73) | 0.89 | 1.00 | 9 / 14 |
| heldout | ds11 | fillable | fake_scan | FFDetr | 2 | 98 | 104 | 0.79 (0.64) | 0.74 | 1.00 | 5 / 16 |
| heldout | ics211 | fillable | as_published | FFDNet-L | 1 | 157 | 154 | 0.94 (0.94) | 0.96 | 0.99 | 4 / 5 |
| heldout | ics211 | fillable | as_published | FFDetr | 1 | 157 | 132 | 0.73 (0.72) | 0.86 | 1.00 | 37 / 5 |
| heldout | ics211 | fillable | fake_scan | FFDNet-L | 1 | 157 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 151 / 5 |
| heldout | ics211 | fillable | fake_scan | FFDetr | 1 | 157 | 157 | 0.92 (0.91) | 0.92 | 1.00 | 7 / 5 |
| heldout | pacific_app | flat | as_published | FFDNet-L | 4 | 83 | 68 | 0.76 (0.34) | 0.93 | 0.98 | 18 / 2 |
| heldout | pacific_app | flat | as_published | FFDetr | 4 | 83 | 74 | 0.81 (0.71) | 0.91 | 0.99 | 13 / 3 |
| heldout | pacific_app | flat | fake_scan | FFDNet-L | 4 | 83 | 68 | 0.78 (0.12) | 0.96 | 1.00 | 12 / 5 |
| heldout | pacific_app | flat | fake_scan | FFDetr | 4 | 83 | 71 | 0.82 (0.53) | 0.96 | 1.00 | 11 / 3 |
| heldout | uwosh_health | flat | as_published | FFDNet-L | 2 | 318 | 255 | 0.75 (0.74) | 0.93 | 0.27 | 18 / 63 |
| heldout | uwosh_health | flat | as_published | FFDetr | 2 | 318 | 200 | 0.58 (0.51) | 0.92 | 0.72 | 31 / 104 |
| heldout | uwosh_health | flat | fake_scan | FFDNet-L | 2 | 318 | 106 | 0.32 (0.22) | 0.97 | 0.07 | 58 / 156 |
| heldout | uwosh_health | flat | fake_scan | FFDetr | 2 | 318 | 127 | 0.33 (0.19) | 0.82 | 0.59 | 36 / 178 |
| heldout | dagsboro_permit | scanned | as_published | FFDNet-L | 3 | 101 | 21 | 0.21 (0.21) | 1.00 | 1.00 | 37 / 42 |
| heldout | dagsboro_permit | scanned | as_published | FFDetr | 3 | 101 | 50 | 0.31 (0.27) | 0.62 | 0.97 | 29 / 41 |
