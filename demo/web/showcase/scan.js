// What the page shows while the detector and Auto map work. A scan: a thin line sweeps down the
// page with a soft light under it, over and over while the detector runs; when its boxes arrive the
// line makes one last pass and each box appears as the line reaches it. Links landing: the boxes
// Auto map linked turn the linked colour one after another, in reading order.
//
// The overlay sits on the page view's sheet (.fc-pv-page) and uses its tokens, so it reads the same
// in light and dark. Boxes are redrawn on every render, so their delays are set again by `decorate`
// (showcase.js calls it after each render), counted from when the motion started; a box redrawn
// part-way through picks up where it was. Under prefers-reduced-motion nothing moves: no sweep,
// boxes and links show at once, and the status line says what is happening.

/** One pass of the line over the page when the boxes arrive, in ms. */
export const REVEAL_MS = 1400;
/** The longest a run of links takes to land, in ms, however many there are. */
export const LAND_MS = 1200;
const BOX_MS = 320; // a box's own fade-in (showcase.css .is-revealing .fc-box)
const LINK_MS = 420; // one link landing (showcase.css .is-landing)

/**
 * The scan for a page view.
 * @param {HTMLElement} host the PageView's container (#pageview)
 */
export function createScan(host) {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const overlay = document.createElement("div");
  overlay.className = "scan";
  overlay.setAttribute("aria-hidden", "true");
  let revealAt = null; // performance.now() when the last pass started, while boxes are appearing
  let landing = null; // {at, ids: Map(field id -> rank)} while links land
  let waits = []; // [{timer, resolve}]: `stop` ends them early, so nobody waits on motion that was cut

  /** Put the overlay on the page sheet (the page view makes the sheet once; it is never replaced). */
  function attach() {
    const sheet = host.querySelector(".fc-pv-page");
    if (sheet && overlay.parentNode !== sheet) sheet.append(overlay);
  }

  /** Resolves after `ms`, or as soon as `stop` is called; true when the time ran out. */
  function later(ms) {
    return new Promise((resolve) => {
      const wait = { resolve, timer: 0 };
      wait.timer = setTimeout(() => {
        waits = waits.filter((w) => w !== wait);
        resolve(true);
      }, ms);
      waits.push(wait);
    });
  }

  const scan = {
    /** True when motion is off (prefers-reduced-motion). */
    get still() {
      return still.matches;
    },

    /** Start sweeping: the detector is working. */
    start() {
      if (still.matches) return;
      attach();
      overlay.classList.remove("is-pass");
      overlay.classList.add("is-sweeping");
    },

    /**
     * One last pass that shows the boxes as the line reaches them. Call it just before the
     * template with the boxes is rendered; resolves when every box is in.
     * @returns {Promise<void>}
     */
    async reveal() {
      if (still.matches) {
        scan.stop();
        return;
      }
      attach();
      overlay.classList.remove("is-sweeping");
      void overlay.offsetWidth; // restart the line's animation from the top
      overlay.classList.add("is-pass");
      revealAt = performance.now();
      host.classList.add("is-revealing");
      if (!(await later(REVEAL_MS + BOX_MS))) return;
      revealAt = null;
      host.classList.remove("is-revealing");
      overlay.classList.remove("is-pass");
    },

    /**
     * Show links landing on `ids` (in reading order). Call it just before the template with the
     * links is rendered; resolves when the last one has landed.
     * @param {string[]} ids
     * @returns {Promise<void>}
     */
    async land(ids) {
      if (still.matches || ids.length === 0) return;
      landing = { at: performance.now(), ids: new Map(ids.map((id, i) => [id, i])) };
      host.classList.add("is-mapping");
      if (!(await later(LAND_MS + LINK_MS))) return;
      landing = null;
      host.classList.remove("is-mapping");
    },

    /** Stop any motion now; a `reveal` or `land` under way resolves at once. */
    stop() {
      for (const w of waits) {
        clearTimeout(w.timer);
        w.resolve(false);
      }
      waits = [];
      revealAt = null;
      landing = null;
      overlay.classList.remove("is-sweeping", "is-pass");
      host.classList.remove("is-revealing", "is-mapping");
    },

    /** Set each drawn box's delay for the motion under way (after every render). */
    decorate() {
      if (revealAt === null && landing === null) return;
      const now = performance.now();
      const sheet = host.querySelector(".fc-pv-page");
      const height = sheet?.clientHeight || 1;
      const boxes = [...host.querySelectorAll(".fc-box")];
      if (revealAt !== null) {
        for (const el of boxes) {
          const top = parseFloat(el.style.top) || 0;
          el.style.setProperty("--reveal-delay", `${Math.round((top / height) * REVEAL_MS - (now - revealAt))}ms`);
        }
      }
      if (landing !== null) {
        // ranks among the boxes on this page, so the run plays out on the page in view
        const here = boxes.filter((el) => landing.ids.has(el.dataset.fieldId));
        here.sort((a, b) => landing.ids.get(a.dataset.fieldId) - landing.ids.get(b.dataset.fieldId));
        const ranks = new Map([...new Set(here.map((el) => el.dataset.fieldId))].map((id, i) => [id, i]));
        const step = Math.min(70, LAND_MS / Math.max(1, ranks.size));
        for (const el of here) {
          el.classList.add("is-landing");
          el.style.setProperty("--land-delay", `${Math.round(ranks.get(el.dataset.fieldId) * step - (now - landing.at))}ms`);
        }
      }
    },
  };
  return scan;
}
