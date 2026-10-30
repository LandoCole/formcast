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
  const slow = await createServerEngine({ fetch, timeoutMs: 20, uploadTimeoutMs: 20, formMapTimeoutMs: 20 });
  await assert.rejects(slow.seed({ id: "x" }, {}), (e) => e instanceof EngineError && e.code === "timeout" && /didn't answer in time/.test(e.message));
  assert.match((await slow.apply(TEMPLATE, {})).error, /didn't answer in time/);
  await assert.rejects(slow.open(new Uint8Array([1, 2, 3])), (e) => e.code === "timeout");
});

test("seed, matches and an edit checked against the PDF wait longer, for the server's first reading of the form", async () => {
  // every answer comes after 80 ms: past the 20 ms request timeout, inside the 400 ms form-map one
  const late = (body) => (url, init) =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(json(200, body)), 80);
      init.signal.addEventListener("abort", () => {
        clearTimeout(timer);
        reject(init.signal.reason);
      });
    });
  const routes = {
    "/api/seed": late({ template: TEMPLATE }),
    "/api/apply": late({ template: TEMPLATE, warnings: [] }),
    [`/api/documents/${"d".repeat(32)}/matches`]: late([]),
    "/api/forms": late({ forms: [] }),
  };
  const fetch = async (url, init) => (url === "/api/capabilities" ? json(200, CAPS) : routes[url](url, init));
  const engine = await createServerEngine({ fetch, timeoutMs: 20, uploadTimeoutMs: 20, formMapTimeoutMs: 400 });
  const doc = { id: "d".repeat(32) };
  assert.deepEqual(await engine.seed(doc, "fw9"), TEMPLATE);
  assert.deepEqual(await engine.matches(doc), []);
  assert.deepEqual(await engine.apply(TEMPLATE, { op: "move" }, "human", { doc }), { template: TEMPLATE, warnings: [] });
  assert.match((await engine.apply(TEMPLATE, { op: "move" })).error, /didn't answer in time/); // no PDF to wait for
  await assert.rejects(engine.forms(), (e) => e.code === "timeout");
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

// ------------------------------------------------------------------ extraction

const EXTRACT_CAPS = { ...CAPS, extract: true };
const BATCH_ID = "b".repeat(32);
const SETUP_ID = "5".repeat(32);
const RUN_ID = "7".repeat(32);
const MiB = 2 ** 20;

/** The `file` parts of a multipart body, as [name, size]. */
const parts = (form) => form.getAll("file").map((f) => [f.name, f.size]);

test("engine.extract is there only when the server says extract, and passes assertEngine", async () => {
  const { engine } = await engineWith({});
  assert.equal(engine.extract, undefined);
  const on = await engineWith({ "GET /api/capabilities": json(200, EXTRACT_CAPS) });
  assertEngine(on.engine);
  assert.equal(typeof on.engine.extract.addFiles, "function");
  assert.deepEqual(on.engine.capabilities, { detectLive: false, ai: true, matches: true, store: true, records: true, scanned: true });
});

test("addFiles sends three files of 30 MiB in requests under 40 MiB each, to the batch's files route", async () => {
  let count = 0;
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/batches/${BATCH_ID}/files`]: ({ body }) => json(200, { id: BATCH_ID, count: (count += body.getAll("file").length) }),
  });
  const files = ["a.pdf", "b.pdf", "c.pdf"].map((name) => new File([new Uint8Array(30 * MiB)], name, { type: "application/pdf" }));
  const seen = [];
  const out = await engine.extract.addFiles(BATCH_ID, files, { onProgress: (p) => seen.push(p) });
  assert.equal(fetch.calls.length, 3);
  for (const call of fetch.calls) {
    assert.equal(call.url, `/api/batches/${BATCH_ID}/files`);
    assert.equal(call.method, "POST");
    assert.ok(parts(call.body).reduce((n, [, size]) => n + size, 0) < 40 * MiB);
  }
  assert.deepEqual(fetch.calls.map((c) => parts(c.body).map(([name]) => name)), [["a.pdf"], ["b.pdf"], ["c.pdf"]]);
  assert.equal(out.batch.count, 3); // the batch as the last request left it
  assert.deepEqual(out.tooLarge, []);
  assert.deepEqual(seen.map((p) => [p.done, p.total, p.bytes, p.totalBytes, p.batch.count]), [
    [1, 3, 30 * MiB, 90 * MiB, 1],
    [2, 3, 60 * MiB, 90 * MiB, 2],
    [3, 3, 90 * MiB, 90 * MiB, 3],
  ]);
});

test("addFiles puts small files together in one request and sends a file over the limit on its own", async () => {
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/batches/${BATCH_ID}/files`]: json(200, { id: BATCH_ID, count: 4 }),
  });
  const small = ["a.pdf", "b.pdf", "c.zip"].map((name) => new File([new Uint8Array(1000)], name));
  const big = new File([new Uint8Array(41 * MiB)], "big.zip");
  await engine.extract.addFiles(BATCH_ID, [...small, big]);
  assert.deepEqual(fetch.calls.map((c) => parts(c.body).map(([name]) => name)), [["a.pdf", "b.pdf", "c.zip"], ["big.zip"]]);
  await assert.rejects(engine.extract.addFiles(BATCH_ID, []), (e) => e instanceof EngineError && e.code === "bad_upload");
});

const TOO_LARGE = json(413, { error: "too_large", detail: "This request is larger than the 1 KB limit." });

test("addFiles sends a request the server finds too large again file by file, and skips and names a file too large alone", async () => {
  let count = 0;
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/batches/${BATCH_ID}/files`]: ({ body }) => {
      const names = body.getAll("file").map((f) => f.name);
      if (names.length > 1 || names.includes("big.zip")) return TOO_LARGE.clone();
      return json(200, { id: BATCH_ID, count: ++count });
    },
  });
  const files = ["a.pdf", "big.zip", "b.pdf"].map((name) => new File([new Uint8Array(100)], name));
  const seen = [];
  const out = await engine.extract.addFiles(BATCH_ID, files, { onProgress: (p) => seen.push(p) });
  assert.deepEqual(fetch.calls.map((c) => parts(c.body).map(([name]) => name)), [["a.pdf", "big.zip", "b.pdf"], ["a.pdf"], ["big.zip"], ["b.pdf"]]);
  assert.equal(out.batch.count, 2);
  assert.deepEqual(out.tooLarge, ["big.zip"]);
  assert.deepEqual(seen.map((p) => [p.done, p.total, p.bytes, p.tooLarge.length]), [[1, 3, 100, 0], [2, 3, 200, 1], [3, 3, 300, 1]]);
});

test("addFiles with every file too large resolves the batch as it is, and still rejects at another refusal", async () => {
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/batches/${BATCH_ID}/files`]: TOO_LARGE,
    [`GET /api/batches/${BATCH_ID}`]: json(200, { id: BATCH_ID, count: 0 }),
  });
  const out = await engine.extract.addFiles(BATCH_ID, [new File([new Uint8Array(10)], "big.zip")]);
  assert.deepEqual(out, { batch: { id: BATCH_ID, count: 0 }, tooLarge: ["big.zip"] });
  assert.deepEqual(fetch.calls.map((c) => c.method), ["POST", "GET"]);

  const refused = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/batches/${BATCH_ID}/files`]: json(400, { error: "bad_upload", detail: "That zip can't be opened." }),
  });
  await assert.rejects(refused.engine.extract.addFiles(BATCH_ID, [new File([new Uint8Array(10)], "x.zip")]), (e) => e.code === "bad_upload");
});

test("the batch, setup and saved extraction calls go to their routes with their bodies", async () => {
  const setup = { id: SETUP_ID, batch: BATCH_ID, template: {}, proof: {}, warnings: [], samples: [] };
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    "POST /api/batches": json(200, { id: BATCH_ID }),
    [`GET /api/batches/${BATCH_ID}`]: json(200, { id: BATCH_ID, count: 5 }),
    [`GET /api/batches/${BATCH_ID}/files?offset=50&limit=50`]: json(200, { total: 60, files: [{ index: 50 }] }),
    [`POST /api/batches/${BATCH_ID}/sample`]: json(200, { samples: [{ index: 0 }, { index: 2 }, { index: 4 }] }),
    [`GET /api/setups/${SETUP_ID}`]: json(200, setup),
    "POST /api/setups": json(200, setup),
    [`POST /api/setups/${SETUP_ID}/edit`]: json(200, setup),
    [`POST /api/setups/${SETUP_ID}/learn`]: json(200, setup),
    [`GET /api/setups/${SETUP_ID}/samples/2/marks`]: json(200, { marks: [{ path: "a", kind: "box" }] }),
    "PUT /api/extractions/Client%20info": json(200, { name: "Client info", template_id: "t" }),
    "GET /api/extractions": json(200, { extractions: [{ name: "Client info" }] }),
    "DELETE /api/extractions/Client%20info": json(200, { name: "Client info" }),
  });
  assert.deepEqual(await engine.extract.createBatch({ folder: "/data/clients" }), { id: BATCH_ID });
  assert.deepEqual(fetch.calls[0].body, { folder: "/data/clients" });
  assert.equal((await engine.extract.batch(BATCH_ID)).count, 5);
  assert.equal((await engine.extract.files(BATCH_ID, 50, 50)).total, 60);
  assert.deepEqual(await engine.extract.pickSample(BATCH_ID, { n: 3, how: "pick", picked: [0, 2, 4] }), [{ index: 0 }, { index: 2 }, { index: 4 }]);
  assert.deepEqual(fetch.calls[3].body, { n: 3, how: "pick", picked: [0, 2, 4] });
  assert.equal(engine.extract.pageUrl(BATCH_ID, 3, 1), `/api/batches/${BATCH_ID}/files/3/pages/1.png?dpi=110`);
  assert.equal(engine.extract.pageUrl(BATCH_ID, 3, 1, 72), `/api/batches/${BATCH_ID}/files/3/pages/1.png?dpi=72`);

  assert.equal((await engine.extract.setup(SETUP_ID)).id, SETUP_ID);
  await engine.extract.reopen("Client info");
  assert.deepEqual(fetch.calls.at(-1).body, { extraction: "Client info" });
  const edit = { op: "set_box", path: "investor.tax_id", page: 0, rect: [1, 2, 3, 4], sample: 3 };
  await engine.extract.edit(SETUP_ID, edit);
  assert.deepEqual(fetch.calls.at(-1).body, { edit }); // as given, its sample included
  await engine.extract.learn(SETUP_ID, "investor.country");
  assert.deepEqual(fetch.calls.at(-1).body, { path: "investor.country" });
  assert.deepEqual(await engine.extract.marks(SETUP_ID, 2), [{ path: "a", kind: "box" }]);
  assert.equal(engine.extract.samplePageUrl(SETUP_ID, 2, 0), `/api/setups/${SETUP_ID}/samples/2/pages/0.png?dpi=110`);

  assert.deepEqual(await engine.extract.save("Client info", SETUP_ID), { name: "Client info", template_id: "t" });
  assert.deepEqual(fetch.calls.at(-1).body, { setup: SETUP_ID });
  assert.deepEqual(await engine.extract.list(), [{ name: "Client info" }]);
  assert.equal(await engine.extract.remove("Client info"), "Client info");
});

test("the setup, line and run jobs start as extract jobs and poll like the others", async () => {
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    "POST /api/jobs": json(200, { id: "j1" }),
    "GET /api/jobs/j1": json(200, { id: "j1", kind: "extract-run", status: "done", stages: [], result: { run: RUN_ID } }),
  });
  const started = () => fetch.calls.filter((c) => c.method === "POST").at(-1).body; // the polls come after
  const spec = { name: "Client info", start: "ai", lines: [], destination: null, detect: "auto", pages: {}, ocr: "auto", sample_how: "spread" };
  const setupJob = await engine.extract.startSetup({ batch: BATCH_ID, samples: [0, 1, 2], spec });
  assert.deepEqual(started(), { kind: "extract-setup", batch: BATCH_ID, samples: [0, 1, 2], spec });
  assert.equal(setupJob.kind, "extract-setup");
  const lineJob = await engine.extract.startLine(SETUP_ID, "wire details");
  assert.equal(lineJob.kind, "extract-line");
  assert.deepEqual(started(), { kind: "extract-line", setup: SETUP_ID, line: "wire details" });
  const runJob = await engine.extract.startRun({ extraction: "Client info", batch: BATCH_ID });
  assert.deepEqual(started(), { kind: "extract-run", extraction: "Client info", batch: BATCH_ID, ai: false });
  assert.equal((await runJob.done).result.run, RUN_ID);
});

test("the run calls: list, open a folder, problems and set-aside paged, decisions, pages, export and to-data", async () => {
  const run = { id: RUN_ID, name: "r", summary: {}, fields: [] };
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    "GET /api/runs": json(200, { runs: [{ id: RUN_ID }] }),
    "POST /api/runs": json(200, run),
    [`GET /api/runs/${RUN_ID}`]: json(200, run),
    [`GET /api/runs/${RUN_ID}/problems?path=investor.tax_id&status=missing&offset=0&limit=50`]: json(200, { total: 1, documents: [] }),
    [`GET /api/runs/${RUN_ID}/problems?offset=50&limit=50`]: json(200, { total: 60, documents: [] }),
    [`GET /api/runs/${RUN_ID}/set-aside?offset=0&limit=20`]: json(200, { total: 2, documents: [] }),
    [`POST /api/runs/${RUN_ID}/decisions`]: json(200, run),
    [`POST /api/runs/${RUN_ID}/to-data`]: json(200, { dataset: { id: "d", name: "r results" }, truncated: false, with_problems: 3 }),
  });
  assert.deepEqual(await engine.extract.runs(), [{ id: RUN_ID }]);
  await engine.extract.openRun("/data/out");
  assert.deepEqual(fetch.calls.at(-1).body, { folder: "/data/out" });
  assert.equal((await engine.extract.run(RUN_ID)).id, RUN_ID);
  assert.equal((await engine.extract.problems(RUN_ID, { path: "investor.tax_id", status: "missing" })).total, 1);
  assert.equal((await engine.extract.problems(RUN_ID, { offset: 50 })).total, 60);
  assert.equal((await engine.extract.setAside(RUN_ID, { limit: 20 })).total, 2);
  const decision = { doc_id: "d1", path: "investor.tax_id", decision: "value", value: "84-2219037" };
  await engine.extract.decide(RUN_ID, decision);
  assert.deepEqual(fetch.calls.at(-1).body, decision);
  assert.equal(engine.extract.runPageUrl(RUN_ID, "doc 1", 0), `/api/runs/${RUN_ID}/pages.png?doc=doc%201&page=0&dpi=110`);
  assert.equal(engine.extract.exportUrl(RUN_ID, "parquet"), `/api/runs/${RUN_ID}/export?format=parquet`);
  assert.deepEqual(await engine.extract.toData(RUN_ID), { dataset: { id: "d", name: "r results" }, truncated: false, with_problems: 3 });
});

test("exportRun downloads the run's file with its name and type, and a refusal rejects with its code", async () => {
  const xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const { engine, fetch } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`GET /api/runs/${RUN_ID}/export?format=xlsx`]: new Response(new Uint8Array([80, 75, 3, 4]), {
      status: 200,
      headers: { "Content-Type": xlsx, "Content-Disposition": 'attachment; filename="Client forms-results.xlsx"' },
    }),
    [`GET /api/runs/${RUN_ID}/export?format=parquet`]: json(400, { error: "not_supported", detail: "Parquet export needs pyarrow: pip install formcast[parquet]" }),
  });
  const file = await engine.extract.exportRun(RUN_ID, "xlsx");
  assert.deepEqual([...file.bytes], [80, 75, 3, 4]);
  assert.equal(file.type, xlsx);
  assert.equal(file.filename, "Client forms-results.xlsx");
  assert.equal(fetch.calls.at(-1).method, "GET");
  await assert.rejects(engine.extract.exportRun(RUN_ID, "parquet"), (e) => e instanceof EngineError && e.code === "not_supported" && /pyarrow/.test(e.message));
});

test("runPage fetches a page of a run's document, and a page past its last rejects as not_found with its status", async () => {
  const { engine } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`GET /api/runs/${RUN_ID}/pages.png?doc=a.pdf&page=0&dpi=40`]: new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "Content-Type": "image/png" } }),
    [`GET /api/runs/${RUN_ID}/pages.png?doc=a.pdf&page=3&dpi=40`]: json(404, { error: "not_found", detail: "a.pdf has no page 4" }),
    [`GET /api/runs/${RUN_ID}/pages.png?doc=a.pdf&page=1&dpi=40`]: json(422, { error: "bad_pdf", detail: "a.pdf can't be shown" }),
  });
  const png = await engine.extract.runPage(RUN_ID, "a.pdf", 0, 40);
  assert.deepEqual([...png.bytes], [137, 80, 78, 71]);
  assert.equal(png.type, "image/png");
  await assert.rejects(engine.extract.runPage(RUN_ID, "a.pdf", 3, 40), (e) => e instanceof EngineError && e.code === "not_found" && e.status === 404);
  await assert.rejects(engine.extract.runPage(RUN_ID, "a.pdf", 1, 40), (e) => e.code === "bad_pdf" && e.status === 422);
});

test("extraction reads a saved extraction template by name", async () => {
  const template = { name: "Client info", setup: { destination: "fw9" } };
  const { engine } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    "GET /api/extractions/Client%20info": json(200, { template }),
  });
  assert.deepEqual(await engine.extract.extraction("Client info"), template);
});

test("an extraction call the server refuses rejects with its code, as every engine call does", async () => {
  const { engine } = await engineWith({
    "GET /api/capabilities": json(200, EXTRACT_CAPS),
    [`POST /api/setups/${SETUP_ID}/edit`]: json(409, { error: "busy_setup", detail: "A field is being added to this setup; try again when it is done." }),
    [`GET /api/setups/${SETUP_ID}`]: json(404, { error: "not_found", detail: `no such setup: ${SETUP_ID}` }),
  });
  await assert.rejects(engine.extract.edit(SETUP_ID, { op: "remove", path: "a" }), (e) => e instanceof EngineError && e.code === "busy_setup" && e.status === 409);
  await assert.rejects(engine.extract.setup(SETUP_ID), (e) => e.code === "not_found" && /no such setup/.test(e.message));
});
