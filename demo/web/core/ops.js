// Edit operations for the browser: the nine ops a person can make, validated the way
// src/formcast/template/ops.py validates them, with Python's error messages and Python's
// history entries, so a template edited here replays identically in Python.
//
// A template is the plain JSON of a Python `Template` (`model_dump(by_alias=True)`). Everything
// here is pure: `applyOp` returns a new template and never touches its input. Rects are
// [x0, y0, x1, y1] in PDF points, origin top-left.
//
// Not ported (the browser has no use for them): the table-building ops, `link_widget`, and the
// guards that only apply to an agent author (seeded fields can't be moved or removed by an agent,
// and an agent can't change whether a box is required once the person has set it).
// Python's lax input coercion is kept for numbers, integers and booleans given as strings, so the
// same op data gives the same answer.

import { containedFraction, iou } from "./geometry.js";
import {
  decimalDigitValue,
  getCloseMatches,
  hasKey,
  isDict,
  pyFixed,
  pyFloatRepr,
  pyFloorDiv,
  pyFormatG,
  pyRepr,
  pyRound1,
} from "./pycompat.js";
import { WIDGET_FIELD_TYPES } from "./fill/bind.js";
import { flattenSchema } from "./schema.js";
import { validateChain } from "./transforms.js";

/** The ops the browser can apply. */
export const BROWSER_OPS = [
  "add_field",
  "remove_field",
  "move",
  "resize",
  "set_label",
  "set_type",
  "set_source",
  "set_style",
  "set_required",
];

/** Every op the Python library knows, in its order (the "unknown op" message lists them). */
export const OP_NAMES = [
  ...BROWSER_OPS,
  "add_table",
  "set_table_columns",
  "set_table_rows",
  "add_table_segment",
  "link_widget",
];

/** An op was rejected. `message` (also `reason`) is Python's text. */
export class OpError extends Error {
  /**
   * @param {object} op the op that was rejected
   * @param {string} reason
   */
  constructor(op, reason) {
    super(reason);
    this.name = "OpError";
    this.op = op;
    this.reason = reason;
  }
}

const FIELD_TYPES = ["text", "checkbox", "radio_option", "signature", "choice_mark", "table"];
const ALIGNS = ["left", "center", "right"];
const CHOICE_TYPES = ["checkbox", "radio_option", "choice_mark"];
const EDGE_TOLERANCE = 2.0;
const MIN_SIZE = 2.0;
const OVERLAP_WARN_IOU = 0.5;
const CELL_COVER_SHARE = 0.7;
const REPLACE_IOU = 0.6;
const MAX_WARN_IDS = 20;
const ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const GENERATED_ID = /^f(\p{Nd}+)$/u;
// Ops after which the touched field is checked for overlap and checkbox-source warnings.
const WARN_OPS = new Set(["add_field", "move", "resize", "set_type", "set_source"]);
const RECT_HINT = "a rect is 4 numbers [x0, y0, x1, y1] in PDF points, origin top-left";

// ---------------------------------------------------------------- input validation
//
// Python reads an op into pydantic models first; a malformed op is reported as
// "invalid <op> op: <loc>: <message> (got <value>)". The small parsers below give the same
// locations and messages for the shapes an op can have.

const FAIL = Symbol("fail");

function fail(errors, loc, type, msg, input) {
  errors.push({ loc, type, msg, input });
  return FAIL;
}

function parseInt_(v, loc, errors) {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v;
    if (Number.isFinite(v)) {
      return fail(errors, loc, "int_from_float", "Input should be a valid integer, got a number with a fractional part", v);
    }
    return fail(errors, loc, "finite_number", "Input should be a finite number", v);
  }
  if (typeof v === "string") {
    if (/^\s*[+-]?\d+\s*$/.test(v)) return Number(v.trim());
    return fail(errors, loc, "int_parsing", "Input should be a valid integer, unable to parse string as an integer", v);
  }
  return fail(errors, loc, "int_type", "Input should be a valid integer", v);
}

function parseFloat_(v, loc, errors) {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const s = v.trim();
    if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s)) return Number(s);
    if (/^[+-]?inf(inity)?$/i.test(s)) return s.startsWith("-") ? -Infinity : Infinity;
    if (/^[+-]?nan$/i.test(s)) return NaN;
    return fail(errors, loc, "float_parsing", "Input should be a valid number, unable to parse string as a number", v);
  }
  return fail(errors, loc, "float_type", "Input should be a valid number", v);
}

function parseStr(v, loc, errors) {
  if (typeof v === "string") return v;
  return fail(errors, loc, "string_type", "Input should be a valid string", v);
}

function parseBool(v, loc, errors) {
  const msg = "Input should be a valid boolean, unable to interpret input";
  if (typeof v === "boolean") return v;
  if ((typeof v === "number" && (v === 0 || v === 1))) return v === 1;
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    if (["0", "off", "f", "false", "n", "no"].includes(s)) return false;
    if (["1", "on", "t", "true", "y", "yes"].includes(s)) return true;
    return fail(errors, loc, "bool_parsing", msg, v);
  }
  if (typeof v === "number") return fail(errors, loc, "bool_parsing", msg, v);
  return fail(errors, loc, "bool_type", "Input should be a valid boolean", v);
}

function literalOf(values) {
  const quoted = values.map(pyRepr);
  const list = quoted.length > 1 ? `${quoted.slice(0, -1).join(", ")} or ${quoted[quoted.length - 1]}` : quoted[0];
  return (v, loc, errors) => (values.includes(v) ? v : fail(errors, loc, "literal_error", `Input should be ${list}`, v));
}

function nullable(parse) {
  return (v, loc, errors) => (v === null ? null : parse(v, loc, errors));
}

function listOf(parse) {
  return (v, loc, errors) => {
    if (!Array.isArray(v)) return fail(errors, loc, "list_type", "Input should be a valid list", v);
    const before = errors.length;
    const out = v.map((item, i) => parse(item, [...loc, i], errors));
    return errors.length > before ? FAIL : out;
  };
}

function parseRect(v, loc, errors) {
  if (!Array.isArray(v)) return fail(errors, loc, "tuple_type", "Input should be a valid tuple", v);
  const before = errors.length;
  const out = v.slice(0, 4).map((item, i) => parseFloat_(item, [...loc, i], errors));
  if (v.length > 4) {
    fail(errors, loc, "too_long", `Tuple should have at most 4 items after validation, not ${v.length}`, v);
  }
  for (let i = v.length; i < 4; i++) fail(errors, [...loc, i], "missing", "Field required", undefined);
  return errors.length > before ? FAIL : out;
}

function parseArgs(v, loc, errors) {
  if (!isDict(v)) return fail(errors, loc, "dict_type", "Input should be a valid dictionary", v);
  return structuredClone(v);
}

/**
 * Read `value` as the model `name` with `fields` (`{key, parse, required, default}`), the way
 * pydantic does: errors for every bad field, in field order, then the extra keys. Returns
 * `{full, given}` (`given` holds only the keys the input set) or FAIL.
 */
function parseModel(name, fields, value, loc, errors, { forbidExtra = false, after = null } = {}) {
  if (!isDict(value)) {
    return fail(errors, loc, "model_type", `Input should be a valid dictionary or instance of ${name}`, value);
  }
  const before = errors.length;
  const full = {};
  const given = {};
  for (const f of fields) {
    if (hasKey(value, f.key)) {
      const parsed = f.parse(value[f.key], [...loc, f.key], errors);
      if (parsed !== FAIL) {
        full[f.key] = f.fullOf ? f.fullOf(parsed) : parsed;
        given[f.key] = f.givenOf ? f.givenOf(parsed) : parsed;
      }
    } else if (f.required) {
      fail(errors, [...loc, f.key], "missing", "Field required", undefined);
    } else {
      full[f.key] = structuredClone(f.default);
    }
  }
  if (forbidExtra) {
    const known = new Set(fields.map((f) => f.key));
    for (const key of Object.keys(value)) {
      if (!known.has(key)) fail(errors, [...loc, key], "extra_forbidden", "Extra inputs are not permitted", value[key]);
    }
  }
  if (errors.length > before) return FAIL;
  if (after) after(full, given, value, loc, errors);
  return errors.length > before ? FAIL : { full, given };
}

/** A model parser's `{full, given}` result carried through list/nullable wrappers. */
const fullPart = (r) => (r === null ? null : r.full);
const givenPart = (r) => (r === null ? null : r.given);

function parseTransform(v, loc, errors) {
  return parseModel(
    "Transform",
    [
      { key: "op", parse: parseStr, required: true },
      { key: "args", parse: parseArgs, default: {} },
    ],
    v,
    loc,
    errors,
    { forbidExtra: true },
  );
}

function parseTransforms(v, loc, errors) {
  const r = listOf(parseTransform)(v, loc, errors);
  return r === FAIL ? FAIL : { full: r.map(fullPart), given: r.map(givenPart) };
}

function parseSource(v, loc, errors) {
  return parseModel(
    "Source",
    [
      { key: "paths", parse: listOf(parseStr), required: true },
      {
        key: "transforms",
        parse: parseTransforms,
        default: [],
        fullOf: (r) => r.full,
        givenOf: (r) => r.given,
      },
      { key: "join", parse: nullable(parseStr), default: null },
      {
        key: "when",
        parse: nullable(parseTransform),
        default: null,
        fullOf: fullPart,
        givenOf: givenPart,
      },
    ],
    v,
    loc,
    errors,
  );
}

function parseFontSize(v, loc, errors) {
  if (v === "auto") return v;
  const sub = [];
  const n = parseFloat_(v, [...loc, "constrained-float"], sub);
  if (n !== FAIL) {
    if (!Number.isFinite(n)) fail(sub, [...loc, "constrained-float"], "finite_number", "Input should be a finite number", v);
    else if (!(n > 0)) fail(sub, [...loc, "constrained-float"], "greater_than", "Input should be greater than 0", v);
    else return n;
  }
  errors.push(...sub);
  return fail(errors, [...loc, "literal['auto']"], "literal_error", "Input should be 'auto'", v);
}

function parseSize(v, loc, errors) {
  const n = parseFloat_(v, loc, errors);
  if (n === FAIL) return FAIL;
  if (!Number.isFinite(n)) return fail(errors, loc, "finite_number", "Input should be a finite number", v);
  if (!(n > 0)) return fail(errors, loc, "greater_than", "Input should be greater than 0", v);
  return n;
}

const SIZE_MESSAGE = (min, size) =>
  `min_font_size (${pyFloatRepr(min)}) can't exceed font_size (${pyFloatRepr(size)}); ` +
  "lower min_font_size too, or use font_size='auto'";

function parseStyle(v, loc, errors) {
  return parseModel(
    "Style",
    [
      { key: "font_size", parse: parseFontSize, default: "auto" },
      { key: "min_font_size", parse: parseSize, default: 6 },
      { key: "align", parse: literalOf(ALIGNS), default: "left" },
      { key: "comb", parse: nullable(parseInt_), default: null },
      { key: "multiline", parse: parseBool, default: false },
    ],
    v,
    loc,
    errors,
    {
      after: (f, g, input) => {
        if (f.font_size === "auto" || f.min_font_size <= f.font_size) return;
        if (hasKey(g, "min_font_size")) {
          fail(errors, loc, "value_error", SIZE_MESSAGE(f.min_font_size, f.font_size), input);
        } else {
          // an unset (default) minimum follows a smaller explicit size, and stays "unset"
          f.min_font_size = f.font_size;
        }
      },
    },
  );
}

const ID = { key: "id", parse: parseStr, required: true };
const OP_SPECS = {
  add_field: [
    { key: "id", parse: nullable(parseStr), default: null },
    { key: "type", parse: literalOf(FIELD_TYPES), required: true },
    { key: "page", parse: parseInt_, required: true },
    { key: "rect", parse: parseRect, required: true },
    { key: "label", parse: parseStr, default: "" },
    { key: "source", parse: nullable(parseSource), default: null, fullOf: fullPart, givenOf: givenPart },
    { key: "style", parse: nullable(parseStyle), default: null, fullOf: fullPart, givenOf: givenPart },
  ],
  remove_field: [ID],
  move: [
    ID,
    { key: "rect", parse: nullable(parseRect), default: null },
    { key: "dx", parse: nullable(parseFloat_), default: null },
    { key: "dy", parse: nullable(parseFloat_), default: null },
    { key: "segment", parse: nullable(parseInt_), default: null },
  ],
  resize: [
    ID,
    { key: "rect", parse: parseRect, required: true },
    { key: "segment", parse: nullable(parseInt_), default: null },
  ],
  set_label: [ID, { key: "label", parse: parseStr, required: true }],
  set_type: [ID, { key: "type", parse: literalOf(FIELD_TYPES), required: true }],
  set_source: [
    ID,
    { key: "source", parse: nullable(parseSource), required: true, fullOf: fullPart, givenOf: givenPart },
  ],
  set_style: [ID, { key: "style", parse: parseStyle, required: true, fullOf: fullPart, givenOf: givenPart }],
  set_required: [
    ID,
    { key: "required", parse: parseBool, required: true },
    { key: "reason", parse: parseStr, default: "" },
  ],
};

/** Python's `_describe`: pydantic errors as "loc: message (got value); ...". */
function describe(errors, name) {
  const parts = [];
  let rectError = false;
  for (const err of errors) {
    let loc = [...err.loc];
    if (loc.length && loc[0] === name) loc = loc.slice(1);
    rectError = rectError || loc.includes("rect");
    let msg = err.msg;
    if (err.type !== "missing" && err.type !== "extra_forbidden" && loc.length) {
      const got = pyRepr(err.input);
      const points = Array.from(got);
      msg += ` (got ${points.length > 80 ? `${points.slice(0, 77).join("")}...` : got})`;
    }
    parts.push(loc.length ? `${loc.join(".")}: ${msg}` : msg);
  }
  if (rectError) parts.push(RECT_HINT);
  return parts.join("; ");
}

/** Read an op like Python's `_parse`; returns `{name, parsed, set}` or throws OpError. */
function parseOp(op) {
  if (!isDict(op)) {
    throw new OpError({}, `an op must be a dict like {'op': 'move', ...} or an op model, got ${pyTypeOf(op)}`);
  }
  const name = op.op;
  if (typeof name !== "string" || !OP_NAMES.includes(name)) {
    throw new OpError({ ...op }, `unknown op ${pyRepr(name)}; valid ops are: ${OP_NAMES.join(", ")}`);
  }
  if (!BROWSER_OPS.includes(name)) {
    throw new OpError({ ...op }, `op ${pyRepr(name)} is not available in the browser; the browser edits with: ${BROWSER_OPS.join(", ")}`);
  }
  const errors = [];
  const fields = OP_SPECS[name];
  const parsed = {};
  const set = new Set();
  const loc = [name];
  for (const f of fields) {
    if (hasKey(op, f.key)) {
      const r = f.parse(op[f.key], [...loc, f.key], errors);
      if (r !== FAIL) {
        parsed[f.key] = f.fullOf ? f.fullOf(r) : r;
        set.add(f.key);
        if (f.givenOf) parsed[`${f.key}$given`] = f.givenOf(r);
      }
    } else if (f.required) {
      fail(errors, [...loc, f.key], "missing", "Field required", undefined);
    } else {
      parsed[f.key] = structuredClone(f.default);
    }
  }
  const known = new Set(["op", ...fields.map((f) => f.key)]);
  for (const key of Object.keys(op)) {
    if (!known.has(key)) fail(errors, [...loc, key], "extra_forbidden", "Extra inputs are not permitted", op[key]);
  }
  if (!errors.length && name === "move") {
    const offset = parsed.dx !== null || parsed.dy !== null;
    if (parsed.rect === null && !offset) {
      fail(errors, loc, "value_error", "move needs either rect=[x0, y0, x1, y1] or a dx/dy offset", op);
    } else if (parsed.rect !== null && offset) {
      fail(errors, loc, "value_error", "move takes rect or dx/dy, not both", op);
    }
  }
  if (errors.length) throw new OpError({ ...op }, `invalid ${name} op: ${describe(errors, name)}`);
  return { name, parsed, set, fields };
}

function pyTypeOf(v) {
  if (v === null || v === undefined) return "NoneType";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (typeof v === "string") return "str";
  return Array.isArray(v) ? "list" : "dict";
}

/** What Python stores in history: `{"op": name, ...keys the op set}` with values as Python read them. */
function historyRecord({ name, parsed, set, fields }) {
  const record = { op: name };
  for (const f of fields) {
    if (!set.has(f.key)) continue;
    record[f.key] = f.givenOf ? structuredClone(parsed[`${f.key}$given`]) : structuredClone(parsed[f.key]);
  }
  return record;
}

// ---------------------------------------------------------------- formatting

const fmtRect = (rect) => `[${rect.map(pyFormatG).join(", ")}]`;
const floatList = (rect) => `[${rect.map(pyFloatRepr).join(", ")}]`;
const reprList = (items) => `[${items.map(pyRepr).join(", ")}]`;

function idsText(ids) {
  const shown = ids.slice(0, MAX_WARN_IDS).map(pyRepr).join(", ");
  return shown + (ids.length > MAX_WARN_IDS ? ` and ${ids.length - MAX_WARN_IDS} more` : "");
}

function columnPath(tablePath, key) {
  return `${tablePath}[]${key.startsWith("[") ? "" : "."}${key}`;
}

// ---------------------------------------------------------------- template helpers

/**
 * Every cell of `table`: each segment's rows by its columns. A cell linked through `Column.fields`
 * takes the linked field's page and rect; the others are computed from the segment.
 */
function tableCells(template, table) {
  const byId = new Map(template.fields.map((f) => [f.id, f]));
  const out = [];
  table.segments.forEach((seg, si) => {
    for (let row = 0; row < seg.max_rows; row++) {
      const top = seg.row_top + row * seg.row_height;
      for (const col of seg.columns) {
        const fid = row < col.fields.length ? col.fields[row] : null;
        const linked = fid !== null ? byId.get(fid) : undefined;
        if (linked !== undefined && linked.rect !== null && linked.page !== null) {
          out.push({ page: linked.page, rect: linked.rect, segment: si, row, column: col, field: linked });
        } else {
          out.push({
            page: seg.page,
            rect: [col.x[0], top, col.x[1], top + seg.row_height],
            segment: si,
            row,
            column: col,
            field: null,
          });
        }
      }
    }
  });
  return out;
}

/** Field id -> {table, segment, row, key} for every field linked as a table cell (first link wins). */
function linkedCells(template) {
  const out = new Map();
  for (const f of template.fields) {
    f.segments.forEach((seg, si) => {
      for (const col of seg.columns) {
        col.fields.forEach((fid, row) => {
          if (fid !== null && !out.has(fid)) out.set(fid, { table: f.id, segment: si, row, key: col.key });
        });
      }
    });
  }
  return out;
}

/**
 * What ops are validated against: the template's schema paths and page sizes, and (when the PDF is
 * open) the kinds of its widgets by name, which limit the types a widget-backed field may take
 * (Python's `OpContext.widget_kinds`). Without `widgetKinds` any type may use any widget.
 * @param {object} template
 * @param {{widgetKinds?: Record<string, string[]> | null}} [opts] widget name -> kinds of every widget with that name
 * @returns {{schemaPaths: object[], pageSizes: number[][], widgetKinds: Record<string, string[]> | null}}
 */
export function contextFromTemplate(template, { widgetKinds = null } = {}) {
  return {
    schemaPaths: flattenSchema(template.schema),
    pageSizes: template.form.page_sizes.map((s) => [...s]),
    widgetKinds: widgetKinds === null ? null : structuredClone(widgetKinds),
  };
}

/**
 * The id Python gives a new field when the op has none: `f` and one more than the largest
 * number among existing ids of the form `f<digits>`.
 * @param {object} template
 * @returns {string}
 */
export function newFieldId(template) {
  let max = 0n;
  for (const f of template.fields) {
    const m = GENERATED_ID.exec(f.id);
    if (m === null) continue;
    let n = 0n;
    for (const ch of m[1]) n = n * 10n + BigInt(decimalDigitValue(ch));
    if (n > max) max = n;
  }
  return `f${max + 1n}`;
}

// ---------------------------------------------------------------- one op application

class Run {
  constructor(template, ctx, raw, author) {
    this.t = template;
    this.author = author;
    this.ctx = ctx;
    this.raw = raw;
    this.paths = new Map(ctx.schemaPaths.map((sp) => [sp.path, sp]));
  }

  fail(reason) {
    throw new OpError(this.raw, reason);
  }

  /** Fail when `fieldType` can't be backed by the kinds of the widget named `widget`. */
  checkWidgetKind(f, fieldType, widget) {
    const known = this.ctx.widgetKinds;
    const kinds = widget !== null && known && Object.hasOwn(known, widget) ? known[widget] : null;
    if (!kinds || !kinds.length) return;
    const allowed = [...new Set(kinds.flatMap((k) => WIDGET_FIELD_TYPES[k] ?? []))].sort();
    if (!allowed.includes(fieldType)) {
      this.fail(
        `field ${pyRepr(f.id)} is linked to widget ${pyRepr(widget)} (${[...new Set(kinds)].sort().join(", ")}); ` +
          `a ${fieldType} field can't use it - allowed types: ${allowed.length ? pyRepr(allowed) : "none"}`,
      );
    }
  }

  // -- lookups

  field(fid) {
    const found = this.t.fields.find((f) => f.id === fid);
    if (found !== undefined) return found;
    if (this.t.history.some((h) => h.op.op === "remove_field" && h.op.id === fid)) {
      this.fail(
        `unknown field id ${pyRepr(fid)}: it was already removed by an earlier remove_field ` +
          "(add_field makes a new one if the blank is needed)",
      );
    }
    const ids = this.t.fields.map((f) => f.id);
    if (!ids.length) this.fail(`unknown field id ${pyRepr(fid)}; the template has no fields yet`);
    const close = getCloseMatches(String(fid), ids, 5, 0.6);
    const hint = close.length ? `; similar ids: ${close.join(", ")}` : "";
    return this.fail(`unknown field id ${pyRepr(fid)}${hint}; the template has ${ids.length} field(s), listed in tpl`);
  }

  tableSegmentFor(f, index, verb) {
    if (index === null) {
      this.fail(
        `table field ${pyRepr(f.id)}: ${verb} with a rect needs segment=<index> (0..${f.segments.length - 1}) ` +
          "to say which segment's rect to set; or move by dx/dy to shift the whole table",
      );
    }
    return this.segment(f, index);
  }

  noSegment(f, index) {
    if (index !== null) {
      this.fail(`field ${pyRepr(f.id)} is a ${f.type} field; segment=${index} applies only to table fields`);
    }
  }

  segment(f, index) {
    if (!(index >= 0 && index < f.segments.length)) {
      this.fail(`table field ${pyRepr(f.id)} has no segment ${index}; valid segments are 0..${f.segments.length - 1}`);
    }
    return f.segments[index];
  }

  newId(wanted) {
    if (wanted === null) return newFieldId(this.t);
    if (!ID_PATTERN.test(wanted)) {
      this.fail(
        `invalid field id ${pyRepr(wanted)}: ids start with a letter and use only letters, digits, _ or - ` +
          "(at most 64 characters); or omit id to get a generated one",
      );
    }
    if (this.t.fields.some((f) => f.id === wanted)) {
      this.fail(`duplicate id ${pyRepr(wanted)}: a field with that id already exists; pick another id or omit it`);
    }
    return wanted;
  }

  schemaPath(fid, path) {
    const sp = this.paths.get(path);
    if (sp === undefined) {
      const close = getCloseMatches(path, [...this.paths.keys()], 3, 0.6);
      const hint = close.length
        ? `; did you mean ${close.map(pyRepr).join(", ")}?`
        : "; use a path from the schema path list";
      this.fail(`field ${pyRepr(fid)}: unknown schema path ${pyRepr(path)}${hint}`);
    }
    return sp;
  }

  // -- geometry

  checkRect(fid, page, rect, what = "rect") {
    const n = this.ctx.pageSizes.length;
    if (!(page >= 0 && page < n)) {
      this.fail(`field ${pyRepr(fid)}: page ${page} does not exist; valid pages are 0..${n - 1}`);
    }
    if (!rect.every(Number.isFinite)) {
      this.fail(`field ${pyRepr(fid)}: ${what} ${floatList(rect)} has non-finite coordinates`);
    }
    const [x0, y0, x1, y1] = rect;
    if (x1 - x0 < MIN_SIZE || y1 - y0 < MIN_SIZE) {
      this.fail(
        `field ${pyRepr(fid)}: ${what} ${fmtRect(rect)} is degenerate (width ${pyFormatG(x1 - x0)}, ` +
          `height ${pyFormatG(y1 - y0)}); rects are [x0, y0, x1, y1] with x1 - x0 and y1 - y0 each at least ` +
          `${pyFormatG(MIN_SIZE)}pt`,
      );
    }
    const [w, h] = this.ctx.pageSizes[page];
    const tol = EDGE_TOLERANCE;
    if (x0 < -tol || y0 < -tol || x1 > w + tol || y1 > h + tol) {
      this.fail(
        `field ${pyRepr(fid)}: ${what} ${fmtRect(rect)} is off page ${page}; the page is ${pyFormatG(w)} x ` +
          `${pyFormatG(h)}pt, so the rect must lie within [0, 0, ${pyFormatG(w)}, ${pyFormatG(h)}] ` +
          `(give or take ${pyFormatG(tol)}pt)`,
      );
    }
  }

  checkSegment(f, i) {
    const seg = f.segments[i];
    const where = `table field ${pyRepr(f.id)} segment ${i}`;
    this.checkRect(f.id, seg.page, seg.rect, `segment ${i} rect`);
    const [x0, y0, x1, y1] = seg.rect;
    for (const [name, value] of [
      ["row_top", seg.row_top],
      ["row_height", seg.row_height],
    ]) {
      if (!Number.isFinite(value)) this.fail(`${where}: ${name} ${pyFloatRepr(value)} is non-finite; give a number of PDF points`);
    }
    for (const col of seg.columns) {
      if (!col.x.every(Number.isFinite)) {
        this.fail(`${where}: column ${pyRepr(col.key)} x ${floatList(col.x)} has non-finite coordinates`);
      }
    }
    if (seg.row_height <= 0 || seg.max_rows < 1) {
      this.fail(
        `${where}: row_height must be > 0 and max_rows >= 1 ` +
          `(got row_height=${pyFormatG(seg.row_height)}, max_rows=${seg.max_rows})`,
      );
    }
    if (!(y0 - EDGE_TOLERANCE <= seg.row_top && seg.row_top < y1)) {
      this.fail(
        `${where}: row_top ${pyFormatG(seg.row_top)} is outside the segment rect's y range ` +
          `[${pyFormatG(y0)}, ${pyFormatG(y1)})`,
      );
    }
    const bottom = seg.row_top + seg.row_height * seg.max_rows;
    if (bottom > y1 + EDGE_TOLERANCE) {
      const fit = Math.trunc(pyFloorDiv(y1 + EDGE_TOLERANCE - seg.row_top, seg.row_height));
      this.fail(
        `${where}: ${seg.max_rows} rows of ${pyFormatG(seg.row_height)}pt from row_top ${pyFormatG(seg.row_top)} ` +
          `end at y=${pyFormatG(bottom)}, past the segment rect bottom ${pyFormatG(y1)}; at most ${fit} rows fit ` +
          "(lower max_rows or row_height, or enlarge the segment rect)",
      );
    }
    const seen = new Set();
    for (const col of seg.columns) {
      const [cx0, cx1] = col.x;
      if (seen.has(col.key)) this.fail(`${where}: column key ${pyRepr(col.key)} appears twice`);
      seen.add(col.key);
      if (cx1 - cx0 < MIN_SIZE || cx0 < x0 - EDGE_TOLERANCE || cx1 > x1 + EDGE_TOLERANCE) {
        this.fail(
          `${where}: column ${pyRepr(col.key)} x ${fmtRect(col.x)} must be at least ${pyFormatG(MIN_SIZE)}pt wide ` +
            `and inside the segment rect's x range [${pyFormatG(x0)}, ${pyFormatG(x1)}]`,
        );
      }
      this.checkChain(`${where} column ${pyRepr(col.key)} transforms`, col.transforms);
      this.checkColumnFields(f, i, col);
    }
  }

  checkColumnFields(f, i, col) {
    const seg = f.segments[i];
    const where = `table field ${pyRepr(f.id)} segment ${i} column ${pyRepr(col.key)}`;
    if (col.fields.length > seg.max_rows) {
      this.fail(
        `${where}: fields lists ${col.fields.length} ids but the segment has ${seg.max_rows} rows; ` +
          "give at most one field id (or None) per row",
      );
    }
    const tables = [...this.t.fields.filter((g) => g.type === "table" && g !== f), f];
    col.fields.forEach((fid, row) => {
      if (fid === null) return;
      const g = this.t.fields.find((x) => x.id === fid);
      if (g === undefined) {
        this.fail(
          `${where} row ${row}: unknown field id ${pyRepr(fid)}; fields links existing plain fields ` +
            "(ids in tpl), one per row, None where a row has none",
        );
      }
      if (g.type === "table") this.fail(`${where} row ${row}: ${pyRepr(fid)} is a table; a cell can only link a plain field`);
      const [x0, y0, x1, y1] = seg.rect;
      const cx = (g.rect[0] + g.rect[2]) / 2;
      const cy = (g.rect[1] + g.rect[3]) / 2;
      const tol = EDGE_TOLERANCE;
      if (g.page !== seg.page || !(x0 - tol <= cx && cx <= x1 + tol && y0 - tol <= cy && cy <= y1 + tol)) {
        this.fail(
          `${where} row ${row}: field ${pyRepr(fid)} (page ${g.page}, rect ${fmtRect(g.rect)}) is not inside the ` +
            `segment rect ${fmtRect(seg.rect)} on page ${seg.page}; link fields that sit in this segment`,
        );
      }
      if (g.source !== null && g.source.paths.length) {
        this.fail(
          `${where} row ${row}: field ${pyRepr(fid)} has its own source ${reprList(g.source.paths)}; a linked cell ` +
            `gets its value from the column, so set_source(${pyRepr(fid)}, None) first`,
        );
      }
      let uses = 0;
      for (const t of tables) for (const s of t.segments) for (const c of s.columns) uses += c.fields.filter((x) => x === fid).length;
      if (uses > 1) {
        this.fail(
          `${where} row ${row}: field ${pyRepr(fid)} is linked as a cell more than once; ` +
            "each field can be one cell of one table",
        );
      }
    });
  }

  /** How a field linked as a table cell is linked, for messages; null when it is not. */
  cellLink(fid) {
    const link = linkedCells(this.t).get(fid);
    if (link === undefined) return null;
    return `the cell of table ${pyRepr(link.table)} segment ${link.segment} column ${pyRepr(link.key)} row ${link.row}`;
  }

  // -- sources

  checkChain(where, chain) {
    const errors = validateChain(chain);
    if (errors.length) {
      this.fail(`${where}: invalid transform chain: ${errors.join("; ")}; list the valid transforms with transforms()`);
    }
  }

  checkSource(f) {
    const s = f.source;
    if (s === null) return;
    if (f.type !== "table") {
      const link = this.cellLink(f.id);
      if (link) {
        this.fail(
          `field ${pyRepr(f.id)} is ${link}, so its value comes from that column; set the column's ` +
            "transforms with set_table_columns instead, or unlink the field from the column first",
        );
      }
    }
    if (!s.paths.length) {
      this.fail(`field ${pyRepr(f.id)}: source.paths is empty; give at least one schema path, or source=None to unmap`);
    }
    const resolved = s.paths.map((p) => this.schemaPath(f.id, p));
    this.checkChain(`field ${pyRepr(f.id)} source.transforms`, s.transforms);
    if (s.when !== null) {
      this.checkChain(`field ${pyRepr(f.id)} source.when`, [s.when]);
      if (!CHOICE_TYPES.includes(f.type)) {
        this.fail(
          `field ${pyRepr(f.id)}: source.when is only allowed on choice fields (${CHOICE_TYPES.join(", ")}), ` +
            `but this is a ${f.type} field; drop when, or set_type to a choice type first`,
        );
      }
    }
    if (f.type === "table") {
      this.checkTableSource(f, resolved);
      return;
    }
    for (const sp of resolved) {
      if (sp.type === "object") {
        this.fail(`field ${pyRepr(f.id)}: schema path ${pyRepr(sp.path)} is an object; map one of its leaf paths instead`);
      }
      if (sp.type === "array" && sp.item_type === "object") {
        this.fail(
          `field ${pyRepr(f.id)}: schema path ${pyRepr(sp.path)} is a list of rows; map it with ` +
            `add_table(source_path=${pyRepr(sp.path)}, segments=[...]) instead`,
        );
      }
      if (sp.in_table) this.fail(this.columnOnPlainField(f, sp));
    }
  }

  columnOnPlainField(f, sp) {
    const table = sp.in_table;
    const key = sp.path.startsWith(`${table}[]`) ? sp.path.slice(table.length + 2).replace(/^\./, "") : sp.path;
    const reason =
      `field ${pyRepr(f.id)}: schema path ${pyRepr(sp.path)} is a column of table ${pyRepr(table)}; ` +
      `map it as a column key in add_table/set_table_columns for ${pyRepr(table)}`;
    if (f.rect === null) return reason;
    const [x0, y0, x1] = f.rect.map(pyRound1);
    return (
      `${reason}. For a cell at this field's rect: add_table(source_path=${pyRepr(table)}, segments=[{` +
      `'page': ${f.page}, 'rect': <the table area>, 'row_top': ${pyFormatG(y0)}, 'row_height': <distance ` +
      "between consecutive row tops>, 'max_rows': <rows>, 'columns': " +
      `[{'key': ${pyRepr(key)}, 'x': [${pyFormatG(x0)}, ${pyFormatG(x1)}], 'fields': [<one field id per row>]}, ...]}]); ` +
      "fm.grid([...the fields the table holds...]) gives row_top, row_height, the column x ranges " +
      "and column_fields. Link the plain fields as the cells (fields) and unmap them " +
      `(set_source(${pyRepr(f.id)}, None)) rather than removing them, so each cell keeps its box. If the ` +
      "records are printed as repeated blocks of lines: one table per block line, one segment per " +
      "record (max_rows=1)."
    );
  }

  checkTableSource(f, resolved) {
    if (resolved.length !== 1) {
      this.fail(
        `table field ${pyRepr(f.id)}: source needs exactly one array path, got ${reprList(resolved.map((sp) => sp.path))}`,
      );
    }
    const sp = resolved[0];
    if (sp.type !== "array") {
      const arrays = this.ctx.schemaPaths.filter((p) => p.type === "array").map((p) => p.path);
      this.fail(
        `table field ${pyRepr(f.id)}: source path ${pyRepr(sp.path)} has type ${sp.type}, but a table needs a ` +
          `type=array path; array paths in the schema: ${arrays.length ? reprList(arrays) : "none"}`,
      );
    }
    f.segments.forEach((seg, i) => {
      for (const col of seg.columns) {
        const fullPath = columnPath(sp.path, col.key);
        if (this.paths.has(fullPath)) continue;
        const prefix = `${sp.path}[].`;
        const keys = this.ctx.schemaPaths
          .filter((p) => p.in_table === sp.path && p.path.startsWith(prefix))
          .map((p) => p.path.slice(prefix.length));
        const hint = keys.length
          ? `valid column keys: ${reprList(keys)}`
          : `items of ${pyRepr(sp.path)} are ${sp.item_type}, not objects, so it has no columns`;
        this.fail(
          `table field ${pyRepr(f.id)} segment ${i}: column key ${pyRepr(col.key)} (${pyRepr(fullPath)}) ` +
            `is not in the schema; ${hint}`,
        );
      }
    });
  }

  // -- warnings

  tableCoverWarning(f) {
    const cells = tableCells(this.t, f);
    const linked = new Set(cells.filter((c) => c.field !== null).map((c) => c.field.id));
    const matched = [];
    const mismatched = [];
    for (const g of this.t.fields) {
      if (g === f || g.rect === null || linked.has(g.id)) continue;
      const inside = cells.filter((c) => c.page === g.page && containedFraction(g.rect, c.rect) >= CELL_COVER_SHARE);
      if (!inside.length) continue;
      const best = Math.max(...inside.map((c) => iou(g.rect, c.rect)));
      if (best >= REPLACE_IOU) matched.push(g.id);
      else mismatched.push([g.id, best]);
    }
    const out = [];
    if (matched.length) {
      out.push(
        `table ${pyRepr(f.id)} covers field(s) ${idsText(matched)}; if its cells replace them, link them as ` +
          "the cells (the column's fields) or remove_field them, so each blank is filled once",
      );
    }
    if (mismatched.length) {
      const values = mismatched.map(([, v]) => v);
      const lo = Math.min(...values);
      const hi = Math.max(...values);
      out.push(
        `table ${pyRepr(f.id)}: its cells are larger than or offset from the boxes of field(s) ` +
          `${idsText(mismatched.map(([id]) => id))} (cell IoU ${pyFixed(lo, 2)}-${pyFixed(hi, 2)}), so cells would not sit on those ` +
          "blanks. Link those fields as the cells instead of removing them: give each column fields=[one " +
          "field id per row, None where a row has none] (fm.grid(ids)['column_fields'] lists them per " +
          "column); each cell then keeps its field's exact box and widget",
      );
    }
    return out;
  }

  warnings(f) {
    if (f.type === "table") return this.tableCoverWarning(f);
    const out = [];
    if (f.rect !== null) {
      for (const g of this.t.fields) {
        if (g !== f && g.page === f.page && g.rect !== null) {
          const v = iou(f.rect, g.rect);
          if (v > OVERLAP_WARN_IOU) {
            out.push(
              `field ${pyRepr(f.id)} overlaps field ${pyRepr(g.id)} on page ${f.page} (IoU ${pyFixed(v, 2)}); ` +
                "if they are the same blank, remove one",
            );
          }
        }
      }
    }
    const s = f.source;
    for (const p of s ? s.paths : []) {
      if (!this.paths.has(p)) {
        out.push(`field ${pyRepr(f.id)} maps ${pyRepr(p)}, which is not in the schema; set_source to a valid path`);
      }
    }
    if (f.type === "text" && s) out.push(...this.booleanOnTextWarning(f, s.paths));
    if (f.type === "checkbox" && s && s.when === null && !s.transforms.some((tr) => tr.op === "bool_to_check")) {
      for (const p of s.paths) {
        const sp = this.paths.get(p);
        if (sp !== undefined && !["boolean", "enum", "array"].includes(sp.type)) {
          out.push(
            `checkbox field ${pyRepr(f.id)} maps ${pyRepr(p)} (type ${sp.type}) with no when condition or ` +
              "bool_to_check transform; add when (e.g. equals/contains) or bool_to_check " +
              "so the box state is explicit",
          );
        }
      }
    }
    return out;
  }

  /** A boolean path on a text field while a choice box sits in the same row: likely the wrong box. */
  booleanOnTextWarning(f, paths) {
    const booleans = paths.filter((p) => this.paths.get(p)?.type === "boolean");
    if (!booleans.length) return [];
    const sameRow = (g) => {
      const top = Math.max(f.rect[1], g.rect[1]);
      const bottom = Math.min(f.rect[3], g.rect[3]);
      return bottom - top >= 0.5 * Math.min(f.rect[3] - f.rect[1], g.rect[3] - g.rect[1]);
    };
    const boxes = this.t.fields.filter(
      (g) => g !== f && (g.type === "checkbox" || g.type === "radio_option") && g.page === f.page && sameRow(g),
    );
    if (!boxes.length) return [];
    const centre = (r) => (r[0] + r[2]) / 2;
    let near = boxes[0];
    for (const g of boxes) if (Math.abs(centre(g.rect) - centre(f.rect)) < Math.abs(centre(near.rect) - centre(f.rect))) near = g;
    return [
      `text field ${pyRepr(f.id)} maps boolean ${pyRepr(booleans[0])}, and ${near.type} field ${pyRepr(near.id)} sits in the ` +
        "same row: a yes/no value is marked in the box (when equals true, or bool_to_check), so map " +
        `${pyRepr(near.id)} and keep ${pyRepr(f.id)} for what its own label asks`,
    ];
  }
}

// ---------------------------------------------------------------- handlers
// Each mutates `run.t` and returns the field it touched (for warnings).

function newStyle(given) {
  return { font_size: "auto", min_font_size: 6, align: "left", comb: null, multiline: false, ...(given ?? {}) };
}

function addField(run, op) {
  if (op.type === "table") run.fail("add_field cannot create a table; use add_table(source_path=..., segments=[...])");
  op.id = run.newId(op.id);
  run.checkRect(op.id, op.page, op.rect);
  const f = {
    id: op.id,
    type: op.type,
    label: op.label,
    page: op.page,
    rect: op.rect,
    source: op.source,
    style: op.style === null ? newStyle() : op.style,
    existing_widget: null,
    segments: [],
    confidence: null,
    origin: run.author,
    required: false,
    required_reason: "",
    required_by: null,
  };
  run.checkSource(f);
  run.t.fields.push(f);
  return f;
}

function removeField(run, op) {
  const f = run.field(op.id);
  const link = run.cellLink(f.id);
  if (link) {
    run.fail(
      `field ${pyRepr(f.id)} is ${link}; unlink it first (set_table_columns with None in its place in ` +
        "that column's fields) or remove the table",
    );
  }
  run.t.fields.splice(run.t.fields.indexOf(f), 1);
  return null;
}

function shiftSegment(seg, dx, dy) {
  const [x0, y0, x1, y1] = seg.rect;
  seg.rect = [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  seg.row_top += dy;
  for (const col of seg.columns) col.x = [col.x[0] + dx, col.x[1] + dx];
}

function moveTable(run, f, op) {
  if (op.rect !== null) {
    const seg = run.tableSegmentFor(f, op.segment, "move");
    const [x0, y0] = seg.rect;
    shiftSegment(seg, op.rect[0] - x0, op.rect[1] - y0);
    seg.rect = op.rect; // a different size is allowed; checkSegment verifies rows/columns still fit
    run.checkSegment(f, op.segment);
    return;
  }
  if (op.segment !== null) run.segment(f, op.segment); // validates the index
  const indexes = op.segment === null ? f.segments.map((_, i) => i) : [op.segment];
  for (const i of indexes) {
    shiftSegment(f.segments[i], op.dx ?? 0, op.dy ?? 0);
    run.checkSegment(f, i);
  }
}

function move(run, op) {
  const f = run.field(op.id);
  if (f.type === "table") {
    moveTable(run, f, op);
    return f;
  }
  run.noSegment(f, op.segment);
  let rect;
  if (op.rect !== null) {
    rect = op.rect;
  } else {
    const dx = op.dx ?? 0;
    const dy = op.dy ?? 0;
    rect = [f.rect[0] + dx, f.rect[1] + dy, f.rect[2] + dx, f.rect[3] + dy];
  }
  run.checkRect(f.id, f.page, rect);
  f.rect = rect;
  return f;
}

function resize(run, op) {
  const f = run.field(op.id);
  if (f.type === "table") {
    run.tableSegmentFor(f, op.segment, "resize").rect = op.rect; // rows and columns keep their positions
    run.checkSegment(f, op.segment);
    return f;
  }
  run.noSegment(f, op.segment);
  run.checkRect(f.id, f.page, op.rect);
  f.rect = op.rect;
  return f;
}

function setLabel(run, op) {
  const f = run.field(op.id);
  f.label = op.label;
  return f;
}

function setRequired(run, op) {
  const f = run.field(op.id);
  f.required = op.required;
  f.required_reason = op.reason;
  f.required_by = run.author;
  return f;
}

function setType(run, op) {
  const f = run.field(op.id);
  if (op.type === "table") {
    run.fail(
      `set_type cannot turn field ${pyRepr(f.id)} into a table (a table needs an array source and segments); ` +
        `remove_field(${pyRepr(f.id)}) then add_table(source_path=..., segments=[...])`,
    );
  }
  if (f.type === "table") {
    run.fail(`field ${pyRepr(f.id)} is a table and has no page/rect; remove_field(${pyRepr(f.id)}) then add_field instead`);
  }
  run.checkWidgetKind(f, op.type, f.existing_widget);
  f.type = op.type;
  run.checkSource(f);
  return f;
}

function setSource(run, op) {
  const f = run.field(op.id);
  f.source = op.source;
  run.checkSource(f);
  return f;
}

function setStyle(run, op, givenStyle) {
  const f = run.field(op.id);
  const merged = { ...f.style, ...givenStyle };
  const size = givenStyle.font_size;
  if (typeof size === "number" && !hasKey(givenStyle, "min_font_size")) {
    // a smaller size lowers the minimum with it, but never raises a deliberately lower one
    merged.min_font_size = Math.min(f.style.min_font_size, size);
  }
  const errors = [];
  const style = parseStyle(merged, [], errors);
  if (style === FAIL) run.fail(`invalid style for field ${pyRepr(f.id)}: ${describe(errors, "style")}`);
  f.style = style.full;
  return f;
}

const HANDLERS = {
  add_field: addField,
  remove_field: removeField,
  move,
  resize,
  set_label: setLabel,
  set_type: setType,
  set_source: setSource,
  set_style: (run, op) => setStyle(run, op, op.styleGiven),
  set_required: setRequired,
};

// ---------------------------------------------------------------- public API

/**
 * Validate and apply one op. Returns a new template (the input is untouched) and any warnings;
 * throws OpError, with Python's message, when the op is rejected or is not a browser op.
 * The op is stored in `history` as Python stores it.
 * @param {object} template a Python-shaped template
 * @param {object} op `{op: "move", id: "f1", dx: 4, ...}`
 * @param {{author: "human" | "agent", note?: string, at?: string, context?: object}} options `at` defaults
 *   to now; `context` (from {@link contextFromTemplate}) defaults to the template's own, without widget kinds
 * @returns {{template: object, warnings: string[]}}
 */
export function applyOp(template, op, { author, note = "", at, context } = {}) {
  const ctx = context ?? contextFromTemplate(template);
  const model = parseOp(op);
  const raw = { ...op };
  if (author !== "agent" && author !== "human") {
    throw new OpError(raw, `author must be 'agent' or 'human', got ${pyRepr(author)}`);
  }
  if (author !== "human") {
    throw new OpError(raw, "the browser edits as 'human'; the checks that apply only to an agent's edits are not available here");
  }
  const t = structuredClone(template);
  const run = new Run(t, ctx, raw, author);
  const values = { ...model.parsed };
  if (model.name === "set_style") values.styleGiven = model.parsed["style$given"];
  const touched = HANDLERS[model.name](run, values);
  const warnings = touched !== null && WARN_OPS.has(model.name) ? run.warnings(touched) : [];
  if (model.name === "add_field") {
    model.parsed.id = values.id;
    model.set.add("id");
  }
  t.history.push({
    op: historyRecord(model),
    author,
    at: at ?? new Date().toISOString().replace(/Z$/, "000+00:00"),
    note,
  });
  return { template: t, warnings };
}
