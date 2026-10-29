// What the server is doing while it reads a PDF (its form map): the page under way and its step, a strip
// with a cell per page (waiting, being read, boxes found, skipped as prose, fillable, left for you to pick),
// the counts so far and a rough time left. `showReading(el, progress)` draws one progress report from
// `engine.readProgress` / `engine.waitForReading` (see engines/server/client.js) into `el`.

const STEPS = { text: "reading its text", ocr: "running OCR on it (a scanned page)", detect: "finding the blanks" };
const PHASES = {
  queued: "Opening the PDF…",
  features: "Opening the PDF and taking its fingerprint…",
  detectors: "Loading the field detectors (the first time downloads them, which can take a few minutes)…",
  finishing: "Finishing up…",
  done: "Read.",
};

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** `seconds` as m:ss. */
export function clock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A cell's state: pending, working, boxes, empty, prose, fillable or manual. */
export function cellState(page) {
  if (page.state === "pending") return "pending";
  if (page.state === "working") return "working";
  if (page.kind === "fillable") return "fillable";
  if (page.skipped) return page.skipped; // "prose" or "manual"
  return page.boxes > 0 ? "boxes" : "empty";
}

function cellTitle(page, index) {
  const where = `Page ${index + 1}`;
  switch (cellState(page)) {
    case "pending": return `${where}: waiting`;
    case "working": return `${where}: being read now`;
    case "fillable": return `${where}: fillable, ${plural(page.widgets, "field")} of its own`;
    case "prose": return `${where}: skipped, it reads as running text`;
    case "manual": return `${where}: not searched, you pick the pages`;
    case "boxes": return `${where}: ${plural(page.boxes, "box", "boxes")} found`;
    default: return `${where}: searched, no blanks found`;
  }
} // prettier-ignore

/** The line saying what is happening now. */
export function nowLine(progress) {
  const { phase, current, page_count: total } = progress;
  if (phase === "pages" && current) {
    const step = STEPS[current.step] ?? current.step;
    const by = current.step === "detect" && current.detectors?.length ? ` (${current.detectors.join(", ")})` : "";
    return `Page ${current.page + 1} of ${total}: ${step}${by}`;
  }
  if (phase === "pages") return progress.pages_done >= total ? PHASES.finishing : `Page ${progress.pages_done + 1} of ${total}`;
  return PHASES[phase] ?? "Reading the form…";
}

/** Counts so far and a rough time left: "41 boxes on 9 pages · 30 pages skipped as prose · 0:52 · about 2:10 left". */
export function sumLine(progress) {
  const pages = progress.pages;
  const states = pages.map(cellState);
  const count = (s) => states.filter((x) => x === s).length;
  const parts = [];
  const boxes = pages.reduce((n, p) => n + p.boxes, 0);
  parts.push(`${plural(boxes, "box", "boxes")} on ${plural(count("boxes"), "page")}`);
  if (count("fillable")) parts.push(`${plural(count("fillable"), "fillable page")}`);
  if (count("prose")) parts.push(`${plural(count("prose"), "page")} skipped as prose`);
  if (count("manual")) parts.push(`${plural(count("manual"), "page")} left for you to pick`);
  parts.push(clock(progress.elapsed));
  const done = progress.pages_done;
  const left = progress.page_count - done;
  if (progress.phase === "pages" && done >= 3 && left > 0) {
    const timed = pages.filter((p) => p.state === "done" && typeof p.seconds === "number");
    const each = timed.reduce((s, p) => s + p.seconds, 0) / Math.max(1, timed.length);
    parts.push(`about ${clock(each * left)} left`);
  }
  return parts.join(" · ");
}

/**
 * Draw `progress` into `el`: the line for now, a bar, the page strip and the counts. Cells are made once per
 * document and only their state changes after, so the strip does not flicker between polls.
 */
export function showReading(el, progress) {
  el.hidden = false;
  if (el.dataset.pages !== String(progress.page_count)) {
    el.dataset.pages = String(progress.page_count);
    const now = document.createElement("p");
    now.className = "reading-now";
    const bar = document.createElement("div");
    bar.className = "reading-bar";
    bar.append(document.createElement("span"));
    const strip = document.createElement("ol");
    strip.className = "reading-strip";
    strip.setAttribute("aria-label", "Pages");
    for (let i = 0; i < progress.page_count; i++) strip.append(document.createElement("li"));
    const sum = document.createElement("p");
    sum.className = "reading-sum";
    el.replaceChildren(now, bar, strip, sum);
  }
  const [now, bar, strip, sum] = el.children;
  now.textContent = nowLine(progress);
  const share = progress.page_count ? progress.pages_done / progress.page_count : 0;
  bar.firstChild.style.width = `${Math.round((progress.phase === "done" ? 1 : share) * 100)}%`;
  progress.pages.forEach((page, i) => {
    const cell = strip.children[i];
    const state = cellState(page);
    if (cell.dataset.state !== state) cell.dataset.state = state;
    cell.title = cellTitle(page, i);
  });
  sum.textContent = sumLine(progress);
}

/** Take the drawing away (and forget its cells, so the next document gets its own). */
export function hideReading(el) {
  el.hidden = true;
  el.replaceChildren();
  delete el.dataset.pages;
}
