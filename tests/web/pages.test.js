// The page bar's fill-in: which boxes the AI is asked about, and how its answer is laid over edits made meanwhile.
import assert from "node:assert/strict";
import { test } from "node:test";

import { fillInBoxes, layFillIn } from "../../demo/web/workbench/pages.js";

const box = (id, extra = {}) => ({ id, type: "text", page: 0, rect: [0, 0, 10, 10], label: "", source: null, required: false, ...extra });
const name = { paths: ["name"] };

test("fillInBoxes is the page's boxes with no mapping or no label", () => {
  const t = { fields: [box("a", { label: "A", source: name }), box("b", { label: "B" }), box("c", { source: name }), box("d"), box("e", { page: 1 })] };
  assert.deepEqual(fillInBoxes(t, 0).map((f) => f.id), ["b", "c", "d"]);
  assert.deepEqual(fillInBoxes(t, 2), []);
  assert.deepEqual(fillInBoxes(null, 0), []);
});

test("layFillIn returns the result when nothing changed meanwhile", () => {
  const sent = { fields: [box("a")] };
  const result = { fields: [box("a", { source: name })] };
  assert.equal(layFillIn(sent, sent, result), result);
});

test("layFillIn keeps edits made meanwhile and adds only what a box still lacks", () => {
  const sent = { fields: [box("a"), box("b"), box("c")] };
  const result = {
    fields: [box("a", { label: "Name", source: name }), box("b", { label: "Date", source: { paths: ["date"] } }), box("c", { label: "City" }), box("tbl1", { type: "table" })],
  };
  // meanwhile the person mapped a, labelled b, and deleted c
  const current = { fields: [box("a", { source: { paths: ["other"] } }), box("b", { label: "Mine" })] };
  const out = layFillIn(current, sent, result);
  assert.deepEqual(
    out.fields.map((f) => [f.id, f.label, f.source?.paths ?? null]),
    [["a", "Name", ["other"]], ["b", "Mine", ["date"]], ["tbl1", "", null]],
  );
});
