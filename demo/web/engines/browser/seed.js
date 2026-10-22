// The starting template for an open PDF, built as the library's
// `new_template(schema, form_map, candidates=True)` builds it: one field per existing widget, then
// one per detector candidate on pages without widgets. Pure (no pdf.js), so Node can test it.

import { pyFixed, pyIsSpace } from "../../core/pycompat.js";

/** Template field type for each widget kind; push buttons get no field (template/model.py). */
const WIDGET_FIELD_TYPE = Object.freeze({
  text: "text",
  combo: "text",
  list: "text",
  checkbox: "checkbox",
  radio: "radio_option",
  signature: "signature",
});

/** Template field type for each detector class (template/model.py `_CANDIDATE_FIELD_TYPE`). */
const CANDIDATE_FIELD_TYPE = Object.freeze({ text: "text", choice: "checkbox", signature: "signature" });

/** Longest seed label, in characters (template/model.py `MAX_SEED_LABEL`). */
export const MAX_SEED_LABEL = 80;
/** A label to the left of a box may end at most this far (points) from its left edge. */
export const LABEL_LEFT_REACH = 200;
/** A label above a box may end at most this far (points) above its top edge. */
export const LABEL_ABOVE_REACH = 30;
// Boxes whose tops differ by less than this share of the page height are one row (pagemap/detect.py).
const ROW_BUCKET = 0.01;
// A line may poke this far (points) into the box and still count as left of or above it.
const TOUCH = 2;

/**
 * @typedef {[number, number, number, number]} Rect
 * @typedef {{kind: "text" | "choice" | "signature", rect: Rect, conf: number}} Candidate
 * @typedef {{text: string, rect: Rect}} TextLine
 */

function defaultStyle() {
  return { font_size: "auto", min_font_size: 6.0, align: "left", comb: null, multiline: false };
}

function field(id, type, label, page, rect, existingWidget, confidence, origin) {
  return {
    id,
    type,
    label,
    page,
    rect: [...rect],
    source: null,
    style: defaultStyle(),
    existing_widget: existingWidget,
    segments: [],
    confidence,
    origin,
  };
}

/** Python's `" ".join(s.split())[:n]`. */
function squeeze(s, n) {
  const words = [];
  let word = "";
  for (const ch of s) {
    if (pyIsSpace(ch)) {
      if (word) words.push(word);
      word = "";
    } else {
      word += ch;
    }
  }
  if (word) words.push(word);
  return [...words.join(" ")].slice(0, n).join("");
}

/**
 * Candidates in reading order, as `pagemap/detect.py` numbers them: sorted by top edge (as a share
 * of the page height, rounded to 3 places) then left edge, grouped into rows whose tops are within
 * 1% of the page height of the row's first box, each row left to right.
 * @template {{rect: Rect}} T
 * @param {T[]} cands
 * @param {number} pageHeight
 * @returns {T[]}
 */
export function readingOrder(cands, pageHeight) {
  const key = (c) => Number(pyFixed(c.rect[1] / pageHeight, 3));
  const ordered = cands
    .map((c, i) => ({ c, i }))
    .sort((a, b) => key(a.c) - key(b.c) || a.c.rect[0] - b.c.rect[0] || a.i - b.i)
    .map(({ c }) => c);
  const rows = [];
  for (const c of ordered) {
    const row = rows.at(-1);
    if (row && Math.abs(c.rect[1] - row[0].rect[1]) / pageHeight < ROW_BUCKET) row.push(c);
    else rows.push([c]);
  }
  return rows.flatMap((row) => row.map((c, i) => ({ c, i })).sort((a, b) => a.c.rect[0] - b.c.rect[0] || a.i - b.i))
    .map(({ c }) => c);
}

/**
 * The seed label for a box: the closest text line ending within 200 pt to its left on the same row
 * (vertical overlap of at least half the shorter height), else the closest line ending within 30 pt
 * above it that overlaps it horizontally, else "". Whitespace collapsed, at most 80 characters.
 * A simple heuristic; it does not try to match the library's page map.
 * @param {Rect} rect
 * @param {TextLine[]} lines
 * @returns {string}
 */
export function nearestLabel(rect, lines) {
  const [x0, y0, x1, y1] = rect;
  let best = null;
  let bestGap = Infinity;
  for (const line of lines) {
    const [lx0, ly0, lx1, ly1] = line.rect;
    const overlap = Math.min(y1, ly1) - Math.max(y0, ly0);
    if (overlap < 0.5 * Math.min(y1 - y0, ly1 - ly0) || lx1 > x0 + TOUCH) continue;
    const gap = x0 - lx1;
    if (gap <= LABEL_LEFT_REACH && gap < bestGap) {
      best = line;
      bestGap = gap;
    }
  }
  if (best === null) {
    for (const line of lines) {
      const [lx0, ly0, lx1, ly1] = line.rect;
      if (ly1 > y0 + TOUCH || ly0 >= y0 || lx1 <= x0 || lx0 >= x1) continue;
      const gap = y0 - ly1;
      if (gap <= LABEL_ABOVE_REACH && gap < bestGap) {
        best = line;
        bestGap = gap;
      }
    }
  }
  return best === null ? "" : squeeze(best.text, MAX_SEED_LABEL);
}

/**
 * The starting template for `doc`: widget fields `w<page>_<i>` (i = the widget's index on its page)
 * in reading order (top, then left), with `existing_widget` and the widget's tooltip as label; then
 * detector fields `c<page>_<n>` in reading order on pages that have no widgets, typed from their
 * class (choice -> checkbox), labelled from `lines`, with their confidence. No fingerprint: the
 * browser cannot compute the library's.
 * @param {{pageCount: number, pages: {index: number, width: number, height: number, kind: string,
 *   widgets: {name: string, kind: string, rect: Rect, tooltip?: string | null}[]}[]}} doc a BrowserDoc
 * @param {object} schema
 * @param {Record<number, Candidate[]>} [detections] by page index
 * @param {Record<number, TextLine[]>} [lines] by page index
 * @returns {object} the plain JSON of a template
 */
export function seedTemplate(doc, schema, detections = {}, lines = {}) {
  const fields = [];
  const pages = [...doc.pages].sort((a, b) => a.index - b.index);
  for (const page of pages) {
    const widgets = page.widgets
      .map((w, i) => ({ w, i }))
      .sort((a, b) => a.w.rect[1] - b.w.rect[1] || a.w.rect[0] - b.w.rect[0] || a.i - b.i);
    for (const { w, i } of widgets) {
      const type = WIDGET_FIELD_TYPE[w.kind];
      if (type === undefined) continue;
      fields.push(field(`w${page.index}_${i}`, type, w.tooltip || "", page.index, w.rect, w.name, null, "widget"));
    }
    if (page.widgets.length) continue;
    const cands = readingOrder(detections[page.index] ?? [], page.height);
    const pageLines = lines[page.index] ?? [];
    cands.forEach((c, n) => {
      const label = nearestLabel(c.rect, pageLines);
      const type = CANDIDATE_FIELD_TYPE[c.kind];
      fields.push(field(`c${page.index}_${n}`, type, label, page.index, c.rect, null, c.conf, "detector"));
    });
  }
  return {
    formcast_version: "1",
    template_id: crypto.randomUUID().replaceAll("-", ""),
    schema: structuredClone(schema),
    form: {
      fingerprint: null,
      page_count: doc.pageCount,
      page_sizes: pages.map((p) => [p.width, p.height]),
      kinds: pages.map((p) => p.kind),
    },
    fields,
    history: [],
  };
}
