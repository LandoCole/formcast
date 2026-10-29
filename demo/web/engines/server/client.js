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
// Reading the form. The server reads a PDF (its form map) on its own as soon as it is opened. `readProgress(doc)`
// says how that is going without waiting (phase, the page and step under way, each page's state and boxes), so
// the page can show it and ask for the seed only once it is done; `waitForReading(doc, onUpdate)` polls it until
// then. `open(pdf, {detect: "manual"})` reads it with no detection, and `detectPage(doc, n)` then searches page n
// alone (or one skipped as prose) and resolves the new fields for its boxes. Both exist only when the server
// says so (its `progress` and `detectPage` capabilities).
//
// Extraction. When the server says `extract`, `engine.extract` holds its calls (the browser engine has none): a
// batch of PDFs (`createBatch`, `addFiles`, which sends the files in requests of at most UPLOAD_CHUNK_BYTES so no
// request passes the server's body limit, and skips and names a file over a lower limit the server has, `batch`,
// `files`, `pickSample`, `pageUrl`), a setup built from a sample of
// it (`startSetup` and `startLine` are jobs; `setup`, `reopen` of a saved extraction template, `edit`, `learn`,
// `marks`, `samplePageUrl`), the saved extraction templates (`save`, `list`, `extraction`, `remove`) and runs
// (`startRun` is a job; `runs`, `openRun`, `run`, `problems`, `setAside`, `decide`, `runPageUrl`, `exportUrl`,
// `exportRun`, which downloads the export, `toData`). They are the
// server's routes (demo/server/extract_api.py) one for one, and fail with EngineError as every call does: a setup or
// run the server no longer has (it restarted) is `not_found`, an edit while a line job runs on the setup
// `busy_setup`, and so on.
//
// Jobs (`build`, `revise`, `oneshot`, and extraction's `startSetup`, `startLine`, `startRun`) return a JobHandle {id, kind, poll(), cancel(), done, onUpdate}. `done`
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
/** The most file bytes `extract.addFiles` puts in one request (the server takes 50 MiB a request by default). */
export const UPLOAD_CHUNK_BYTES = 40 * 2 ** 20;
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

/** `?a=1&b=2` from the entries whose value is not null, undefined or "" (each encoded); "" when none is left. */
function query(params) {
  const pairs = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return pairs.length ? `?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")}` : "";
}

/**
 * `files` in order, in runs whose sizes add up to at most `limit` bytes; a file larger than that goes alone.
 * @param {Blob[]} files
 * @param {number} limit
 * @returns {Blob[][]}
 */
function chunks(files, limit) {
  const out = [];
  let run = [];
  let bytes = 0;
  for (const file of files) {
    if (run.length && bytes + file.size > limit) {
      out.push(run);
      run = [];
      bytes = 0;
    }
    run.push(file);
    bytes += file.size;
  }
  if (run.length) out.push(run);
  return out;
}

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

/** The file name a Content-Disposition header gives (`attachment; filename="a b.xlsx"`), or "download". */
function attachmentName(header) {
  const m = /filename="([^"]*)"/.exec(header ?? "") ?? /filename=([^;\s]+)/.exec(header ?? "");
  return m?.[1] || "download";
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
   * JSON of a 2xx (with `file`, the body as `{bytes, type, filename}`, the name from its
   * Content-Disposition); throws EngineError otherwise.
   */
  async function call(method, path, { json, form, timeout = timeoutMs, file = false } = {}) {
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
      if (file && res.ok) {
        const bytes = new Uint8Array(await res.arrayBuffer());
        return { bytes, type: res.headers.get("Content-Type") ?? "", filename: attachmentName(res.headers.get("Content-Disposition")) };
      }
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
    return {
      id, name: summary.name, pageCount: summary.page_count, kinds: summary.kinds ?? [], pages,
      detect: summary.detect ?? "auto", destroy: async () => {},
    };  // prettier-ignore
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

  /** The extraction calls (see the top); ids and names are encoded into the paths. */
  function extractCalls() {
    const batchPath = (id) => `/api/batches/${encodeURIComponent(id)}`;
    const setupPath = (id) => `/api/setups/${encodeURIComponent(id)}`;
    const runPath = (id) => `/api/runs/${encodeURIComponent(id)}`;
    const savedPath = (name) => `/api/extractions/${encodeURIComponent(name)}`;
    return {
      /**
       * A new batch: `{name?}` an empty one for uploads, or `{folder, name?}` the PDFs in a folder on the server's
       * machine, scanning. Resolves the Batch `{id, name, count, bytes, skipped, folder, scan}`.
       * @param {{name?: string, folder?: string}} [body]
       */
      createBatch: (body = {}) => call("POST", "/api/batches", { json: body, timeout: formMapTimeoutMs }), // a folder is listed first

      /**
       * Add PDFs and zips of PDFs to a batch, in requests of at most UPLOAD_CHUNK_BYTES (a file larger than that
       * goes alone). A request the server finds too large (413 `too_large`: its limit may be lower than
       * UPLOAD_CHUNK_BYTES) is sent again a file at a time, and a file too large on its own is skipped. Resolves
       * `{batch, tooLarge}`: the Batch as the last request left it (as it is, when no file was taken) and the names
       * of the files skipped as too large. Rejects at any other refusal, the files sent before it staying in the
       * batch. `onProgress({done, total, bytes, totalBytes, batch, tooLarge})` after each file or request taken or
       * skipped: `done` of `total` files and `bytes` of `totalBytes` dealt with so far.
       * @param {string} batchId
       * @param {File[]} files
       * @param {{onProgress?: (progress: object) => void}} [opts]
       * @returns {Promise<{batch: object, tooLarge: string[]}>}
       */
      async addFiles(batchId, files, { onProgress } = {}) {
        const list = [...files];
        if (list.length === 0) throw new EngineError("bad_upload", "Choose at least one PDF or .zip file.");
        const totalBytes = list.reduce((n, f) => n + f.size, 0);
        const tooLarge = [];
        let done = 0;
        let bytes = 0;
        let batch = null;
        const name = (file) => file.name ?? "document.pdf";
        /** Send `run` in one request; false when the server found it too large (nothing of it was added). */
        const send = async (run) => {
          const form = new FormData();
          for (const file of run) form.append("file", file, name(file));
          try {
            batch = await call("POST", `${batchPath(batchId)}/files`, { form, timeout: uploadTimeoutMs });
            return true;
          } catch (e) {
            if (e instanceof EngineError && e.code === "too_large") return false;
            throw e;
          }
        };
        const dealt = (run) => {
          done += run.length;
          bytes += run.reduce((n, f) => n + f.size, 0);
          onProgress?.({ done, total: list.length, bytes, totalBytes, batch, tooLarge: [...tooLarge] });
        };
        for (const run of chunks(list, UPLOAD_CHUNK_BYTES)) {
          if (await send(run)) {
            dealt(run);
            continue;
          }
          for (const file of run) {
            if (run.length === 1 || !(await send([file]))) tooLarge.push(name(file));
            dealt([file]);
          }
        }
        return { batch: batch ?? (await call("GET", batchPath(batchId))), tooLarge };
      },

      /** The Batch now, its scan's progress included. */
      batch: (id) => call("GET", batchPath(id)),
      /** A page of the batch's files: `{total, files: [{index, name, size, group}]}` (`limit` 1 to 200). */
      files: (id, offset = 0, limit = 50) => call("GET", `${batchPath(id)}/files${query({ offset, limit })}`),
      /**
       * The documents to build a template from: `[{index, name, group}]`. 409 `scanning` until the scan is done.
       * @param {string} id
       * @param {{n?: number, how?: "spread" | "first" | "pick", picked?: number[]}} [opts]
       */
      pickSample: async (id, { n = 5, how = "spread", picked } = {}) => {
        const body = picked === undefined ? { n, how } : { n, how, picked };
        return (await call("POST", `${batchPath(id)}/sample`, { json: body })).samples;
      },
      /** The URL of page `page` (from 0) of the batch's file `index`, as a PNG. */
      pageUrl: (batchId, index, page, dpi = DEFAULT_DPI) => `${root}${batchPath(batchId)}/files/${index}/pages/${page}.png?dpi=${Math.round(dpi)}`,

      /**
       * Start building a template from samples of a batch: `{batch, samples: [index], spec: {name, start, lines,
       * destination, detect, pages, ocr, sample_how}}`. The done job's `result.setup` is the Setup.
       * @param {{batch: string, samples: number[], spec: object}} body
       * @returns {Promise<JobHandle>}
       */
      startSetup: (body) => startJob({ kind: "extract-setup", ...body }),
      /** The Setup `{id, batch, template, proof, warnings, samples: [{index, name, kind, pages}]}`. */
      setup: (id) => call("GET", setupPath(id)),
      /** A saved extraction template opened as a new Setup: its samples read again and proven (no model call). */
      reopen: (name) => call("POST", "/api/setups", { json: { extraction: name }, timeout: formMapTimeoutMs }),
      /**
       * One edit of the setup's template, sent as given (a box drawn on a sample names it: `sample`); resolves the
       * Setup proven again. A refused edit rejects with code `bad_edit` and the library's message.
       * @param {string} id
       * @param {object} edit
       */
      edit: (id, edit) => call("POST", `${setupPath(id)}/edit`, { json: { edit }, timeout: formMapTimeoutMs }),
      /** Accept the wordings the proof suggests for `path` into its reader's map; resolves the Setup proven again. */
      learn: (id, path) => call("POST", `${setupPath(id)}/learn`, { json: { path }, timeout: formMapTimeoutMs }),
      /** The marks of the setup's sample `sample` (from 0): `[{path, kind, page, rect, ...}]`, in that sample's frame. */
      marks: async (id, sample) => (await call("GET", `${setupPath(id)}/samples/${sample}/marks`)).marks,
      /** The URL of page `page` of the setup's sample `sample`, as a PNG. */
      samplePageUrl: (id, sample, page, dpi = DEFAULT_DPI) => `${root}${setupPath(id)}/samples/${sample}/pages/${page}.png?dpi=${Math.round(dpi)}`,
      /**
       * Start adding the fields of one more line ("wire details") to a setup; the done job's `result` is
       * `{setup, paths}`, `paths` the ones added.
       * @returns {Promise<JobHandle>}
       */
      startLine: (id, line) => startJob({ kind: "extract-line", setup: id, line }),

      /** Save the setup's template as `name`, with its sample PDFs: `{name, template_id}`. */
      save: (name, setupId) => call("PUT", savedPath(name), { json: { setup: setupId } }),
      /** The saved extraction templates: `[{name, template_id, fields, samples, updated}]`. */
      list: async () => (await call("GET", "/api/extractions")).extractions,
      /** The saved extraction template `name` (its `setup.destination` names the fill template it was made for). */
      extraction: async (name) => (await call("GET", savedPath(name))).template,
      /** @returns {Promise<string>} the deleted name */
      remove: async (name) => (await call("DELETE", savedPath(name), { json: {} })).name,

      /**
       * Start reading every file of a batch with a saved extraction template; `ai` also asks the model about the
       * documents whose required fields still fail. The done job's `result` is `{run, done, total, summary}`.
       * @param {{extraction: string, batch: string, ai?: boolean}} opts
       * @returns {Promise<JobHandle>}
       */
      startRun: ({ extraction, batch, ai = false }) => startJob({ kind: "extract-run", extraction, batch, ai }),
      /** The runs the server knows: `[{id, name, extraction, started, summary}]`. */
      runs: async () => (await call("GET", "/api/runs")).runs,
      /** A command line run's output folder on the server's machine, opened: the Run. */
      openRun: (folder) => call("POST", "/api/runs", { json: { folder }, timeout: formMapTimeoutMs }),
      /** The Run `{id, name, extraction, summary, fields, seconds}`. */
      run: (id) => call("GET", runPath(id)),
      /**
       * A page of the documents that still have problems: `{total, documents: [{doc_id, name, problems}]}`.
       * @param {string} id
       * @param {{path?: string, status?: "missing" | "failed_check" | "unsure", offset?: number, limit?: number}} [opts]
       */
      problems: (id, { path, status, offset = 0, limit = 50 } = {}) => call("GET", `${runPath(id)}/problems${query({ path, status, offset, limit })}`),
      /**
       * A page of the documents set aside: `{total, documents: [{doc_id, name, reason, closest_template, closest_score}]}`.
       * @param {string} id
       * @param {{offset?: number, limit?: number}} [opts]
       */
      setAside: (id, { offset = 0, limit = 50 } = {}) => call("GET", `${runPath(id)}/set-aside${query({ offset, limit })}`),
      /**
       * Record a review decision `{doc_id, path, decision: "value" | "accept" | "not_in_document", value?}`; the Run.
       * @param {string} id
       * @param {object} decision
       */
      decide: (id, decision) => call("POST", `${runPath(id)}/decisions`, { json: decision }),
      /** The URL of page `page` (from 0) of the run's document `docId`, as a PNG. */
      runPageUrl: (id, docId, page, dpi = DEFAULT_DPI) => `${root}${runPath(id)}/pages.png${query({ doc: docId, page, dpi: Math.round(dpi) })}`,
      /** The URL that downloads the run's results: `xlsx`, or a zip of the `parquet` or `csv` files. */
      exportUrl: (id, format = "xlsx") => `${root}${runPath(id)}/export${query({ format })}`,
      /**
       * The run's results downloaded: `{bytes, type, filename}` (the .xlsx, or a zip of the Parquet or CSV files). A
       * format whose extra the server lacks (Parquet without pyarrow) rejects with code `not_supported`.
       * @param {string} id
       * @param {"xlsx" | "parquet" | "csv"} [format]
       */
      exportRun: (id, format = "xlsx") => call("GET", `${runPath(id)}/export${query({ format })}`, { file: true, timeout: formMapTimeoutMs }),
      /** The run's read documents as a dataset for the fill flow: `{dataset, truncated, with_problems}`. */
      toData: (id) => call("POST", `${runPath(id)}/to-data`, { json: {}, timeout: formMapTimeoutMs }),
    };
  }

  const fingerprintOption =(checkFingerprint) => (typeof checkFingerprint === "boolean" ? { check_fingerprint: checkFingerprint } : {});

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
     * @param {{name?: string, detect?: "auto" | "manual"}} [opts] `name`: the file name shown for uploaded
     *   bytes; `detect: "manual"` reads the PDF with no detection (see `detectPage`)
     */
    async open(source, { name, detect = "auto" } = {}) {
      const url = detect === "manual" ? "/api/documents?detect=manual" : "/api/documents";
      if (typeof source === "string") return makeDoc(await call("POST", url, { json: { form: source } }));
      if (isObject(source) && typeof source.template === "string") {
        return makeDoc(await call("POST", url, { json: { template: source.template }, timeout: uploadTimeoutMs }));
      }
      const blob = source instanceof Blob ? source : new Blob([source], { type: "application/pdf" });
      const form = new FormData();
      form.append("file", blob, name ?? source.name ?? "document.pdf");
      return makeDoc(await call("POST", url, { form, timeout: uploadTimeoutMs }));
    },

    /**
     * How the server's reading of `doc` is going, without waiting: `{phase, detect, page_count, pages_done,
     * current: {page, step, detectors} | null, pages: [{state, kind, boxes, widgets, skipped, ocr, seconds}],
     * boxes, elapsed, error}`. `phase` ends "done" or "error".
     */
    readProgress: flag("progress") ? (doc) => call("GET", `/api/documents/${doc.id}/progress`) : undefined,

    /**
     * Poll `readProgress` until the reading is done; `onUpdate(progress)` after every poll. Resolves the last
     * progress; rejects with the server's message when the reading failed.
     */
    waitForReading: flag("progress")
      ? async (doc, onUpdate = () => {}, { everyMs = 600 } = {}) => {
          let failures = 0;
          for (;;) {
            let progress;
            try {
              progress = await call("GET", `/api/documents/${doc.id}/progress`);
              failures = 0;
            } catch (e) {
              if (++failures >= POLL_FAILURES) throw e;
            }
            if (progress !== undefined) {
              onUpdate(progress);
              if (progress.phase === "done") return progress;
              if (progress.phase === "error") throw new EngineError("bad_pdf", progress.error ?? MESSAGES.server);
            }
            await new Promise((resolve) => setTimeout(resolve, everyMs));
          }
        }
      : undefined,

    /**
     * Search page `index` of `doc` for blanks: `{page, detected, fields, boxes}`, `fields` the new unmapped
     * fields for the boxes found (empty when the page was searched already, or is fillable).
     */
    detectPage: flag("detectPage")
      ? (doc, index) => call("POST", `/api/documents/${doc.id}/pages/${index}/detect`, { json: {}, timeout: formMapTimeoutMs })
      : undefined,

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

    extract: flag("extract") ? extractCalls() : undefined,
  };
  return assertEngine(engine);
}

/**
 * @typedef {object} JobHandle
 * @property {string} id
 * @property {"build" | "adapt" | "revise" | "oneshot" | "extract-setup" | "extract-line" | "extract-run"} kind
 * @property {() => Promise<object>} poll the job now: {id, kind, status, steps, template, summary, error, unplaceable, started,
 *   ended, stages, result, usage}
 * @property {() => Promise<object>} cancel
 * @property {Promise<object>} done the job once it is done, cancelled or error
 * @property {((job: object) => void) | null} onUpdate set it to see the job after every poll of `done`
 */
