// showcase/limits.js: time limits with plain messages.
import assert from "node:assert/strict";
import { test } from "node:test";

import { TimeLimitError, seconds, withinTime } from "../../demo/web/showcase/limits.js";

const later = (ms, value) => new Promise((resolve) => setTimeout(() => resolve(value), ms));

test("withinTime passes a result or an error through when the work is in time", async () => {
  assert.equal(await withinTime(later(5, "done"), 200, "too slow"), "done");
  await assert.rejects(withinTime(Promise.reject(new Error("broken")), 200, "too slow"), { message: "broken" });
});

test("withinTime fails with the plain message past the limit, and hands a late result to onLate", async () => {
  const late = [];
  const work = later(60, { doc: "d" });
  await assert.rejects(withinTime(work, 10, "Filling took more than 1 second. Try again.", { onLate: (v) => late.push(v) }), (e) => {
    assert.ok(e instanceof TimeLimitError);
    assert.equal(e.name, "TimeLimitError");
    assert.equal(e.message, "Filling took more than 1 second. Try again.");
    return true;
  });
  assert.deepEqual(late, []);
  await work;
  await later(0);
  assert.deepEqual(late, [{ doc: "d" }]);
  // work that never settles just stays pending; the limit still fires
  await assert.rejects(withinTime(new Promise(() => {}), 10, "hung"), { message: "hung" });
});

test("seconds", () => {
  assert.equal(seconds(500), "1 second");
  assert.equal(seconds(30_000), "30 seconds");
});
