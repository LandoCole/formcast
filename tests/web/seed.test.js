// seed.js builds the starting template from an open document, as the library's
// new_template(..., candidates=True) does: widget fields, then detector fields with labels.
import assert from "node:assert/strict";
import { test } from "node:test";

import { applyOp } from "../../demo/web/core/ops.js";
import { nearestLabel, readingOrder, seedTemplate } from "../../demo/web/engines/browser/seed.js";

const SCHEMA = { type: "object", properties: { name: { type: "string" }, agree: { type: "boolean" } } };

/** A BrowserDoc stand-in: page 0 has widgets (listed out of reading order), page 1 has none. */
function fakeDoc() {
  const widget = (name, kind, rect, extra = {}) => ({ name, kind, rect, options: [], ...extra });
  return {
    pageCount: 2,
    bytes: new Uint8Array(),
    pages: [
      {
        index: 0, width: 612, height: 792, rotation: 0, cropBox: [0, 0, 612, 792], kind: "fillable",
        widgets: [
          widget("lower", "text", [50, 300, 200, 314]),
          widget("top-right", "checkbox", [400, 100, 410, 110]),
          widget("send", "button", [50, 50, 100, 70]),
          widget("top-left", "text", [50, 100, 200, 114], { tooltip: "Your full name" }),
          widget("pick", "combo", [50, 200, 200, 214], { options: ["A", "B"] }),
          widget("yes", "radio", [300, 200, 310, 210]),
          widget("sig", "signature", [50, 400, 300, 440]),
        ],
      },
      { index: 1, width: 600, height: 400, rotation: 0, cropBox: [0, 0, 600, 400], kind: "digital", widgets: [] },
    ],
    render: async () => {},
    textLines: async () => [],
  };
}

const DETECTIONS = {
  0: [{ kind: "text", rect: [300, 300, 400, 314], conf: 0.9 }], // page with widgets: skipped
  1: [
    { kind: "choice", rect: [300, 102, 310, 112], conf: 0.8 },
    { kind: "text", rect: [120, 100, 280, 114], conf: 0.95 },
    { kind: "signature", rect: [120, 300, 300, 340], conf: 0.5 },
    { kind: "text", rect: [120, 200, 280, 214], conf: 0.6 },
  ],
};

const LINES = {
  1: [
    { text: "Full   name", rect: [40, 101, 110, 113] }, // left of c1 (text at y 100)
    { text: "Far away label", rect: [10, 101, 30, 113] }, // also left, but farther
    { text: "Agree", rect: [250, 101, 290, 113] }, // left of the choice box
    { text: "Signature of applicant", rect: [120, 280, 260, 292] }, // above the signature box
    { text: "Below the box, not a label", rect: [120, 216, 280, 226] },
  ],
};

test("widget fields come in reading order with their form-map index as id; buttons are skipped", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, {}, {});
  const page0 = t.fields.filter((f) => f.page === 0);
  assert.deepEqual(page0.map((f) => f.id), ["w0_3", "w0_1", "w0_4", "w0_5", "w0_0", "w0_6"]);
  assert.deepEqual(page0.map((f) => f.type), ["text", "checkbox", "text", "radio_option", "text", "signature"]);
  assert.deepEqual(page0.map((f) => f.existing_widget), ["top-left", "top-right", "pick", "yes", "lower", "sig"]);
  for (const f of page0) {
    assert.equal(f.origin, "widget");
    assert.equal(f.confidence, null);
  }
  assert.equal(page0[0].label, "Your full name");
  assert.equal(page0[1].label, "");
});

test("fields have the library's full shape and key order", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, DETECTIONS, LINES);
  assert.deepEqual(Object.keys(t), ["formcast_version", "template_id", "schema", "form", "fields", "history"]);
  assert.deepEqual(t.fields[0], {
    id: "w0_3",
    type: "text",
    label: "Your full name",
    page: 0,
    rect: [50, 100, 200, 114],
    source: null,
    style: { font_size: "auto", min_font_size: 6.0, align: "left", comb: null, multiline: false },
    existing_widget: "top-left",
    segments: [],
    confidence: null,
    origin: "widget",
    required: false,
    required_reason: "",
    required_by: null,
  });
});

test("detection fields: ids in reading order, types from kinds, confidence; pages with widgets skip them", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, DETECTIONS, LINES);
  const det = t.fields.filter((f) => f.origin === "detector");
  assert.deepEqual(det.map((f) => f.id), ["c1_0", "c1_1", "c1_2", "c1_3"]);
  assert.deepEqual(det.map((f) => f.type), ["text", "checkbox", "text", "signature"]);
  assert.deepEqual(det.map((f) => f.confidence), [0.95, 0.8, 0.6, 0.5]);
  assert.ok(det.every((f) => f.page === 1 && f.existing_widget === null));
  assert.deepEqual(det[1].rect, [300, 102, 310, 112]);
});

test("detection labels: the nearest line to the left on the row, else the nearest above", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, DETECTIONS, LINES);
  const label = (id) => t.fields.find((f) => f.id === id).label;
  assert.equal(label("c1_0"), "Full name"); // whitespace collapsed, the closer of two lines on the left
  assert.equal(label("c1_1"), "Agree");
  assert.equal(label("c1_2"), ""); // the only nearby line is below
  assert.equal(label("c1_3"), "Signature of applicant");
});

test("nearestLabel limits: 200 pt to the left, 30 pt above, 80 characters", () => {
  const box = [300, 100, 400, 114];
  assert.equal(nearestLabel(box, [{ text: "x", rect: [50, 100, 99, 114] }]), ""); // 201 pt away
  assert.equal(nearestLabel(box, [{ text: "x", rect: [50, 100, 101, 114] }]), "x");
  assert.equal(nearestLabel(box, [{ text: "up", rect: [300, 60, 380, 69] }]), ""); // 31 pt above
  assert.equal(nearestLabel(box, [{ text: "up", rect: [300, 60, 380, 71] }]), "up");
  assert.equal(nearestLabel(box, [{ text: "aside", rect: [500, 80, 560, 95] }]), ""); // above but not over the box
  const long = "word ".repeat(30);
  assert.equal(nearestLabel(box, [{ text: long, rect: [200, 100, 290, 114] }]), long.trim().slice(0, 80));
  assert.equal(nearestLabel(box, []), "");
  // the row wins over a closer line above
  assert.equal(
    nearestLabel(box, [{ text: "Above", rect: [300, 95, 380, 99] }, { text: "Left", rect: [150, 101, 250, 113] }]),
    "Left",
  );
});

test("nearestLabel skips lines with no letter or digit", () => {
  const box = [300, 100, 400, 114];
  for (const text of [".", "–", ". .", "$ –"]) {
    assert.equal(nearestLabel(box, [{ text, rect: [280, 101, 295, 113] }]), "", text);
    assert.equal(nearestLabel(box, [{ text, rect: [280, 101, 295, 113] }, { text: "Name", rect: [200, 101, 240, 113] }]), "Name", text);
  }
});

test("nearestLabel: a weak line on the left gives way to the line above", () => {
  const box = [300, 100, 400, 114];
  const above = { text: "Social security number", rect: [300, 85, 400, 95] };
  const left = (text, x1 = 295) => ({ text, rect: [x1 - 60, 101, x1, 113] });
  // short: fewer than 3 letters and digits
  assert.equal(nearestLabel(box, [left("(c)"), above]), "Social security number");
  assert.equal(nearestLabel(box, [left("2a $"), above]), "Social security number");
  assert.equal(nearestLabel(box, [left("ZIP"), above]), "ZIP"); // 3 is enough
  // far: ending more than 72 pt from the box
  assert.equal(nearestLabel(box, [left("Document title", 227), above]), "Social security number"); // 73 pt
  assert.equal(nearestLabel(box, [left("Document title", 228), above]), "Document title"); // 72 pt
  // the tail of a sentence: starts with a lower-case letter
  assert.equal(nearestLabel(box, [left("on page 3."), above]), "Social security number");
  assert.equal(nearestLabel(box, [left("On page 3."), above]), "On page 3.");
  // a weak left line stays when there is no line above, or the line above is short too
  assert.equal(nearestLabel(box, [left("on page 3.")]), "on page 3.");
  assert.equal(nearestLabel(box, [left("(c)"), { text: "2c $", rect: [300, 85, 400, 95] }]), "(c)");
  // a punctuation-only line above is skipped, not taken as the label
  assert.equal(nearestLabel(box, [left("(c)"), { text: ". . .", rect: [300, 85, 400, 95] }]), "(c)");
});

test("readingOrder buckets rows by 1% of the page height, then sorts each row left to right", () => {
  const rect = (x, y) => ({ rect: [x, y, x + 10, y + 10] });
  const out = readingOrder([rect(300, 104), rect(100, 100), rect(200, 300), rect(50, 108)], 792);
  // 100 and 104 are one row (4 pt < 7.92 pt); 108 is 8 pt from the row's first box, so a new row
  assert.deepEqual(out.map((c) => c.rect.slice(0, 2)), [[100, 100], [300, 104], [50, 108], [200, 300]]);
});

test("form info: no fingerprint, page count, sizes and kinds; empty history; a dashless uuid", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, DETECTIONS, LINES);
  assert.equal(t.formcast_version, "1");
  assert.match(t.template_id, /^[0-9a-f]{32}$/);
  assert.notEqual(seedTemplate(fakeDoc(), SCHEMA, {}, {}).template_id, t.template_id);
  assert.deepEqual(t.form, {
    fingerprint: null,
    page_count: 2,
    page_sizes: [[612, 792], [600, 400]],
    kinds: ["fillable", "digital"],
  });
  assert.deepEqual(t.history, []);
  assert.deepEqual(t.schema, SCHEMA);
  assert.notEqual(t.schema, SCHEMA); // a copy, so later edits never reach the caller's object
});

test("the seed takes edit ops", () => {
  const t = seedTemplate(fakeDoc(), SCHEMA, DETECTIONS, LINES);
  const { template, warnings } = applyOp(t, { op: "set_label", id: "c1_2", label: "Date" }, { author: "human" });
  assert.equal(template.fields.find((f) => f.id === "c1_2").label, "Date");
  assert.deepEqual(warnings, []);
  assert.equal(template.history.length, 1);
  const linked = applyOp(t, { op: "set_source", id: "c1_1", source: { paths: ["agree"] } }, { author: "human" });
  assert.deepEqual(linked.template.fields.find((f) => f.id === "c1_1").source.paths, ["agree"]);
});
