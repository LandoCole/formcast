// The pure parts of the panels: placement status (after coverage.py) and history summaries.
import assert from "node:assert/strict";
import { test } from "node:test";

import { summarizeOp } from "../../demo/web/core/history.js";
import { placementStatus } from "../../demo/web/core/schemalist.js";
import { flattenSchema } from "../../demo/web/core/schema.js";

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
