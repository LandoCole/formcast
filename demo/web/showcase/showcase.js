// The formcast showcase (layout C) on the browser engine: a guided run of the three steps. Detect
// runs the detector over the blank form under a scan, Link's Auto map carries the AI agent's
// mapping for a sample onto the boxes, and Fill shows the filled PDF in place before it is
// downloaded. A thin wiring layer: it creates the engine and the session (core/session.js:
// document, template, undo, the ordered edit queue), mounts the page view and the scan, renders on
// the session's events and hands the three steps to their modules (detect.js, link.js, fill.js),
// which call back through `app`. The write-up's Results section renders from results.json
// (results.js).
//
// The filled preview. While Fill shows a filled PDF (`state.preview`), the page view draws that
// PDF's pages and hides the boxes. Any change to the template, another document, or leaving the
// Fill step ends the preview, so the page never shows a fill of an older template.
//
// Test hooks: `?samples=<dir>/` (the folder holding index.json), `?worker=<url>` (a stand-in
// detection worker) and `?timeout=<ms>` (every time limit below: fetching sample files, opening a
// visitor's PDF, filling) are honoured only when the page is served from localhost or 127.0.0.1.

import { createBus } from "../core/bus.js";
import { makeView, viewSize } from "../core/geometry.js";
import { PageView } from "../core/pageview.js";
import { schemaPathsOf } from "../core/schema.js";
import { fieldWarnings } from "../core/schemalist.js";
import { createSession } from "../core/session.js";
import { createBrowserEngine } from "../engines/browser/engine.js";
import { mountDetect } from "./detect.js";
import { mountFill } from "./fill.js";
import { mountLink } from "./link.js";
import { mountResults } from "./results.js";
import { createScan } from "./scan.js";

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
/** Time limits in ms: fetching a sample file, opening a visitor's PDF, filling or making a fillable PDF. */
const FETCH_TIMEOUT_MS = timeoutParam > 0 ? timeoutParam : 20_000;
const OPEN_TIMEOUT_MS = timeoutParam > 0 ? timeoutParam : 30_000;
const FILL_TIMEOUT_MS = timeoutParam > 0 ? timeoutParam : 60_000;
const touch = detectTouch();

const bus = createBus();
const engine = createBrowserEngine({ workerUrl });
const view = new PageView($("pageview"), { bus });
const scan = createScan($("pageview"));
const session = createSession({ engine, bus });

/** What the page shows besides the session's document and template. */
const state = {
  name: "",
  sample: null, // the open sample's files (see detect.js), or null for a visitor's own PDF
  page: 0,
  selected: null,
  compare: false,
  /** The filled PDF on show in the Fill step: {doc, bytes, title}, or null. */
  preview: null,
  /** The open document (the session's), or null. */
  get doc() {
    return session.doc;
  },
};
const hooks = [];
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
  session,
  view,
  scan,
  state,
  samplesBase,
  touch, // tap to link, no drag, the model choice hidden (FFDNet-S)
  liveOff: detectLiveOff(touch), // live detection waits for the visitor to confirm the download
  fetchTimeoutMs: FETCH_TIMEOUT_MS,
  openTimeoutMs: OPEN_TIMEOUT_MS,
  fillTimeoutMs: FILL_TIMEOUT_MS,
  say,

  /** The template on screen, or null before a document is open. */
  get template() {
    return session.template;
  },

  /** The step on show: "detect", "link" or "fill". */
  get step() {
    return step;
  },

  /** The current schema's flattened paths ([] when it can't be flattened). */
  schemaPaths() {
    return schemaPathsOf(app.template?.schema);
  },

  /** Register a function called after every render. */
  onRender(fn) {
    hooks.push(fn);
  },

  /**
   * Show a new document with its starting template; the undo history starts over.
   * @param {{doc: object, name: string, sample: object | null, template: object,
   *   source: {kind: string, text: string}}} opts
   * (New templates for the document on screen go through `app.session.replaceTemplate`.)
   */
  setDocument({ doc, name, sample, template, source }) {
    scan.stop();
    dropPreview();
    Object.assign(state, { name, sample, page: 0, selected: null });
    session.setDocument(doc, template, { source });
    say($("op-msg"), "");
    view.select(null);
    for (const fn of documentHooks) fn();
    showPage(0);
  },

  /** Register a function called when a new document is shown (before it is drawn). */
  onDocument(fn) {
    documentHooks.push(fn);
  },

  /**
   * Show a filled PDF in place of the form, boxes hidden; null goes back to the form.
   * @param {{doc: object, bytes: Uint8Array, title: string} | null} preview `doc` is the filled PDF
   *   opened by the engine (closed when the preview ends); `bytes` what Download saves
   */
  setPreview(preview) {
    if (preview === state.preview) return;
    dropPreview();
    state.preview = preview;
    showPage(state.page);
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

  showPage,
  setStep,
  render,
};
const documentHooks = [];

/** End the preview without redrawing (the caller does): its document is closed. */
function dropPreview() {
  const old = state.preview;
  state.preview = null;
  old?.doc.destroy().catch(() => {});
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
  const shown = state.preview?.doc ?? doc; // the filled PDF has the form's pages, filled in
  view.setScale(fitScale(page));
  view.setPage({
    index: page.index,
    width: page.width,
    height: page.height,
    rotation: page.rotation,
    render: (canvas, scale) => shown.render(page.index, canvas, scale),
  });
  render();
}

/** Outlines of the sample's original fields on the current page, when "show" is on. */
function compareRects() {
  if (!state.compare || state.sample === null) return [];
  return state.sample.originals.pages[String(state.page)] ?? [];
}

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

function render() {
  const t = app.template;
  const open = state.doc !== null && t !== null;
  $("pageview").hidden = !open;
  $("stage-empty").hidden = open;
  $("pager").hidden = !open || state.doc.pageCount < 2;
  const filled = open && state.preview !== null;
  $("pageview").classList.toggle("is-filled", filled);
  $("key").hidden = filled;
  if (open) {
    if (state.selected !== null && !t.fields.some((f) => f.id === state.selected)) {
      state.selected = null;
      view.select(null);
    }
    view.setTemplate(t, { compare: filled ? [] : compareRects(), warnings: fieldWarnings(t, app.schemaPaths(), session.fieldWarnings) });
    scan.decorate();
    $("page-no").textContent = `Page ${state.page + 1} of ${state.doc.pageCount}`;
    $("prev-page").disabled = state.page === 0;
    $("next-page").disabled = state.page >= state.doc.pageCount - 1;
    if (filled) {
      $("caption").textContent = `Fig. 1 · The filled PDF · ${state.preview.title}`;
    } else {
      const linked = t.fields.filter((f) => (f.source?.paths ?? []).length > 0).length;
      const source = session.source?.text ?? "";
      const boxes = t.fields.length === 0 ? "no boxes yet" : `${plural(t.fields.length, "box", "boxes")} · ${linked} linked`;
      const text = source ? `${source} · ${boxes}` : boxes;
      $("caption").textContent = `Fig. 1 · ${text[0].toUpperCase()}${text.slice(1)}`;
    }
  } else {
    $("caption").textContent = "Fig. 1 · No form open";
  }
  for (const fn of hooks) fn();
}

// ------------------------------------------------------------------ steps

const STEPS = ["detect", "link", "fill"];

function setStep(name) {
  if (name !== "fill" && state.preview !== null) app.setPreview(null); // back to the boxes to edit
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
bus.on("op", ({ op }) => session.apply(op));
bus.on("undo", () => session.undo());
bus.on("redo", () => session.redo());
bus.on("template", ({ added, reason }) => {
  if (reason === "apply") say($("op-msg"), "");
  if (added !== null) state.selected = added;
  if (state.preview !== null) app.setPreview(null); // a fill of the template before is out of date
  else render();
  if (added !== null) view.select(added);
});
bus.on("error", ({ message }) => {
  say($("op-msg"), `That change wasn't made: ${message}`);
  render(); // puts a dragged box back where the template has it
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
mountResults({ url: new URL("results.json", location.href), $, timeoutMs: FETCH_TIMEOUT_MS });
