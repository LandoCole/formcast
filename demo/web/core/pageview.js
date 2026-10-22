// The page view: a page image (a canvas from pdf.js, or a PNG from the server) with a box over
// each field. Select, drag to move, corner handles to resize, draw a new box, nudge with the
// arrow keys, Delete, drop a schema path on a box to link it, and an optional area select.
//
// Everything on screen is placed through geometry.js (`toScreen`, `toPage`), the only place that
// converts between PDF points and pixels, so rotated pages work the same as upright ones. The
// view never edits a template: it emits ready-to-apply ops on the bus (once per gesture, on
// pointerup or keyup, and only when the box ends somewhere new) and redraws when the host calls
// `setTemplate` again. `destroy()` removes its listeners, its observer and what it drew.

import { makeView, ptToPx, toPage, toScreen, viewSize } from "./geometry.js";

/** The `dataTransfer` type a schema list sets when a path is dragged. */
export const PATH_MIME = "application/x-formcast-path";

const DRAG_THRESHOLD = 3; // pixels before a press becomes a drag
const MIN_PT = 3; // smallest box a person can draw or resize to, in points
const ARROWS = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};
const CORNERS = ["nw", "ne", "sw", "se"];
// A selected box with a side under this many pixels gets its handles outside the corners, so the
// 24 px hit areas never cover the box body and the box stays draggable.
const SMALL_BOX_PX = 48;

/**
 * @typedef {import("./geometry.js").Rect} Rect
 * @typedef {import("./geometry.js").Box} Box
 * @typedef {import("./bus.js").Bus} Bus
 *
 * @typedef {object} PageSpec
 * @property {number} index page index
 * @property {number} width unrotated page width in points
 * @property {number} height unrotated page height in points
 * @property {number} [rotation] clockwise page rotation (0, 90, 180, 270)
 * @property {string | ((canvas: HTMLCanvasElement, scale: number) => Promise<void | string> | string)} render
 *   Draws the page into `canvas` at `scale` pixels per point (a promise), or returns an image
 *   URL (a string, or a promise of one). A plain URL string is also accepted.
 */

function clamp(v, lo, hi) {
  return Math.min(Math.max(v, lo), Math.max(lo, hi));
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

/** The paths a field is linked to. */
function pathsOf(field) {
  return field.source?.paths ?? [];
}

export class PageView {
  #el;
  #bus;
  #readOnly;
  #regionSelect;
  #stage;
  #surface;
  #layer;
  #addBtn = null;
  #media = null;
  #mediaIsImage = false;
  /** @type {PageSpec | null} */
  #page = null;
  #prefScale = 1;
  #scale = 1;
  #view = null;
  #template = null;
  #compare = [];
  #warnings = {};
  #selected = null;
  #addMode = false;
  #token = 0;
  #paintTimer = 0;
  #marquee = null;
  #observer = null;
  /** Aborted by `destroy()`; every listener the view adds uses its signal. */
  #life = new AbortController();
  /** @type {WeakMap<Element, Box>} the box each element currently shows (it may be ahead of the template) */
  #shown = new WeakMap();
  /** @type {{el: HTMLElement, box: Box, start: Box} | null} arrow-key nudges not yet emitted */
  #nudge = null;

  /**
   * @param {HTMLElement} el container; the view fills it
   * @param {{bus: Bus, readOnly?: boolean, regionSelect?: boolean}} opts
   */
  constructor(el, { bus, readOnly = false, regionSelect = false }) {
    this.#el = el;
    this.#bus = bus;
    this.#readOnly = readOnly;
    this.#regionSelect = regionSelect;
    el.classList.add("fc-pageview");
    const signal = this.#life.signal;

    if (!readOnly) {
      const bar = document.createElement("div");
      bar.className = "fc-pv-bar";
      const add = document.createElement("button");
      add.type = "button";
      add.className = "fc-pv-btn fc-pv-add";
      add.textContent = "Add box";
      add.setAttribute("aria-pressed", "false");
      add.addEventListener("click", () => this.setAddMode(!this.#addMode), { signal });
      bar.append(add);
      this.#addBtn = add;
      el.append(bar);
    }

    this.#stage = document.createElement("div");
    this.#stage.className = "fc-pv-stage";
    this.#surface = document.createElement("div");
    this.#surface.className = "fc-pv-page";
    this.#layer = document.createElement("div");
    this.#layer.className = "fc-pv-layer";
    this.#surface.append(this.#layer);
    this.#stage.append(this.#surface);
    el.append(this.#stage);

    const layer = this.#layer;
    layer.addEventListener("pointerdown", (e) => this.#onPointerDown(e), { signal });
    layer.addEventListener("keydown", (e) => this.#onKeyDown(e), { signal });
    layer.addEventListener("keyup", (e) => this.#onKeyUp(e), { signal });
    layer.addEventListener("focusin", (e) => this.#onFocusIn(e), { signal });
    layer.addEventListener("focusout", () => this.#flushNudge(), { signal });
    layer.addEventListener("dragover", (e) => this.#onDragOver(e), { signal });
    layer.addEventListener("dragleave", (e) => this.#onDragLeave(e), { signal });
    layer.addEventListener("drop", (e) => this.#onDrop(e), { signal });
    el.addEventListener(
      "keydown",
      (e) => {
        if (e.key === "Escape") {
          if (this.#addMode) this.setAddMode(false);
          this.#clearMarquee();
        }
      },
      { signal },
    );

    if (typeof ResizeObserver === "function") {
      this.#observer = new ResizeObserver(() => this.#layout(false));
      this.#observer.observe(this.#stage);
    }
  }

  // ------------------------------------------------------------------ public

  /**
   * Show a page. The image is drawn (or loaded) for the current scale.
   * @param {PageSpec} page
   */
  setPage(page) {
    this.#page = typeof page.render === "string" ? { ...page, render: () => /** @type {string} */ (page.render) } : page;
    this.#clearMarquee();
    this.#mediaIsImage = false;
    this.#layout(true);
  }

  /**
   * Show a template's boxes for the current page, replacing what was drawn.
   * @param {object} template a formcast template (plain JSON)
   * @param {{compare?: Rect[], warnings?: Record<string, string[]>}} [opts] `compare`: faint
   *   outlines of the original fields; `warnings`: messages per field id
   */
  setTemplate(template, { compare = [], warnings = {} } = {}) {
    this.#template = template;
    this.#compare = compare;
    this.#warnings = warnings;
    this.#renderBoxes();
  }

  /**
   * Select a field (or none). Does not emit `select`; the host asked for it.
   * @param {string | null} fieldId
   */
  select(fieldId) {
    this.#applySelection(fieldId ?? null);
    if (fieldId == null) return;
    const el = this.#boxElements(fieldId)[0];
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  /**
   * Set the preferred scale in pixels per point. A page wider than its container is shrunk to fit.
   * @param {number} scale
   */
  setScale(scale) {
    if (!(scale > 0)) throw new RangeError("scale must be positive");
    this.#prefScale = scale;
    this.#layout(false);
  }

  /**
   * Turn "draw a new box" mode on or off. Emits `addmode` when it changes.
   * @param {boolean} on
   */
  setAddMode(on) {
    if (this.#readOnly) return;
    on = Boolean(on);
    if (on === this.#addMode) return;
    this.#addMode = on;
    this.#addBtn?.setAttribute("aria-pressed", String(on));
    this.#syncLayerMode();
    this.#bus.emit("addmode", { on });
  }

  /** Remove the area-select outline. */
  clearRegion() {
    this.#clearMarquee();
  }

  /**
   * Take the view down: stop observing the container's size, remove every listener (a gesture in
   * progress included), drop a pending repaint and nudge, and remove what the view added to the
   * container. The view does nothing after this; make a new one to show a page again.
   */
  destroy() {
    this.#life.abort();
    this.#observer?.disconnect();
    this.#observer = null;
    clearTimeout(this.#paintTimer);
    this.#token += 1; // a paint still in flight is dropped
    this.#nudge = null;
    this.#page = null;
    this.#view = null;
    this.#template = null;
    this.#addBtn?.closest(".fc-pv-bar")?.remove();
    this.#stage.remove();
    this.#el.classList.remove("fc-pageview");
  }

  // ------------------------------------------------------------------ layout and image

  #layout(force) {
    const p = this.#page;
    if (p === null) return;
    const natural = viewSize(makeView({ width: p.width, height: p.height, rotation: p.rotation ?? 0, scale: 1 })).width;
    const avail = this.#stage.clientWidth;
    const scale = avail > 0 ? Math.min(this.#prefScale, avail / natural) : this.#prefScale;
    if (!force && this.#view !== null && Math.abs(scale - this.#scale) < 1e-4) return;
    this.#scale = scale;
    this.#view = makeView({ width: p.width, height: p.height, rotation: p.rotation ?? 0, scale });
    const size = viewSize(this.#view);
    this.#surface.style.width = `${size.width}px`;
    this.#surface.style.height = `${size.height}px`;
    this.#renderBoxes();
    if (force) {
      this.#paint();
    } else if (!this.#mediaIsImage) {
      clearTimeout(this.#paintTimer);
      this.#paintTimer = setTimeout(() => this.#paint(), 150);
    }
  }

  async #paint() {
    const page = this.#page;
    if (page === null) return;
    const token = ++this.#token;
    const canvas = document.createElement("canvas");
    const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    let out;
    try {
      out = await page.render(canvas, this.#scale * dpr);
    } catch (error) {
      if (token === this.#token) {
        this.#surface.classList.add("is-failed");
        console.error("page render failed", error);
      }
      return;
    }
    if (token !== this.#token) return;
    this.#surface.classList.remove("is-failed");
    let media;
    if (typeof out === "string") {
      media = document.createElement("img");
      media.alt = "";
      media.draggable = false;
      media.src = out;
      this.#mediaIsImage = true;
    } else {
      media = canvas;
      this.#mediaIsImage = false;
    }
    media.classList.add("fc-pv-media");
    media.setAttribute("aria-hidden", "true");
    this.#media?.remove();
    this.#media = media;
    this.#surface.insertBefore(media, this.#layer);
  }

  // ------------------------------------------------------------------ boxes

  #renderBoxes() {
    const layer = this.#layer;
    const focusedId = layer.contains(document.activeElement) ? document.activeElement.dataset?.fieldId : undefined;
    const focusedSeg = layer.contains(document.activeElement) ? document.activeElement.dataset?.segment : undefined;
    this.#nudge = null;
    this.#marquee = null;
    layer.replaceChildren();
    const page = this.#page;
    const view = this.#view;
    if (page === null || view === null) return;
    const frag = document.createDocumentFragment();
    for (const rect of this.#compare) {
      const c = document.createElement("div");
      c.className = "fc-compare";
      c.setAttribute("aria-hidden", "true");
      this.#place(c, toScreen(rect, view));
      frag.append(c);
    }
    for (const field of this.#template?.fields ?? []) {
      if (field.type === "table") {
        (field.segments ?? []).forEach((seg, si) => {
          if (seg.page === page.index) frag.append(this.#makeBox(field, seg.rect, si, seg));
        });
      } else if (field.page === page.index && field.rect) {
        frag.append(this.#makeBox(field, field.rect, null, null));
      }
    }
    layer.append(frag);
    this.#applySelection(this.#selected);
    if (focusedId !== undefined) {
      const sel = `[data-field-id="${CSS.escape(focusedId)}"]` + (focusedSeg !== undefined ? `[data-segment="${focusedSeg}"]` : "");
      layer.querySelector(sel)?.focus({ preventScroll: true });
    }
  }

  #place(el, box) {
    el.style.left = `${box.left}px`;
    el.style.top = `${box.top}px`;
    el.style.width = `${box.width}px`;
    el.style.height = `${box.height}px`;
    this.#shown.set(el, box);
  }

  #makeBox(field, rect, segment, seg) {
    const view = this.#view;
    const paths = pathsOf(field);
    const linked = paths.length > 0;
    const el = document.createElement("div");
    el.className = "fc-box";
    el.classList.add(linked ? "is-linked" : "is-unlinked");
    if (field.type === "table") el.classList.add("fc-table");
    if (field.origin === "agent") el.classList.add("is-ai");
    el.dataset.fieldId = field.id;
    if (segment !== null) el.dataset.segment = String(segment);
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    el.setAttribute("aria-label", `${field.label || field.id}: ${linked ? paths.join(", ") : "not linked"}`);
    el.setAttribute("aria-pressed", "false");
    const box = toScreen(rect, view);
    this.#place(el, box);

    const tag = document.createElement("span");
    tag.className = "fc-tag";
    tag.textContent = linked ? paths.join(", ") : "not linked";
    el.append(tag);

    const marks = [];
    if (field.origin === "agent") marks.push("AI");
    const warn = this.#warnings[field.id];
    if (warn && warn.length > 0) {
      marks.push("!");
      el.classList.add("has-warning");
      el.title = warn.join("\n");
    }
    if (marks.length > 0) {
      const wrap = document.createElement("span");
      wrap.className = "fc-marks";
      for (const m of marks) {
        const s = document.createElement("span");
        s.className = m === "AI" ? "fc-mark fc-ai" : "fc-mark fc-warn";
        s.textContent = m;
        wrap.append(s);
      }
      el.append(wrap);
    }

    if (seg !== null) this.#drawTable(el, seg, box);
    return el;
  }

  /** Row and column lines of a table segment, positioned relative to its outline box. */
  #drawTable(el, seg, box) {
    const view = this.#view;
    const [x0, y0, x1, y1] = seg.rect;
    const rowsEnd = Math.min(y1, seg.row_top + seg.max_rows * seg.row_height);
    const lines = [];
    for (let i = 0; i <= seg.max_rows; i++) {
      const y = seg.row_top + i * seg.row_height;
      if (y > y1 + 1e-6) break;
      lines.push([x0, y, x1, y]);
    }
    const xs = new Set();
    for (const col of seg.columns ?? []) {
      xs.add(col.x[0]);
      xs.add(col.x[1]);
    }
    for (const x of xs) lines.push([x, seg.row_top, x, rowsEnd]);
    for (const line of lines) {
      const b = toScreen(line, view);
      const d = document.createElement("span");
      d.className = "fc-line";
      d.setAttribute("aria-hidden", "true");
      d.style.left = `${b.left - box.left}px`;
      d.style.top = `${b.top - box.top}px`;
      d.style.width = `${Math.max(b.width, 1)}px`;
      d.style.height = `${Math.max(b.height, 1)}px`;
      el.append(d);
    }
  }

  #boxElements(id) {
    return [...this.#layer.querySelectorAll(".fc-box")].filter((e) => e.dataset.fieldId === id);
  }

  #applySelection(id) {
    this.#selected = id;
    for (const el of this.#layer.querySelectorAll(".fc-box")) {
      const on = id !== null && el.dataset.fieldId === id;
      el.classList.toggle("is-selected", on);
      el.setAttribute("aria-pressed", String(on));
      el.querySelectorAll(":scope > .fc-hd").forEach((h) => h.remove());
      el.classList.remove("is-small");
      if (on && !this.#readOnly) {
        const shown = this.#shown.get(el);
        if (shown !== undefined && Math.min(shown.width, shown.height) < SMALL_BOX_PX) el.classList.add("is-small");
        for (const corner of CORNERS) {
          const h = document.createElement("span");
          h.className = "fc-hd";
          h.dataset.handle = corner;
          h.setAttribute("aria-hidden", "true");
          el.append(h);
        }
      }
    }
  }

  /** A selection made by the person: update and tell the host. */
  #userSelect(id) {
    if (id === this.#selected) return;
    this.#applySelection(id);
    this.#bus.emit("select", { id });
  }

  #syncLayerMode() {
    this.#layer.classList.toggle("is-drawing", this.#addMode || this.#regionSelect);
  }

  // ------------------------------------------------------------------ ops

  #emitOp(op) {
    this.#bus.emit("op", { op });
  }

  #rectOf(box) {
    return toPage(box, this.#view).map(round2);
  }

  #emitBox(kind, el, box) {
    const op = { op: kind, id: el.dataset.fieldId, rect: this.#rectOf(box) };
    if (el.dataset.segment !== undefined) op.segment = Number(el.dataset.segment);
    this.#emitOp(op);
  }

  #pageBounds() {
    return viewSize(this.#view);
  }

  // ------------------------------------------------------------------ pointer

  #onPointerDown(e) {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    const target = /** @type {Element} */ (e.target);
    const handle = target.closest?.(".fc-hd");
    const boxEl = target.closest?.(".fc-box");
    if (handle && boxEl) {
      e.stopPropagation();
      this.#startGesture(e, boxEl, handle, "resize", handle.dataset.handle);
    } else if (boxEl) {
      this.#userSelect(boxEl.dataset.fieldId);
      if (!this.#readOnly) this.#startGesture(e, boxEl, boxEl, "move", null);
    } else if (target === this.#layer) {
      this.#clearMarquee();
      if (!this.#readOnly && this.#addMode) this.#startDraw(e, "add");
      else if (this.#regionSelect) this.#startDraw(e, "region");
      else this.#userSelect(null);
    }
  }

  /** Move or resize a box by pointer; the op is emitted once, on pointerup. */
  #startGesture(e, boxEl, captureEl, kind, corner) {
    if (this.#view === null) return;
    this.#flushNudge();
    const base = this.#shown.get(boxEl);
    if (base === undefined) return;
    const { width: pageW, height: pageH } = this.#pageBounds();
    const minPx = ptToPx(MIN_PT, this.#view);
    const startX = e.clientX;
    const startY = e.clientY;
    let box = base;
    let moved = false;
    try {
      captureEl.setPointerCapture(e.pointerId);
    } catch {
      // a synthetic pointer id has nothing to capture; the gesture still works
    }

    const compute = (dx, dy) => {
      if (kind === "move") {
        return {
          left: clamp(base.left + dx, 0, pageW - base.width),
          top: clamp(base.top + dy, 0, pageH - base.height),
          width: base.width,
          height: base.height,
        };
      }
      let l = base.left;
      let t = base.top;
      let r = base.left + base.width;
      let b = base.top + base.height;
      if (corner.includes("w")) l = clamp(l + dx, 0, r - minPx);
      else r = clamp(r + dx, l + minPx, pageW);
      if (corner.includes("n")) t = clamp(t + dy, 0, b - minPx);
      else b = clamp(b + dy, t + minPx, pageH);
      return { left: l, top: t, width: r - l, height: b - t };
    };

    const onMove = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      if (!moved) boxEl.classList.add("is-dragging");
      moved = true;
      box = compute(dx, dy);
      this.#setBoxStyle(boxEl, box);
    };
    const finish = (ev, commit) => {
      if (ev.pointerId !== e.pointerId) return;
      captureEl.removeEventListener("pointermove", onMove);
      captureEl.removeEventListener("pointerup", onUp);
      captureEl.removeEventListener("pointercancel", onCancel);
      boxEl.classList.remove("is-dragging");
      if (!moved) return;
      if (commit && this.#sameRect(box, base)) commit = false; // dragged back to where it started
      if (commit) {
        this.#shown.set(boxEl, box);
        if (boxEl.classList.contains("is-selected")) this.#applySelection(this.#selected);
        this.#emitBox(kind, boxEl, box);
      } else {
        this.#place(boxEl, base);
      }
    };
    const onUp = (ev) => finish(ev, true);
    const onCancel = (ev) => finish(ev, false);
    const signal = this.#life.signal;
    captureEl.addEventListener("pointermove", onMove, { signal });
    captureEl.addEventListener("pointerup", onUp, { signal });
    captureEl.addEventListener("pointercancel", onCancel, { signal });
  }

  /** True when two screen boxes give the same rect in points (as emitted, to 0.01 pt). */
  #sameRect(a, b) {
    const ra = this.#rectOf(a);
    const rb = this.#rectOf(b);
    return ra.every((v, i) => v === rb[i]);
  }

  /** Update a box's pixels without recording them as the committed position. */
  #setBoxStyle(el, box) {
    el.style.left = `${box.left}px`;
    el.style.top = `${box.top}px`;
    el.style.width = `${box.width}px`;
    el.style.height = `${box.height}px`;
  }

  /** Draw a rectangle on empty space: a new box ("add") or an area for the agent ("region"). */
  #startDraw(e, kind) {
    if (this.#view === null || this.#page === null) return;
    const layerRect = this.#layer.getBoundingClientRect();
    const { width: pageW, height: pageH } = this.#pageBounds();
    const x0 = clamp(e.clientX - layerRect.left, 0, pageW);
    const y0 = clamp(e.clientY - layerRect.top, 0, pageH);
    const m = document.createElement("div");
    m.className = "fc-marquee";
    m.setAttribute("aria-hidden", "true");
    this.#layer.append(m);
    this.#marquee = m;
    let box = { left: x0, top: y0, width: 0, height: 0 };
    this.#setBoxStyle(m, box);
    const layer = this.#layer;
    try {
      layer.setPointerCapture(e.pointerId);
    } catch {
      // see #startGesture
    }
    const onMove = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      const x1 = clamp(ev.clientX - layerRect.left, 0, pageW);
      const y1 = clamp(ev.clientY - layerRect.top, 0, pageH);
      box = { left: Math.min(x0, x1), top: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
      this.#setBoxStyle(m, box);
    };
    const onUp = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      layer.removeEventListener("pointermove", onMove);
      layer.removeEventListener("pointerup", onUp);
      layer.removeEventListener("pointercancel", onUp);
      const minPx = ptToPx(MIN_PT, this.#view);
      const big = box.width >= minPx && box.height >= minPx && ev.type === "pointerup";
      if (!big) {
        this.#clearMarquee();
        if (kind === "add") this.#userSelect(null);
        return;
      }
      const rect = this.#rectOf(box);
      if (kind === "add") {
        this.#clearMarquee();
        this.#emitOp({ op: "add_field", type: "text", page: this.#page.index, rect });
        this.setAddMode(false);
      } else {
        this.#bus.emit("region", { page: this.#page.index, rect });
      }
    };
    const signal = this.#life.signal;
    layer.addEventListener("pointermove", onMove, { signal });
    layer.addEventListener("pointerup", onUp, { signal });
    layer.addEventListener("pointercancel", onUp, { signal });
  }

  #clearMarquee() {
    this.#marquee?.remove();
    this.#marquee = null;
  }

  // ------------------------------------------------------------------ keyboard

  #boxOf(e) {
    return /** @type {Element} */ (e.target).closest?.(".fc-box") ?? null;
  }

  #onFocusIn(e) {
    const el = this.#boxOf(e);
    if (el) this.#userSelect(el.dataset.fieldId);
  }

  #onKeyDown(e) {
    const el = this.#boxOf(e);
    if (el === null || /** @type {Element} */ (e.target) !== el) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      this.#userSelect(el.dataset.fieldId);
      return;
    }
    if (this.#readOnly || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      if (!e.repeat) this.#emitOp({ op: "remove_field", id: el.dataset.fieldId }); // a held key removes one box
      return;
    }
    const dir = ARROWS[e.key];
    if (dir === undefined || this.#view === null) return;
    e.preventDefault();
    if (this.#nudge !== null && this.#nudge.el !== el) this.#flushNudge();
    const shown = this.#shown.get(el);
    if (shown === undefined) return;
    const step = ptToPx(e.shiftKey ? 10 : 1, this.#view);
    const { width: pageW, height: pageH } = this.#pageBounds();
    const from = this.#nudge?.box ?? shown;
    const box = {
      left: clamp(from.left + dir[0] * step, 0, pageW - from.width),
      top: clamp(from.top + dir[1] * step, 0, pageH - from.height),
      width: from.width,
      height: from.height,
    };
    this.#nudge = { el, box, start: this.#nudge?.start ?? shown };
    this.#setBoxStyle(el, box);
  }

  #onKeyUp(e) {
    if (e.key in ARROWS) this.#flushNudge();
  }

  /** Emit one `move` for the arrow presses since the last emit; none when the box is where it started. */
  #flushNudge() {
    const n = this.#nudge;
    if (n === null) return;
    this.#nudge = null;
    if (!n.el.isConnected) return;
    if (this.#sameRect(n.box, n.start)) {
      this.#place(n.el, n.start); // pressed against the page edge, or back and forth
      return;
    }
    this.#shown.set(n.el, n.box);
    this.#emitBox("move", n.el, n.box);
  }

  // ------------------------------------------------------------------ drop to link

  #dropTarget(e) {
    if (this.#readOnly) return null;
    const types = e.dataTransfer?.types;
    if (!types || !Array.from(types).includes(PATH_MIME)) return null;
    return this.#boxOf(e);
  }

  #onDragOver(e) {
    const el = this.#dropTarget(e);
    if (el === null) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "link";
    el.classList.add("is-drop");
  }

  #onDragLeave(e) {
    const el = this.#boxOf(e);
    if (el === null) return;
    const to = /** @type {Node | null} */ (e.relatedTarget);
    if (to === null || !el.contains(to)) el.classList.remove("is-drop");
  }

  #onDrop(e) {
    const el = this.#dropTarget(e);
    if (el === null) return;
    e.preventDefault();
    el.classList.remove("is-drop");
    const path = e.dataTransfer.getData(PATH_MIME);
    if (!path) return;
    this.#emitOp({ op: "set_source", id: el.dataset.fieldId, source: { paths: [path], transforms: [] } });
  }
}
