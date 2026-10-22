// The Results section of the showcase page, rendered from results.json (demo/tools/build_showcase.py
// writes it from the committed eval files). Every number, label and explanation on it comes from
// that file; this module only lays them out and words the lead from the figures.

const GROUP_ORDER = ["all", "fillable", "digital", "scanned"];
const AGENT_METRICS = ["recall_50", "precision_30", "path", "joint", "fill_acc"];
const DETECTION_METRICS = ["recall_50", "recall_30", "precision_30"];

/** A share as two decimals ("0.63"); "–" when there is no figure. */
export function fixed2(v) {
  return typeof v === "number" ? v.toFixed(2) : "–";
}

/** A share as a whole percentage ("63%"). */
export function percent(v) {
  return `${Math.round(v * 100)}%`;
}

/** Input tokens per session in thousands ("411K"); "0" for none. */
export function thousands(v) {
  return v ? `${Math.round(v / 1000)}K` : "0";
}

function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) node.append(child);
  return node;
}

/** A row header: the name on one line, the detail under it. */
function rowHeader(label, detail) {
  const th = el("th", { scope: "row" }, [label]);
  if (detail) th.append(el("span", { className: "k" }, [detail]));
  return th;
}

function table(caption, headers, bodies) {
  const head = el("tr", {}, headers.map((h) => el("th", { scope: "col" }, [h])));
  return el("table", {}, [el("caption", {}, [caption]), el("thead", {}, [head]), ...bodies]);
}

/** One tbody: a heading row spanning the table, then the rows. */
function tbody(heading, columns, rows, group) {
  const body = el("tbody");
  if (group) body.dataset.group = group;
  body.append(el("tr", { className: "grp" }, [el("th", { scope: "rowgroup", colSpan: columns }, [heading])]));
  for (const row of rows) body.append(row);
  return body;
}

function agentTable(res) {
  const m = res.metrics;
  const headers = ["Run", "Sessions", ...AGENT_METRICS.map((k) => m[k].label), m.steps.label, m.input_tokens.label];
  const bodies = GROUP_ORDER.map((group) => {
    const rows = res.agent_dev
      .filter((r) => r.group === group)
      .map((r) => {
        const tr = el("tr", {}, [
          rowHeader(r.label, r.detail),
          el("td", {}, [String(r.sessions)]),
          ...AGENT_METRICS.map((k) => el("td", {}, [fixed2(r[k])])),
          el("td", {}, [r.steps.toFixed(1)]),
          el("td", {}, [thousands(r.input_tokens)]),
        ]);
        if (r.headline) tr.className = "pick";
        return tr;
      });
    return tbody(res.groups[group].label, headers.length, rows, group);
  });
  return table("Dev forms · per session, averaged over the answer key's boxes", headers, bodies);
}

/** Rows of detector figures grouped by form kind and variant, in the file's order. */
function detectorTable(caption, rows, res, { gt = false } = {}) {
  const m = res.metrics;
  const headers = ["Detector", ...DETECTION_METRICS.map((k) => m[k].label)];
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.label)) groups.set(r.label, []);
    groups.get(r.label).push(r);
  }
  const bodies = [...groups].map(([label, rs]) => {
    const trs = rs.map((r) => {
      const d = res.detectors[r.detector] ?? { label: r.detector, text: "" };
      const tr = el("tr", {}, [rowHeader(d.label), ...DETECTION_METRICS.map((k) => el("td", {}, [fixed2(r[k])]))]);
      if (r.detector === "FFDNet-S-onnx") tr.className = "pick";
      return tr;
    });
    const heading = gt && rs[0].gt ? `${label} · ${rs[0].gt.toLocaleString("en-US")} boxes in the answer key` : label;
    return tbody(heading, headers.length, trs);
  });
  return table(caption, headers, bodies);
}

function notes(texts) {
  return texts.map((t) => el("p", { className: "note" }, [t]));
}

function defs(entries) {
  return entries.flatMap(({ label, text }) => [el("dt", {}, [label]), el("dd", {}, [text])]);
}

/**
 * The headline in plain words, from the current code's path by group.
 * @param {object} res results.json
 * @returns {string}
 */
export function leadText(res) {
  const head = (g) => res.agent_dev.find((r) => r.headline && r.group === g && r.sessions_of === null);
  const all = head("all");
  if (!all) return "";
  const by = ["fillable", "digital", "scanned"].map(head);
  return (
    `With the current code, the agent links ${percent(all.path)} of the boxes that take data to the right schema path: ` +
    `${percent(by[0].path)} on fillable forms, ${percent(by[1].path)} on flat ones and ${percent(by[2].path)} on scans.`
  );
}

/**
 * How to read the agent table against its floor (the seed) and the best earlier mapping, worded
 * from the figures so it follows them.
 * @param {object} res results.json
 * @returns {string}
 */
export function readingText(res) {
  const all = res.agent_dev.filter((r) => r.group === "all" && r.sessions_of === null);
  const seed = all.find((r) => r.model === "none");
  const current = all.find((r) => r.headline);
  if (!seed || !current) return "";
  const kept = current.recall_50 >= seed.recall_50;
  let text =
    `The seeded template is the floor. With the current code the agent ${kept ? "keeps" : "loses some of"} its boxes ` +
    `(recall ${fixed2(current.recall_50)} against the seed's ${fixed2(seed.recall_50)}) and adds the links the seed ` +
    `doesn't have (path ${fixed2(current.path)}, fill ${fixed2(current.fill_acc)}).`;
  const best = all
    .filter((r) => r !== current && r.model === current.model)
    .sort((a, b) => b.path - a.path)[0];
  if (best && best.path > current.path && best.recall_50 < current.recall_50) {
    text +=
      ` The ${best.detail.split(",")[0]} run linked more (path ${fixed2(best.path)}) but replaced correct boxes ` +
      `with coarser ones (recall ${fixed2(best.recall_50)}).`;
  }
  return text;
}

/**
 * Fill the Results section from results.json.
 * @param {object} res the parsed results.json
 * @param {(id: string) => HTMLElement} $ element lookup by id
 */
export function renderResults(res, $) {
  $("results-lead").textContent = leadText(res);
  $("agent-table").replaceChildren(agentTable(res));
  $("caveats").replaceChildren(
    el("p", { className: "note" }, [el("b", {}, ["Caveats. "]), res.caveats.join(" ")]),
  );
  $("agent-reading").textContent = readingText(res);
  const metricKeys = [...AGENT_METRICS, "steps", "input_tokens"];
  $("agent-defs").replaceChildren(
    ...defs(metricKeys.map((k) => res.metrics[k])),
    ...defs(GROUP_ORDER.map((g) => res.groups[g])),
  );
  $("agent-notes").replaceChildren(
    ...notes([res.notes.averaging, res.notes.seed, res.notes.errored_sessions, ...res.notes.partial_runs]),
  );

  const h = res.heldout_status;
  $("heldout").replaceChildren(
    el("p", { className: "heldout-line" }, [
      el("b", {}, [`${h.sessions_completed} of ${h.sessions_planned}`]),
      " held-out agent sessions finished.",
    ]),
    ...notes([h.note, `Source: ${h.source}.`]),
  );

  $("browser-table").replaceChildren(
    detectorTable("Dev forms · each setup on the same pages", res.browser_models, res),
  );
  const browserDetectors = ["FFDNet-L", "FFDNet-L-onnx", "FFDNet-S-onnx"];
  $("browser-notes").replaceChildren(
    ...notes([res.notes.browser_models, res.metrics.recall_30.text]),
    el("dl", { className: "defs" }, defs(browserDetectors.map((d) => res.detectors[d]))),
  );

  $("detection-dev").replaceChildren(detectorTable("Dev forms", res.detection.dev, res, { gt: true }));
  $("detection-heldout").replaceChildren(detectorTable("Held-out forms", res.detection.heldout, res, { gt: true }));
  $("detection-notes").replaceChildren(
    ...notes([res.notes.bare_detector, res.notes.heldout_scan]),
    el("dl", { className: "defs" }, defs([res.detectors.FFDetr])),
  );
  $("sources").textContent = `Read from ${res.generated_from.join(", ")}.`;
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
