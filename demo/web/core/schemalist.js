// The schema list: the flattened schema as a tree, each leaf with its type and whether a box uses
// it yet. Click a leaf to pick it (the host highlights the boxes that use it); drag a leaf onto a
// box in the page view to link it (desktop). A path a field uses that the current schema no longer
// has is listed at the end, flagged "not in schema", never dropped.

import { PATH_MIME } from "./pageview.js";
import { formatPath, isArrayItem, parsePath } from "./schema.js";

/**
 * @typedef {import("./bus.js").Bus} Bus
 * @typedef {import("./schema.js").SchemaPath} SchemaPath
 * @typedef {"placed" | "partly" | "none"} Status
 */

const CHOICE_TYPES = ["checkbox", "radio_option", "choice_mark"];

const MARKS = { placed: "✓", partly: "½", none: "·", orphan: "!" };
const STATUS_TEXT = { placed: "placed", partly: "partly placed", none: "not placed", orphan: "not in schema" };

/**
 * Where each schema path stands against a template, after src/formcast/template/coverage.py: a path is
 * placed when some field's source uses it or a table field covers its column; an enum (or array of an
 * enum) that only has option boxes is `partly` placed until it has a box per option. A path some field
 * uses that the schema does not have is an orphan.
 * @param {SchemaPath[]} schemaPaths
 * @param {{fields: {id: string, type: string, source?: {paths: string[]} | null}[]}} template
 * @returns {{status: Map<string, Status>, orphans: {path: string, fields: string[]}[]}}
 */
export function placementStatus(schemaPaths, template) {
  const byPath = new Map(schemaPaths.map((sp) => [sp.path, sp]));
  /** @type {Map<string, {id: string, type: string}[]>} */
  const used = new Map();
  const tables = new Set();
  for (const f of template?.fields ?? []) {
    const paths = f.source?.paths ?? [];
    if (f.type === "table" && paths.length > 0) tables.add(paths[0]);
    for (const p of paths) {
      if (!used.has(p)) used.set(p, []);
      used.get(p).push(f);
    }
  }
  const coveredByTable = (sp) => {
    for (let t = sp.in_table; t !== null && t !== undefined; t = byPath.get(t)?.in_table ?? null) {
      if (tables.has(t)) return true;
    }
    return false;
  };
  const status = new Map();
  for (const sp of schemaPaths) {
    if (sp.type === "object") continue;
    const fields = used.get(sp.path) ?? [];
    if (fields.length === 0) {
      status.set(sp.path, coveredByTable(sp) ? "placed" : "none");
    } else if (sp.enum !== null && sp.enum.length > 0 && fields.every((f) => CHOICE_TYPES.includes(f.type))) {
      status.set(sp.path, fields.length >= sp.enum.length ? "placed" : "partly");
    } else {
      status.set(sp.path, "placed");
    }
  }
  const orphans = [...used]
    .filter(([path]) => !byPath.has(path))
    .map(([path, fields]) => ({ path, fields: fields.map((f) => f.id) }));
  return { status, orphans };
}

/** The name shown for a path: its last segment, and the depth it is indented to. */
function nameAndDepth(path) {
  let segments;
  try {
    segments = parsePath(path);
  } catch {
    return { name: path, depth: 0 };
  }
  const props = segments.filter((s) => !isArrayItem(s));
  const last = segments.at(-1);
  const name = isArrayItem(last) ? formatPath(segments.slice(-2)) : formatPath([last]);
  return { name, depth: Math.max(0, props.length - 1) };
}

function typeText(sp) {
  if (sp.type === "array") return sp.item_type ? `${sp.item_type}[]` : "array";
  return sp.type;
}

export class SchemaList {
  #el;
  #bus;
  #draggable;
  #picked = null;

  /**
   * @param {HTMLElement} el
   * @param {{bus: Bus, draggable?: boolean}} options `draggable: false` for touch screens, where
   *   linking happens in the inspector
   */
  constructor(el, { bus, draggable = true }) {
    this.#el = el;
    this.#bus = bus;
    this.#draggable = draggable;
    el.classList.add("fc-schemalist");
    el.addEventListener("click", (e) => this.#onClick(e));
    el.addEventListener("keydown", (e) => this.#onKeyDown(e));
    el.addEventListener("dragstart", (e) => this.#onDragStart(e));
  }

  /**
   * Show the schema and where the template stands against it.
   * @param {SchemaPath[]} schemaPaths the flattened schema
   * @param {object} template
   */
  set(schemaPaths, template) {
    const { status, orphans } = placementStatus(schemaPaths, template);
    const list = document.createElement("ul");
    list.className = "fc-sl-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Data fields");
    for (const sp of schemaPaths) {
      const { name, depth } = nameAndDepth(sp.path);
      if (sp.type === "object") {
        const li = document.createElement("li");
        li.className = "fc-sl-grp";
        li.setAttribute("role", "presentation");
        li.style.setProperty("--depth", String(depth));
        li.textContent = name;
        list.append(li);
        continue;
      }
      list.append(this.#leaf(sp.path, name, depth, typeText(sp), status.get(sp.path), sp.description, true));
    }
    if (orphans.length > 0) {
      const head = document.createElement("li");
      head.className = "fc-sl-grp";
      head.setAttribute("role", "presentation");
      head.style.setProperty("--depth", "0");
      head.textContent = "Used by boxes, missing from the schema";
      list.append(head);
      for (const o of orphans) {
        list.append(this.#leaf(o.path, o.path, 0, "not in schema", "orphan", `Used by ${o.fields.join(", ")}`, false));
      }
    }
    this.#el.replaceChildren(list);
    this.#markPicked();
  }

  /**
   * Mark a path as the picked one (or none) without emitting `pick`.
   * @param {string | null} path
   */
  setPicked(path) {
    this.#picked = path;
    this.#markPicked();
  }

  #leaf(path, name, depth, type, status, description, canDrag) {
    const li = document.createElement("li");
    li.className = "fc-sl-leaf";
    li.dataset.path = path;
    li.dataset.status = status;
    li.style.setProperty("--depth", String(depth));
    li.tabIndex = 0;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", "false");
    li.setAttribute("aria-label", `${path}, ${type}, ${STATUS_TEXT[status]}`);
    if (description) li.title = description;
    if (canDrag && this.#draggable) li.draggable = true;
    const mark = document.createElement("span");
    mark.className = "fc-sl-st";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = MARKS[status];
    const label = document.createElement("span");
    label.className = "fc-sl-name";
    label.textContent = name;
    const ty = document.createElement("span");
    ty.className = "fc-sl-ty";
    ty.textContent = type;
    li.append(mark, label, ty);
    return li;
  }

  #markPicked() {
    for (const li of this.#el.querySelectorAll(".fc-sl-leaf")) {
      const on = li.dataset.path === this.#picked;
      li.classList.toggle("is-picked", on);
      li.setAttribute("aria-selected", String(on));
    }
  }

  #leafOf(e) {
    return e.target instanceof Element ? e.target.closest(".fc-sl-leaf") : null;
  }

  #pick(li) {
    this.#picked = li.dataset.path;
    this.#markPicked();
    this.#bus.emit("pick", { path: li.dataset.path });
  }

  #onClick(e) {
    const li = this.#leafOf(e);
    if (li) this.#pick(li);
  }

  #onKeyDown(e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    const li = this.#leafOf(e);
    if (!li) return;
    e.preventDefault();
    this.#pick(li);
  }

  #onDragStart(e) {
    const li = this.#leafOf(e);
    if (!li || li.dataset.status === "orphan" || !e.dataTransfer) return;
    e.dataTransfer.setData(PATH_MIME, li.dataset.path);
    e.dataTransfer.setData("text/plain", li.dataset.path);
    e.dataTransfer.effectAllowed = "link";
  }
}
