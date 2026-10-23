// The Fill step, a test bench: pick a made-up record (or edit its JSON), Fill shows the filled PDF
// in the page view (the very bytes Download saves, drawn by pdf.js with the fields' appearances),
// Download saves them and Clear goes back to the form with its boxes. Picking another record while
// the filled PDF is on show fills again, always from the template and the blank form, never on top
// of the filled file. pdf-lib loads on the first fill (the engine imports it then). Filling has a
// time limit (app.fillTimeoutMs), loading pdf-lib and opening the result included; a result that
// comes after it, or after the template or the form changed, is dropped.

import { downloadBytes } from "../core/store.js";
import { seconds, withinTime } from "./limits.js";

const PROTECTED =
  "This PDF is protected against changes, so it can't be filled in the browser. The full library can fill it.";

/** @param {object} app see showcase.js */
export function mountFill(app) {
  const { $, bus, engine, session, state, say } = app;
  const msg = $("fill-msg");
  const recordError = $("record-error");
  const pick = $("record-pick");
  let records = []; // [{title, record}] on offer
  let placeholder = true; // the records are one made-up record for the schema, not the sample's
  let busy = false;
  let filling = 0; // bumps on every fill and whenever the template changes, so only a current fill is shown

  /** A file name stem for downloads: the sample's form id, or the visitor's file name. */
  function stem() {
    if (state.sample) return state.sample.manifest.form ?? state.sample.id;
    return state.name.replace(/\.pdf$/i, "") || "form";
  }

  /** Put a record in the text box. */
  function showRecord(record) {
    $("record").value = `${JSON.stringify(record, null, 2)}\n`;
    say(recordError, "");
  }

  /**
   * The records the Fill step offers: a sample's made-up records (a picker), or one placeholder
   * record for a visitor's PDF or pasted schema (no picker).
   * @param {{title: string, record: object}[]} list
   * @param {{placeholder?: boolean}} [opts]
   */
  app.setRecords = (list, { placeholder: made = false } = {}) => {
    records = list;
    placeholder = made;
    pick.replaceChildren(...list.map((r, i) => new Option(r.title, String(i))));
    pick.value = "0";
    showRecord(list[0]?.record ?? {});
    $("record-pick-group").hidden = placeholder;
    $("record-label").textContent = placeholder ? "Record" : "The record as JSON";
    $("record-hint").textContent = placeholder
      ? "A made-up record for the schema. Edit it or paste your own."
      : "Made-up records for this form. Pick one, or edit the JSON.";
    if (state.preview !== null) app.setPreview(null);
  };

  function readRecord() {
    say(recordError, "");
    try {
      return { record: JSON.parse($("record").value) };
    } catch (e) {
      say(recordError, `The record isn't valid JSON: ${e.message}`);
      return null;
    }
  }

  /** The title the filled PDF is shown under: the picked record's, unless its JSON was edited. */
  function titleOf(record) {
    const picked = records[Number(pick.value)];
    if (!picked || JSON.stringify(picked.record) !== JSON.stringify(record)) return "your edited record";
    return placeholder ? "a made-up record" : picked.title;
  }

  async function fill() {
    if (busy || app.template === null || state.doc === null) return;
    say(msg, "");
    if (state.doc.encrypted) {
      say(msg, PROTECTED);
      return;
    }
    const parsed = readRecord();
    if (parsed === null) return;
    const mine = ++filling;
    const token = session.generation;
    busy = true;
    app.render();
    say(msg, "Filling…");
    try {
      await session.idle(); // edits still on their way are part of what gets filled
      if (mine !== filling) return;
      const template = session.template;
      const late = `Filling took more than ${seconds(app.fillTimeoutMs)}. Try again.`;
      const work = (async () => {
        const { pdfBytes } = await engine.fill(template, state.doc, parsed.record);
        return { bytes: pdfBytes, doc: await engine.open(pdfBytes) };
      })();
      const out = await withinTime(work, app.fillTimeoutMs, late, { onLate: (o) => o.doc.destroy().catch(() => {}) });
      if (mine !== filling || token !== session.generation || template !== session.template || app.step !== "fill") {
        out.doc.destroy().catch(() => {}); // the form, the template or the step changed meanwhile
        if (mine === filling) say(msg, "");
        return;
      }
      const title = titleOf(parsed.record);
      app.setPreview({ doc: out.doc, bytes: out.bytes, title });
      say(msg, `Filled with ${title}. This is the PDF Download saves, and its fields stay editable.`);
    } catch (e) {
      if (mine === filling) say(msg, `That didn't work: ${e.message}`);
    } finally {
      if (mine === filling) busy = false;
      app.render();
    }
  }

  $("fill").addEventListener("click", () => fill());

  pick.addEventListener("change", () => {
    const picked = records[Number(pick.value)];
    if (!picked) return;
    showRecord(picked.record);
    if (state.preview !== null || busy) {
      busy = false; // a fill still running is overtaken by this one
      fill();
    }
  });

  $("download").addEventListener("click", () => {
    if (state.preview === null) return;
    downloadBytes(state.preview.bytes, `${stem()}-filled.pdf`);
    say(msg, "The filled PDF is downloading: the one on the page.");
  });

  $("clear").addEventListener("click", () => {
    filling += 1;
    busy = false;
    app.setPreview(null);
    say(msg, "");
  });

  bus.on("template", () => {
    filling += 1; // a fill of the template before is not shown (showcase.js ends a preview on show)
    busy = false;
    if (state.preview === null) say(msg, "");
  });
  app.onDocument(() => {
    filling += 1;
    busy = false;
    say(msg, "");
  });

  app.onRender(() => {
    const none = app.template === null || state.doc === null;
    $("fill").disabled = none || busy;
    $("fill").textContent = state.preview !== null ? "Fill again" : "Fill the form";
    $("download").disabled = state.preview === null;
    $("clear").disabled = state.preview === null;
  });
}
