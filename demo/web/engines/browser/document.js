// Opening a PDF in the browser with pdf.js: page sizes, rotation, crop boxes, form widgets, a page
// kind, a page renderer and text lines for seed labels. Rects come out as the library's: PDF
// points, origin top-left, unrotated crop-box space (converted by geometry.js).

import { AnnotationMode, GlobalWorkerOptions, getDocument } from "../../vendor/pdfjs/build/pdf.min.mjs";
import { fromUserSpace, normRect } from "../../core/geometry.js";

const PDFJS = new URL("../../vendor/pdfjs/", import.meta.url);
GlobalWorkerOptions.workerSrc = new URL("build/pdf.worker.min.mjs", PDFJS).href;

/** The demo takes PDFs of at most this many pages. */
export const MAX_PAGES = 50;
/** A page without widgets with at least this many text characters is `digital`, else `scanned`. */
export const MIN_TEXT_CHARS = 10;
// A PDF's header must start within this many bytes.
const HEADER_WINDOW = 1024;

/** Why a PDF could not be opened. `code` is one of the codes below; `message` is plain text for a visitor. */
export class DocError extends Error {
  /**
   * @param {"encrypted" | "corrupt" | "not_pdf" | "empty" | "too_many_pages"} code
   * @param {string} message
   */
  constructor(code, message) {
    super(message);
    this.name = "DocError";
    this.code = code;
  }
}

const MESSAGES = {
  encrypted: "This PDF needs a password to open. Save a copy without the password and try that.",
  corrupt: "This PDF is damaged and can't be read.",
  not_pdf: "This file isn't a PDF.",
  empty: "This PDF has no pages.",
};

/**
 * @typedef {[number, number, number, number]} Rect
 * @typedef {{name: string, kind: "text" | "checkbox" | "radio" | "combo" | "list" | "signature" | "button",
 *   rect: Rect, options: string[], tooltip: string | null}} Widget
 * @typedef {{index: number, width: number, height: number, rotation: number, cropBox: Rect,
 *   kind: "fillable" | "digital" | "scanned", widgets: Widget[]}} Page
 * @typedef {{text: string, rect: Rect}} TextLine
 * @typedef {{pageCount: number, pages: Page[], encrypted: boolean, bytes: Uint8Array,
 *   render(index: number, canvas: HTMLCanvasElement | OffscreenCanvas, scale: number): Promise<void>,
 *   textLines(index: number): Promise<TextLine[]>, destroy(): Promise<void>}} BrowserDoc
 */

function hasPdfHeader(bytes) {
  const n = Math.min(bytes.length, HEADER_WINDOW) - 5;
  for (let i = 0; i <= n; i++) {
    if (bytes[i] === 0x25 && bytes[i + 1] === 0x50 && bytes[i + 2] === 0x44 && bytes[i + 3] === 0x46 && bytes[i + 4] === 0x2d) {
      return true; // "%PDF-"
    }
  }
  return false;
}

/** The widget kind of a pdf.js Widget annotation, or null for anything that is not a form field. */
function widgetKind(a) {
  switch (a.fieldType) {
    case "Tx": return "text";
    case "Sig": return "signature";
    case "Ch": return a.combo ? "combo" : "list";
    case "Btn": return a.checkBox ? "checkbox" : a.radioButton ? "radio" : "button";
    default: return null;
  }
}

function widgetsOf(annotations, cropBox) {
  const out = [];
  for (const a of annotations) {
    if (a.annotationType !== 20 && a.subtype !== "Widget") continue; // 20 = AnnotationType.WIDGET
    const kind = widgetKind(a);
    if (kind === null || !Array.isArray(a.rect)) continue;
    const display = (o) => (Array.isArray(o.displayValue) ? o.displayValue.join("") : o.displayValue ?? o.exportValue ?? "");
    const options = kind === "combo" || kind === "list" ? (a.options ?? []).map((o) => String(display(o))) : [];
    out.push({
      name: a.fieldName ?? "",
      kind,
      rect: fromUserSpace(a.rect, cropBox),
      options,
      tooltip: a.alternativeText || null,
    });
  }
  return out;
}

/**
 * Text lines from pdf.js text items: items on the same baseline and close together join into one
 * line. Each item's box runs from a little under its baseline to its font height above it.
 */
function linesOf(items, cropBox) {
  const lines = [];
  let cur = null;
  const flush = () => {
    if (cur && cur.text.trim()) lines.push({ text: cur.text.trim(), rect: fromUserSpace(cur.box, cropBox) });
    cur = null;
  };
  for (const item of items) {
    if (typeof item.str !== "string") continue;
    const [a, b, c, d, e, f] = item.transform;
    const size = Math.hypot(c, d) || item.height || 1;
    const along = Math.hypot(a, b) || 1;
    const ux = a / along;
    const uy = b / along;
    const horizontal = Math.abs(uy) < 1e-3 && ux > 0;
    const width = item.width;
    let box;
    if (horizontal) {
      box = [e, f - 0.2 * size, e + width, f + 0.8 * size];
    } else {
      // any other direction: the bounding box of the run's quad
      const vx = -uy;
      const vy = ux;
      const xs = [e, e + ux * width, e + vx * size, e + ux * width + vx * size];
      const ys = [f, f + uy * width, f + vy * size, f + uy * width + vy * size];
      box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    if (item.str.trim()) {
      const joins = cur !== null && horizontal && cur.horizontal &&
        Math.abs(cur.baseline - f) < 0.3 * size && box[0] - cur.box[2] < size && box[0] - cur.box[2] > -0.5 * size;
      if (!joins) {
        flush();
        cur = { text: item.str, box, baseline: f, horizontal };
      } else {
        const gap = box[0] - cur.box[2];
        const space = gap > 0.15 * size && !/\s$/.test(cur.text) && !/^\s/.test(item.str) ? " " : "";
        cur.text += space + item.str;
        cur.box = [
          Math.min(cur.box[0], box[0]),
          Math.min(cur.box[1], box[1]),
          Math.max(cur.box[2], box[2]),
          Math.max(cur.box[3], box[3]),
        ];
      }
    } else if (cur !== null && horizontal && item.str) {
      cur.text += " "; // a space item inside a line
      cur.box[2] = Math.max(cur.box[2], box[2]);
    }
    if (item.hasEOL) flush();
  }
  flush();
  return lines;
}

function countChars(items) {
  let n = 0;
  for (const item of items) {
    if (typeof item.str === "string") n += item.str.replace(/\s/gu, "").length;
  }
  return n;
}

/**
 * Open a PDF with pdf.js. A PDF that needs only an owner password opens (`encrypted: true`; the
 * browser fill refuses it); one that needs a password to open is refused.
 * @param {Uint8Array} bytes
 * @returns {Promise<BrowserDoc>}
 * @throws {DocError} with code "encrypted", "corrupt", "not_pdf", "empty" or "too_many_pages"
 */
export async function openPdf(bytes) {
  if (!(bytes instanceof Uint8Array) || !hasPdfHeader(bytes)) throw new DocError("not_pdf", MESSAGES.not_pdf);
  const task = getDocument({
    data: bytes.slice(), // pdf.js takes ownership of the buffer it is given
    cMapUrl: new URL("cmaps/", PDFJS).href,
    cMapPacked: true,
    standardFontDataUrl: new URL("standard_fonts/", PDFJS).href,
    wasmUrl: new URL("wasm/", PDFJS).href,
    iccUrl: new URL("iccs/", PDFJS).href,
    isEvalSupported: false,
    enableXfa: false,
  });
  let pdf;
  try {
    pdf = await task.promise;
  } catch (e) {
    await task.destroy().catch(() => {});
    if (e?.name === "PasswordException") throw new DocError("encrypted", MESSAGES.encrypted);
    throw new DocError("corrupt", MESSAGES.corrupt);
  }
  try {
    const pageCount = pdf.numPages;
    if (pageCount === 0) throw new DocError("empty", MESSAGES.empty);
    if (pageCount > MAX_PAGES) {
      throw new DocError("too_many_pages", `This PDF has ${pageCount} pages; the demo takes PDFs of up to ${MAX_PAGES} pages.`);
    }
    const meta = await pdf.getMetadata().catch(() => null);
    const encrypted = Boolean(meta?.info?.EncryptFilterName);
    const linesCache = new Map();
    const pages = [];
    for (let index = 0; index < pageCount; index++) {
      const page = await pdf.getPage(index + 1);
      const cropBox = /** @type {Rect} */ (normRect(page.view));
      const [annotations, text] = await Promise.all([page.getAnnotations({ intent: "display" }), page.getTextContent()]);
      const widgets = widgetsOf(annotations, cropBox);
      linesCache.set(index, linesOf(text.items, cropBox));
      const kind = widgets.length ? "fillable" : countChars(text.items) >= MIN_TEXT_CHARS ? "digital" : "scanned";
      pages.push({
        index,
        width: cropBox[2] - cropBox[0],
        height: cropBox[3] - cropBox[1],
        rotation: ((page.rotate % 360) + 360) % 360,
        cropBox,
        kind,
        widgets,
      });
    }
    return {
      pageCount,
      pages,
      encrypted,
      bytes,
      async render(index, canvas, scale) {
        const page = await pdf.getPage(index + 1);
        const viewport = page.getViewport({ scale });
        // whole pixels covering the page, as MuPDF sizes a pixmap
        canvas.width = Math.max(1, Math.ceil(viewport.width - 0.001));
        canvas.height = Math.max(1, Math.ceil(viewport.height - 0.001));
        await page.render({ canvas, viewport, annotationMode: AnnotationMode.ENABLE }).promise;
      },
      async textLines(index) {
        if (!linesCache.has(index)) throw new RangeError(`no page ${index}`);
        return linesCache.get(index).map((l) => ({ text: l.text, rect: [...l.rect] }));
      },
      async destroy() {
        await task.destroy();
      },
    };
  } catch (e) {
    await task.destroy().catch(() => {});
    if (e instanceof DocError) throw e;
    throw new DocError("corrupt", MESSAGES.corrupt);
  }
}
