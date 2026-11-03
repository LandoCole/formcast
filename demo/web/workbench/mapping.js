// The Mapping view, in the left column beside the form's fields: how your data was matched to the form, for a person
// to triage (docs/superpowers/specs/2026-11-03-fill-mapping-triage-design.md). Three lists: what was matched (form
// field ← where its value comes from, the value it gives the record on show, who made it, confirmed or not), the
// form fields with no data, and the columns of your data no match reads. A person confirms a match, changes where it
// comes from, removes it, matches a leftover column to a leftover form field, or types a value for a form field (for
// the record on show, or for every record). Picking a form field turns the page to its box.
//
// The mapping on show is `app.data.mapping` (a one-shot job's data, see fill.js). An edit shows at once; the server
// then makes the records again from the edited mapping (`engine.data.map`, no model call), and the answer replaces
// `app.data`, which refills the PDF on show. An edit made while an earlier one is on its way wins: the earlier answer
// is dropped. Undo takes back the last edit. Typed values for one record stay in the page (`app.typed`: record index ->
// {path: value}), laid over that record when it is shown and filled; they are not saved. Free text has no columns, so
// the view is offered only for a table's data.

import { change, confirm, confirmAll, connect, fixed, formFields, remove, titleOf, triage } from "./mapping-model.js";

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, cls, onClick, label) {
  const b = el("button", cls, text);
  b.type = "button";
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

/** A value as the view shows it. */
function shown(value) {
  if (value === undefined || value === null || value === "") return "empty in this record";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** Where a rule's value comes from, in words. */
function sourceText(rule) {
  if (rule.column !== undefined && rule.column !== null) return rule.column;
  if (rule.template !== undefined && rule.template !== null) return rule.template;
  return `“${typeof rule.value === "string" ? rule.value : JSON.stringify(rule.value)}”`;
}

/** @param {object} app see workbench.js */
export function mountMapping(app) {
  const { $, engine, bus, say } = app;
  const view = $("mapping-view");
  const msg = $("map-msg");
  const columns = new Map(); // dataset id -> its column profiles [{name, samples}]
  let open = null; // the row expanded: "m:<path>", "f:<path>" or "c:<column>"
  let undo = []; // the mappings before each edit, newest last
  let latest = 0; // the number of the newest edit sent; an older answer is dropped
  let waiting = false;
  let good = null; // the last data the server answered: what a failed change goes back to
  app.typed = new Map();

  /** Whether the data on hand is a table's, so it has columns to match. */
  const isTable = () => Boolean(app.data && !app.data.error && app.data.mapping && app.data.dataset?.kind === "table");

  /** The form fields' paths, in the order of the boxes. */
  const linked = () => formFields(app.template).map((f) => f.path);

  /** Show the form's fields or the mapping in the left column. */
  function showTab(which) {
    const mapping = which === "mapping" && isTable();
    $("fields-view").hidden = mapping;
    view.hidden = !mapping;
    $("show-fields").setAttribute("aria-pressed", String(!mapping));
    $("show-mapping").setAttribute("aria-pressed", String(mapping));
    if (mapping) render();
  }

  async function columnsOf(id) {
    if (!columns.has(id)) {
      try {
        const summary = await engine.data.get(id);
        columns.set(id, (summary.columns ?? []).map((c) => ({ name: c.name, samples: c.samples ?? [] })));
      } catch {
        return (app.data?.mapping?.columns ?? []).map((name) => ({ name, samples: [] }));
      }
    }
    return columns.get(id);
  }

  /** Send `next` (the mapping after a person's edit) to be made into records; the view shows it at once. */
  async function edit(next) {
    if (!isTable()) return;
    const before = app.data.mapping;
    undo.push(before);
    // a form field a rule now sets takes its value from the rule: a value typed for it in any record goes
    const ruled = new Set((next.rules ?? []).map((r) => r.path));
    for (const [index, values] of app.typed) {
      const kept = Object.fromEntries(Object.entries(values).filter(([path]) => !ruled.has(path)));
      app.typed.set(index, kept);
    }
    await send(next, () => undo.pop());
  }

  /** Make records of `next`; `undone()` puts the undo history back when the server refuses it. */
  async function send(next, undone) {
    const before = app.data;
    const mine = ++latest;
    app.data = { ...before, mapping: next };
    waiting = true;
    render();
    try {
      const out = await engine.data.map(before.dataset.id, app.template.schema, next, before.mode);
      if (mine !== latest || app.data?.dataset?.id !== before.dataset.id) return; // a later edit, or other data
      // `edited`: a person changed this mapping, so it is saved with the template even when it began as columns by name
      app.data = { ...out, dataset: { ...before.dataset, ...out.dataset }, edited: true };
      good = app.data;
      waiting = false;
      say(msg, warningsText(out.warnings));
      app.dataChanged?.();
    } catch (e) {
      if (mine !== latest) return;
      waiting = false;
      app.data = good ?? before; // the records and the mapping the server last made agree
      undone();
      say(msg, `That change wasn't made: ${e.message}`);
      app.dataChanged?.();
    }
    render();
  }

  const warningsText = (warnings) => ((warnings ?? []).length ? `Left out: ${warnings.join("; ")}.` : "");

  /** The record on show in the fill drawer (with its typed values), and its index. */
  const current = () => app.fillRecord?.() ?? { index: 0, record: app.data?.records?.[0] ?? null };

  function toggle(key) {
    open = open === key ? null : key;
    render();
  }

  /** Select the box of the form field `path` (a table cell selects its table). */
  function pick(path) {
    const field = formFields(app.template).find((f) => f.path === path);
    bus.emit("pick", { path: field?.box ?? path });
  }

  function columnSelect(label, names, onChoose) {
    const select = el("select", "mp-select");
    select.setAttribute("aria-label", label);
    select.append(new Option(`${label}…`, ""), ...names.map((n) => new Option(n, n)));
    select.addEventListener("change", () => {
      if (select.value) onChoose(select.value);
    });
    return select;
  }

  function matchedRow(row, allColumns) {
    const li = el("li", "mp-row");
    li.dataset.path = row.path;
    li.dataset.kind = "matched";
    if (row.rule.confirmed) li.classList.add("is-confirmed");
    const head = el("div", "mp-head");
    const main = button("", "mp-main", () => {
      toggle(`m:${row.path}`);
      pick(row.path);
    });
    main.setAttribute("aria-expanded", String(open === `m:${row.path}`));
    main.append(el("span", "mp-title", row.title), el("span", "mp-path", row.path));
    const from = el("span", "mp-from");
    from.append(el("span", "mp-arrow", "← "), el("span", "mp-src", sourceText(row.rule)), el("span", "mp-tag", row.origin));
    main.append(from, el("span", "mp-value", shown(row.value)));
    if (row.required) main.prepend(el("span", "mp-req", "required"));
    const tick = button("✓", "mp-tick", () => edit(confirm(app.data.mapping, row.path, !row.rule.confirmed)), row.rule.confirmed ? `Unconfirm ${row.title}` : `Confirm ${row.title}`);
    tick.setAttribute("aria-pressed", String(Boolean(row.rule.confirmed)));
    head.append(main, tick);
    li.append(head);
    if (open === `m:${row.path}`) {
      const more = el("div", "mp-more");
      more.append(
        columnSelect("Change to", allColumns.filter((c) => c !== row.rule.column), (col) => edit(change(app.data.mapping, row.path, col))),
        button("Remove", "btn small quiet", () => edit(remove(app.data.mapping, row.path))),
      );
      li.append(more);
    }
    return li;
  }

  function missingRow(row, unusedNames, allColumns) {
    const li = el("li", "mp-row");
    li.dataset.path = row.path;
    li.dataset.kind = "missing";
    const { index } = current();
    const typed = app.typed.get(index)?.[row.path];
    const main = button("", "mp-main", () => {
      toggle(`f:${row.path}`);
      pick(row.path);
    });
    main.setAttribute("aria-expanded", String(open === `f:${row.path}`));
    if (row.required) main.append(el("span", "mp-req", "required"));
    main.append(el("span", "mp-title", row.title), el("span", "mp-path", row.path));
    if (typed !== undefined) main.append(el("span", "mp-value", `typed for this record: ${typed}`));
    li.append(main);
    if (open === `f:${row.path}`) {
      const more = el("div", "mp-more");
      const rest = allColumns.filter((c) => !unusedNames.includes(c));
      more.append(columnSelect("Match to", [...unusedNames, ...rest], (col) => edit(connect(app.data.mapping, row.path, col))));
      const input = el("input", "text mp-input");
      input.type = "text";
      input.placeholder = "Type a value";
      input.setAttribute("aria-label", `A value for ${row.title}`);
      input.value = typed ?? "";
      const here = button("This record", "btn small quiet", () => setTyped(row.path, input.value));
      const every = button("Every record", "btn small quiet", () => {
        if (input.value.trim()) {
          setTyped(row.path, "");
          edit(fixed(app.data.mapping, row.path, input.value));
        }
      });
      const buttons = el("div", "row mp-buttons");
      buttons.append(here, every);
      more.append(input, buttons);
      li.append(more);
    }
    return li;
  }

  function unusedRow(col, missing, matched) {
    const li = el("li", "mp-row");
    li.dataset.column = col.name;
    li.dataset.kind = "unused";
    const main = button("", "mp-main", () => toggle(`c:${col.name}`));
    main.setAttribute("aria-expanded", String(open === `c:${col.name}`));
    main.append(el("span", "mp-title", col.name));
    if (col.samples.length) main.append(el("span", "mp-value", col.samples.join(" · ")));
    li.append(main);
    if (open === `c:${col.name}`) {
      const more = el("div", "mp-more");
      const select = el("select", "mp-select");
      select.setAttribute("aria-label", "Match to");
      select.append(new Option("Match to…", ""));
      const titled = (rows) => rows.map((r) => new Option(`${r.title} · ${r.path}`, r.path));
      if (missing.length) select.append(Object.assign(el("optgroup"), { label: "With no data" }), ...titled(missing));
      if (matched.length) select.append(Object.assign(el("optgroup"), { label: "Matched (replaces its source)" }), ...titled(matched));
      select.addEventListener("change", () => {
        if (select.value) edit(connect(app.data.mapping, select.value, col.name));
      });
      more.append(select);
      li.append(more);
    }
    return li;
  }

  /** Set (or, with an empty value, clear) the typed value of `path` for the record on show. */
  function setTyped(path, value) {
    const { index } = current();
    const values = { ...(app.typed.get(index) ?? {}) };
    if (value.trim()) values[path] = value;
    else delete values[path];
    app.typed.set(index, values);
    app.dataChanged?.();
    render();
  }

  async function render() {
    const table = isTable();
    $("left-switch").hidden = !table;
    if (!table) {
      if (!view.hidden) showTab("fields");
      return;
    }
    if (view.hidden) return;
    const data = app.data;
    const cols = await columnsOf(data.dataset.id);
    if (!isTable() || app.data.dataset.id !== data.dataset.id) return; // other data came while the columns were fetched
    const { record } = current();
    const t = triage({ mapping: app.data.mapping, schema: app.template?.schema, linked: linked(), columns: cols, record, mode: app.data.mode });
    const { counts } = t;
    $("map-counts").textContent =
      `${counts.matched} matched (${counts.confirmed} confirmed) · ${plural(counts.missing, "form field")} with no data · ${plural(counts.unused, "column")} not used` +
      (waiting ? " · updating…" : "");
    $("map-confirm-all").disabled = counts.matched === counts.confirmed;
    $("map-undo").disabled = undo.length === 0;
    const allColumns = cols.map((c) => c.name);
    const unusedNames = t.unused.map((c) => c.name);
    $("map-matched").replaceChildren(...t.matched.map((row) => matchedRow(row, allColumns)));
    $("map-missing").replaceChildren(...t.missing.map((row) => missingRow(row, unusedNames, allColumns)));
    $("map-unused").replaceChildren(...t.unused.map((col) => unusedRow(col, t.missing, t.matched)));
    $("map-matched-none").hidden = t.matched.length > 0;
    $("map-missing-none").hidden = t.missing.length > 0;
    $("map-unused-none").hidden = t.unused.length > 0;
    const notes = app.data.notes ? `The AI notes: ${app.data.notes}` : "";
    say($("map-notes"), notes);
  }

  $("show-fields").addEventListener("click", () => showTab("fields"));
  $("show-mapping").addEventListener("click", () => showTab("mapping"));
  $("map-confirm-all").addEventListener("click", () => {
    const t = triage({ mapping: app.data.mapping, schema: app.template?.schema, linked: linked(), columns: [], mode: app.data.mode });
    edit(confirmAll(app.data.mapping, t.matched.map((r) => r.path)));
  });
  $("map-undo").addEventListener("click", () => {
    const shown = app.data?.mapping;
    const before = undo.pop();
    if (before !== undefined) send(before, () => undo.push(shown));
  });
  bus.on("template", () => render());

  /** New data from a job (fill.js): the edits and typed values of the data before are gone; show the mapping. */
  function fresh() {
    undo = [];
    open = null;
    latest += 1;
    waiting = false;
    good = app.data;
    app.typed = new Map();
    say(msg, isTable() ? warningsText(app.data.warnings) : ""); // what the first mapping left out
    if (isTable()) showTab("mapping");
    else render();
  }

  app.onReset(() => {
    fresh();
    showTab("fields");
  });

  return { render, show: () => showTab("mapping"), fresh, titleOf: (path) => titleOf(app.template?.schema, path) };
}
