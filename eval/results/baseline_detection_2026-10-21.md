# Baseline detection, 2026-10-21

CommonForms detectors only, no agent. Recall/precision at IoU 0.3 (IoU 0.5 recall in parentheses). Forms without ground truth show detection counts only.

| split | form | kind | variant | detector | pages | GT | detected | recall@.3 (.5) | precision@.3 | type ok | missed text / choice |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | wadoc_auth | flat | as_published | FFDNet-L | 1 | none yet | 27 | – | – | – | – |
| dev | wadoc_auth | flat | as_published | FFDetr | 1 | none yet | 26 | – | – | – | – |
| dev | wadoc_auth | flat | fake_scan | FFDNet-L | 1 | none yet | 27 | – | – | – | – |
| dev | wadoc_auth | flat | fake_scan | FFDetr | 1 | none yet | 26 | – | – | – | – |
| dev | coleman_pir | fillable | as_published | FFDNet-L | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | coleman_pir | fillable | as_published | FFDetr | 1 | 22 | 23 | 0.91 (0.91) | 0.87 | 0.90 | 2 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDNet-L | 1 | 22 | 15 | 0.68 (0.68) | 1.00 | 0.87 | 7 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDetr | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | ndguard | flat | as_published | FFDNet-L | 1 | none yet | 32 | – | – | – | – |
| dev | ndguard | flat | as_published | FFDetr | 1 | none yet | 31 | – | – | – | – |
| dev | ndguard | flat | fake_scan | FFDNet-L | 1 | none yet | 15 | – | – | – | – |
| dev | ndguard | flat | fake_scan | FFDetr | 1 | none yet | 34 | – | – | – | – |
| dev | i9 | fillable | as_published | FFDNet-L | 3 | 130 | 129 | 0.95 (0.95) | 0.96 | 1.00 | 2 / 4 |
| dev | i9 | fillable | as_published | FFDetr | 3 | 130 | 129 | 0.95 (0.94) | 0.96 | 1.00 | 1 / 5 |
| dev | i9 | fillable | fake_scan | FFDNet-L | 3 | 130 | 124 | 0.95 (0.94) | 0.99 | 1.00 | 0 / 7 |
| dev | i9 | fillable | fake_scan | FFDetr | 3 | 130 | 130 | 0.94 (0.92) | 0.94 | 1.00 | 2 / 6 |
| dev | f8949 | fillable | as_published | FFDNet-L | 2 | 202 | 200 | 0.99 (0.98) | 1.00 | 1.00 | 2 / 0 |
| dev | f8949 | fillable | as_published | FFDetr | 2 | 202 | 198 | 0.94 (0.21) | 0.95 | 1.00 | 9 / 4 |
| dev | f8949 | fillable | fake_scan | FFDNet-L | 2 | 202 | 4 | 0.02 (0.02) | 1.00 | 1.00 | 186 / 12 |
| dev | f8949 | fillable | fake_scan | FFDetr | 2 | 202 | 203 | 0.83 (0.44) | 0.83 | 1.00 | 26 / 8 |
| dev | cms1500 | flat | as_published | FFDNet-L | 1 | none yet | 157 | – | – | – | – |
| dev | cms1500 | flat | as_published | FFDetr | 1 | none yet | 43 | – | – | – | – |
| dev | cms1500 | flat | fake_scan | FFDNet-L | 1 | none yet | 156 | – | – | – | – |
| dev | cms1500 | flat | fake_scan | FFDetr | 1 | none yet | 18 | – | – | – | – |
| dev | menomonie_app | fillable | as_published | FFDNet-L | 3 | 56 | 56 | 1.00 (1.00) | 1.00 | 0.98 | 0 / 0 |
| dev | menomonie_app | fillable | as_published | FFDetr | 3 | 56 | 49 | 0.88 (0.86) | 1.00 | 0.98 | 0 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDNet-L | 3 | 56 | 52 | 0.88 (0.82) | 0.94 | 0.98 | 0 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDetr | 3 | 56 | 56 | 0.98 (0.91) | 0.98 | 0.98 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDNet-L | 2 | 160 | 160 | 0.99 (0.29) | 0.99 | 1.00 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDetr | 2 | 160 | 162 | 0.98 (0.29) | 0.97 | 1.00 | 1 / 2 |
| dev | columbus_medhist | fillable | fake_scan | FFDNet-L | 2 | 160 | 69 | 0.43 (0.29) | 1.00 | 1.00 | 0 / 91 |
| dev | columbus_medhist | fillable | fake_scan | FFDetr | 2 | 160 | 160 | 0.97 (0.49) | 0.97 | 1.00 | 0 / 5 |
| dev | grandview_app | flat | as_published | FFDNet-L | 4 | none yet | 260 | – | – | – | – |
| dev | grandview_app | flat | as_published | FFDetr | 4 | none yet | 233 | – | – | – | – |
| dev | grandview_app | flat | fake_scan | FFDNet-L | 4 | none yet | 199 | – | – | – | – |
| dev | grandview_app | flat | fake_scan | FFDetr | 4 | none yet | 209 | – | – | – | – |
| dev | csudh_health | flat | as_published | FFDNet-L | 2 | none yet | 173 | – | – | – | – |
| dev | csudh_health | flat | as_published | FFDetr | 2 | none yet | 113 | – | – | – | – |
| dev | csudh_health | flat | fake_scan | FFDNet-L | 2 | none yet | 0 | – | – | – | – |
| dev | csudh_health | flat | fake_scan | FFDetr | 2 | none yet | 132 | – | – | – | – |
| dev | fw9 | fillable | as_published | FFDNet-L | 1 | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | 0 / 0 |
| dev | fw9 | fillable | as_published | FFDetr | 1 | 23 | 28 | 0.96 (0.74) | 0.79 | 1.00 | 0 / 1 |
| dev | fw9 | fillable | fake_scan | FFDNet-L | 1 | 23 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 15 / 8 |
| dev | fw9 | fillable | fake_scan | FFDetr | 1 | 23 | 27 | 0.57 (0.48) | 0.48 | 1.00 | 2 / 8 |
| dev | fw4 | fillable | as_published | FFDNet-L | 3 | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | 0 / 0 |
| dev | fw4 | fillable | as_published | FFDetr | 3 | 48 | 43 | 0.83 (0.79) | 0.93 | 1.00 | 5 / 3 |
| dev | fw4 | fillable | fake_scan | FFDNet-L | 3 | 48 | 23 | 0.48 (0.48) | 1.00 | 1.00 | 20 / 5 |
| dev | fw4 | fillable | fake_scan | FFDetr | 3 | 48 | 44 | 0.88 (0.81) | 0.95 | 1.00 | 5 / 1 |
