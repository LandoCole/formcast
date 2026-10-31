// Dragging the side columns of every `.bench` (filling, the setup screen, the review) wider. Each bench gets a grip on
// the inner edge of each side column; a drag, or the arrow keys on a grip, sets `--pane-left` or `--pane-right` on the
// page, so the columns of all three stay the same width, and a double click puts that column back. The widths they open
// at (MIN) are the narrowest they go, and the page column always keeps PAGE_MIN. Below 1024px the columns stack and
// workbench.css hides the grips. The widths are remembered in this browser, where it lets them be.

/** The narrowest each side column goes: the widths the workbench opens at. */
const MIN = { left: 240, right: 300 };
/** The narrowest the page column between them goes. */
const PAGE_MIN = 360;
/** Pixels an arrow key moves a grip. */
const STEP = 16;
const KEY = "formcast.panes";
const NAMES = { left: "Resize the left panel", right: "Resize the right panel" };

function remembered() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) ?? {};
  } catch {
    return {};
  }
}

/** Show the grips and apply the widths this browser remembers. */
export function mountPanes() {
  const saved = remembered();
  const widths = { left: Number(saved.left) || MIN.left, right: Number(saved.right) || MIN.right };
  const grips = [];

  /** The width `side` can take: at least its MIN, leaving the other side and PAGE_MIN of the window. */
  function bounded(side, width) {
    const other = side === "left" ? "right" : "left";
    const most = Math.max(MIN[side], innerWidth - widths[other] - PAGE_MIN);
    return Math.round(Math.min(most, Math.max(MIN[side], width)));
  }

  function set(side, width) {
    widths[side] = bounded(side, width);
    document.documentElement.style.setProperty(`--pane-${side}`, `${widths[side]}px`);
    for (const grip of grips) {
      if (grip.dataset.side === side) grip.setAttribute("aria-valuenow", String(widths[side]));
    }
  }

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(widths));
    } catch {
      // a private window, say: the widths last until the page closes
    }
  }

  function grip(bench, side) {
    const el = document.createElement("div");
    el.className = `pane-grip ${side}`;
    el.dataset.side = side;
    el.tabIndex = 0;
    el.title = "Drag to resize; double click to reset";
    el.setAttribute("role", "separator");
    el.setAttribute("aria-orientation", "vertical");
    el.setAttribute("aria-label", NAMES[side]);
    el.setAttribute("aria-valuemin", String(MIN[side]));
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing-pane");
    });
    el.addEventListener("pointermove", (e) => {
      if (!el.hasPointerCapture(e.pointerId)) return;
      const box = bench.getBoundingClientRect();
      set(side, side === "left" ? e.clientX - box.left : box.right - e.clientX);
    });
    el.addEventListener("lostpointercapture", () => {
      document.body.classList.remove("resizing-pane");
      save();
    });
    el.addEventListener("dblclick", () => {
      set(side, MIN[side]);
      save();
    });
    el.addEventListener("keydown", (e) => {
      const wider = side === "left" ? "ArrowRight" : "ArrowLeft";
      const narrower = side === "left" ? "ArrowLeft" : "ArrowRight";
      if (e.key !== wider && e.key !== narrower) return;
      e.preventDefault();
      set(side, widths[side] + (e.key === wider ? STEP : -STEP));
      save();
    });
    bench.append(el);
    grips.push(el);
  }

  for (const bench of document.querySelectorAll(".bench")) {
    grip(bench, "left");
    grip(bench, "right");
  }
  set("left", widths.left);
  set("right", widths.right);
  addEventListener("resize", () => {
    set("left", widths.left);
    set("right", widths.right);
  });
}
