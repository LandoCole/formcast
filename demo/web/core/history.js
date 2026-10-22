// The history panel: the template's history, newest first, and the undo and redo buttons.

/**
 * @typedef {import("./bus.js").Bus} Bus
 */

const AUTHORS = { human: "you", agent: "AI" };

/**
 * One history entry in words: the op, then the field it touched by its label (its id when the
 * field is gone or has no label).
 * @param {{op: Record<string, any>}} entry
 * @param {{id: string, label?: string}[]} fields
 * @returns {string}
 */
export function summarizeOp(entry, fields) {
  const op = entry.op ?? {};
  const name = String(op.op ?? "edit").replaceAll("_", " ");
  const id = op.id ?? null;
  let subject = "";
  if (typeof id === "string") {
    const field = fields.find((f) => f.id === id);
    subject = field?.label || id;
  } else if (typeof op.source_path === "string") {
    subject = op.source_path;
  }
  return subject === "" ? name : `${name} · ${subject}`;
}

function button(text, label, type, bus) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "fc-btn";
  b.dataset.action = type;
  b.textContent = text;
  b.setAttribute("aria-label", label);
  b.addEventListener("click", () => bus.emit(type));
  return b;
}

export class HistoryPanel {
  #el;
  #bus;

  /**
   * @param {HTMLElement} el
   * @param {{bus: Bus}} options
   */
  constructor(el, { bus }) {
    this.#el = el;
    this.#bus = bus;
    el.classList.add("fc-history");
  }

  /**
   * Show the template's history. Buttons are disabled when there is nothing to undo or redo.
   * @param {{fields: {id: string, label?: string}[], history: {op: object, author: string, at?: string, note?: string}[]}} template
   * @param {{canUndo: boolean, canRedo: boolean}} state
   */
  set(template, { canUndo, canRedo }) {
    const bar = document.createElement("div");
    bar.className = "fc-hi-bar";
    const undo = button("Undo", "Undo the last change", "undo", this.#bus);
    undo.disabled = !canUndo;
    const redo = button("Redo", "Redo the change you undid", "redo", this.#bus);
    redo.disabled = !canRedo;
    bar.append(undo, redo);

    const entries = template.history ?? [];
    const list = document.createElement("ul");
    list.className = "fc-hi-list";
    for (const entry of [...entries].reverse()) {
      const li = document.createElement("li");
      li.dataset.author = entry.author;
      const who = document.createElement("span");
      who.className = "fc-hi-who";
      who.textContent = AUTHORS[entry.author] ?? String(entry.author);
      const what = document.createElement("b");
      what.textContent = summarizeOp(entry, template.fields ?? []);
      li.append(who, what);
      if (entry.note) {
        const note = document.createElement("span");
        note.className = "fc-hi-note";
        note.textContent = `· ${entry.note}`;
        li.append(note);
      }
      if (entry.at) li.title = entry.at;
      list.append(li);
    }
    const children = [bar];
    if (entries.length === 0) {
      const empty = document.createElement("p");
      empty.className = "fc-hi-empty";
      empty.textContent = "No changes yet.";
      children.push(empty);
    } else {
      children.push(list);
    }
    this.#el.replaceChildren(...children);
  }
}
