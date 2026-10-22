// The Detect step: pick a sample form or drop a PDF, run the detector live, compare the boxes with
// the form's own fields, and open the AI agent's template for the sample.
//
// A sample is a folder of files (Task 13 builds them; see demo/tools/build_showcase.py):
// manifest.json, form.pdf (fields stripped), detections.json, original-fields.json, schema.json,
// record.json, published.pdf, agent-template.json, agent-scores.json. The first five load with the
// sample; the agent's three load when asked for.

import { iou } from "../core/geometry.js";
import { checkTemplateFits, recall, remember } from "../core/store.js";

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

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url.pathname} could not be loaded (HTTP ${res.status}).`);
  return res.json();
}

async function getBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url.pathname} could not be loaded (HTTP ${res.status}).`);
  return new Uint8Array(await res.arrayBuffer());
}

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
  const { $, engine, state, say } = app;
  const msg = $("detect-msg");
  const status = (text) => {
    $("detect-status").textContent = text;
  };
  let index = [];
  const cache = new Map(); // sample id -> its files
  let running = false;
  let restored = false;

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

  /** The remembered template for a sample variant when it still fits the PDF, else null. */
  function restore(sample, variant, doc) {
    const t = recall(`${sample.id}/${variant}`);
    return t !== null && typeof t === "object" && checkTemplateFits(t, doc) === null ? t : null;
  }

  /** Open a sample with its fields stripped and the boxes detected ahead of time. */
  async function openSample(id, { fresh = false } = {}) {
    say(msg, "");
    status("Opening the sample…");
    try {
      const sample = await loadSample(id);
      const doc = await engine.open(sample.bytes);
      const seeded = await engine.seed(doc, sample.schema, { detections: sample.detections.pages });
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
      say(msg, `This sample couldn't be opened. ${e.message}`);
      status("");
    }
  }

  /** Open the sample as published with the AI agent's template for it. */
  async function openAgent({ fresh = false } = {}) {
    const sample = state.sample;
    if (sample === null) return;
    say(msg, "");
    status("Opening the form as published…");
    try {
      const agent = await loadAgent(sample);
      const doc = await engine.open(agent.bytes);
      const misfit = checkTemplateFits(agent.template, doc);
      if (misfit) throw new Error(misfit);
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
    say(msg, "");
    $("phone-note").hidden = true;
    status(`Opening ${file.name}…`);
    let doc;
    try {
      doc = await engine.open(new Uint8Array(await file.arrayBuffer()));
    } catch (e) {
      say(msg, e?.name === "DocError" ? e.message : "This file couldn't be opened.");
      status("");
      return;
    }
    const schema = app.template?.schema ?? state.sample?.schema ?? { type: "object", properties: {} };
    const fillable = doc.pages.some((p) => p.widgets.length > 0);
    const template = await engine.seed(doc, schema);
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
    } else if (app.phone) {
      status("");
      $("phone-note").hidden = false;
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

  async function runLive() {
    if (running || state.doc === null) return;
    say(msg, "");
    $("phone-note").hidden = true;
    if (state.variant === "published") await openSample(state.sample.id);
    const doc = state.doc;
    const token = state.token;
    const pages = detectPages();
    if (pages.length === 0) {
      status("Every page of this PDF has its own form fields; there is nothing to detect.");
      return;
    }
    const model = app.phone ? "S" : $("model").value;
    running = true;
    app.render();
    status("Starting the detector…");
    const mb = (n) => (n / (1024 * 1024)).toFixed(1);
    try {
      const detections = {};
      for (const [i, p] of pages.entries()) {
        status(pages.length > 1 ? `Detecting page ${p + 1} (${i + 1} of ${pages.length})…` : `Detecting page ${p + 1}…`);
        detections[p] = await engine.detect(doc, p, {
          model,
          onProgress: (loaded, total) => {
            if (token === state.token) {
              status(loaded < total ? `Downloading the model: ${mb(loaded)} of ${mb(total)} MB` : "Model ready. Detecting…");
            }
          },
        });
        if (token !== state.token) return;
      }
      const template = await engine.seed(doc, app.template.schema, { detections });
      if (token !== state.token) return;
      const info = engine.detectorInfo(model);
      const how = `${info.model}${info.provider ? ` on ${PROVIDERS[info.provider] ?? info.provider}` : ""}`;
      const speed = info.msPerPage === null ? "" : ` · ${Math.round(info.msPerPage)} ms per page`;
      app.commit(template, { source: { kind: "live", text: `Detected live by ${how}${speed}` } });
      const found = Object.values(detections).reduce((n, list) => n + list.length, 0);
      status(`Done: ${found} boxes on ${pages.length === 1 ? "1 page" : `${pages.length} pages`}. Undo brings back the boxes before.`);
    } catch (e) {
      if (token === state.token) {
        say(msg, `Live detection didn't finish: ${e.message} The boxes shown are unchanged.`);
        status("");
      }
    } finally {
      running = false;
      app.render();
    }
  }

  $("run-live").addEventListener("click", () => runLive());
  $("try-anyway").addEventListener("click", () => runLive());
  $("load-agent").addEventListener("click", () => openAgent());
  $("compare").addEventListener("change", (e) => {
    state.compare = e.target.checked;
    app.render();
  });

  // ---------------------------------------------------------------- render

  app.onRender(() => {
    const sample = state.sample;
    const t = app.template;
    $("model-group").hidden = app.phone;
    $("run-live").disabled = running || state.doc === null;
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
