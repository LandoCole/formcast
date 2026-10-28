// The start screen and the match prompt: the one-shot flow's way in.
//
// Start: (1) the form: a saved template (it keeps its PDF, boxes and schema), a demo form or the
// person's own PDF; (2) optionally their data: a CSV, Excel or JSON file, or pasted text (records, a
// table, or plain prose); (3) the schema, folded away: by default the AI designs it from the form, or a
// stored or pasted JSON Schema (a demo form picks its own), and how the boxes are linked: by the AI with
// the agent checking its page pass (the default), by the page pass alone, or by hand. Then:
//   * a saved template opens as it is, with no detection and no AI; given data, a one-shot job maps
//     just the data (no AI when its columns are schema paths or match a mapping saved with the template);
//   * a PDF is first scored against the stored templates. When one fits, the match prompt offers Reuse
//     (open it as it is, then map the data as for a saved template), Adapt (an AI job starting from it)
//     or Build new. Otherwise, and on Build new, the seed (the form's own widgets and the detector's
//     boxes) goes on screen and, unless the boxes are to be linked by hand, a one-shot job designs the
//     schema (unless one was picked), maps the boxes and maps the data. Its result loads with one undo
//     step, back to the seed.
// With AI off, or linking by hand, a PDF opens on the seed with the schema picked, and data waits in the
// fill drawer.

import { checkTemplateFits } from "../core/store.js";
import { describeData, sendData } from "./data.js";

const PASTE = "__paste__";
const DERIVE = "__derive__";
const EMPTY_SCHEMA = { type: "object", properties: {} };
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
  const dataInput = $("data-file");
  const ai = engine.capabilities.ai && typeof engine.oneshot === "function";
  let forms = [];
  let saved = []; // stored templates that keep their PDF
  let pick = null; // what the match prompt is about: {doc, schema, schemaName, fits, dataset, check}

  function showStart(text = "") {
    match.hidden = true;
    start.hidden = false;
    load(); // a template saved since the page opened is offered too
    $("start-close").hidden = app.template === null;
    say(msg, text);
    $("open").disabled = false;
    $("form-pick").focus();
  }

  function hideAll() {
    start.hidden = true;
    match.hidden = true;
  }

  /**
   * Read the demo forms, stored schemas and saved templates, and offer them. Only what exists is offered:
   * with the demo off (no demo forms) the list is your saved templates, and with none of either the
   * form list goes away and choosing a PDF is the way in. Run again each time the start screen opens,
   * so a template saved since shows up; the choice made, when still there, is kept.
   */
  async function load() {
    try {
      const [f, s, t] = await Promise.all([engine.forms(), engine.records.schemas(), engine.templates.list()]);
      forms = f;
      app.schemas = s;
      saved = t.filter((x) => x.has_pdf);
    } catch (e) {
      say(msg, e.message);
    }
    const groups = [];
    if (saved.length) {
      const g = document.createElement("optgroup");
      g.label = "Saved templates · no detection, no AI";
      g.append(...saved.map((t) => new Option(t.title ? `${t.name} · ${t.title}` : t.name, `saved:${t.name}`)));
      groups.push(g);
    }
    if (forms.length) {
      const demo = document.createElement("optgroup");
      demo.label = "Demo forms";
      demo.append(...forms.map((f) => new Option(f.name, `demo:${f.name}`)));
      groups.push(demo);
    }
    const formPick = $("form-pick");
    const before = formPick.value;
    const prompt = forms.length ? "Choose a form" : "Choose a saved template";
    formPick.replaceChildren(new Option(prompt, ""), ...groups);
    if ([...formPick.options].some((o) => o.value === before)) formPick.value = before;
    formPick.hidden = groups.length === 0;
    $("form-pick-label").textContent = groups.length ? "1 · The form" : "1 · The form (a PDF)";
    $("file").closest(".file").classList.toggle("below", groups.length > 0);
    if (!fileInput.files?.[0]) $("file-label").textContent = groups.length ? "Or choose a PDF" : "Choose a PDF";
    const schemaBefore = $("schema-pick").value;
    $("schema-pick").replaceChildren(
      ...(ai ? [new Option("The AI designs it from the form", DERIVE)] : []),
      ...Object.entries(app.schemas).map(([name, schema]) => new Option(schema?.title ? `${name} · ${schema.title}` : name, name)),
      new Option("Paste a JSON Schema", PASTE),
    );
    if ([...$("schema-pick").options].some((o) => o.value === schemaBefore)) $("schema-pick").value = schemaBefore;
    sync();
  }

  /** The saved template picked, or null. */
  const savedPick = () => ($("form-pick").value.startsWith("saved:") ? $("form-pick").value.slice(6) : null);
  const hasData = () => Boolean(dataInput.files?.[0]) || $("data-text").value.trim() !== "";

  /** The AI links the boxes (one-shot job), rather than opening them unlinked. */
  const mapping = () => ai && $("ai-mode").value !== "off";

  /** Show what fits the choices: the schema part only for a PDF, and the button's word for what happens. */
  function sync() {
    const value = $("schema-pick").value;
    $("schema-text").hidden = value !== PASTE;
    const fromSaved = savedPick() !== null;
    $("schema-group").hidden = fromSaved;
    $("ai-mode").hidden = !ai;
    const how = !ai ? "" : { check: "", fast: " · one pass", off: " · link by hand" }[$("ai-mode").value];
    $("schema-sum").textContent =
      (value === DERIVE ? "the AI designs it from the form" : value === PASTE ? "pasted" : value ? `stored · ${value}` : "") + how;
    if (fromSaved) $("open").textContent = hasData() ? "Open and fill" : "Open";
    else $("open").textContent = mapping() ? "Map with AI" : "Open";
  }

  $("form-pick").addEventListener("change", () => {
    fileInput.value = "";
    $("file-label").textContent = forms.length || saved.length ? "Or choose a PDF" : "Choose a PDF";
    // a demo form comes with its own schema (and records); the AI can still design one when asked
    const form = forms.find((f) => `demo:${f.name}` === $("form-pick").value);
    if (form?.schema) $("schema-pick").value = form.schema;
    sync();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    $("file-label").textContent = file ? file.name : forms.length || saved.length ? "Or choose a PDF" : "Choose a PDF";
    if (file) {
      $("form-pick").value = "";
      if (ai) $("schema-pick").value = DERIVE; // your own PDF: the one-shot default
    }
    sync();
  });
  $("ai-mode").addEventListener("change", sync);
  dataInput.addEventListener("change", () => {
    const file = dataInput.files?.[0];
    $("data-label").textContent = file ? file.name : "Choose a CSV, Excel or JSON file";
    sync();
  });
  $("data-text").addEventListener("input", sync);
  $("schema-pick").addEventListener("change", sync);
  $("doc-name").addEventListener("click", () => showStart());
  $("start-close").addEventListener("click", hideAll);

  /** The schema chosen on the start screen: {schema, schemaName} (schema null: the AI designs it), or a message. */
  function chosenSchema() {
    const value = $("schema-pick").value;
    if (value === DERIVE) return { schema: null, schemaName: null };
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
    const value = $("form-pick").value;
    const file = fileInput.files?.[0] ?? null;
    if (!value && file === null) {
      say(msg, forms.length || saved.length ? "Choose a form or a PDF first." : "Choose a PDF first.");
      return;
    }
    const savedName = savedPick();
    const chosen = savedName === null ? chosenSchema() : { schema: null, schemaName: null };
    if (chosen.error) {
      say(msg, chosen.error);
      return;
    }
    $("open").disabled = true;
    let dataset = null;
    if (hasData()) {
      say(msg, "Reading your data…");
      try {
        dataset = await sendData(engine, { file: dataInput.files?.[0] ?? null, text: $("data-text").value });
      } catch (e) {
        showStart(`Your data can't be used: ${e.message}`);
        return;
      }
    }
    say(msg, "Opening…");
    if (savedName !== null) {
      await openSaved(savedName, dataset);
      return;
    }
    let doc;
    try {
      doc = file ? await engine.open(file, { name: file.name }) : await engine.open(value.slice(5));
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
    pick = { doc, ...chosen, fits, dataset, check: $("ai-mode").value === "check", ai: mapping() };
    if (fits.length === 0) {
      await buildNew(pick);
      return;
    }
    showMatch(fits[0]);
  });

  /** Open a saved template on its own PDF; with data, map just the data. */
  async function openSaved(name, dataset) {
    let doc;
    let template;
    try {
      [doc, template] = await Promise.all([engine.open({ template: name }), engine.templates.get(name)]);
    } catch (e) {
      showStart(e.message);
      return;
    }
    const misfit = checkTemplateFits(template, doc);
    if (misfit) {
      showStart(misfit);
      return;
    }
    hideAll();
    app.setDocument({
      doc, template, savedName: name,
      source: { kind: "stored", text: `Saved template ${name}` },
      schemaName: app.schemaNameOf(template.schema),
    });  // prettier-ignore
    if (dataset !== null) app.jobs.oneshot({ template, dataset, saved: name });
  }

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
    if (pick.dataset !== null) app.jobs.oneshot({ template: s.template, dataset: pick.dataset, saved: s.name });
  });

  $("adapt").addEventListener("click", async () => {
    busy(true);
    const s = await stored({ forAdapt: true });
    if (s === null) {
      busy(false);
      return;
    }
    // the seed (with the stored template's schema) goes on screen first, so one Undo returns to it
    const opened = await buildNew({ doc: pick.doc, schema: s.template.schema, schemaName: app.schemaNameOf(s.template.schema), ai: false });
    busy(false);
    if (!opened) return;
    if (pick.dataset !== null) app.pendingData = pick.dataset; // mapped when the fill drawer opens
    app.jobs.adapt(s.template);
  });

  $("build-new").addEventListener("click", async () => {
    busy(true);
    await buildNew(pick);
    busy(false);
  });

  /**
   * Seed the document and put it on screen, then (with AI on, unless `ai` is false) start the one-shot
   * job. Resolves false (with a message) when the seed fails.
   */
  async function buildNew({ doc, schema, schemaName, dataset = null, check = true, ai: runAi = ai }) {
    if (schema == null && !runAi) {
      showStart("Only the AI can design the schema. Pick a schema to link the boxes by hand, or let the AI link them.");
      return false;
    }
    if (match.hidden) say(msg, READING);
    else $("match-hint").textContent = READING; // asked from the match prompt, which stays up while it waits
    let template;
    try {
      template = await engine.seed(doc, schemaName ?? schema ?? EMPTY_SCHEMA);
    } catch (e) {
      showStart(e.message);
      return false;
    }
    hideAll();
    app.setDocument({ doc, template, source: { kind: "seed", text: "The form's own fields and detected boxes" }, schemaName });
    if (runAi) {
      app.jobs.oneshot({ schema: schemaName ?? schema, dataset, check });
    } else if (dataset !== null) {
      app.pendingData = dataset;
      app.notice(`Your data (${describeData(dataset)}) is waiting: link the boxes, then open Fill to map it.`);
    }
    return true;
  }

  load();
}
