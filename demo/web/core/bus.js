// A tiny event bus: the shared parts talk through it and never reach into each other's DOM.

/**
 * @typedef {{on: (type: string, fn: (detail: any) => void) => () => void,
 *            emit: (type: string, detail?: any) => void}} Bus
 */

/**
 * A new event bus. `on` returns a function that removes the listener. A listener that throws
 * does not stop the others; its error is rethrown asynchronously so it still reaches the
 * page's error handler.
 * @returns {Bus}
 */
export function createBus() {
  /** @type {Map<string, Set<(detail: any) => void>>} */
  const listeners = new Map();
  return {
    on(type, fn) {
      let set = listeners.get(type);
      if (set === undefined) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(fn);
      return () => {
        set.delete(fn);
      };
    },
    emit(type, detail) {
      const set = listeners.get(type);
      if (set === undefined) return;
      for (const fn of [...set]) {
        try {
          fn(detail);
        } catch (error) {
          queueMicrotask(() => {
            throw error;
          });
        }
      }
    },
  };
}
