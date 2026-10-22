// The fill drawer: a record from the record source, or a sample record as editable JSON, Fill
// (downloads the filled PDF and shows the report) and the fillable empty PDF. Stored records are
// offered only for the stored schema that is exactly the template's own; a template with any other
// schema (a pasted one, or one changed since) is filled from the sample record, never from records
// of another schema. When the template was made for another version of this PDF
// the server refuses (409 fingerprint_mismatch); the drawer then says so and offers to rebind the
// template to this PDF (one undo step) or to fill anyway. The server's note on a skipped check
// (a template with no fingerprint, or Fill anyway) is shown with the result.

import { renderReport } from "../core/report.js";
import { placeholderRecord } from "../core/schema.js";
import { downloadBytes } from "../core/store.js";

const SAMPLE = "__sample__";

/** @param {object} app see workbench.js */
export function mountFill(app) {
  const { $, engine, session, say } = app;
  const drawer = $("fill");
  const msg = $("fill-msg");
  const mismatch = $("fill-mismatch");
  const records = new Map(); // schema name -> [{id, title}]
  let last = null; // the action the server refused: "fill" or "smart"
  let busy = false;
  let sampleFor = null; // the schema (as JSON text) the sample record was made for

  const sampling = () => $("fill-schema").value === SAMPLE;

  function setBusy(on) {
    busy = on;
    for (const id of ["fill-go", "smart-go", "rebind", "fill-anyway"]) $(id).disabled = on;
    $("fill-go").disabled = on || (!sampling() && !$("fill-record").value);
  }

  /** A fresh sample record for the template's schema, unless the one in the box was made for it. */
  function makeSample() {
    const schema = app.template?.schema;
    const text = JSON.stringify(schema ?? null);
    if (text === sampleFor) return;
    sampleFor = text;
    let record;
    try {
      record = placeholderRecord(schema);
    } catch {
      record = {}; // a schema that can't be sampled still fills (the report says what is missing)
    }
    $("fill-sample").value = JSON.stringify(record, null, 2);
  }

  async function showRecords() {
    const name = $("fill-schema").value;
    const select = $("fill-record");
    $("fill-sample-group").hidden = name !== SAMPLE;
    $("fill-record-group").hidden = name === SAMPLE;
    if (name === SAMPLE) {
      makeSample();
      setBusy(busy);
      return;
    }
    select.replaceChildren(new Option("Loading…", ""));
    setBusy(busy);
    try {
      if (!records.has(name)) {
        // the same record can come from more than one source (CSV and SQLite); the server reads the first
        const seen = new Set();
        records.set(name, (await engine.records.list(name)).filter((r) => !seen.has(r.id) && seen.add(r.id)));
      }
    } catch (e) {
      select.replaceChildren(new Option("No records", ""));
      say(msg, e.message);
      return;
    }
    const list = records.get(name);
    select.replaceChildren(...(list.length ? list.map((r) => new Option(r.title, r.id)) : [new Option("No records for this schema", "")]));
    setBusy(busy);
  }

  function open() {
    if (app.template === null) return;
    // only the stored schema that is exactly the template's own has records that fit it
    const own = app.schemaNameOf(app.template.schema);
    const options = own === null ? [] : [new Option(`Stored records (${own})`, own)];
    $("fill-schema").replaceChildren(...options, new Option("A sample record", SAMPLE));
    $("fill-schema").value = own ?? SAMPLE;
    // below the toolbar, so Undo and Redo stay in reach while the drawer is open
    drawer.style.top = `${Math.max(0, $("toolbar").getBoundingClientRect().bottom)}px`;
    drawer.hidden = false;
    showRecords();
    $("fill-record").focus();
  }

  async function run(action, { checkFingerprint } = {}) {
    if (busy || app.template === null) return;
    mismatch.hidden = true;
    setBusy(true);
    say(msg, action === "fill" ? "Filling…" : "Making the fillable PDF…");
    try {
      await app.track(session.idle()); // edits still on their way are part of what gets filled
      const template = session.template;
      const doc = session.doc;
      if (action === "fill") {
        let filled;
        if (sampling()) {
          let record;
          try {
            record = JSON.parse($("fill-sample").value);
          } catch (e) {
            say(msg, `The record isn't valid JSON: ${e.message}`);
            return;
          }
          filled = await engine.fill(template, doc, record, { checkFingerprint });
        } else {
          const recordRef = { schema: $("fill-schema").value, id: $("fill-record").value };
          filled = await engine.fill(template, doc, null, { recordRef, checkFingerprint });
        }
        const { pdfBytes, report, note } = filled;
        downloadBytes(pdfBytes, `${app.stem()}-filled.pdf`);
        renderReport($("report"), report);
        const n = report.counts?.fields_written ?? 0;
        say(msg, `Filled ${n} field${n === 1 ? "" : "s"}; the PDF is downloading.${note ? ` ${note}` : ""}`);
      } else {
        const { pdfBytes, note } = await engine.smartPdf(template, doc, { checkFingerprint, withNote: true });
        downloadBytes(pdfBytes, `${app.stem()}-fillable.pdf`);
        say(msg, `The fillable empty PDF is downloading: a form field for every box.${note ? ` ${note}` : ""}`);
      }
    } catch (e) {
      if (e.code === "fingerprint_mismatch") {
        last = action;
        $("mismatch-text").textContent = e.message;
        mismatch.hidden = false;
        say(msg, "");
      } else {
        say(msg, `That didn't work: ${e.message}`);
      }
    } finally {
      setBusy(false);
    }
  }

  $("fill-open").addEventListener("click", open);
  $("fill-close").addEventListener("click", () => {
    drawer.hidden = true;
  });
  $("fill-schema").addEventListener("change", showRecords);
  $("fill-record").addEventListener("change", () => setBusy(busy));
  $("fill-go").addEventListener("click", () => run("fill"));
  $("smart-go").addEventListener("click", () => run("smart"));
  $("fill-anyway").addEventListener("click", () => run(last, { checkFingerprint: false }));
  $("rebind").addEventListener("click", async () => {
    if (busy || app.template === null) return;
    setBusy(true);
    say(msg, "Rebinding…");
    let rebound;
    try {
      await app.track(session.idle());
      rebound = await engine.rebind(session.template, session.doc);
    } catch (e) {
      say(msg, e.message);
      setBusy(false);
      return;
    }
    // only the form info changes; built from the template on screen when its turn comes
    const res = await app.track(session.replaceTemplate((t) => ({ ...structuredClone(t), form: structuredClone(rebound.form) })));
    setBusy(false);
    if (res.error !== undefined) {
      say(msg, res.error);
      return;
    }
    app.notice("Rebound the template to this PDF. Undo goes back to the template before.");
    run(last);
  });
  app.onReset(() => {
    sampleFor = null;
    drawer.hidden = true;
    mismatch.hidden = true;
    say(msg, "");
    $("report").replaceChildren();
  });
}
