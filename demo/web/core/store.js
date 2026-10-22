// Saving, loading and remembering templates in the browser. Pure apart from the DOM calls in
// `downloadTemplate` and the `localStorage` calls, which are wrapped so the page works without them.

const STORAGE_PREFIX = "formcast:template:";
const SIZE_TOLERANCE = 1; // points

/**
 * Download a template as a JSON file.
 * @param {object} template
 * @param {string} [filename]
 */
export function downloadTemplate(template, filename = "template.json") {
  const blob = new Blob([`${JSON.stringify(template, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Read a template from a JSON file. Checks the parts the demo relies on; the full check is
 * Python's `Template.model_validate`, which a template saved by the demo passes.
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
  const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  if (!isObject(data) || data.formcast_version === undefined) {
    throw new Error("This file is not a formcast template.");
  }
  if (data.formcast_version !== "1") {
    throw new Error(`This template is version ${JSON.stringify(data.formcast_version)}; this page reads version "1".`);
  }
  if (!Array.isArray(data.fields) || !isObject(data.form) || !Array.isArray(data.form.page_sizes) || !isObject(data.schema)) {
    throw new Error("This file is not a complete template: it needs a form, a schema and a list of fields.");
  }
  if (!Array.isArray(data.history)) data.history = [];
  return data;
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
