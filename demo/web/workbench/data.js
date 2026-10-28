// Data to fill with, in the one-shot flow: what the person gave (a file, or pasted text) sent to the
// server as a dataset, and the data mapping a job made from it, shown as a table. The start screen
// and the fill drawer's "Add data" both use these. A dataset is a table (CSV, TSV, Excel, JSON
// records, or pasted text that is one of those) or free text; the job maps a table's columns onto
// the template's schema (directly, by a saved mapping or by the model) and has the model read free text.

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/**
 * Send the data to the server: the file when there is one, else the text. Resolves the dataset's
 * summary, or null when neither was given.
 * @param {object} engine
 * @param {{file?: File | null, text?: string}} input
 */
export async function sendData(engine, { file = null, text = "" }) {
  if (file) return engine.data.file(file);
  if (text.trim()) return engine.data.text(text);
  return null;
}

/** A dataset summary in words: "applicants.csv · table, 12 rows × 8 columns". */
export function describeData(summary) {
  if (summary.kind === "text") return `${summary.name} · free text, ${summary.chars.toLocaleString()} characters`;
  const what = { csv: "table", xlsx: "Excel sheet", json: "JSON records" }[summary.format] ?? "table";
  const cut = summary.truncated ? " (the first rows only)" : "";
  return `${summary.name} · ${what}, ${plural(summary.rows, "row")} × ${plural(summary.columns.length, "column")}${cut}`;
}

const MODES = {
  direct: "columns named by schema path, no AI",
  saved: "a mapping saved with this template, no AI",
  model: "mapped by the AI",
  text: "read from the text by the AI",
};

/** One line on how the data became records. */
export function mappingSummary(data) {
  if (data.error) return `couldn't be mapped`;
  const n = plural(data.total ?? data.records.length, "record");
  return `${n} · ${MODES[data.mode] ?? data.mode}`;
}

function transformText(t) {
  const args = Object.entries(t.args ?? {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`);
  return `${t.op}(${args.join(", ")})`;
}

function cell(tag, text, cls = "") {
  const el = document.createElement(tag);
  el.textContent = text;
  if (cls) el.className = cls;
  return el;
}

/**
 * Show a job's data result in `el`: the mapping's rules (schema path ← column, template or value,
 * with transforms), the columns left out, warnings and the model's notes.
 * @param {HTMLElement} el
 * @param {object} data the job's `result.data`
 */
export function renderMapping(el, data) {
  const parts = [];
  if (data.error) {
    parts.push(cell("p", data.error, "mapnote"));
    el.replaceChildren(...parts);
    return;
  }
  const rules = data.mapping?.rules ?? [];
  if (rules.length) {
    const table = document.createElement("table");
    table.className = "maptable";
    const head = document.createElement("tr");
    head.append(cell("th", "Form field"), cell("th", ""), cell("th", "From your data"));
    table.append(head);
    for (const r of rules) {
      const tr = document.createElement("tr");
      const from = r.column !== undefined ? r.column : r.template !== undefined ? r.template : JSON.stringify(r.value);
      const src = cell("td", from);
      if (r.transforms?.length) src.append(document.createElement("br"), cell("span", r.transforms.map(transformText).join(" → "), "tf"));
      tr.append(cell("td", r.path), cell("td", "←", "arrow"), src);
      table.append(tr);
    }
    parts.push(table);
  } else if (data.mode === "text") {
    parts.push(cell("p", "The AI read the text and wrote the records itself; pick one to see it as JSON.", "mapnote"));
  }
  const unmapped = data.mapping?.unmapped_columns ?? [];
  if (unmapped.length) parts.push(cell("p", `Not used: ${unmapped.join(", ")}.`, "mapnote"));
  for (const w of data.warnings ?? []) parts.push(cell("p", `Left out: ${w}.`, "mapnote"));
  if (data.notes) parts.push(cell("p", `The AI notes: ${data.notes}`, "mapnote"));
  el.replaceChildren(...parts);
}
