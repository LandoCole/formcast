// Holds ops.js to the answers recorded from Python (fixtures/ops.json), and checks the pieces
// that have no Python counterpart: browser-only refusals and the Python-compat helpers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { BROWSER_OPS, OP_NAMES, OpError, applyOp, contextFromTemplate, newFieldId } from "../../demo/web/core/ops.js";
import { containedFraction } from "../../demo/web/core/geometry.js";
import { getCloseMatches, pyFixed, pyFloorDiv, pyFormatG, pyRound1 } from "../../demo/web/core/pycompat.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/ops.json", import.meta.url), "utf8"));

/** Apply a case's ops in order, as the exporter did (with the open PDF's widget kinds when the case has them). */
function run(c) {
  let template = structuredClone(fixture.templates[c.input.template]);
  let warnings = [];
  for (const op of c.input.ops) {
    const context = "widget_kinds" in c.input
      ? contextFromTemplate(template, { widgetKinds: c.input.widget_kinds })
      : undefined;
    ({ template, warnings } = applyOp(template, op, { author: c.input.author, note: c.input.note, at: fixture.at, context }));
  }
  return { template, warnings };
}

test("fixture covers set_type against widget kinds, allowed and refused", () => {
  const cases = fixture.cases.filter((c) => "widget_kinds" in c.input);
  assert.ok(cases.some((c) => "output" in c));
  assert.ok(cases.some((c) => "error" in c && c.error.includes("allowed types: none")));
  assert.ok(cases.some((c) => "error" in c && c.error.includes("allowed types: ['")));
});

test("without widget kinds any type may use a widget, as Python without a form map", () => {
  const t = structuredClone(fixture.templates.plain);
  const { template } = applyOp(t, { op: "set_type", id: "w1", type: "checkbox" }, { author: "human", at: fixture.at });
  assert.equal(template.fields.find((f) => f.id === "w1").type, "checkbox");
  assert.equal(contextFromTemplate(t).widgetKinds, null);
});

test("fixture covers every browser op, both outcomes, and templates with tables", () => {
  const ids = fixture.cases.map((c) => c.id);
  for (const name of ["add", "remove", "move", "resize", "label", "type", "source", "style", "required"]) {
    assert.ok(ids.some((id) => id.includes(`/${name}/`) && !id.includes("/err-")), `a valid ${name} case`);
    assert.ok(ids.some((id) => id.includes(`/${name}/err-`)), `an invalid ${name} case`);
  }
  assert.ok(fixture.cases.some((c) => c.id.startsWith("table/")));
  assert.ok(fixture.cases.some((c) => c.output?.warnings.length));
  assert.ok(fixture.cases.filter((c) => "error" in c).length >= 100);
});

test("a new field is optional until someone sets it; set_required records the person", () => {
  const added = fixture.cases.find((c) => c.id === "plain/add/basic").output.fields.at(-1);
  assert.deepEqual([added.required, added.required_reason, added.required_by], [false, "", null]);
  const set = fixture.cases.find((c) => c.id === "plain/required/set").output.fields[0];
  assert.deepEqual([set.required, set.required_reason, set.required_by], [true, "Set by you", "human"]);
  const cleared = fixture.cases.find((c) => c.id === "plain/required/clear").output.fields[0];
  assert.deepEqual([cleared.required, cleared.required_reason, cleared.required_by], [false, "Set by you", "human"]);
});

for (const c of fixture.cases) {
  test(`ops ${c.id}`, () => {
    const before = structuredClone(fixture.templates[c.input.template]);
    if ("error" in c) {
      assert.throws(
        () => run(c),
        (e) => e instanceof OpError && e.message === c.error && e.reason === c.error,
      );
    } else {
      const { template, warnings } = run(c);
      assert.deepEqual(template.fields, c.output.fields);
      assert.deepEqual(template.history, c.output.history);
      assert.deepEqual(warnings, c.output.warnings);
      // everything but fields and history is carried over unchanged
      assert.deepEqual({ ...template, fields: null, history: null }, { ...before, fields: null, history: null });
    }
  });
}

test("applyOp leaves its input alone", () => {
  const t = structuredClone(fixture.templates.plain);
  const frozen = structuredClone(t);
  const op = { op: "set_source", id: "n2", source: { paths: ["name"], transforms: [{ op: "upper" }] } };
  const opCopy = structuredClone(op);
  const { template } = applyOp(t, op, { author: "human", at: fixture.at });
  assert.deepEqual(t, frozen);
  assert.deepEqual(op, opCopy);
  assert.notEqual(template, t);
  // changing the result later does not reach back into the template or the op
  template.fields[1].source.paths.push("x");
  template.history[0].op.source.paths.push("y");
  assert.deepEqual(t, frozen);
  assert.deepEqual(op, opCopy);
});

test("an omitted timestamp is filled in as ISO-8601 UTC", () => {
  const { template } = applyOp(fixture.templates.plain, { op: "set_label", id: "n1", label: "x" }, { author: "human" });
  assert.match(template.history.at(-1).at, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+\+00:00$/);
});

test("the table ops are not available in the browser", () => {
  for (const name of OP_NAMES.filter((n) => !BROWSER_OPS.includes(n))) {
    assert.throws(
      () => applyOp(fixture.templates.plain, { op: name, id: "n1" }, { author: "human", at: fixture.at }),
      (e) => e instanceof OpError && /not available in the browser/.test(e.message),
    );
  }
  assert.throws(
    () => applyOp(fixture.templates.plain, { op: "add_table", source_path: "meds", segments: [] }, { author: "human" }),
    /not available in the browser/,
  );
});

test("agent edits are refused: their extra checks are not ported", () => {
  assert.throws(
    () => applyOp(fixture.templates.plain, { op: "move", id: "w1", dx: 1 }, { author: "agent", at: fixture.at }),
    (e) => e instanceof OpError && /human/.test(e.message),
  );
});

test("newFieldId follows the generated ids already in the template", () => {
  const t = (...ids) => ({ fields: ids.map((id) => ({ id })) });
  assert.equal(newFieldId(t()), "f1");
  assert.equal(newFieldId(t("n1", "f3", "f10", "f2x", "F20", "f")), "f11");
  assert.equal(newFieldId(t("f٣")), "f4"); // Python's \d takes any decimal digit
  assert.equal(newFieldId(t("f99999999999999999999")), "f100000000000000000000");
  assert.equal(newFieldId(fixture.templates.plain), "f4");
});

test("contextFromTemplate reads schema paths and page sizes", () => {
  const ctx = contextFromTemplate(fixture.templates.plain);
  assert.deepEqual(ctx.pageSizes, [[612, 792], [400, 500]]);
  assert.ok(ctx.schemaPaths.some((p) => p.path === "meds[].dose" && p.in_table === "meds"));
});

test("python-compat helpers", () => {
  assert.equal(pyFormatG(612), "612");
  assert.equal(pyFormatG(0.5), "0.5");
  assert.equal(pyFormatG(100.125), "100.125");
  assert.equal(pyFormatG(1234567), "1.23457e+06");
  assert.equal(pyFormatG(0.00001234), "1.234e-05");
  assert.equal(pyFormatG(-2), "-2");
  assert.equal(pyFormatG(1 / 3), "0.333333");
  assert.equal(pyFixed(0.125, 2), "0.12");
  assert.equal(pyFixed(0.375, 2), "0.38");
  assert.equal(pyFixed(0.15, 1), "0.1"); // the double below 0.15, not a tie
  assert.equal(pyFixed(0.3333, 2), "0.33");
  assert.equal(pyRound1(72.25), 72.2);
  assert.equal(pyRound1(72.75), 72.8);
  assert.equal(pyFloorDiv(1.0, 0.1), 9);
  assert.equal(pyFloorDiv(7, 2), 3);
  assert.equal(pyFloorDiv(-7, 2), -4);
  assert.deepEqual(getCloseMatches("appel", ["ape", "apple", "peach", "puppy"]), ["apple", "ape"]);
  assert.deepEqual(getCloseMatches("wheel", ["while", "if", "for", "else"]), ["while"]);
  assert.deepEqual(getCloseMatches("x", []), []);
  assert.equal(containedFraction([0, 0, 10, 10], [5, 0, 20, 10]), 0.5);
  assert.equal(containedFraction([0, 0, 10, 10], [20, 0, 30, 10]), 0);
  assert.equal(containedFraction([0, 0, 0, 10], [0, 0, 10, 10]), 0);
});
