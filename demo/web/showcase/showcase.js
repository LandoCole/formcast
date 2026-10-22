// The formcast showcase (layout C) on the browser engine. A thin wiring layer: it creates the
// engine, mounts the page view, keeps the session (document, template and its undo stack) and hands
// the three steps to their modules (detect.js, link.js, fill.js), which call back through `app`.
//
// Test hooks: `?samples=<dir>/` (the folder holding index.json), `?worker=<url>` (a stand-in
// detection worker) and `?timeout=<ms>` (the sample fetch time limit) are honoured only when the
// page is served from localhost or 127.0.0.1.

import { createBus } from "../core/bus.js";
import { makeView, viewSize } from "../core/geometry.js";
import { PageView } from "../core/pageview.js";
import { flattenSchema } from "../core/schema.js";
import { remember } from "../core/store.js";
import { UndoStack } from "../core/undo.js";
import { createBrowserEngine } from "../engines/browser/engine.js";
import { mountDetect } from "./detect.js";
import { mountFill } from "./fill.js";
import { mountLink } from "./link.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const LOCAL = location.hostname === "localhost" || location.hostname === "127.0.0.1";

/** A test override from the query string, as a URL; null when absent or not on localhost. */
function override(name) {
  const value = params.get(name);
  return LOCAL && value ? new URL(value, location.href) : null;
}

/** A touch screen on a narrow viewport: tap-to-link, no drag, the small model only. */
function detectTouch() {
  const coarse = matchMedia("(pointer: coarse)").matches || navigator.maxTouchPoints > 0;
  return coarse && matchMedia("(max-width: 760px)").matches;
}

/**
 * Whether live detection is off until the visitor confirms (spec §6): on touch phones, and where
 * the browser reports less than 4 GB of memory. Samples work fully either way.
 */
function detectLiveOff(touch) {
  const lowMemory = typeof navigator.deviceMemory === "number" && navigator.deviceMemory < 4;
  return touch || lowMemory;
}

const samplesBase = override("samples") ?? new URL("samples/", location.href);
if (!samplesBase.pathname.endsWith("/")) samplesBase.pathname += "/";
const workerUrl = override("worker") ?? undefined;
const timeoutParam = LOCAL ? Number(params.get("timeout")) : NaN;
/** Time limit for fetching sample files, in ms. */
const FETCH_TIMEOUT_MS = timeoutParam > 0 ? timeoutParam : 20_000;
const touch = detectTouch();

const bus = createBus();
const engine = createBrowserEngine({ workerUrl });
const view = new PageView($("pageview"), { bus });

/** Session state. Every template lives in the undo stack; `state.undo.current` is the one shown. */
const state = {
  doc: null,
  name: "",
  sample: null, // the open sample's files (see detect.js), or null for a visitor's own PDF
  variant: null, // "form" (fields stripped), "published" (as published) or "own"
  undo: null,
  page: 0,
  selected: null,
  compare: false,
  warnings: {}, // field id -> messages from the last edit
  token: 0, // bumps whenever the document changes, so late async results can be dropped
};
const sources = new WeakMap(); // template snapshot -> what its boxes are, for the caption
const hooks = [];
let schemaCache = { schema: null, paths: [] };
let step = "detect";

/** Show `text` in a message element, or hide it when `text` is empty. */
function say(el, text) {
  el.textContent = text;
  el.hidden = !text;
}

const app = {
  $,
  bus,
  engine,
  view,
  state,
  samplesBase,
  touch, // tap to link, no drag, the model choice hidden (FFDNet-S)
  liveOff: detectLiveOff(touch), // live detection waits for the visitor to confirm the download
  fetchTimeoutMs: FETCH_TIMEOUT_MS,
  say,

  /** The template on screen, or null before a document is open. */
  get template() {
    return state.undo?.current ?? null;
  },

  /** The current schema's flattened paths ([] when it can't be flattened). */
  schemaPaths() {
    const schema = app.template?.schema ?? null;
    if (schema !== schemaCache.schema) {
      let paths = [];
      try {
        paths = schema ? flattenSchema(schema) : [];
      } catch {
        paths = [];
      }
      schemaCache = { schema, paths };
    }
    return schemaCache.paths;
  },

  /** Register a function called after every render. */
  onRender(fn) {
    hooks.push(fn);
  },

  /**
   * Show a new document with its starting template; the undo history starts over.
   * @param {{doc: object, name: string, sample: object | null, variant: string, template: object,
   *   source: {kind: string, text: string}}} opts
   */
  setDocument({ doc, name, sample, variant, template, source }) {
    if (state.doc && state.doc !== doc) state.doc.destroy().catch(() => {});
    state.token += 1;
    Object.assign(state, { doc, name, sample, variant, page: 0, selected: null, warnings: {} });
    state.undo = new UndoStack(template);
    sources.set(state.undo.current, source);
    say($("op-msg"), "");
    view.select(null);
    showPage(0);
  },

  /**
   * Record a new template (an edit, new boxes, a schema change or a loaded template). Undo goes
   * back to the one before. `source` replaces the caption's description of the boxes.
   */
  commit(template, { source, warnings = {} } = {}) {
    const before = app.template;
    state.undo.push(template);
    sources.set(state.undo.current, source ?? sources.get(before));
    state.warnings = warnings;
    if (state.sample) remember(storageKey(), state.undo.current);
    render();
  },

  /** Apply one edit op from the page view or the inspector. */
  async apply(op) {
    const t = app.template;
    if (t === null) return;
    const token = state.token;
    let res;
    try {
      res = await engine.apply(t, op, "human");
    } catch (e) {
      res = { error: String(e?.message ?? e) };
    }
    if (token !== state.token) return;
    if (res.error !== undefined) {
      say($("op-msg"), `That change wasn't made: ${res.error}`);
      render(); // puts a dragged box back where the template has it
      return;
    }
    say($("op-msg"), "");
    const ids = new Set(t.fields.map((f) => f.id));
    const added = res.template.fields.find((f) => !ids.has(f.id));
    const touched = op.op === "remove_field" ? null : (added?.id ?? op.id ?? null);
    if (op.op === "remove_field" && state.selected === op.id) state.selected = null;
    if (added) state.selected = added.id;
    app.commit(res.template, { warnings: touched && res.warnings.length ? { [touched]: res.warnings } : {} });
    if (added) view.select(added.id);
  },

  /** Select a field (or none) from outside the page view, turning to its page. */
  select(id) {
    const field = app.template?.fields.find((f) => f.id === id) ?? null;
    state.selected = field ? id : null;
    const page = field ? (field.type === "table" ? field.segments[0]?.page : field.page) : undefined;
    if (page !== undefined && page !== state.page) showPage(page);
    else render();
    view.select(state.selected);
  },

  /** Put a record in the Fill step's text box. */
  setRecord(record) {
    $("record").value = `${JSON.stringify(record, null, 2)}\n`;
  },

  /** The localStorage key of the open sample's template. */
  storageKey,
  showPage,
  setStep,
  render,
};

function storageKey() {
  return `${state.sample.id}/${state.variant}`;
}

// ------------------------------------------------------------------ page and rendering

/** Pixels per point: on desktop the page fits the window's height (it stays put while the panel scrolls). */
function fitScale(page) {
  if (matchMedia("(max-width: 760px)").matches) return 2;
  const tall = viewSize(makeView({ width: page.width, height: page.height, rotation: page.rotation, scale: 1 })).height;
  return Math.min(2, Math.max(0.5, (innerHeight - 150) / tall));
}

function showPage(index) {
  const doc = state.doc;
  if (doc === null) return render();
  state.page = Math.max(0, Math.min(index, doc.pageCount - 1));
  const page = doc.pages[state.page];
  view.setScale(fitScale(page));
  view.setPage({
    index: page.index,
    width: page.width,
    height: page.height,
    rotation: page.rotation,
    render: (canvas, scale) => doc.render(page.index, canvas, scale),
  });
  render();
}

/** Outlines of the sample's original fields on the current page, when "show" is on. */
function compareRects() {
  if (!state.compare || state.sample === null || state.variant === "own") return [];
  return state.sample.originals.pages[String(state.page)] ?? [];
}

/** Per-field messages for the page view: the last edit's warnings, and paths the schema lacks. */
function fieldWarnings(template) {
  const known = new Set(app.schemaPaths().map((sp) => sp.path));
  const out = {};
  for (const f of template.fields) {
    const missing = (f.source?.paths ?? []).filter((p) => !known.has(p));
    const msgs = [...(state.warnings[f.id] ?? []), ...missing.map((p) => `${p} is not in the schema`)];
    if (msgs.length) out[f.id] = msgs;
  }
  return out;
}

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

function render() {
  const t = app.template;
  const open = state.doc !== null && t !== null;
  $("pageview").hidden = !open;
  $("stage-empty").hidden = open;
  $("pager").hidden = !open || state.doc.pageCount < 2;
  if (open) {
    if (state.selected !== null && !t.fields.some((f) => f.id === state.selected)) {
      state.selected = null;
      view.select(null);
    }
    view.setTemplate(t, { compare: compareRects(), warnings: fieldWarnings(t) });
    $("page-no").textContent = `Page ${state.page + 1} of ${state.doc.pageCount}`;
    $("prev-page").disabled = state.page === 0;
    $("next-page").disabled = state.page >= state.doc.pageCount - 1;
    const linked = t.fields.filter((f) => (f.source?.paths ?? []).length > 0).length;
    const source = sources.get(t)?.text ?? "";
    $("caption").textContent = `Fig. 1 · ${source ? `${source} · ` : ""}${plural(t.fields.length, "box", "boxes")} · ${linked} linked`;
  } else {
    $("caption").textContent = "Fig. 1 · No form open";
  }
  for (const fn of hooks) fn();
}

// ------------------------------------------------------------------ steps

const STEPS = ["detect", "link", "fill"];

function setStep(name) {
  step = name;
  for (const s of STEPS) {
    const tab = $(`tab-${s}`);
    const on = s === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`step-${s}`).hidden = !on;
  }
}

for (const s of STEPS) {
  const tab = $(`tab-${s}`);
  tab.addEventListener("click", () => setStep(s));
  tab.addEventListener("keydown", (e) => {
    const by = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!by) return;
    e.preventDefault();
    const next = STEPS[(STEPS.indexOf(s) + by + STEPS.length) % STEPS.length];
    setStep(next);
    $(`tab-${next}`).focus();
  });
}
for (const btn of document.querySelectorAll("[data-goto]")) {
  btn.addEventListener("click", () => {
    setStep(btn.dataset.goto);
    $(`tab-${btn.dataset.goto}`).scrollIntoView({ block: "nearest" });
  });
}

// ------------------------------------------------------------------ bus

bus.on("select", ({ id }) => {
  state.selected = id;
  if (id !== null && step === "detect") setStep("link");
  render();
});
bus.on("op", ({ op }) => app.apply(op));
bus.on("undo", () => {
  if (state.undo?.undo()) {
    state.warnings = {};
    if (state.sample) remember(storageKey(), state.undo.current);
    render();
  }
});
bus.on("redo", () => {
  if (state.undo?.redo()) {
    state.warnings = {};
    if (state.sample) remember(storageKey(), state.undo.current);
    render();
  }
});

$("prev-page").addEventListener("click", () => showPage(state.page - 1));
$("next-page").addEventListener("click", () => showPage(state.page + 1));

let resizeTimer = 0;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.doc) view.setScale(fitScale(state.doc.pages[state.page]));
  }, 150);
});

mountDetect(app);
mountLink(app);
mountFill(app);
render();
