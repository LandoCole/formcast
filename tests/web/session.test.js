// core/session.js: the ordered op queue, undo and redo behind it, replaceTemplate and events.
import assert from "node:assert/strict";
import { test } from "node:test";

import { createBus } from "../../demo/web/core/bus.js";
import { createSession } from "../../demo/web/core/session.js";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const blank = () => ({ name: "t", fields: [], history: [] });
const ids = (t) => t.fields.map((f) => f.id);

/**
 * An engine whose `apply` answers after `op.delay` ms: `{op: "add", id}` appends a field,
 * `{op: "fail"}` is refused, `{op: "boom"}` throws. `calls` records the base each call started from.
 */
function fakeEngine() {
  const calls = [];
  return {
    calls,
    async apply(template, op, author, opts) {
      calls.push({ base: ids(template), op, author, opts });
      await wait(op.delay ?? 0);
      if (op.op === "fail") return { error: `refused ${op.id}` };
      if (op.op === "boom") throw new Error("engine down");
      const t = structuredClone(template);
      t.fields.push({ id: op.id });
      return { template: t, warnings: op.warn ?? [] };
    },
  };
}

function setup() {
  const bus = createBus();
  const engine = fakeEngine();
  const session = createSession({ engine, bus });
  const events = [];
  bus.on("template", (e) => events.push(["template", ids(e.template), e.reason]));
  bus.on("error", (e) => events.push(["error", e.message]));
  const doc = { destroyed: 0, async destroy() { this.destroyed += 1; } };
  session.setDocument(doc, blank(), { source: { kind: "x", text: "start" } });
  return { session, engine, events, doc, bus };
}

test("two applies issued without awaiting land in order, the second on the first's template", async () => {
  const { session, engine } = setup();
  const first = session.apply({ op: "add", id: "a", delay: 50 });
  const second = session.apply({ op: "add", id: "b", delay: 5 });
  const [r1, r2] = await Promise.all([first, second]);
  assert.deepEqual(ids(session.template), ["a", "b"]);
  assert.deepEqual(ids(r1.template), ["a"]);
  assert.deepEqual(ids(r2.template), ["a", "b"]);
  assert.deepEqual(engine.calls.map((c) => c.base), [[], ["a"]]);
});

test("a refused op leaves the template alone, emits error once, and later ops still apply", async () => {
  const { session, events } = setup();
  const results = await Promise.all([
    session.apply({ op: "add", id: "a", delay: 10 }),
    session.apply({ op: "fail", id: "x" }),
    session.apply({ op: "add", id: "b" }),
  ]);
  assert.deepEqual(ids(session.template), ["a", "b"]);
  assert.deepEqual(results[1], { error: "refused x" });
  assert.deepEqual(events.filter((e) => e[0] === "error"), [["error", "refused x"]]);
  assert.equal(session.canUndo, true);
  await session.undo();
  assert.deepEqual(ids(session.template), ["a"]); // two edits landed, not three
});

test("an engine that throws is an error result, not a rejection", async () => {
  const { session, events } = setup();
  const res = await session.apply({ op: "boom" });
  assert.deepEqual(res, { error: "engine down" });
  assert.deepEqual(events, [["error", "engine down"]]);
  assert.deepEqual(ids(session.template), []);
});

test("undo issued while an op is in flight undoes that op after it lands", async () => {
  const { session } = setup();
  const applied = session.apply({ op: "add", id: "a", delay: 30 });
  const undone = session.undo();
  assert.equal(await undone, true);
  await applied;
  assert.deepEqual(ids(session.template), []);
  assert.equal(session.canRedo, true);
  assert.equal(await session.redo(), true);
  assert.deepEqual(ids(session.template), ["a"]);
  assert.equal(await session.redo(), false);
});

test("undo with nothing to undo resolves false and emits nothing", async () => {
  const { session, events } = setup();
  assert.equal(await session.undo(), false);
  assert.deepEqual(events, []);
});

test("replaceTemplate pushes one undo snapshot and keeps the source unless given a new one", async () => {
  const { session, events } = setup();
  await session.apply({ op: "add", id: "a" });
  const { template } = await session.replaceTemplate({ ...blank(), fields: [{ id: "n1" }, { id: "n2" }] });
  assert.deepEqual(ids(template), ["n1", "n2"]);
  assert.equal(session.source.text, "start");
  await session.replaceTemplate({ ...blank(), fields: [{ id: "z" }] }, { source: { kind: "live", text: "Detected" } });
  assert.equal(session.source.text, "Detected");
  await session.undo();
  assert.deepEqual(ids(session.template), ["n1", "n2"]);
  assert.equal(session.source.text, "start");
  await session.undo();
  assert.deepEqual(ids(session.template), ["a"]);
  assert.deepEqual(events.map((e) => e[2]), ["apply", "replace", "replace", "undo", "undo"]);
});

test("replaceTemplate with pushUndo false swaps the state in place", async () => {
  const { session } = setup();
  await session.apply({ op: "add", id: "a" });
  await session.replaceTemplate({ ...blank(), fields: [{ id: "p" }] }, { pushUndo: false });
  assert.deepEqual(ids(session.template), ["p"]);
  await session.undo();
  assert.deepEqual(ids(session.template), []); // straight back past the replaced state
});

test("replaceTemplate waits for the ops ahead of it", async () => {
  const { session } = setup();
  const applied = session.apply({ op: "add", id: "a", delay: 30 });
  const replaced = session.replaceTemplate({ ...blank(), fields: [{ id: "r" }] });
  await Promise.all([applied, replaced]);
  assert.deepEqual(ids(session.template), ["r"]); // the replacement was not overwritten by the late op
  await session.undo();
  assert.deepEqual(ids(session.template), ["a"]);
});

test("events carry the template, the field warnings and the added id", async () => {
  const { session, bus } = setup();
  const seen = [];
  bus.on("template", (e) => seen.push(e));
  await session.apply({ op: "add", id: "a", warn: ["tight"] });
  assert.equal(seen[0].added, "a");
  assert.deepEqual(seen[0].warnings, ["tight"]);
  assert.deepEqual(seen[0].fieldWarnings, { a: ["tight"] });
  assert.equal(seen[0].source.text, "start");
  await session.apply({ op: "add", id: "b" });
  assert.deepEqual(session.fieldWarnings, {});
});

test("the engine gets the author, the note and the open document", async () => {
  const { session, engine, doc } = setup();
  await session.apply({ op: "add", id: "a" }, { note: "why" });
  await session.apply({ op: "add", id: "b" }, { author: "agent" });
  assert.equal(engine.calls[0].author, "human");
  assert.equal(engine.calls[0].opts.doc, doc);
  assert.equal(engine.calls[0].opts.note, "why");
  assert.equal(engine.calls[1].author, "agent");
});

test("a new document drops queued and running ops without touching its template", async () => {
  const { session, events, doc } = setup();
  const running = session.apply({ op: "add", id: "a", delay: 30 });
  const queued = session.apply({ op: "add", id: "b" });
  const generation = session.generation;
  const next = { destroyed: 0, async destroy() {} };
  session.setDocument(next, { ...blank(), fields: [{ id: "n" }] });
  assert.equal(session.generation, generation + 1);
  assert.equal(doc.destroyed, 1);
  const [r1, r2] = await Promise.all([running, queued]);
  assert.equal(r1.stale, true);
  assert.equal(r2.stale, true);
  assert.deepEqual(ids(session.template), ["n"]);
  assert.equal(session.canUndo, false);
  assert.deepEqual(events, []);
});

test("idle resolves once the queue has drained", async () => {
  const { session } = setup();
  session.apply({ op: "add", id: "a", delay: 20 });
  session.apply({ op: "add", id: "b", delay: 5 });
  await session.idle();
  assert.deepEqual(ids(session.template), ["a", "b"]);
});

test("nothing is open before setDocument", async () => {
  const session = createSession({ engine: fakeEngine(), bus: createBus() });
  assert.equal(session.template, null);
  assert.equal(session.canUndo, false);
  assert.deepEqual(await session.apply({ op: "add", id: "a" }), { error: "No form is open." });
  assert.equal((await session.replaceTemplate(blank())).stale, true);
  assert.equal(await session.undo(), false);
});

test("the undo stack is capped", async () => {
  const bus = createBus();
  const session = createSession({ engine: fakeEngine(), bus, max: 3 });
  session.setDocument({ destroy: async () => {} }, blank());
  for (const id of ["a", "b", "c", "d"]) await session.apply({ op: "add", id });
  assert.equal(await session.undo(), true);
  assert.equal(await session.undo(), true);
  assert.equal(await session.undo(), false);
  assert.deepEqual(ids(session.template), ["a", "b"]); // the oldest state kept: three states back from d
});
