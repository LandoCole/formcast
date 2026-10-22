// One-at-a-time calls of an async function. The detection worker handles its messages through it,
// because onnxruntime-web does not support two `session.run` calls on one session at once (a page
// whose request timed out on the main thread may still be running when the next one arrives).

/**
 * Wrap `fn` so each call starts only after the previous call has settled (resolved or rejected),
 * in the order the calls were made. Each call gets its own result or error.
 * @template {unknown[]} A
 * @template R
 * @param {(...args: A) => Promise<R> | R} fn
 * @returns {(...args: A) => Promise<R>}
 */
export function serial(fn) {
  let last = Promise.resolve();
  return (...args) => {
    const result = last.then(() => fn(...args));
    last = result.then(
      () => {},
      () => {},
    );
    return result;
  };
}
