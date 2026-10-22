// The session controller: the open document, the template on screen, its undo history and the
// ordered queue every change goes through. Both layouts (the showcase and the workbench) use it, so
// none of this lives in a page script.
//
// Ordering: edits are queued and run one at a time, each against the template the one before it
// produced. An engine may take a while (the workbench's server engine does a round trip), so two
// edits issued without waiting never start from the same base. An edit the engine refuses leaves the
// template as it was and the edits behind it still run. Undo, redo and replaceTemplate are queued
// too, so they wait for the edits ahead of them instead of cutting in. Nothing is cancelled.
//
// A new document (`setDocument`) drops whatever is queued or still running for the old one: those
// calls resolve with `{error, stale: true}` and emit nothing.
//
// Events on the bus:
//   template {template, warnings, fieldWarnings, source, added, reason}  after the template changed
//       (reason: "apply" | "undo" | "redo" | "replace"; `warnings` are the engine's messages for an
//       edit, `fieldWarnings` maps a field id to its messages; `added` is the id of a field an edit
//       created, else null). `setDocument` emits nothing: its caller renders.
//   error {message}  once per edit the engine refused

import { UndoStack } from "./undo.js";

/**
 * @typedef {import("./bus.js").Bus} Bus
 * @typedef {{kind: string, text: string}} Source what a template's boxes are, for a caption
 * @typedef {{template: object, warnings: string[], added: string | null} | {error: string, stale?: boolean}} ApplyResult
 */

const STALE = "The form changed before this finished.";

/**
 * Create a session.
 * @param {{engine: {apply: Function}, bus: Bus, max?: number}} options `engine` is any engine
 *   (core/engine.js); `max` caps the undo stack (default: the UndoStack's)
 */
export function createSession({ engine, bus, max }) {
  let doc = null;
  let undo = null;
  let generation = 0;
  let fieldWarnings = {};
  let tail = Promise.resolve();
  const sources = new WeakMap(); // template snapshot -> its Source

  const stale = () => ({ error: STALE, stale: true });

  /** Run `job` after everything queued before it; dropped when the document changes first. */
  function enqueue(job, onStale) {
    const mine = generation;
    const run = tail.then(() => (mine !== generation ? onStale() : job(mine)));
    tail = run.then(() => {}, () => {});
    return run;
  }

  /** Announce the template on screen. */
  function announce(reason, extra = {}) {
    bus.emit("template", {
      template: undo.current,
      warnings: [],
      fieldWarnings,
      source: sources.get(undo.current) ?? null,
      added: null,
      reason,
      ...extra,
    });
  }

  const session = {
    /** The open document (an engine's doc), or null. */
    get doc() {
      return doc;
    },

    /** The template on screen, or null before a document is open. */
    get template() {
      return undo?.current ?? null;
    },

    /** What the boxes of the template on screen are ({kind, text}), or null. */
    get source() {
      return undo ? (sources.get(undo.current) ?? null) : null;
    },

    /** Warnings from the last edit, by field id. */
    get fieldWarnings() {
      return fieldWarnings;
    },

    /** Counts up whenever the document changes: a late result compares it to the one it started with. */
    get generation() {
      return generation;
    },

    get canUndo() {
      return undo?.canUndo ?? false;
    },

    get canRedo() {
      return undo?.canRedo ?? false;
    },

    /**
     * Show a new document with its starting template; the undo history starts over, queued and
     * running edits for the old document are dropped, and the old document is destroyed.
     * @param {object} newDoc
     * @param {object} template
     * @param {{source?: Source}} [options]
     */
    setDocument(newDoc, template, { source } = {}) {
      if (doc && doc !== newDoc) doc.destroy?.().catch?.(() => {});
      generation += 1;
      doc = newDoc;
      undo = new UndoStack(template, { max });
      if (source) sources.set(undo.current, source);
      fieldWarnings = {};
    },

    /**
     * Apply one edit. Resolves when the engine has answered and the template is updated.
     * @param {object} op
     * @param {{author?: string, note?: string}} [options]
     * @returns {Promise<ApplyResult>}
     */
    apply(op, { author = "human", note } = {}) {
      if (undo === null) return Promise.resolve({ error: "No form is open." });
      return enqueue(async (mine) => {
        const base = undo.current;
        let res;
        try {
          res = await engine.apply(base, op, author, { doc, note });
        } catch (e) {
          res = { error: String(e?.message ?? e) };
        }
        if (mine !== generation) return stale();
        if (res.error !== undefined) {
          bus.emit("error", { message: res.error });
          return { error: res.error };
        }
        const ids = new Set(base.fields.map((f) => f.id));
        const added = res.template.fields.find((f) => !ids.has(f.id))?.id ?? null;
        const touched = op.op === "remove_field" ? null : (added ?? op.id ?? null);
        fieldWarnings = touched && res.warnings.length ? { [touched]: res.warnings } : {};
        undo.push(res.template);
        const prior = sources.get(base);
        if (prior) sources.set(undo.current, prior);
        announce("apply", { warnings: res.warnings, added });
        return { template: undo.current, warnings: res.warnings, added };
      }, stale);
    },

    /**
     * Step back once the edits ahead of this call have landed.
     * @returns {Promise<boolean>} false when there was nothing to undo
     */
    undo() {
      return enqueue(() => step("undo"), () => false);
    },

    /**
     * Step forward once the edits ahead of this call have landed.
     * @returns {Promise<boolean>} false when there was nothing to redo
     */
    redo() {
      return enqueue(() => step("redo"), () => false);
    },

    /**
     * Put a whole new template on screen (detector boxes, a loaded template, an AI job's result),
     * after the edits ahead of it. One undo step by default.
     * @param {object} template
     * @param {{source?: Source, pushUndo?: boolean}} [options] `source` replaces the caption's
     *   description of the boxes (the old one stays when omitted); `pushUndo: false` swaps the
     *   current state in place, with no step to undo
     * @returns {Promise<{template: object} | {error: string, stale: true}>}
     */
    replaceTemplate(template, { source, pushUndo = true } = {}) {
      if (undo === null) return Promise.resolve({ error: "No form is open.", stale: true });
      return enqueue(() => {
        const before = undo.current;
        if (pushUndo) undo.push(template);
        else undo.replace(template);
        const kept = source ?? sources.get(before);
        if (kept) sources.set(undo.current, kept);
        fieldWarnings = {};
        announce("replace");
        return { template: undo.current };
      }, stale);
    },

    /** Resolves once nothing is queued or running. */
    async idle() {
      let seen;
      do {
        seen = tail;
        await seen;
      } while (seen !== tail);
    },
  };

  function step(which) {
    if (undo === null || undo[which]() === null) return false;
    fieldWarnings = {};
    announce(which);
    return true;
  }

  return session;
}
