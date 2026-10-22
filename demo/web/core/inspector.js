// The inspector: the selected box's label, type, data field, transform chain and style, plus its
// warnings. Every edit is emitted as an op on the bus (`set_label`, `set_type`, `set_source`,
// `set_style`); the inspector never edits the template. The host applies the op and calls `show`
// again with the new field.
//
// The transform chain editor is built from `listTransforms()`: each step's argument inputs come
// from the transform's JSON Schema, so there is no per-transform code here. A chain that does not
// validate yet (a new `date_format` with no format) stays a draft in the panel and is emitted once
// it does.

import { listTransforms, validateChain } from "./transforms.js";

/**
 * @typedef {import("./bus.js").Bus} Bus
 * @typedef {{op: string, args?: Record<string, any>}} Step
 */

const FIELD_TYPES = ["text", "checkbox", "radio_option", "signature", "choice_mark"];
const TYPE_LABELS = {
  text: "Text",
  checkbox: "Checkbox",
  radio_option: "Radio option",
  signature: "Signature",
  choice_mark: "Choice mark",
  table: "Table",
};
const ALIGNS = ["left", "center", "right"];
const NO_STYLE = ["checkbox", "radio_option"];
const DEFAULT_FIXED_SIZE = 10;

const pathOf = (p) => (typeof p === "string" ? p : p.path);

/** Mappable paths for a field of this type, as a path-list: tables take arrays of objects, others take the rest. */
function choosable(schemaPaths, fieldType) {
  return schemaPaths.filter((sp) => {
    if (typeof sp === "string") return true;
    if (sp.type === "object") return false;
    const isTable = sp.type === "array" && sp.item_type === "object";
    if (sp.type === "array" && sp.item_type === "array") return false;
    return fieldType === "table" ? isTable : !isTable;
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function option(value, text, selected = false) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = text;
  o.selected = selected;
  return o;
}

function select(ctl, options, value) {
  const s = el("select", "fc-in");
  s.dataset.ctl = ctl;
  for (const [v, text] of options) s.append(option(v, text, v === value));
  return s;
}

function numberInput(ctl, value, { min, max, step = "any", placeholder = "" } = {}) {
  const i = el("input", "fc-in");
  i.type = "number";
  i.dataset.ctl = ctl;
  i.step = String(step);
  if (min !== undefined) i.min = String(min);
  if (max !== undefined) i.max = String(max);
  if (value !== undefined && value !== null) i.value = String(value);
  i.placeholder = placeholder;
  return i;
}

function iconButton(text, label, ctl, disabled = false) {
  const b = el("button", "fc-ib", text);
  b.type = "button";
  b.dataset.ctl = ctl;
  b.setAttribute("aria-label", label);
  b.title = label;
  b.disabled = disabled;
  return b;
}

/** Text shown for an untyped (any JSON value) argument: JSON unless the plain text reads back as itself. */
function anyToText(v) {
  if (typeof v === "string") {
    try {
      if (typeof JSON.parse(v) !== "string") return JSON.stringify(v);
    } catch {
      return v;
    }
    return JSON.stringify(v);
  }
  return JSON.stringify(v);
}

function textToAny(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** The args a freshly added step starts with: required enums take their first option, required strings are empty. */
function initialArgs(schema) {
  const args = {};
  for (const name of schema.required ?? []) {
    const prop = schema.properties?.[name] ?? {};
    if (Array.isArray(prop.enum)) args[name] = prop.enum[0];
    else if (prop.type === "string") args[name] = "";
    else if (prop.type === undefined && !("enum" in prop)) args[name] = "";
  }
  return args;
}

export class Inspector {
  #el;
  #bus;
  #catalogue = new Map(listTransforms().map((t) => [t.name, t]));
  #field = null;
  #schemaPaths = [];
  #warnings = [];
  /** @type {Step[]} */
  #draft = [];
  #chainErrors = [];

  /**
   * @param {HTMLElement} el
   * @param {{bus: Bus}} options
   */
  constructor(el, { bus }) {
    this.#el = el;
    this.#bus = bus;
    el.classList.add("fc-inspector");
  }

  /**
   * Show a field (or the empty state for null).
   * @param {object | null} field a template field
   * @param {{schemaPaths?: (string | import("./schema.js").SchemaPath)[], warnings?: (string | {message: string})[]}} [context]
   */
  show(field, { schemaPaths = [], warnings = [] } = {}) {
    const same = this.#field?.id === field?.id;
    this.#field = field;
    this.#schemaPaths = schemaPaths;
    this.#warnings = warnings;
    this.#draft = structuredClone(field?.source?.transforms ?? []);
    this.#chainErrors = [];
    this.#render(same);
  }

  #emit(op) {
    this.#bus.emit("op", { op });
  }

  /** @param {boolean} [keepFocus] put focus back on the control that had it (same field, edited) */
  #render(keepFocus = true) {
    const focused = keepFocus ? this.#el.querySelector(":focus")?.dataset.ctl : null;
    this.#draw();
    if (focused) this.#el.querySelector(`[data-ctl="${CSS.escape(focused)}"]`)?.focus();
  }

  #draw() {
    const f = this.#field;
    if (f === null) {
      this.#el.replaceChildren(el("p", "fc-ins-empty", "Select a box to edit it."));
      return;
    }
    const known = new Set(this.#schemaPaths.map(pathOf));
    const used = f.source?.paths ?? [];
    const orphans = used.filter((p) => !known.has(p));
    const parts = [];

    if (orphans.length > 0) {
      const warn = el("p", "fc-ins-warn is-orphan", "This field's data field is not in the current schema");
      warn.append(el("code", "", ` ${orphans.join(", ")}`));
      parts.push(warn);
    }
    for (const w of this.#warnings) {
      parts.push(el("p", "fc-ins-warn", typeof w === "string" ? w : w.message));
    }

    const dl = el("dl", "fc-ins-dl");
    // Each row's term names the controls in it that have no name of their own (an aria-label, or a
    // <label> around them), so a screen reader announces "Label", "Type", "Data field" and so on.
    const row = (name, ...nodes) => {
      const dt = el("dt", "", name);
      const dd = el("dd");
      dd.append(...nodes);
      for (const c of dd.querySelectorAll("input, select, textarea")) {
        if (!c.hasAttribute("aria-label") && c.closest("label") === null) c.setAttribute("aria-label", name);
      }
      dl.append(dt, dd);
    };

    const label = el("input", "fc-in");
    label.type = "text";
    label.dataset.ctl = "label";
    label.value = f.label ?? "";
    label.addEventListener("change", () => {
      if (label.value !== (f.label ?? "")) this.#emit({ op: "set_label", id: f.id, label: label.value });
    });
    row("Label", label);

    const types = f.type === "table" ? ["table"] : FIELD_TYPES;
    const type = select("type", types.map((t) => [t, TYPE_LABELS[t]]), f.type);
    type.disabled = f.type === "table";
    type.addEventListener("change", () => this.#emit({ op: "set_type", id: f.id, type: type.value }));
    row("Type", type);

    row("Data field", ...this.#dataField(f, orphans));

    if (f.type === "table") row("Table", this.#segments(f));
    if (!NO_STYLE.includes(f.type) && f.type !== "table") this.#style(f, row);

    parts.push(dl);
    if (used.length > 0 || this.#draft.length > 0) parts.push(this.#chainEditor(f));
    this.#el.replaceChildren(...parts);
  }

  #dataField(f, orphans) {
    const used = f.source?.paths ?? [];
    const options = [["", "not linked"]];
    for (const sp of choosable(this.#schemaPaths, f.type)) options.push([pathOf(sp), pathOf(sp)]);
    for (const p of orphans) options.push([p, `${p} (not in schema)`]);
    const s = select("path", options, used[0] ?? "");
    s.addEventListener("change", () => {
      if (s.value === "") {
        this.#emit({ op: "set_source", id: f.id, source: null });
        return;
      }
      this.#emit({ op: "set_source", id: f.id, source: { ...(f.source ?? { transforms: [] }), paths: [s.value, ...used.slice(1)] } });
    });
    const nodes = [s];
    if (used.length > 1) nodes.push(el("p", "fc-ins-hint", `Also uses ${used.slice(1).join(", ")}`));
    return nodes;
  }

  #segments(f) {
    const box = el("div", "fc-ins-seg");
    f.segments.forEach((seg, i) => {
      const keys = seg.columns.map((c) => c.key).join(", ");
      box.append(el("p", "fc-ins-hint", `Part ${i + 1}: page ${seg.page + 1}, ${seg.max_rows} rows; columns ${keys}`));
    });
    return box;
  }

  #style(f, row) {
    const style = { font_size: "auto", min_font_size: 6, align: "left", comb: null, multiline: false, ...f.style };
    const set = (key, value) => this.#emit({ op: "set_style", id: f.id, style: { [key]: value } });

    const mode = select("font-mode", [["auto", "Auto-fit"], ["fixed", "Fixed"]], style.font_size === "auto" ? "auto" : "fixed");
    const size = numberInput("font-size", style.font_size === "auto" ? "" : style.font_size, { min: 2, step: 0.5 });
    size.disabled = style.font_size === "auto";
    mode.setAttribute("aria-label", "Auto-fit or fixed font size");
    size.setAttribute("aria-label", "Fixed font size in points");
    mode.addEventListener("change", () => set("font_size", mode.value === "auto" ? "auto" : DEFAULT_FIXED_SIZE));
    size.addEventListener("change", () => {
      if (size.value !== "") set("font_size", Number(size.value));
    });
    const wrap = el("div", "fc-ins-pair");
    wrap.append(mode, size);
    row("Font size", wrap);

    const min = numberInput("min-size", style.min_font_size, { min: 2, step: 0.5 });
    min.addEventListener("change", () => {
      if (min.value !== "") set("min_font_size", Number(min.value));
    });
    row("Smallest", min);

    const align = select("align", ALIGNS.map((a) => [a, a[0].toUpperCase() + a.slice(1)]), style.align);
    align.addEventListener("change", () => set("align", align.value));
    row("Align", align);

    const comb = numberInput("comb", style.comb, { min: 1, step: 1, placeholder: "none" });
    comb.addEventListener("change", () => set("comb", comb.value === "" ? null : Number(comb.value)));
    row("Comb cells", comb);

    const multi = el("input");
    multi.type = "checkbox";
    multi.dataset.ctl = "multiline";
    multi.checked = Boolean(style.multiline);
    multi.addEventListener("change", () => set("multiline", multi.checked));
    const multiLabel = el("label", "fc-ins-check");
    multiLabel.append(multi, " Wrap onto several lines");
    row("Lines", multiLabel);
  }

  // ---- transform chain ----

  #chainEditor(f) {
    const box = el("section", "fc-chain");
    box.setAttribute("aria-label", "Transforms");
    box.append(el("p", "fc-ins-lab", "Transforms"));
    const list = el("ol", "fc-chain-list");
    this.#draft.forEach((step, i) => list.append(this.#step(step, i)));
    box.append(list);
    if (this.#draft.length === 0) box.append(el("p", "fc-ins-hint", "The value is used as it is."));

    const linked = (f.source?.paths ?? []).length > 0;
    const add = el("select", "fc-in fc-chain-add");
    add.dataset.ctl = "add-transform";
    add.setAttribute("aria-label", "Add a transform");
    add.disabled = !linked;
    add.append(option("", "Add a transform…"));
    for (const t of this.#catalogue.values()) add.append(option(t.name, t.name));
    add.addEventListener("change", () => {
      if (add.value === "") return;
      this.#draft.push({ op: add.value, args: initialArgs(this.#catalogue.get(add.value).args) });
      this.#chainChanged(f);
    });
    box.append(add);

    if (this.#chainErrors.length > 0) {
      const err = el("p", "fc-ins-warn fc-chain-error");
      err.setAttribute("role", "status");
      err.textContent = `Not applied yet: ${this.#chainErrors.join("; ")}`;
      box.append(err);
    }
    return box;
  }

  #step(step, index) {
    const f = this.#field;
    const entry = this.#catalogue.get(step.op);
    const li = el("li", "fc-step");
    li.dataset.op = step.op;
    const head = el("div", "fc-step-head");
    const name = select(`step-op-${index}`, [...this.#catalogue.keys()].map((n) => [n, n]), step.op);
    name.setAttribute("aria-label", `Transform ${index + 1}`);
    name.addEventListener("change", () => {
      this.#draft[index] = { op: name.value, args: initialArgs(this.#catalogue.get(name.value).args) };
      this.#chainChanged(f);
    });
    const up = iconButton("↑", "Move up", `step-up-${index}`, index === 0);
    const down = iconButton("↓", "Move down", `step-down-${index}`, index === this.#draft.length - 1);
    const del = iconButton("×", "Remove", `step-remove-${index}`);
    up.addEventListener("click", () => this.#moveStep(index, -1));
    down.addEventListener("click", () => this.#moveStep(index, 1));
    del.addEventListener("click", () => {
      this.#draft.splice(index, 1);
      this.#chainChanged(f);
    });
    head.append(name, up, down, del);
    li.append(head);

    if (entry !== undefined) {
      const props = entry.args.properties ?? {};
      for (const [argName, schema] of Object.entries(props)) {
        li.append(this.#arg(step, index, argName, schema, (entry.args.required ?? []).includes(argName)));
      }
      const note = entry.takesList ? `${entry.doc} It takes a list.` : entry.doc;
      li.append(el("p", "fc-ins-hint", note));
    }
    return li;
  }

  #moveStep(index, by) {
    const to = index + by;
    if (to < 0 || to >= this.#draft.length) return;
    const [step] = this.#draft.splice(index, 1);
    this.#draft.splice(to, 0, step);
    this.#chainChanged(this.#field);
  }

  /** One labelled input for a step argument, from the argument's JSON Schema. */
  #arg(step, index, name, schema, required) {
    const wrap = el("label", "fc-arg");
    wrap.append(el("span", "fc-arg-name", name));
    const args = (step.args ??= {});
    const cur = args[name];
    const set = (value) => {
      if (value === undefined) delete args[name];
      else args[name] = value;
      this.#chainChanged(this.#field);
    };
    let input;
    if (Array.isArray(schema.enum)) {
      input = select(`arg-${index}-${name}`, [...(required ? [] : [["", ""]]), ...schema.enum.map((v) => [String(v), String(v)])], cur === undefined ? "" : String(cur));
      if (required && cur === undefined) input.prepend(option("", "", true));
      input.addEventListener("change", () => {
        const picked = schema.enum.find((v) => String(v) === input.value);
        set(picked);
      });
    } else if (schema.type === "integer" || schema.type === "number") {
      input = numberInput(`arg-${index}-${name}`, cur, {
        min: schema.minimum,
        max: schema.maximum,
        step: schema.type === "integer" ? 1 : "any",
        placeholder: schema.default === undefined ? "" : String(schema.default),
      });
      input.addEventListener("change", () => set(input.value === "" ? undefined : Number(input.value)));
    } else if (schema.type === "boolean") {
      input = el("input");
      input.type = "checkbox";
      input.dataset.ctl = `arg-${index}-${name}`;
      input.checked = cur === true;
      input.addEventListener("change", () => set(input.checked));
    } else if (schema.type === "string") {
      input = el("input", "fc-in");
      input.type = "text";
      input.dataset.ctl = `arg-${index}-${name}`;
      input.value = cur ?? "";
      input.placeholder = schema.default === undefined ? "" : String(schema.default);
      input.addEventListener("change", () => set(input.value === "" && !required ? undefined : input.value));
    } else {
      // no type: any JSON value (equals, contains, default)
      input = el("input", "fc-in");
      input.type = "text";
      input.dataset.ctl = `arg-${index}-${name}`;
      input.value = cur === undefined ? "" : anyToText(cur);
      input.addEventListener("change", () => set(input.value === "" ? (required ? "" : undefined) : textToAny(input.value)));
    }
    wrap.append(input);
    return wrap;
  }

  /** The draft changed: emit it when it is a valid chain, else keep it and say what is missing. */
  #chainChanged(f) {
    this.#chainErrors = validateChain(this.#draft);
    if (this.#chainErrors.length === 0) {
      const source = { ...(f.source ?? { paths: [] }), transforms: structuredClone(this.#draft) };
      this.#emit({ op: "set_source", id: f.id, source });
    }
    this.#render();
  }
}
