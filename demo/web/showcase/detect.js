// The Detect step: pick a sample form or drop a PDF, then Detect finds its boxes under the scan
// (scan.js), and the boxes can be compared with the form's own fields.
//
// A sample opens blank: the form with its fields stripped and no boxes. Detect runs FFDNet in the
// browser on it. Where live detection is off until the visitor confirms the download (touch
// phones, under 4 GB of memory), Detect plays the same scan over the sample's saved detections and
// says plainly that they are saved results from an earlier run; Try anyway runs the detector live.
// A visitor's flat PDF is detected live as soon as it opens (or waits for Try anyway); a fillable
// one uses its own form fields.
//
// A sample is a folder of files (demo/tools/build_showcase.py builds them): manifest.json, form.pdf
// (fields stripped), detections.json, original-fields.json, schema.json, records.json,
// agent-template.json, agent-scores.json. All but the agent's template load with the sample; Auto
// map (link.js) loads that when it is first asked for.

import { placeholderRecord } from "../core/schema.js";
import { pairByOverlap } from "./automap.js";
import { fetchWithin, seconds, withinTime } from "./limits.js";

const PROVIDERS = { webgpu: "WebGPU", wasm: "WebAssembly" };

const MODEL_MB = { S: 38, L: 102 };

/** "FFDNet-S-onnx" -> "FFDNet-S". */
function modelName(model) {
  return String(model ?? "the detector").replace(/-onnx$/i, "");
}

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** @param {object} app see showcase.js */
export function mountDetect(app) {
  const { $, engine, session, state, scan, say } = app;
  const msg = $("detect-msg");
  const status = (text) => {
    $("detect-status").textContent = text;
  };
  const getJson = (url) => fetchWithin(url, app.fetchTimeoutMs, (res) => res.json());
  const getBytes = (url) => fetchWithin(url, app.fetchTimeoutMs, async (res) => new Uint8Array(await res.arrayBuffer()));
  let index = [];
  const cache = new Map(); // sample id -> its files
  let run = null; // the detection in progress (live or saved): {token} of the document it runs on
  let confirmed = false; // the visitor accepted the download where live detection is off
  let failed = false; // the last live run on this sample failed: its saved results are on offer
  let opening = 0; // bumps on every open, so only the latest one is shown

  /** True while a detection runs on the document on screen (a run on an older one doesn't count). */
  const detecting = () => run !== null && run.token === session.generation;
  /** Show a status line for document `token` only while it is still the one on screen. */
  const statusFor = (token, text) => {
    if (token === session.generation) status(text);
  };
  const mb = () => `${MODEL_MB[app.touch ? "S" : $("model").value] ?? MODEL_MB.S} MB`;

  // ---------------------------------------------------------------- samples

  async function loadSample(id) {
    if (cache.has(id)) return cache.get(id);
    const base = new URL(`${encodeURIComponent(id)}/`, app.samplesBase);
    const [manifest, detections, originals, schema, records, scores, bytes] = await Promise.all([
      getJson(new URL("manifest.json", base)),
      getJson(new URL("detections.json", base)),
      getJson(new URL("original-fields.json", base)),
      getJson(new URL("schema.json", base)),
      getJson(new URL("records.json", base)),
      getJson(new URL("agent-scores.json", base)),
      getBytes(new URL("form.pdf", base)),
    ]);
    const sample = { id, base, manifest, detections, originals, schema, records, scores, bytes, agent: null };
    cache.set(id, sample);
    return sample;
  }

  /** Open a sample blank: its fields stripped and no boxes yet. */
  async function openSample(id) {
    const mine = ++opening;
    say(msg, "");
    status("Opening the sample…");
    try {
      const sample = await loadSample(id);
      const doc = await engine.open(sample.bytes);
      const blank = await engine.seed(doc, sample.schema); // a page without widgets gets no boxes
      if (mine !== opening) return dropLate(doc);
      failed = false;
      app.setDocument({ doc, name: sample.manifest.title, sample, template: blank, source: null });
      app.setRecords(sample.records);
      $("sample").value = id;
      status(app.liveOff && !confirmed
        ? "Detect plays back the detector's saved results here: live detection is off on this device."
        : `Detect runs ${modelName(sample.detections.model)} on this page in your browser to find the blanks.`);
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
    failed = false;
    app.setDocument({
      doc,
      name: file.name,
      sample: null,
      template,
      source: fillable ? { kind: "widgets", text: "The PDF's own form fields" } : null,
    });
    let record;
    try {
      record = placeholderRecord(schema);
    } catch {
      record = {};
    }
    app.setRecords([{ title: "A made-up record", record }], { placeholder: true });
    $("sample").value = "";
    if (fillable) {
      status("This PDF has its own form fields, so they are used as they are; no model needed.");
    } else {
      await detect();
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

  // ---------------------------------------------------------------- detection

  /** Pages to detect: the sample's pages with fields, else every page without form fields. */
  function detectPages() {
    if (state.sample) return Object.keys(state.sample.detections.pages).map(Number);
    return state.doc ? state.doc.pages.filter((p) => p.widgets.length === 0).map((p) => p.index) : [];
  }

  /** The note under Detect where live detection is off (spec §6): saved results, or none. */
  function showNote(saved) {
    const why = `The model is a ${mb()} download, may be slow here and can run the tab out of memory.`;
    $("live-note-text").textContent = saved
      ? `Saved results from an earlier run of the detector: live detection is off on this device. ${why}`
      : `Live detection is off on this device. ${why} You can still add boxes by hand.`;
    $("live-note").hidden = false;
  }

  /** Detect: live, or the saved results where live detection waits for the visitor to confirm. */
  async function detect() {
    if (detecting() || state.doc === null) return;
    if (app.liveOff && !confirmed) {
      if (state.sample) await playSaved();
      else {
        status("");
        showNote(false);
      }
      return;
    }
    await runLive();
  }

  /** Claim the run for the document on screen (before any await, so a second click can't start a parallel run). */
  async function claim(work) {
    const mine = { token: session.generation };
    run = mine;
    say(msg, "");
    app.render();
    try {
      await work(mine.token);
    } finally {
      if (run === mine) run = null;
      app.render();
    }
  }

  /**
   * Put detected boxes on screen (one undo step) as the scan's last pass reaches them. False when
   * the document changed meanwhile; throws when the boxes couldn't be put on screen.
   */
  async function showBoxes(token, detections, source) {
    const template = await engine.seed(state.doc, app.template.schema, { detections });
    if (token !== session.generation) return false;
    const shown = scan.reveal(); // before the render, so the boxes come in with the line
    const res = await session.replaceTemplate(template, { source });
    if (res.stale) return false;
    if (res.error !== undefined) {
      scan.stop();
      throw new Error(res.error);
    }
    await shown;
    return token === session.generation;
  }

  /** Play the scan over the sample's saved detections, and say plainly that they are saved. */
  function playSaved() {
    return claim(async (token) => {
      const sample = state.sample;
      failed = false;
      $("live-note").hidden = true;
      status("Reading the page…");
      const name = modelName(sample.detections.model);
      const source = { kind: "saved", text: `Saved results from an earlier run of ${name}` };
      try {
        if (!(await showBoxes(token, structuredClone(sample.detections.pages), source))) return;
      } catch (e) {
        if (token === session.generation) {
          say(msg, `The saved results couldn't be shown: ${e.message}`);
          status("");
        }
        return;
      }
      const found = Object.values(sample.detections.pages).reduce((n, list) => n + list.length, 0);
      const pages = Object.keys(sample.detections.pages).length;
      status(`${plural(found, "box", "boxes")} on ${plural(pages, "page")}, from a run of ${name} made ahead of time.`);
      if (app.liveOff && !confirmed) showNote(true);
    });
  }

  function runLive() {
    return claim(async (token) => {
      const doc = state.doc;
      const pages = detectPages();
      $("live-note").hidden = true;
      if (pages.length === 0) {
        status("Every page of this PDF has its own form fields; there is nothing to detect.");
        return;
      }
      const model = app.touch ? "S" : $("model").value;
      status(scan.still ? "Scanning the page: starting the detector…" : "Starting the detector…");
      scan.start();
      const mbOf = (n) => (n / (1024 * 1024)).toFixed(1);
      try {
        const detections = {};
        for (const [i, p] of pages.entries()) {
          statusFor(token, pages.length > 1 ? `Detecting page ${p + 1} (${i + 1} of ${pages.length})…` : `Detecting page ${p + 1}…`);
          detections[p] = await engine.detect(doc, p, {
            model,
            onProgress: (loaded, total) => {
              statusFor(token, loaded < total ? `Downloading the model: ${mbOf(loaded)} of ${mbOf(total)} MB` : "Model ready. Detecting…");
            },
          });
          if (token !== session.generation) return;
        }
        const info = engine.detectorInfo(model);
        const how = `${info.model}${info.provider ? ` on ${PROVIDERS[info.provider] ?? info.provider}` : ""}`;
        const speed = info.msPerPage === null ? "" : ` · ${Math.round(info.msPerPage)} ms per page`;
        if (!(await showBoxes(token, detections, { kind: "live", text: `Detected live by ${how}${speed}` }))) return;
        failed = false;
        const found = Object.values(detections).reduce((n, list) => n + list.length, 0);
        status(`Done: ${plural(found, "box", "boxes")} on ${plural(pages.length, "page")}. Undo brings back the boxes before.`);
      } catch (e) {
        if (token === session.generation) {
          scan.stop();
          failed = state.sample !== null;
          say(msg, `Live detection didn't finish: ${e.message} The boxes shown are unchanged.`);
          status("");
        }
      }
    });
  }

  $("detect").addEventListener("click", () => detect());
  $("try-anyway").addEventListener("click", () => {
    confirmed = true;
    runLive();
  });
  $("play-saved").addEventListener("click", () => playSaved());
  $("model").addEventListener("change", () => app.render());
  $("compare").addEventListener("change", (e) => {
    state.compare = e.target.checked;
    app.render();
  });

  // ---------------------------------------------------------------- render

  app.onRender(() => {
    const sample = state.sample;
    const t = app.template;
    const boxes = t?.fields.length ?? 0;
    $("model-group").hidden = app.touch;
    $("detect").disabled = detecting() || state.doc === null;
    $("detect").textContent = detecting() ? "Detecting…" : boxes > 0 ? "Detect again" : "Detect the boxes";
    $("play-saved").hidden = !(failed && sample !== null) || detecting();
    $("compare-group").hidden = sample === null || boxes === 0;
    const page = state.doc?.pages[state.page];
    $("scanned-note").hidden = page?.kind !== "scanned";

    if (sample !== null && t !== null) {
      let n = 0;
      let m = 0;
      for (const [key, rects] of Object.entries(sample.originals.pages)) {
        const rectsOnPage = t.fields.filter((f) => f.page === Number(key) && f.rect).map((f) => f.rect);
        n += pairByOverlap(rects, rectsOnPage, 0.5).length; // each own field found at most once
        m += rects.length;
      }
      $("found").textContent = `found ${n} of ${m} of the form's own fields (IoU 0.5 or more)`;
    }

    const m = sample?.manifest;
    const docline = $("docline");
    if (state.doc === null) {
      docline.textContent = "No form open";
    } else if (m) {
      docline.replaceChildren(Object.assign(document.createElement("b"), { textContent: m.title }), ` · rev. ${m.revision} · fields stripped`);
    } else {
      docline.replaceChildren(Object.assign(document.createElement("b"), { textContent: state.name }), " · your PDF");
    }
  });

  loadIndex();
}
