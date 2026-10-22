// The pure parts of the panels: placement status (after coverage.py) and history summaries.
import assert from "node:assert/strict";
import { test } from "node:test";

import { summarizeOp } from "../../demo/web/core/history.js";
import { fieldWarnings, placementStatus, schemaLabel } from "../../demo/web/core/schemalist.js";
import { flattenSchema, schemaPathsOf } from "../../demo/web/core/schema.js";

const schema = flattenSchema({
  type: "object",
  properties: {
    name: { type: "string" },
    kind: { type: "string", enum: ["a", "b", "c"] },
    tags: { type: "array", items: { type: "string", enum: ["x", "y"] } },
    rows: { type: "array", items: { type: "object", properties: { n: { type: "string" } } } },
    who: { type: "object", properties: { id: { type: "string" } } },
  },
});
const field = (id, type, paths) => ({ id, type, source: paths ? { paths, transforms: [] } : null });

test("placed, not placed, partly placed, covered by a table, orphan", () => {
  const { status, orphans } = placementStatus(schema, {
    fields: [
      field("f1", "text", ["name"]),
      field("f2", "radio_option", ["kind"]),
      field("f3", "radio_option", ["kind"]),
      field("f4", "checkbox", ["tags"]),
      field("f5", "checkbox", ["tags"]),
      field("t", "table", ["rows"]),
      field("g", "text", ["gone.path"]),
      field("h", "text", null),
    ],
  });
  assert.equal(status.get("name"), "placed");
  assert.equal(status.get("kind"), "partly"); // 2 boxes for 3 options
  assert.equal(status.get("tags"), "placed"); // a box per option
  assert.equal(status.get("rows"), "placed");
  assert.equal(status.get("rows[].n"), "placed"); // covered by the table
  assert.equal(status.get("who.id"), "none");
  assert.equal(status.has("who"), false); // objects have no status
  assert.deepEqual(orphans, [{ path: "gone.path", fields: ["g"] }]);
});

test("an enum shown in a text box is placed, not partly", () => {
  const { status } = placementStatus(schema, { fields: [field("f", "text", ["kind"])] });
  assert.equal(status.get("kind"), "placed");
});

test("summarizeOp names the op and the field by label, falling back to its id", () => {
  const fields = [{ id: "f1", label: "Date of birth" }, { id: "f2", label: "" }];
  assert.equal(summarizeOp({ op: { op: "move", id: "f1", dx: 2 } }, fields), "move · Date of birth");
  assert.equal(summarizeOp({ op: { op: "set_source", id: "f2" } }, fields), "set source · f2");
  assert.equal(summarizeOp({ op: { op: "remove_field", id: "gone" } }, fields), "remove field · gone");
  assert.equal(summarizeOp({ op: { op: "add_table", source_path: "rows" } }, fields), "add table · rows");
});

test("schemaPathsOf flattens once per schema object and gives [] for none or a broken one", () => {
  const raw = { type: "object", properties: { a: { type: "string" } } };
  const first = schemaPathsOf(raw);
  assert.deepEqual(first.map((sp) => sp.path), ["a"]);
  assert.equal(schemaPathsOf(raw), first); // the same array: not flattened again
  assert.deepEqual(schemaPathsOf(null), []);
  assert.deepEqual(schemaPathsOf(undefined), []);
  assert.deepEqual(schemaPathsOf({ type: "string" }), []); // the root is not an object schema
});

test("fieldWarnings puts the edit's warnings first, then paths the schema lacks; quiet fields are left out", () => {
  const template = { fields: [field("f1", "text", ["name", "gone"]), field("f2", "text", ["kind"]), field("f3", "text", null)] };
  const out = fieldWarnings(template, schema, { f1: ["too long"] });
  assert.deepEqual(out, { f1: ["too long", "gone is not in the schema"] });
  assert.deepEqual(fieldWarnings(template, schema), { f1: ["gone is not in the schema"] });
});

test("schemaLabel counts placed paths and names the schema's title when it has one", () => {
  const template = { schema: { title: "Vendor" }, fields: [field("f1", "text", ["name"])] };
  const { status } = placementStatus(schema, template);
  assert.equal(schemaLabel(schema, template), `Schema · Vendor · 1 of ${status.size} placed`);
  assert.equal(schemaLabel(schema, { schema: {}, fields: [] }), `Schema · 0 of ${status.size} placed`);
});
