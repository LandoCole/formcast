// The setup screen (`.view[data-only=setup]`): an extraction template being made from its samples, as the server's
// Setup has it ({id, batch, template, proof, warnings, samples}), and every change a person makes to it. `open(setup)`
// shows a Setup (the Extract modal's build hands it on through `app.extract.openSetup`); the toolbar then names the
// batch, its size and the sample count (#doc-pick goes back to the choice), with Save and Run on all.
//
// The screen, as the mockup lays it out. The strip (#strip) names where the fields started (the AI's suggestion, the
// person's lines, shown in a text area, or a saved fill template, with how many of its fields this form lacks), the
// sample and the detection, with "Change the setup" (#change), which opens the Extract modal again with the
// template's choices (its SetupInfo). The field list (#fields) has a row per field (a repeated group's items folded
// into one, `groupFields`), with its state across the samples (`fieldState` of `rowProof`): ● every sample, ◐ some,
// ? unsure, ○ not on this form; under it, "Add a field" (#add) runs an `extract-line` job and selects the first field
// it adds. The middle column is a page of one sample (‹ › through the samples and their pages, fit width / fit page
// and zoom steps) with the readers' marks on it (`engine.extract.marks`, in that sample page's frame): each `box` and
// each checkbox `option` drawn as the editor draws a linked box, which moves and resizes as there; where a value was
// `read`, dotted, and a `label` a reader looks beside, underlined (these two only show, they do not move). The
// selected field's marks are heavy, its first one with its path. On the right, the field (#ins): its name, title,
// type and definition, how it is read (`readFrom`), its answers and the box of each, the wordings the samples have
// that its answers' map lacks (accepted with "Add to the answers' map", `engine.extract.learn`), its labels as a comma
// list, its checks (Required, a format), "Not on this form" or "Bring back", and "Remove field"; under it, one line
// per sample (`outcomeLine`), which shows that sample when clicked.
//
// Every change is one call to the server (`edit`, `learn`, or the line job), made one after another (a change made
// while a line job runs waits for it, since the server takes none meanwhile); the Setup it
// answers replaces the screen's (the fields, the inspector, the lines per sample, and the page's marks, fetched
// again), with the field selected kept. A box moved on the page is a `set_box` (an option's, a `set_option_box`)
// naming the sample shown (`sample`), since the marks are in that sample's frame: the server moves it into the
// layout's place (ruling P3). A widget read has no place of its own, so a fillable sample shows the field's box mark
// at the widget (ruling P4). The marks are redrawn only when no press is held on the page, so a box being dragged
// is never pulled from under the pointer.
//
// Save asks for a name (#setup-save-box, as the fill template's save) and stores the template with its sample PDFs
// (`engine.extract.save`). A setup the server no longer has (it restarted, or dropped it) answers any call with 404
// "no such setup": the screen closes and the choice opens with "This setup is no longer on the server; start again."
//
// The page view here is a PageView of its own, on a bus of its own, so the fill editor's handlers never hear it.

import { createBus } from "../core/bus.js";
import { makeView, viewSize } from "../core/geometry.js";
import { PageView } from "../core/pageview.js";
import { DETECT_SUM } from "./extract.js";
import { checksOf, fieldSchema, fieldState, groupFields, outcomeLine, readFrom, rowProof } from "./extract-model.js";

/** What the screen says when the server no longer has the setup. */
export const GONE = "This setup is no longer on the server; start again.";
/** Store names, as the server accepts them (demo/server/store.py). */
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$/;
/** Zoom levels in pixels per point (1 is the page at 72 dpi, a PDF viewer's actual size), as the mockup has them. */
const ZOOMS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
/** The page images' resolution bounds (the server's). */
const MIN_DPI = 36;
const MAX_DPI = 200;
/** Where the fields started, as the strip names it. */
const START_NAMES = { ai: "AI suggests", lines: "I'll list them", destination: "Destination" };
/** The types a person can give a field (the edit's names) and how the inspector says them. */
const TYPES = [
  ["text", "text"],
  ["number", "number"],
  ["integer", "whole number"],
  ["date", "date"],
  ["yes/no", "yes or no"],
];
/** A mark's kinds in the order they are drawn: a read place and a label under the boxes, so a box is on top. */
const LAYER = { read: 0, label: 1, box: 2, option: 3 };
/** A path's first mark of these kinds, in this order, is the one selected and tagged with its path. */
const PRIMARY = { box: 0, option: 1, read: 2, label: 3 };
/** Each kind of mark in words, for the box's accessible name. */
const KIND_WORDS = { read: "where it was read", label: "its label", box: "its box", option: "a checkbox" };

const plural = (n, word, many = `${word}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? word : many}`;
const generic = (path) => path.replace(/\[\d+\]/g, "[]");
const leafWords = (path) => generic(path).split(".").at(-1).replace(/\[\]/g, "").replaceAll("_", " ");
const andList = (words) => (words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`);

/** An element with `props` set and `children` appended. */
function make(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  Object.assign(el, props);
  el.append(...children);
  return el;
}

/** Whether `e` is the server saying it has no such setup (a 404 from any setup call). */
function isGone(e) {
  return e?.code === "not_found" && /^no such setup/i.test(e.detail ?? e.message ?? "");
}

/** A store name made from a template's name. */
function suggest(name) {
  return String(name ?? "").replace(/[^A-Za-z0-9 _.-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "").slice(0, 80) || "extraction";
}

/** The field type the inspector's select shows for a schema node ("" when it is none of them). */
function typeOf(prop) {
  if (prop.type === "boolean") return "yes/no";
  if (prop.type === "integer") return "integer";
  if (prop.type === "number") return "number";
  if (prop.type === "string") return prop.format === "date" ? "date" : "text";
  return "";
}

/**
 * @param {object} app see workbench.js
 * @returns {{open: (setup: object) => void, promptSave: () => void, readonly current: object | null,
 *   readonly savedName: string | null, readonly changed: boolean, readonly idle: boolean}}
 */
export function mountSetup(app) {
  const { $, engine } = app;
  const calls = engine.extract;
  const bus = createBus(); // the setup page's own: the fill editor's handlers never hear it
  const pvEl = $("setup-pv");
  const view = new PageView(pvEl, { bus });
  const saveBox = $("setup-save-box");

  const state = {
    setup: null, // the Setup on show, as the server last gave it
    batch: null, // its batch, for the toolbar (null for a saved template reopened, or until it is fetched)
    selected: null, // the concrete path of the field selected, or null
    sample: 0, // the sample shown, from 0 in the template's order
    page: 0, // its page shown, from 0
    zoom: "width", // "width" or "page" (a fit), or a fixed scale in pixels per point
    marks: [], // the shown sample's marks
    savedName: null, // the name it was last saved as
    changed: false, // edited since it was opened or saved
  };
  let queue = Promise.resolve(); // the changes, one after another
  let pending = 0; // changes and mark loads under way
  let marksToken = 0; // the marks asked for last
  let held = false; // a press is held on the page
  let redrawLater = false;
  let adding = false; // a line job is running
  let inspected = null; // the path the inspector was last drawn for
  /** @type {Map<string, {path: string, kind: string, answer: string | null, page: number, rect: number[], primary: boolean}>} */
  const shown = new Map(); // the page's box ids -> their marks

  // ------------------------------------------------------------------ the setup and its parts

  const template = () => state.setup.template;
  const schema = () => template().schema ?? {};
  const groups = () => groupFields(state.setup.proof, schema());
  const rows = () => groups().flatMap((g) => g.items);
  const rowOf = (path) => rows().find((r) => r.paths.includes(path)) ?? null;
  const proofOf = (path) => state.setup.proof.fields.find((f) => f.path === path) ?? null;
  const firstPath = () => rows()[0]?.path ?? null;

  /** Show `setup` on the setup screen, from its first sample and field. */
  function open(setup) {
    for (const o of document.querySelectorAll(".overlay")) o.hidden = true;
    app.extract.setMode("extract");
    marksToken += 1;
    Object.assign(state, { setup, batch: null, sample: 0, page: 0, marks: [], savedName: null, changed: false });
    state.selected = firstPath();
    showView("setup");
    loadBatch();
    renderAll();
    showPage();
    loadMarks();
  }

  /** Show the extraction view `name` ("setup", or null for none) with its toolbar part. */
  function showView(name) {
    for (const el of document.querySelectorAll("#extract-views [data-only], #toolbar [data-only]")) el.hidden = el.dataset.only !== name;
    $("extract-empty").hidden = name !== null;
    $("view-pick").hidden = name === null;
    for (const btn of $("view-pick").querySelectorAll("[data-view]")) btn.setAttribute("aria-pressed", String(btn.dataset.view === name));
  }

  /** The setup is gone from the server: close the screen and open the choice with the reason. */
  function lost() {
    marksToken += 1;
    Object.assign(state, { setup: null, batch: null, selected: null, marks: [] });
    showView(null);
    $("doc-pick").textContent = "No extraction open";
    for (const o of document.querySelectorAll(".overlay")) o.hidden = true;
    app.say($("choose-msg"), GONE);
    app.extract.show();
  }

  /** Fetch the setup's batch for the toolbar's name and size. */
  async function loadBatch() {
    const s = state.setup;
    if (!s.batch) return;
    try {
      const batch = await calls.batch(s.batch);
      if (state.setup?.id !== s.id) return;
      state.batch = batch;
      renderToolbar();
    } catch {
      // a batch the server no longer has: the toolbar names the template instead
    }
  }

  // ------------------------------------------------------------------ changes

  /**
   * Make one change: `send(setupId)` is the call, resolving the Setup proven again, which replaces the screen's.
   * Changes run one after another, behind a line job running (see `addField`). Resolves whether it was made; a
   * refusal is shown as a notice, and puts back only `control` (the id of the box whose change it was).
   */
  function change(send, { select = null, control = null } = {}) {
    const s = state.setup;
    if (s === null) return Promise.resolve(false);
    pending += 1;
    const run = queue.then(async () => {
      try {
        if (state.setup?.id !== s.id) return false; // replaced or gone meanwhile: skipped, and no longer pending
        const next = await send(s.id);
        if (state.setup?.id !== s.id) return false;
        replace(next, select);
        return true;
      } catch (e) {
        failed(e, "That change wasn't made", control);
        return false;
      } finally {
        pending -= 1;
      }
    });
    queue = run;
    return run;
  }

  const edit = (op, opts) => change((id) => calls.edit(id, op), opts);

  /** The Setup `next` in place of the screen's, the selection kept (or `select`, when it has that field). */
  function replace(next, select = null) {
    state.setup = next;
    state.changed = true;
    app.notice("");
    const paths = new Set(next.proof.fields.map((f) => f.path));
    if (select !== null && paths.has(select)) state.selected = select;
    else if (!paths.has(state.selected)) state.selected = firstPath();
    renderAll();
    loadMarks();
  }

  /**
   * A call that failed: the screen closes when the setup is gone; else the notice says why, the box whose change it
   * was (`control`, an id) shows the field as it is again, the others keep what is typed in them, and a moved box on
   * the page goes back.
   */
  function failed(e, what, control = null) {
    if (isGone(e)) {
      lost();
      return;
    }
    app.notice(`${what}: ${e.message}`);
    renderInspector({ keepTyped: true, refused: control });
    drawMarks(); // a moved box goes back where the template has it
  }

  // ------------------------------------------------------------------ toolbar, strip, fields

  function renderAll() {
    renderToolbar();
    renderStrip();
    renderFields();
    renderSide();
    renderPager();
  }

  function renderSide() {
    renderInspector();
    renderSamples();
    renderCaption();
  }

  function renderToolbar() {
    const s = state.setup;
    if (s === null) return;
    const parts = [state.batch?.name || template().name];
    if (state.batch) parts.push(plural(state.batch.count, "PDF"));
    parts.push(plural(s.samples.length, "sample"));
    $("doc-pick").textContent = parts.join(" · ");
  }

  /** The strip: where the fields started, the sample and the detection, and Change the setup. */
  function renderStrip() {
    const info = template().setup;
    const head = make("span", { className: "label" });
    const detect = DETECT_SUM[info?.detect] ?? "";
    const start = START_NAMES[info?.start] ?? "A saved template";
    head.textContent = [start, plural(state.setup.samples.length, "sample"), detect].filter(Boolean).join(" · ");
    const p = make("p", {}, head, make("br"), stripText(info));
    const parts = [p];
    if (info?.start === "lines") {
      const height = Math.min(6, Math.max(3, info.lines.length));
      parts.push(make("textarea", { readOnly: true, rows: height, value: info.lines.join("\n"), ariaLabel: "Your lines", spellcheck: false }));
    }
    const button = make("button", { type: "button", className: "btn quiet small", id: "change", textContent: "Change the setup" });
    button.addEventListener("click", () => app.extract.openModal(template().setup ?? undefined));
    parts.push(button);
    $("strip").replaceChildren(...parts);
  }

  /** What the strip says of where the fields came from. */
  function stripText(info) {
    const all = rows();
    if (info?.start === "ai") {
      return `The AI read the samples and suggested ${plural(all.length, "field")}. Remove what you don't need, rename them, or add your own on the left.`;
    }
    if (info?.start === "lines") {
      const said = info.lines.flatMap((line) => {
        const names = [...new Set((info.line_paths?.[line] ?? []).map(leafWords))];
        if (names.length === 0) return [`“${line}” found no field`];
        return names.length > 1 ? [`“${line}” became ${andList(names)}`] : [];
      });
      return `From your ${plural(info.lines.length, "line")}${said.length ? `; ${said.join("; ")}` : ""}. Add more on the left.`;
    }
    if (info?.start === "destination") {
      const lacking = all.filter((r) => rowProof(r, state.setup.proof).absent).length;
      const gap = lacking === 0
        ? "Every one of its fields is on this form."
        : `${lacking} of its fields ${lacking === 1 ? "is" : "are"} not on this form; they stay in the list, marked ○, so the gap is known before the run.`;
      return `From the saved template ${info.destination}: the fields it needs that this form can supply. ${gap}`;
    }
    return "A saved extraction template, proven again on its samples.";
  }

  /** The field list: a row per field, grouped, each with its state across the samples. */
  function renderFields() {
    const out = [];
    let count = 0;
    let weak = 0;
    const proof = state.setup.proof;
    for (const { group, items } of groups()) {
      if (group) out.push(make("li", { className: "fc-sl-grp", textContent: group }));
      for (const r of items) {
        const st = fieldState(rowProof(r, proof));
        count += 1;
        if (st.state === "weak") weak += 1;
        const name = r.generic.split(".").slice(1).join(".") || r.generic;
        const li = make(
          "li",
          { className: "fc-sl-leaf", tabIndex: 0, title: r.title },
          make("span", { className: "fc-sl-st", textContent: st.glyph }),
          make("span", { className: "fc-sl-name", textContent: name }),
          make("span", { className: "fc-sl-ty", textContent: st.text }),
        );
        li.dataset.path = r.path;
        li.dataset.state = st.state;
        li.style.setProperty("--depth", group ? "1" : "0");
        li.classList.toggle("is-picked", r.paths.includes(state.selected));
        const pick = () => select(r.paths.includes(state.selected) ? state.selected : r.path, { turn: true });
        li.addEventListener("click", pick);
        li.addEventListener("keydown", (e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          pick();
        });
        out.push(li);
      }
    }
    $("fields").replaceChildren(...out);
    $("fields-label").textContent = `Fields · ${count} · ${weak} need a look`;
  }

  /** Select the field `path` (or none); `turn` turns to a page it has marks on when this page has none. */
  function select(path, { turn = false } = {}) {
    state.selected = path;
    renderFields();
    renderSide();
    $("fields").querySelector(".is-picked")?.scrollIntoView({ block: "nearest" });
    if (turn && path !== null && primaryOf(path) === null) {
      const elsewhere = marksOf(path)[0];
      if (elsewhere !== undefined && elsewhere.page !== state.page) {
        state.page = elsewhere.page;
        showPage();
        return;
      }
    }
    view.select(primaryOf(path));
    decorate();
  }

  // ------------------------------------------------------------------ the field

  /**
   * The inspector for the field selected. Drawn again for the same field (a change came back meanwhile), the text box
   * the person is typing in keeps its text, focus and selection; with `keepTyped`, every text box does, but
   * `refused` (an id), which shows the field as the server has it.
   */
  function renderInspector({ keepTyped = false, refused = null } = {}) {
    const ins = $("ins");
    const path = state.selected;
    const active = document.activeElement;
    const same = path === inspected;
    const typed = new Map();
    for (const el of same ? ins.querySelectorAll("textarea, input[type=text]") : []) {
      if (el.id && el.id !== refused && (keepTyped || el === active)) typed.set(el.id, el.value);
    }
    const focus = same && ins.contains(active) && typed.has(active.id) ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
    inspected = path;
    if (state.setup === null || path === null) {
      ins.replaceChildren(make("p", { className: "fc-ins-empty", textContent: "Click a field, or a box on the page, to see how it is read." }));
      return;
    }
    ins.replaceChildren(...inspectorParts(path));
    for (const [id, value] of typed) {
      const el = document.getElementById(id);
      if (el) el.value = value;
    }
    if (focus !== null) {
      const el = document.getElementById(focus.id);
      el?.focus({ preventScroll: true });
      el?.setSelectionRange?.(focus.start, focus.end);
    }
  }

  /** The inspector's elements for the field `path`. */
  function inspectorParts(path) {
    const t = template();
    const row = rowOf(path);
    const fp = proofOf(path);
    const { prop, required } = fieldSchema(schema(), path);
    const reader = t.readers?.[path];
    const absent = fp?.absent ?? (t.absent ?? []).includes(path);
    const out = [];
    const lab = (text) => make("p", { className: "fc-ins-lab", textContent: text });
    const ro = (text) => make("div", { className: "ro", textContent: text });
    const hint = (text) => make("p", { className: "fc-ins-hint", textContent: text });

    const dl = make("dl", { className: "fc-ins-dl" });
    const item = (label, control) => dl.append(make("dt", { textContent: label }), make("dd", {}, control));
    item("Name", make("span", { className: "ins-name", textContent: row?.items ? row.generic : path }));
    const title = make("input", { type: "text", className: "fc-in", id: "ins-title", value: prop.title ?? "", ariaLabel: "Title" });
    commitOnChange(title, () => title.value.trim() !== (prop.title ?? "") && edit({ op: "set_field", path, title: title.value.trim() }, { control: title.id }));
    item("Title", title);
    item("Type", typeSelect(path, prop));
    if (row?.items) {
      const pickItem = make("select", { className: "fc-in", id: "ins-item", ariaLabel: "Item" });
      pickItem.append(...row.paths.map((p, i) => new Option(`${i + 1} of ${row.paths.length}`, p)));
      pickItem.value = path;
      pickItem.addEventListener("change", () => select(pickItem.value, { turn: true }));
      item("Item", pickItem);
    }
    out.push(dl);

    out.push(lab("Definition"));
    const def = make("textarea", { className: "fc-in", id: "ins-def", rows: 3, value: prop.description ?? "", ariaLabel: "Definition" });
    commitOnChange(def, () => def.value.trim() !== (prop.description ?? "") && edit({ op: "set_field", path, description: def.value.trim() }, { control: def.id }));
    out.push(def);
    const line = Object.entries(t.setup?.line_paths ?? {}).find(([, paths]) => paths.includes(generic(path)))?.[0];
    if (line !== undefined) out.push(hint(`From your line “${line}”.`));

    out.push(lab("Read from"));
    if (absent) {
      out.push(ro("Not on this form. It is left empty in the results, to ask for or fill in at review."));
    } else {
      const ways = readFrom(reader, prop);
      if (ways.length === 0) out.push(ro("Nothing reads it yet: name the label it sits beside, below."));
      for (const way of ways) out.push(ro(way));
      if (ways.length > 1) out.push(hint("Tried in this order; the first that finds a value is used."));
      if (reader?.clean?.length) out.push(hint(`Cleaned: ${reader.clean.map((c) => c.op).join(", ")}.`));
    }
    for (const warning of state.setup.warnings ?? []) {
      if (warning.startsWith(`${path}: `)) out.push(make("p", { className: "fc-ins-warn", textContent: warning.slice(path.length + 2) }));
    }

    out.push(...answersParts(path, prop, reader));
    out.push(...wordingsParts(path, fp));

    if (!absent) {
      out.push(lab("Labels"));
      const anchor = reader?.strategies?.find((s) => s.kind === "anchor");
      const labels = make("input", { type: "text", className: "fc-in", id: "ins-labels", ariaLabel: "Labels", placeholder: "Taxpayer ID, TIN" });
      labels.value = (anchor?.labels ?? []).join(", ");
      const typed = () => labels.value.split(",").map((s) => s.trim()).filter(Boolean);
      commitOnChange(labels, () => typed().join("\n") !== (anchor?.labels ?? []).join("\n") && edit({ op: "set_labels", path, labels: typed() }, { control: labels.id }));
      out.push(labels, hint("The printed words the value sits beside, separated by commas. Empty, it is not read beside a label."));
    }

    out.push(lab("Checks"));
    const req = make("input", { type: "checkbox", id: "ins-required", checked: required });
    req.addEventListener("change", () => edit({ op: "set_field", path, required: req.checked }));
    out.push(make("label", { className: "fc-ins-check" }, req, "Required"));
    const pattern = make("input", { type: "text", className: "fc-in", id: "ins-pattern", value: prop.pattern ?? "", spellcheck: false });
    pattern.placeholder = "a regular expression, such as ^\\d{5}$";
    commitOnChange(pattern, () => pattern.value !== (prop.pattern ?? "") && edit({ op: "set_field", path, pattern: pattern.value }, { control: pattern.id }));
    out.push(make("label", { className: "ins-format" }, "Format", pattern));
    for (const check of checksOf(prop, false)) if (!check.startsWith("Format ")) out.push(ro(check));

    const away = make("button", { type: "button", className: "btn quiet small", id: "ins-absent" });
    away.textContent = absent ? "Bring back" : "Not on this form";
    away.addEventListener("click", () => setAbsent(row, !absent));
    const remove = make("button", { type: "button", className: "btn quiet small", id: "ins-remove", textContent: "Remove field" });
    remove.addEventListener("click", () => edit({ op: "remove_field", path }));
    out.push(make("div", { className: "ins-acts" }, away, remove));
    return out;
  }

  /** Commit `el`'s value with `commit` when it changes; Enter in a one-line box commits it too. */
  function commitOnChange(el, commit) {
    el.addEventListener("change", commit);
    if (el.tagName === "INPUT") {
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        el.blur(); // its change event commits it, once
      });
    }
  }

  /** The type select: the types a person can give a field, the field's own selected. */
  function typeSelect(path, prop) {
    const sel = make("select", { className: "fc-in", id: "ins-type", ariaLabel: "Type" });
    const now = typeOf(prop);
    if (now === "") sel.append(new Option(String(prop.type ?? "other"), "", true, true));
    sel.append(...TYPES.map(([value, words]) => new Option(words, value)));
    sel.value = now;
    sel.addEventListener("change", () => sel.value && edit({ op: "set_field", path, type: sel.value }));
    return sel;
  }

  /** A choice field's answers, each with where its box is. */
  function answersParts(path, prop, reader) {
    const choices = (reader?.strategies ?? []).filter((s) => s.kind === "choice");
    const answers = [...new Set([...(prop.enum ?? []).map(String), ...choices.flatMap((s) => Object.keys(s.options ?? {}))])];
    if (answers.length === 0) return [];
    const where = (answer) => {
      const places = choices.flatMap((s) => {
        const m = s.options?.[answer];
        if (!m) return [];
        if (m.widget) return ["form field"];
        return m.page !== null && m.page !== undefined ? [`box, page ${m.page + 1}`] : [];
      });
      return places.length ? places.join(" · ") : "no box";
    };
    const line = (a) => make("li", {}, make("span", { textContent: a }), ...(choices.length ? [make("span", { textContent: where(a) })] : []));
    const list = make("ul", { className: "answers" }, ...answers.map(line));
    return [make("p", { className: "fc-ins-lab", textContent: choices.length ? "Answers · a box each" : "Answers" }), list];
  }

  /** The wordings read on the samples that the answers' map lacks (FieldProof.suggested), and accepting them. */
  function wordingsParts(path, fp) {
    const suggested = Object.entries(fp?.suggested ?? {});
    if (suggested.length === 0) return [];
    const items = suggested.map(([raw, answer]) => make("li", { textContent: `${raw} → ${answer}` }));
    const list = make("ul", { className: "answers", id: "ins-wordings" }, ...items);
    const learn = make("button", { type: "button", className: "btn quiet small", id: "ins-learn", textContent: "Add to the answers' map" });
    learn.addEventListener("click", () => change((id) => calls.learn(id, path)));
    return [
      make("p", { className: "fc-ins-lab", textContent: "Wordings seen on the samples" }),
      list,
      make("p", { className: "fc-ins-hint", textContent: "Read on the samples, where the AI read the answer beside them. Added to the map, each reads as its answer." }),
      learn,
    ];
  }

  /** Mark the row's field not on this form (each item of a repeated group's), or bring it back. */
  function setAbsent(row, absent) {
    for (const path of row.paths) {
      const now = proofOf(path)?.absent ?? false;
      if (now !== absent) edit({ op: absent ? "mark_absent" : "restore", path });
    }
  }

  /** One line per sample for the field selected; a click shows that sample. */
  function renderSamples() {
    const fp = state.selected === null ? null : proofOf(state.selected);
    $("per-sample").replaceChildren(
      ...state.setup.samples.map((smp, i) => {
        const sp = fp?.samples?.[i];
        const line = sp ? outcomeLine(sp) : { glyph: "·", text: smp.name };
        const why = make("span", { className: "why", textContent: `${smp.name} · ${smp.kind}` });
        const text = make("span", {}, `${i + 1}. ${line.text}`, why);
        const li = make("li", { className: i === state.sample ? "on" : "" }, make("span", { textContent: line.glyph }), text);
        li.addEventListener("click", () => showSample(i));
        return li;
      }),
    );
  }

  function renderCaption() {
    const { ready, total } = state.setup.proof;
    $("cap").textContent = `${ready} of ${total} fields ready · click a box or a field to see how it is read`;
  }

  // ------------------------------------------------------------------ add a field from a line

  async function addField() {
    const line = $("add").value.trim();
    const msg = $("add-msg");
    if (!line) {
      app.say(msg, "Say what to add, in your words: wire instructions, bank and account.");
      return;
    }
    if (adding || state.setup === null) return;
    adding = true;
    $("add-go").disabled = true;
    app.say(msg, `Finding “${line}” on the samples…`);
    const s = state.setup;
    pending += 1;
    // on the queue: the changes before it go in first, and those made while it runs (an edit, a drag, Save) wait
    // for it, as the server takes no change to a setup while a line job runs on it
    const run = queue.then(async () => {
      try {
        if (state.setup?.id !== s.id) return;
        const job = await (await calls.startLine(s.id, line)).done;
        if (state.setup?.id !== s.id) return;
        if (job.status !== "done") throw new Error(job.status === "cancelled" ? "Adding the field was cancelled." : job.error || "The field couldn't be added.");
        const { setup, paths } = job.result;
        $("add").value = "";
        app.say(msg, paths.length ? "" : `No new field came of “${line}”: nothing on the samples answers it, or its fields are in the list already.`);
        replace(setup, paths[0] ?? null);
        if (paths.length) select(paths[0], { turn: true });
      } catch (e) {
        if (isGone(e)) lost();
        else app.say(msg, e.message);
      } finally {
        pending -= 1;
        adding = false;
        $("add-go").disabled = false;
      }
    });
    queue = run;
    await run;
  }

  $("add-go").addEventListener("click", addField);
  $("add").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    addField();
  });

  // ------------------------------------------------------------------ the sample page

  /** The shown page's [width, height] in points. */
  function pageSize() {
    return state.setup?.samples[state.sample]?.pages[state.page] ?? [612, 792];
  }

  function renderPager() {
    const s = state.setup;
    if (s === null) return;
    const smp = s.samples[state.sample];
    const pages = smp.pages.length;
    $("sample-no").textContent = `Sample ${state.sample + 1} / ${s.samples.length}`;
    $("sample-kind").textContent = `${smp.name} · ${smp.kind}`;
    $("page-of").textContent = `Page ${state.page + 1} / ${pages}`;
    $("prev-s").disabled = $("next-s").disabled = s.samples.length < 2;
    $("prev-p").disabled = state.page === 0;
    $("next-p").disabled = state.page >= pages - 1;
  }

  /** Show the sample `i` (round the samples), from its first page. */
  function showSample(i) {
    const n = state.setup.samples.length;
    state.sample = (i + n) % n;
    state.page = 0;
    state.marks = [];
    showPage();
    renderSamples();
    loadMarks();
  }

  /** Show the page `state.page` of the sample shown, with its marks. */
  function showPage() {
    const s = state.setup;
    const [width, height] = pageSize();
    const sample = state.sample;
    const page = state.page;
    view.setPage({
      index: page,
      width,
      height,
      rotation: 0,
      render: (_canvas, scale) => calls.samplePageUrl(s.id, sample, page, Math.min(MAX_DPI, Math.max(MIN_DPI, scale * 72))),
    });
    applyZoom();
    drawMarks();
    renderPager();
  }

  /** Fetch the shown sample's marks and draw them. */
  async function loadMarks() {
    const s = state.setup;
    if (s === null) return;
    const sample = state.sample;
    const token = ++marksToken;
    pending += 1;
    try {
      const marks = await calls.marks(s.id, sample);
      if (token !== marksToken) return;
      state.marks = marks;
      drawMarks();
    } catch (e) {
      if (token !== marksToken) return;
      if (isGone(e)) lost();
      else app.notice(`The sample's boxes couldn't be shown: ${e.message}`);
    } finally {
      pending -= 1;
    }
  }

  /** The marks of `path` on any page, its primary kind first. */
  function marksOf(path) {
    return state.marks.filter((m) => m.path === path).sort((a, b) => PRIMARY[a.kind] - PRIMARY[b.kind]);
  }

  /** The box id of `path`'s first mark on the page shown, or null. */
  function primaryOf(path) {
    if (path === null) return null;
    for (const [id, m] of shown) if (m.path === path && m.primary && m.page === state.page) return id;
    return null;
  }

  /**
   * The marks as the page view's template: a field per mark (`<path>#box`, `<path>#opt:<answer>`, `<path>#read`,
   * `<path>#label`; a second mark of the same id gets `~2`), the read places and labels first so the boxes are on
   * top. A mark the same as one before it (a checkbox read as a widget and as a drawn box) is drawn once.
   */
  function drawMarks() {
    if (held) {
      redrawLater = true;
      return;
    }
    shown.clear();
    const fields = [];
    const seen = new Set();
    const firsts = new Set();
    const ordered = [...state.marks].sort((a, b) => LAYER[a.kind] - LAYER[b.kind]);
    const primaries = new Set();
    for (const m of [...state.marks].sort((a, b) => PRIMARY[a.kind] - PRIMARY[b.kind])) {
      const key = `${m.path}|${m.page}`;
      if (!firsts.has(key)) {
        firsts.add(key);
        primaries.add(m);
      }
    }
    for (const m of ordered) {
      const base = m.kind === "option" ? `${m.path}#opt:${m.answer}` : `${m.path}#${m.kind}`;
      const same = `${base}|${m.page}|${m.rect.join(",")}`;
      if (seen.has(same)) continue;
      seen.add(same);
      let id = base;
      for (let k = 2; shown.has(id); k++) id = `${base}~${k}`;
      shown.set(id, { path: m.path, kind: m.kind, answer: m.answer ?? null, page: m.page, rect: m.rect, primary: primaries.has(m) });
      const words = m.kind === "option" ? `the ${m.answer} checkbox` : KIND_WORDS[m.kind];
      fields.push({ id, type: "text", page: m.page, rect: m.rect, label: `${m.path}, ${words}`, source: { paths: [m.path] }, origin: "human" });
    }
    view.setTemplate({ fields });
    view.select(primaryOf(state.selected));
    decorate();
  }

  /** Mark each box with its kind, the selected field's heavy, and its primary one as the one tagged with its path. */
  function decorate() {
    const picked = new Set(state.selected === null ? [] : (rowOf(state.selected)?.paths ?? [state.selected]));
    for (const el of pvEl.querySelectorAll(".fc-box")) {
      const mark = shown.get(el.dataset.fieldId);
      if (mark === undefined) continue;
      el.dataset.kind = mark.kind;
      el.classList.toggle("is-picked", picked.has(mark.path));
      el.classList.toggle("has-tag", mark.primary);
    }
  }
  // the page view draws its boxes again on its own (a zoom, a resize): mark them again each time
  new MutationObserver(decorate).observe(pvEl, { childList: true, subtree: true });

  bus.on("select", ({ id }) => {
    const mark = id === null ? null : shown.get(id);
    state.selected = mark ? mark.path : null;
    renderFields();
    renderSide();
    $("fields").querySelector(".is-picked")?.scrollIntoView({ block: "nearest" });
    decorate();
  });
  bus.on("op", ({ op }) => {
    const mark = shown.get(op.id);
    if (mark === undefined || (op.op !== "move" && op.op !== "resize")) return; // a box is not added or deleted here
    if (mark.kind === "read" || mark.kind === "label") {
      drawMarks(); // these only show where the readers look: put it back
      return;
    }
    const where = { path: mark.path, page: mark.page, rect: op.rect, sample: state.sample };
    edit(mark.kind === "box" ? { op: "set_box", ...where } : { op: "set_option_box", answer: mark.answer, ...where });
  });

  pvEl.addEventListener("pointerdown", (e) => {
    if (e.button === 0) held = true;
  }, { capture: true }); // prettier-ignore
  for (const type of ["pointerup", "pointercancel", "contextmenu"]) {
    addEventListener(type, () => {
      if (!held) return;
      held = false;
      if (redrawLater) {
        redrawLater = false;
        drawMarks();
      }
    });
  }

  $("prev-s").addEventListener("click", () => showSample(state.sample - 1));
  $("next-s").addEventListener("click", () => showSample(state.sample + 1));
  $("prev-p").addEventListener("click", () => turnPage(-1));
  $("next-p").addEventListener("click", () => turnPage(1));

  function turnPage(step) {
    const pages = state.setup.samples[state.sample].pages.length;
    const next = Math.max(0, Math.min(pages - 1, state.page + step));
    if (next === state.page) return;
    state.page = next;
    showPage();
  }

  // ------------------------------------------------------------------ zoom

  const zoomBar = $("setup-zoom");
  const col = pvEl.closest(".col-page");

  /** The page's width at scale 1, in pixels. */
  function naturalWidth() {
    const [width, height] = pageSize();
    return viewSize(makeView({ width, height, rotation: 0, scale: 1 })).width;
  }

  /** The two fits for the column: as wide as the column, and the whole page in view (within the window). */
  function fits() {
    const cs = getComputedStyle(col);
    const wide = col.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const used = col.querySelector(".pagebar").offsetHeight + $("cap").offsetHeight + 12;
    const high = Math.min(col.clientHeight, innerHeight) - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - used;
    const [, height] = pageSize();
    const width = Math.max(0.1, wide / naturalWidth());
    return { width, page: Math.max(0.1, Math.min(width, high / height)) };
  }

  const scaleNow = () => (typeof state.zoom === "number" ? state.zoom : fits()[state.zoom]);

  /** Size the page for the zoom, and show the level and which fit is on. */
  function applyZoom() {
    if (state.setup === null || col.offsetParent === null) return; // not on show
    const scale = scaleNow();
    pvEl.style.width = state.zoom === "width" ? "" : `${Math.floor(naturalWidth() * scale)}px`;
    view.setScale(scale);
    zoomBar.querySelector("[data-level]").textContent = `${Math.round(scale * 100)}%`;
    for (const b of zoomBar.querySelectorAll("[data-fit]")) b.setAttribute("aria-pressed", String(b.dataset.fit === state.zoom));
    zoomBar.querySelector('[data-step="-1"]').disabled = scale <= ZOOMS[0] + 1e-3;
    zoomBar.querySelector('[data-step="1"]').disabled = scale >= ZOOMS.at(-1) - 1e-3;
  }

  /** One zoom step from the scale now (a fit's included) to the next level up or down. */
  function stepZoom(dir) {
    const now = scaleNow();
    const next = dir > 0 ? ZOOMS.find((z) => z > now + 1e-3) : [...ZOOMS].reverse().find((z) => z < now - 1e-3);
    if (next === undefined) return;
    state.zoom = next;
    applyZoom();
  }

  for (const b of zoomBar.querySelectorAll("[data-fit]")) {
    b.addEventListener("click", () => {
      state.zoom = b.dataset.fit;
      applyZoom();
    });
  }
  for (const b of zoomBar.querySelectorAll("[data-step]")) b.addEventListener("click", () => stepZoom(Number(b.dataset.step)));
  zoomBar.querySelector("[data-level]").addEventListener("click", () => {
    state.zoom = "width";
    applyZoom();
  });
  // Ctrl (or ⌘) and the wheel over the page zoom it, as in a PDF viewer
  col.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    stepZoom(e.deltaY < 0 ? 1 : -1);
  }, { passive: false }); // prettier-ignore
  // a fit follows the column when the window or the panes change size, and when the screen is shown again
  new ResizeObserver(() => {
    if (typeof state.zoom === "string") applyZoom();
  }).observe(col);

  // ------------------------------------------------------------------ save

  const saveMsg = $("setup-save-msg");

  /** Ask for a name (the last one saved, else the template's) and save the template under it. */
  function promptSave() {
    if (state.setup === null) return;
    $("setup-save-name").value = state.savedName ?? suggest(template().name);
    app.say(saveMsg, "");
    saveBox.hidden = false;
    $("setup-save-name").select();
  }

  let saving = false; // a save is under way: Save and Enter in the name box start no second one

  async function save() {
    if (saving) return;
    const name = $("setup-save-name").value.trim();
    if (!NAME_RE.test(name)) {
      app.say(saveMsg, "Use 1 to 80 letters, digits, spaces, dots, dashes or underscores, starting with a letter or digit.");
      return;
    }
    const go = $("setup-save-go");
    saving = true;
    go.disabled = true;
    app.say(saveMsg, "Saving…");
    try {
      await queue; // the changes on their way are part of what is saved
      const s = state.setup;
      if (s === null) return;
      await calls.save(name, s.id);
      state.savedName = name;
      state.changed = false;
      saveBox.hidden = true;
      app.notice(`Saved “${name}”, with its ${plural(s.samples.length, "sample")}.`);
    } catch (e) {
      if (isGone(e)) lost();
      else app.say(saveMsg, e.message);
    } finally {
      saving = false;
      go.disabled = false;
    }
  }

  $("setup-save").addEventListener("click", promptSave);
  $("setup-save-cancel").addEventListener("click", () => {
    saveBox.hidden = true;
  });
  $("setup-save-go").addEventListener("click", save);
  saveBox.addEventListener("keydown", (e) => {
    if (e.key === "Escape") saveBox.hidden = true;
    if (e.key === "Enter" && e.target === $("setup-save-name")) save();
  });
  for (const btn of $("view-pick").querySelectorAll("[data-view]")) {
    btn.addEventListener("click", () => {
      if (state.setup !== null && btn.dataset.view === "setup") showView("setup");
    });
  }

  return {
    open,
    promptSave,
    get current() {
      return state.setup;
    },
    get savedName() {
      return state.savedName;
    },
    get changed() {
      return state.changed;
    },
    get idle() {
      return pending === 0;
    },
  };
}
