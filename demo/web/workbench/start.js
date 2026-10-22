// The start screen and the match prompt. Start: pick a demo form or upload a PDF, and a stored
// schema or a pasted one. Then, when the engine can match, the stored templates are scored against
// the PDF; if one fits (the server's action is reuse or adapt), the prompt offers Reuse (load it as
// it is), Adapt (an AI job starting from it, when AI is on) or Build new (the seed: the form's own
// widgets and the detector's boxes). Otherwise the seed opens straight away.

import { checkTemplateFits } from "../core/store.js";

const PASTE = "__paste__";
// The server reads a PDF (its form map) once, starting when the document opens; the first time it also
// downloads the detector weights. Matching and seeding wait for that reading.
const FIRST_TIME = "the first time can take a minute while the detector downloads";
const READING = `Reading the form (${FIRST_TIME})…`;

/** @param {object} app see workbench.js */
export function mountStart(app) {
  const { $, engine, say } = app;
  const start = $("start");
  const match = $("match");
  const msg = $("start-msg");
  const fileInput = $("file");
  let forms = [];
  let pick = null; // what the match prompt is about: {doc, schema, schemaName, fits}

  function showStart(text = "") {
    match.hidden = true;
    start.hidden = false;
    $("start-close").hidden = app.template === null;
    say(msg, text);
    $("open").disabled = false;
    $("form-pick").focus();
  }

  function hideAll() {
    start.hidden = true;
    match.hidden = true;
  }

  async function load() {
    try {
      const [f, s] = await Promise.all([engine.forms(), engine.records.schemas()]);
      forms = f;
      app.schemas = s;
    } catch (e) {
      say(msg, e.message);
    }
    const formPick = $("form-pick");
    formPick.replaceChildren(new Option("Choose a demo form", ""), ...forms.map((f) => new Option(f.name, f.name)));
    const schemaPick = $("schema-pick");
    schemaPick.replaceChildren(
      ...Object.entries(app.schemas).map(([name, schema]) => new Option(schema?.title ? `${name} · ${schema.title}` : name, name)),
      new Option("Paste a JSON Schema", PASTE),
    );
    syncPaste();
  }

  function syncPaste() {
    $("schema-text").hidden = $("schema-pick").value !== PASTE;
  }

  $("form-pick").addEventListener("change", () => {
    fileInput.value = "";
    $("file-label").textContent = "Choose a PDF";
    const form = forms.find((f) => f.name === $("form-pick").value);
    if (form?.schema) $("schema-pick").value = form.schema;
    syncPaste();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    $("file-label").textContent = file ? file.name : "Choose a PDF";
    if (file) $("form-pick").value = "";
  });
  $("schema-pick").addEventListener("change", syncPaste);
  $("doc-name").addEventListener("click", () => showStart());
  $("start-close").addEventListener("click", hideAll);

  /** The schema chosen on the start screen: {schema, schemaName}, or a message. */
  function chosenSchema() {
    const value = $("schema-pick").value;
    if (value !== PASTE) {
      const schema = app.schemas[value];
      return schema ? { schema, schemaName: value } : { error: "Choose a schema." };
    }
    let schema;
    try {
      schema = JSON.parse($("schema-text").value);
    } catch (e) {
      return { error: `This isn't valid JSON: ${e.message}` };
    }
    if (schema === null || typeof schema !== "object" || Array.isArray(schema)) {
      return { error: "A JSON Schema is an object, like {\"type\": \"object\", \"properties\": {...}}." };
    }
    return { schema, schemaName: null };
  }

  $("open").addEventListener("click", async () => {
    say(msg, "");
    const formName = $("form-pick").value;
    const file = fileInput.files?.[0] ?? null;
    if (!formName && file === null) {
      say(msg, "Choose a demo form or a PDF first.");
      return;
    }
    const chosen = chosenSchema();
    if (chosen.error) {
      say(msg, chosen.error);
      return;
    }
    $("open").disabled = true;
    say(msg, "Opening…");
    let doc;
    try {
      doc = formName ? await engine.open(formName) : await engine.open(file, { name: file.name });
    } catch (e) {
      showStart(e.message);
      return;
    }
    let fits = [];
    if (engine.capabilities.matches) {
      say(msg, `Reading the form and comparing it with the stored templates (${FIRST_TIME})…`);
      try {
        fits = (await engine.matches(doc)).filter((m) => m.action === "reuse" || m.action === "adapt");
      } catch {
        fits = []; // matching is a convenience: without it the form still opens
      }
    }
    if (fits.length === 0) {
      await buildNew({ doc, ...chosen });
      return;
    }
    pick = { doc, ...chosen, fits };
    showMatch(fits[0]);
  });

  /** The match prompt for one stored template. */
  function showMatch(m) {
    const text = $("match-text");
    const name = document.createElement("em");
    name.textContent = m.name;
    text.replaceChildren("This looks like ", name, ` (${m.score.toFixed(2)}): ${m.explanation}`);
    const select = $("match-pick");
    select.replaceChildren(...pick.fits.map((f) => new Option(`${f.name} · ${f.score.toFixed(2)}`, f.name)));
    select.value = m.name;
    $("match-pick-group").hidden = pick.fits.length < 2;
    const ai = engine.capabilities.ai;
    $("adapt").hidden = !ai;
    $("match-hint").textContent =
      m.action === "reuse"
        ? "The same form: Reuse opens its template as it is."
        : `A different version of the form: Reuse keeps the boxes where they are (rebind it to this PDF when you fill)${ai ? "; Adapt has the AI move them to fit this one" : ""}.`;
    start.hidden = true;
    match.hidden = false;
    $("reuse").disabled = $("adapt").disabled = $("build-new").disabled = false;
    $("reuse").focus();
  }

  $("match-pick").addEventListener("change", () => {
    const m = pick?.fits.find((f) => f.name === $("match-pick").value);
    if (m) showMatch(m);
  });

  function busy(on) {
    for (const id of ["reuse", "adapt", "build-new"]) $(id).disabled = on;
  }

  /**
   * The stored template picked in the prompt, checked against the PDF; null after a message. Reuse
   * needs the same pages (count and sizes); an adapt moves the boxes onto pages of other sizes, so
   * it needs only the same page count.
   */
  async function stored({ forAdapt = false } = {}) {
    const name = $("match-pick").value;
    try {
      const template = await engine.templates.get(name);
      const sameCount = template.form.page_count === pick.doc.pageCount;
      const misfit = forAdapt && sameCount ? null : checkTemplateFits(template, pick.doc);
      if (misfit) {
        $("match-hint").textContent = misfit;
        return null;
      }
      return { name, template };
    } catch (e) {
      $("match-hint").textContent = e.message;
      return null;
    }
  }

  $("reuse").addEventListener("click", async () => {
    busy(true);
    const s = await stored();
    busy(false);
    if (s === null) return;
    hideAll();
    app.setDocument({
      doc: pick.doc,
      template: s.template,
      source: { kind: "stored", text: `Stored template ${s.name}` },
      schemaName: app.schemaNameOf(s.template.schema) ?? pick.schemaName,
      savedName: s.name,
    });
  });

  $("adapt").addEventListener("click", async () => {
    busy(true);
    const s = await stored({ forAdapt: true });
    if (s === null) {
      busy(false);
      return;
    }
    // the seed (with the stored template's schema) goes on screen first, so one Undo returns to it
    const opened = await buildNew({ doc: pick.doc, schema: s.template.schema, schemaName: app.schemaNameOf(s.template.schema) });
    busy(false);
    if (opened) app.jobs.adapt(s.template);
  });

  $("build-new").addEventListener("click", async () => {
    busy(true);
    await buildNew(pick);
    busy(false);
  });

  /** Seed the document and put it on screen. Resolves false (with a message) when the seed fails. */
  async function buildNew({ doc, schema, schemaName }) {
    if (match.hidden) say(msg, READING);
    else $("match-hint").textContent = READING; // asked from the match prompt, which stays up while it waits
    try {
      const template = await engine.seed(doc, schemaName ?? schema);
      hideAll();
      app.setDocument({ doc, template, source: { kind: "seed", text: "The form's own fields and detected boxes" }, schemaName });
      return true;
    } catch (e) {
      showStart(e.message);
      return false;
    }
  }

  load();
}
