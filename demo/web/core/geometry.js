// Pure geometry: rectangle maths and the only place that converts between PDF points,
// screen pixels and PDF user space. No browser globals, so Node can import it directly.
//
// Rect: [x0, y0, x1, y1] in PDF points, origin top-left, unrotated crop-box space.

/**
 * @typedef {[number, number, number, number]} Rect
 * @typedef {{left: number, top: number, width: number, height: number}} Box
 * @typedef {{width: number, height: number, rotation: 0 | 90 | 180 | 270, scale: number}} View
 */

/**
 * Area of a rect; zero for empty or inverted rects.
 * @param {Rect} r
 * @returns {number}
 */
export function area(r) {
  return Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]);
}

/**
 * Intersection of two rects, or null when they do not overlap with positive area.
 * @param {Rect} a
 * @param {Rect} b
 * @returns {Rect | null}
 */
export function intersect(a, b) {
  const x0 = Math.max(a[0], b[0]);
  const y0 = Math.max(a[1], b[1]);
  const x1 = Math.min(a[2], b[2]);
  const y1 = Math.min(a[3], b[3]);
  if (x1 <= x0 || y1 <= y0) return null;
  return [x0, y0, x1, y1];
}

/**
 * Intersection over union; 0 for disjoint or degenerate rects.
 * @param {Rect} a
 * @param {Rect} b
 * @returns {number}
 */
export function iou(a, b) {
  const inter = intersect(a, b);
  if (inter === null) return 0;
  const interArea = area(inter);
  const union = area(a) + area(b) - interArea;
  return union > 0 ? interArea / union : 0;
}

/**
 * Share (0 to 1) of `inner`'s area that lies inside `outer`; 0 for disjoint or empty `inner`.
 * @param {Rect} inner
 * @param {Rect} outer
 * @returns {number}
 */
export function containedFraction(inner, outer) {
  const a = area(inner);
  const inter = intersect(inner, outer);
  if (inter === null || a <= 0) return 0;
  return area(inter) / a;
}

/**
 * Rect with x0 <= x1 and y0 <= y1.
 * @param {Rect} r
 * @returns {Rect}
 */
export function normRect(r) {
  return [
    Math.min(r[0], r[2]),
    Math.min(r[1], r[3]),
    Math.max(r[0], r[2]),
    Math.max(r[1], r[3]),
  ];
}

/**
 * A page view: the unrotated page size in points, a clockwise rotation (PDF `/Rotate`, as
 * pdf.js renders it) and a scale in pixels per point.
 * @param {{width: number, height: number, rotation?: number, scale?: number}} opts
 * @returns {View}
 */
export function makeView({ width, height, rotation = 0, scale = 1 }) {
  const rot = ((Math.round(rotation) % 360) + 360) % 360;
  if (rot % 90 !== 0 || rotation !== Math.round(rotation)) {
    throw new RangeError(`rotation must be a multiple of 90, got ${rotation}`);
  }
  if (!(width > 0 && height > 0 && scale > 0)) {
    throw new RangeError('width, height and scale must be positive');
  }
  return Object.freeze({ width, height, rotation: /** @type {0|90|180|270} */ (rot), scale });
}

/**
 * Size of the rotated page in pixels (width and height swap at 90 and 270).
 * @param {View} view
 * @returns {{width: number, height: number}}
 */
export function viewSize(view) {
  const swap = view.rotation === 90 || view.rotation === 270;
  const w = swap ? view.height : view.width;
  const h = swap ? view.width : view.height;
  return { width: w * view.scale, height: h * view.scale };
}

/**
 * A length in points as pixels at the view's scale (rotation does not change lengths).
 * @param {number} pt
 * @param {View} view
 * @returns {number}
 */
export function ptToPx(pt, view) {
  return pt * view.scale;
}

/**
 * Inverse of {@link ptToPx}: a length in pixels as points.
 * @param {number} px
 * @param {View} view
 * @returns {number}
 */
export function pxToPt(px, view) {
  return px / view.scale;
}

// Page point -> rotated, unscaled screen point.
function pointToScreen(x, y, view) {
  switch (view.rotation) {
    case 90: return [view.height - y, x];
    case 180: return [view.width - x, view.height - y];
    case 270: return [y, view.width - x];
    default: return [x, y];
  }
}

// Inverse of pointToScreen.
function pointToPage(sx, sy, view) {
  switch (view.rotation) {
    case 90: return [sy, view.height - sx];
    case 180: return [view.width - sx, view.height - sy];
    case 270: return [view.width - sy, sx];
    default: return [sx, sy];
  }
}

/**
 * Page rect (points) to the screen box (pixels) it occupies in the rotated, scaled view.
 * @param {Rect} rect
 * @param {View} view
 * @returns {Box}
 */
export function toScreen(rect, view) {
  const [ax, ay] = pointToScreen(rect[0], rect[1], view);
  const [bx, by] = pointToScreen(rect[2], rect[3], view);
  const s = view.scale;
  return {
    left: Math.min(ax, bx) * s,
    top: Math.min(ay, by) * s,
    width: Math.abs(bx - ax) * s,
    height: Math.abs(by - ay) * s,
  };
}

/**
 * Inverse of {@link toScreen}: a screen box (pixels) back to a page rect (points).
 * @param {Box} box
 * @param {View} view
 * @returns {Rect}
 */
export function toPage(box, view) {
  const s = view.scale;
  const [ax, ay] = pointToPage(box.left / s, box.top / s, view);
  const [bx, by] = pointToPage((box.left + box.width) / s, (box.top + box.height) / s, view);
  return normRect([ax, ay, bx, by]);
}

/**
 * Page rect (top-left origin, relative to the crop box) to PDF user space (bottom-left
 * origin). Independent of page rotation: the rect is in unrotated crop-box space.
 * @param {Rect} rect
 * @param {Rect} cropBox PDF user space `[x0, y0, x1, y1]`, bottom-left origin
 * @returns {Rect}
 */
export function toUserSpace(rect, cropBox) {
  const [x0, y0, x1, y1] = normRect(rect);
  return [cropBox[0] + x0, cropBox[3] - y1, cropBox[0] + x1, cropBox[3] - y0];
}

/**
 * Inverse of {@link toUserSpace}: a PDF user-space rect (bottom-left origin) back to a page rect
 * (top-left origin, relative to the crop box).
 * @param {Rect} rect PDF user space
 * @param {Rect} cropBox PDF user space `[x0, y0, x1, y1]`, bottom-left origin
 * @returns {Rect}
 */
export function fromUserSpace(rect, cropBox) {
  const [x0, y0, x1, y1] = normRect(rect);
  return [x0 - cropBox[0], cropBox[3] - y1, x1 - cropBox[0], cropBox[3] - y0];
}
