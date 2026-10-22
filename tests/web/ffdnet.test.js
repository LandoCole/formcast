// ffdnet.js decodes the FFDNet output as FFDNetOnnxDetector's _decode does (the same cases as
// tests/pagemap/test_detect_onnx.py). Pillow-exact resizing is checked against Python in
// tests/demo/test_browser_engine.py.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CONFIDENCE,
  DETECT_DPI,
  IMAGE_SIZE,
  MODELS,
  NMS_IOU,
  decode,
  letterbox,
  letterboxPlacement,
} from "../../demo/web/engines/browser/ffdnet.js";

/** Model output layout [1, 4 + classes, anchors] from rows [cx, cy, w, h, ...scores]. */
function raw(rows) {
  const n = rows.length;
  const out = new Float32Array(rows[0].length * n);
  rows.forEach((row, i) => row.forEach((v, r) => { out[r * n + i] = v; }));
  return { data: out, anchors: n };
}

const FULL = { scale: 1.0, left: 0, top: 0, width: 1216, height: 1216 };
const near = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-6);

test("constants are the library's", () => {
  assert.equal(IMAGE_SIZE, 1216);
  assert.equal(CONFIDENCE, 0.3);
  assert.equal(NMS_IOU, 0.1);
  assert.equal(DETECT_DPI, 144);
  assert.deepEqual(MODELS.S, { url: "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx", bytes: 38370092 });
  assert.deepEqual(MODELS.L, { url: "https://huggingface.co/jbarrow/FFDNet-L-cpu/resolve/main/FFDNet-L.onnx", bytes: 101944542 });
});

test("decode keeps the more confident of two overlapping boxes", () => {
  const { data, anchors } = raw([[300, 300, 100, 40, 0.9, 0, 0], [304, 300, 100, 40, 0.6, 0, 0]]);
  const out = decode(data, anchors, FULL);
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, "text");
  assert.equal(Math.round(out[0].conf * 100) / 100, 0.9);
  assert.ok(near(out[0].box, [250 / 1216, 280 / 1216, 350 / 1216, 320 / 1216]));
});

test("decode NMS is per class and the confidence drops weak boxes", () => {
  const { data, anchors } = raw([
    [300, 300, 100, 40, 0.9, 0, 0],
    [300, 300, 100, 40, 0, 0.5, 0], // same place, other class: kept
    [800, 800, 60, 60, 0.29, 0.1, 0], // best score under 0.3: dropped
  ]);
  assert.deepEqual(decode(data, anchors, FULL).map((d) => d.kind).sort(), ["choice", "text"]);
});

test("decode maps class ids to kinds and orders by score", () => {
  const { data, anchors } = raw([[100, 100, 40, 40, 0, 0, 0.8], [600, 600, 40, 40, 0, 0.95, 0]]);
  assert.deepEqual(decode(data, anchors, FULL).map((d) => d.kind), ["choice", "signature"]);
});

test("decode undoes the letterbox offset and scale", () => {
  const lb = { scale: 1.5, left: 158, top: 308, width: 600, height: 400 };
  const { data, anchors } = raw([[(383 + 608) / 2, (458 + 608) / 2, 225, 150, 0.8, 0, 0]]);
  const [d] = decode(data, anchors, lb);
  assert.ok(near(d.box, [150 / 600, 100 / 400, 300 / 600, 200 / 400]));
});

test("decode clamps boxes that reach into the padding", () => {
  const lb = { scale: 1.0, left: 100, top: 0, width: 1000, height: 1216 };
  const { data, anchors } = raw([[105, 600, 40, 40, 0.8, 0, 0]]);
  const [d] = decode(data, anchors, lb);
  assert.equal(d.box[0], 0);
  assert.ok(d.box[2] > 0 && d.box[2] < 1);
});

test("decode of nothing above the confidence is empty", () => {
  const { data, anchors } = raw([[100, 100, 40, 40, 0.1, 0.1, 0.1]]);
  assert.deepEqual(decode(data, anchors, FULL), []);
});

test("letterbox placement: a Letter page at 144 dpi is scaled and centred like ultralytics", () => {
  const p = letterboxPlacement(1224, 1584);
  assert.equal(p.newH, 1216);
  assert.equal(p.newW, 940); // round(1224 * 1216 / 1584) = round(939.6)
  assert.equal(p.left, 138); // round((1216 - 940) / 2 - 0.1)
  assert.equal(p.top, 0);
  const up = letterboxPlacement(600, 400);
  assert.deepEqual([up.scale, up.newW, up.newH, up.left, up.top], [1216 / 600, 1216, 811, 0, 202]);
});

test("letterbox fills the padding with grey 114 and the image as RGB / 255, channels first", () => {
  const w = 4;
  const h = 2;
  const rgba = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) rgba.set([200, 100, 50, 255], i * 4);
  const { tensor, letterbox: lb } = letterbox(rgba, w, h, 8);
  assert.deepEqual(lb, { scale: 2, left: 0, top: 2, width: 4, height: 2 });
  assert.equal(tensor.length, 3 * 64);
  assert.equal(tensor[0], Math.fround(114 / 255)); // top-left pixel is padding
  const at = (c, y, x) => tensor[c * 64 + y * 8 + x];
  assert.deepEqual([at(0, 3, 3), at(1, 3, 3), at(2, 3, 3)], [200, 100, 50].map((v) => Math.fround(v / 255)));
  assert.equal(at(0, 6, 0), Math.fround(114 / 255));
});
