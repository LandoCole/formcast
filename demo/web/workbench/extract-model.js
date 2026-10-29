// Extraction's page logic, with no DOM (node-tested in tests/web/extract_model.test.js): what the extraction
// screens say about a setup, a run and a batch, worked out from the server's JSON.
//
// A setup's proof (plan 2's formcast.extract.proof) has, for every concrete path, a FieldProof `{path, ready, absent,
// samples: [{file, outcome, read, opinion}], suggested}`: each sample's `outcome` is `agree` (the readers read what
// the model read), `disagree`, `missing` (neither has a value), `failed_check` (both read the same value, which fails
// the schema's checks: the form holds a bad answer, not a bad reader) or `absent` (the path is marked not on this
// form); `read` is the readers' FieldResult `{status, value, raw, reasons, page, rect}`. A field's row in the list
// (`fieldState`) counts the samples proven (agree or failed_check) out of all of them, with a glyph as the mockup's
// legend has it: ● every sample, ◐ some, ? an unsure read, × none, ○ not on this form. `outcomeLine` is one sample's
// line under the field. A repeated group's fields are read per item (`owners[0].name` ... `owners[3].name`), and
// `groupFields` folds them back into one row per field of the group (`owners[ ] · up to 4`), whose items' proofs
// `rowProof` takes together for the row's state.
//
// A field's schema says how it is checked (`checksOf`) and its reader how it is found (`readFrom`), each as lines of
// plain words; `fieldSchema` finds a concrete path's schema node. A batch (`batchLine`, `stripCells`, `batchNote`) is
// told by its scan: how many of its PDFs are copies of its largest layout, the rest being set aside for another
// template. A run's review says its numbers (`runStats`, `runTime`), each document's open problems (`docLine`) or why
// it was set aside (`asideLine`), a decision made (`decisionText`), a saved run in a line (`runLine`) and what Send to
// fill warns of first (`sendNotice`).

/** Items a repeated group has when its schema gives no `maxItems`, as the library gives it (formcast.extract.model). */
const DEFAULT_ITEMS = 4;
/** Outcomes that prove a reader: it read the model's answer (a failed check fails the answer, not the reader). */
const PROVEN = new Set(["agree", "failed_check"]);
/** Each problem status in words, as the review lists them. */
const PROBLEM_LABELS = { missing: "Missing", failed_check: "Fails a check", unsure: "Unsure" };

const number = (n) => Number(n ?? 0).toLocaleString("en-US");
const plural = (n, word, many = `${word}s`) => `${number(n)} ${n === 1 ? word : many}`;
const either = (words) => words.join(" or ");
const blank = (v) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);

/** A read value as text: a list's items joined, yes or no for a boolean, "—" for nothing. */
function shown(value) {
  if (blank(value)) return "—";
  if (Array.isArray(value)) return value.map(shown).join(" · ");
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ------------------------------------------------------------------ a setup's fields

/**
 * A field's row state from its FieldProof: the glyph, the samples proven out of all of them, and `ready`, `weak` or
 * `absent` (see the top).
 * @param {{ready: boolean, absent: boolean, samples: {outcome: string, read?: {status: string}}[]}} fieldProof
 * @returns {{glyph: string, text: string, state: "ready" | "weak" | "absent"}}
 */
export function fieldState(fieldProof) {
  if (fieldProof.absent) return { glyph: "○", text: "not on this form", state: "absent" };
  const samples = fieldProof.samples ?? [];
  const proven = samples.filter((s) => PROVEN.has(s.outcome)).length;
  let glyph = "×";
  if (samples.length > 0 && proven === samples.length) glyph = "●";
  else if (!fieldProof.ready && samples.some((s) => s.read?.status === "unsure")) glyph = "?";
  else if (proven > 0) glyph = "◐";
  return { glyph, text: `${proven}/${samples.length}`, state: fieldProof.ready ? "ready" : "weak" };
}

/**
 * How a field is read, one line per strategy of its reader, in the order they are tried.
 * @param {{strategies: object[]} | undefined} reader the template's reader for the path
 * @param {object} [schemaProp] the path's schema: on a yes-or-no field a lone check mark reads as a checkbox
 * @returns {string[]}
 */
export function readFrom(reader, schemaProp = {}) {
  return (reader?.strategies ?? []).map((s) => {
    switch (s.kind) {
      case "widget":
        return `Form field ${either(s.names)}`;
      case "box":
        return `A box on page ${s.page + 1}`;
      case "anchor":
        return `${s.side === "below" ? "Below" : "Next to"} the label ${either(s.labels)}`;
      case "phrase":
        return `Page text, after ${either(s.phrases)}`;
      case "choice": {
        const keys = Object.keys(s.options ?? {});
        if (schemaProp?.type === "boolean" && keys.length === 1) return `A checkbox, checked for ${keys[0]}`;
        return `Checkbox group, ${plural(keys.length, "box", "boxes")}`;
      }
      default:
        return s.kind;
    }
  });
}

/**
 * A field's checks in words: required, its format, a date, its allowed answers, its range.
 * @param {object} [schemaProp] the path's schema
 * @param {boolean} [required] whether its object requires it
 * @returns {string[]}
 */
export function checksOf(schemaProp = {}, required = false) {
  const p = schemaProp ?? {};
  const out = [];
  if (required) out.push("Required");
  if (typeof p.pattern === "string") out.push(`Format ${p.pattern}`);
  if (p.format === "date") out.push("Is a date");
  else if (p.format === "date-time") out.push("Is a date and time");
  else if (p.format === "email") out.push("Is an email address");
  if (Array.isArray(p.enum)) out.push(`One of ${p.enum.map(String).join(", ")}`);
  else if (Array.isArray(p.items?.enum)) out.push(`Any of ${p.items.enum.map(String).join(", ")}`);
  const low = typeof p.minimum === "number" ? p.minimum : null;
  const high = typeof p.maximum === "number" ? p.maximum : null;
  if (low !== null && high !== null) out.push(`Between ${low} and ${high}`);
  else if (low !== null) out.push(`At least ${low}`);
  else if (high !== null) out.push(`At most ${high}`);
  return out;
}

/**
 * One sample's line under a field: ● the value read, × why not (its first reason; for a disagreement with none,
 * what each side read), ○ not on this form.
 * @param {{outcome: string, read?: {value: any, reasons?: string[]}, opinion?: any}} sampleProof
 * @returns {{glyph: string, text: string}}
 */
export function outcomeLine(sampleProof) {
  const { outcome, read = {}, opinion } = sampleProof;
  if (outcome === "absent") return { glyph: "○", text: "not on this form" };
  if (outcome === "agree") return { glyph: "●", text: shown(read.value ?? read.raw) };
  const reason = read.reasons?.[0];
  if (reason) return { glyph: "×", text: reason };
  if (outcome === "disagree") {
    const said = (v) => (blank(v) ? "nothing" : shown(v));
    return { glyph: "×", text: `read ${said(read.value)}, the AI read ${said(opinion)}` };
  }
  return { glyph: "×", text: outcome === "failed_check" ? "fails a check" : "missing" };
}

// ------------------------------------------------------------------ schema paths

/** `#/$defs/x`-style local references followed (once each step), so a field's node is its own. */
function deref(schema, node) {
  let out = node;
  for (let hops = 0; out && typeof out.$ref === "string" && out.$ref.startsWith("#/") && hops < 8; hops++) {
    out = out.$ref
      .slice(2)
      .split("/")
      .reduce((n, key) => n?.[key.replaceAll("~1", "/").replaceAll("~0", "~")], schema);
  }
  return out ?? undefined;
}

/** A path's segments: `owners[2].name` -> [{key: "owners", item: true}, {key: "name", item: false}]. */
function segments(path) {
  return path.split(".").map((part) => {
    const m = /^(.*?)((?:\[\d*\])*)$/.exec(part);
    return { key: m[1], item: m[2] !== "" };
  });
}

/**
 * The schema of a field, by its concrete (`owners[2].name`) or generic (`owners[].name`) path, and whether the
 * object holding it requires it. `{prop: {}, required: false}` for a path the schema does not have.
 * @param {object} schema
 * @param {string} path
 * @returns {{prop: object, required: boolean}}
 */
export function fieldSchema(schema, path) {
  let node = deref(schema, schema);
  let required = false;
  for (const { key, item } of segments(path)) {
    required = Array.isArray(node?.required) && node.required.includes(key);
    node = deref(schema, node?.properties?.[key]);
    if (item) node = deref(schema, node?.items);
    if (node === undefined) return { prop: {}, required: false };
  }
  return { prop: node, required };
}

/** `owners[2].name` -> `owners[].name`. */
const genericOf = (path) => path.replace(/\[\d+\]/g, "[]");

/** The array node a generic path's first `[]` is the items of, for its `maxItems`. */
function firstArray(schema, generic) {
  const head = generic.slice(0, generic.indexOf("[]"));
  let node = deref(schema, schema);
  for (const key of head.split(".")) node = deref(schema, node?.properties?.[key]);
  return node ?? {};
}

/**
 * A setup's fields in groups for the field list: a group per top-level object (`investor`), a repeated group's
 * fields under `owners[ ] · up to 4` with each field's item paths folded into one row, and top-level fields of no
 * object under the group "". A row is `{path, title, generic, items, paths}`: `path` its first concrete path (the
 * only one for a plain field), `generic` its path with the item numbers taken out, `items` the number of items
 * folded (null for a plain field), `paths` every concrete path in it. Groups and rows keep the proof's order.
 * @param {{fields: {path: string}[]}} proof
 * @param {object} schema
 * @returns {{group: string, items: {path: string, title: string, generic: string, items: number | null, paths: string[]}[]}[]}
 */
export function groupFields(proof, schema) {
  const groups = new Map();
  const rows = new Map(); // generic path -> its row
  for (const { path } of proof.fields ?? []) {
    const generic = genericOf(path);
    const repeated = generic !== path;
    const known = rows.get(generic);
    if (known) {
      known.paths.push(path);
      if (repeated) known.items = known.paths.length;
      continue;
    }
    let group = path.includes(".") ? path.split(".")[0] : "";
    if (repeated) {
      const head = generic.slice(0, generic.indexOf("[]"));
      const max = firstArray(schema, generic).maxItems;
      group = `${head}[ ] · up to ${Number.isInteger(max) && max > 0 ? max : DEFAULT_ITEMS}`;
    }
    const { prop } = fieldSchema(schema, path);
    const leaf = segments(generic).at(-1).key;
    const row = { path, title: typeof prop.title === "string" && prop.title ? prop.title : leaf, generic, items: repeated ? 1 : null, paths: [path] };
    rows.set(generic, row);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(row);
  }
  return [...groups].map(([group, items]) => ({ group, items }));
}

/** A sample's outcomes from worst to best, for a folded row: the one shown is the first an item has. */
const WORST_FIRST = ["disagree", "failed_check", "agree", "missing"];

/**
 * The FieldProof a row of the field list (`groupFields`) stands for: a plain row's own; for a repeated group's
 * field folded from its items, the items' proofs taken together. On each sample the row has the worst outcome of
 * its items (disagree, failed_check, agree, missing, in that order; absent when every item is), it is absent when
 * every item is, and ready when every item that some sample has is ready (an item on no sample, such as a fourth
 * owner nobody has, proves nothing either way).
 * @param {{path: string, paths: string[]}} row
 * @param {{fields: object[]}} proof
 * @returns {{path: string, ready: boolean, absent: boolean, samples: object[], suggested: object}}
 */
export function rowProof(row, proof) {
  const byPath = new Map((proof.fields ?? []).map((f) => [f.path, f]));
  const items = row.paths.map((p) => byPath.get(p)).filter(Boolean);
  if (items.length === 1) return items[0];
  if (items.length === 0) return { path: row.path, ready: false, absent: false, samples: [], suggested: {} };
  const present = items.filter((f) => !f.absent);
  const count = Math.max(...items.map((f) => f.samples.length));
  const samples = Array.from({ length: count }, (_, i) => {
    const there = present.map((f) => f.samples[i]).filter(Boolean);
    for (const outcome of WORST_FIRST) {
      const found = there.find((s) => s.outcome === outcome);
      if (found) return found;
    }
    return there[0] ?? items[0].samples[i];
  });
  const tried = present.filter((f) => f.samples.some((s) => s.outcome !== "missing"));
  return { path: row.path, absent: present.length === 0, ready: tried.length > 0 && tried.every((f) => f.ready), samples, suggested: {} };
}

// ------------------------------------------------------------------ runs and batches

/**
 * A review problem's status in words.
 * @param {string} status `missing`, `failed_check` or `unsure`
 * @returns {string}
 */
export function problemLabel(status) {
  return PROBLEM_LABELS[status] ?? status;
}

/** Seconds as the review's run time: m:ss, or h:mm:ss from an hour. */
export function runTime(seconds) {
  const total = Math.round(Number(seconds) || 0);
  const two = (n) => String(n).padStart(2, "0");
  const [h, m, s] = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}

/**
 * The review's numbers from a run's summary: its documents, the complete ones, the ones with problems, the ones set
 * aside (no template fits, unreadable, or an internal error: not read) and the run time.
 * @param {{total: number, complete: number, with_problems: number, no_template: number, unreadable: number,
 *   internal_errors: number, seconds: number}} summary a RunSummary
 * @returns {{key: string, value: string, label: string}[]}
 */
export function runStats(summary) {
  const aside = (summary.no_template ?? 0) + (summary.unreadable ?? 0) + (summary.internal_errors ?? 0);
  return [
    { key: "total", value: number(summary.total), label: "Documents" },
    { key: "complete", value: number(summary.complete), label: "Complete" },
    { key: "with_problems", value: number(summary.with_problems), label: "With problems" },
    { key: "set_aside", value: number(aside), label: "Set aside" },
    { key: "time", value: runTime(summary.seconds), label: "Run time" },
  ];
}

/**
 * A listed document's line: its problems not yet settled, two by path and more as the first and a count; or "all
 * settled".
 * @param {{path: string}[]} problems the document's problems
 * @param {Set<string>} [settled] the paths decided in this review
 * @returns {string}
 */
export function docLine(problems, settled = new Set()) {
  const open = problems.filter((p) => !settled.has(p.path)).map((p) => p.path);
  if (open.length === 0) return "all settled";
  const which = open.length > 2 ? `${open[0]} +${open.length - 1} others` : open.join(", ");
  return `${plural(open.length, "problem")}: ${which}`;
}

/** Why a document was set aside, with the closest template and its score when there is one. */
export function asideLine(doc) {
  if (!doc.closest_template) return doc.reason;
  return `${doc.reason} · closest: ${doc.closest_template}, ${Math.round((doc.closest_score ?? 0) * 100)}%`;
}

/**
 * What a review decision settled, as its card says it.
 * @param {"value" | "accept" | "not_in_document"} decision
 * @param {unknown} [value] the value typed, or the one accepted
 * @returns {string}
 */
export function decisionText(decision, value) {
  if (decision === "value") return `Value entered: ${shown(value)}`;
  if (decision === "accept") return `Accepted as read: ${shown(value)}`;
  return "Marked not in this document";
}

/** A saved run in one line: its name, its template, its documents and how many still have problems. */
export function runLine(run) {
  const s = run.summary ?? {};
  const problems = s.with_problems ? `${number(s.with_problems)} with problems` : "no problems";
  return [run.name, run.extraction, plural(s.total ?? 0, "document"), problems].join(" · ");
}

/**
 * What Send to fill says first (ruling P13): the documents that still have problems, whose unsettled fields are gaps,
 * and a dataset cut short (`truncated`); "" when neither.
 * @param {{dataset: {rows: number}, truncated: boolean, with_problems: number}} sent the run's `toData`
 * @returns {string}
 */
export function sendNotice(sent) {
  const parts = [];
  const n = sent.with_problems ?? 0;
  if (n > 0) {
    const [has, its] = n === 1 ? ["has", "its"] : ["have", "their"];
    parts.push(`${plural(n, "document")} still ${has} problems; ${its} unsettled fields are left blank, and fill will report them as gaps.`);
  }
  if (sent.truncated) {
    parts.push(`Only the first ${number(sent.dataset?.rows)} documents are in the data, as many as a dataset holds; export the run for every one.`);
  }
  return parts.join(" ");
}

/** Bytes as the batch line gives them: MB from one, else KB. */
function size(bytes) {
  if (bytes >= 1e6) return `${number(Math.round(bytes / 1e6))} MB`;
  return `${number(Math.round(bytes / 1e3))} KB`;
}

/** The batch's largest scan group (its layout), or null before the scan is done or when it found none. */
function mainGroup(batch) {
  if (batch.scan?.phase !== "done") return null;
  return (batch.scan.groups ?? []).reduce((best, g) => (best === null || g.size > best.size ? g : best), null);
}

/**
 * A batch in one line: its PDFs, their size, and how many are copies of its layout (the rest set aside), or how
 * the scan is going.
 * @param {{count: number, bytes: number, scan: {phase: string, done: number, total: number, error: string | null,
 *   groups: {size: number}[]}}} batch a Batch
 * @returns {string}
 */
export function batchLine(batch) {
  const parts = [plural(batch.count, "PDF"), size(batch.bytes ?? 0)];
  const scan = batch.scan ?? {};
  if (scan.phase === "error") parts.push(`the scan failed: ${scan.error}`);
  else if (scan.phase !== "done") parts.push(`scanning, ${number(scan.done)} of ${number(scan.total)}`);
  else if (batch.count > 0) {
    const main = mainGroup(batch)?.size ?? 0;
    const aside = batch.count - main;
    parts.push(`${plural(main, "fillable or flattened copy", "fillable or flattened copies")} of one layout${aside > 0 ? `, ${number(aside)} set aside` : ""}`);
  }
  return parts.join(" · ");
}

/**
 * Page numbers as a person types them, counted from 1, with commas and ranges ("1, 3-4"), as page indexes from 0,
 * in order and each once ([0, 2, 3]). Null for nothing named, a page 0, a range that runs backwards or anything else.
 * @param {string} text
 * @returns {number[] | null}
 */
export function parsePages(text) {
  const parts = String(text ?? "").split(",").map((part) => part.trim());
  if (parts.every((part) => part === "")) return null;
  const pages = new Set();
  for (const part of parts) {
    if (part === "") continue; // "1, 3," is fine
    const m = /^(\d+)(?:\s*[-–]\s*(\d+))?$/.exec(part);
    if (m === null) return null;
    const from = Number(m[1]);
    const to = m[2] === undefined ? from : Number(m[2]);
    if (from < 1 || to < from || to > 10_000) return null;
    for (let n = from; n <= to; n++) pages.add(n - 1);
  }
  return [...pages].sort((a, b) => a - b);
}

/**
 * Page indexes from 0 as `parsePages` reads them back: counted from 1, runs as ranges ([0, 2, 3] -> "1, 3-4").
 * @param {number[]} pages
 * @returns {string}
 */
export function pagesText(pages) {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  const runs = [];
  for (const n of sorted) {
    const run = runs.at(-1);
    if (run && n === run[1] + 1) run[1] = n;
    else runs.push([n, n]);
  }
  return runs.map(([a, b]) => (a === b ? `${a + 1}` : `${a + 1}-${b + 1}`)).join(", ");
}

/**
 * What the batch's scan found, under its strip: how many documents its layout fits (fillable and flattened), how
 * many look different and are set aside, and what a cell of the strip (`cells` of them) stands for. "" before the
 * scan is done and for an empty batch.
 * @param {object} batch a Batch
 * @param {number} cells the strip's cells (`stripCells(batch).length`)
 * @returns {string}
 */
export function batchNote(batch, cells) {
  if (batch.scan?.phase !== "done" || !batch.count) return "";
  const main = mainGroup(batch);
  if (main === null) return "None of them could be read as a form, so there is no layout to learn.";
  const aside = batch.count - main.size;
  const asideText = aside > 0 ? `; ${number(aside)} ${aside === 1 ? "looks different and is" : "look different and are"} set aside, for a second template later` : "";
  const per = Math.round(batch.count / Math.max(cells, 1));
  const cell = per <= 1 ? "Each cell is a document" : `Each cell is about ${number(per)} documents`;
  const legend = ["■ in the sample", "□ fillable", "▨ flattened", ...(aside > 0 ? ["dashed, a different layout"] : [])].join(" · ");
  return `One layout fits ${number(main.size)} of them (${number(main.fillable ?? 0)} fillable, ${number(main.flattened ?? 0)} flattened)${asideText}. ${cell}: ${legend}.`;
}

/**
 * The batch as a strip of at most `max` cells, in file order, each standing for about count / cells documents: a
 * cell holding a sample document is `sample`; the layout's share of the cells comes first, with its flattened share
 * spread through it (`flat`, else `main`), and the documents set aside (`other`) last. The scan gives the counts,
 * not which file is which, so the strip shows proportions; until the scan is done every cell is `pending` (not
 * scanned yet), with no sample marked.
 * @param {object} batch a Batch
 * @param {number[]} [sampleIndexes] the batch's file indexes in the sample
 * @param {number} [max]
 * @returns {{state: "sample" | "main" | "flat" | "other" | "pending"}[]}
 */
export function stripCells(batch, sampleIndexes = [], max = 120) {
  const count = batch.count ?? 0;
  if (count === 0) return [];
  const cells = Math.min(count, max);
  if (batch.scan?.phase !== "done") return Array.from({ length: cells }, () => ({ state: "pending" }));
  const main = mainGroup(batch);
  const mainCells = main ? Math.round((cells * main.size) / count) : cells;
  const flatCells = main?.size ? Math.round((mainCells * (main.flattened ?? 0)) / main.size) : 0;
  const samples = new Set(sampleIndexes.filter((i) => i >= 0 && i < count).map((i) => Math.floor((i * cells) / count)));
  return Array.from({ length: cells }, (_, i) => {
    if (samples.has(i)) return { state: "sample" };
    if (i >= mainCells) return { state: "other" };
    const flat = Math.floor(((i + 1) * flatCells) / mainCells) > Math.floor((i * flatCells) / mainCells);
    return { state: flat ? "flat" : "main" };
  });
}
