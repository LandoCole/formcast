# Baseline detection, 2026-10-21_onnx

CommonForms detectors only, no agent. Recall/precision at IoU 0.3 (IoU 0.5 recall in parentheses). Forms without ground truth show detection counts only.

| split | form | kind | variant | detector | pages | GT | detected | recall@.3 (.5) | precision@.3 | type ok | missed text / choice |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | wadoc_auth | flat | as_published | FFDNet-L | 1 | 27 | 27 | 1.00 (1.00) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | as_published | FFDNet-L-onnx | 1 | 27 | 27 | 1.00 (0.89) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | as_published | FFDNet-S-onnx | 1 | 27 | 27 | 1.00 (0.85) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | fake_scan | FFDNet-L | 1 | 27 | 27 | 1.00 (0.85) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | fake_scan | FFDNet-L-onnx | 1 | 27 | 27 | 1.00 (0.78) | 1.00 | 0.93 | 0 / 0 |
| dev | wadoc_auth | flat | fake_scan | FFDNet-S-onnx | 1 | 27 | 27 | 1.00 (0.81) | 1.00 | 0.93 | 0 / 0 |
| dev | coleman_pir | fillable | as_published | FFDNet-L | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | coleman_pir | fillable | as_published | FFDNet-L-onnx | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | coleman_pir | fillable | as_published | FFDNet-S-onnx | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDNet-L | 1 | 22 | 15 | 0.68 (0.68) | 1.00 | 0.87 | 7 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDNet-L-onnx | 1 | 22 | 10 | 0.45 (0.45) | 1.00 | 0.90 | 11 / 0 |
| dev | coleman_pir | fillable | fake_scan | FFDNet-S-onnx | 1 | 22 | 25 | 1.00 (1.00) | 0.88 | 0.91 | 0 / 0 |
| dev | ndguard | flat | as_published | FFDNet-L | 1 | 33 | 32 | 0.67 (0.64) | 0.69 | 0.77 | 7 / 3 |
| dev | ndguard | flat | as_published | FFDNet-L-onnx | 1 | 33 | 30 | 0.64 (0.64) | 0.70 | 0.76 | 8 / 3 |
| dev | ndguard | flat | as_published | FFDNet-S-onnx | 1 | 33 | 31 | 0.70 (0.61) | 0.74 | 0.78 | 6 / 3 |
| dev | ndguard | flat | fake_scan | FFDNet-L | 1 | 33 | 15 | 0.42 (0.42) | 0.93 | 1.00 | 10 / 8 |
| dev | ndguard | flat | fake_scan | FFDNet-L-onnx | 1 | 33 | 14 | 0.42 (0.42) | 1.00 | 1.00 | 10 / 8 |
| dev | ndguard | flat | fake_scan | FFDNet-S-onnx | 1 | 33 | 29 | 0.61 (0.61) | 0.69 | 0.75 | 9 / 3 |
| dev | i9 | fillable | as_published | FFDNet-L | 3 | 130 | 129 | 0.95 (0.95) | 0.96 | 0.93 | 2 / 4 |
| dev | i9 | fillable | as_published | FFDNet-L-onnx | 3 | 130 | 122 | 0.92 (0.91) | 0.98 | 0.93 | 6 / 4 |
| dev | i9 | fillable | as_published | FFDNet-S-onnx | 3 | 130 | 125 | 0.96 (0.95) | 1.00 | 0.93 | 1 / 4 |
| dev | i9 | fillable | fake_scan | FFDNet-L | 3 | 130 | 124 | 0.95 (0.94) | 0.99 | 0.93 | 0 / 7 |
| dev | i9 | fillable | fake_scan | FFDNet-L-onnx | 3 | 130 | 116 | 0.89 (0.88) | 1.00 | 0.93 | 5 / 8 |
| dev | i9 | fillable | fake_scan | FFDNet-S-onnx | 3 | 130 | 127 | 0.96 (0.95) | 0.98 | 0.93 | 1 / 4 |
| dev | f8949 | fillable | as_published | FFDNet-L | 2 | 202 | 200 | 0.99 (0.98) | 1.00 | 1.00 | 2 / 0 |
| dev | f8949 | fillable | as_published | FFDNet-L-onnx | 2 | 202 | 198 | 0.98 (0.96) | 1.00 | 1.00 | 4 / 0 |
| dev | f8949 | fillable | as_published | FFDNet-S-onnx | 2 | 202 | 193 | 0.94 (0.94) | 0.98 | 1.00 | 12 / 0 |
| dev | f8949 | fillable | fake_scan | FFDNet-L | 2 | 202 | 4 | 0.02 (0.02) | 1.00 | 1.00 | 186 / 12 |
| dev | f8949 | fillable | fake_scan | FFDNet-L-onnx | 2 | 202 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 190 / 12 |
| dev | f8949 | fillable | fake_scan | FFDNet-S-onnx | 2 | 202 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 190 / 12 |
| dev | cms1500 | flat | as_published | FFDNet-L | 1 | 254 | 157 | 0.52 (0.50) | 0.83 | 0.92 | 119 / 3 |
| dev | cms1500 | flat | as_published | FFDNet-L-onnx | 1 | 254 | 148 | 0.52 (0.50) | 0.89 | 0.95 | 118 / 4 |
| dev | cms1500 | flat | as_published | FFDNet-S-onnx | 1 | 254 | 133 | 0.50 (0.49) | 0.96 | 0.98 | 116 / 8 |
| dev | cms1500 | flat | fake_scan | FFDNet-L | 1 | 254 | 156 | 0.44 (0.18) | 0.72 | 0.98 | 122 / 19 |
| dev | cms1500 | flat | fake_scan | FFDNet-L-onnx | 1 | 254 | 127 | 0.37 (0.16) | 0.73 | 0.98 | 135 / 25 |
| dev | cms1500 | flat | fake_scan | FFDNet-S-onnx | 1 | 254 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 222 / 29 |
| dev | menomonie_app | fillable | as_published | FFDNet-L | 3 | 56 | 56 | 1.00 (1.00) | 1.00 | 0.98 | 0 / 0 |
| dev | menomonie_app | fillable | as_published | FFDNet-L-onnx | 3 | 56 | 56 | 1.00 (1.00) | 1.00 | 0.98 | 0 / 0 |
| dev | menomonie_app | fillable | as_published | FFDNet-S-onnx | 3 | 56 | 56 | 1.00 (1.00) | 1.00 | 0.98 | 0 / 0 |
| dev | menomonie_app | fillable | fake_scan | FFDNet-L | 3 | 56 | 52 | 0.88 (0.82) | 0.94 | 0.98 | 0 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDNet-L-onnx | 3 | 56 | 48 | 0.86 (0.80) | 1.00 | 0.98 | 1 / 7 |
| dev | menomonie_app | fillable | fake_scan | FFDNet-S-onnx | 3 | 56 | 55 | 0.98 (0.88) | 1.00 | 0.98 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDNet-L | 2 | 160 | 160 | 0.99 (0.29) | 0.99 | 1.00 | 0 / 1 |
| dev | columbus_medhist | fillable | as_published | FFDNet-L-onnx | 2 | 160 | 160 | 1.00 (0.29) | 1.00 | 1.00 | 0 / 0 |
| dev | columbus_medhist | fillable | as_published | FFDNet-S-onnx | 2 | 160 | 160 | 0.99 (0.29) | 0.99 | 1.00 | 0 / 1 |
| dev | columbus_medhist | fillable | fake_scan | FFDNet-L | 2 | 160 | 69 | 0.43 (0.29) | 1.00 | 1.00 | 0 / 91 |
| dev | columbus_medhist | fillable | fake_scan | FFDNet-L-onnx | 2 | 160 | 66 | 0.41 (0.28) | 1.00 | 1.00 | 2 / 92 |
| dev | columbus_medhist | fillable | fake_scan | FFDNet-S-onnx | 2 | 160 | 160 | 0.99 (0.29) | 0.99 | 1.00 | 0 / 2 |
| dev | grandview_app | flat | as_published | FFDNet-L | 4 | 323 | 260 | 0.76 (0.74) | 0.95 | 0.93 | 52 / 24 |
| dev | grandview_app | flat | as_published | FFDNet-L-onnx | 4 | 323 | 227 | 0.70 (0.68) | 0.99 | 0.92 | 64 / 33 |
| dev | grandview_app | flat | as_published | FFDNet-S-onnx | 4 | 323 | 222 | 0.65 (0.63) | 0.95 | 0.98 | 71 / 41 |
| dev | grandview_app | flat | fake_scan | FFDNet-L | 4 | 323 | 199 | 0.58 (0.56) | 0.94 | 0.93 | 67 / 68 |
| dev | grandview_app | flat | fake_scan | FFDNet-L-onnx | 4 | 323 | 140 | 0.41 (0.40) | 0.94 | 0.94 | 109 / 82 |
| dev | grandview_app | flat | fake_scan | FFDNet-S-onnx | 4 | 323 | 20 | 0.06 (0.06) | 0.95 | 1.00 | 205 / 98 |
| dev | csudh_health | flat | as_published | FFDNet-L | 2 | 175 | 173 | 0.98 (0.98) | 0.99 | 0.99 | 1 / 3 |
| dev | csudh_health | flat | as_published | FFDNet-L-onnx | 2 | 175 | 147 | 0.83 (0.83) | 0.99 | 0.99 | 7 / 23 |
| dev | csudh_health | flat | as_published | FFDNet-S-onnx | 2 | 175 | 171 | 0.97 (0.97) | 0.99 | 0.99 | 6 / 0 |
| dev | csudh_health | flat | fake_scan | FFDNet-L | 2 | 175 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 55 / 118 |
| dev | csudh_health | flat | fake_scan | FFDNet-L-onnx | 2 | 175 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 55 / 118 |
| dev | csudh_health | flat | fake_scan | FFDNet-S-onnx | 2 | 175 | 159 | 0.90 (0.90) | 0.99 | 0.99 | 10 / 8 |
| dev | fw9 | fillable | as_published | FFDNet-L | 1 | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | 0 / 0 |
| dev | fw9 | fillable | as_published | FFDNet-L-onnx | 1 | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | 0 / 0 |
| dev | fw9 | fillable | as_published | FFDNet-S-onnx | 1 | 23 | 23 | 1.00 (1.00) | 1.00 | 1.00 | 0 / 0 |
| dev | fw9 | fillable | fake_scan | FFDNet-L | 1 | 23 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 15 / 8 |
| dev | fw9 | fillable | fake_scan | FFDNet-L-onnx | 1 | 23 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 15 / 8 |
| dev | fw9 | fillable | fake_scan | FFDNet-S-onnx | 1 | 23 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 15 / 8 |
| dev | fw4 | fillable | as_published | FFDNet-L | 3 | 48 | 49 | 1.00 (1.00) | 0.98 | 1.00 | 0 / 0 |
| dev | fw4 | fillable | as_published | FFDNet-L-onnx | 3 | 48 | 50 | 1.00 (1.00) | 0.96 | 1.00 | 0 / 0 |
| dev | fw4 | fillable | as_published | FFDNet-S-onnx | 3 | 48 | 49 | 0.98 (0.98) | 0.96 | 1.00 | 1 / 0 |
| dev | fw4 | fillable | fake_scan | FFDNet-L | 3 | 48 | 23 | 0.48 (0.48) | 1.00 | 1.00 | 20 / 5 |
| dev | fw4 | fillable | fake_scan | FFDNet-L-onnx | 3 | 48 | 23 | 0.48 (0.48) | 1.00 | 1.00 | 20 / 5 |
| dev | fw4 | fillable | fake_scan | FFDNet-S-onnx | 3 | 48 | 0 | 0.00 (0.00) | 0.00 | 0.00 | 43 / 5 |

## Totals by form kind

Micro-averaged over GT fields of the forms that have ground truth. Precision is matched/detected at IoU 0.3.

| kind | variant | detector | GT | detected | recall@.5 | recall@.3 | precision@.3 |
|---|---|---|---|---|---|---|---|
| fillable | as_published | FFDNet-L | 641 | 642 | 0.81 | 0.99 | 0.98 |
| fillable | as_published | FFDNet-L-onnx | 641 | 634 | 0.79 | 0.98 | 0.99 |
| fillable | as_published | FFDNet-S-onnx | 641 | 631 | 0.79 | 0.97 | 0.99 |
| fillable | fake_scan | FFDNet-L | 641 | 287 | 0.40 | 0.44 | 0.99 |
| fillable | fake_scan | FFDNet-L-onnx | 641 | 263 | 0.37 | 0.41 | 1.00 |
| fillable | fake_scan | FFDNet-S-onnx | 641 | 367 | 0.38 | 0.56 | 0.98 |
| flat | as_published | FFDNet-L | 812 | 649 | 0.72 | 0.74 | 0.92 |
| flat | as_published | FFDNet-L-onnx | 812 | 579 | 0.66 | 0.68 | 0.95 |
| flat | as_published | FFDNet-S-onnx | 812 | 584 | 0.66 | 0.69 | 0.95 |
| flat | fake_scan | FFDNet-L | 812 | 397 | 0.32 | 0.42 | 0.86 |
| flat | fake_scan | FFDNet-L-onnx | 812 | 308 | 0.25 | 0.33 | 0.86 |
| flat | fake_scan | FFDNet-S-onnx | 812 | 235 | 0.27 | 0.27 | 0.95 |
