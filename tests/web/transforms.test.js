// Holds transforms.js to the answers recorded from Python (fixtures/transforms.json).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { TransformError, applyChain, listTransforms, validateChain } from "../../demo/web/core/transforms.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/transforms.json", import.meta.url), "utf8"));

test("fixture has cases for every transform", () => {
  const ops = new Set(fixture.cases.flatMap((c) => c.input.chain.map((s) => s.op)));
  for (const t of fixture.catalogue) assert.ok(ops.has(t.name), `no case uses ${t.name}`);
  assert.ok(fixture.cases.length > 400);
});

test("listTransforms matches Python's catalogue (names, order, schemas, docs)", () => {
  assert.deepEqual(listTransforms(), fixture.catalogue);
});

for (const c of fixture.cases) {
  test(`${c.kind} ${c.id}`, () => {
    if (c.kind === "validate") {
      assert.deepEqual(validateChain(c.input.chain), c.output);
      return;
    }
    if ("error" in c) {
      assert.throws(
        () => applyChain(c.input.values, c.input.chain),
        (e) => e instanceof TransformError && e.message === c.error,
      );
    } else {
      assert.deepEqual(applyChain(c.input.values, c.input.chain), c.output);
    }
  });
}

test("applyChain does not mutate its input", () => {
  const values = [["b", "a"]];
  applyChain(values, [{ op: "slice", args: { start: 0, end: 1 } }]);
  assert.deepEqual(values, [["b", "a"]]);
});

test("listTransforms returns copies", () => {
  const listing = listTransforms();
  listing[0].args.properties = "mangled";
  assert.notEqual(listTransforms()[0].args.properties, "mangled");
});
