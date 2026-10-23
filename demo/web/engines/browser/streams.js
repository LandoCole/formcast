// Async iteration of a ReadableStream (`for await (const chunk of stream)`), which pdf.js uses to
// read a page's text. Safari 26.0 and older lack it, and pdf.js's legacy build does not add it, so
// every PDF failed to open there; this adds it where it is missing, following the Streams spec's
// `values()`, and leaves a native one alone. document.js imports it before pdf.js.

/**
 * Give `Stream.prototype` `values()` and `[Symbol.asyncIterator]` if it has no async iterator.
 * @param {{prototype: object} | undefined} Stream a ReadableStream class (the global one by default)
 * @returns {boolean} whether it was added
 */
export function addStreamIteration(Stream = globalThis.ReadableStream) {
  if (typeof Stream !== "function" || typeof Stream.prototype[Symbol.asyncIterator] === "function") return false;
  async function* values({ preventCancel = false } = {}) {
    const reader = this.getReader();
    let finished = false;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          finished = true;
          return;
        }
        yield value;
      }
    } finally {
      // left early (break, return or an error): cancel the stream, as the spec's iterator does
      if (!finished && !preventCancel) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  Object.defineProperty(Stream.prototype, "values", { value: values, writable: true, configurable: true });
  Object.defineProperty(Stream.prototype, Symbol.asyncIterator, { value: values, writable: true, configurable: true });
  return true;
}

addStreamIteration();
