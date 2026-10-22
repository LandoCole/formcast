import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_UNDO, UndoStack } from "../../demo/web/core/undo.js";

const t = (name) => ({ name, fields: [] });

test("push, undo and redo walk one line of history", () => {
  const u = new UndoStack(t("a"));
  assert.equal(u.canUndo, false);
  assert.equal(u.canRedo, false);
  u.push(t("b"));
  u.push(t("c"));
  assert.equal(u.current.name, "c");
  assert.equal(u.undo().name, "b");
  assert.equal(u.current.name, "b");
  assert.equal(u.canRedo, true);
  assert.equal(u.canUndo, true);
  assert.equal(u.redo().name, "c");
  assert.equal(u.canRedo, false);
  assert.equal(u.redo(), null);
});

test("pushing after an undo drops the redo branch", () => {
  const u = new UndoStack(t("a"));
  u.push(t("b"));
  u.push(t("c"));
  u.undo();
  u.push(t("d"));
  assert.equal(u.canRedo, false);
  assert.equal(u.current.name, "d");
  assert.equal(u.undo().name, "b");
  assert.equal(u.undo().name, "a");
  assert.equal(u.canUndo, false);
  assert.equal(u.undo(), null);
  assert.equal(u.current.name, "a");
});

test("clear starts again from the given template", () => {
  const u = new UndoStack(t("a"));
  u.push(t("b"));
  u.undo();
  u.clear(t("z"));
  assert.equal(u.current.name, "z");
  assert.equal(u.canUndo, false);
  assert.equal(u.canRedo, false);
});

test("stored states are private copies and cannot be changed", () => {
  const original = { name: "a", fields: [{ id: "x" }] };
  const u = new UndoStack(original);
  original.fields.push({ id: "y" }); // the caller's later edit does not reach the stack
  assert.equal(u.current.fields.length, 1);
  assert.throws(() => {
    "use strict";
    u.current.name = "changed";
  }, TypeError);
  u.push({ name: "b", fields: [] });
  assert.equal(u.undo().name, "a");
});

test("the stack keeps at most MAX_UNDO states, dropping the oldest", () => {
  assert.equal(MAX_UNDO, 100);
  const u = new UndoStack(t("s0"));
  for (let i = 1; i <= 150; i++) u.push(t(`s${i}`));
  assert.equal(u.current.name, "s150");
  let steps = 0;
  while (u.undo()) steps += 1;
  assert.equal(steps, MAX_UNDO - 1); // 100 states kept: s51 .. s150
  assert.equal(u.current.name, "s51");
  while (u.redo());
  assert.equal(u.current.name, "s150");
  // a smaller cap from the constructor; a push after undoing still drops the redo branch first
  const small = new UndoStack(t("a"), { max: 3 });
  for (const n of ["b", "c", "d"]) small.push(t(n));
  small.undo();
  small.push(t("e"));
  assert.deepEqual([small.undo()?.name, small.undo()?.name, small.undo()], ["c", "b", null]);
});
