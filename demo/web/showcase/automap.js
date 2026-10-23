// Auto map: the AI agent's mapping for a sample, carried onto the boxes on screen by position.
// Each sample bundle holds the template the agent built for that form in the dev eval; its fields
// sit on the form's own blanks, so a detected box that overlaps one of them (same page, IoU of at
// least 0.3, the eval's match threshold) takes that field's source and style. Pairs are made best
// overlap first and one to one; boxes left over stay for linking by hand. Every change goes
// through ops.js, so a mapping the template's checks would refuse is skipped, not forced; the
// history entries it adds carry the author "agent", since the mapping is the agent's. Pure (no
// DOM), so Node can test it.

import { iou } from "../core/geometry.js";
import { OpError, applyOp, contextFromTemplate } from "../core/ops.js";

/** IoU at or above which a box and an agent field are the same blank (the eval's match threshold). */
export const MATCH_IOU = 0.3;

/** The note on every history entry Auto map adds. */
export const NOTE = "Auto map: the AI agent's mapping from the eval, paired with this box by position";

/**
 * Pair rects of `a` with rects of `b` overlapping with IoU >= `threshold`, best overlap first, each
 * rect in at most one pair. Ties keep the order of `a`, then of `b`.
 * @param {number[][]} a rects
 * @param {number[][]} b rects
 * @param {number} threshold the IoU a pair needs (required: callers differ, 0.3 here, 0.5 for "found")
 * @returns {[number, number][]} index pairs [i in a, j in b], best overlap first
 */
export function pairByOverlap(a, b, threshold) {
  if (typeof threshold !== "number" || !(threshold > 0)) throw new TypeError("pairByOverlap needs an IoU threshold above 0");
  const pairs = [];
  a.forEach((ra, i) => {
    b.forEach((rb, j) => {
      const v = iou(ra, rb);
      if (v >= threshold) pairs.push([v, i, j]);
    });
  });
  pairs.sort((p, q) => q[0] - p[0]);
  const usedA = new Set();
  const usedB = new Set();
  const out = [];
  for (const [, i, j] of pairs) {
    if (usedA.has(i) || usedB.has(j)) continue;
    usedA.add(i);
    usedB.add(j);
    out.push([i, j]);
  }
  return out;
}

/** A field drawn as one box on one page (not a table). */
const isBox = (f) => f.type !== "table" && Number.isInteger(f.page) && Array.isArray(f.rect);

/**
 * The template with the agent's mapping carried onto its boxes. A box paired with an agent field
 * takes the field's source (paths, transforms, join, when) and style, and its type when the two
 * differ (a choice the agent marks with `when` needs a choice box); it keeps its own rect, id and
 * label. A box already linked is overwritten. The input is not changed.
 * @param {object} template the template on screen
 * @param {object} agent the agent's template for the same form
 * @param {{threshold?: number}} [opts]
 * @returns {{template: object, mapped: string[], boxes: number}} `mapped`: ids of the boxes that
 *   took a mapping, in template order; `boxes`: how many boxes the template has
 */
export function carryMapping(template, agent, { threshold = MATCH_IOU } = {}) {
  const boxes = template.fields.filter(isBox);
  const linked = agent.fields.filter((f) => isBox(f) && (f.source?.paths ?? []).length > 0);
  const pairs = new Map(); // box id -> agent field
  const pages = new Set(boxes.map((f) => f.page));
  for (const page of pages) {
    const mine = boxes.filter((f) => f.page === page);
    const theirs = linked.filter((f) => f.page === page);
    for (const [i, j] of pairByOverlap(mine.map((f) => f.rect), theirs.map((f) => f.rect), threshold)) {
      pairs.set(mine[i].id, theirs[j]);
    }
  }
  let t = structuredClone(template);
  const mapped = [];
  const start = t.history.length;
  for (const box of boxes) {
    const from = pairs.get(box.id);
    if (from === undefined) continue;
    const ops = [];
    if (from.type !== box.type) ops.push({ op: "set_type", id: box.id, type: from.type });
    ops.push({ op: "set_source", id: box.id, source: structuredClone(from.source) });
    ops.push({ op: "set_style", id: box.id, style: structuredClone(from.style) });
    try {
      let next = t;
      for (const op of ops) {
        // the browser's ops.js applies edits only as "human" (an agent's own guards need the
        // library); the entries are relabelled below
        next = applyOp(next, op, { author: "human", note: NOTE, context: contextFromTemplate(next) }).template;
      }
      t = next;
      mapped.push(box.id);
    } catch (e) {
      if (!(e instanceof OpError)) throw e;
      // refused by the template's checks: the box stays as it was, for linking by hand
    }
  }
  // the mapping is the agent's, so its history entries say so ("agent" is a template author)
  for (const entry of t.history.slice(start)) entry.author = "agent";
  return { template: t, mapped, boxes: boxes.length };
}
