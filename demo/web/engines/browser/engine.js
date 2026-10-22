// The browser engine: everything runs in the page. pdf.js opens the PDF, FFDNet runs in a worker,
// ops.js applies edits and pdf-lib fills. No AI, no stores, no records database.

import { assertEngine } from "../../core/engine.js";
import { fill, smartPdf } from "../../core/fill/index.js";
import { OpError, applyOp, contextFromTemplate } from "../../core/ops.js";
import { Detector } from "./detect.js";
import { openPdf } from "./document.js";
import { seedTemplate } from "./seed.js";

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
  let openKinds = null; // widget kinds of the last opened document

  function detector(model) {
    if (!detectors.has(model)) detectors.set(model, new Detector({ model, workerUrl }));
    return detectors.get(model);
  }

  const engine = {
    capabilities: { detectLive: true, ai: false, matches: false, store: false, records: false, scanned: false },

    /**
     * Open PDF bytes (see document.js `openPdf`; rejects with a DocError).
     * @param {Uint8Array} bytes
     */
    async open(bytes) {
      const doc = await openPdf(bytes);
      openKinds = widgetKinds(doc);
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
      const d = detector(model);
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
     * One edit op, checked against the template and the open document's widget kinds.
     * @returns {Promise<{template: object, warnings: string[]} | {error: string}>}
     */
    async apply(template, op, author = "human") {
      const context = contextFromTemplate(template, { widgetKinds: openKinds });
      try {
        return applyOp(template, op, { author, context });
      } catch (e) {
        if (e instanceof OpError) return { error: e.message };
        throw e;
      }
    },

    /** Fill `doc` with one record (pdf-lib); `{pdfBytes, report}`. */
    async fill(template, doc, record) {
      return fill(template, doc.bytes, record);
    },

    /** The PDF with the template's fields as empty form fields. */
    async smartPdf(template, doc) {
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
