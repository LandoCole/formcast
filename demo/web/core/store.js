// Downloads and whether a template belongs to a PDF. Pure apart from the DOM calls in
// `downloadBytes`.

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
