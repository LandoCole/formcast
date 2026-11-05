// The chat panel: the Chat tab of both template editors. A person asks the AI for a fix in words, with an area of the
// page when they marked one; the editor's `send` asks (a job on the server) and answers with the AI's reply and the
// changes it proposes, which the panel lists with Apply and Discard. Nothing changes until Apply; once applied or
// discarded the buttons give way to the outcome. The panel knows nothing of either editor: `send` and the answer's
// `apply` and `discard` are theirs.
//
// The conversation lives here, in memory only: nothing is stored, and a reload forgets it. Each message is sent with
// the last MAX_HISTORY exchanges answered before it ({message, reply, changes, outcome}, `changes` the proposal in one
// line), so the server keeps no chat. Clear (and `clear()`, which an editor calls when another template opens) empties
// it, dropping any proposal still pending.
//
// The thread: the person's messages (an area as a chip, "page N · [x0, y0, x1, y1]"), the replies, each with its list of
// changes and the preview of what they read (`preview`, when the editor gives one), and Apply / Discard. While a
// message is on its way, a line says so with Cancel. A message that failed says why, with Try again (the same message,
// area and history): an answer the AI could not make usable (the editor's error code `unusable`) says UNUSABLE. An
// Apply refused because the template changed meanwhile (code `stale_setup` or `stale`) says STALE and drops the
// proposal. The composer: the area marked for the next message as a chip that can be removed, the text box (Enter
// sends, Shift and Enter is a new line), Send and Clear. With `available: false` (no model configured) the panel says
// so and has no composer.

/** At most this many earlier exchanges go with a message (the server refuses more). */
export const MAX_HISTORY = 3;
/** What an Apply refused as stale says. */
export const STALE = "The template changed since this was proposed; ask again.";
/** What an answer the AI could not make usable says. */
export const UNUSABLE = "The AI's answer could not be used.";
/** What the panel says when no model is configured. */
export const AI_OFF = "The AI is not configured on this server, so it can't be asked for a fix here. Edit the template by hand, or set up a model and restart the server.";

/**
 * @typedef {{sample?: number, page: number, rect: number[]}} ChatArea a place on the page, `rect` in its points
 * @typedef {{message: string, reply: string, changes: string, outcome: "applied" | "discarded" | "pending"}} Exchange
 * @typedef {{reply: string, changes: string[], preview?: string[], apply: (() => Promise<void>) | null,
 *   discard: () => void}} Answer `apply` null when there is nothing to apply (an answer with no changes)
 * @typedef {(message: string, area: ChatArea | null, history: Exchange[], signal: AbortSignal) => Promise<Answer>} Send
 */

/** An element with `props` set and `children` appended. */
function make(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  Object.assign(el, props);
  el.append(...children);
  return el;
}

function button(text, className, onClick) {
  const b = make("button", { type: "button", className: `fc-btn ${className}`, textContent: text });
  b.addEventListener("click", onClick);
  return b;
}

/** An area in words: "page 3 · [302, 214, 572, 226]". */
export function areaText(area) {
  return `page ${area.page + 1} · [${area.rect.map((v) => Math.round(v)).join(", ")}]`;
}

export class ChatPanel {
  #el;
  #send;
  #available;
  #onArea;
  /** The thread, oldest first: {message, area, state: "waiting" | "answered" | "failed", ...the answer, outcome, note, error}. */
  #turns = [];
  /** @type {ChatArea | null} */
  #area = null;
  /** The message on its way's controller, or null. */
  #waiting = null;
  #thread;
  #text = null;
  #sendBtn = null;
  #chipSlot = null;

  /**
   * @param {HTMLElement} el the panel's host
   * @param {{send: Send, available: boolean, onArea?: (area: ChatArea | null) => void}} opts `send` asks the AI (see
   *   the top; `signal` aborts when the person cancels); `available` false says the AI is off; `onArea(null)` is
   *   called when the panel lets go of the area marked (removed, sent, or cleared), so the page can stop showing it
   */
  constructor(el, { send, available, onArea = () => {} }) {
    this.#el = el;
    this.#send = send;
    this.#available = Boolean(available);
    this.#onArea = onArea;
    el.classList.add("fc-chat");
    if (!this.#available) {
      el.replaceChildren(make("p", { className: "fc-chat-off", textContent: AI_OFF }));
      return;
    }
    this.#thread = make("ol", { className: "fc-chat-thread" });
    this.#thread.setAttribute("aria-live", "polite");
    this.#text = make("textarea", { className: "fc-chat-text", rows: 3, placeholder: "Describe the fix, in your words.", ariaLabel: "Ask for a fix" });
    this.#text.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      this.#submit();
    });
    this.#sendBtn = button("Send", "is-solid fc-chat-send", () => this.#submit());
    this.#chipSlot = make("div", { className: "fc-chat-pending" });
    const hint = make("p", { className: "fc-chat-hint", textContent: "Drag on the page to point at an area: it goes with the next message." });
    const composer = make(
      "div",
      { className: "fc-chat-composer" },
      make("p", { className: "fc-chat-label", textContent: "Ask for a fix" }),
      this.#chipSlot,
      this.#text,
      make("div", { className: "fc-chat-row" }, hint, button("Clear", "fc-chat-clear", () => this.clear()), this.#sendBtn),
    );
    el.replaceChildren(this.#thread, composer);
    this.#render();
  }

  /** Empty the thread and its history: a message on its way is cancelled, a proposal pending discarded. */
  clear() {
    if (!this.#available) return;
    this.#waiting?.abort();
    this.#waiting = null;
    for (const turn of this.#turns) if (turn.state === "answered" && turn.outcome === "pending") turn.discard();
    this.#turns = [];
    this.setArea(null);
    this.#render();
  }

  /** The area the next message goes with (null for none). */
  setArea(area) {
    if (!this.#available) return;
    const had = this.#area !== null;
    this.#area = area;
    this.#renderArea();
    if (area === null && had) this.#onArea(null);
  }

  /** The exchanges answered before `turn` (every one, for none), at most MAX_HISTORY, oldest first. */
  #historyBefore(turn = null) {
    const end = turn === null ? this.#turns.length : this.#turns.indexOf(turn);
    return this.#turns
      .slice(0, end)
      .filter((t) => t.state === "answered")
      .slice(-MAX_HISTORY)
      .map((t) => ({ message: t.message, reply: t.reply, changes: t.changes.join("; ") || "no changes", outcome: t.outcome }));
  }

  #submit() {
    const message = this.#text.value.trim();
    if (!message || this.#waiting !== null) {
      this.#text.focus();
      return;
    }
    const turn = { message, area: this.#area, state: "waiting" };
    this.#turns.push(turn);
    this.#text.value = "";
    this.setArea(null);
    this.#ask(turn);
  }

  /** Send `turn`'s message (again, for Try again) and show what comes of it. */
  async #ask(turn) {
    const controller = new AbortController();
    this.#waiting = controller;
    turn.state = "waiting";
    turn.cancel = () => {
      controller.abort();
      if (this.#waiting === controller) this.#waiting = null;
      Object.assign(turn, { state: "failed", error: "Cancelled." });
      this.#render();
    };
    this.#render();
    let answer = null;
    let error = null;
    try {
      answer = await this.#send(turn.message, turn.area, this.#historyBefore(turn), controller.signal);
    } catch (e) {
      error = e;
    }
    if (this.#waiting !== controller) {
      answer?.discard?.(); // cancelled or cleared meanwhile: what came of it is dropped
      return;
    }
    this.#waiting = null;
    if (error !== null) {
      Object.assign(turn, { state: "failed", error: error?.code === "unusable" ? UNUSABLE : error?.message || String(error) });
    } else {
      const apply = answer.apply ?? null;
      Object.assign(turn, {
        state: "answered",
        reply: answer.reply,
        changes: answer.changes ?? [],
        preview: answer.preview ?? [],
        apply,
        discard: answer.discard ?? (() => {}),
        outcome: apply === null ? "applied" : "pending", // nothing to apply: nothing was left out
        note: null,
      });
    }
    this.#render();
  }

  async #apply(turn) {
    turn.busy = true;
    this.#render();
    try {
      await turn.apply();
      Object.assign(turn, { outcome: "applied", note: "Applied." });
    } catch (e) {
      if (e?.code === "stale_setup" || e?.code === "stale") {
        turn.discard();
        Object.assign(turn, { outcome: "discarded", note: STALE });
      } else {
        turn.note = `Not applied: ${e?.message || e}`; // still pending: Apply again, or Discard
      }
    } finally {
      turn.busy = false;
    }
    this.#render();
  }

  #discard(turn) {
    turn.discard();
    Object.assign(turn, { outcome: "discarded", note: "Discarded." });
    this.#render();
  }

  #renderArea() {
    if (this.#area === null) {
      this.#chipSlot.replaceChildren();
      return;
    }
    const remove = make("button", { type: "button", className: "fc-chat-unchip", textContent: "×", ariaLabel: "Leave the area out" });
    remove.addEventListener("click", () => this.setArea(null));
    this.#chipSlot.replaceChildren(make("span", { className: "fc-chat-chip" }, areaText(this.#area), remove));
  }

  #render() {
    this.#sendBtn.disabled = this.#waiting !== null;
    const items = [];
    for (const turn of this.#turns) {
      const me = make("li", { className: "fc-chat-me" });
      if (turn.area) me.append(make("span", { className: "fc-chat-chip", textContent: areaText(turn.area) }));
      me.append(make("p", { textContent: turn.message }));
      items.push(me);
      if (turn.state === "waiting") items.push(this.#waitingItem(turn));
      else if (turn.state === "failed") items.push(this.#failedItem(turn));
      else items.push(this.#answerItem(turn));
    }
    this.#thread.replaceChildren(...items);
    items.at(-1)?.scrollIntoView?.({ block: "nearest" });
  }

  #waitingItem(turn) {
    return make(
      "li",
      { className: "fc-chat-wait" },
      make("p", { className: "fc-chat-label", textContent: "Asking the AI" }),
      make("p", { className: "fc-chat-readout", textContent: "It reads the template and the samples, and proves its changes on them…" }),
      button("Cancel", "fc-chat-cancel", () => turn.cancel()),
    );
  }

  #failedItem(turn) {
    const retry = button("Try again", "fc-chat-retry", () => this.#ask(turn));
    retry.disabled = this.#waiting !== null;
    return make("li", { className: "fc-chat-error" }, make("p", { textContent: turn.error }), retry);
  }

  #answerItem(turn) {
    const li = make("li", { className: "fc-chat-ai" }, make("p", { className: "fc-chat-who", textContent: "AI" }), make("p", { className: "fc-chat-reply", textContent: turn.reply }));
    if (turn.changes.length > 0 || turn.preview.length > 0) {
      const box = make("div", { className: "fc-chat-change" });
      if (turn.changes.length > 0) box.append(make("ul", { className: "fc-chat-changes" }, ...turn.changes.map((c) => make("li", { textContent: c }))));
      if (turn.preview.length > 0) box.append(make("ul", { className: "fc-chat-proof" }, ...turn.preview.map((c) => make("li", { textContent: c }))));
      li.append(box);
    }
    if (turn.apply !== null && turn.outcome === "pending") {
      const apply = button("Apply", "is-solid fc-chat-apply", () => this.#apply(turn));
      const discard = button("Discard", "fc-chat-discard", () => this.#discard(turn));
      apply.disabled = discard.disabled = Boolean(turn.busy);
      li.append(make("div", { className: "fc-chat-acts" }, apply, discard));
    }
    if (turn.note) li.append(make("p", { className: "fc-chat-outcome", textContent: turn.note }));
    return li;
  }
}
