// Saving, loading and remembering templates in the browser. Pure apart from the DOM calls in
// `downloadBytes` and the `localStorage` calls, which are wrapped so the page works without them.

const STORAGE_PREFIX = "formcast:template:";
const SIZE_TOLERANCE = 1; // points

/**
 * Download bytes (or text) as a file.
 * @param {Uint8Array | string} data
 * @param {string} filename
 * @param {string} [type] the media type (default a PDF)
 */
export function downloadBytes(data, filename, type = "application/pdf") {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Download a template as a JSON file.
 * @param {object} template
 * @param {string} [filename]
 */
export function downloadTemplate(template, filename = "template.json") {
  downloadBytes(`${JSON.stringify(template, null, 2)}\n`, filename, "application/json");
}

/**
 * Check that parsed JSON is a template with the parts the demo relies on (version "1", a form with
 * a page count and page sizes, a schema, a list of fields). Each field must be an object with a
 * string id and type; a table needs at least one segment (an integer page, a rect of four numbers,
 * numeric row_top and row_height, an integer max_rows, and columns with a string key and an x
 * range of two numbers), any other field an integer page and a rect of four numbers. The full check
 * is Python's `Template.model_validate`, which a template saved by the demo passes. A missing
 * history becomes [].
 * @param {any} data
 * @returns {object} `data`
 * @throws {Error} with a plain message when it is not a template
 */
export function checkTemplate(data) {
  const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  if (!isObject(data) || data.formcast_version === undefined) {
    throw new Error("This file is not a formcast template.");
  }
  if (data.formcast_version !== "1") {
    throw new Error(`This template is version ${JSON.stringify(data.formcast_version)}; this page reads version "1".`);
  }
  if (
    !Array.isArray(data.fields) || !isObject(data.form) || !Array.isArray(data.form.page_sizes) ||
    !Number.isInteger(data.form.page_count) || !isObject(data.schema)
  ) {
    throw new Error("This file is not a complete template: it needs a form, a schema and a list of fields.");
  }
  data.fields.forEach((f, i) => {
    const problem = fieldProblem(f);
    if (problem !== null) {
      const which = isObject(f) && typeof f.id === "string" ? `field ${i + 1} (${JSON.stringify(f.id)})` : `field ${i + 1}`;
      throw new Error(`This file is not a complete template: ${which} ${problem}.`);
    }
  });
  if (!Array.isArray(data.history)) data.history = [];
  return data;
}

const isNumbers = (v, n) => Array.isArray(v) && v.length === n && v.every((x) => Number.isFinite(x));

/** What is wrong with one template field's shape, or null. */
function fieldProblem(f) {
  if (f === null || typeof f !== "object" || Array.isArray(f)) return "is not an object";
  if (typeof f.id !== "string" || typeof f.type !== "string") return "needs a string id and type";
  if (f.type !== "table") {
    return Number.isInteger(f.page) && isNumbers(f.rect, 4) ? null : "needs a page number and a rect of four numbers";
  }
  const segmentOk = (seg) =>
    seg !== null && typeof seg === "object" && Number.isInteger(seg.page) && isNumbers(seg.rect, 4) &&
    Number.isFinite(seg.row_top) && Number.isFinite(seg.row_height) && Number.isInteger(seg.max_rows) &&
    Array.isArray(seg.columns) &&
    seg.columns.every((c) => c !== null && typeof c === "object" && typeof c.key === "string" && isNumbers(c.x, 2));
  return Array.isArray(f.segments) && f.segments.length > 0 && f.segments.every(segmentOk)
    ? null
    : "is a table and needs segments, each with a page, a rect, rows and columns";
}

/**
 * Read a template from a JSON file (see `checkTemplate`).
 * @param {{text: () => Promise<string>}} file a File or Blob
 * @returns {Promise<object>}
 * @throws {Error} with a plain message when the file is not a template
 */
export async function readTemplateFile(file) {
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error("This file is not a template: it is not valid JSON.");
  }
  return checkTemplate(data);
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const pt = (n) => String(Math.round(n * 10) / 10);

/**
 * Whether a template belongs to a PDF: same page count, and every page the same size within a point.
 * @param {{form: {page_count: number, page_sizes: number[][]}}} template
 * @param {{pageCount: number, pages: {width: number, height: number}[]}} doc the opened PDF
 * @returns {string | null} null when it fits, else a message naming what differs
 */
export function checkTemplateFits(template, doc) {
  const want = template.form.page_count;
  if (want !== doc.pageCount) {
    return `This template is for a ${want}-page form; this PDF has ${plural(doc.pageCount, "page")}.`;
  }
  for (let i = 0; i < want; i++) {
    const size = template.form.page_sizes[i];
    if (!size) continue;
    const [tw, th] = size;
    const { width, height } = doc.pages[i];
    if (Math.abs(tw - width) > SIZE_TOLERANCE || Math.abs(th - height) > SIZE_TOLERANCE) {
      return `This template expects page ${i + 1} to be ${pt(tw)} x ${pt(th)} pt; this PDF's page ${i + 1} is ${pt(width)} x ${pt(height)} pt.`;
    }
  }
  return null;
}

/**
 * Remember a template under a key (one per sample form). Does nothing when storage is unavailable.
 * @param {string} key
 * @param {object} template
 * @returns {boolean} true when it was stored
 */
export function remember(key, template) {
  try {
    localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(template));
    return true;
  } catch {
    return false;
  }
}

/**
 * The template remembered under a key, or null when there is none or storage is unavailable.
 * @param {string} key
 * @returns {object | null}
 */
export function recall(key) {
  try {
    const text = localStorage.getItem(STORAGE_PREFIX + key);
    return text === null ? null : JSON.parse(text);
  } catch {
    return null;
  }
}
