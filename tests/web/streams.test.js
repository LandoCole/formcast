// engines/browser/streams.js: async iteration of a ReadableStream where the browser lacks it.
import assert from "node:assert/strict";
import { test } from "node:test";

import { addStreamIteration } from "../../demo/web/engines/browser/streams.js";

/** A stream class with a reader and no async iterator, as Safari 26.0's ReadableStream. */
function plainStream() {
  const log = [];
  class Plain {
    constructor(chunks) {
      this.chunks = [...chunks];
    }
    getReader() {
      log.push("getReader");
      return {
        read: async () => (this.chunks.length ? { done: false, value: this.chunks.shift() } : { done: true, value: undefined }),
        cancel: async () => log.push("cancel"),
        releaseLock: () => log.push("releaseLock"),
      };
    }
  }
  return { Plain, log };
}

test("a stream without an async iterator gets one that reads every chunk", async () => {
  const { Plain, log } = plainStream();
  assert.equal(addStreamIteration(Plain), true);
  const seen = [];
  for await (const chunk of new Plain(["a", "b", "c"])) seen.push(chunk);
  assert.deepEqual(seen, ["a", "b", "c"]);
  assert.deepEqual(log, ["getReader", "releaseLock"]); // read to the end: nothing to cancel
});

test("leaving the loop early cancels the stream unless preventCancel is set", async () => {
  const { Plain, log } = plainStream();
  addStreamIteration(Plain);
  for await (const chunk of new Plain(["a", "b"])) {
    assert.equal(chunk, "a");
    break;
  }
  assert.deepEqual(log, ["getReader", "cancel", "releaseLock"]);
  log.length = 0;
  for await (const chunk of new Plain(["a", "b"]).values({ preventCancel: true })) {
    assert.equal(chunk, "a");
    break;
  }
  assert.deepEqual(log, ["getReader", "releaseLock"]);
});

test("a native async iterator is left alone", () => {
  const native = ReadableStream.prototype[Symbol.asyncIterator];
  assert.equal(typeof native, "function");
  assert.equal(addStreamIteration(ReadableStream), false);
  assert.equal(ReadableStream.prototype[Symbol.asyncIterator], native);
  assert.equal(addStreamIteration(undefined), false);
});
