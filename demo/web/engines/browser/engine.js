// The browser engine: everything runs in the page. pdf.js opens the PDF, FFDNet runs in a worker,
// ops.js applies edits and pdf-lib fills. No AI, no stores, no records database.
//
// The detector and the fill (with pdf-lib) are imported on first use, so a page that only opens
// PDFs and edits templates never downloads them. When that import fails (the connection dropped),
// the call fails with PART_FAILED, a plain message, rather than the browser's own.

import { assertEngine } from "../../core/engine.js";
import { OpError, applyOp, contextFromTemplate } from "../../core/ops.js";
import { openPdf } from "./document.js";
import { seedTemplate } from "./seed.js";

/** The message when a part of the page loaded on first use (the detector, the fill) can't be fetched. */
export const PART_FAILED = "Part of the page didn't load. Check the connection and try again.";

/** `import()` that fails with PART_FAILED (the browser's error kept as `cause`). */
async function importPart(load) {
  try {
    return await load();
  } catch (e) {
    throw new Error(PART_FAILED, { cause: e });
  }
}

/**
 * Widget name -> kinds of every widget with that name, over the whole document (Python's
 * `OpContext.from_template(template, form_map).widget_kinds`).
 * @param {{pages: {widgets: {name: string, kind: string}[]}[]}} doc
 * @returns {Record<string, string[]>}
 */
export function widgetKinds(doc) {
  const kinds = {};
  for (const page of doc.pages) {
    for (const w of page.widgets) (Object.hasOwn(kinds, w.name) ? kinds[w.name] : (kinds[w.name] = [])).push(w.kind);
  }
  return kinds;
}

/**
 * Create the browser engine.
 * @param {{workerUrl?: string | URL, detectorModel?: "S" | "L"}} [opts] `workerUrl` of detect-worker.js
 *   (defaults to the one next to detect.js); `detectorModel` used when `detect` is not told otherwise
 * @returns {object} an engine (see core/engine.js)
 */
export function createBrowserEngine({ workerUrl, detectorModel = "S" } = {}) {
  const detectors = new Map();
  let fillModule = null;

  async function detector(model) {
    if (!detectors.has(model)) {
      const { Detector } = await importPart(() => import("./detect.js"));
      if (!detectors.has(model)) detectors.set(model, new Detector({ model, workerUrl }));
    }
    return detectors.get(model);
  }

  function filler() {
    fillModule ??= importPart(() => import("../../core/fill/index.js")).catch((e) => {
      fillModule = null;
      throw e;
    });
    return fillModule;
  }

  const engine = {
    capabilities: { detectLive: true, ai: false, matches: false, store: false, records: false, scanned: false },

    /**
     * Open PDF bytes (see document.js `openPdf`; rejects with a DocError). The document keeps its
     * own widget kinds (`doc.widgetKinds`), so several documents can be open at once.
     * @param {Uint8Array} bytes
     */
    async open(bytes) {
      const doc = await openPdf(bytes);
      doc.widgetKinds = widgetKinds(doc);
      return doc;
    },

    /**
     * The starting template: widget fields, plus a field per detection on pages without widgets.
     * @param {object} doc a BrowserDoc
     * @param {object} schema
     * @param {{detections?: Record<number, object[]>}} [opts] candidates by page index, from `detect`
     */
    async seed(doc, schema, { detections = {} } = {}) {
      const lines = {};
      for (const key of Object.keys(detections)) lines[key] = await doc.textLines(Number(key));
      return seedTemplate(doc, schema, detections, lines);
    },

    /**
     * FFDNet candidates on one page (loads the model on first use).
     * @param {object} doc
     * @param {number} pageIndex
     * @param {{model?: "S" | "L", onProgress?: (loaded: number, total: number) => void}} [opts]
     */
    async detect(doc, pageIndex, { model = detectorModel, onProgress } = {}) {
      const d = await detector(model);
      await d.load(onProgress);
      return d.detect(doc, pageIndex);
    },

    /**
     * What the detector ran on: model, execution provider and ms per page (null before any use).
     * @param {"S" | "L"} [model]
     */
    detectorInfo(model = detectorModel) {
      return detectors.get(model)?.info ?? null;
    },

    /**
     * One edit op, checked against the template and the widget kinds of `doc` (the document the
     * template is for; without one, any type may use any widget).
     * @param {object} template
     * @param {object} op
     * @param {"human"} [author]
     * @param {{doc?: object | null, note?: string}} [opts]
     * @returns {Promise<{template: object, warnings: string[]} | {error: string}>}
     */
    async apply(template, op, author = "human", { doc = null, note = "" } = {}) {
      const context = contextFromTemplate(template, { widgetKinds: doc?.widgetKinds ?? null });
      try {
        return applyOp(template, op, { author, note, context });
      } catch (e) {
        if (e instanceof OpError) return { error: e.message };
        throw e;
      }
    },

    /** Fill `doc` with one record (pdf-lib); `{pdfBytes, report}`. */
    async fill(template, doc, record) {
      const { fill } = await filler();
      return fill(template, doc.bytes, record);
    },

    /** The PDF with the template's fields as empty form fields. */
    async smartPdf(template, doc) {
      const { smartPdf } = await filler();
      return smartPdf(template, doc.bytes);
    },

    /** Stop the detection workers. */
    dispose() {
      for (const d of detectors.values()) d.dispose();
      detectors.clear();
    },
  };
  return assertEngine(engine);
}
