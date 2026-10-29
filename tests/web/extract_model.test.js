// workbench/extract-model.js: the extraction page's pure logic, on the mockup's sample data shapes.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  batchLine,
  batchNote,
  checksOf,
  fieldSchema,
  fieldState,
  groupFields,
  outcomeLine,
  pagesText,
  parsePages,
  problemLabel,
  readFrom,
  rowProof,
  stripCells,
} from "../../demo/web/workbench/extract-model.js";

/** A sample's proof: its outcome and what the readers read (`status` found unless given). */
function sample(outcome, value = "x", { status = "found", reasons = [], opinion = value, file = "a.pdf" } = {}) {
  return { file, outcome, read: { path: "p", status, raw: value, value, reasons, page: 0, rect: null }, opinion };
}

/** A FieldProof with these samples. */
function proof(samples, { ready = true, absent = false, path = "investor.tax_id" } = {}) {
  return { path, ready, absent, samples, suggested: {} };
}

const times = (n, make) => Array.from({ length: n }, make);

// ------------------------------------------------------------------ fieldState

test("fieldState: five agreeing samples are ● 5/5 ready", () => {
  assert.deepEqual(fieldState(proof(times(5, () => sample("agree")))), { glyph: "●", text: "5/5", state: "ready" });
});

test("fieldState: three agree, one fails its check alike and one is missing: ◐ 4/5 ready", () => {
  const samples = [...times(3, () => sample("agree")), sample("failed_check", "8422-19037"), sample("missing", null)];
  assert.deepEqual(fieldState(proof(samples)), { glyph: "◐", text: "4/5", state: "ready" });
});

test("fieldState: four agree and one disagrees: ◐ 4/5 weak", () => {
  const samples = [...times(4, () => sample("agree")), sample("disagree", "8422", { opinion: "84-22" })];
  assert.deepEqual(fieldState(proof(samples, { ready: false })), { glyph: "◐", text: "4/5", state: "weak" });
});

test("fieldState: five disagreeing samples are × 0/5 weak", () => {
  const samples = times(5, () => sample("disagree", "a", { opinion: "b" }));
  assert.deepEqual(fieldState(proof(samples, { ready: false })), { glyph: "×", text: "0/5", state: "weak" });
});

test("fieldState: a field not ready with an unsure read is ?, and an absent field is not on this form", () => {
  const samples = [...times(4, () => sample("agree")), sample("disagree", "03/11/1979", { status: "unsure", opinion: "1979-03-11" })];
  assert.deepEqual(fieldState(proof(samples, { ready: false })), { glyph: "?", text: "4/5", state: "weak" });
  const absent = proof(times(5, () => sample("absent", null)), { ready: false, absent: true });
  assert.deepEqual(fieldState(absent), { glyph: "○", text: "not on this form", state: "absent" });
});

// ------------------------------------------------------------------ readFrom

test("readFrom gives one line per strategy, for each kind of strategy", () => {
  const reader = {
    strategies: [
      { kind: "widget", names: ["tax_id"] },
      { kind: "box", page: 0, rect: [72, 300, 300, 318] },
      { kind: "anchor", labels: ["Taxpayer ID"], page: null, side: "right", max_width: 300, max_lines: 1 },
      { kind: "anchor", labels: ["Mailing address"], page: null, side: "below", max_width: 300, max_lines: 2 },
      { kind: "phrase", phrases: ["account"], page: null, stop: "digits" },
      { kind: "choice", options: { individual: { widget: "t1" }, joint: { widget: "t2" }, trust: { widget: "t3" }, llc: { widget: "t4" } } },
    ],
    clean: [],
  };
  assert.deepEqual(readFrom(reader, { type: "string" }), [
    "Form field tax_id",
    "A box on page 1",
    "Next to the label Taxpayer ID",
    "Below the label Mailing address",
    "Page text, after account",
    "Checkbox group, 4 boxes",
  ]);
});

test("readFrom names every wording of a strategy, and a lone checkbox on a yes-or-no field as one", () => {
  const reader = { strategies: [{ kind: "widget", names: ["TaxID", "tin"] }, { kind: "anchor", labels: ["Taxpayer ID", "TIN"], side: "right" }] };
  assert.deepEqual(readFrom(reader, {}), ["Form field TaxID or tin", "Next to the label Taxpayer ID or TIN"]);
  const lone = { strategies: [{ kind: "choice", options: { yes: { page: 0, rect: [1, 2, 3, 4] } } }] };
  assert.deepEqual(readFrom(lone, { type: "boolean" }), ["A checkbox, checked for yes"]);
  assert.deepEqual(readFrom(lone, { type: "string" }), ["Checkbox group, 1 box"]);
  assert.deepEqual(readFrom(undefined, {}), []);
});

// ------------------------------------------------------------------ checksOf

test("checksOf lists the field's checks in words", () => {
  assert.deepEqual(checksOf({ type: "string", pattern: "^\\d{2}-\\d{7}$" }, true), ["Required", "Format ^\\d{2}-\\d{7}$"]);
  assert.deepEqual(checksOf({ type: "string", format: "date" }, false), ["Is a date"]);
  assert.deepEqual(checksOf({ type: "string", enum: ["individual", "joint", "trust", "llc"] }, true), ["Required", "One of individual, joint, trust, llc"]);
  assert.deepEqual(checksOf({ type: "number", minimum: 0, maximum: 100 }, false), ["Between 0 and 100"]);
  assert.deepEqual(checksOf({ type: "number", minimum: 0 }, false), ["At least 0"]);
  assert.deepEqual(checksOf({ type: "number", maximum: 100 }, false), ["At most 100"]);
  assert.deepEqual(checksOf({ type: "string" }, false), []);
  assert.deepEqual(checksOf(undefined, true), ["Required"]);
});

// ------------------------------------------------------------------ outcomeLine

test("outcomeLine: an agreeing sample shows its value, a failing one its first reason, an absent one says so", () => {
  assert.deepEqual(outcomeLine(sample("agree", "84-2219037")), { glyph: "●", text: "84-2219037" });
  const bad = sample("failed_check", "8422-19037", { reasons: ["doesn't match the format", "too long"] });
  assert.deepEqual(outcomeLine(bad), { glyph: "×", text: "doesn't match the format" });
  assert.deepEqual(outcomeLine(sample("missing", null)), { glyph: "×", text: "missing" });
  assert.deepEqual(outcomeLine(sample("absent", null)), { glyph: "○", text: "not on this form" });
});

test("outcomeLine: a disagreeing sample with no reason says what each side read; lists and booleans read as words", () => {
  assert.deepEqual(outcomeLine(sample("disagree", "8422", { opinion: "84-22" })), { glyph: "×", text: "read 8422, the AI read 84-22" });
  assert.deepEqual(outcomeLine(sample("disagree", null, { opinion: "Chase" })), { glyph: "×", text: "read nothing, the AI read Chase" });
  assert.deepEqual(outcomeLine(sample("agree", ["Ana Ruiz", "Ben Ruiz"])), { glyph: "●", text: "Ana Ruiz · Ben Ruiz" });
  assert.deepEqual(outcomeLine(sample("agree", true)), { glyph: "●", text: "yes" });
});

// ------------------------------------------------------------------ groupFields

const SCHEMA = {
  type: "object",
  properties: {
    investor: {
      type: "object",
      required: ["legal_name", "tax_id"],
      properties: {
        legal_name: { type: "string", title: "Investor legal name" },
        tax_id: { type: "string", title: "Taxpayer ID", pattern: "^\\d{2}-\\d{7}$" },
        type: { type: "string", enum: ["individual", "joint", "trust", "llc"] },
      },
    },
    owners: {
      type: "array",
      maxItems: 4,
      items: {
        type: "object",
        properties: { name: { type: "string", title: "Beneficial owner name" }, percent: { type: "number", minimum: 0, maximum: 100 } },
      },
    },
    wire: { type: "object", properties: { bank_name: { type: "string", title: "Bank" } } },
    note: { type: "string" },
  },
};

const OWNER_PATHS = [0, 1, 2, 3].flatMap((i) => [`owners[${i}].name`, `owners[${i}].percent`]);
const PATHS = ["investor.legal_name", "investor.tax_id", "investor.type", ...OWNER_PATHS, "wire.bank_name", "note"];
const PROOF = { fields: PATHS.map((path) => proof(times(5, () => sample("agree")), { path })), ready: PATHS.length, total: PATHS.length };

test("groupFields groups by the top-level key and folds a repeated group's items into one row each", () => {
  const groups = groupFields(PROOF, SCHEMA);
  assert.deepEqual(groups.map((g) => g.group), ["investor", "owners[ ] · up to 4", "wire", ""]);
  assert.deepEqual(groups[0].items, [
    { path: "investor.legal_name", title: "Investor legal name", generic: "investor.legal_name", items: null, paths: ["investor.legal_name"] },
    { path: "investor.tax_id", title: "Taxpayer ID", generic: "investor.tax_id", items: null, paths: ["investor.tax_id"] },
    { path: "investor.type", title: "type", generic: "investor.type", items: null, paths: ["investor.type"] },
  ]);
  assert.deepEqual(groups[1].items, [
    { path: "owners[0].name", title: "Beneficial owner name", generic: "owners[].name", items: 4, paths: ["owners[0].name", "owners[1].name", "owners[2].name", "owners[3].name"] },
    { path: "owners[0].percent", title: "percent", generic: "owners[].percent", items: 4, paths: ["owners[0].percent", "owners[1].percent", "owners[2].percent", "owners[3].percent"] },
  ]);
  assert.deepEqual(groups[3].items.map((r) => r.path), ["note"]);
});

test("a repeated group without maxItems has 4 items, as the library gives it", () => {
  const schema = { type: "object", properties: { refs: { type: "array", items: { type: "object", properties: { phone: { type: "string" } } } } } };
  const paths = [0, 1, 2, 3].map((i) => `refs[${i}].phone`);
  const groups = groupFields({ fields: paths.map((path) => proof([], { path })) }, schema);
  assert.deepEqual(groups.map((g) => [g.group, g.items.length, g.items[0].items]), [["refs[ ] · up to 4", 1, 4]]);
});

test("rowProof is a plain row's own FieldProof", () => {
  const row = groupFields(PROOF, SCHEMA)[0].items[1];
  assert.equal(rowProof(row, PROOF), PROOF.fields[1]);
});

test("rowProof takes a folded row's items together: each sample's worst outcome, absent only when every item is", () => {
  const names = [
    proof([sample("agree"), sample("agree"), sample("missing", null)], { path: "owners[0].name" }),
    proof([sample("agree"), sample("disagree"), sample("missing", null)], { path: "owners[1].name", ready: false }),
    proof([sample("missing", null), sample("missing", null), sample("missing", null)], { path: "owners[2].name", ready: false }),
    proof(times(3, () => sample("absent", null)), { path: "owners[3].name", ready: false, absent: true }),
  ];
  const row = { path: "owners[0].name", generic: "owners[].name", items: 4, paths: names.map((f) => f.path) };
  const merged = rowProof(row, { fields: names });
  assert.deepEqual(merged.samples.map((s) => s.outcome), ["agree", "disagree", "missing"]);
  assert.deepEqual([merged.path, merged.absent, merged.ready], ["owners[0].name", false, false]); // owners[1] is not ready
  assert.deepEqual(fieldState(merged), { glyph: "◐", text: "1/3", state: "weak" });

  const fixed = names.map((f, i) => (i === 1 ? proof([sample("agree"), sample("agree"), sample("missing", null)], { path: f.path }) : f));
  assert.deepEqual(fieldState(rowProof(row, { fields: fixed })), { glyph: "◐", text: "2/3", state: "ready" }); // owners[2] is on no sample
  const gone = names.map((f) => ({ ...f, absent: true, ready: false, samples: times(3, () => sample("absent", null)) }));
  assert.deepEqual(fieldState(rowProof(row, { fields: gone })), { glyph: "○", text: "not on this form", state: "absent" });
});

test("fieldSchema finds a concrete path's schema and whether it is required", () => {
  assert.deepEqual(fieldSchema(SCHEMA, "investor.tax_id"), { prop: SCHEMA.properties.investor.properties.tax_id, required: true });
  assert.deepEqual(fieldSchema(SCHEMA, "investor.type"), { prop: SCHEMA.properties.investor.properties.type, required: false });
  assert.deepEqual(fieldSchema(SCHEMA, "owners[2].percent").prop.maximum, 100);
  const withRef = { type: "object", $defs: { tin: { type: "string", pattern: "x" } }, required: ["tin"], properties: { tin: { $ref: "#/$defs/tin" } } };
  assert.deepEqual(fieldSchema(withRef, "tin"), { prop: { type: "string", pattern: "x" }, required: true });
  assert.deepEqual(fieldSchema(SCHEMA, "nope.here"), { prop: {}, required: false });
});

// ------------------------------------------------------------------ problemLabel, batchLine, stripCells

test("problemLabel names each problem status", () => {
  assert.equal(problemLabel("missing"), "Missing");
  assert.equal(problemLabel("failed_check"), "Fails a check");
  assert.equal(problemLabel("unsure"), "Unsure");
});

const BATCH = {
  id: "b".repeat(32),
  name: "client_forms_2026.zip",
  count: 1248,
  bytes: 412_000_000,
  skipped: 0,
  folder: false,
  scan: {
    phase: "done",
    done: 1248,
    total: 1248,
    error: null,
    groups: [
      { group: 0, size: 1163, fillable: 776, flattened: 387, closeness: 0.97 },
      { group: 1, size: 47, fillable: 0, flattened: 47, closeness: 0.4 },
    ],
  },
};

test("batchLine says how many PDFs, their size and how many copies of one layout", () => {
  assert.equal(batchLine(BATCH), "1,248 PDFs · 412 MB · 1,163 fillable or flattened copies of one layout, 85 set aside");
  const small = { ...BATCH, count: 5, bytes: 61_440, scan: { ...BATCH.scan, done: 5, total: 5, groups: [{ group: 0, size: 5, fillable: 3, flattened: 2, closeness: 1 }] } };
  assert.equal(batchLine(small), "5 PDFs · 61 KB · 5 fillable or flattened copies of one layout");
  const scanning = { ...BATCH, scan: { phase: "scanning", done: 40, total: 1248, error: null, groups: [] } };
  assert.equal(batchLine(scanning), "1,248 PDFs · 412 MB · scanning, 40 of 1,248");
  const failed = { ...BATCH, scan: { phase: "error", done: 3, total: 1248, error: "The scan stopped.", groups: [] } };
  assert.equal(batchLine(failed), "1,248 PDFs · 412 MB · the scan failed: The scan stopped.");
  assert.equal(batchLine({ ...BATCH, count: 1, bytes: 2_000_000, scan: { ...small.scan, groups: [{ group: 0, size: 1, fillable: 1, flattened: 0 }] } }), "1 PDF · 2 MB · 1 fillable or flattened copy of one layout");
});

test("stripCells: one cell per document up to max, the samples marked, flattened copies spread, other layouts last", () => {
  const five = { ...BATCH, count: 5, scan: { ...BATCH.scan, groups: [{ group: 0, size: 5, fillable: 5, flattened: 0 }] } };
  assert.deepEqual(stripCells(five, [0, 2]).map((c) => c.state), ["sample", "main", "sample", "main", "main"]);

  const cells = stripCells(BATCH, [0, 600, 1100]);
  assert.equal(cells.length, 120);
  const count = (state) => cells.filter((c) => c.state === state).length;
  assert.equal(count("sample"), 3);
  assert.equal(count("other"), 120 - Math.round((120 * 1163) / 1248)); // the tail: documents set aside
  assert.equal(cells.at(-1).state, "other");
  assert.ok(count("flat") > 30 && count("flat") < 45, `about a third of the layout's cells are flattened: ${count("flat")}`);
  assert.deepEqual([cells[0].state, cells[Math.floor((600 * 120) / 1248)].state], ["sample", "sample"]);
  assert.equal(stripCells(BATCH, [], 10).length, 10);
});

test("stripCells before the scan is done shows every cell as not scanned yet, no sample marked, and an empty batch has none", () => {
  const scanning = { ...BATCH, count: 4, scan: { phase: "scanning", done: 1, total: 4, error: null, groups: [] } };
  assert.deepEqual(stripCells(scanning, [0, 2]).map((c) => c.state), ["pending", "pending", "pending", "pending"]);
  const failed = { ...scanning, scan: { ...scanning.scan, phase: "error", error: "The scan stopped." } };
  assert.deepEqual(stripCells(failed, []).map((c) => c.state), ["pending", "pending", "pending", "pending"]);
  assert.deepEqual(stripCells({ ...BATCH, count: 0 }, []), []);
});

test("parsePages reads page numbers from 1 with commas and ranges as page indexes from 0, in order and each once", () => {
  assert.deepEqual(parsePages("1, 3-4"), [0, 2, 3]);
  assert.deepEqual(parsePages("2"), [1]);
  assert.deepEqual(parsePages(" 4 - 5 ,1,1, "), [0, 3, 4]);
  for (const bad of ["", "  ", ",", "0", "x", "4-2", "1, two", "1-", "-3", "1.5", "0-2"]) {
    assert.equal(parsePages(bad), null, JSON.stringify(bad));
  }
});

test("pagesText writes page indexes back as parsePages reads them, runs as ranges", () => {
  assert.equal(pagesText([0, 2, 3]), "1, 3-4");
  assert.equal(pagesText([3, 0, 1, 2, 7]), "1-4, 8");
  assert.equal(pagesText([]), "");
  assert.deepEqual(parsePages(pagesText([4, 5, 9])), [4, 5, 9]);
});

test("batchNote says how many fit the layout, how many are set aside, and what a strip cell stands for", () => {
  assert.equal(
    batchNote(BATCH, 120),
    "One layout fits 1,163 of them (776 fillable, 387 flattened); 85 look different and are set aside, for a second template later. " +
      "Each cell is about 10 documents: ■ in the sample · □ fillable · ▨ flattened · dashed, a different layout.",
  );
  const five = { ...BATCH, count: 5, scan: { ...BATCH.scan, groups: [{ group: 0, size: 5, fillable: 3, flattened: 2, closeness: 1 }] } };
  assert.equal(batchNote(five, 5), "One layout fits 5 of them (3 fillable, 2 flattened). Each cell is a document: ■ in the sample · □ fillable · ▨ flattened.");
  const oneAside = { ...five, count: 6, scan: { ...five.scan, groups: [...five.scan.groups, { group: 1, size: 1, fillable: 0, flattened: 1 }] } };
  assert.match(batchNote(oneAside, 6), /; 1 looks different and is set aside, for a second template later\./);
});

test("batchNote says nothing before the scan is done, and says so when no document could be read", () => {
  assert.equal(batchNote({ ...BATCH, scan: { phase: "scanning", done: 1, total: 4, error: null, groups: [] } }, 4), "");
  assert.equal(batchNote({ ...BATCH, count: 0, scan: { ...BATCH.scan, groups: [] } }, 0), "");
  assert.equal(batchNote({ ...BATCH, count: 2, scan: { ...BATCH.scan, groups: [] } }, 2), "None of them could be read as a form, so there is no layout to learn.");
});
