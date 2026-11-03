// workbench/mapping-model.js: the Mapping view's three lists and the edits a person makes to a data mapping.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  change,
  columnsOf,
  confirm,
  confirmAll,
  connect,
  fixed,
  formFields,
  originOf,
  remove,
  titleOf,
  triage,
  valueAt,
  withValues,
} from "../../demo/web/workbench/mapping-model.js";

const SCHEMA = {
  type: "object",
  required: ["applicant"],
  properties: {
    applicant: {
      type: "object",
      title: "Applicant",
      required: ["dob"],
      properties: {
        name: { type: "string", title: "Full name" },
        dob: { type: "string", title: "Date of birth" },
        address: { type: "string" },
      },
    },
    owners: { type: "array", items: { type: "object", properties: { name: { type: "string", title: "Owner name" } } } },
    unboxed: { type: "string", title: "Not on the page" },
  },
};
const LINKED = ["applicant.name", "applicant.dob", "applicant.address", "owners[0].name", "owners[1].name"];
const COLUMNS = [
  { name: "Full Name", samples: ["Ann Lee", "Bo Chen", "Cy Diaz"] },
  { name: "DOB", samples: ["01/02/1990"] },
  { name: "Street", samples: ["1 Main St"] },
  { name: "City", samples: ["Erie"] },
  { name: "Internal ID", samples: ["7"] },
];
const MAPPING = {
  columns: COLUMNS.map((c) => c.name),
  rules: [
    { path: "applicant.name", column: "Full Name", transforms: [] },
    { path: "applicant.address", template: "{Street}, {City}", confirmed: true },
    { path: "owners[0].name", column: "Full Name", transforms: [{ op: "split", args: { index: 0 } }] },
    { path: "unboxed", value: "x" },
  ],
  unmapped_columns: ["DOB", "Internal ID"],
};

test("triage sorts the form fields by whether a rule sets them and the columns by whether one reads them", () => {
  const record = { applicant: { name: "Ann Lee", address: "1 Main St, Erie" }, owners: [{ name: "Ann" }] };
  const t = triage({ mapping: MAPPING, schema: SCHEMA, linked: LINKED, columns: COLUMNS, record });
  assert.deepEqual(
    t.matched.map((r) => [r.path, r.title, r.required, r.origin, r.value]),
    [
      ["applicant.name", "Full name", false, "AI", "Ann Lee"],
      ["applicant.address", "address", false, "AI", "1 Main St, Erie"],
      ["owners[0].name", "Owner name", false, "AI", "Ann"],
    ],
  );
  // the required one first; a rule for a path no box uses ("unboxed") is in neither list
  assert.deepEqual(t.missing.map((r) => [r.path, r.title, r.required]), [["applicant.dob", "Date of birth", true], ["owners[1].name", "Owner name", false]]);
  assert.deepEqual(t.unused, [{ name: "DOB", samples: ["01/02/1990"] }, { name: "Internal ID", samples: ["7"] }]);
  assert.deepEqual(t.counts, { matched: 3, confirmed: 1, missing: 2, unused: 2 });
});

test("the required form fields come first in each list, then the order the boxes have", () => {
  const mapping = { rules: [{ path: "applicant.name", column: "Full Name" }, { path: "applicant.dob", column: "DOB" }] };
  const t = triage({ mapping, schema: SCHEMA, linked: LINKED, columns: COLUMNS });
  assert.deepEqual(t.matched.map((r) => r.path), ["applicant.dob", "applicant.name"]);
  assert.equal(t.matched[0].value, undefined); // no record on show
});

test("a table box's form fields are its cells, and a pick of one selects the table", () => {
  const template = {
    fields: [
      { id: "a", type: "text", source: { paths: ["applicant.name"] } },
      {
        id: "t", type: "table", source: { paths: ["owners"] },
        segments: [{ max_rows: 2, columns: [{ key: "name" }] }, { max_rows: 1, columns: [{ key: "name" }] }],
      },
      { id: "u", type: "text" },
    ],
  };
  assert.deepEqual(formFields(template), [
    { path: "applicant.name", box: "applicant.name" },
    { path: "owners[0].name", box: "owners" },
    { path: "owners[1].name", box: "owners" },
    { path: "owners[2].name", box: "owners" },
  ]);
});

test("a path that is not a value of the form (an object, a list of records, none at all) is no form field", () => {
  const linked = ["applicant", "owners", "nope", "applicant.name"];
  const t = triage({ mapping: { rules: [] }, schema: SCHEMA, linked, columns: [] });
  assert.deepEqual(t.missing.map((r) => r.path), ["applicant.name"]);
});

test("a rule reads its column or its template's placeholders; a fixed value reads none", () => {
  assert.deepEqual(columnsOf({ path: "a", column: "A" }), ["A"]);
  assert.deepEqual(columnsOf({ path: "a", template: "{Street}, {City} {ZIP}" }), ["Street", "City", "ZIP"]);
  assert.deepEqual(columnsOf({ path: "a", value: 3 }), []);
});

test("a title falls back on the field's own name, and an item's on its place in the list", () => {
  assert.equal(titleOf(SCHEMA, "applicant.address"), "address");
  assert.equal(titleOf({ type: "object", properties: { refs: { type: "array", items: { type: "object", properties: { last_name: { type: "string" } } } } } }, "refs[1].last_name"), "last name 2");
  assert.equal(titleOf(SCHEMA, "nope.at_all"), "at all");
  assert.equal(valueAt({ owners: [{ name: "A" }, { name: "B" }] }, "owners[1].name"), "B");
  assert.equal(valueAt({ owners: [] }, "owners[1].name"), undefined);
});

test("typed values are laid over a copy of the record, making what is missing on their way", () => {
  const record = { applicant: { name: "Ann" } };
  const out = withValues(record, { "applicant.dob": "1990-01-02", "owners[1].name": "Bo" });
  assert.deepEqual(out.applicant, { name: "Ann", dob: "1990-01-02" });
  assert.equal(out.owners.length, 2);
  assert.deepEqual(out.owners[1], { name: "Bo" });
  assert.deepEqual(record, { applicant: { name: "Ann" } });
  assert.deepEqual(withValues(null, {}), {});
});

test("a rule's origin is the person's when they made it, else how the mapping came about", () => {
  assert.equal(originOf({ manual: true }, "model"), "you");
  assert.equal(originOf({}, "saved"), "saved");
  assert.equal(originOf({}, "direct"), "by name");
  assert.equal(originOf({}, "model"), "AI");
});

test("confirming marks one rule or several, and unconfirming takes the mark away", () => {
  const once = confirm(MAPPING, "applicant.name");
  assert.equal(once.rules[0].confirmed, true);
  assert.equal(MAPPING.rules[0].confirmed, undefined); // the mapping given is left as it was
  assert.equal("confirmed" in confirm(once, "applicant.name", false).rules[0], false);
  const all = confirmAll(MAPPING, ["applicant.name", "owners[0].name"]);
  assert.deepEqual(all.rules.map((r) => Boolean(r.confirmed)), [true, true, true, false]);
});

test("changing, connecting and fixing make a person's rule in place; removing takes the rule out", () => {
  const changed = change(MAPPING, "owners[0].name", "Internal ID");
  assert.deepEqual(changed.rules[2], { path: "owners[0].name", column: "Internal ID", transforms: [], manual: true });
  assert.equal(changed.rules.length, 4);
  const connected = connect(MAPPING, "applicant.dob", "DOB");
  assert.deepEqual(connected.rules.at(-1), { path: "applicant.dob", column: "DOB", transforms: [], manual: true });
  const typed = fixed(MAPPING, "applicant.dob", "1990-01-02");
  assert.deepEqual(typed.rules.at(-1), { path: "applicant.dob", value: "1990-01-02", transforms: [], manual: true });
  assert.deepEqual(remove(MAPPING, "applicant.name").rules.map((r) => r.path), ["applicant.address", "owners[0].name", "unboxed"]);
  assert.deepEqual(changed.columns, MAPPING.columns);
});
