// showcase/automap.js: carrying the AI agent's mapping onto detected boxes by position.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { MATCH_IOU, NOTE, carryMapping, pairByOverlap } from "../../demo/web/showcase/automap.js";

const SAMPLES = new URL("../../demo/web/showcase/samples/", import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, SAMPLES), "utf8"));

const STYLE = { font_size: "auto", min_font_size: 6, align: "left", comb: null, multiline: false };
const SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string" },
    city: { type: "string" },
    zip: { type: "string" },
    kind: { type: "string", enum: ["a", "b"] },
  },
};

/** A field as seeded (no source) or as the agent left it. */
function field(id, page, rect, { type = "text", source = null, style = STYLE } = {}) {
  return { id, type, label: "", page, rect, source, style: { ...style }, existing_widget: null, segments: [], confidence: null, origin: "detector" };
}

function template(fields) {
  return {
    formcast_version: "1",
    template_id: "t",
    schema: structuredClone(SCHEMA),
    form: { fingerprint: null, page_count: 2, page_sizes: [[612, 792], [612, 792]], kinds: ["digital", "digital"] },
    fields,
    history: [],
  };
}

const src = (paths, extra = {}) => ({ paths, transforms: [], join: null, when: null, ...extra });

test("the match threshold is the eval's", () => {
  assert.equal(MATCH_IOU, 0.3);
});

test("pairByOverlap pairs best overlap first, one to one, at or above the threshold", () => {
  const a = [[0, 0, 10, 10], [20, 0, 30, 10]];
  const b = [[1, 0, 11, 10], [0, 0, 10, 10], [50, 50, 60, 60]];
  // a0 overlaps b1 exactly and b0 closely; the exact one wins and b0 goes unpaired
  assert.deepEqual(pairByOverlap(a, b, 0.5), [[0, 1]]);
  assert.deepEqual(pairByOverlap([[0, 0, 10, 10]], [[5, 0, 15, 10]], 0.5), []); // IoU 1/3
  assert.deepEqual(pairByOverlap([[0, 0, 10, 10]], [[5, 0, 15, 10]], 0.3), [[0, 0]]);
});

test("pairByOverlap needs its threshold", () => {
  assert.throws(() => pairByOverlap([], []), TypeError);
  assert.throws(() => pairByOverlap([], [], 0), TypeError);
});

test("the history entries Auto map adds carry the agent as author, one per op", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 108, 108]), field("c0_1", 0, [100, 200, 300, 220])]);
  const when = { op: "equals", args: { value: "a" } };
  const agent = template([
    field("w0_0", 0, [100, 100, 108, 108], { type: "checkbox", source: src(["kind"], { when }) }),
    field("w0_1", 0, [100, 200, 300, 220], { source: src(["name"]) }),
  ]);
  boxes.history.push({ op: { op: "move", id: "c0_0" }, author: "human", at: "t", note: "" });
  const { template: out } = carryMapping(boxes, agent);
  assert.equal(out.history[0].author, "human", "earlier entries are left alone");
  const added = out.history.slice(1);
  assert.deepEqual(added.map((h) => [h.op.op, h.op.id, h.author]), [
    ["set_type", "c0_0", "agent"],
    ["set_source", "c0_0", "agent"],
    ["set_style", "c0_0", "agent"],
    ["set_source", "c0_1", "agent"],
    ["set_style", "c0_1", "agent"],
  ]);
  assert.ok(added.every((h) => h.note === NOTE));
});

test("a box takes the source and style of the agent field it overlaps; its own rect stays", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120]), field("c0_1", 0, [100, 200, 300, 220])]);
  const agent = template([
    field("w0_0", 0, [101, 99, 302, 121], { source: src(["name"]) }),
    field("w0_1", 0, [100, 201, 299, 219], { source: src(["city", "zip"], { join: ", " }), style: { ...STYLE, comb: 5 } }),
  ]);
  const { template: out, mapped, boxes: total } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, ["c0_0", "c0_1"]);
  assert.equal(total, 2);
  assert.deepEqual(out.fields[0].source, src(["name"]));
  assert.deepEqual(out.fields[0].rect, [100, 100, 300, 120]);
  assert.deepEqual(out.fields[1].source, src(["city", "zip"], { join: ", " }));
  assert.equal(out.fields[1].style.comb, 5);
  assert.equal(boxes.fields[0].source, null, "the input template is untouched");
});

test("pairs stay on one page, and need IoU 0.3", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120]), field("c1_0", 1, [0, 0, 50, 10])]);
  const agent = template([
    field("w1_0", 1, [100, 100, 300, 120], { source: src(["name"]) }), // the same rect, another page
    field("w1_1", 1, [40, 0, 90, 10], { source: src(["city"]) }), // IoU 10/90 with c1_0
  ]);
  const { mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, []);
});

test("one agent field maps one box: the closer box wins, the other is left for hand linking", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120]), field("c0_1", 0, [110, 100, 310, 120])]);
  const agent = template([field("w0_0", 0, [110, 100, 310, 120], { source: src(["name"]) })]);
  const { template: out, mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, ["c0_1"]);
  assert.equal(out.fields[0].source, null);
});

test("agent fields with no source and tables are not carried", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120])]);
  const table = { ...field("t", null, null, { type: "table", source: src(["name"]) }), segments: [{ page: 0, rect: [100, 100, 300, 120] }] };
  const agent = template([field("w0_0", 0, [100, 100, 300, 120]), table]);
  const { mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, []);
});

test("a choice the agent marks with `when` turns the box into its type", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 108, 108])]);
  const when = { op: "equals", args: { value: "a" } };
  const agent = template([field("w0_0", 0, [100, 100, 108, 108], { type: "checkbox", source: src(["kind"], { when }) })]);
  const { template: out, mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, ["c0_0"]);
  assert.equal(out.fields[0].type, "checkbox");
  assert.deepEqual(out.fields[0].source.when, when);
});

test("a mapping the template's checks refuse leaves that box as it was", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120]), field("c0_1", 0, [100, 200, 300, 220])]);
  const agent = template([
    field("w0_0", 0, [100, 100, 300, 120], { source: src(["not.in.schema"]) }),
    field("w0_1", 0, [100, 200, 300, 220], { source: src(["name"]) }),
  ]);
  const { template: out, mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, ["c0_1"]);
  assert.equal(out.fields[0].source, null);
});

test("a box already linked by hand takes the agent's mapping too", () => {
  const boxes = template([field("c0_0", 0, [100, 100, 300, 120], { source: src(["city"]) })]);
  const agent = template([field("w0_0", 0, [100, 100, 300, 120], { source: src(["name"]) })]);
  const { template: out, mapped } = carryMapping(boxes, agent);
  assert.deepEqual(mapped, ["c0_0"]);
  assert.deepEqual(out.fields[0].source.paths, ["name"]);
});

test("on the real fw9 bundle the saved detections take most of the agent's mapping", () => {
  // the detections as the page seeds them: one box per candidate, no source
  const detections = read("fw9/detections.json").pages;
  const agent = read("fw9/agent-template.json");
  const fields = Object.entries(detections).flatMap(([page, list]) =>
    list.map((c, i) => field(`c${page}_${i}`, Number(page), c.rect, { type: c.kind === "choice" ? "checkbox" : "text" })),
  );
  const seeded = { ...template(fields), schema: agent.schema, form: agent.form };
  const { template: out, mapped, boxes } = carryMapping(seeded, agent);
  assert.equal(boxes, fields.length);
  const linkedByAgent = agent.fields.filter((f) => f.type !== "table" && f.source?.paths?.length).length;
  assert.ok(mapped.length >= linkedByAgent - 2, `${mapped.length} of ${linkedByAgent}`);
  for (const id of mapped) assert.ok(out.fields.find((f) => f.id === id).source.paths.length > 0);
});
