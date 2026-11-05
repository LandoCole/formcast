// core/chat.js: what an exchange carries to the server as history.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_CHANGES, MAX_TURN_CHARS, exchange } from "../../demo/web/core/chat.js";

const turn = (changes, more = {}) => ({ message: "Add it.", reply: "Added.", changes, outcome: "applied", ...more });

test("an exchange's changes are one line; none says so", () => {
  assert.deepEqual(exchange(turn(["Add “Country”", "“Country”: read beside “Country”"])), {
    message: "Add it.",
    reply: "Added.",
    changes: "Add “Country”; “Country”: read beside “Country”",
    outcome: "applied",
  });
  assert.equal(exchange(turn([])).changes, "no changes");
});

test("an exchange carries its first MAX_CHANGES changes and how many more", () => {
  assert.equal(MAX_CHANGES, 10);
  const changes = Array.from({ length: 25 }, (_, n) => `change ${n + 1}`);
  const line = exchange(turn(changes)).changes;
  assert.equal(line, `${changes.slice(0, 10).join("; ")}; and 15 more`);
  assert.equal(exchange(turn(changes.slice(0, 10))).changes, changes.slice(0, 10).join("; ")); // ten: all of them
});

test("no part of an exchange is longer than the server takes", () => {
  assert.equal(MAX_TURN_CHARS, 2000);
  const long = "x".repeat(2500);
  const sent = exchange(turn([long], { message: long, reply: long }));
  for (const key of ["message", "reply", "changes"]) {
    assert.equal(sent[key].length, MAX_TURN_CHARS, key);
    assert.ok(sent[key].endsWith("…"), key);
  }
  assert.equal(exchange(turn([], { message: "x".repeat(2000) })).message, "x".repeat(2000)); // at the limit: whole
});
