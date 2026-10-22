// Undo and redo over whole-template snapshots. Templates are small JSON, so each state is a
// copy; nothing is ever edited in place. The stack keeps at most MAX_UNDO states (the current one
// included) and drops the oldest past that, so a long session can't grow memory without bound.
// Pure: no browser globals.

/** The most states an UndoStack keeps by default: 99 undo steps back from the current state. */
export const MAX_UNDO = 100;

/** A linear history of templates with a cursor. */
export class UndoStack {
  /**
   * @param {object} template the starting state
   * @param {{max?: number}} [options] `max`: the most states kept (default MAX_UNDO, at least 1)
   */
  constructor(template, { max = MAX_UNDO } = {}) {
    this._max = Math.max(1, Math.floor(max));
    this.clear(template);
  }

  /** The state the cursor is on. */
  get current() {
    return this._states[this._index];
  }

  /** True when `undo()` has a state to go back to. */
  get canUndo() {
    return this._index > 0;
  }

  /** True when `redo()` has a state to go forward to. */
  get canRedo() {
    return this._index < this._states.length - 1;
  }

  /**
   * Record a new state after the cursor; any states that were undone are dropped, and the oldest
   * states past the cap.
   * @param {object} template
   */
  push(template) {
    this._states.length = this._index + 1;
    this._states.push(freeze(template));
    if (this._states.length > this._max) this._states.splice(0, this._states.length - this._max);
    this._index = this._states.length - 1;
  }

  /**
   * Swap the current state for `template` without adding a step: undo still goes back to the
   * state before it. States that were undone are dropped, as with `push`.
   * @param {object} template
   */
  replace(template) {
    this._states.length = this._index + 1;
    this._states[this._index] = freeze(template);
  }

  /**
   * Step back.
   * @returns {object | null} the state now current, or null when already at the start
   */
  undo() {
    if (!this.canUndo) return null;
    this._index -= 1;
    return this.current;
  }

  /**
   * Step forward.
   * @returns {object | null} the state now current, or null when already at the end
   */
  redo() {
    if (!this.canRedo) return null;
    this._index += 1;
    return this.current;
  }

  /**
   * Forget all history and start again from `template`.
   * @param {object} template
   */
  clear(template) {
    this._states = [freeze(template)];
    this._index = 0;
  }
}

/** A private copy, so later changes to the caller's object can't alter a stored state. */
function freeze(template) {
  return Object.freeze(structuredClone(template));
}
