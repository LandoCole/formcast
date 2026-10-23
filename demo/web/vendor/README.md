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
| `build/pdf.min.mjs` | 518,555 |
| `build/pdf.worker.min.mjs` | 1,317,034 |
| `cmaps/` (character maps, 169 files) | about 1.6 MB |
| `standard_fonts/` (the 14 standard fonts, 16 files) | about 0.8 MB |
| `wasm/` (JBIG2, JPEG 2000, colour and QuickJS sandbox builds, with their licences) | about 1.5 MB |
| `iccs/` (ICC profile and licence) | about 20 KB |

The two `build/` files are the tarball's `legacy/build/pdf.min.mjs` and
`legacy/build/pdf.worker.min.mjs` (same version, unmodified), not its modern `build/`. The modern
build calls `Map.prototype.getOrInsertComputed` and `Math.sumPrecise`, which Safari 26.0 lacks, so
no PDF opened there; the legacy build adds them. It does not add async iteration of a
`ReadableStream`, which pdf.js uses to read text; `engines/browser/streams.js` adds that.
Replaced on 2026-10-23 (tarball integrity
`sha512-ZHjSVpDa3D6izMq8/04lvkhkATUmL9px6ChPaXc1k6nU2Mrhlg1/7F0bdUqCwUjw3NsPTfPZsMDUU6ZIcRaeQw==`).

`cmaps/` and `standard_fonts/` are needed to render many forms correctly. Point pdf.js
at them with `cMapUrl`, `standardFontDataUrl` and `wasmUrl`.

The npm tarball ships licences for the JBIG2, OpenJPEG and QCMS builds in `wasm/` but none
for `wasm/quickjs-eval.wasm` and `quickjs-eval.js` (the QuickJS sandbox pdf.js uses for form
scripts). pdf.js's repository at tag `v6.3.289` ships no licence text for them either: its
`external/quickjs/README.md` says QuickJS and pdf.js.quickjs are MIT, so the build is MIT. The
two vendored files are byte-identical to `external/quickjs/` at that tag (same git blob ids).
Added on 2026-10-22, in the same naming as the other `wasm/` licences:

| File | Source |
| --- | --- |
| `wasm/LICENSE_QUICKJS` | QuickJS's `LICENSE` at commit `3d5e064e9dd67c70f7962836505a7fa067bf0a4e`, https://raw.githubusercontent.com/bellard/quickjs/3d5e064e9dd67c70f7962836505a7fa067bf0a4e/LICENSE (the revision pdf.js built from: pdf.js commit 7a7e4fd, "Update quickjs to rev 3d5e064e…", the last change to `quickjs-eval.wasm` before `v6.3.289`) |
| `wasm/LICENSE_PDFJS_QUICKJS` | pdf.js.quickjs's `LICENSE` (MIT, Mozilla Foundation), https://raw.githubusercontent.com/mozilla/pdf.js.quickjs/e2c5bfc8194b16f5973eea5e4e025b67be3c1015/LICENSE |

## pdf-lib/ (about 0.5 MB)

Licence: `pdf-lib/LICENSE.md`.

| File | Bytes |
| --- | ---: |
| `pdf-lib.esm.min.js` | 523,417 |

## onnxruntime-web/ (about 26 MB)

Licence: `onnxruntime-web/LICENSE`. The npm tarball ships no licence file, so this is the
MIT text from the project, with the copyright line taken from the header of
`ort.webgpu.min.mjs` ("Copyright (c) Microsoft Corporation. Licensed under the MIT License.").

Third-party notices: `onnxruntime-web/ThirdPartyNotices.txt` (338,088 bytes), the project's
notices for the components built into ONNX Runtime, unmodified from the `v1.30.0` tag:
https://raw.githubusercontent.com/microsoft/onnxruntime/v1.30.0/ThirdPartyNotices.txt
(fetched 2026-10-22; SHA-256 `143764b952fdb1a7c69ce653bfba74a7744d6a8a573bfb73e235fba356c83de3`).

The WebGPU build. Its single WebAssembly binary also holds the CPU (WASM) provider, so
one set of files serves both.

| File | Bytes |
| --- | ---: |
| `ort.webgpu.min.mjs` | 66,349 |
| `ort-wasm-simd-threaded.asyncify.mjs` (loader) | 53,057 |
| `ort-wasm-simd-threaded.asyncify.wasm` | 26,781,914 |

Set `ort.env.wasm.wasmPaths` to this folder so the loader and binary resolve here.

## Total

33.0 MB in 212 files, most of it the ONNX Runtime WebAssembly binary.
