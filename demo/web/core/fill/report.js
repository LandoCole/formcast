// Assemble the fill report from what the browser fill resolved, bound and wrote: a port of
// src/formcast/render/assemble.py (fill design §5.7), including the post-write check that every
// record value shows somewhere or is explained by a warning (`not_shown`, library Ruling V).
//
// The report has Python's FillReport JSON shape (template_id, fingerprint_ok, strict, fields,
// tables, paths, gaps, warnings, counts) plus `writer: "browser"`.

import { isDict, pyRepr, pyTruthy } from "../pycompat.js";
import { ARRAY_ITEM, PathError, flattenSchema, formatPath, isArrayItem, parsePath } from "../schema.js";
import { TransformError } from "../transforms.js";
import { cellValue, choiceOn, hasValueAt, isMissing, lookup, makeWarning, pyInt, rowProblem } from "../values.js";

export const CHOICE_TYPES = ["checkbox", "radio_option", "choice_mark"];

/** Codes that already say why a record value isn't visible; `not_shown` adds nothing to them. */
const EXPLAINS_HIDDEN = new Set([
  "unmapped_data", "missing", "transform_error", "widget_not_found", "type_mismatch", "not_writable",
  "shared_field_conflict", "rows_dropped", "invalid",
]);

/**
 * One bound target (a field, or one table-cell slot) and what was written to it.
 * @typedef {object} Slot
 * @property {import("./bind.js").Target} target
 * @property {object} field  the template field; the table field for a cell
 * @property {boolean} mapped
 * @property {string|null} path  data path for warnings
 * @property {object|null} column
 * @property {number|null} segment
 * @property {number|null} row
 * @property {boolean} list_row
 * @property {number[]} order
 * @property {import("./bind.js").Binding|null} binding
 * @property {boolean} written
 * @property {string|boolean|null} value
 * @property {number|null} font_size
 * @property {string|null} widget_name
 * @property {number[]|null} widget_rect
 * @property {object[]} warnings
 */

/**
 * A new slot.
 * @returns {Slot}
 */
export function makeSlot(target, field, { mapped, path, column = null, segment = null, row = null, listRow = false }) {
  return {
    target, field, mapped, path, column, segment, row, list_row: listRow, order: [],
    binding: null, written: false, value: null, font_size: null, widget_name: null, widget_rect: null, warnings: [],
  };
}

/** Whether the slot's field is a choice box. */
export function isChoice(slot) {
  return CHOICE_TYPES.includes(slot.field.type);
}

/** Record what was written to `slot`. */
export function slotDone(slot, name, rect, value, fontSize, warnings) {
  slot.written = true;
  slot.widget_name = name;
  slot.widget_rect = rect;
  slot.value = value;
  slot.font_size = fontSize;
  slot.warnings.push(...warnings);
}

/** The widget's name and rect: as written, else the bound existing widget's. */
function slotWidget(s) {
  if (s.written) return [s.widget_name, s.widget_rect];
  const w = s.binding?.widget ?? null;
  return w !== null ? [w.name, w.rect] : [null, null];
}

/** Sort key of a target: page, top, left, input position. */
export function orderKey(target, index) {
  return [target.page, target.rect[1], target.rect[0], index];
}

/** Lexicographic comparison of equal-length number arrays. */
export function compareKeys(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

/**
 * Schema path of a table column: `medications` + `dose` -> `medications[].dose`; the columns of a
 * list-row table all live at the item path `rows[]`.
 * @param {string} tablePath
 * @param {string} key
 * @param {{listRows?: boolean}} [opts]
 * @returns {string}
 */
export function columnPath(tablePath, key, { listRows = false } = {}) {
  try {
    return formatPath([...parsePath(tablePath), ARRAY_ITEM, ...(listRows ? [] : parsePath(key))]);
  } catch (e) {
    if (!(e instanceof PathError)) throw e;
    return listRows ? `${tablePath}[]` : `${tablePath}[].${key}`;
  }
}

function sortKey(w) {
  return [w.page === null ? -1 : w.page, w.rect ? w.rect[1] : -1, w.rect ? w.rect[0] : -1];
}

function sortWarnings(warnings) {
  return [...warnings].sort((a, b) => compareKeys(sortKey(a), sortKey(b)));
}

/** Every warning sorted by position, with `missing` kept once per path. */
function topLevel(warnings) {
  const seen = new Set();
  const out = [];
  for (const w of sortWarnings(warnings)) {
    if (w.code === "missing" && w.path !== null) {
      if (seen.has(w.path)) continue;
      seen.add(w.path);
    }
    out.push(w);
  }
  return out;
}

function fieldReport(f, s, r) {
  const [widget, rect] = slotWidget(s);
  return {
    id: f.id, type: f.type, page: f.page ?? null, paths: s.mapped ? [...f.source.paths] : [],
    binding: s.binding.how, widget, widget_rect: rect, value: s.value, font_size: s.font_size,
    warnings: [...(r ? r.warnings : []), ...s.warnings],
  };
}

function tableReport(f, slots, tr, cells) {
  const cellReports = slots.map((s) => {
    const [widget, rect] = slotWidget(s);
    const c = cells.get(cellId(f.id, s.row, s.column.key));
    // resolve() words a cell's path as "<table>[].<key>"; use the schema path the slot carries
    const resolved = (c ? c.warnings : []).map((w) => (w.path === s.path ? w : { ...w, path: s.path }));
    return {
      row: s.row, key: s.column.key, segment: s.segment, page: s.target.page, rect: s.target.rect,
      binding: s.binding.how, widget, widget_rect: rect, value: s.value, font_size: s.font_size,
      warnings: [...resolved, ...s.warnings],
    };
  });
  const perSegment = tr ? [...tr.rows_per_segment] : f.segments.map(() => 0);
  return {
    id: f.id, path: tr ? tr.path : "", rows_provided: tr ? tr.rows_provided : 0,
    rows_written: perSegment.reduce((a, b) => a + b, 0), rows_dropped: tr ? tr.rows_dropped : 0,
    rows_per_segment: perSegment, cells: cellReports, warnings: tr ? [...tr.warnings] : [],
  };
}

/** Key of a resolved table cell in the `cells` map. */
export function cellId(tableId, row, key) {
  return JSON.stringify([tableId, row, key]);
}

/** A box that came out empty: no source, or mapped but nothing visible was written. */
function isGap(f, slots, r) {
  if (!f.source || !f.source.paths.length || !slots.length) return true;
  if (f.type === "table") return !slots.some((s) => s.value);
  const s = slots[0];
  if (isChoice(s)) return !r || !r.has_value || !s.written;
  return !s.value;
}

/** A field joining several paths words its warnings without a path; they belong to each one. */
function multiPaths(template) {
  const out = new Map();
  for (const f of template.fields) {
    if (f.type !== "table" && f.source && f.source.paths.length > 1) out.set(f.id, [...new Set(f.source.paths)]);
  }
  return out;
}

function warningPaths(w, multi) {
  return w.path !== null ? [w.path] : multi.get(w.field ?? "") ?? [];
}

function pathReports(template, data, slotsByField, cells, warnings) {
  const fieldsAt = new Map();
  const rendered = new Map();
  const at = (map, key, make) => {
    if (!map.has(key)) map.set(key, make());
    return map.get(key);
  };
  for (const f of template.fields) {
    const slots = slotsByField.get(f.id) ?? [];
    if (!slots.length || !slots[0].mapped) continue;
    if (f.type !== "table") {
      for (const p of new Set(f.source.paths)) {
        at(fieldsAt, p, () => []).push(f.id);
        at(rendered, p, () => ({}))[f.id] = slots[0].value;
      }
      continue;
    }
    at(fieldsAt, f.source.paths[0], () => []).push(f.id);
    for (const s of slots) {
      const list = at(fieldsAt, s.path, () => []);
      if (!list.includes(f.id)) list.push(f.id);
      if (cells.has(cellId(f.id, s.row, s.column.key))) {
        const key = s.list_row ? `${f.id}[${s.row}][${s.column.key}]` : `${f.id}[${s.row}]`;
        at(rendered, s.path, () => ({}))[key] = s.value;
      }
    }
  }
  const multi = multiPaths(template);
  const byPath = new Map();
  for (const w of warnings) for (const p of warningPaths(w, multi)) at(byPath, p, () => []).push(w);
  const out = {};
  for (const sp of flattenSchema(template.schema)) {
    out[sp.path] = {
      path: sp.path, mapped: fieldsAt.has(sp.path), has_value: hasValueAt(data, sp.path),
      fields: [...(fieldsAt.get(sp.path) ?? [])], rendered: { ...(rendered.get(sp.path) ?? {}) },
      warnings: byPath.get(sp.path) ?? [],
    };
  }
  return out;
}

/**
 * Build the report.
 * @param {object} template
 * @param {any} data
 * @param {{record: object[], slots: Slot[], fields: object, tables: object, cells: Map<string, object>}} parts
 * @returns {object} the FillReport JSON
 */
export function buildReport(template, data, { record, slots, fields, tables, cells }) {
  const slotsByField = new Map();
  for (const s of slots) {
    if (!slotsByField.has(s.field.id)) slotsByField.set(s.field.id, []);
    slotsByField.get(s.field.id).push(s);
  }
  const fieldReports = [];
  const tableReports = [];
  const gaps = [];
  const requiredGaps = [];
  for (const f of template.fields) {
    const fs = slotsByField.get(f.id) ?? [];
    if (f.type === "table") tableReports.push(tableReport(f, fs, tables[f.id] ?? null, cells));
    else fieldReports.push(fieldReport(f, fs[0], fields[f.id] ?? null));
    if (isGap(f, fs, fields[f.id] ?? null)) {
      gaps.push(f.id);
      if (f.required) requiredGaps.push(f.id);
    }
  }

  let every = [...record, ...fieldReports.flatMap((fr) => fr.warnings), ...tableReports.flatMap((tr) => tr.warnings)];
  every.push(...notShown(template, data, slotsByField, tableReports, every));
  every.push(...tableReports.flatMap((tr) => tr.cells.flatMap((c) => c.warnings)));
  every = sortWarnings(every);
  const top = topLevel(every);

  const written = fieldReports.filter((fr) => fr.value).length;
  const counts = {};
  for (const w of top) counts[w.code] = (counts[w.code] ?? 0) + 1;
  counts.fields_written = written;
  counts.fields_empty = fieldReports.length - written;
  counts.cells_written = tableReports.reduce((n, tr) => n + tr.cells.filter((c) => c.value).length, 0);
  if (requiredGaps.length) counts.required_left_empty = requiredGaps.length;
  return {
    template_id: template.template_id ?? "",
    fingerprint_ok: true,
    strict: false,
    fields: fieldReports,
    tables: tableReports,
    paths: pathReports(template, data, slotsByField, cells, every),
    gaps,
    required_gaps: requiredGaps,
    warnings: top,
    counts,
    writer: "browser",
  };
}

// ---------------------------------------------------------------- Ruling V: nothing visible

/**
 * `not_shown` for every record value no written widget ended up showing, unless a warning already
 * explains it. Falsy values (false, 0) are exempt. A table cell's warning also goes on its cell.
 */
function notShown(template, data, slotsByField, tableReports, warnings) {
  if (!isDict(data)) return [];
  const multi = multiPaths(template);
  const explained = new Set();
  for (const w of warnings) if (EXPLAINS_HIDDEN.has(w.code)) for (const p of warningPaths(w, multi)) explained.add(p);
  const schemaPaths = flattenSchema(template.schema);
  const byPath = new Map(schemaPaths.map((sp) => [sp.path, sp]));
  const mapped = template.fields.filter((f) => f.source && f.source.paths.length);
  const tables = new Map(mapped.filter((f) => f.type === "table").map((f) => [f.source.paths[0], f]));
  const plain = mapped.filter((f) => f.type !== "table");
  const out = [];
  for (const sp of schemaPaths) {
    const segs = parsePath(sp.path);
    if (sp.type === "object" || sp.in_table !== null || isArrayItem(segs[segs.length - 1]) || tables.has(sp.path)) continue;
    if (explained.has(sp.path)) continue;
    const value = lookup(data, sp.path);
    if (isMissing(value) || !pyTruthy(value)) continue;
    for (const [item, inList] of hidden(sp.path, segs, value, plain, slotsByField, data)) {
      const what = inList ? `${pyRepr(item)} in ${pyRepr(sp.path)}` : `${pyRepr(item)} at ${pyRepr(sp.path)}`;
      out.push(makeWarning("not_shown", `the record has ${what} but no box on the form shows it`, { path: sp.path }));
    }
  }
  const reports = new Map(tableReports.map((tr) => [tr.id, tr]));
  for (const [path, f] of tables) {
    out.push(...hiddenCells(f, path, byPath.get(path) ?? null, data, slotsByField.get(f.id) ?? [], reports.get(f.id), explained));
  }
  return out;
}

function isPrefix(pre, segs) {
  let p;
  try {
    p = parsePath(pre);
  } catch (e) {
    if (e instanceof PathError) return false;
    throw e;
  }
  return p.length <= segs.length && p.every((x, i) => String(x) === String(segs[i]));
}

/** The parts of `value` (itself, or each list item) no written widget shows. */
function hidden(path, segs, value, plain, slotsByField, data) {
  const covering = plain.filter((f) => f.source.paths.some((q) => isPrefix(q, segs)));
  if (covering.some((f) => !CHOICE_TYPES.includes(f.type) && slotsByField.get(f.id)[0].value)) return [];
  const boxesOn = covering.filter((f) => CHOICE_TYPES.includes(f.type) && slotsByField.get(f.id)[0].value === true);
  if (!Array.isArray(value)) return boxesOn.length ? [] : [[value, false]];
  const out = [];
  for (const item of value) {
    if (isMissing(item) || !pyTruthy(item)) continue;
    if (!boxesOn.some((f) => represents(f, path, item, data))) out.push([item, true]);
  }
  return out;
}

/** Whether box `f` would be on if the list at `path` held only `item` (it stands for it). */
function represents(f, path, item, data) {
  if (!f.source.paths.includes(path)) return true; // mapped through a parent path
  const values = f.source.paths.map((q) => {
    if (q === path) return [item];
    const v = lookup(data, q);
    return isMissing(v) ? null : v;
  });
  try {
    return choiceOn(f.source, values);
  } catch (e) {
    if (e instanceof TransformError) return false;
    throw e;
  }
}

/** `not_shown` per written row's value that its cell doesn't show (dropped rows: rows_dropped). */
function hiddenCells(f, path, sp, data, slots, tr, explained) {
  const rows = lookup(data, path);
  if (!Array.isArray(rows)) return [];
  const itemType = sp !== null ? sp.item_type : null;
  const byCell = new Map(slots.map((s, i) => [JSON.stringify([s.row, s.column.key]), [s, tr.cells[i]]]));
  const keys = [...new Set(f.segments.flatMap((seg) => seg.columns.map((c) => c.key)))];
  const out = [];
  for (let i = 0; i < Math.min(tr.rows_written, rows.length); i++) {
    const row = rows[i];
    if (rowProblem(row, itemType) !== null) continue;
    let entries;
    if (isDict(row)) {
      entries = keys.map((k) => [k, cellValue(row, k)]);
    } else {
      const indexKeys = new Map();
      for (const k of keys) {
        const n = pyInt(k);
        if (n !== null && !indexKeys.has(n)) indexKeys.set(n, k);
      }
      entries = row.map((v, j) => [indexKeys.get(j) ?? null, v]);
    }
    for (const [key, value] of entries) {
      if (isMissing(value) || !pyTruthy(value)) continue;
      const cp = columnPath(path, key ?? "", { listRows: !isDict(row) });
      const [slot, cell] = byCell.get(JSON.stringify([i, key])) ?? [null, null];
      if (explained.has(cp) || (slot !== null && slot.value)) continue;
      if (cell !== null && cell.warnings.some((w) => EXPLAINS_HIDDEN.has(w.code))) continue;
      const where = key !== null ? `column ${pyRepr(key)}` : "a position no column covers";
      const w = makeWarning("not_shown", `${path} row ${i}: the record has ${pyRepr(value)} in ${where} but its cell shows nothing`, {
        path: cp, field: f.id, page: cell ? cell.page : null, rect: cell ? cell.rect : null,
      });
      if (cell !== null) cell.warnings.push(w);
      else out.push(w);
    }
  }
  return out;
}
