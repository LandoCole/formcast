// serial.js: the detection worker's queue, so two model runs never overlap.
import assert from "node:assert/strict";
import { test } from "node:test";

import { serial } from "../../demo/web/engines/browser/serial.js";

/** A promise with its resolve and reject, to finish work when the test says so. */
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("calls run one at a time, in the order they were made", async () => {
  const log = [];
  const gates = [deferred(), deferred(), deferred()];
  let running = 0;
  let most = 0;
  const run = serial(async (i) => {
    running += 1;
    most = Math.max(most, running);
    log.push(`start ${i}`);
    await gates[i].promise;
    log.push(`end ${i}`);
    running -= 1;
    return i * 10;
  });
  const results = [run(0), run(1), run(2)];
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(log, ["start 0"]); // 1 and 2 wait although 0 is still running
  gates[1].resolve(); // finishing out of order changes nothing
  gates[0].resolve();
  gates[2].resolve();
  assert.deepEqual(await Promise.all(results), [0, 10, 20]);
  assert.deepEqual(log, ["start 0", "end 0", "start 1", "end 1", "start 2", "end 2"]);
  assert.equal(most, 1);
});

test("a failed call rejects on its own and the next call still runs", async () => {
  const run = serial(async (x) => {
    if (x === "bad") throw new Error("no");
    return x;
  });
  const bad = run("bad");
  const good = run("good");
  await assert.rejects(bad, /no/);
  assert.equal(await good, "good");
  // a function that throws synchronously is handled the same way
  const sync = serial((x) => {
    if (x) throw new Error("sync");
    return "ok";
  });
  await assert.rejects(sync(true), /sync/);
  assert.equal(await sync(false), "ok");
});
