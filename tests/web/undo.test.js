import assert from "node:assert/strict";
import { test } from "node:test";

import { UndoStack } from "../../demo/web/core/undo.js";

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
