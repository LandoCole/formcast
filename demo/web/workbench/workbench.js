// The formcast workbench (layout B) on the server engine. A thin wiring layer: it creates the
// engine, the session (core/session.js: document, template, undo, the ordered edit queue) and the
// shared parts, renders on the session's events, and hands the overlays to their modules: the
// start screen and match prompt (start.js), AI jobs and area revise (jobs.js), the fill drawer
// (fill.js) and saving to the store (save.js), which call back through `app`.
//
// Rendering under latency. Every edit is a round trip to the server, and the session runs edits one
// at a time. Redrawing the page's boxes when one edit lands would put a box the person has since
// moved (its edit still queued) back where the template has it, and would drop an arrow-key nudge
// still being held. So the page's boxes are redrawn only when nothing is queued or in flight and no
// pointer or arrow key is held on the page; the side panels redraw at once.
//
// The filled preview. Fill in the drawer shows the filled PDF on the page (`state.preview`, drawn
// in the browser by pdf.js from the very bytes Download saves) with the boxes hidden. Any template
// change and any new document end it, so the page never shows a fill of an older template.
//
// Test hooks (localhost and 127.0.0.1 only): `?poll=<ms>` sets how often AI jobs are polled, and
// `window.__workbench` exposes the template on screen and whether the edit queue is idle.

import { createBus } from "../core/bus.js";
import { makeView, viewSize } from "../core/geometry.js";
import { HistoryPanel } from "../core/history.js";
import { Inspector } from "../core/inspector.js";
import { PageView } from "../core/pageview.js";
import { showLinkPanels } from "../core/panels.js";
import { schemaPathsOf } from "../core/schema.js";
import { SchemaList, fieldWarnings } from "../core/schemalist.js";
import { createSession } from "../core/session.js";
import { createServerEngine } from "../engines/server/client.js";
import { mountFill } from "./fill.js";
import { mountJobs } from "./jobs.js";
import { mountPages } from "./pages.js";
import { mountSave } from "./save.js";
import { mountStart } from "./start.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const LOCAL = location.hostname === "localhost" || location.hostname === "127.0.0.1";
const pollParam = LOCAL ? Number(params.get("poll")) : NaN;
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];

/** Show `text` in a message element, or hide it when `text` is empty. */
function say(el, text) {
  el.textContent = text;
  el.hidden = !text;
}

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

let engine;
try {
  engine = await createServerEngine(pollParam > 0 ? { pollMs: pollParam } : {});
} catch (e) {
  say($("start-msg"), e.message);
  $("open").disabled = true;
  throw e;
}

const bus = createBus();
const session = createSession({ engine, bus });
const view = new PageView($("pageview"), { bus, regionSelect: engine.capabilities.ai });
const schemaList = new SchemaList($("schemalist"), { bus });
const inspector = new Inspector($("inspector"), { bus });
const history = new HistoryPanel($("history"), { bus });

/** What the page shows besides the session's document and template. */
const state = {
  page: 0,
  selected: null,
  zoom: null, // pixels per point, or null to fit the page to the column
  schemaName: null, // the stored schema picked on the start screen, if any
  savedName: null, // the store name this template was loaded from or saved as
  /** The filled PDF on show from the fill drawer: {doc, bytes, title}, or null (see fill.js). */
  preview: null,
};

let pending = 0; // session calls queued or running
const held = { pointer: false, key: false };
let pageDirty = false;
let selectAfterRender = null;

const app = {
  $,
  bus,
  engine,
  session,
  view,
  state,
  say,
  schemas: {}, // stored schema name -> JSON Schema (start.js fills it)
  data: null, // your data's mapped records, a one-shot job's result.data (fill.js shows them)
  pendingData: null, // a dataset given on the start screen, mapped when the fill drawer opens

  /** The template on screen, or null before a document is open. */
  get template() {
    return session.template;
  },

  /** The open document, or null. */
  get doc() {
    return session.doc;
  },

  /** A file name stem for downloads and store names. */
  stem() {
    return (session.doc?.name ?? "form").replace(/\.pdf$/i, "") || "form";
  },

  /** Count a session call as in flight until it settles; resolves with its result. */
  track(promise) {
    pending += 1;
    return promise.finally(() => {
      pending -= 1;
      flushPage();
    });
  },

  /**
   * Show a new document with its starting template; the undo history starts over.
   * @param {{doc: object, template: object, source: {kind: string, text: string}, schemaName?: string | null,
   *   savedName?: string | null}} opts
   */
  setDocument({ doc, template, source, schemaName = null, savedName = null }) {
    dropPreview();
    Object.assign(state, { page: 0, selected: null, schemaName, savedName });
    session.setDocument(doc, template, { source });
    app.notice("");
    view.select(null);
    view.setAddMode(false);
    for (const fn of resetHooks) fn();
    showPage(0);
  },

  /**
   * Show a passing message under the toolbar (empty text hides it). An AI result on offer has its
   * own line (jobs.js), which messages never replace.
   * @param {string} text
   */
  notice(text) {
    say($("notice-text"), text);
    $("notice").hidden = !text;
  },

  /** Register a function called when a new document is shown. */
  onReset(fn) {
    resetHooks.push(fn);
  },

  /**
   * Show a filled PDF on the page in place of the form, boxes hidden; null goes back to the form.
   * @param {{doc: object, bytes: Uint8Array, title: string} | null} preview `doc` is the filled PDF
   *   opened with pdf.js (closed when the preview ends); `bytes` what Download saves
   */
  setPreview(preview) {
    if (preview === state.preview) return;
    dropPreview();
    state.preview = preview;
    if (session.doc !== null) showPage(state.page);
  },

  /** The current schema's flattened paths ([] when it can't be flattened). */
  schemaPaths() {
    return schemaPathsOf(app.template?.schema);
  },

  /** The stored schema whose JSON is exactly `schema`, else null. */
  schemaNameOf(schema) {
    const text = JSON.stringify(schema ?? null);
    return Object.keys(app.schemas).find((n) => JSON.stringify(app.schemas[n]) === text) ?? null;
  },

  render,
};
const resetHooks = [];

/** End the preview without redrawing (the caller does): its document is closed. */
function dropPreview() {
  const old = state.preview;
  state.preview = null;
  old?.doc.destroy().catch(() => {});
}

// ------------------------------------------------------------------ page

function currentPage() {
  return session.doc?.pages[state.page] ?? null;
}

function naturalWidth(page) {
  return viewSize(makeView({ width: page.width, height: page.height, rotation: page.rotation, scale: 1 })).width;
}

function fitScale(page) {
  const avail = $("stage").clientWidth || 600;
  return Math.min(3, Math.max(0.25, avail / naturalWidth(page)));
}

/** Size the page for the zoom: at "fit" it follows the column; zoomed, the column scrolls. */
function applyScale() {
  const page = currentPage();
  if (page === null) return;
  const scale = state.zoom ?? fitScale(page);
  $("pageview").style.width = state.zoom === null ? "" : `${Math.ceil(naturalWidth(page) * scale)}px`;
  view.setScale(scale);
  $("zoom-level").textContent = state.zoom === null ? "Fit" : `${Math.round(scale * 100)}%`;
}

function zoomBy(direction) {
  const page = currentPage();
  if (page === null) return;
  const now = state.zoom ?? fitScale(page);
  const next = direction > 0 ? ZOOMS.find((z) => z > now + 1e-3) : [...ZOOMS].reverse().find((z) => z < now - 1e-3);
  if (next === undefined) return;
  state.zoom = next;
  applyScale();
}

function showPage(index) {
  const doc = session.doc;
  if (doc === null) return render();
  state.page = Math.max(0, Math.min(index, doc.pageCount - 1));
  const page = doc.pages[state.page];
  const filled = state.preview?.doc ?? null; // the filled PDF has the form's pages, filled in
  view.setPage({
    index: page.index,
    width: page.width,
    height: page.height,
    rotation: page.rotation,
    render: (canvas, scale) => (filled ? filled.render(page.index, canvas, scale) : page.render(canvas, scale)),
  });
  applyScale();
  renderPanels();
  renderPage(true);
}

/** Redraw the boxes, unless an edit is on its way or the person is mid-gesture (see the top). */
function renderPage(force = false) {
  if (!force && (pending > 0 || held.pointer || held.key)) {
    pageDirty = true;
    return;
  }
  pageDirty = false;
  const t = session.template;
  if (session.doc === null || t === null) return;
  if (state.selected !== null && !t.fields.some((f) => f.id === state.selected)) {
    state.selected = null;
    view.select(null);
  }
  view.setTemplate(t, { warnings: fieldWarnings(t, app.schemaPaths(), session.fieldWarnings) });
  if (selectAfterRender !== null) {
    view.select(selectAfterRender);
    selectAfterRender = null;
  }
}

function flushPage() {
  if (pageDirty) renderPage();
}

const pv = $("pageview");
// capture phase: the page view stops propagation of a press on a resize handle. Only the primary
// button holds a gesture (a secondary press may never see its pointerup, e.g. under a context menu).
pv.addEventListener(
  "pointerdown",
  (e) => {
    if (e.button === 0) held.pointer = true;
  },
  { capture: true },
);
for (const type of ["pointerup", "pointercancel", "contextmenu"]) {
  addEventListener(type, () => {
    if (!held.pointer) return;
    held.pointer = false;
    flushPage();
  });
}
const isArrow = (key) => key.startsWith("Arrow");
pv.addEventListener("keydown", (e) => {
  if (isArrow(e.key)) held.key = true;
});
pv.addEventListener("keyup", (e) => {
  if (!isArrow(e.key)) return;
  held.key = false;
  flushPage();
});
pv.addEventListener("focusout", () => {
  held.key = false;
  flushPage();
});

// ------------------------------------------------------------------ panels and toolbar

function renderPanels() {
  const t = session.template;
  const doc = session.doc;
  const open = doc !== null && t !== null;
  $("pageview").hidden = !open;
  $("stage-empty").hidden = open;
  $("doc-name").textContent = open ? `${doc.name} · ${plural(doc.pageCount, "page")}` : "Open a form";
  $("prev-page").disabled = !open || state.page === 0;
  $("next-page").disabled = !open || state.page >= doc.pageCount - 1;
  $("page-no").textContent = open ? `Page ${state.page + 1} / ${doc.pageCount}` : "No form open";
  app.pages?.render(); // finding the blanks on this page (pages.js)
  for (const id of ["add-box", "save", "fill-open", "build-ai"]) $(id).disabled = !open;
  $("undo").disabled = !session.canUndo;
  $("redo").disabled = !session.canRedo;

  $("schema-label").textContent = showLinkPanels({ schemaList, inspector, history }, {
    template: open ? t : null,
    schemaPaths: app.schemaPaths(),
    selected: state.selected,
    editWarnings: session.fieldWarnings,
    canUndo: session.canUndo,
    canRedo: session.canRedo,
  });
  $("pageview").classList.toggle("is-filled", open && state.preview !== null);
  if (!open) {
    $("caption").textContent = "";
    return;
  }
  if (state.preview !== null) {
    $("caption").textContent = `The filled PDF · ${state.preview.title} · Clear in the fill drawer goes back to the boxes`;
    return;
  }
  const linked = t.fields.filter((f) => (f.source?.paths ?? []).length > 0).length;
  const source = session.source?.text ?? "";
  const regionHint = engine.capabilities.ai ? " · drag on empty space to ask the AI about an area" : "";
  $("caption").textContent = `${source ? `${source} · ` : ""}${plural(t.fields.length, "box", "boxes")} · ${linked} linked${regionHint}`;
}

function render() {
  renderPanels();
  renderPage();
}

/** Select a field (or none) from outside the page view, turning to its page. */
function select(id) {
  const field = app.template?.fields.find((f) => f.id === id) ?? null;
  state.selected = field ? id : null;
  const page = field ? (field.type === "table" ? field.segments[0]?.page : field.page) : undefined;
  if (page !== undefined && page !== state.page) showPage(page);
  else renderPanels();
  view.select(state.selected);
}

const undo = () => app.track(session.undo());
const redo = () => app.track(session.redo());

$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);
$("add-box").addEventListener("click", () => view.setAddMode($("add-box").getAttribute("aria-pressed") !== "true"));
$("prev-page").addEventListener("click", () => showPage(state.page - 1));
$("next-page").addEventListener("click", () => showPage(state.page + 1));
$("zoom-in").addEventListener("click", () => zoomBy(1));
$("zoom-out").addEventListener("click", () => zoomBy(-1));
$("zoom-level").addEventListener("click", () => {
  state.zoom = null;
  applyScale();
});
$("notice-close").addEventListener("click", () => app.notice(""));

addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "z") return;
  if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if (session.template === null || document.querySelector(".overlay:not([hidden])")) return;
  e.preventDefault();
  if (e.shiftKey) redo();
  else undo();
});

let resizeTimer = 0;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.zoom === null) applyScale();
  }, 150);
});

// ------------------------------------------------------------------ overlays are modal

// While an overlay (start, match, job, save) is open, everything else on the page is inert: no
// focus, no clicks, no keys reach the boxes behind it. Focus moves into the overlay, Tab cycles
// inside it, and focus goes back to where it was when the last overlay closes.
const overlays = [...document.querySelectorAll(".overlay")];
const FOCUSABLE = "button:not([hidden]):not(:disabled), select:not(:disabled), textarea:not([hidden]), input:not([hidden]), [tabindex='0']";
let focusBefore = null; // the element focused outside the overlays

document.addEventListener("focusin", (e) => {
  if (!e.target.closest?.(".overlay")) focusBefore = e.target;
});

function openOverlay() {
  return overlays.find((o) => !o.hidden) ?? null;
}

function focusables(overlay) {
  return [...overlay.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
}

function syncModal() {
  const open = openOverlay();
  for (const el of document.body.children) {
    if (el.tagName !== "SCRIPT") el.inert = open !== null && el !== open;
  }
  if (open !== null) {
    if (!open.contains(document.activeElement)) focusables(open)[0]?.focus();
  } else if (focusBefore?.isConnected) {
    focusBefore.focus({ preventScroll: true });
  }
}

const watcher = new MutationObserver(syncModal);
for (const o of overlays) watcher.observe(o, { attributes: true, attributeFilter: ["hidden"] });
document.addEventListener("keydown", (e) => {
  const open = openOverlay();
  if (open === null || e.key !== "Tab") return;
  const items = focusables(open);
  if (items.length === 0) return;
  const at = items.indexOf(document.activeElement);
  const next = e.shiftKey ? (at <= 0 ? items.length - 1 : at - 1) : (at + 1) % items.length;
  e.preventDefault();
  items[next].focus();
});

// ------------------------------------------------------------------ bus

bus.on("select", ({ id }) => {
  state.selected = id;
  renderPanels();
});
bus.on("pick", ({ path }) => {
  const field = app.template?.fields.find((f) => (f.source?.paths ?? []).includes(path));
  if (field) {
    app.notice("");
    select(field.id);
  } else {
    app.notice(`No box uses ${path} yet. Drag it onto a box, or pick it in the inspector.`);
  }
});
bus.on("op", ({ op }) => app.track(session.apply(op)));
bus.on("undo", undo);
bus.on("redo", redo);
bus.on("addmode", ({ on }) => $("add-box").setAttribute("aria-pressed", String(on)));
bus.on("template", ({ added, reason }) => {
  if (reason === "apply") app.notice(""); // a made edit clears an old message
  if (added !== null) {
    state.selected = added;
    selectAfterRender = added;
  }
  if (state.preview !== null) app.setPreview(null); // a fill of the template before is out of date
  render();
});
bus.on("error", ({ message }) => {
  app.notice(`That change wasn't made: ${message}`);
  pageDirty = true; // puts a moved box back where the template has it, once the queue is idle
  render();
});

if (LOCAL) {
  window.__workbench = {
    get template() {
      return session.template;
    },
    get idle() {
      return pending === 0;
    },
  };
}

$("engine-line").textContent = engine.capabilities.ai ? "Local server · AI on" : "Local server · AI off";
app.jobs = mountJobs(app);
mountFill(app);
mountSave(app);
mountStart(app);
app.pages = mountPages(app);
syncModal();
render();
