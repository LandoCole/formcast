# Vendored libraries

Third-party browser code, copied unmodified from the npm tarballs so the demo runs
without a CDN. Nothing here loads from the network at runtime. Only the model weights
are fetched, from Hugging Face. Fetched 2026-10-22 with `npm pack <name>@<version>`.

| Library | Version | Released | Licence | Source |
| --- | --- | --- | --- | --- |
| pdfjs-dist | 6.3.289 | 2026-08-29 | Apache-2.0 | https://www.npmjs.com/package/pdfjs-dist/v/6.3.289 |
| pdf-lib | 1.17.1 | 2021-11-06 | MIT | https://www.npmjs.com/package/pdf-lib/v/1.17.1 |
| onnxruntime-web | 1.30.0 | 2026-09-14 | MIT | https://www.npmjs.com/package/onnxruntime-web/v/1.30.0 |

## pdfjs/ (about 5.6 MB)

Licence: `pdfjs/LICENSE`.

| File | Bytes |
| --- | ---: |
| `build/pdf.min.mjs` | 458,705 |
| `build/pdf.worker.min.mjs` | 1,265,413 |
| `cmaps/` (character maps, 169 files) | about 1.6 MB |
| `standard_fonts/` (the 14 standard fonts, 16 files) | about 0.8 MB |
| `wasm/` (JBIG2, JPEG 2000 and colour decoders, with their licences) | about 1.5 MB |
| `iccs/` (ICC profile and licence) | about 20 KB |

`cmaps/` and `standard_fonts/` are needed to render many forms correctly. Point pdf.js
at them with `cMapUrl`, `standardFontDataUrl` and `wasmUrl`.

## pdf-lib/ (about 0.5 MB)

Licence: `pdf-lib/LICENSE.md`.

| File | Bytes |
| --- | ---: |
| `pdf-lib.esm.min.js` | 523,417 |

## onnxruntime-web/ (about 26 MB)

Licence: `onnxruntime-web/LICENSE`. The npm tarball ships no licence file, so this is the
MIT text from the project, with the copyright line taken from the header of
`ort.webgpu.min.mjs` ("Copyright (c) Microsoft Corporation. Licensed under the MIT License.").

The WebGPU build. Its single WebAssembly binary also holds the CPU (WASM) provider, so
one set of files serves both.

| File | Bytes |
| --- | ---: |
| `ort.webgpu.min.mjs` | 66,349 |
| `ort-wasm-simd-threaded.asyncify.mjs` (loader) | 53,057 |
| `ort-wasm-simd-threaded.asyncify.wasm` | 26,781,914 |

Set `ort.env.wasm.wasmPaths` to this folder so the loader and binary resolve here.

## Total

32.7 MB in 209 files, most of it the ONNX Runtime WebAssembly binary.
