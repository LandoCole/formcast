// The text layer: a page's words laid invisibly over the page image (a PNG from the server or a pdf.js canvas, which
// has no text of its own), so a person can select them and copy them.
//
// The words come in page points, unrotated (`{width, height, rotation, lines: [[{text, rect}]]}`, as the server's
// `page_words` lists them; the boxes' rects are in the same space). The layer is laid out in those points, one CSS
// pixel a point, and put onto the image by one CSS transform: turned by the page's /Rotate (as geometry.js turns a
// point) and scaled to the host's width, which a ResizeObserver follows, so zooming never lays the words out again.
// Each word is a span at its rect: its font size the rect's height, stretched to the rect's width, its glyphs
// transparent; only the selection's highlight shows. The spans are in reading order, so a drag selects as one reads.
// A drag that runs off the words onto empty space would make the browser carry the selection on to some far span; so
// while a press is held, an empty backdrop (`.fc-text-end`) fills the layer under the words, kept in the DOM right
// after the end being dragged (before it, when the start is): empty space is then that spot, and the selection stays
// where the words end.
//
// Copying writes the words selected as they read: a space between the words of a line, a line break between lines
// (`selectedText`; the browser would run the spans together). The layer takes the pointer only while it is active
// (`setActive`): the page view turns it on in its "Select text" mode, the run's review keeps it on (its marks never
// take the pointer). `destroy()` removes its element and listeners.

/** @typedef {{text: string, rect: [number, number, number, number]}} PageWord */
/** @typedef {{width: number, height: number, rotation?: number, lines: PageWord[][]}} PageWords */

const MEASURE_PX = 100; // the font size words are measured at
const FONT = "sans-serif";

let measurer = null;

/** The width of `text` at MEASURE_PX in FONT; a guess where there is no canvas to measure with. */
function measure(text) {
  if (measurer === null) {
    measurer = globalThis.document?.createElement("canvas").getContext?.("2d") ?? false;
    if (measurer) measurer.font = `${MEASURE_PX}px ${FONT}`;
  }
  return measurer ? measurer.measureText(text).width : text.length * MEASURE_PX * 0.5;
}

/** The CSS transform that turns the unrotated page (`width` × `height` points) by `rotation` and scales it by `k`. */
export function pageTransform(width, height, rotation, k) {
  const turn = {
    90: `translate(${height * k}px, 0px) rotate(90deg) `,
    180: `translate(${width * k}px, ${height * k}px) rotate(180deg) `,
    270: `translate(0px, ${width * k}px) rotate(270deg) `,
  }[rotation] ?? "";
  return `${turn}scale(${k})`;
}

/**
 * The caption line for "Select text" while it is on: reading the words (`words` null), how to select and copy them,
 * or why there are none.
 * @param {{count: number, error?: Error} | null} words
 */
export function selectTextNote(words) {
  if (words === null) return "Select text · reading the page's text…";
  if (words.error) return `Select text · the page's text couldn't be read: ${words.error.message}`;
  if (words.count === 0) return "Select text · this page has no text to select (a scan the server couldn't read, or a picture)";
  return "Select text · drag over the words, then copy (⌘C or Ctrl+C) · Esc to stop";
}

export class TextLayer {
  #host;
  #el;
  /** @type {PageWords | null} */
  #words = null;
  /** @type {HTMLElement[]} */
  #spans = [];
  #observer = null;
  #life = new AbortController();
  /** The backdrop under the words while a press is held (see the top). */
  #end;
  /** @type {Range | null} the selection as it last was during this press */
  #before = null;

  /**
   * @param {HTMLElement} host the positioned element the page image fills
   * @param {{before?: Node | null}} [opts] where in `host` the layer goes (by default last)
   */
  constructor(host, { before = null } = {}) {
    this.#host = host;
    const el = document.createElement("div");
    el.className = "fc-text";
    el.hidden = true;
    host.insertBefore(el, before);
    this.#el = el;
    this.#end = document.createElement("div");
    this.#end.className = "fc-text-end";
    el.append(this.#end);
    const signal = this.#life.signal;
    el.addEventListener(
      "pointerdown",
      () => {
        el.classList.add("is-selecting");
        this.#before = null;
      },
      { signal },
    );
    for (const type of ["pointerup", "pointercancel"]) {
      document.addEventListener(
        type,
        () => {
          if (!el.classList.contains("is-selecting")) return;
          el.classList.remove("is-selecting");
          el.append(this.#end);
        },
        { signal },
      );
    }
    document.addEventListener("selectionchange", () => this.#followSelection(), { signal });
    document.addEventListener(
      "copy",
      (e) => {
        if (!this.#holdsSelection()) return;
        const text = this.selectedText();
        if (text === "" || !e.clipboardData) return;
        e.clipboardData.setData("text/plain", text);
        e.preventDefault();
      },
      { signal },
    );
    if (typeof ResizeObserver === "function") {
      this.#observer = new ResizeObserver(() => this.#fit());
      this.#observer.observe(host);
    }
  }

  /** The layer's element. */
  get element() {
    return this.#el;
  }

  /** How many words are shown. */
  get count() {
    return this.#spans.length;
  }

  /**
   * Show a page's words, replacing what was shown (null shows none).
   * @param {PageWords | null} words
   */
  show(words) {
    this.#words = words;
    const spans = [];
    const frag = document.createDocumentFragment();
    (words?.lines ?? []).forEach((line, li) => {
      for (const { text, rect } of line) {
        const [x0, y0, x1, y1] = rect;
        const size = y1 - y0;
        if (!text || !(size > 0) || !(x1 > x0)) continue;
        const span = document.createElement("span");
        span.textContent = text;
        span.dataset.line = String(li);
        const natural = (measure(text) * size) / MEASURE_PX;
        Object.assign(span.style, {
          left: `${x0}px`,
          top: `${y0}px`,
          fontSize: `${size}px`,
          transform: natural > 0 ? `scaleX(${(x1 - x0) / natural})` : "",
        });
        spans.push(span);
        frag.append(span);
      }
    });
    this.#el.replaceChildren(frag, this.#end);
    this.#spans = spans;
    this.#el.hidden = words === null;
    if (words !== null) {
      this.#el.style.width = `${words.width}px`;
      this.#el.style.height = `${words.height}px`;
    }
    this.#fit();
  }

  /**
   * Take the pointer (the words can be selected) or not.
   * @param {boolean} on
   */
  setActive(on) {
    this.#el.classList.toggle("is-active", Boolean(on));
    if (!on && this.#holdsSelection()) globalThis.getSelection?.()?.removeAllRanges();
  }

  /** The words selected on this layer as they read: words of a line apart by a space, lines by a line break. */
  selectedText() {
    const sel = globalThis.getSelection?.();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return "";
    const range = sel.getRangeAt(0);
    let out = "";
    let last = null;
    for (const span of this.#spans) {
      if (!range.intersectsNode(span)) continue;
      const text = span.textContent;
      const node = span.firstChild;
      let start = 0;
      let end = text.length;
      if (range.startContainer === node) start = range.startOffset;
      else if (range.startContainer === span) start = range.startOffset > 0 ? text.length : 0;
      if (range.endContainer === node) end = range.endOffset;
      else if (range.endContainer === span) end = range.endOffset > 0 ? text.length : 0;
      const part = text.slice(start, end);
      if (part === "") continue;
      if (out !== "") out += span.dataset.line === last ? " " : "\n";
      out += part;
      last = span.dataset.line;
    }
    return out;
  }

  /** Remove the layer and its listeners. */
  destroy() {
    this.#life.abort();
    this.#observer?.disconnect();
    this.#el.remove();
  }

  /** While a press is held, keep the backdrop next to the end of the selection being dragged (see the top). */
  #followSelection() {
    if (!this.#el.classList.contains("is-selecting")) return;
    const sel = globalThis.getSelection?.();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!this.#el.contains(range.commonAncestorContainer)) return;
    const was = this.#before;
    const startMoved = was !== null && (range.compareBoundaryPoints(Range.END_TO_END, was) === 0 || range.compareBoundaryPoints(Range.START_TO_END, was) === 0);
    let at = startMoved ? range.startContainer : range.endContainer;
    if (at.nodeType === Node.TEXT_NODE) at = at.parentNode;
    if (at.parentNode === this.#el && at !== this.#end) this.#el.insertBefore(this.#end, startMoved ? at : at.nextSibling);
    this.#before = range.cloneRange();
  }

  /** True when the page's selection is on this layer. */
  #holdsSelection() {
    const sel = globalThis.getSelection?.();
    if (!sel || sel.rangeCount === 0) return false;
    return this.#el.contains(sel.getRangeAt(0).commonAncestorContainer);
  }

  /** Turn and scale the layer onto the host's width. */
  #fit() {
    const w = this.#words;
    if (w === null) return;
    const rotation = ((Math.round(w.rotation ?? 0) % 360) + 360) % 360;
    const shown = rotation === 90 || rotation === 270 ? w.height : w.width;
    const k = this.#host.clientWidth / shown;
    if (!(k > 0)) return;
    this.#el.style.transform = pageTransform(w.width, w.height, rotation, k);
  }
}
