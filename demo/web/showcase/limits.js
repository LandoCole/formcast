// Time limits for the showcase's slow steps: fetching a sample's files, opening a visitor's PDF,
// filling. Each fails with a plain message instead of leaving a spinner up.

/** The error a time limit fails with; its message is plain text for the visitor. */
export class TimeLimitError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = "TimeLimitError";
  }
}

/** "N seconds" for a limit in ms. */
export function seconds(ms) {
  const n = Math.max(1, Math.round(ms / 1000));
  return `${n} second${n === 1 ? "" : "s"}`;
}

/**
 * Wait for `work` at most `ms` milliseconds. Past that, reject with a TimeLimitError carrying
 * `message`; `work` is not stopped, and if it finishes later its value goes to `onLate` (to close
 * a document nobody will show, say) and is otherwise dropped.
 * @template T
 * @param {Promise<T>} work
 * @param {number} ms
 * @param {string} message
 * @param {{onLate?: (value: T) => void}} [opts]
 * @returns {Promise<T>}
 */
export function withinTime(work, ms, message, { onLate } = {}) {
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new TimeLimitError(message)), ms);
  });
  return Promise.race([work, limit]).then(
    (value) => {
      clearTimeout(timer);
      return value;
    },
    (e) => {
      clearTimeout(timer);
      if (e instanceof TimeLimitError && onLate) work.then(onLate, () => {});
      throw e;
    },
  );
}

/**
 * Fetch `url` and read its body with `read`, failing with a plain message on an HTTP error or
 * when the whole request takes longer than `ms`.
 * @template T
 * @param {URL} url
 * @param {number} ms
 * @param {(res: Response) => Promise<T>} read
 * @returns {Promise<T>}
 */
export async function fetchWithin(url, ms, read) {
  const name = url.pathname.split("/").pop();
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(ms) });
    if (!res.ok) throw new Error(`${name} could not be loaded (HTTP ${res.status}).`);
    return await read(res);
  } catch (e) {
    if (e?.name === "TimeoutError" || e?.name === "AbortError") {
      throw new TimeLimitError(`${name} took more than ${seconds(ms)} to load. Check the connection and try again.`);
    }
    throw e;
  }
}
