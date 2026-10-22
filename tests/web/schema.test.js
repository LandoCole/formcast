// Holds schema.js to the answers recorded from Python (fixtures/schema.json).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ARRAY_ITEM,
  PathError,
  SchemaError,
  flattenSchema,
  formatPath,
  isArrayItem,
  parsePath,
} from "../../demo/web/core/schema.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/schema.json", import.meta.url), "utf8"));

// the fixture writes the "[]" marker as {"array_item": true}
const isMarker = (s) => typeof s === "object" && s !== null && s.array_item === true;
const fromFixture = (segments) => segments.map((s) => (isMarker(s) ? ARRAY_ITEM : s));

test("fixture covers each kind and the invalid schemas", () => {
  const kinds = new Set(fixture.cases.map((c) => c.kind));
  assert.deepEqual([...kinds].sort(), ["flatten", "format_path", "parse_path"]);
  assert.ok(fixture.cases.filter((c) => c.kind === "flatten" && "error" in c).length >= 10);
  assert.ok(fixture.cases.some((c) => c.id.startsWith("flatten/dev-form/")));
});

for (const c of fixture.cases) {
  test(`${c.kind} ${c.id}`, () => {
    if (c.kind === "flatten") {
      if ("error" in c) {
        assert.throws(
          () => flattenSchema(c.input.schema),
          (e) => e instanceof SchemaError && e.message === c.error,
        );
      } else {
        assert.deepEqual(flattenSchema(c.input.schema), c.output);
      }
    } else if (c.kind === "parse_path") {
      if ("error" in c) {
        assert.throws(
          () => parsePath(c.input.path),
          (e) => e instanceof PathError && e.message === c.error,
        );
      } else {
        assert.deepEqual(parsePath(c.input.path), fromFixture(c.output));
      }
    } else {
      assert.equal(formatPath(fromFixture(c.input.segments)), c.output);
    }
  });
}

test("a property named like the array marker is distinguishable from it", () => {
  const parsed = parsePath('a["[]"]');
  assert.notEqual(parsed[1], ARRAY_ITEM);
  assert.equal(isArrayItem(parsePath("a[]")[1]), true);
  assert.equal(isArrayItem(parsed[1]), false);
  assert.equal(formatPath(["a", ARRAY_ITEM]), "a[]");
});

test("flattenSchema does not change the schema it is given", () => {
  const schema = {
    type: "object",
    $defs: { A: { type: "object", properties: { q: { type: "string" } } } },
    properties: { p: { allOf: [{ $ref: "#/$defs/A" }, { properties: { r: { type: "integer" } } }] } },
  };
  const before = JSON.stringify(schema);
  flattenSchema(schema);
  assert.equal(JSON.stringify(schema), before);
});
