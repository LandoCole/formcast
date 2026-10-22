// FFDNet pre- and post-processing, as `formcast.pagemap.detect.FFDNetOnnxDetector` does it (plan
// Ruling A): letterbox the page render into the 1216 x 1216 model input, then decode the raw
// output tensor (best class per anchor, confidence 0.3, class-aware greedy NMS at IoU 0.1, at most
// 300 boxes) and undo the letterbox. Pure (no browser globals): the detection worker imports it and
// Node tests it.

/** Where the ONNX exports live (never re-hosted, re-exported or quantized), and their sizes in bytes. */
export const MODELS = Object.freeze({
  S: Object.freeze({ url: "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx", bytes: 38370092 }),
  L: Object.freeze({ url: "https://huggingface.co/jbarrow/FFDNet-L-cpu/resolve/main/FFDNet-L.onnx", bytes: 101944542 }),
});
/** The library renders pages for detection at this resolution (`DetectPolicy.dpi`). */
export const DETECT_DPI = 144;
/** The exports take a fixed square input of this many pixels. */
export const IMAGE_SIZE = 1216;
/** `FFDNetOnnxDetector`'s default confidence. */
export const CONFIDENCE = 0.3;
/** The library's `DETECTOR_NMS_IOU`. */
export const NMS_IOU = 0.1;
const MAX_DET = 300;
const PAD_VALUE = 114;
/** CommonForms class ids. */
export const CLASS_KINDS = Object.freeze(["text", "choice", "signature"]);

const f32 = Math.fround;

/** Python's `round(x)` for a float: an exact half goes to the even neighbour. */
function pyRoundInt(x) {
  const r = Math.round(x);
  return (r - x === 0.5 && r % 2 !== 0 ? r - 1 : r) + 0; // + 0 turns -0 into 0
}

// ------------------------------------------------------------------ resize (Pillow's bilinear)
//
// Pillow's `Image.resize(..., BILINEAR)` (libImaging/Resample.c): a triangle filter widened by the
// scale when shrinking (so it antialiases), coefficients in fixed point with 22 fractional bits,
// a horizontal pass then a vertical pass, each rounding back to 8 bits.

const PRECISION_BITS = 32 - 8 - 2;

function coefficients(inSize, outSize) {
  const scale = inSize / outSize;
  const filterScale = Math.max(scale, 1.0);
  const support = 1.0 * filterScale;
  const ksize = Math.ceil(support) * 2 + 1;
  const bounds = new Int32Array(outSize * 2);
  const kk = new Int32Array(outSize * ksize);
  const w = new Float64Array(ksize);
  const ss = 1.0 / filterScale;
  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale;
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;
    let ww = 0.0;
    for (let x = 0; x < xmax; x++) {
      const t = Math.abs((x + xmin - center + 0.5) * ss);
      w[x] = t < 1.0 ? 1.0 - t : 0.0;
      ww += w[x];
    }
    for (let x = 0; x < xmax; x++) {
      const k = ww !== 0.0 ? w[x] / ww : w[x];
      kk[xx * ksize + x] = k < 0 ? Math.trunc(-0.5 + k * (1 << PRECISION_BITS)) : Math.trunc(0.5 + k * (1 << PRECISION_BITS));
    }
    bounds[xx * 2] = xmin;
    bounds[xx * 2 + 1] = xmax;
  }
  return { ksize, bounds, kk };
}

function clip8(ss) {
  const v = ss >> PRECISION_BITS;
  return v < 0 ? 0 : v > 255 ? 255 : v;
}

/**
 * Resize an RGB image (3 bytes per pixel) the way Pillow's BILINEAR resize does, byte for byte.
 * @param {Uint8Array | Uint8ClampedArray} rgb
 * @param {number} width
 * @param {number} height
 * @param {number} outW
 * @param {number} outH
 * @returns {Uint8Array} outW x outH RGB
 */
export function resizeBilinear(rgb, width, height, outW, outH) {
  if (outW === width && outH === height) return Uint8Array.from(rgb);
  const half = 1 << (PRECISION_BITS - 1);
  const vert = coefficients(height, outH);
  let src = rgb;
  if (outW !== width) {
    // horizontal pass over the source rows the vertical pass will read
    const horiz = coefficients(width, outW);
    const first = outH !== height ? vert.bounds[0] : 0;
    const last = outH !== height ? vert.bounds[outH * 2 - 2] + vert.bounds[outH * 2 - 1] : height;
    for (let i = 0; i < outH; i++) vert.bounds[i * 2] -= first;
    const tmp = new Uint8Array(outW * (last - first) * 3);
    for (let y = 0; y < last - first; y++) {
      const rowIn = (y + first) * width * 3;
      for (let xx = 0; xx < outW; xx++) {
        const xmin = horiz.bounds[xx * 2];
        const xmax = horiz.bounds[xx * 2 + 1];
        const k0 = xx * horiz.ksize;
        let s0 = half;
        let s1 = half;
        let s2 = half;
        for (let x = 0; x < xmax; x++) {
          const k = horiz.kk[k0 + x];
          const p = rowIn + (x + xmin) * 3;
          s0 += rgb[p] * k;
          s1 += rgb[p + 1] * k;
          s2 += rgb[p + 2] * k;
        }
        const o = (y * outW + xx) * 3;
        tmp[o] = clip8(s0);
        tmp[o + 1] = clip8(s1);
        tmp[o + 2] = clip8(s2);
      }
    }
    src = tmp;
  }
  if (outH === height) return /** @type {Uint8Array} */ (src);
  const out = new Uint8Array(outW * outH * 3);
  for (let yy = 0; yy < outH; yy++) {
    const ymin = vert.bounds[yy * 2];
    const ymax = vert.bounds[yy * 2 + 1];
    const k0 = yy * vert.ksize;
    for (let xx = 0; xx < outW; xx++) {
      let s0 = half;
      let s1 = half;
      let s2 = half;
      for (let y = 0; y < ymax; y++) {
        const k = vert.kk[k0 + y];
        const p = ((y + ymin) * outW + xx) * 3;
        s0 += src[p] * k;
        s1 += src[p + 1] * k;
        s2 += src[p + 2] * k;
      }
      const o = (yy * outW + xx) * 3;
      out[o] = clip8(s0);
      out[o + 1] = clip8(s1);
      out[o + 2] = clip8(s2);
    }
  }
  return out;
}

// ------------------------------------------------------------------ letterbox

/**
 * @typedef {{scale: number, left: number, top: number, width: number, height: number}} Letterbox
 *   how an image of `width` x `height` sits in the square input: scaled, then offset
 */

/**
 * Where an image of `width` x `height` goes in the `size` square: scale to fit (upscaling
 * allowed), centred (ultralytics' `LetterBox` with `auto=False`).
 * @param {number} width
 * @param {number} height
 * @param {number} [size]
 * @returns {Letterbox & {newW: number, newH: number}}
 */
export function letterboxPlacement(width, height, size = IMAGE_SIZE) {
  const scale = Math.min(size / width, size / height);
  const newW = Math.max(pyRoundInt(width * scale), 1);
  const newH = Math.max(pyRoundInt(height * scale), 1);
  const left = pyRoundInt((size - newW) / 2 - 0.1);
  const top = pyRoundInt((size - newH) / 2 - 0.1);
  return { scale, left, top, width, height, newW, newH };
}

/**
 * The model input for an RGBA page render: resized with Pillow's bilinear filter, centred on grey
 * (114), as a float32 NCHW tensor of RGB values / 255.
 * @param {Uint8Array | Uint8ClampedArray} rgba width x height, 4 bytes per pixel (alpha ignored)
 * @param {number} width
 * @param {number} height
 * @param {number} [size]
 * @returns {{tensor: Float32Array, letterbox: Letterbox}}
 */
export function letterbox(rgba, width, height, size = IMAGE_SIZE) {
  const p = letterboxPlacement(width, height, size);
  const rgb = new Uint8Array(width * height * 3);
  for (let i = 0, j = 0; i < rgb.length; i += 3, j += 4) {
    rgb[i] = rgba[j];
    rgb[i + 1] = rgba[j + 1];
    rgb[i + 2] = rgba[j + 2];
  }
  const resized = resizeBilinear(rgb, width, height, p.newW, p.newH);
  const plane = size * size;
  const tensor = new Float32Array(3 * plane).fill(PAD_VALUE / 255);
  for (let y = 0; y < p.newH; y++) {
    const ty = y + p.top;
    if (ty < 0 || ty >= size) continue;
    for (let x = 0; x < p.newW; x++) {
      const tx = x + p.left;
      if (tx < 0 || tx >= size) continue;
      const s = (y * p.newW + x) * 3;
      const t = ty * size + tx;
      tensor[t] = resized[s] / 255;
      tensor[plane + t] = resized[s + 1] / 255;
      tensor[2 * plane + t] = resized[s + 2] / 255;
    }
  }
  return { tensor, letterbox: { scale: p.scale, left: p.left, top: p.top, width, height } };
}

// ------------------------------------------------------------------ decode

/**
 * @typedef {{kind: "text" | "choice" | "signature", box: [number, number, number, number], conf: number}} Detection
 *   box normalized (0..1) to the image that was letterboxed
 */

/**
 * Greedy NMS over xyxy boxes (flat, 4 per box): indices kept, best score first. A box goes when
 * its IoU with a kept box is above the threshold (or undefined, for empty boxes).
 */
function nms(boxes, scores, iouThreshold) {
  const n = scores.length;
  let order = Array.from({ length: n }, (_, i) => i).sort((a, b) => scores[b] - scores[a] || a - b);
  const area = (i) => (boxes[i * 4 + 2] - boxes[i * 4]) * (boxes[i * 4 + 3] - boxes[i * 4 + 1]);
  const keep = [];
  while (order.length) {
    const best = order[0];
    keep.push(best);
    const rest = [];
    for (let r = 1; r < order.length; r++) {
      const i = order[r];
      const x0 = Math.max(boxes[best * 4], boxes[i * 4]);
      const y0 = Math.max(boxes[best * 4 + 1], boxes[i * 4 + 1]);
      const x1 = Math.min(boxes[best * 4 + 2], boxes[i * 4 + 2]);
      const y1 = Math.min(boxes[best * 4 + 3], boxes[i * 4 + 3]);
      const inter = Math.max(x1 - x0, 0) * Math.max(y1 - y0, 0);
      const overlap = inter / (area(best) + area(i) - inter);
      if (overlap <= iouThreshold) rest.push(i);
    }
    order = rest;
  }
  return keep;
}

/**
 * Detections from the raw model output `[1, 4 + classes, anchors]`: rows 0-3 are box centre x,
 * centre y, width and height in input pixels, the rest per-class scores (already sigmoided).
 * Each anchor takes its best class; anchors under `confidence` go; greedy NMS runs per class at
 * `iouThreshold`; at most 300 boxes stay, mapped back through the letterbox and clamped to 0..1.
 * Arithmetic follows the Python (float32 where numpy works in float32).
 * @param {Float32Array} raw
 * @param {number} anchors
 * @param {Letterbox} lb
 * @param {{confidence?: number, iouThreshold?: number}} [opts]
 * @returns {Detection[]}
 */
export function decode(raw, anchors, lb, { confidence = CONFIDENCE, iouThreshold = NMS_IOU } = {}) {
  const classes = raw.length / anchors - 4;
  if (!Number.isInteger(classes) || classes < 1) throw new RangeError("output does not match the anchor count");
  const kept = [];
  for (let i = 0; i < anchors; i++) {
    let cls = 0;
    let best = raw[4 * anchors + i];
    for (let c = 1; c < classes; c++) {
      const s = raw[(4 + c) * anchors + i];
      if (s > best) {
        best = s;
        cls = c;
      }
    }
    if (best >= f32(confidence)) kept.push({ i, cls, best });
  }
  if (!kept.length) return [];
  const xyxy = new Float64Array(kept.length * 4);
  const shifted = new Float64Array(kept.length * 4);
  const scores = new Float64Array(kept.length);
  kept.forEach(({ i, cls, best }, k) => {
    const cx = raw[i];
    const cy = raw[anchors + i];
    const hw = f32(raw[2 * anchors + i] / 2);
    const hh = f32(raw[3 * anchors + i] / 2);
    const box = [f32(cx - hw), f32(cy - hh), f32(cx + hw), f32(cy + hh)];
    const offset = cls * 2 * IMAGE_SIZE; // far apart per class, so one NMS pass is per class
    for (let j = 0; j < 4; j++) {
      xyxy[k * 4 + j] = box[j];
      shifted[k * 4 + j] = box[j] + offset;
    }
    scores[k] = best;
  });
  const scale = f32(lb.scale);
  const norm = (v, pad, size) => Math.min(1.0, Math.max(0.0, f32(f32(f32(v - pad) / scale) / size)));
  return nms(shifted, scores, iouThreshold)
    .slice(0, MAX_DET)
    .map((k) => ({
      kind: /** @type {Detection["kind"]} */ (CLASS_KINDS[kept[k].cls]),
      box: [
        norm(xyxy[k * 4], lb.left, lb.width),
        norm(xyxy[k * 4 + 1], lb.top, lb.height),
        norm(xyxy[k * 4 + 2], lb.left, lb.width),
        norm(xyxy[k * 4 + 3], lb.top, lb.height),
      ],
      conf: scores[k],
    }));
}
