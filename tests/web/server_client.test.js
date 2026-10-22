// engines/server/client.js: the server engine over a stub `fetch`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { createBus } from "../../demo/web/core/bus.js";
import { assertEngine } from "../../demo/web/core/engine.js";
import { createSession } from "../../demo/web/core/session.js";
import { EngineError, createServerEngine } from "../../demo/web/engines/server/client.js";

const CAPS = { ai: true, matches: true, store: true, records: true, scanned: true, detectLive: false };

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const b64 = (text) => Buffer.from(text).toString("base64");

/**
 * A stub fetch: `routes` maps "METHOD /path" to a response (or a function of the request, or an
 * array answered in turn). `calls` records {method, url, body, headers, signal}.
 */
function stub(routes) {
  const calls = [];
  const queues = new Map();
  async function fetchStub(url, init = {}) {
    const method = init.method ?? "GET";
    const key = `${method} ${url}`;
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    calls.push({ method, url, body, headers: init.headers, signal: init.signal });
    let route = routes[key];
    if (route === undefined) return json(404, { error: "not_found", detail: `no stub for ${key}` });
    if (Array.isArray(route)) {
      if (!queues.has(key)) queues.set(key, [...route]);
      const queue = queues.get(key);
      route = queue.length > 1 ? queue.shift() : queue[0];
    }
    if (route instanceof Response) return route.clone(); // a stub may answer many times
    return route({ method, url, body, init });
  }
  fetchStub.calls = calls;
  return fetchStub;
}

async function engineWith(routes, opts = {}) {
  const fetch = stub({ "GET /api/capabilities": json(200, CAPS), ...routes });
  const engine = await createServerEngine({ fetch, pollMs: 0, ...opts });
  fetch.calls.length = 0;
  return { engine, fetch };
}

const TEMPLATE = { name: "t", fields: [], history: [] };
const DOC_SUMMARY = {
  id: "d".repeat(32),
  name: "fw9.pdf",
  page_count: 2,
  page_sizes: [[612, 792], [200, 100]],
  rotations: [0, 90],
  kinds: ["fillable", "digital"],
};

test("the engine passes assertEngine, takes its flags from the server and never offers detect", async () => {
  const { engine } = await engineWith({});
  assertEngine(engine);
  assert.deepEqual(engine.capabilities, { detectLive: false, ai: true, matches: true, store: true, records: true, scanned: true });
  assert.equal(engine.detect, undefined);
  const off = await engineWith({ "GET /api/capabilities": json(200, { ...CAPS, ai: false, detectLive: true }) });
  assert.equal(off.engine.capabilities.ai, false);
  assert.equal(off.engine.capabilities.detectLive, false);
});

test("an unreachable server fails creation with a plain message", async () => {
  const fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(createServerEngine({ fetch }), (e) => e instanceof EngineError && e.code === "network" && /Couldn't reach the workbench/.test(e.message));
});

test("apply posts the template, op, author, note and doc id, and returns the template and warnings", async () => {
  const { engine, fetch } = await engineWith({ "POST /api/apply": json(200, { template: { ...TEMPLATE, name: "u" }, warnings: ["w"] }) });
  const doc = { id: "abc" };
  const res = await engine.apply(TEMPLATE, { op: "move" }, "human", { doc, note: "n" });
  assert.deepEqual(res, { template: { ...TEMPLATE, name: "u" }, warnings: ["w"] });
  assert.deepEqual(fetch.calls[0].body, { template: TEMPLATE, op: { op: "move" }, author: "human", note: "n", doc: "abc" });
  await engine.apply(TEMPLATE, { op: "move" });
  assert.deepEqual(fetch.calls[1].body, { template: TEMPLATE, op: { op: "move" }, author: "human", note: "" }); // no doc key
});

test("apply maps a 422 op_rejected to {error: detail}", async () => {
  const { engine } = await engineWith({ "POST /api/apply": json(422, { error: "op_rejected", detail: "field f1 is not on page 3" }) });
  assert.deepEqual(await engine.apply(TEMPLATE, { op: "move" }), { error: "field f1 is not on page 3" });
});

test("apply resolves {error} for a bad template, a 500 and a dropped connection too", async () => {
  let { engine } = await engineWith({ "POST /api/apply": json(400, { error: "bad_template", detail: "this is not a valid template: fields: required" }) });
  assert.deepEqual(await engine.apply(TEMPLATE, {}), { error: "this is not a valid template: fields: required" });
  ({ engine } = await engineWith({ "POST /api/apply": new Response("<html>oops</html>", { status: 502 }) }));
  const res = await engine.apply(TEMPLATE, {});
  assert.match(res.error, /workbench ran into a problem/);
  ({ engine } = await engineWith({
    "POST /api/apply": () => {
      throw new TypeError("Failed to fetch");
    },
  }));
  assert.match((await engine.apply(TEMPLATE, {})).error, /Couldn't reach the workbench/);
});

test("the session can use the server engine: an op and a refused op", async () => {
  const { engine } = await engineWith({
    "POST /api/apply": ({ body }) => (body.op.op === "bad" ? json(422, { error: "op_rejected", detail: "no" }) : json(200, { template: { ...body.template, fields: [{ id: "a" }] }, warnings: [] })),
  });
  const bus = createBus();
  const errors = [];
  bus.on("error", (e) => errors.push(e.message));
  const session = createSession({ engine, bus });
  session.setDocument({ id: "abc" }, TEMPLATE);
  assert.equal((await session.apply({ op: "add" })).template.fields.length, 1);
  assert.equal((await session.apply({ op: "bad" })).error, "no");
  assert.deepEqual(errors, ["no"]);
});

test("a request that never answers times out with EngineError timeout", async () => {
  const hang = (url, init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason)));
  const fetch = async (url, init) => (url === "/api/capabilities" ? json(200, CAPS) : hang(url, init));
  const slow = await createServerEngine({ fetch, timeoutMs: 20, uploadTimeoutMs: 20 });
  await assert.rejects(slow.seed({ id: "x" }, {}), (e) => e instanceof EngineError && e.code === "timeout" && /didn't answer in time/.test(e.message));
  assert.match((await slow.apply(TEMPLATE, {})).error, /didn't answer in time/);
  await assert.rejects(slow.open(new Uint8Array([1, 2, 3])), (e) => e.code === "timeout");
});

test("every request carries a timeout signal; uploads get a longer one", async () => {
  const { engine, fetch } = await engineWith({
    "POST /api/seed": json(200, { template: TEMPLATE }),
    "POST /api/documents": json(200, DOC_SUMMARY),
  });
  await engine.seed({ id: "x" }, {});
  assert.ok(fetch.calls[0].signal instanceof AbortSignal);
  await engine.open(new Uint8Array([37, 80, 68, 70]), { name: "a.pdf" });
  assert.ok(fetch.calls[1].signal instanceof AbortSignal);
});

test("open: a form name posts JSON, bytes post a multipart file, and the ServerDoc has page views", async () => {
  const { engine, fetch } = await engineWith({ "POST /api/documents": json(200, DOC_SUMMARY) });
  const doc = await engine.open("fw9");
  assert.deepEqual(fetch.calls[0].body, { form: "fw9" });
  assert.equal(doc.id, DOC_SUMMARY.id);
  assert.equal(doc.pageCount, 2);
  assert.equal(doc.pages.length, 2);
  assert.deepEqual(doc.pages.map((p) => [p.width, p.height, p.rotation, p.kind]), [[612, 792, 0, "fillable"], [200, 100, 90, "digital"]]);
  assert.equal(doc.pages[1].imageUrl(72), `/api/documents/${DOC_SUMMARY.id}/pages/1.png?dpi=72`);
  assert.equal(doc.pages[0].imageUrl(), `/api/documents/${DOC_SUMMARY.id}/pages/0.png?dpi=110`);
  assert.equal(await doc.pages[0].render({}, 1.5), `/api/documents/${DOC_SUMMARY.id}/pages/0.png?dpi=108`);
  assert.equal(await doc.pages[0].render({}, 9), `/api/documents/${DOC_SUMMARY.id}/pages/0.png?dpi=200`); // the server's own ceiling
  await doc.destroy(); // nothing to release; the session calls it on a new document

  await engine.open(new Blob(["%PDF-1.4"]), { name: "mine.pdf" });
  const form = fetch.calls[1].body;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("file").name, "mine.pdf");
  assert.equal(fetch.calls[1].headers["Content-Type"], undefined); // the browser sets the boundary
});

test("open with a base URL puts it in every URL", async () => {
  const { engine, fetch } = await engineWith({ "POST http://h:1/api/documents": json(200, DOC_SUMMARY), "GET http://h:1/api/capabilities": json(200, CAPS) }, { baseUrl: "http://h:1/" });
  const doc = await engine.open("fw9");
  assert.equal(fetch.calls[0].url, "http://h:1/api/documents");
  assert.equal(doc.pages[0].imageUrl(36), `http://h:1/api/documents/${DOC_SUMMARY.id}/pages/0.png?dpi=36`);
});

test("a server without rotations is read as upright pages", async () => {
  const { rotations, ...old } = DOC_SUMMARY;
  const { engine } = await engineWith({ "POST /api/documents": json(200, old) });
  assert.deepEqual((await engine.open("x")).pages.map((p) => p.rotation), [0, 0]);
});

test("server errors keep their code and detail", async () => {
  const { engine } = await engineWith({ "POST /api/documents": json(400, { error: "bad_pdf", detail: "This file can't be used: encrypted." }) });
  await assert.rejects(engine.open("x"), (e) => e instanceof EngineError && e.code === "bad_pdf" && e.message === "This file can't be used: encrypted." && e.status === 400);
});

test("seed posts the doc id and the schema (an object or a stored name)", async () => {
  const { engine, fetch } = await engineWith({ "POST /api/seed": json(200, { template: TEMPLATE }) });
  assert.deepEqual(await engine.seed({ id: "abc" }, "fw9"), TEMPLATE);
  assert.deepEqual(fetch.calls[0].body, { doc: "abc", schema: "fw9" });
});

test("fill decodes the PDF and sends a record, a reference or the fingerprint option", async () => {
  const { engine, fetch } = await engineWith({
    "POST /api/fill": json(200, { pdf_base64: b64("%PDF-out"), report: { counts: {} }, fingerprint_note: "skipped" }),
  });
  const doc = { id: "abc" };
  const out = await engine.fill(TEMPLATE, doc, { name: "Ada" });
  assert.equal(Buffer.from(out.pdfBytes).toString(), "%PDF-out");
  assert.deepEqual(out.report, { counts: {} });
  assert.equal(out.note, "skipped");
  assert.deepEqual(fetch.calls[0].body, { template: TEMPLATE, doc: "abc", record: { name: "Ada" } });
  await engine.fill(TEMPLATE, doc, { schema: "fw9", id: "7" }, { checkFingerprint: false });
  assert.deepEqual(fetch.calls[1].body, { template: TEMPLATE, doc: "abc", record_ref: { schema: "fw9", id: "7" }, check_fingerprint: false });
  await engine.fill(TEMPLATE, doc, { schema: "x", id: "y", other: 1 }); // more keys: a record
  assert.deepEqual(fetch.calls[2].body.record, { schema: "x", id: "y", other: 1 });
  await engine.fill(TEMPLATE, doc, null, { recordRef: { schema: "fw9", id: "2" } });
  assert.deepEqual(fetch.calls[3].body.record_ref, { schema: "fw9", id: "2" });
});

test("a fill for another version of the PDF rejects with fingerprint_mismatch; rebind posts to /api/rebind", async () => {
  const { engine, fetch } = await engineWith({
    "POST /api/fill": json(409, { error: "fingerprint_mismatch", detail: "Rebind it to this PDF first." }),
    "POST /api/smart-pdf": json(409, { error: "fingerprint_mismatch", detail: "Rebind it to this PDF first." }),
    "POST /api/rebind": json(200, { template: { ...TEMPLATE, rebound: true } }),
  });
  const doc = { id: "abc" };
  await assert.rejects(engine.fill(TEMPLATE, doc, {}), (e) => e.code === "fingerprint_mismatch" && e.message === "Rebind it to this PDF first." && e.status === 409);
  await assert.rejects(engine.smartPdf(TEMPLATE, doc), (e) => e.code === "fingerprint_mismatch");
  assert.deepEqual(await engine.rebind(TEMPLATE, doc), { ...TEMPLATE, rebound: true });
  assert.deepEqual(fetch.calls[2].body, { template: TEMPLATE, doc: "abc" });
});

test("smartPdf returns bytes and passes checkFingerprint through", async () => {
  const { engine, fetch } = await engineWith({ "POST /api/smart-pdf": json(200, { pdf_base64: b64("%PDF-smart") }) });
  const bytes = await engine.smartPdf(TEMPLATE, { id: "abc" }, { checkFingerprint: false });
  assert.equal(Buffer.from(bytes).toString(), "%PDF-smart");
  assert.equal(fetch.calls[0].body.check_fingerprint, false);
});

test("the job done promise resolves after running, running, done", async () => {
  const job = (status) => json(200, { id: "j".repeat(32), kind: "build", status, steps: [], template: status === "done" ? TEMPLATE : null, summary: "", error: null, started: 1, ended: null, unplaceable: [] });
  const { engine, fetch } = await engineWith({
    "POST /api/jobs": json(200, { id: "j".repeat(32) }),
    [`GET /api/jobs/${"j".repeat(32)}`]: [job("running"), job("running"), job("done")],
  });
  const handle = await engine.build({ id: "abc" }, { type: "object" });
  assert.equal(handle.id, "j".repeat(32));
  assert.deepEqual(fetch.calls[0].body, { kind: "build", doc: "abc", schema: { type: "object" } });
  const done = await handle.done;
  assert.equal(done.status, "done");
  assert.deepEqual(done.template, TEMPLATE);
  assert.equal(fetch.calls.filter((c) => c.method === "GET").length, 3);
  assert.equal((await handle.poll()).status, "done"); // a later poll still works
});

test("onUpdate sees the job after every poll of done, without extra requests", async () => {
  const job = (status, n) => json(200, { id: "j".repeat(32), kind: "build", status, steps: Array.from({ length: n }, (_, i) => ({ step: i + 1 })), template: null, summary: "", error: null, started: 1, ended: null, unplaceable: [] });
  const { engine, fetch } = await engineWith({
    "POST /api/jobs": json(200, { id: "j".repeat(32) }),
    [`GET /api/jobs/${"j".repeat(32)}`]: [job("running", 0), job("running", 1), job("running", 2), job("done", 3)],
  }, { pollMs: 5 });
  const handle = await engine.build({ id: "abc" }, { type: "object" });
  const seen = [];
  handle.onUpdate = (j) => seen.push(`${j.status}:${j.steps.length}`);
  await handle.done;
  assert.deepEqual(seen.slice(-2), ["running:2", "done:3"]);
  assert.equal(fetch.calls.filter((c) => c.method === "GET").length, 4);
});

test("smartPdf withNote resolves the bytes and the server's fingerprint note", async () => {
  const note = "This template has no fingerprint, so the check was skipped.";
  const { engine } = await engineWith({ "POST /api/smart-pdf": json(200, { pdf_base64: b64("%PDF-smart"), fingerprint_note: note }) });
  const out = await engine.smartPdf(TEMPLATE, { id: "abc" }, { withNote: true });
  assert.equal(Buffer.from(out.pdfBytes).toString(), "%PDF-smart");
  assert.equal(out.note, note);
  const plain = await engine.smartPdf(TEMPLATE, { id: "abc" });
  assert.ok(plain instanceof Uint8Array);
});

test("done also resolves for cancelled and error jobs, and polls every pollMs", async () => {
  const final = (status) => json(200, { id: "j".repeat(32), kind: "build", status, steps: [], template: null, summary: "", error: status === "error" ? "The model call failed." : null, started: 1, ended: 2, unplaceable: [] });
  for (const status of ["cancelled", "error"]) {
    const { engine } = await engineWith({ "POST /api/jobs": json(200, { id: "j".repeat(32) }), [`GET /api/jobs/${"j".repeat(32)}`]: final(status) });
    const done = await (await engine.build({ id: "abc" }, {})).done;
    assert.equal(done.status, status);
  }
  const times = [];
  const { engine } = await engineWith(
    {
      "POST /api/jobs": json(200, { id: "j".repeat(32) }),
      [`GET /api/jobs/${"j".repeat(32)}`]: () => {
        times.push(Date.now());
        return times.length < 3 ? json(200, { status: "running" }) : final("done");
      },
    },
    { pollMs: 40 },
  );
  await (await engine.build({ id: "abc" }, {})).done;
  assert.ok(times[1] - times[0] >= 35 && times[2] - times[1] >= 35, `polls ${times.map((t) => t - times[0])}`);
});

test("cancel posts to the cancel route and returns the job", async () => {
  const id = "j".repeat(32);
  const { engine, fetch } = await engineWith({
    "POST /api/jobs": json(200, { id }),
    [`GET /api/jobs/${id}`]: json(200, { id, status: "cancelled" }),
    [`POST /api/jobs/${id}/cancel`]: json(200, { id, status: "running" }),
  });
  const handle = await engine.build({ id: "abc" }, {});
  assert.equal((await handle.cancel()).status, "running");
  assert.ok(fetch.calls.some((c) => c.method === "POST" && c.url === `/api/jobs/${id}/cancel`));
  assert.equal((await handle.done).status, "cancelled");
});

test("a build with startFrom is an adapt job; revise sends instruction and region", async () => {
  const { engine, fetch } = await engineWith({ "POST /api/jobs": json(200, { id: "j".repeat(32) }), [`GET /api/jobs/${"j".repeat(32)}`]: json(200, { status: "done" }) });
  const a = await engine.build({ id: "abc" }, null, { startFrom: TEMPLATE });
  assert.deepEqual(fetch.calls[0].body, { kind: "adapt", doc: "abc", template: TEMPLATE });
  assert.equal(a.kind, "adapt");
  const r = await engine.revise(TEMPLATE, { id: "abc" }, "split the name", { page: 0, rect: [1, 2, 3, 4] });
  assert.deepEqual(fetch.calls.find((c) => c.body?.kind === "revise").body, { kind: "revise", doc: "abc", template: TEMPLATE, instruction: "split the name", region: { page: 0, rect: [1, 2, 3, 4] } });
  await engine.revise(TEMPLATE, { id: "abc" }, "again");
  assert.equal("region" in fetch.calls.filter((c) => c.body?.kind === "revise")[1].body, false);
  await Promise.all([a.done, r.done]);
});

test("starting a job surfaces busy and ai_not_configured", async () => {
  const { engine } = await engineWith({ "POST /api/jobs": [json(429, { error: "busy", detail: "Two builds are already running." }), json(400, { error: "ai_not_configured", detail: "AI is not configured." })] });
  await assert.rejects(engine.build({ id: "a" }, {}), (e) => e.code === "busy" && e.status === 429);
  await assert.rejects(engine.revise(TEMPLATE, { id: "a" }, "x"), (e) => e.code === "ai_not_configured");
});

test("done survives one failed poll but rejects when the server stays down", async () => {
  const id = "j".repeat(32);
  let n = 0;
  let { engine } = await engineWith({
    "POST /api/jobs": json(200, { id }),
    [`GET /api/jobs/${id}`]: () => {
      if (++n === 1) throw new TypeError("Failed to fetch");
      return json(200, { id, status: "done" });
    },
  });
  assert.equal((await (await engine.build({ id: "a" }, {})).done).status, "done");
  ({ engine } = await engineWith({
    "POST /api/jobs": json(200, { id }),
    [`GET /api/jobs/${id}`]: () => {
      throw new TypeError("Failed to fetch");
    },
  }));
  await assert.rejects((await engine.build({ id: "a" }, {})).done, (e) => e.code === "network");
});

test("matches returns the bare list", async () => {
  const list = [{ name: "fw9", score: 0.99, action: "reuse" }];
  const { engine, fetch } = await engineWith({ "GET /api/documents/abc/matches": json(200, list) });
  assert.deepEqual(await engine.matches({ id: "abc" }), list);
  assert.equal(fetch.calls[0].url, "/api/documents/abc/matches");
});

test("templates: list, get, put, delete with encoded names", async () => {
  const { engine, fetch } = await engineWith({
    "GET /api/templates": json(200, { templates: [{ name: "a" }] }),
    "GET /api/templates/a%20b": json(200, { template: TEMPLATE }),
    "PUT /api/templates/a%20b": json(200, { name: "a b", template_id: "tid" }),
    "DELETE /api/templates/a%20b": json(200, { deleted: "a b" }),
  });
  assert.deepEqual(await engine.templates.list(), [{ name: "a" }]);
  assert.deepEqual(await engine.templates.get("a b"), TEMPLATE);
  assert.deepEqual(await engine.templates.put("a b", TEMPLATE), { name: "a b", template_id: "tid" });
  assert.deepEqual(fetch.calls[2].body, { template: TEMPLATE });
  assert.equal(await engine.templates.delete("a b"), "a b");
});

test("a template name with a slash is encoded, not a path", async () => {
  const { engine, fetch } = await engineWith({ "GET /api/templates/..%2Fx": json(400, { error: "bad_request", detail: "bad template name" }) });
  await assert.rejects(engine.templates.get("../x"), (e) => e.code === "bad_request");
  assert.equal(fetch.calls[0].url, "/api/templates/..%2Fx");
});

test("records: schemas, list and get", async () => {
  const { engine, fetch } = await engineWith({
    "GET /api/schemas": json(200, { schemas: { fw9: { type: "object" } } }),
    "GET /api/records?schema=fw9": json(200, { schema: "fw9", records: [{ id: "1" }] }),
    "GET /api/records/fw9/1": json(200, { record: { id: "1", name: "Ada" } }),
  });
  assert.deepEqual(await engine.records.schemas(), { fw9: { type: "object" } });
  assert.deepEqual(await engine.records.list("fw9"), [{ id: "1" }]);
  assert.deepEqual(await engine.records.get("fw9", "1"), { id: "1", name: "Ada" });
  assert.equal(fetch.calls.length, 3);
});

test("forms lists the demo forms", async () => {
  const { engine } = await engineWith({ "GET /api/forms": json(200, { forms: [{ name: "fw9", size: 1, schema: "fw9" }] }) });
  assert.deepEqual(await engine.forms(), [{ name: "fw9", size: 1, schema: "fw9" }]);
});
