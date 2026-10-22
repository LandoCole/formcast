// Holds values.js to the answers recorded from Python (fixtures/values.json), and checks the
// helpers on their own.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { compareCodePoints } from "../../demo/web/core/pycompat.js";
import { SEVERITY, choiceOn, isMissing, lookup, makeWarning, resolve } from "../../demo/web/core/values.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/values.json", import.meta.url), "utf8"));

/** Warnings ordered by (code, path, field), as the exporter orders them. */
function sorted(warnings) {
  const key = (w) => [w.code, w.path ?? "", w.field ?? ""];
  return [...warnings].sort((a, b) => {
    const [ka, kb] = [key(a), key(b)];
    for (let i = 0; i < ka.length; i++) {
      const d = compareCodePoints(ka[i], kb[i]);
      if (d !== 0) return d;
    }
    return 0;
  });
}

/** Every warning in a result, wherever it sits. */
function allWarnings(result) {
  return [
    ...result.warnings,
    ...Object.values(result.fields).flatMap((f) => f.warnings),
    ...Object.values(result.tables).flatMap((t) => [...t.warnings, ...t.cells.flatMap((c) => c.warnings)]),
  ];
}

test("fixture covers the warning codes values.py produces, and every kind of box", () => {
  const codes = new Set();
  for (const c of fixture.cases) for (const w of allWarnings(c.output)) codes.add(w.code);
  for (const code of ["missing", "transform_error", "unmapped_data", "list_joined", "unknown_keys", "rows_dropped", "invalid"]) {
    assert.ok(codes.has(code), `no case produces ${code}`);
  }
  const kinds = new Set(fixture.cases.flatMap((c) => Object.values(c.output.fields).map((f) => f.kind)));
  assert.deepEqual([...kinds].sort(), ["choice", "text"]);
  assert.ok(fixture.cases.some((c) => Object.values(c.output.tables).some((t) => t.rows_dropped > 0)));
  assert.ok(fixture.cases.every((c) => !c.output.warnings.some((w) => w.code === "invalid")));
});

test("SEVERITY matches Python's", () => {
  assert.deepEqual({ ...SEVERITY }, fixture.severity);
});

for (const c of fixture.cases) {
  test(`resolve ${c.id}`, () => {
    const template = structuredClone(fixture.templates[c.input.template]);
    const data = structuredClone(c.input.data);
    const result = resolve(template, data);
    assert.deepEqual(result.fields, c.output.fields);
    assert.deepEqual(result.tables, c.output.tables);
    assert.deepEqual(sorted(result.warnings), c.output.warnings);
    // resolve reads its inputs and changes neither
    assert.deepEqual(template, fixture.templates[c.input.template]);
    assert.deepEqual(data, c.input.data);
  });
}

test("every mapped field gets one entry and unmapped boxes get none", () => {
  const c = fixture.cases.find((x) => x.id === "main/full");
  const template = fixture.templates.main;
  const { fields, tables } = resolve(template, c.input.data);
  const mapped = template.fields.filter((f) => f.source && f.source.paths.length);
  assert.deepEqual(
    Object.keys({ ...fields, ...tables }).sort(),
    mapped.map((f) => f.id).sort(),
  );
  assert.ok(!("t_no_source" in fields) && !("t_no_paths" in fields) && !("m1" in fields));
});

test("a record that is not an object is reported field by field and never throws", () => {
  for (const data of [null, [], "text", 7]) {
    const { fields, tables, warnings } = resolve(fixture.templates.main, data);
    assert.equal(fields.t_name.text, null);
    assert.equal(fields.t_name.warnings[0].code, "missing");
    assert.equal(tables.tbl_meds.warnings[0].code, "missing");
    assert.deepEqual(warnings, []);
  }
});

test("a template written without the optional keys resolves the same", () => {
  const lean = {
    schema: fixture.templates.main.schema,
    fields: [
      { id: "a", type: "text", page: 0, rect: [0, 0, 10, 10], source: { paths: ["name"] } },
      { id: "b", type: "checkbox", page: 0, rect: [0, 0, 10, 10], source: { paths: ["smoker"] } },
    ],
  };
  const { fields } = resolve(lean, { name: "Ann", smoker: true });
  assert.equal(fields.a.text, "Ann");
  assert.equal(fields.b.on, true);
});

test("makeWarning takes its severity from SEVERITY and fills every key", () => {
  assert.deepEqual(makeWarning("list_joined", "m"), {
    code: "list_joined", severity: "info", message: "m", path: null, field: null, page: null, rect: null,
  });
  const w = makeWarning("missing", "m", { path: "p", field: "f", page: 2, rect: [1, 2, 3, 4] });
  assert.deepEqual([w.severity, w.path, w.field, w.page, w.rect], ["warning", "p", "f", 2, [1, 2, 3, 4]]);
  assert.throws(() => makeWarning("nope", "m"), /unknown warning code 'nope'/);
});

test("isMissing: absent, blank, empty list; zero and false are values", () => {
  for (const v of [null, undefined, "", "  \t\n", [], " "]) assert.equal(isMissing(v), true, JSON.stringify(v));
  for (const v of [0, false, "0", "a", [null], [""], {}]) assert.equal(isMissing(v), false, JSON.stringify(v));
});

test("lookup follows objects only", () => {
  const data = { a: { b: [1, 2], "x y": 3, constructor: 4 }, "": 5 };
  assert.deepEqual(lookup(data, "a.b"), [1, 2]);
  assert.equal(lookup(data, 'a["x y"]'), 3);
  assert.equal(lookup(data, "a.constructor"), 4);
  assert.equal(lookup(data, "a.toString"), null);
  assert.equal(lookup(data, "a.b[]"), null);
  assert.equal(lookup(data, "a.b.c"), null);
  assert.equal(lookup(data, "z"), null);
  assert.equal(lookup(data, "a..b"), null);
  assert.equal(lookup(data, ""), null);
  assert.equal(lookup([1], "a"), null);
});

test("choiceOn: when wins over bool_to_check; errors propagate", () => {
  const eq = { op: "equals", args: { value: "F" } };
  assert.equal(choiceOn({ transforms: [], when: eq }, ["F"]), true);
  assert.equal(choiceOn({ transforms: [{ op: "lower", args: {} }], when: eq }, ["F"]), false);
  assert.equal(choiceOn({ transforms: [], when: null }, ["no"]), false);
  assert.equal(choiceOn({ transforms: [], when: null }, ["yes"]), true);
  assert.throws(() => choiceOn({ transforms: [{ op: "shout", args: {} }] }, ["x"]), /shout/);
});
