// The server engine: the workbench page talking to `python -m demo.server` over its JSON API
// (demo/server/api.py). Same interface as the browser engine (core/engine.js) plus what only a
// server has: AI jobs (build, adapt, revise), template matches, the template store and the record
// source. Requests go to the page's own origin (the server refuses any other), so `baseUrl` is ""
// by default and every URL is relative.
//
// Page images. The server renders every page (`GET /api/documents/{id}/pages/{n}.png?dpi=`) with the
// page's /Rotate already applied, so a ServerDoc page carries `imageUrl(dpi)` and a `render(canvas,
// scale)` that returns that URL, and the PageView shows it as an <img>. The page's `width` and
// `height` stay the UNROTATED crop-box size and `rotation` is the page's real /Rotate (from the
// server's `rotations`), exactly as for a browser document: template rects are in unrotated
// crop-box space, and geometry.js turns them into positions over the turned image. Giving the
// view `rotation: 0` with the image's own (turned) size would put every box in the wrong place on
// a rotated page. An older server that sends no `rotations` is read as all zeros.
//
// Errors. Every failure is an `EngineError(code, detail)` whose `message` is the plain detail:
//   the server's own {error, detail} keeps its code (not_found, bad_input, busy, ai_not_configured,
//   fingerprint_mismatch, cannot_rebind, ...); `timeout`, `network` and `server` are made here.
// Every request has a timeout: 30 s, or 120 s for a document upload and for the requests that may wait
// for the server's first reading of a PDF (its form map, started when the document is opened, which on a
// first run includes downloading the detector weights): seed, matches, and an edit checked against the PDF.
// `apply` is the exception to throwing: like the browser engine it always resolves
// `{template, warnings}` or `{error}`, because the session's queue relies on that. A fill or smart
// PDF for a PDF that is not the one the template was made for rejects with code
// `fingerprint_mismatch`; the workbench then offers `rebind(template, doc)` or refills with
// `{checkFingerprint: false}`.
//
// The one-shot flow: `data.file(file)` / `data.text(text)` send the data to fill with and resolve its
// summary (a table's columns with types and samples, or free text); `oneshot(doc, opts)` starts the job
// that designs the schema, maps the boxes and maps that data (see demo/server/oneshot.py);
// `open({template: name})` opens the PDF a saved template keeps; `templates.put(name, template, {doc,
// mapping})` saves that PDF and the data mapping with it; `templates.schemaUrl(name)` is its schema file.
//
// Jobs (`build`, `revise`, `oneshot`) return a JobHandle {id, kind, poll(), cancel(), done, onUpdate}. `done`
// polls `GET /api/jobs/{id}` (first at once, then every `pollMs`, 1000 by default) and resolves with
// the job when its status is done, cancelled or error. It rejects only if the server cannot be
// reached several polls in a row. Set `onUpdate` to a function to see the job after every poll
// (its steps so far), without polling a second time.

import { assertEngine } from "../../core/engine.js";

/** Milliseconds a request may take (a document upload gets {@link UPLOAD_TIMEOUT_MS}). */
export const REQUEST_TIMEOUT_MS = 30_000;
export const UPLOAD_TIMEOUT_MS = 120_000;
/** Milliseconds for a request that may wait for the server's first reading of the PDF (see the top). */
export const FORM_MAP_TIMEOUT_MS = 120_000;
/** How often a running job is polled. */
export const POLL_MS = 1000;
/** Consecutive failed polls before `done` gives up. */
const POLL_FAILURES = 3;
const DEFAULT_DPI = 110;
const MIN_DPI = 36;
const MAX_DPI = 200;

const MESSAGES = {
  timeout: "The workbench didn't answer in time. Check that it is still running, then try again.",
  network: "Couldn't reach the workbench. Check that it is still running, then try again.",
  server: "The workbench ran into a problem. Its log has the details.",
};

/** Why a call to the workbench failed. `message` is plain text for a visitor. */
export class EngineError extends Error {
  /**
   * @param {string} code the server's error code, or "timeout", "network" or "server"
   * @param {string} detail
   * @param {{status?: number, cause?: unknown}} [extra] the HTTP status, when there was an answer
   */
  constructor(code, detail, { status = 0, cause } = {}) {
    super(detail, cause === undefined ? undefined : { cause });
    this.name = "EngineError";
    this.code = code;
    this.detail = detail;
    this.status = status;
  }
}

function fromBase64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/**
 * The record argument of `fill`: a record, or a reference `{schema, id}` to one in the record
 * source (an object with exactly those two keys, both text). `recordRef` in the options says so
 * explicitly, for a record that really has just those two fields.
 */
function recordBody(record, recordRef) {
  if (recordRef !== undefined) return { record_ref: recordRef };
  const keys = isObject(record) ? Object.keys(record) : [];
  if (keys.length === 2 && typeof record.schema === "string" && typeof record.id === "string") {
    return { record_ref: { schema: record.schema, id: record.id } };
  }
  return { record: record ?? {} };
}

/**
 * Create the server engine; fetches the server's capabilities first (rejects with an EngineError
 * when the server can't be reached).
 * @param {{baseUrl?: string, fetch?: typeof fetch, timeoutMs?: number, uploadTimeoutMs?: number,
 *   formMapTimeoutMs?: number, pollMs?: number}} [opts] `fetch`, the timeouts and `pollMs` are for tests
 * @returns {Promise<object>} an engine (see core/engine.js)
 */
export async function createServerEngine({
  baseUrl = "",
  fetch: fetchImpl = (...args) => globalThis.fetch(...args),
  timeoutMs = REQUEST_TIMEOUT_MS,
  uploadTimeoutMs = UPLOAD_TIMEOUT_MS,
  formMapTimeoutMs = FORM_MAP_TIMEOUT_MS,
  pollMs = POLL_MS,
} = {}) {
  const root = baseUrl.replace(/\/+$/, "");

  /**
   * One request: `json` is sent as the JSON body, `form` as a multipart body. Returns the parsed
   * JSON of a 2xx; throws EngineError otherwise.
   */
  async function call(method, path, { json, form, timeout = timeoutMs } = {}) {
    const init = { method, signal: AbortSignal.timeout(timeout), headers: {} };
    if (json !== undefined) {
      init.body = JSON.stringify(json);
      init.headers["Content-Type"] = "application/json";
    } else if (form !== undefined) {
      init.body = form; // the browser sets the multipart boundary
    }
    let res;
    let text;
    try {
      res = await fetchImpl(`${root}${path}`, init);
      text = await res.text();
    } catch (e) {
      if (e?.name === "TimeoutError" || e?.name === "AbortError") throw new EngineError("timeout", MESSAGES.timeout);
      throw new EngineError("network", MESSAGES.network, { cause: e });
    }
    let body = null;
    try {
      body = text === "" ? null : JSON.parse(text);
    } catch {
      body = null;
    }
    if (res.ok) {
      if (body === null) throw new EngineError("server", MESSAGES.server, { status: res.status });
      return body;
    }
    if (isObject(body) && typeof body.error === "string") {
      throw new EngineError(body.error, String(body.detail ?? body.error), { status: res.status });
    }
    throw new EngineError(res.status >= 500 ? "server" : `http_${res.status}`, MESSAGES.server, { status: res.status });
  }

  const caps = await call("GET", "/api/capabilities");
  const flag = (name) => caps[name] === true;

  /** The ServerDoc for a document summary. */
  function makeDoc(summary) {
    const id = summary.id;
    const sizes = summary.page_sizes ?? [];
    const rotations = summary.rotations ?? [];
    const imageUrl = (index, dpi = DEFAULT_DPI) => `${root}/api/documents/${id}/pages/${index}.png?dpi=${Math.round(dpi)}`;
    const pages = sizes.map(([width, height], index) => ({
      index,
      width,
      height,
      rotation: rotations[index] ?? 0,
      kind: summary.kinds?.[index] ?? "digital",
      widgets: [], // the server keeps the form map; edits are checked there
      imageUrl: (dpi) => imageUrl(index, dpi),
      render: async (_canvas, scale) => imageUrl(index, Math.min(MAX_DPI, Math.max(MIN_DPI, scale * 72))),
    }));
    return { id, name: summary.name, pageCount: summary.page_count, kinds: summary.kinds ?? [], pages, destroy: async () => {} };
  }

  /** Start a job; returns its handle. */
  async function startJob(body) {
    const { id } = await call("POST", "/api/jobs", { json: body });
    return jobHandle(id, body.kind);
  }

  function jobHandle(id, kind) {
    const handle = {
      id,
      kind,
      /** Called with the job after every poll; null for none. */
      onUpdate: null,
      /** The job as the server reports it now. */
      poll: () => call("GET", `/api/jobs/${id}`),
      /** Ask the job to stop at its next step; the job (still running until it does). */
      cancel: () => call("POST", `/api/jobs/${id}/cancel`, { json: {} }),
    };
    handle.done = (async () => {
      let failures = 0;
      for (;;) {
        let job;
        try {
          job = await handle.poll();
          failures = 0;
        } catch (e) {
          if (++failures >= POLL_FAILURES) throw e;
        }
        if (job !== undefined) {
          try {
            handle.onUpdate?.(job);
          } catch (e) {
            queueMicrotask(() => {
              throw e; // a listener's bug reaches the page's error handler, not the poll loop
            });
          }
          if (job.status !== "running") return job;
        }
        await new Promise((resolve) => setTimeout(resolve, pollMs));
      }
    })();
    handle.done.catch(() => {}); // a caller that only polls must not see an unhandled rejection
    return handle;
  }

  const fingerprintOption = (checkFingerprint) => (typeof checkFingerprint === "boolean" ? { check_fingerprint: checkFingerprint } : {});

  const engine = {
    capabilities: {
      detectLive: false, // the server engine has no live detector: it seeds from the form map
      ai: flag("ai"),
      matches: flag("matches"),
      store: flag("store"),
      records: flag("records"),
      scanned: flag("scanned"),
    },

    /**
     * Open a PDF on the server: a demo form by name (see `forms`), or the bytes of one.
     * @param {string | Blob | Uint8Array | ArrayBuffer} source
     * @param {{name?: string}} [opts] the file name shown for uploaded bytes
     */
    async open(source, { name } = {}) {
      if (typeof source === "string") return makeDoc(await call("POST", "/api/documents", { json: { form: source } }));
      if (isObject(source) && typeof source.template === "string") {
        return makeDoc(await call("POST", "/api/documents", { json: { template: source.template }, timeout: uploadTimeoutMs }));
      }
      const blob = source instanceof Blob ? source : new Blob([source], { type: "application/pdf" });
      const form = new FormData();
      form.append("file", blob, name ?? source.name ?? "document.pdf");
      return makeDoc(await call("POST", "/api/documents", { form, timeout: uploadTimeoutMs }));
    },

    /** The demo forms the server has: `[{name, size, schema}]`. */
    async forms() {
      return (await call("GET", "/api/forms")).forms;
    },

    /**
     * The starting template: a field per form widget and per detector candidate.
     * @param {object} doc a ServerDoc
     * @param {object | string} schema a JSON Schema, or the name of a stored one
     */
    async seed(doc, schema) {
      return (await call("POST", "/api/seed", { json: { doc: doc.id, schema }, timeout: formMapTimeoutMs })).template;
    },

    /**
     * One edit op, checked on the server against the PDF when the template belongs to it. Always
     * resolves `{template, warnings}` or `{error}` (a rejected op, a bad template, an unreachable
     * server: each a plain message).
     * @param {object} template
     * @param {object} op
     * @param {"human" | "agent"} [author]
     * @param {{doc?: object | null, note?: string}} [opts]
     */
    async apply(template, op, author = "human", { doc = null, note = "" } = {}) {
      const body = { template, op, author, note };
      if (doc !== null) body.doc = doc.id;
      try {
        // checked against the PDF, the edit may wait for the server's first reading of it
        const timeout = doc !== null ? formMapTimeoutMs : timeoutMs;
        const { template: updated, warnings } = await call("POST", "/api/apply", { json: body, timeout });
        return { template: updated, warnings };
      } catch (e) {
        if (e instanceof EngineError) return { error: e.message };
        throw e;
      }
    },

    /**
     * Fill `doc` with one record: an object, or `{schema, id}` naming one in the record source.
     * @param {object} template
     * @param {object} doc
     * @param {object} record
     * @param {{checkFingerprint?: boolean, recordRef?: {schema: string, id: string}}} [opts]
     *   `checkFingerprint: false` fills a PDF the template was not made for (default: the server checks)
     * @returns {Promise<{pdfBytes: Uint8Array, report: object, note: string | null}>} `note` says
     *   why the fingerprint check was skipped, if it was
     */
    async fill(template, doc, record, { checkFingerprint, recordRef } = {}) {
      const body = { template, doc: doc.id, ...recordBody(record, recordRef), ...fingerprintOption(checkFingerprint) };
      const out = await call("POST", "/api/fill", { json: body });
      return { pdfBytes: fromBase64(out.pdf_base64), report: out.report, note: out.fingerprint_note ?? null };
    },

    /**
     * The PDF with an empty form field for every template field it lacks.
     * @param {object} template
     * @param {object} doc
     * @param {{checkFingerprint?: boolean, withNote?: boolean}} [opts] `withNote: true` resolves
     *   `{pdfBytes, note}` instead of the bytes alone, `note` saying why the fingerprint check was
     *   skipped (or null), as for `fill`
     * @returns {Promise<Uint8Array | {pdfBytes: Uint8Array, note: string | null}>}
     */
    async smartPdf(template, doc, { checkFingerprint, withNote = false } = {}) {
      const out = await call("POST", "/api/smart-pdf", { json: { template, doc: doc.id, ...fingerprintOption(checkFingerprint) } });
      const pdfBytes = fromBase64(out.pdf_base64);
      return withNote ? { pdfBytes, note: out.fingerprint_note ?? null } : pdfBytes;
    },

    /** The template carrying this PDF's fingerprint, page kinds and signature. */
    async rebind(template, doc) {
      return (await call("POST", "/api/rebind", { json: { template, doc: doc.id } })).template;
    },

    /**
     * Start an AI build, or an adapt when `startFrom` (a template for another PDF) is given.
     * @param {object} doc
     * @param {object | string} schema a JSON Schema or the name of a stored one (an adapt uses the template's own)
     * @param {{startFrom?: object | null}} [opts]
     * @returns {Promise<JobHandle>}
     */
    async build(doc, schema, { startFrom = null } = {}) {
      if (startFrom !== null) return startJob({ kind: "adapt", doc: doc.id, template: startFrom });
      return startJob({ kind: "build", doc: doc.id, schema });
    },

    /**
     * Start an AI revision of `template` from a plain instruction.
     * @param {object} template
     * @param {object} doc
     * @param {string} instruction
     * @param {{page: number, rect: number[]} | null} [region] PDF points, origin top-left
     * @returns {Promise<JobHandle>}
     */
    async revise(template, doc, instruction, region = null) {
      const body = { kind: "revise", doc: doc.id, template, instruction };
      if (region !== null) body.region = region;
      return startJob(body);
    },

    /**
     * Start a one-shot job: design the schema (unless `schema` or `template` is given), map the boxes
     * (unless `template` is given) and map the data (when `data`, a dataset id, is given).
     * @param {object} doc
     * @param {{schema?: object | string | null, template?: object | null, data?: string | null,
     *   saved?: string | null, check?: boolean, instructions?: string}} [opts] `saved`: a stored template
     *   whose saved data mappings are tried first; `check: false` skips the agent's check of the page pass
     * @returns {Promise<JobHandle>}
     */
    async oneshot(doc, { schema = null, template = null, data = null, saved = null, check = true, instructions = "" } = {}) {
      const body = { kind: "oneshot", doc: doc.id, check, instructions };
      if (template !== null) body.template = template;
      else if (schema !== null) body.schema = schema;
      if (data !== null) body.data = data;
      if (saved !== null) body.saved = saved;
      return startJob(body);
    },

    data: {
      /** A table file (CSV, TSV, XLSX, JSON); resolves its summary {id, name, kind, rows, columns, preview, ...}. */
      async file(file) {
        const form = new FormData();
        form.append("file", file, file.name ?? "data");
        return call("POST", "/api/data", { form, timeout: uploadTimeoutMs });
      },
      /** Pasted text: a table when it is JSON records or a delimited table, else free text. */
      text: (text) => call("POST", "/api/data", { json: { text } }),
    },

    /** Stored templates scored against `doc`, best first: `[{name, template_id, score, basis, explanation, action}]`. */
    async matches(doc) {
      return call("GET", `/api/documents/${doc.id}/matches`, { timeout: formMapTimeoutMs }); // may wait behind the form map
    },

    templates: {
      /** `[{name, ...}]`, as the store lists them. */
      list: async () => (await call("GET", "/api/templates")).templates,
      get: async (name) => (await call("GET", `/api/templates/${encodeURIComponent(name)}`)).template,
      /**
       * Save `template`; with `doc` the store keeps that PDF too, with `mapping` that data mapping.
       * @returns {Promise<{name: string, template_id: string}>}
       */
      put: (name, template, { doc = null, mapping = null } = {}) => {
        const body = { template };
        if (doc !== null) body.doc = doc.id;
        if (mapping !== null) body.mapping = mapping;
        return call("PUT", `/api/templates/${encodeURIComponent(name)}`, { json: body });
      },
      /** The URL of a saved template's data schema (a JSON file). */
      schemaUrl: (name) => `${root}/api/templates/${encodeURIComponent(name)}/schema`,
      /** @returns {Promise<string>} the deleted name */
      delete: async (name) => (await call("DELETE", `/api/templates/${encodeURIComponent(name)}`, { json: {} })).deleted,
    },

    records: {
      /** @returns {Promise<Record<string, object>>} schema name -> JSON Schema */
      schemas: async () => (await call("GET", "/api/schemas")).schemas,
      list: async (schema) => (await call("GET", `/api/records?schema=${encodeURIComponent(schema)}`)).records,
      get: async (schema, id) => (await call("GET", `/api/records/${encodeURIComponent(schema)}/${encodeURIComponent(id)}`)).record,
    },
  };
  return assertEngine(engine);
}

/**
 * @typedef {object} JobHandle
 * @property {string} id
 * @property {"build" | "adapt" | "revise" | "oneshot"} kind
 * @property {() => Promise<object>} poll the job now: {id, kind, status, steps, template, summary, error, unplaceable, started,
 *   ended, stages, result, usage}
 * @property {() => Promise<object>} cancel
 * @property {Promise<object>} done the job once it is done, cancelled or error
 * @property {((job: object) => void) | null} onUpdate set it to see the job after every poll of `done`
 */
