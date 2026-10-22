// placeholderRecord: type-driven sample records for a pasted schema.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { SchemaError, flattenSchema, parsePath, isArrayItem, placeholderRecord } from "../../demo/web/core/schema.js";

const root = new URL("../../", import.meta.url);
const readJson = (rel) => JSON.parse(readFileSync(new URL(rel, root), "utf8"));
const obj = (properties, extra = {}) => ({ type: "object", properties, ...extra });

// dev-split forms only; the held-out schemas are never read
const devSchemas = readJson("forms/manifest.json").forms
  .filter((f) => f.split === "dev")
  .map((f) => [f.id, readJson(`forms/schemas/${f.id}.json`)]);

/** Every value the record holds at `path`, expanding "[]" over the rows. */
function valuesAt(record, path) {
  let level = [record];
  for (const seg of parsePath(path)) {
    level = level.flatMap((v) => {
      if (isArrayItem(seg)) return Array.isArray(v) ? v : [];
      return v !== null && typeof v === "object" && Object.hasOwn(v, seg) ? [v[seg]] : [];
    });
  }
  return level;
}

test("there are dev schemas to check", () => {
  assert.ok(devSchemas.length >= 10);
});

for (const [id, schema] of devSchemas) {
  test(`dev form ${id}: a value at every path`, () => {
    const record = placeholderRecord(schema);
    for (const sp of flattenSchema(schema)) {
      const values = valuesAt(record, sp.path);
      assert.ok(values.length > 0, `${sp.path} has no value`);
      for (const v of values) {
        assert.notEqual(v, undefined, sp.path);
        if (sp.type === "array") assert.ok(Array.isArray(v), `${sp.path} is not an array`);
        else if (sp.type === "object") assert.equal(typeof v, "object", sp.path);
        else assert.notEqual(v, null, `${sp.path} is null`);
      }
    }
  });
}

test("scalars take the value their type calls for", () => {
  const r = placeholderRecord(obj({
    first_name: { type: "string" },
    titled: { type: "string", title: "Pet name" },
    count: { type: "integer" },
    weight: { type: "number" },
    smoker: { type: "boolean" },
    nothing: { type: "null" },
    untyped: {},
  }));
  assert.equal(r.first_name, "Sample first name");
  assert.equal(r.titled, "Sample Pet name");
  assert.equal(r.count, 1);
  assert.equal(r.weight, 1);
  assert.equal(r.smoker, true);
  assert.equal(r.nothing, null);
  assert.equal(r.untyped, "Sample untyped");
});

test("an enum takes its first option, whatever the value type", () => {
  const r = placeholderRecord(obj({
    sex: { type: "string", enum: ["F", "M"] },
    level: { type: "integer", enum: [3, 4] },
    flag: { enum: [true, "x"] },
    only: { const: "fixed" },
  }));
  assert.deepEqual(r, { sex: "F", level: 3, flag: true, only: "fixed" });
});

test("format date gives an ISO date; a date-like name does too", () => {
  const r = placeholderRecord(obj({
    visit: { type: "string", format: "date" },
    date_of_birth: { type: "string" },
    stamp: { type: "string", format: "date-time" },
  }));
  assert.match(r.visit, /^\d{4}-\d{2}-\d{2}$/);
  assert.match(r.date_of_birth, /^\d{4}-\d{2}-\d{2}$/);
  assert.match(r.stamp, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.ok(!Number.isNaN(Date.parse(r.visit)));
});

test("email-named strings and format email contain an @", () => {
  const r = placeholderRecord(obj({
    email: { type: "string" },
    contactEmail: { type: "string" },
    work_email_address: { type: "string" },
    other: { type: "string", format: "email" },
    mailing_address: { type: "string" },
  }));
  for (const key of ["email", "contactEmail", "work_email_address", "other"]) assert.match(r[key], /@/, key);
  assert.doesNotMatch(r.mailing_address, /@/);
});

test("ZIP-named strings are 5 digits; phone and SSN names get a fitting value", () => {
  const r = placeholderRecord(obj({
    zip: { type: "string" },
    zip_code: { type: "string" },
    postalCode: { type: "string" },
    phone: { type: "string" },
    ssn: { type: "string" },
  }));
  for (const key of ["zip", "zip_code", "postalCode"]) assert.match(r[key], /^\d{5}$/, key);
  assert.match(r.phone, /^\d{3}-\d{3}-\d{4}$/);
  assert.match(r.ssn, /^\d{3}-\d{2}-\d{4}$/);
});

test("a simple pattern is honoured, and so are length limits", () => {
  const r = placeholderRecord(obj({
    npi: { type: "string", pattern: "^\\d{10}$" },
    ssn: { type: "string", pattern: "^\\d{9}$" },
    ssn2: { type: "string", pattern: "^\\d{3}-?\\d{2}-?\\d{4}$" },
    state: { type: "string", pattern: "^[A-Z]{2}$" },
    initial: { type: "string", maxLength: 1 },
    padded: { type: "string", minLength: 30 },
  }));
  assert.match(r.npi, /^\d{10}$/);
  assert.match(r.ssn, /^\d{9}$/);
  assert.match(r.ssn2, /^\d{3}-?\d{2}-?\d{4}$/);
  assert.match(r.state, /^[A-Z]{2}$/);
  assert.equal(r.initial.length, 1);
  assert.equal(r.padded.length, 30);
});

test("numbers stay inside their range", () => {
  const r = placeholderRecord(obj({
    a: { type: "integer", minimum: 5 },
    b: { type: "integer", maximum: 0 },
    c: { type: "number", exclusiveMinimum: 10 },
    d: { type: "integer", minimum: 0, maximum: 1 },
    e: { type: "integer", multipleOf: 4 },
  }));
  assert.equal(r.a, 5);
  assert.equal(r.b, 0);
  assert.ok(r.c > 10);
  assert.equal(r.d, 1);
  assert.equal(r.e, 4);
});

test("arrays have 2 rows unless minItems or maxItems say otherwise", () => {
  const rows = obj({ n: { type: "string" }, d: { type: "string", format: "date" } });
  const r = placeholderRecord(obj({
    tags: { type: "array", items: { type: "string" } },
    meds: { type: "array", items: rows },
    grid: { type: "array", items: { type: "array", items: { type: "integer" } } },
    few: { type: "array", maxItems: 1, items: { type: "string" } },
    many: { type: "array", minItems: 4, items: { type: "string" } },
    nothing: { type: "array", maxItems: 0, items: { type: "string" } },
    open: { type: "array" },
  }));
  assert.equal(r.tags.length, 2);
  assert.equal(r.meds.length, 2);
  assert.deepEqual(r.grid, [[1, 2], [1, 2]]);
  assert.equal(r.few.length, 1);
  assert.equal(r.many.length, 4);
  assert.deepEqual(r.nothing, []);
  assert.equal(r.open.length, 2);
  assert.notDeepEqual(r.meds[0], r.meds[1]);
});

test("uniqueItems arrays hold distinct values", () => {
  const r = placeholderRecord(obj({
    picks: { type: "array", uniqueItems: true, items: { type: "string", enum: ["a", "b", "c"] } },
    one: { type: "array", uniqueItems: true, items: { type: "string", enum: ["a"] } },
    flags: { type: "array", uniqueItems: true, items: { type: "boolean" } },
  }));
  assert.deepEqual(r.picks, ["a", "b"]);
  assert.deepEqual(r.one, ["a"]);
  assert.deepEqual(r.flags, [true]);
});

test("refs, allOf and nullable unions are followed; a recursive ref stops", () => {
  const r = placeholderRecord({
    type: "object",
    $defs: {
      Addr: obj({ zip: { type: "string" }, city: { type: "string" } }),
      Node: obj({ name: { type: "string" }, child: { $ref: "#/$defs/Node" } }),
    },
    properties: {
      home: { $ref: "#/$defs/Addr" },
      past: { type: "array", items: { $ref: "#/$defs/Addr" } },
      merged: { allOf: [obj({ a: { type: "string" } }), { properties: { b: { type: "integer" } } }] },
      maybe: { anyOf: [{ type: "string", format: "date" }, { type: "null" }] },
      tree: { $ref: "#/$defs/Node" },
    },
  });
  assert.equal(r.home.zip, "12345");
  assert.equal(r.past.length, 2);
  assert.deepEqual(Object.keys(r.merged), ["a", "b"]);
  assert.match(r.maybe, /^\d{4}-\d{2}-\d{2}$/);
  assert.deepEqual(r.tree, { name: "Sample name", child: {} });
});

test("property order follows the schema; odd names survive", () => {
  const r = placeholderRecord(obj({ z: { type: "string" }, a: { type: "string" }, "a.b": { type: "string" }, "": { type: "string" } }));
  assert.deepEqual(Object.keys(r), ["z", "a", "a.b", ""]);
});

test("a schema that is not an object schema is refused with flattenSchema's message", () => {
  assert.throws(() => placeholderRecord({ type: "array", items: { type: "string" } }),
    (e) => e instanceof SchemaError && e.message === "schema root must be an object schema");
  assert.throws(() => placeholderRecord(obj({ a: { $ref: "#/$defs/Missing" } })), SchemaError);
});

test("records are deterministic", () => {
  const schema = devSchemas[0][1];
  assert.deepEqual(placeholderRecord(schema), placeholderRecord(schema));
});
