// The detection worker: downloads FFDNet from Hugging Face (kept in the Cache API under its URL),
// runs it with onnxruntime-web (WebGPU when the browser offers it, else WebAssembly) and answers
// detect requests with boxes normalized to the page render. Pre- and post-processing: ffdnet.js.
//
// Messages in:  {id, type: "load", url, bytes}  and  {id, type: "detect", rgba, width, height}
// Messages out: {id, type: "progress", loaded, total}, then {id, ok: true, ...} or {id, ok: false, message}
// Requests are handled one at a time, in the order they arrive (serial.js).

import * as ort from "../../vendor/onnxruntime-web/ort.webgpu.min.mjs";
import { IMAGE_SIZE, decode, letterbox } from "./ffdnet.js";
import { serial } from "./serial.js";

ort.env.wasm.wasmPaths = new URL("../../vendor/onnxruntime-web/", import.meta.url).href;
ort.env.wasm.proxy = false; // already off the main thread
ort.env.logLevel = "error";

const CACHE_NAME = "formcast-models";
const PROGRESS_STEP = 1 << 20; // report at most once per MiB

/** @type {{session: any, provider: "webgpu" | "wasm", model: Uint8Array | null} | null} */
let state = null;

async function openCache() {
  try {
    return typeof caches === "undefined" ? null : await caches.open(CACHE_NAME);
  } catch {
    return null; // no Cache API here (private window, blocked storage): download every time
  }
}

/** The model file, from the cache when it holds a complete copy, else downloaded with progress. */
async function fetchModel(id, url, expected) {
  const cache = await openCache();
  if (cache) {
    const hit = await cache.match(url).catch(() => undefined);
    const buf = hit ? new Uint8Array(await hit.arrayBuffer().catch(() => new ArrayBuffer(0))) : null;
    if (buf) {
      if (buf.length === expected) {
        postMessage({ id, type: "progress", loaded: buf.length, total: buf.length });
        return buf;
      }
      await cache.delete(url).catch(() => false);
    }
  }
  let res;
  try {
    res = await fetch(url);
  } catch (e) {
    throw plain("The detection model could not be downloaded. Check the connection and try again.", e);
  }
  if (!res.ok || !res.body) throw new Error(`The detection model could not be downloaded (HTTP ${res.status}).`);
  const total = Number(res.headers.get("content-length")) || expected;
  const reader = res.body.getReader();
  const chunks = [];
  let loaded = 0;
  let reported = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.length;
      if (loaded - reported >= PROGRESS_STEP) {
        postMessage({ id, type: "progress", loaded, total });
        reported = loaded;
      }
    }
  } catch (e) {
    throw plain("The detection model download was interrupted. Check the connection and try again.", e);
  }
  if (loaded !== expected) throw new Error("The detection model download was incomplete. Try again.");
  const buf = new Uint8Array(loaded);
  let at = 0;
  for (const c of chunks) {
    buf.set(c, at);
    at += c.length;
  }
  postMessage({ id, type: "progress", loaded, total: loaded });
  if (cache) await cache.put(url, new Response(buf, { headers: { "content-type": "application/octet-stream" } })).catch(() => {});
  return buf;
}

async function webgpuAvailable() {
  try {
    return Boolean(navigator.gpu && (await navigator.gpu.requestAdapter()));
  } catch {
    return false;
  }
}

/** A plain error for the visitor, keeping the technical one as `cause`. */
function plain(message, cause) {
  return new Error(message, { cause });
}

/**
 * A session on WebGPU when the browser has an adapter and the model runs there, else on
 * WebAssembly. A WebGPU session keeps the model bytes, to start over on WebAssembly if it fails.
 */
async function createSession(model) {
  if (await webgpuAvailable()) {
    try {
      const session = await ort.InferenceSession.create(model, { executionProviders: ["webgpu"] });
      return { session, provider: "webgpu", model };
    } catch {
      // fall through to WebAssembly
    }
  }
  try {
    const session = await ort.InferenceSession.create(model, { executionProviders: ["wasm"] });
    return { session, provider: "wasm", model: null };
  } catch (e) {
    throw plain("The detection model could not start in this browser.", e);
  }
}

async function run(tensor) {
  const input = new ort.Tensor("float32", tensor, [1, 3, IMAGE_SIZE, IMAGE_SIZE]);
  const feeds = { [state.session.inputNames[0]]: input };
  let out;
  try {
    out = await state.session.run(feeds);
  } catch (e) {
    if (state.provider !== "webgpu") throw e;
    // a WebGPU session that fails at run time: redo it on WebAssembly once
    await state.session.release().catch(() => {});
    const session = await ort.InferenceSession.create(state.model, { executionProviders: ["wasm"] });
    state = { session, provider: "wasm", model: null };
    out = await state.session.run({ [state.session.inputNames[0]]: input });
  }
  const output = out[state.session.outputNames[0]];
  const data = /** @type {Float32Array} */ (await output.getData());
  return { data, anchors: output.dims[2] };
}

async function handle(msg) {
  if (msg.type === "load") {
    if (state === null) state = await createSession(await fetchModel(msg.id, msg.url, msg.bytes));
    return { provider: state.provider };
  }
  if (msg.type === "detect") {
    if (state === null) throw new Error("The detection model is not loaded.");
    const started = performance.now();
    const { tensor, letterbox: lb } = letterbox(new Uint8ClampedArray(msg.rgba), msg.width, msg.height);
    let out;
    try {
      out = await run(tensor);
    } catch (e) {
      throw plain("Detection failed on this page.", e);
    }
    const { data, anchors } = out;
    const detections = decode(data, anchors, lb);
    return { detections, provider: state.provider, ms: performance.now() - started };
  }
  throw new Error(`unknown request ${msg.type}`);
}

// One message at a time: onnxruntime-web does not support overlapping runs on one session.
const handleInOrder = serial(handle);

self.onmessage = async (event) => {
  const msg = event.data;
  try {
    postMessage({ id: msg.id, ok: true, ...(await handleInOrder(msg)) });
  } catch (e) {
    const detail = e instanceof Error && e.cause ? String(e.cause?.message ?? e.cause) : undefined;
    postMessage({ id: msg.id, ok: false, message: e instanceof Error ? e.message : String(e), detail });
  }
};
