// Browser fill: one record into a PDF as real, editable form fields, with pdf-lib. The browser
// counterpart of src/formcast/render/fill.py (fill design §4, review-apps spec §8.5).
//
// Orchestrates: check the template's transforms, open the PDF and check its page count, resolve
// values (core/values.js), build one target per field and per table-cell slot, bind targets to
// widgets (bind.js), fit and write (fit.js, write.js), then report (report.js). Output always keeps
// real form fields.
//
// Python is the reference: fill.test.js holds this module to fixtures recorded from the library's
// fill (bindings, widget names, values, warning codes). The report leaves out what the browser
// cannot produce: `font_substituted` and `adopted` (MuPDF-side notes), `invalid` for the record (no
// JSON Schema validator), `shared_field_conflict`, and the fingerprint check (no MuPDF to compute
// one), so `fingerprint_ok` is always true.

import { PDFDocument } from "../../vendor/pdf-lib/pdf-lib.esm.min.js";
import { hasKey, pyFormatG, pyRepr } from "../pycompat.js";
import { flattenSchema } from "../schema.js";
import { validateChain } from "../transforms.js";
import { makeWarning, resolve } from "../values.js";
import { bind, cellKey } from "./bind.js";
import { fitText, makeMeasure } from "./fit.js";
import {
  buildReport, cellId, columnPath, compareKeys, isChoice, makeSlot, orderKey, slotDone,
} from "./report.js";
import {
  optionValue, readExisting, registerFonts, showsOn, textFlags, textFontFor, widgetRotation, writeChoice,
  writeChoiceMark, writeText, writerContext,
} from "./write.js";

export const FINGERPRINT_ERROR = "the browser cannot check fingerprints; pass checkFingerprint: false";

const STYLE_DEFAULTS = { font_size: "auto", min_font_size: 6, align: "left", comb: null, multiline: false };

/**
 * Write `data` into `pdfBytes` as real form fields.
 *
 * Throws when the PDF can't be opened or is encrypted, its page count differs from the template's,
 * the template uses an unknown transform (or bad transform arguments), or `checkFingerprint` is set
 * for a template that has a fingerprint (the browser cannot compute one). Every other problem is a
 * report entry.
 * @param {object} template  the plain JSON of a template
 * @param {Uint8Array} pdfBytes
 * @param {any} data  one record
 * @param {{checkFingerprint?: boolean}} [opts]
 * @returns {Promise<{pdfBytes: Uint8Array, report: object}>}
 */
export async function fill(template, pdfBytes, data, { checkFingerprint = false } = {}) {
  return run(template, pdfBytes, data, { checkFingerprint, smart: false });
}

/**
 * `pdfBytes` with an empty form field for every template field the PDF doesn't already have. The
 * form's own widgets are left as they are.
 * @param {object} template
 * @param {Uint8Array} pdfBytes
 * @returns {Promise<Uint8Array>}
 */
export async function smartPdf(template, pdfBytes) {
  return (await run(template, pdfBytes, null, { checkFingerprint: false, smart: true })).pdfBytes;
}

// ---------------------------------------------------------------- pipeline

async function openPdf(pdfBytes) {
  let doc;
  try {
    doc = await PDFDocument.load(pdfBytes, { updateMetadata: false });
  } catch (e) {
    // pdf-lib's EncryptedPDFError fails instanceof (an ES5 Error subclass); its message is stable
    throw new Error(/is encrypted/.test(String(e?.message)) ? "encrypted PDF" : "cannot open PDF");
  }
  if (doc.getPageCount() === 0) throw new Error("cannot open PDF");
  return doc;
}

async function run(template, pdfBytes, data, { checkFingerprint, smart }) {
  checkTransforms(template);
  const doc = await openPdf(pdfBytes);
  const pageCount = template.form.page_count;
  if (doc.getPageCount() !== pageCount) {
    throw new Error(`page count mismatch: template has ${pageCount} pages, PDF has ${doc.getPageCount()}`);
  }
  if (checkFingerprint && template.form.fingerprint !== null && template.form.fingerprint !== undefined) {
    throw new Error(FINGERPRINT_ERROR);
  }
  const resolved = smart ? { fields: {}, tables: {}, warnings: [] } : resolve(template, data);
  const ctx = await writerContext(doc);
  const slots = makeSlots(template);
  const bindings = bind(slots.map((s) => s.target), readExisting(ctx));
  for (const s of slots) {
    s.binding = bindings.get(s.target.key);
    // `adopted` is a MuPDF-side note the browser report leaves out
    s.warnings.push(...s.binding.warnings.filter((w) => w.code !== "adopted").map((w) => located(w, s)));
  }
  const cells = new Map();
  for (const t of Object.values(resolved.tables)) for (const c of t.cells) cells.set(cellId(c.table_id, c.row, c.key), c);
  writeAll(ctx, slots, resolved.fields, cells, smart);
  settleChoiceStates(slots);
  registerFonts(ctx);
  const out = await doc.save({ updateFieldAppearances: false });
  if (smart) return { pdfBytes: out, report: null };
  const report = buildReport(template, data, { record: resolved.warnings, slots, fields: resolved.fields, tables: resolved.tables, cells });
  return { pdfBytes: out, report };
}

/** Throw on the first unknown transform or bad transform arguments in the template. */
function checkTransforms(template) {
  for (const f of template.fields) {
    const chains = [];
    if (f.source) {
      chains.push(["transforms", f.source.transforms ?? []]);
      if (f.source.when !== undefined && f.source.when !== null) chains.push(["when", [f.source.when]]);
    }
    for (const seg of f.segments ?? []) {
      for (const c of seg.columns) chains.push([`column ${pyRepr(c.key)} transforms`, c.transforms ?? []]);
    }
    for (const [where, chain] of chains) {
      const errors = validateChain(chain);
      if (errors.length) throw new Error(`field ${pyRepr(f.id)} ${where}: ${errors[0]}`);
    }
  }
}

function target(key, fieldId, fieldType, page, rect, existingWidget) {
  return { key, field_id: fieldId, field_type: fieldType, page, rect, existing_widget: existingWidget ?? null };
}

/** One slot per field with a box, and one per table-cell slot (every row of every segment). */
function makeSlots(template) {
  const out = [];
  const keys = new Set();
  const itemTypes = new Map(flattenSchema(template.schema).map((sp) => [sp.path, sp.item_type]));
  for (const f of template.fields) {
    const mapped = Boolean(f.source && f.source.paths.length);
    if (f.type !== "table") {
      const path = mapped && f.source.paths.length === 1 ? f.source.paths[0] : null;
      out.push(makeSlot(target(f.id, f.id, f.type, f.page, f.rect, f.existing_widget), f, { mapped, path }));
      continue;
    }
    const tablePath = mapped ? f.source.paths[0] : null;
    const itemType = itemTypes.get(tablePath) ?? null;
    const listRows = itemType !== null && itemType !== "object";
    let base = 0;
    f.segments.forEach((seg, k) => {
      for (let r = 0; r < seg.max_rows; r++) {
        const y = seg.row_top + r * seg.row_height;
        for (const col of seg.columns) {
          const key = cellKey(f.id, base + r, col.key);
          if (keys.has(key)) continue; // a repeated column key (ops reject it) would bind twice
          keys.add(key);
          const rect = [col.x[0], y, col.x[1], y + seg.row_height];
          const path = tablePath ? columnPath(tablePath, col.key, { listRows }) : null;
          out.push(makeSlot(target(key, f.id, "text", seg.page, rect, null), f, {
            mapped, path, column: col, segment: k, row: base + r, listRow: listRows,
          }));
        }
      }
      base += seg.max_rows;
    });
  }
  out.forEach((s, i) => {
    s.order = orderKey(s.target, i);
  });
  return out;
}

/** `w` with the slot's field id and data path filled in where missing. */
function located(w, s) {
  if (w.field !== null && (w.path !== null || s.path === null)) return w;
  return { ...w, field: w.field ?? s.field.id, path: w.path ?? s.path };
}

// ---------------------------------------------------------------- writing

/** What a mapped slot should show: text (null to clear) or on/off. */
function slotValue(s, fields, cells) {
  if (s.column !== null) return cells.get(cellId(s.field.id, s.row, s.column.key))?.text ?? null;
  const r = fields[s.field.id];
  return r.kind === "choice" ? Boolean(r.on) : r.text;
}

/** Write every slot fill owns; clears and "off"s first, so a later clear never wipes a value. */
function writeAll(ctx, slots, fields, cells, smart) {
  const plan = [];
  for (const s of slots) {
    const b = s.binding;
    if (b.how === "none" || b.warnings.some((w) => w.code === "not_writable")) continue;
    if (smart || !s.mapped) {
      if (b.how === "created") plan.push([0, s, isChoice(s) ? false : null]); // the form's own widgets stay untouched
      continue;
    }
    const value = slotValue(s, fields, cells);
    plan.push([value ? 1 : 0, s, value]);
  }
  plan.sort((a, b) => a[0] - b[0] || compareKeys(a[1].order, b[1].order));
  const measures = Object.fromEntries(Object.entries(ctx.fonts).map(([name, font]) => [name, makeMeasure(font)]));
  for (const [, s, value] of plan) {
    if (isChoice(s)) writeChoiceSlot(ctx, s, Boolean(value));
    else writeTextSlot(ctx, measures, s, typeof value === "string" ? value : null);
  }
}

function writeChoiceSlot(ctx, s, on) {
  const args = { page: s.target.page, on, rect: s.target.rect };
  const res = s.field.type === "choice_mark" ? writeChoiceMark(ctx, s.binding, args) : writeChoice(ctx, s.binding, args);
  slotDone(s, res.name, res.rect, on, null, res.warnings.map((w) => located(w, s)));
}

/** The field's style with the library's defaults (an unset minimum follows a smaller size). */
function effectiveStyle(raw) {
  const style = { ...STYLE_DEFAULTS, ...(raw ?? {}) };
  if (style.font_size !== "auto" && !hasKey(raw ?? {}, "min_font_size") && style.font_size < style.min_font_size) {
    style.min_font_size = style.font_size;
  }
  return style;
}

function cellLines(s) {
  const wrap = s.column !== null ? s.column.wrap ?? null : null;
  return wrap !== null && wrap > 1 ? wrap : null;
}

function writeTextSlot(ctx, measures, s, text) {
  const b = s.binding;
  const explicit = new Set(Object.keys(s.field.style ?? {}));
  let style = effectiveStyle(s.field.style);
  const lines = cellLines(s);
  if (b.how === "created" && lines !== null && !style.multiline) style = { ...style, multiline: true };
  if (text) text = optionValue(b, text); // a combo/list holds (and shows) the export value
  const fit = text ? fitSlot(ctx, measures, s, text, style, explicit, lines) : null;
  const res = writeText(ctx, b, { page: s.target.page, text, fit, style, explicit, rect: s.target.rect });
  const written = (fit !== null ? fit.text : text) || null;
  const page = b.how === "created" ? s.target.page : b.widget.page;
  const fitWarns = fit !== null ? fitWarnings(fit, s, page, res.rect) : [];
  slotDone(s, res.name, res.rect, written, fit !== null ? fit.fontSize : null, [...fitWarns, ...res.warnings.map((w) => located(w, s))]);
}

/** Fit `text` with the font, box and limits the written widget will really have. */
function fitSlot(ctx, measures, s, text, style, explicit, lines) {
  const b = s.binding;
  let rect;
  let multiline;
  let maxLen = null;
  let comb;
  if (b.how === "created") {
    rect = s.target.rect;
    multiline = style.multiline;
    comb = style.comb;
  } else {
    const flags = textFlags(b);
    rect = b.widget.rect;
    multiline = explicit.has("multiline") ? style.multiline : flags.multiline; // library Ruling K
    maxLen = b.widget.max_len;
    comb = flags.comb && maxLen ? maxLen : null;
    if (!multiline) lines = null; // a single-line widget draws one line whatever the column's wrap
  }
  const rotation = widgetRotation(ctx, s.target.page, b);
  if (rotation === 90 || rotation === 270) {
    const [x0, y0, x1, y1] = rect; // the text runs along the rect's height
    rect = [x0, y0, x0 + (y1 - y0), y0 + (x1 - x0)];
  }
  return fitText(text, rect, {
    fontSize: style.font_size, minFontSize: style.min_font_size, measure: measures[textFontFor(b)],
    multiline: Boolean(multiline) || lines !== null, maxLines: lines, maxLen, comb,
  });
}

function fitWarnings(fit, s, page, rect) {
  const where = { field: s.field.id, path: s.path, page, rect };
  const out = [];
  if (fit.truncated) {
    out.push(makeWarning("truncated", `value cut to ${Array.from(fit.text).length} characters, the box's limit`, where));
  }
  if (fit.status === "shrunk") {
    out.push(makeWarning("shrunk", `text shrunk to ${pyFormatG(fit.fontSize)} pt to fit`, where));
  } else if (fit.status === "overflow") {
    out.push(makeWarning("overflow",
      `text doesn't fit even at ${pyFormatG(fit.fontSize)} pt; the full value is in the field, but the end ` +
      "will be cut off when printed or flattened", where));
  }
  if (fit.unencodable) {
    out.push(makeWarning("unencodable",
      `characters ${pyRepr(fit.unencodable)} are outside the standard PDF fonts; the value is stored but ` +
      "may not display", where));
  }
  return out;
}

/** Report what each existing button really shows after every write (its /AS). */
function settleChoiceStates(slots) {
  for (const s of slots) {
    if (s.written && isChoice(s) && s.binding.widget !== null) s.value = showsOn(s.binding);
  }
}
