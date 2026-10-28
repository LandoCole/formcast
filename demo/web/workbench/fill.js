// The fill drawer: a record from the record source, or a sample record as editable JSON, Fill
// (shows the filled PDF on the page and the report), Download (the bytes on show), Clear (back to
// the boxes) and the fillable empty PDF. Picking another record while the filled PDF is on show
// fills again, from the template; any template edit, and closing the drawer, ends the preview. The
// filled bytes are drawn with pdf.js in the browser (engines/browser/document.js, imported on the
// first fill), since the server renders only the PDFs it was given; when pdf.js can't open them
// (more pages than it takes), Download still saves them and the drawer says so. Clear or closing
// the drawer while a fill runs means that fill is never shown. Stored records are
// offered only for the stored schema that is exactly the template's own; a template with any other
// schema (a pasted one, or one changed since) is filled from the sample record, never from records
// of another schema. When the template was made for another version of this PDF
// the server refuses (409 fingerprint_mismatch); the drawer then says so and offers to rebind the
// template to this PDF (one undo step) or to fill anyway. The server's note on a skipped check
// (a template with no fingerprint, or Fill anyway) is shown with the result.
//
// Your data. The one-shot flow's records (`app.data`, a job's `result.data`) are the first source when
// there are any: each record is shown as JSON, editable before filling, and the data mapping that made
// them (or the AI's reading of free text) sits above. `app.showData(data)` opens the drawer on them and
// fills the first one. "Add data" sends another file or text and maps it for the template on screen;
// data given on the start screen that is still waiting (`app.pendingData`) is mapped when the drawer
// opens. "Data schema" saves the template's schema as a JSON file: what records for it must look like.

import { renderReport } from "../core/report.js";
import { placeholderRecord } from "../core/schema.js";
import { downloadBytes } from "../core/store.js";
import { describeData, mappingSummary, renderMapping, sendData } from "./data.js";

const SAMPLE = "__sample__";
const DATA = "__data__";

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
  let checked; // the fingerprint option of the fill on show, reused when the record changes
  let filled = null; // {bytes} of the last fill, what Download saves (on the page too, unless it couldn't be drawn)
  let fills = 0; // bumps on Clear, closing the drawer and a new document: a fill still running is then dropped

  const sampling = () => $("fill-schema").value === SAMPLE;
  const fromData = () => $("fill-schema").value === DATA;
  /** The record in the JSON box: the sample, or one of your data's records. */
  const editing = () => sampling() || fromData();

  function setBusy(on) {
    busy = on;
    for (const id of ["fill-go", "smart-go", "rebind", "fill-anyway"]) $(id).disabled = on;
    $("fill-go").disabled = on || (!editing() && !$("fill-record").value);
    $("fill-download").disabled = on || filled === null;
    $("fill-clear").disabled = filled === null;
  }

  /** The filled PDF opened for the page (pdf.js, loaded on the first fill). */
  async function openFilled(bytes) {
    const { openPdf } = await import("../engines/browser/document.js");
    return openPdf(bytes);
  }

  /** What the filled PDF is shown under: the stored record's title, or the sample record. */
  function recordTitle() {
    if (sampling()) return "the sample record";
    if (fromData()) return $("fill-record").selectedOptions[0]?.textContent || "your record";
    const select = $("fill-record");
    return select.selectedOptions[0]?.textContent || select.value;
  }

  let again = false; // the record changed while a fill ran: fill again when it is done

  /** Fill again with the record now picked, when a filled PDF is on show (or on its way). */
  function refill() {
    if (busy) again = true;
    else if (filled !== null) run("fill", { checkFingerprint: checked });
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

  /** Your data's record now picked, as JSON in the box. */
  function showDataRecord() {
    const record = app.data?.records?.[Number($("fill-record").value)] ?? {};
    $("fill-sample").value = JSON.stringify(record, null, 2);
    sampleFor = null; // the box no longer holds the sample
  }

  async function showRecords() {
    const name = $("fill-schema").value;
    const select = $("fill-record");
    $("fill-sample-group").hidden = name !== SAMPLE && name !== DATA;
    $("fill-record-group").hidden = name === SAMPLE;
    $("fill-sample-hint").textContent =
      name === DATA
        ? "This record as your data mapping made it. Edit it before filling if you like."
        : "Sample values for every field of this template's schema. Edit them as you like.";
    if (name === SAMPLE) {
      makeSample();
      setBusy(busy);
      return;
    }
    if (name === DATA) {
      const titles = app.data?.titles ?? [];
      select.replaceChildren(...(titles.length ? titles.map((t, i) => new Option(t, String(i))) : [new Option("No records", "")]));
      showDataRecord();
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

  /** The sources on offer: your data, the stored records of the template's own schema, a sample. */
  function sources() {
    // only the stored schema that is exactly the template's own has records that fit it
    const own = app.schemaNameOf(app.template.schema);
    const data = app.data?.records?.length ? app.data : null;
    const options = [];
    if (data) options.push(new Option(`Your data · ${data.dataset?.name ?? "records"} (${data.total ?? data.records.length})`, DATA));
    if (own !== null) options.push(new Option(`Stored records (${own})`, own));
    $("fill-schema").replaceChildren(...options, new Option("A sample record", SAMPLE));
    $("fill-schema").value = data ? DATA : own ?? SAMPLE;
    $("mapping-box").hidden = app.data === null;
    if (app.data !== null) {
      $("mapping-sum").textContent = mappingSummary(app.data);
      renderMapping($("mapping"), app.data);
      $("mapping-box").open = Boolean(app.data.error);
    }
  }

  function open() {
    if (app.template === null) return;
    sources();
    // below the toolbar, so Undo and Redo stay in reach while the drawer is open
    drawer.style.top = `${Math.max(0, $("toolbar").getBoundingClientRect().bottom)}px`;
    drawer.hidden = false;
    showRecords();
    $("fill-record").focus();
    if (app.pendingData) {
      const waiting = app.pendingData;
      app.pendingData = null;
      mapData(waiting);
    }
  }

  /** Map a dataset for the template on screen (a one-shot job that maps only the data). */
  async function mapData(dataset) {
    say(msg, `Mapping ${describeData(dataset)}…`);
    await app.track(session.idle());
    app.jobs.oneshot({ template: session.template, dataset, saved: app.state.savedName });
  }

  /** Your data's records are in: show them, open on the first one and fill it. */
  app.showData = (data) => {
    app.data = data;
    if (app.template === null) return;
    open();
    if (data.error) {
      say(msg, `Your data couldn't be mapped: ${data.error}`);
      return;
    }
    say(msg, "");
    if (data.records.length) run("fill");
  };

  async function run(action, { checkFingerprint } = {}) {
    if (busy || app.template === null) return;
    mismatch.hidden = true;
    setBusy(true);
    say(msg, action === "fill" ? "Filling…" : "Making the fillable PDF…");
    const mine = fills;
    try {
      await app.track(session.idle()); // edits still on their way are part of what gets filled
      const template = session.template;
      const doc = session.doc;
      if (action === "fill") {
        let out;
        if (editing()) {
          let record;
          try {
            record = JSON.parse($("fill-sample").value);
          } catch (e) {
            say(msg, `The record isn't valid JSON: ${e.message}`);
            return;
          }
          out = await engine.fill(template, doc, record, { checkFingerprint });
        } else {
          const recordRef = { schema: $("fill-schema").value, id: $("fill-record").value };
          out = await engine.fill(template, doc, null, { recordRef, checkFingerprint });
        }
        const { pdfBytes, report, note } = out;
        const title = recordTitle();
        let shown = null;
        let why = "";
        try {
          shown = await openFilled(pdfBytes);
        } catch (e) {
          why = e.message; // say, more pages than the browser's reader takes: the file is still there
        }
        // Clear, the drawer closing, another form or a template edit meanwhile: this fill is not shown
        if (mine !== fills || template !== session.template || doc !== session.doc || drawer.hidden) {
          shown?.destroy().catch(() => {});
          say(msg, "");
          return;
        }
        checked = checkFingerprint;
        filled = { bytes: pdfBytes };
        app.setPreview(shown === null ? null : { doc: shown, bytes: pdfBytes, title });
        renderReport($("report"), report);
        const n = report.counts?.fields_written ?? 0;
        const where = shown === null
          ? `; the preview couldn't be drawn (${why.replace(/\.$/, "")}), but Download saves the filled PDF.`
          : ", shown on the page: the PDF Download saves.";
        say(msg, `Filled ${n} field${n === 1 ? "" : "s"}${where}${note ? ` ${note}` : ""}`);
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
      if (again) {
        again = false;
        refill();
      }
    }
  }

  /** Back to the form with its boxes. */
  function clear() {
    fills += 1; // a fill still running is not shown when it lands
    again = false;
    filled = null;
    app.setPreview(null);
    setBusy(busy);
  }

  $("fill-open").addEventListener("click", open);
  $("fill-close").addEventListener("click", () => {
    drawer.hidden = true;
    clear();
  });
  $("fill-schema").addEventListener("change", async () => {
    await showRecords();
    refill();
  });
  $("fill-record").addEventListener("change", () => {
    if (fromData()) showDataRecord();
    setBusy(busy);
    refill();
  });
  $("add-data-file").addEventListener("change", () => {
    const file = $("add-data-file").files?.[0];
    $("add-data-label").textContent = file ? file.name : "Choose a CSV, Excel or JSON file";
  });
  $("add-data-go").addEventListener("click", async () => {
    if (app.template === null) return;
    let dataset;
    $("add-data-go").disabled = true;
    try {
      dataset = await sendData(engine, { file: $("add-data-file").files?.[0] ?? null, text: $("add-data-text").value });
    } catch (e) {
      say(msg, `That data can't be used: ${e.message}`);
      return;
    } finally {
      $("add-data-go").disabled = false;
    }
    if (dataset === null) {
      say(msg, "Choose a file or paste some data first.");
      return;
    }
    $("add-data-file").value = "";
    $("add-data-label").textContent = "Choose a CSV, Excel or JSON file";
    $("add-data-text").value = "";
    $("add-data").open = false;
    mapData(dataset);
  });
  $("schema-download").addEventListener("click", () => {
    if (app.template === null) return;
    const text = JSON.stringify(app.template.schema ?? {}, null, 2);
    downloadBytes(new TextEncoder().encode(text), `${app.state.savedName ?? app.stem()}.schema.json`, "application/schema+json");
    say(msg, "The data schema is downloading: records shaped like it (or a CSV with its paths as column names) fill this form without AI.");
  });
  $("fill-download").addEventListener("click", () => {
    if (filled === null) return;
    downloadBytes(filled.bytes, `${app.stem()}-filled.pdf`);
  });
  $("fill-clear").addEventListener("click", () => {
    clear();
    say(msg, "");
  });
  app.bus.on("template", () => {
    filled = null; // workbench.js ends the preview: a fill of the template before is out of date
    setBusy(busy);
  });
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
    app.data = null;
    app.pendingData = null;
    sampleFor = null;
    again = false;
    checked = undefined;
    filled = null;
    fills += 1;
    drawer.hidden = true;
    mismatch.hidden = true;
    say(msg, "");
    $("report").replaceChildren();
  });
}
