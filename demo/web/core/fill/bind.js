// Bind template fields and table cells to PDF widgets: a port of src/formcast/render/binding.py
// (fill design §5.3). Pure logic over widget descriptors; no PDF access.
//
// Three outcomes per target, tried in order: a named existing widget, an adopted unclaimed widget
// under the target, or a new widget with a deterministic unique name. The browser keeps the
// library's rules, including the `adopted` note; fill/index.js leaves that note out of the report,
// as the browser omits codes it does not own.

import { iou } from "../geometry.js";
import { makeWarning } from "../values.js";
import { pyRepr } from "../pycompat.js";

/** Which template field types each widget kind can hold (template/model.py WIDGET_FIELD_TYPES). */
export const WIDGET_FIELD_TYPES = Object.freeze({
  text: ["text", "signature"],
  combo: ["text"],
  list: ["text"],
  checkbox: ["checkbox", "radio_option"],
  radio: ["radio_option", "checkbox"],
  signature: ["signature"],
  button: [],
});

/** A choice_mark re-adopts its own earlier widget at this overlap or better. */
export const MARK_REFILL_IOU = 0.5;

export const SIGNATURE_NOT_WRITABLE =
  "the form's own signature field can only be signed in a PDF viewer; value not written";

/**
 * @typedef {object} ExistingWidget
 * @property {number} page
 * @property {number} index  position among the page's widget annotations
 * @property {string} name  fully qualified field name
 * @property {string} kind  text, checkbox, radio, combo, list, signature or button
 * @property {number[]} rect  top-left origin, crop-box space
 * @property {string|null} on_state
 * @property {number|null} max_len
 * @property {string[]} options
 * @property {object} [ref]  the writer's handle on the widget (opaque here)
 *
 * @typedef {object} Target
 * @property {string} key  field id, or cellKey(table, row, column) for a table cell
 * @property {string} field_id
 * @property {string} field_type  template type; table cells are "text"
 * @property {number} page
 * @property {number[]} rect
 * @property {string|null} existing_widget
 *
 * @typedef {object} Binding
 * @property {string} key
 * @property {"named"|"adopted"|"created"|"none"} how
 * @property {ExistingWidget|null} widget
 * @property {string|null} new_name
 * @property {object[]} warnings
 */

const CELL_KEY = /^([\s\S]*?)\[(\d+)\]\.([\s\S]*)$/;
const PATH_PUNCT = /_*[.[\]]+_*/g;

/**
 * Target key of a table cell.
 * @param {string} tableId
 * @param {number} row
 * @param {string} colKey
 * @returns {string}
 */
export function cellKey(tableId, row, colKey) {
  return `${tableId}[${row}].${colKey}`;
}

/**
 * Readable PDF field name for a target key: cells become `<table>_r<row>_<col>`; `.`, `[` and `]`
 * (hierarchy separators in an AcroForm partial name) become `_`.
 * @param {string} key
 * @returns {string}
 */
export function widgetNameFor(key) {
  const m = CELL_KEY.exec(key);
  const name = m ? `${m[1]}_r${m[2]}_${m[3]}` : key;
  return name.replace(PATH_PUNCT, "_").replace(/_+$/, "") || "_";
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The names fill gives (or gave, on an earlier fill) the widget it creates for `key`. */
function ownName(key) {
  return new RegExp(`^${escapeRegExp(widgetNameFor(key))}(_\\d+)?$`);
}

function centre(r) {
  return [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2];
}

function centreDist(a, b) {
  const [ax, ay] = centre(a);
  const [bx, by] = centre(b);
  return Math.hypot(ax - bx, ay - by);
}

function warn(code, message, t) {
  return makeWarning(code, message, { field: t.field_id, page: t.page, rect: t.rect });
}

function backs(widget, fieldType) {
  return (WIDGET_FIELD_TYPES[widget.kind] ?? []).includes(fieldType);
}

function binding(key, how, { widget = null, newName = null, warnings = [] } = {}) {
  return { key, how, widget, new_name: newName, warnings };
}

function withSignatureNote(b, t) {
  if (b.widget !== null && b.widget.kind === "signature") b.warnings.push(warn("not_writable", SIGNATURE_NOT_WRITABLE, t));
  return b;
}

/** Lexicographic comparison of equal-length number arrays. */
function cmp(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

function bindNamed(t, widgets) {
  const same = widgets.filter((w) => w.page === t.page && w.name === t.existing_widget);
  const notFound = (msg) => binding(t.key, "none", { warnings: [warn("widget_not_found", msg, t)] });
  if (!same.length) return notFound(`no widget named ${pyRepr(t.existing_widget)} on page ${t.page}; field left empty`);
  let pick = same[0];
  if (same.length > 1) {
    let best = null;
    same.forEach((w, i) => {
      const score = [iou(w.rect, t.rect), -centreDist(w.rect, t.rect), -i];
      if (best === null || cmp(score, best.score) > 0) best = { score, w };
    });
    if (best.score[0] <= 0) {
      return notFound(`no widget named ${pyRepr(t.existing_widget)} overlaps the field's box on page ${t.page}; field left empty`);
    }
    pick = best.w;
  }
  if (!backs(pick, t.field_type)) {
    const msg = `widget ${pyRepr(pick.name)} is a ${pick.kind} widget and cannot hold a ${t.field_type} field; skipped`;
    return binding(t.key, "none", { warnings: [warn("type_mismatch", msg, t)] });
  }
  return withSignatureNote(binding(t.key, "named", { widget: pick }), t);
}

function comparePending(a, b) {
  if (a.page !== b.page) return a.page - b.page;
  if (a.rect[1] !== b.rect[1]) return a.rect[1] - b.rect[1];
  if (a.rect[0] !== b.rect[0]) return a.rect[0] - b.rect[0];
  return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
}

/**
 * Decide, for every target, which widget it writes to.
 * @param {Target[]} targets
 * @param {ExistingWidget[]} existing
 * @returns {Map<string, Binding>}
 */
export function bind(targets, existing) {
  const out = new Map();
  // Rule 1: named. A target's widget name claims every widget of that name, so adoption never
  // grabs a sibling kid of a radio/checkbox group some field already names.
  const claimedNames = new Set();
  for (const t of targets) {
    if (t.field_type === "choice_mark" || t.existing_widget === null) continue; // a mark is always created
    claimedNames.add(t.existing_widget);
    out.set(t.key, bindNamed(t, existing));
  }

  // Rule 2: adopt (a choice_mark only re-adopts its own earlier mark), in page / top / left / key order.
  const slot = (w) => `${w.page}/${w.index}`;
  const claimed = new Set(existing.filter((w) => claimedNames.has(w.name)).map(slot));
  const pending = targets.filter((t) => !out.has(t.key) && (t.field_type === "choice_mark" || t.existing_widget === null));
  for (const t of [...pending].sort(comparePending)) {
    const mark = t.field_type === "choice_mark";
    const own = ownName(t.key);
    let best = null;
    for (const w of existing) {
      if (w.page !== t.page || claimed.has(slot(w))) continue;
      if (mark) {
        if (w.kind !== "checkbox" || !own.test(w.name) || iou(w.rect, t.rect) < MARK_REFILL_IOU) continue;
      } else {
        if (!backs(w, t.field_type)) continue;
        const [cx, cy] = centre(w.rect);
        if (!(t.rect[0] <= cx && cx <= t.rect[2] && t.rect[1] <= cy && cy <= t.rect[3])) continue;
      }
      const score = [iou(w.rect, t.rect), -w.index];
      if (best === null || cmp(score, best.score) > 0) best = { score, w };
    }
    if (best === null) continue;
    const w = best.w;
    claimed.add(slot(w));
    const b = binding(t.key, "adopted", { widget: w });
    if (!own.test(w.name)) b.warnings.push(warn("adopted", `adopted existing ${w.kind} widget ${pyRepr(w.name)}`, t));
    out.set(t.key, withSignatureNote(b, t));
  }

  // Rule 3: create the rest, in input order, with unique deterministic names.
  const taken = new Set(existing.map((w) => w.name));
  for (const t of targets) {
    if (out.has(t.key)) continue;
    const base = widgetNameFor(t.key);
    let name = base;
    for (let n = 2; taken.has(name); n++) name = `${base}_${n}`;
    taken.add(name);
    out.set(t.key, binding(t.key, "created", { newName: name }));
  }
  return out;
}
