# Baseline detection, 2026-10-21

CommonForms detectors only, no agent. Recall/precision at IoU 0.3 (IoU 0.5 recall in parentheses). Forms without ground truth show detection counts only.

| split | form | kind | variant | detector | pages | GT | detected | recall@.3 (.5) | precision@.3 | type ok | missed text / choice |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | wadoc_auth | flat | as_published | FFDNet-L | 1 | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | as_published | FFDetr | 1 | 27 | 26 | 0.96 (0.96) | 1.00 | 0.96 | 1 / 0 |
| dev | wadoc_auth | flat | fake_scan | FFDNet-L | 1 | 27 | 27 | 1.00 (0.85) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | fake_scan | FFDetr | 1 | 27 | 26 | 0.96 (0.44) | 1.00 | 0.92 | 1 / 0 |
| dev | coleman_pir | fillable | as_published | FFDNet-L | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | coleman_pir | fillable | as_published | FFDetr | 1 | 22 | 23 | 0.91 (0.91) | 0.87 | 0.90 | 2 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDNet-L | 1 | 22 | 15 | 0.68 (0.68) | 1.00 | 0.87 | 7 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDetr | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | ndguard | flat | as_published | FFDNet-L | 1 | 33 | 32 | 0.67 (0.64) | 0.69 | 0.77 | 7 / 3 |
| dev | ndguard | flat | as_published | FFDetr | 1 | 33 | 31 | 0.67 (0.61) | 0.71 | 0.82 | 7 / 3 |
| dev | ndguard | flat | fake_scan | FFDNet-L | 1 | 33 | 15 | 0.42 (0.42) | 0.93 | 1.00 | 10 / 8 |
| dev | ndguard | flat | fake_scan | FFDetr | 1 | 33 | 34 | 0.76 (0.64) | 0.74 | 0.76 | 5 / 3 |
| dev | i9 | fillable | as_published | FFDNet-L | 3 | 130 | 129 | 0.95 (0.95) | 0.96 | 1.00 | 2 / 4 |
| dev | i9 | fillable | as_published | FFDetr | 3 | 130 | 129 | 0.95 (0.94) | 0.96 | 1.00 | 1 / 5 |
| dev | i9 | fillable | fake_scan | FFDNet-L | 3 | 130 | 124 | 0.95 (0.94) | 0.99 | 1.00 | 0 / 7 |
| dev | i9 | fillable | fake_scan | FFDetr | 3 | 130 | 130 | 0.94 (0.92) | 0.94 | 1.00 | 2 / 6 |
| dev | f8949 | fillable | as_published | FFDNet-L | 2 | 202 | 200 | 0.99 (0.98) | 1.00 | 1.00 | 2 / 0 |
| dev | f8949 | fillable | as_published | FFDetr | 2 | 202 | 198 | 0.94 (0.21) | 0.95 | 1.00 | 9 / 4 |
| dev | f8949 | fillable | fake_scan | FFDNet-L | 2 | 202 | 4 | 0.02 (0.02) | 1.00 | 1.00 | 186 / 12 |
| dev | f8949 | fillable | fake_scan | FFDetr | 2 | 202 | 203 | 0.83 (0.44) | 0.83 | 1.00 | 26 / 8 |
| dev | cms1500 | flat | as_published | FFDNet-L | 1 | 254 | 157 | 0.52 (0.50) | 0.83 | 0.92 | 119 / 3 |
| dev | cms1500 | flat | as_published | FFDetr | 1 | 254 | 43 | 0.13 (0.11) | 0.77 | 0.91 | 210 / 10 |
| dev | cms1500 | flat | fake_scan | FFDNet-L | 1 | 254 | 156 | 0.44 (0.18) | 0.72 | 0.98 | 122 / 19 |
| dev | cms1500 | flat | fake_scan | FFDetr | 1 | 254 | 18 | 0.04 (0.01) | 0.61 | 1.00 | 222 / 18 |
| dev | menomonie_app | fillable | as_published | FFDNet-L | 3 | 56 | 56 | 1.00 (1.00) | 1.00 | 0.98 | 0 / 0 |
| dev | menomonie_app | fillable | as_published | FFDetr | 3 | 56 | 49 | 0.88 (0.86) | 1.00 | 0.98 | 0 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDNet-L | 3 | 56 | 52 | 0.88 (0.82) | 0.94 | 0.98 | 0 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDetr | 3 | 56 | 56 | 0.98 (0.91) | 0.98 | 0.98 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDNet-L | 2 | 160 | 160 | 0.99 (0.29) | 0.99 | 1.00 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDetr | 2 | 160 | 162 | 0.98 (0.29) | 0.97 | 1.00 | 1 / 2 |
| dev | columbus_medhist | fillable | fake_scan | FFDNet-L | 2 | 160 | 69 | 0.43 (0.29) | 1.00 | 1.00 | 0 / 91 |
| dev | columbus_medhist | fillable | fake_scan | FFDetr | 2 | 160 | 160 | 0.97 (0.49) | 0.97 | 1.00 | 0 / 5 |
| dev | grandview_app | flat | as_published | FFDNet-L | 4 | 323 | 260 | 0.76 (0.74) | 0.95 | 0.93 | 52 / 24 |
| dev | grandview_app | flat | as_published | FFDetr | 4 | 323 | 233 | 0.66 (0.54) | 0.91 | 1.00 | 63 / 47 |
| dev | grandview_app | flat | fake_scan | FFDNet-L | 4 | 323 | 199 | 0.58 (0.56) | 0.94 | 0.93 | 67 / 68 |
| dev | grandview_app | flat | fake_scan | FFDetr | 4 | 323 | 209 | 0.59 (0.49) | 0.92 | 1.00 | 93 / 37 |
| dev | csudh_health | flat | as_published | FFDNet-L | 2 | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | 1 / 3 |
| dev | csudh_health | flat | as_published | FFDetr | 2 | 175 | 113 | 0.58 (0.26) | 0.90 | 0.99 | 23 / 49 |
| dev | csudh_health | flat | fake_scan | FFDNet-L | 2 | 175 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 55 / 118 |
| dev | csudh_health | flat | fake_scan | FFDetr | 2 | 175 | 132 | 0.67 (0.31) | 0.89 | 0.98 | 16 / 42 |
| dev | fw9 | fillable | as_published | FFDNet-L | 1 | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | 0 / 0 |
| dev | fw9 | fillable | as_published | FFDetr | 1 | 23 | 28 | 0.96 (0.74) | 0.79 | 1.00 | 0 / 1 |
| dev | fw9 | fillable | fake_scan | FFDNet-L | 1 | 23 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 15 / 8 |
| dev | fw9 | fillable | fake_scan | FFDetr | 1 | 23 | 27 | 0.57 (0.48) | 0.48 | 1.00 | 2 / 8 |
| dev | fw4 | fillable | as_published | FFDNet-L | 3 | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | 0 / 0 |
| dev | fw4 | fillable | as_published | FFDetr | 3 | 48 | 43 | 0.83 (0.79) | 0.93 | 1.00 | 5 / 3 |
| dev | fw4 | fillable | fake_scan | FFDNet-L | 3 | 48 | 23 | 0.48 (0.48) | 1.00 | 1.00 | 20 / 5 |
| dev | fw4 | fillable | fake_scan | FFDetr | 3 | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | 5 / 1 |
