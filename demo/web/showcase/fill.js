// The Fill step: the record as editable JSON, Fill (downloads the filled PDF and shows the
// report), a fillable empty PDF, and saving or loading the template JSON. pdf-lib loads on the
// first fill (the engine imports it then). Filling and making the fillable PDF have a time limit
// (app.fillTimeoutMs), loading pdf-lib included; a result that comes after it is dropped.

import { renderReport } from "../core/report.js";
import { checkTemplateFits, downloadBytes, downloadTemplate, readTemplateFile } from "../core/store.js";
import { seconds, withinTime } from "./limits.js";

const PROTECTED =
  "This PDF is protected against changes, so it can't be filled in the browser. The full library can fill it.";

/** @param {object} app see showcase.js */
export function mountFill(app) {
  const { $, engine, session, state, say } = app;
  const msg = $("fill-msg");
  const recordError = $("record-error");
  let busy = false;

  /** A file name stem for downloads: the sample's form id, or the visitor's file name. */
  function stem() {
    if (state.sample) return state.sample.manifest.form ?? state.sample.id;
    return state.name.replace(/\.pdf$/i, "") || "form";
  }

  function readRecord() {
    say(recordError, "");
    try {
      return { record: JSON.parse($("record").value) };
    } catch (e) {
      say(recordError, `The record isn't valid JSON: ${e.message}`);
      return null;
    }
  }

  async function run(label, work) {
    if (busy || app.template === null || state.doc === null) return;
    say(msg, "");
    if (state.doc.encrypted) {
      say(msg, PROTECTED);
      return;
    }
    busy = true;
    app.render();
    say(msg, label);
    try {
      await work();
    } catch (e) {
      say(msg, `That didn't work: ${e.message}`);
    } finally {
      busy = false;
      app.render();
    }
  }

  $("fill").addEventListener("click", () => {
    const parsed = readRecord();
    if (parsed === null) return;
    run("Filling…", async () => {
      const late = `Filling took more than ${seconds(app.fillTimeoutMs)}. Try again.`;
      const { pdfBytes, report } = await withinTime(engine.fill(app.template, state.doc, parsed.record), app.fillTimeoutMs, late);
      downloadBytes(pdfBytes, `${stem()}-filled.pdf`);
      renderReport($("report"), report);
      const n = report.counts?.fields_written ?? 0;
      say(msg, `Filled ${n} field${n === 1 ? "" : "s"}; the PDF is downloading. Its fields stay editable.`);
    });
  });

  $("smart").addEventListener("click", () => {
    run("Making the fillable PDF…", async () => {
      const late = `Making the fillable PDF took more than ${seconds(app.fillTimeoutMs)}. Try again.`;
      const bytes = await withinTime(engine.smartPdf(app.template, state.doc), app.fillTimeoutMs, late);
      downloadBytes(bytes, `${stem()}-fillable.pdf`);
      say(msg, "The fillable empty PDF is downloading: a form field for every box.");
    });
  });

  $("save-template").addEventListener("click", () => {
    if (app.template === null) return;
    downloadTemplate(app.template, `${stem()}-template.json`);
    say(msg, "The template is downloading. Load it here later, or use it with the formcast library.");
  });

  const input = $("template-file");
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.value = "";
    if (!file || state.doc === null) return;
    say(msg, "");
    try {
      const template = await readTemplateFile(file);
      const misfit = checkTemplateFits(template, state.doc);
      if (misfit) {
        say(msg, misfit);
        return;
      }
      const res = await session.replaceTemplate(template, { source: { kind: "loaded", text: `Template from ${file.name}` } });
      if (res.stale) return;
      say(msg, `Template loaded: ${template.fields.length} boxes. Undo goes back to the one before.`);
    } catch (e) {
      say(msg, e.message);
    }
  });

  app.onRender(() => {
    const none = app.template === null || state.doc === null;
    for (const id of ["fill", "smart", "save-template"]) $(id).disabled = none || busy;
    $("template-file").disabled = none;
  });
}
