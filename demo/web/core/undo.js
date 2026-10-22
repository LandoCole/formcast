// Undo and redo over whole-template snapshots. Templates are small JSON, so each state is a
// copy; nothing is ever edited in place. Pure: no browser globals.

/** A linear history of templates with a cursor. */
export class UndoStack {
  /**
   * @param {object} template the starting state
   */
  constructor(template) {
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
   * Record a new state after the cursor; any states that were undone are dropped.
   * @param {object} template
   */
  push(template) {
    this._states.length = this._index + 1;
    this._states.push(freeze(template));
    this._index += 1;
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
