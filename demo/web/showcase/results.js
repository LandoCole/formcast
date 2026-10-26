// The Results section of the showcase page, rendered from results.json (demo/tools/build_showcase.py
// writes it from the committed eval files). Every number on it comes from that file: the headline
// run (the current code, with page images), one row per kind of form, in three plain measures, and
// a line comparing it with the same code run text-only.

const GROUP_ORDER = ["fillable", "digital", "scanned", "all"];

/** The row names and their one-line explanations, in plain words. */
const GROUPS = {
  fillable: { label: "Fillable PDFs", text: "PDF forms that already have form fields." },
  digital: { label: "Flat PDFs", text: "PDF forms with printed lines and boxes but no form fields." },
  scanned: { label: "Scanned copies", text: "The same forms turned into page images, as from a scanner." },
  all: { label: "All forms", text: "" },
};

/** The measures shown, with what each means. */
const MEASURES = [
  { key: "recall_50", label: "Blanks found", text: "Share of the form's blanks that the template has a box for." },
  { key: "path", label: "Linked to the right field", text: "Share of the blanks that take data that are linked to the right field of the JSON Schema." },
  { key: "fill_acc", label: "Filled correctly", text: "Share of values that land in the right blank when test records are filled in." },
];

/** A share as a whole percentage ("63%"); "–" when there is no figure. */
export function percent(v) {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "–";
}

function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) node.append(child);
  return node;
}

/** The headline run's row for each group, in table order. */
export function headlineRows(res) {
  return GROUP_ORDER.map((group) =>
    res.agent_dev.find((r) => r.headline && r.group === group && r.sessions_of === null),
  ).filter(Boolean);
}

/**
 * What was tested, in one sentence worked out from the headline rows.
 * @param {object} res results.json
 * @returns {string}
 */
export function leadText(res) {
  const by = Object.fromEntries(headlineRows(res).map((r) => [r.group, r]));
  if (!by.fillable || !by.digital) return "";
  const forms = by.fillable.sessions + by.digital.sessions;
  const model = by.fillable.label.startsWith("Claude") ? by.fillable.label : `Claude ${by.fillable.label}`;
  return `Scored box by box against an answer key on ${forms} public forms, with ${model} as the agent.`;
}

/**
 * How the text-only run compares, in one sentence over all forms; "" when results.json has no such run.
 * @param {object} res results.json
 * @returns {string}
 */
export function compareText(res) {
  const all = (run) => res.agent_dev.find((r) => r.run === run && r.group === "all" && r.sessions_of === null);
  const vision = headlineRows(res).find((r) => r.group === "all");
  const text = res.text_only_run && all(res.text_only_run);
  if (!vision || !text) return "";
  return (
    `The model sees each page as an image. Run on the same forms from the page's text and positions alone, ` +
    `it linked ${percent(text.path)} of the blanks to the right field, against ${percent(vision.path)}, ` +
    `and filled ${percent(text.fill_acc)} correctly, against ${percent(vision.fill_acc)}.`
  );
}

function resultsTable(res) {
  const head = el("tr", {}, [
    el("th", { scope: "col" }, ["Form type"]),
    ...MEASURES.map((m) => el("th", { scope: "col" }, [m.label])),
  ]);
  const body = el("tbody");
  for (const r of headlineRows(res)) {
    const tr = el("tr", {}, [
      el("th", { scope: "row" }, [GROUPS[r.group].label]),
      ...MEASURES.map((m) => el("td", {}, [percent(r[m.key])])),
    ]);
    tr.dataset.group = r.group;
    if (r.group === "all") tr.className = "pick";
    body.append(tr);
  }
  return el("table", {}, [el("thead", {}, [head]), body]);
}

function defs(entries) {
  return entries.flatMap(({ label, text }) => [el("dt", {}, [label]), el("dd", {}, [text])]);
}

/**
 * Fill the Results section from results.json.
 * @param {object} res the parsed results.json
 * @param {(id: string) => HTMLElement} $ element lookup by id
 */
export function renderResults(res, $) {
  $("results-lead").textContent = leadText(res);
  $("agent-table").replaceChildren(resultsTable(res));
  $("agent-defs").replaceChildren(
    ...defs(MEASURES),
    ...defs(GROUP_ORDER.filter((g) => GROUPS[g].text).map((g) => GROUPS[g])),
  );
  $("agent-note").textContent =
    "On a fillable form the template starts from the form's own fields, so every blank is found by design.";
  $("agent-compare").textContent = compareText(res);
}

/**
 * Load results.json and render the section; on failure, say so in the lead.
 * `#results` carries data-state "loading", then "ready" or "failed".
 * @param {{url: URL | string, $: (id: string) => HTMLElement, timeoutMs?: number}} opts
 */
export async function mountResults({ url, $, timeoutMs = 20_000 }) {
  const section = $("results");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderResults(await res.json(), $);
    section.dataset.state = "ready";
  } catch {
    $("results-lead").textContent = "The results couldn't be loaded. Reload the page to try again.";
    section.dataset.state = "failed";
  } finally {
    clearTimeout(timer);
  }
}
