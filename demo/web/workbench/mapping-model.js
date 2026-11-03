// The Mapping view's pure logic (mapping.js draws it): a data mapping, the template's schema, the paths its boxes use
// and the data's columns, sorted into the three lists a person triages, and the edits a person makes to the mapping.
//
//   triage(...)       {matched, missing, unused, counts}: the form fields given a value by a rule, the form fields
//                     given none, and the columns no rule reads
//   originOf(rule)    who made a rule: "you", "saved", "by name" or "AI"
//   confirm, confirmAll, change, remove, connect, fixed   edits; each answers a new mapping, the one given untouched
//
// A form field is a leaf path some box of the template uses (`formFields`: a plain box's `source.paths`, a table box's
// cells); a rule for a path no box uses is in neither list, since filling never shows it. The marks a person
// leaves on a rule are `confirmed` and `manual` (formcast.data.mapping.Rule): set when true, absent otherwise.

const PLACEHOLDER = /\{([^{}]+)\}/g;

/** The columns a rule reads: its column, or its template's placeholders. */
export function columnsOf(rule) {
  if (rule.column !== undefined && rule.column !== null) return [rule.column];
  if (typeof rule.template === "string") return [...rule.template.matchAll(PLACEHOLDER)].map((m) => m[1]);
  return [];
}

/** `owners[0].name` as the schema has it: `owners[].name`. */
const generic = (path) => path.replace(/\[\d+\]/g, "[]");

/** The parts of a path: names, and `[]` (or `[n]`) for an item. */
const partsOf = (path) => path.match(/\[\d*\]|[^.[\]]+/g) ?? [];

/** The schema node at `path` and whether each step to it is required, or null when the schema has no such path. */
function nodeAt(schema, path) {
  let node = schema;
  let required = true;
  for (const part of partsOf(generic(path))) {
    if (node === null || typeof node !== "object") return null;
    if (part === "[]") {
      node = node.items;
      continue;
    }
    const props = node.properties;
    if (props === null || typeof props !== "object" || !(part in props)) return null;
    required = required && Array.isArray(node.required) && node.required.includes(part);
    node = props[part];
  }
  return node === null || typeof node !== "object" ? null : { node, required };
}

/** A form field's title: its schema title, else its last name with the underscores made spaces. */
export function titleOf(schema, path) {
  const found = nodeAt(schema, path);
  if (typeof found?.node.title === "string" && found.node.title.trim()) return found.node.title.trim();
  const names = partsOf(path).filter((p) => !p.startsWith("["));
  const last = names[names.length - 1] ?? path;
  const index = path.match(/\[(\d+)\][^[]*$/);
  const words = last.replace(/_/g, " ");
  return index ? `${words} ${Number(index[1]) + 1}` : words;
}

/** The value `record` has at the concrete `path`, or undefined. */
export function valueAt(record, path) {
  let node = record;
  for (const part of partsOf(path)) {
    if (node === null || typeof node !== "object") return undefined;
    node = part.startsWith("[") ? node[Number(part.slice(1, -1))] : node[part];
  }
  return node;
}

/** A copy of `record` with each concrete path of `values` set (made where it is missing); `record` is left as it was. */
export function withValues(record, values) {
  const out = structuredClone(record ?? {});
  for (const [path, value] of Object.entries(values ?? {})) {
    const parts = partsOf(path);
    let node = out;
    parts.forEach((part, i) => {
      const key = part.startsWith("[") ? Number(part.slice(1, -1)) : part;
      if (i === parts.length - 1) {
        node[key] = value;
        return;
      }
      if (node[key] === null || typeof node[key] !== "object") node[key] = parts[i + 1].startsWith("[") ? [] : {};
      node = node[key];
    });
  }
  return out;
}

/**
 * The form fields of `template` in the order of its boxes: [{path, box}], `path` concrete and `box` the path a pick of
 * it selects. A plain box gives its linked paths; a table box, linked to a list (`owners`), gives a path per cell
 * (`owners[0].name`, a row for each of its segments' rows by the first segment's columns), each picking the table.
 */
export function formFields(template) {
  const out = [];
  for (const f of template?.fields ?? []) {
    const paths = f.source?.paths ?? [];
    if (f.type !== "table") {
      for (const path of paths) out.push({ path, box: path });
      continue;
    }
    const list = paths[0];
    if (!list) continue;
    const rows = (f.segments ?? []).reduce((n, s) => n + (s.max_rows ?? 0), 0);
    const keys = (f.segments?.[0]?.columns ?? []).map((c) => c.key);
    for (let row = 0; row < rows; row++) for (const key of keys) out.push({ path: `${list}[${row}].${key}`, box: list });
  }
  return out;
}

/** Whether `path` is a value the form shows: in the schema, and not an object or a list of records. */
function isLeaf(schema, path) {
  const found = nodeAt(schema, path);
  if (found === null) return false;
  const { node } = found;
  return node.type !== "object" && !(node.type === "array" && node.items?.type === "object");
}

/** Who made `rule`, in the words the view tags it with; `mode` is how the mapping first came about. */
export function originOf(rule, mode) {
  if (rule.manual) return "you";
  if (mode === "saved") return "saved";
  if (mode === "direct") return "by name";
  return "AI";
}

/**
 * The three lists of the Mapping view.
 * @param {{mapping: {rules?: object[]}, schema: object, linked: Iterable<string>, columns: {name: string, samples?: string[]}[],
 *   record?: object | null, mode?: string}} input
 */
export function triage({ mapping, schema, linked, columns, record = null, mode = "model" }) {
  const rules = mapping?.rules ?? [];
  const byPath = new Map(rules.map((r) => [r.path, r]));
  const order = [...new Set(linked)].filter((path) => schema == null || isLeaf(schema, path));
  const rank = new Map(order.map((p, i) => [p, i]));
  const rows = order.map((path) => ({ path, title: titleOf(schema, path), required: nodeAt(schema, path)?.required ?? false }));
  const sorted = (list) => list.sort((a, b) => Number(b.required) - Number(a.required) || rank.get(a.path) - rank.get(b.path));
  const matched = sorted(
    rows
      .filter((row) => byPath.has(row.path))
      .map((row) => {
        const rule = byPath.get(row.path);
        return { ...row, rule, origin: originOf(rule, mode), value: record === null ? undefined : valueAt(record, row.path) };
      }),
  );
  const missing = sorted(rows.filter((row) => !byPath.has(row.path)));
  const used = new Set(rules.flatMap(columnsOf));
  const unused = columns.filter((c) => !used.has(c.name)).map((c) => ({ name: c.name, samples: (c.samples ?? []).slice(0, 2) }));
  const counts = {
    matched: matched.length,
    confirmed: matched.filter((row) => row.rule.confirmed).length,
    missing: missing.length,
    unused: unused.length,
  };
  return { matched, missing, unused, counts };
}

/** The mapping with its rules made by `edit(rules)`; `unmapped_columns` is left for the server to work out again. */
function withRules(mapping, edit) {
  return { ...mapping, rules: edit((mapping?.rules ?? []).map((r) => ({ ...r }))) };
}

/** A rule's marks as the library keeps them: present when true, absent otherwise. */
function marked(rule, marks) {
  const out = { ...rule };
  for (const [key, on] of Object.entries(marks)) {
    if (on) out[key] = true;
    else delete out[key];
  }
  return out;
}

/** The rule for `path` confirmed (or, `on` false, no longer). */
export function confirm(mapping, path, on = true) {
  return withRules(mapping, (rules) => rules.map((r) => (r.path === path ? marked(r, { confirmed: on }) : r)));
}

/** Every rule for one of `paths` confirmed. */
export function confirmAll(mapping, paths) {
  const want = new Set(paths);
  return withRules(mapping, (rules) => rules.map((r) => (want.has(r.path) ? marked(r, { confirmed: true }) : r)));
}

/** A person's rule for `path`: the column as it is, or a fixed value; it replaces any rule for that path, in place. */
function put(mapping, rule) {
  return withRules(mapping, (rules) => {
    const at = rules.findIndex((r) => r.path === rule.path);
    if (at === -1) return [...rules, rule];
    rules[at] = rule;
    return rules;
  });
}

/** The form field `path` set from `column` instead, by a person (unconfirmed: a change is not yet checked). */
export function change(mapping, path, column) {
  return put(mapping, { path, column, transforms: [], manual: true });
}

/** A form field with no data connected to `column`. */
export const connect = change;

/** The form field `path` set to `value` in every record. */
export function fixed(mapping, path, value) {
  return put(mapping, { path, value, transforms: [], manual: true });
}

/** The rule for `path` taken out: the form field has no data. */
export function remove(mapping, path) {
  return withRules(mapping, (rules) => rules.filter((r) => r.path !== path));
}
