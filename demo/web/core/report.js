// Renders a fill report (Python's FillReport JSON, from either engine) into an element: counts by
// code with warnings first, then one row per field and a summary per table. Text is set with
// textContent only, so a value from a record can never become markup.

import { SEVERITY } from "./values.js";

/** [count key, tile label, severity]: the required tile comes first and reads as a warning. */
const TOTALS = [
  ["required_left_empty", "required left empty", "warning"],
  ["fields_written", "fields written"],
  ["fields_empty", "fields left empty"],
  ["cells_written", "table cells written"],
];

const BROWSER_NOTE =
  "Filled in this browser. Checks that need the full library, such as font substitution and adopting existing form fields, are not run here.";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const severityOf = (code) => SEVERITY[code] ?? "warning";

function valueText(v) {
  if (v === null || v === undefined) return "—";
  if (v === true) return "on";
  if (v === false) return "off";
  return String(v);
}

function warningList(warnings) {
  if (!warnings || warnings.length === 0) return null;
  const ul = el("ul", "fc-rp-warns");
  for (const w of warnings) {
    const li = el("li");
    li.dataset.severity = w.severity ?? severityOf(w.code);
    li.append(el("span", "fc-rp-code", w.code), ` ${w.message}`);
    ul.append(li);
  }
  return ul;
}

function counts(report) {
  const box = el("div", "fc-rp-counts");
  const stat = (n, label, severity) => {
    const d = el("div", "fc-rp-stat");
    if (severity) d.dataset.severity = severity;
    d.append(el("span", "fc-rp-n", String(n)), el("span", "fc-rp-k", label));
    return d;
  };
  const all = report.counts ?? {};
  const codes = Object.keys(all).filter((k) => !TOTALS.some(([key]) => key === k));
  const bySeverity = (s) => codes.filter((c) => severityOf(c) === s).sort();
  for (const [key, label, severity] of TOTALS) {
    if (key in all) box.append(stat(all[key], label, severity));
  }
  for (const code of bySeverity("warning")) box.append(stat(all[code], code.replaceAll("_", " "), "warning"));
  for (const code of bySeverity("info")) box.append(stat(all[code], code.replaceAll("_", " "), "info"));
  return box;
}

function fieldRows(report) {
  const table = el("table", "fc-rp-table");
  const head = el("tr");
  for (const h of ["Field", "Value", "Binding", "Notes"]) head.append(el("th", "", h));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  for (const f of report.fields ?? []) {
    const tr = el("tr");
    tr.dataset.field = f.id;
    const id = el("td", "fc-rp-id", f.id);
    if (f.paths?.length) id.append(el("span", "fc-rp-path", f.paths.join(", ")));
    const notes = el("td");
    const list = warningList(f.warnings);
    if (list) notes.append(list);
    tr.append(id, el("td", "fc-rp-val", valueText(f.value)), el("td", "", f.binding ?? "none"), notes);
    body.append(tr);
  }
  table.append(body);
  return table;
}

function tableSummaries(report) {
  const box = el("div", "fc-rp-tables");
  for (const t of report.tables ?? []) {
    const d = el("div", "fc-rp-tbl");
    d.dataset.table = t.id;
    const dropped = t.rows_dropped > 0 ? `, ${t.rows_dropped} dropped` : "";
    d.append(el("b", "", t.id), ` ${t.rows_written} of ${t.rows_provided} rows written${dropped}`);
    const list = warningList(t.warnings);
    if (list) d.append(list);
    box.append(d);
  }
  return box;
}

/**
 * Show a fill report.
 * @param {HTMLElement} target
 * @param {object} report a FillReport (`writer: "browser"` marks the browser fill)
 */
export function renderReport(target, report) {
  target.classList.add("fc-report");
  const parts = [counts(report)];
  if (report.fingerprint_ok === false) {
    parts.push(el("p", "fc-rp-note", "This template was made for a different form."));
  }
  const top = warningList(report.warnings);
  if (top) parts.push(top);
  if ((report.fields ?? []).length > 0) parts.push(fieldRows(report));
  if ((report.tables ?? []).length > 0) parts.push(tableSummaries(report));
  const required = report.required_gaps ?? [];
  const others = (report.gaps ?? []).filter((id) => !required.includes(id));
  if (required.length > 0) parts.push(el("p", "fc-rp-note fc-rp-required", `Required boxes left empty: ${required.join(", ")}`));
  if (others.length > 0) parts.push(el("p", "fc-rp-note", `Other empty boxes: ${others.join(", ")}`));
  if (report.writer === "browser") parts.push(el("p", "fc-rp-note fc-rp-writer", BROWSER_NOTE));
  target.replaceChildren(...parts);
}
