// FFDNet field detection from the page: renders a page at the library's detection resolution,
// hands the pixels to the detection worker and maps the boxes it returns to PDF points
// (unrotated crop-box space). Every request has a time limit and fails with a plain message.

import { makeView, toPage, viewSize } from "../../core/geometry.js";
import { DETECT_DPI, MODELS } from "./ffdnet.js";

/**
 * Time limits in milliseconds. Loading fails when this long passes with no progress from the worker
 * (each download progress message restarts the clock, so a slow but working download goes on);
 * detecting a page, render included, fails when it takes longer than its limit.
 */
export const LOAD_TIMEOUT_MS = 120_000;
export const DETECT_TIMEOUT_MS = 60_000;

const WORKER_FAILED = "The detection worker stopped unexpectedly. Try again.";
const RENDER_FAILED = "This page could not be rendered for detection.";

/**
 * @typedef {[number, number, number, number]} Rect
 * @typedef {{kind: "text" | "choice" | "signature", rect: Rect, conf: number}} Candidate
 */

function newCanvas() {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(1, 1);
  return document.createElement("canvas");
}

/** Runs FFDNet in a Web Worker. */
export class Detector {
  /**
   * @param {{model?: "S" | "L", workerUrl?: string | URL, loadTimeoutMs?: number, detectTimeoutMs?: number}} [opts]
   *   `workerUrl` defaults to detect-worker.js next to this module
   */
  constructor({ model = "S", workerUrl, loadTimeoutMs = LOAD_TIMEOUT_MS, detectTimeoutMs = DETECT_TIMEOUT_MS } = {}) {
    if (!Object.hasOwn(MODELS, model)) throw new RangeError(`unknown model ${model}; have S and L`);
    this.model = model;
    this.workerUrl = workerUrl ?? new URL("./detect-worker.js", import.meta.url);
    this.loadTimeoutMs = loadTimeoutMs;
    this.detectTimeoutMs = detectTimeoutMs;
    this.worker = null;
    this.loading = null;
    this.pending = new Map();
    this.nextId = 1;
    this.provider = null;
    this.totalMs = 0;
    this.pages = 0;
  }

  /** What ran: the model name, the execution provider ("webgpu" or "wasm", null before loading) and ms per page. */
  get info() {
    return {
      model: `FFDNet-${this.model}`,
      provider: this.provider,
      msPerPage: this.pages ? this.totalMs / this.pages : null,
    };
  }

  /**
   * Download (or take from the cache) and start the model. Safe to call again; later calls wait for
   * the first. Fails when `loadTimeoutMs` passes without any progress from the worker.
   * @param {(loaded: number, total: number) => void} [onProgress] download progress in bytes
   * @returns {Promise<void>}
   */
  load(onProgress) {
    if (this.loading === null) {
      const { url, bytes } = MODELS[this.model];
      const late = `The detection model stopped loading: nothing arrived for ${this.loadTimeoutMs / 1000} seconds. ` +
        "Check the connection and try again.";
      const limit = { ms: this.loadTimeoutMs, idle: true, message: late };
      this.loading = this.request({ type: "load", url, bytes }, [], limit, onProgress)
        .then((res) => {
          this.provider = res.provider;
        })
        .catch((e) => {
          this.loading = null;
          throw e;
        });
    }
    return this.loading;
  }

  /**
   * Field candidates on one page, in PDF points (unrotated crop-box space, origin top-left). After the
   * model is loaded, rendering and detection together must finish within `detectTimeoutMs`.
   * @param {{pages: {width: number, height: number, rotation: number}[],
   *   render(index: number, canvas: any, scale: number): Promise<void>}} doc a BrowserDoc
   * @param {number} pageIndex
   * @returns {Promise<Candidate[]>}
   */
  async detect(doc, pageIndex) {
    const page = doc.pages[pageIndex];
    if (page === undefined) throw new RangeError(`no page ${pageIndex}`);
    await this.load();
    const started = performance.now();
    const late = `Detection did not finish within ${this.detectTimeoutMs / 1000} seconds on this page.`;
    const { pixels, width, height } = await this.renderPage(doc, pageIndex, this.detectTimeoutMs, late);
    const left = Math.max(0, this.detectTimeoutMs - (performance.now() - started));
    const res = await this.request({ type: "detect", rgba: pixels.buffer, width, height }, [pixels.buffer],
      { ms: left, idle: false, message: late });
    this.provider = res.provider;
    this.totalMs += performance.now() - started;
    this.pages += 1;
    const view = makeView({ width: page.width, height: page.height, rotation: page.rotation });
    const shown = viewSize(view); // the rendered page in points
    return res.detections.map(({ kind, box: [x0, y0, x1, y1], conf }) => ({
      kind,
      rect: toPage(
        { left: x0 * shown.width, top: y0 * shown.height, width: (x1 - x0) * shown.width, height: (y1 - y0) * shown.height },
        view,
      ),
      conf,
    }));
  }

  /** Stop the worker; pending requests fail. The next load starts a new one. */
  dispose() {
    this.stop(new Error("The detector was closed."));
  }

  // -- internals

  /** The page's RGBA pixels at the detection resolution, within `ms` (a plain error otherwise). */
  async renderPage(doc, pageIndex, ms, late) {
    const rendered = (async () => {
      const canvas = newCanvas();
      await doc.render(pageIndex, canvas, DETECT_DPI / 72);
      const { width, height } = canvas;
      return { pixels: canvas.getContext("2d").getImageData(0, 0, width, height).data, width, height };
    })().catch((e) => {
      throw new Error(RENDER_FAILED, { cause: e });
    });
    rendered.catch(() => {}); // settled late after a timeout: nobody is waiting any more
    let timer;
    const expired = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(late)), ms);
    });
    try {
      return await Promise.race([rendered, expired]);
    } finally {
      clearTimeout(timer);
    }
  }

  startWorker() {
    const worker = new Worker(this.workerUrl, { type: "module" });
    worker.onmessage = (event) => {
      const msg = event.data;
      const req = this.pending.get(msg.id);
      if (req === undefined) return;
      if (msg.type === "progress") {
        if (req.limit.idle) req.arm(); // progress restarts an inactivity limit
        req.onProgress?.(msg.loaded, msg.total);
        return;
      }
      this.settle(msg.id);
      if (msg.ok) req.resolve(msg);
      else req.reject(Object.assign(new Error(msg.message), msg.detail ? { detail: msg.detail } : {}));
    };
    worker.onerror = (event) => {
      event.preventDefault?.();
      this.stop(new Error(WORKER_FAILED));
    };
    this.worker = worker;
  }

  /**
   * Post a request to the worker. `limit.ms` is a deadline from now, or with `limit.idle` the longest
   * wait between messages; when it passes the worker is stopped (a stuck worker can't be interrupted,
   * so the next request starts clean) and the request fails with `limit.message`.
   */
  request(body, transfer, limit, onProgress) {
    if (this.worker === null) this.startWorker();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const req = { resolve, reject, timer: null, limit, onProgress };
      req.arm = () => {
        clearTimeout(req.timer);
        req.timer = setTimeout(() => this.stop(new Error(limit.message)), limit.ms);
      };
      req.arm();
      this.pending.set(id, req);
      this.worker.postMessage({ id, ...body }, transfer);
    });
  }

  settle(id) {
    const req = this.pending.get(id);
    if (req === undefined) return;
    clearTimeout(req.timer);
    this.pending.delete(id);
  }

  stop(error) {
    this.worker?.terminate();
    this.worker = null;
    this.loading = null;
    this.provider = null;
    for (const [id, req] of [...this.pending]) {
      this.settle(id);
      req.reject(error);
    }
  }
}
