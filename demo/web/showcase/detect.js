// The Detect step: pick a sample form or drop a PDF, run the detector live, compare the boxes with
// the form's own fields, and open the AI agent's template for the sample.
//
// A sample is a folder of files (Task 13 builds them; see demo/tools/build_showcase.py):
// manifest.json, form.pdf (fields stripped), detections.json, original-fields.json, schema.json,
// record.json, published.pdf, agent-template.json, agent-scores.json. The first five load with the
// sample; the agent's three load when asked for.

import { iou } from "../core/geometry.js";
import { checkTemplate, checkTemplateFits, recall, remember } from "../core/store.js";
import { fetchWithin, seconds, withinTime } from "./limits.js";

/**
 * How many of `originals` some box overlaps with IoU >= `threshold`, pairing each box with at most
 * one original (greedy, best overlap first).
 * @param {number[][]} originals rects
 * @param {number[][]} boxes rects
 * @param {number} [threshold]
 * @returns {number}
 */
export function matchCount(originals, boxes, threshold = 0.5) {
  const pairs = [];
  originals.forEach((o, i) => {
    boxes.forEach((b, j) => {
      const v = iou(o, b);
      if (v >= threshold) pairs.push([v, i, j]);
    });
  });
  pairs.sort((a, b) => b[0] - a[0]);
  const usedO = new Set();
  const usedB = new Set();
  for (const [, i, j] of pairs) {
    if (usedO.has(i) || usedB.has(j)) continue;
    usedO.add(i);
    usedB.add(j);
  }
  return usedO.size;
}

const PROVIDERS = { webgpu: "WebGPU", wasm: "WebAssembly" };

const MODEL_MB = { S: 38, L: 102 };

/** "FFDNet-S-onnx" -> "FFDNet-S". */
function modelName(model) {
  return String(model ?? "the detector").replace(/-onnx$/i, "");
}

const fixed = (v) => (typeof v === "number" ? v.toFixed(2) : "–");

/** The agent session's scores in one line, from agent-scores.json (a row of the eval results). */
export function scoresLine(s) {
  const parts = [
    `detection recall ${fixed(s.detection?.["iou0.5"]?.recall)}`,
    `path accuracy ${fixed(s.mapping?.path_acc)}`,
    `joint ${fixed(s.mapping?.joint_acc)}`,
    `fill accuracy ${fixed(s.fill?.fill_acc)}`,
  ];
  if (typeof s.steps === "number") parts.push(`${s.steps} steps`);
  return `Built by ${s.model_label ?? s.model} in the dev eval (run ${s.run}). That session: ${parts.join(" · ")}.`;
}

/** @param {object} app see showcase.js */
export function mountDetect(app) {
  const { $, engine, session, state, say } = app;
  const msg = $("detect-msg");
  const status = (text) => {
    $("detect-status").textContent = text;
  };
  const getJson = (url) => fetchWithin(url, app.fetchTimeoutMs, (res) => res.json());
  const getBytes = (url) => fetchWithin(url, app.fetchTimeoutMs, async (res) => new Uint8Array(await res.arrayBuffer()));
  let index = [];
  const cache = new Map(); // sample id -> its files
  let run = null; // the live detection in progress: {token} of the document it runs on
  let confirmed = false; // the visitor accepted the download where live detection is off
  let opening = 0; // bumps on every open, so only the latest one is shown
  let restored = false;

  /** True while a live detection runs on the document on screen (a run on an older one doesn't count). */
  const detecting = () => run !== null && run.token === session.generation;
  /** Show a status line for document `token` only while it is still the one on screen. */
  const statusFor = (token, text) => {
    if (token === session.generation) status(text);
  };

  // ---------------------------------------------------------------- samples

  async function loadSample(id) {
    if (cache.has(id)) return cache.get(id);
    const base = new URL(`${encodeURIComponent(id)}/`, app.samplesBase);
    const [manifest, detections, originals, schema, record, bytes] = await Promise.all([
      getJson(new URL("manifest.json", base)),
      getJson(new URL("detections.json", base)),
      getJson(new URL("original-fields.json", base)),
      getJson(new URL("schema.json", base)),
      getJson(new URL("record.json", base)),
      getBytes(new URL("form.pdf", base)),
    ]);
    const sample = { id, base, manifest, detections, originals, schema, record, bytes, agent: null };
    cache.set(id, sample);
    return sample;
  }

  async function loadAgent(sample) {
    if (sample.agent === null) {
      const [bytes, template, scores] = await Promise.all([
        getBytes(new URL("published.pdf", sample.base)),
        getJson(new URL("agent-template.json", sample.base)),
        getJson(new URL("agent-scores.json", sample.base)),
      ]);
      sample.agent = { bytes, template, scores };
    }
    return sample.agent;
  }

  /**
   * The remembered template for a sample variant when it is a template that still fits the PDF,
   * else null. One that doesn't (say, saved by an older page) is forgotten.
   */
  function restore(sample, variant, doc) {
    const key = `${sample.id}/${variant}`;
    const t = recall(key);
    if (t === null) return null;
    try {
      checkTemplate(t);
      if (checkTemplateFits(t, doc) === null) return t;
    } catch {
      // not a template this page can use
    }
    remember(key, null);
    return null;
  }

  /** Open a sample with its fields stripped and the boxes detected ahead of time. */
  async function openSample(id, { fresh = false } = {}) {
    const mine = ++opening;
    say(msg, "");
    status("Opening the sample…");
    try {
      const sample = await loadSample(id);
      const doc = await engine.open(sample.bytes);
      const seeded = await engine.seed(doc, sample.schema, { detections: sample.detections.pages });
      if (mine !== opening) return dropLate(doc);
      const kept = fresh ? null : restore(sample, "form", doc);
      restored = kept !== null;
      app.setDocument({
        doc,
        name: sample.manifest.title,
        sample,
        variant: "form",
        template: kept ?? seeded,
        source: kept
          ? { kind: "restored", text: "Your earlier edits" }
          : { kind: "precomputed", text: `Boxes found ahead of time by ${modelName(sample.detections.model)}` },
      });
      app.setRecord(sample.record);
      $("sample").value = id;
      status(kept
        ? "Your earlier edits to this form are back."
        : `These boxes were found ahead of time by ${modelName(sample.detections.model)}, the model this page runs. ` +
          "Run it here to watch it work; the model downloads once and is kept for your next visit.");
    } catch (e) {
      if (mine !== opening) return false;
      say(msg, `This sample couldn't be opened. ${e.message}`);
      status("");
      $("sample").value = state.sample?.id ?? ""; // the picker shows the form that is still open
      return false;
    }
    return true;
  }

  /** A document opened after a newer open started: never shown. */
  function dropLate(doc) {
    doc.destroy().catch(() => {});
    return false;
  }

  /** Open the sample as published with the AI agent's template for it. */
  async function openAgent({ fresh = false } = {}) {
    const sample = state.sample;
    if (sample === null) return;
    const mine = ++opening;
    say(msg, "");
    status("Opening the form as published…");
    try {
      const agent = await loadAgent(sample);
      const doc = await engine.open(agent.bytes);
      if (mine !== opening) return dropLate(doc);
      const misfit = checkTemplateFits(agent.template, doc);
      if (misfit) {
        doc.destroy().catch(() => {});
        throw new Error(misfit);
      }
      const kept = fresh ? null : restore(sample, "published", doc);
      restored = kept !== null;
      app.setDocument({
        doc,
        name: sample.manifest.title,
        sample,
        variant: "published",
        template: kept ?? agent.template,
        source: kept
          ? { kind: "restored", text: "Your earlier edits" }
          : { kind: "agent", text: `The AI agent's template (${agent.scores.model_label ?? agent.scores.model})` },
      });
      app.setRecord(sample.record);
      status(kept
        ? "Your earlier edits to the agent's template are back."
        : "The form as published, with its own fields, and the template the agent built for it.");
    } catch (e) {
      if (mine !== opening) return;
      say(msg, `The agent's template couldn't be opened. ${e.message}`);
      status("");
    }
  }

  async function loadIndex() {
    const select = $("sample");
    try {
      index = await getJson(new URL("index.json", app.samplesBase));
      if (!Array.isArray(index) || index.length === 0) throw new Error("There are no samples.");
    } catch {
      select.replaceChildren(new Option("No samples", ""));
      say(msg, "The sample forms couldn't be loaded. You can still drop in your own PDF.");
      status("");
      app.render();
      return;
    }
    select.replaceChildren(...index.map((s) => new Option(s.title, s.id)));
    select.disabled = false;
    await openSample(index[0].id);
  }

  $("sample").addEventListener("change", (e) => {
    if (e.target.value) openSample(e.target.value);
  });

  $("start-over").addEventListener("click", () => {
    if (state.sample === null) return;
    remember(app.storageKey(), null);
    if (state.variant === "published") openAgent({ fresh: true });
    else openSample(state.sample.id, { fresh: true });
  });

  // ---------------------------------------------------------------- a visitor's PDF

  async function openFile(file) {
    const mine = ++opening;
    say(msg, "");
    $("live-note").hidden = true;
    status(`Opening ${file.name}…`);
    const schema = app.template?.schema ?? state.sample?.schema ?? { type: "object", properties: {} };
    const work = (async () => {
      const opened = await engine.open(new Uint8Array(await file.arrayBuffer()));
      try {
        return { doc: opened, template: await engine.seed(opened, schema) };
      } catch (e) {
        opened.destroy().catch(() => {});
        throw e;
      }
    })();
    const late = `This file took more than ${seconds(app.openTimeoutMs)} to open. Try again, or try a smaller PDF.`;
    let doc;
    let template;
    try {
      ({ doc, template } = await withinTime(work, app.openTimeoutMs, late, { onLate: (out) => dropLate(out.doc) }));
    } catch (e) {
      if (mine !== opening) return;
      say(msg, e?.name === "DocError" || e?.name === "TimeLimitError" ? e.message : "This file couldn't be opened.");
      status("");
      return;
    }
    if (mine !== opening) return dropLate(doc);
    const fillable = doc.pages.some((p) => p.widgets.length > 0);
    restored = false;
    app.setDocument({
      doc,
      name: file.name,
      sample: null,
      variant: "own",
      template,
      source: fillable ? { kind: "widgets", text: "The PDF's own form fields" } : { kind: "none", text: "No boxes yet" },
    });
    $("sample").value = "";
    if (fillable) {
      status("This PDF has its own form fields, so they are used as they are; no model needed.");
    } else {
      await runLive();
    }
  }

  const fileInput = $("file");
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (file) openFile(file);
  });

  const stage = $("stage");
  const hasFiles = (e) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  stage.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    stage.classList.add("drag");
  });
  stage.addEventListener("dragleave", (e) => {
    if (e.relatedTarget === null || !stage.contains(e.relatedTarget)) stage.classList.remove("drag");
  });
  stage.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    stage.classList.remove("drag");
    const file = e.dataTransfer.files?.[0];
    if (file) openFile(file);
  });

  // ---------------------------------------------------------------- live detection

  /** Pages to detect: the sample's precomputed pages, else every page without form fields. */
  function detectPages() {
    if (state.sample && state.variant === "form") return Object.keys(state.sample.detections.pages).map(Number);
    return state.doc ? state.doc.pages.filter((p) => p.widgets.length === 0).map((p) => p.index) : [];
  }

  /** Ask before downloading the model where live detection is off (spec §6). */
  function askFirst() {
    status("");
    $("live-note").hidden = false;
  }

  async function runLive() {
    if (detecting() || state.doc === null) return;
    if (app.liveOff && !confirmed) {
      askFirst();
      return;
    }
    // claimed before any await, so a second click or a drop can't start a parallel run on this document
    const mine = { token: session.generation };
    run = mine;
    say(msg, "");
    $("live-note").hidden = true;
    app.render();
    try {
      if (state.variant === "published") {
        // the detector runs on the sample with its fields stripped
        if (!(await openSample(state.sample.id)) || state.variant !== "form") return;
        mine.token = session.generation;
        app.render();
      }
      await detectNow(mine.token);
    } finally {
      if (run === mine) run = null;
      app.render();
    }
  }

  /** Detect the pages of the document on screen (`token`) and replace its boxes with what is found. */
  async function detectNow(token) {
    const doc = state.doc;
    const pages = detectPages();
    if (pages.length === 0) {
      status("Every page of this PDF has its own form fields; there is nothing to detect.");
      return;
    }
    const model = app.touch ? "S" : $("model").value;
    status("Starting the detector…");
    const mb = (n) => (n / (1024 * 1024)).toFixed(1);
    try {
      const detections = {};
      for (const [i, p] of pages.entries()) {
        statusFor(token, pages.length > 1 ? `Detecting page ${p + 1} (${i + 1} of ${pages.length})…` : `Detecting page ${p + 1}…`);
        detections[p] = await engine.detect(doc, p, {
          model,
          onProgress: (loaded, total) => {
            statusFor(token, loaded < total ? `Downloading the model: ${mb(loaded)} of ${mb(total)} MB` : "Model ready. Detecting…");
          },
        });
        if (token !== session.generation) return;
      }
      const template = await engine.seed(doc, app.template.schema, { detections });
      if (token !== session.generation) return;
      const info = engine.detectorInfo(model);
      const how = `${info.model}${info.provider ? ` on ${PROVIDERS[info.provider] ?? info.provider}` : ""}`;
      const speed = info.msPerPage === null ? "" : ` · ${Math.round(info.msPerPage)} ms per page`;
      const res = await session.replaceTemplate(template, { source: { kind: "live", text: `Detected live by ${how}${speed}` } });
      if (res.stale) return;
      const found = Object.values(detections).reduce((n, list) => n + list.length, 0);
      status(`Done: ${found} boxes on ${pages.length === 1 ? "1 page" : `${pages.length} pages`}. Undo brings back the boxes before.`);
    } catch (e) {
      if (token === session.generation) {
        say(msg, `Live detection didn't finish: ${e.message} The boxes shown are unchanged.`);
        status("");
      }
    }
  }

  $("run-live").addEventListener("click", () => runLive());
  $("try-anyway").addEventListener("click", () => {
    confirmed = true;
    runLive();
  });
  $("load-agent").addEventListener("click", () => openAgent());
  $("model").addEventListener("change", () => app.render());
  $("compare").addEventListener("change", (e) => {
    state.compare = e.target.checked;
    app.render();
  });

  // ---------------------------------------------------------------- render

  app.onRender(() => {
    const sample = state.sample;
    const t = app.template;
    $("model-group").hidden = app.touch;
    $("run-live").disabled = detecting() || state.doc === null;
    $("note-size").textContent = `${MODEL_MB[app.touch ? "S" : $("model").value] ?? MODEL_MB.S} MB`;
    $("start-over").hidden = !restored || sample === null;
    $("compare-group").hidden = sample === null;
    $("agent-group").hidden = sample === null;
    const page = state.doc?.pages[state.page];
    $("scanned-note").hidden = page?.kind !== "scanned";

    if (sample !== null && t !== null) {
      let n = 0;
      let m = 0;
      for (const [key, rects] of Object.entries(sample.originals.pages)) {
        const boxes = t.fields.filter((f) => f.page === Number(key) && f.rect).map((f) => f.rect);
        n += matchCount(rects, boxes);
        m += rects.length;
      }
      $("found").textContent = `found ${n} of ${m} of the form's own fields (IoU 0.5 or more)`;
      const scores = sample.agent?.scores;
      $("agent-scores").hidden = !(scores && state.variant === "published");
      if (scores) $("agent-scores").textContent = scoresLine(scores);
    }

    const m = sample?.manifest;
    const docline = $("docline");
    if (state.doc === null) {
      docline.textContent = "No form open";
    } else if (m) {
      const how = state.variant === "published" ? "as published, with its own fields" : "fields stripped";
      docline.replaceChildren(Object.assign(document.createElement("b"), { textContent: m.title }), ` · rev. ${m.revision} · ${how}`);
    } else {
      docline.replaceChildren(Object.assign(document.createElement("b"), { textContent: state.name }), " · your PDF");
    }
  });

  loadIndex();
}
