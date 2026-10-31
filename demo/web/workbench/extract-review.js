// A run of a saved extraction template over a batch, and its review (`.view[data-only=review]`): Run on all from the
// setup screen, the review itself, its exports and Send to fill, and the saved runs and templates the choice lists.
//
// Run on all (#run, the setup screen's toolbar) reads every document of the setup's batch with its template. A setup
// never saved, or changed since it was, is saved first with the setup screen's name prompt (`app.setup.promptSave`);
// closing the prompt unsaved starts nothing. A setup with no batch (a saved extraction template reopened) opens the
// Extract modal in its "Run on a batch" mode instead (`app.extract.openRunOn`): the person adds the documents there,
// and its Run on these → starts the run on them, which then goes on as below. Then the `extract-run` job starts,
// asking the AI about the failing documents only when the Extract modal's "AI per document" (#ai-fallback) is on, and the run panel (#running) shows
// its stages live (`showStages`: "Read every document", with "N of M" under it) and "done / total documents" under
// them. Cancel asks the job to stop once the documents being read are written; the run keeps what it read, so a
// cancelled run opens its review of those, and Run on all again carries on where it stopped. A run that ends with an
// error, or can't start (another run of it, every job slot taken, the AI asked for with none configured), says why in
// the notice. A run done opens its review.
//
// Back and forth. ← Go back to setup (#back-setup, on the review's toolbar) shows the setup the run came from, and
// Go to review → (#go-review, on the setup's toolbar) comes back while that setup is on screen (`app.extract.linked`).
// A run with no setup open (a saved run, a command line run) reopens its template on the setup screen first. A setup
// changed since the run (`app.setup.revision`, or built again with Change the setup) can't be reviewed as it stands:
// Go to review asks first (#reprocess-box), saying the changes take effect once every document is read again and how
// many decisions stay with the earlier run. Reprocess saves it under its name, asking nothing, and runs it as Run on
// all does (a setup that never had a name asks for one; a template reopened has no batch and picks one). A setup left
// unchanged goes straight back, the review as it was.
//
// The review, as the mockup lays it out. Its numbers (#stats: the documents, the complete ones, the ones with a
// required problem, the complete ones with an optional problem only, the ones set aside and the run time, `runStats`)
// are the Run's summary, updated by every decision. See details beside them opens #field-details, only to read and
// close (`fieldBreakdown`): the run's totals, then a row per field, required ones first, the most problems first in
// each level, a repeated group's items folded into one, with a bar of how its values ended, the share found and how
// many were found, blank, not located, doubtful, failing a check, not applicable and reviewed. On the left the documents that still
// have problems (#docs, `engine.extract.problems`, 50 at a time, More for the next 50) in two sections, switched above
// the list (#level-pick, `levelSwitch`, with how many documents each lists): Required, first, lists the documents with
// a required problem and shows only their required problems; Optional the documents with an optional problem
// (complete or not), only those, each card with why the field is optional, under a note (#optional-note) that they
// never stop a document from being complete. The field and problem filters apply inside the section; a field picked
// in the filter shows the section it is in. The problem filter's "Set aside" lists the
// documents not read instead (`engine.extract.setAside`), each with why and the closest template, and the sections
// wait meanwhile. The middle column is the chosen document's
// page (`runPageUrl`, ‹ › through its pages, the setup screen's zoom: `mountZoom`) with the place each problem field was
// read, or looked for, outlined (`rect`, in points from the page's top left). The results have no place for a field with
// no value or read from a form field, so choosing a document asks the server where the template says they should be
// (`engine.extract.places`, `locate`). Picking a card goes to its field's page and scrolls to it, highlighted with a
// tag: "Should be here" for a field with no value, otherwise the problem ("Doubtful", "Fails a check"). A field with no
// place at all (its label isn't on the document, say) has nothing to point at; the caption says so. A page image that
// fails is asked for again through the engine (`runPage`) to learn why: a 404 past page 1 is past the document's last
// page, so › stops there; anything else says so under the page with Try again. On the right a card
// per problem (#problems): the field's title, the problem, what was read and why it is a problem, and the decisions:
// Use this value (the value typed), Accept as read (only when a value was read) and Not in this document. A decision
// settles its card (only that card is drawn again; it stays disabled while the decision is on its way), and the stats,
// the document's line and the list's legend follow; the documents settled stay listed (marked "all settled") until the
// list is asked for again. The server lists only documents that still have problems, so More asks from the listed
// ones not settled since, and the label and legend count from the list as it was first asked for.
//
// Export xlsx and Export Parquet download the run's results (`engine.extract.exportRun`); a server without pyarrow
// answers Parquet with `not_supported`, which hides the button and says so. Send to fill → makes the run's documents a
// dataset (`toData`: only found values, ruling P13) and opens the fill start screen with it as Your data
// (`app.start.showStart`), the extraction's destination form picked when it was made for one; under Your data the
// start screen says how many documents still have problems (their unsettled fields are gaps the fill reports), when
// the dataset holds only the first rows, and when the destination couldn't be read.
//
// The choice (#choose) lists, under its two choices, the saved runs (#saved-runs, newest first: "Open a saved run") and
// extraction templates (#saved-extractions: reopened on the setup screen, its samples read again, where Run on all
// runs it on a new batch), each time it is shown (saying so in #choose-msg when they can't be read, and taking that
// back once they can), and opens a command line run's folder on the server's machine by its path (#run-folder).

import { downloadBytes } from "../core/store.js";
import { asideLine, decisionText, docLine, fieldBreakdown, fieldTable, levelSwitch, markTag, noValue, problemLabel, problemName, reprocessNote, runLine, runStats, sendNotice } from "./extract-model.js";
import { mountZoom } from "./extract-setup.js";
import { showStages } from "./jobs.js";

/** Documents listed at a time (the server's default page). */
const PAGE = 50;
/** The page images' resolution, in dots per inch. */
const DPI = 150;
/** What the caption says under a page whose problems are outlined (the mockup's words). */
const CAPTION = "The boxes are where each problem field was read, or looked for";
/** What the legend says under the documents set aside. */
const ASIDE_LEGEND =
  "Not read, so not in the values; the export lists them on their own. To read them, run Extract from PDFs again with just these documents.";

const number = (n) => Number(n ?? 0).toLocaleString("en-US");
const plural = (n, word, many = `${word}s`) => `${number(n)} ${n === 1 ? word : many}`;
const pct = (fraction) => `${fraction * 100}%`;
const keyOf = (docId, path) => `${docId}\n${path}`;

/** An element with `props` set and `children` appended. */
function make(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  Object.assign(el, props);
  el.append(...children);
  return el;
}

/** When a run started, in words ("" when it does not say). */
function startedText(iso) {
  const when = iso ? new Date(iso) : null;
  if (when === null || Number.isNaN(when.getTime())) return "";
  return `started ${when.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`;
}

/**
 * @param {object} app see workbench.js
 * @returns {{openRun: (id: string, opts?: {setupId?: string | null}) => Promise<boolean>, refreshChoices: () => Promise<void>,
 *   pageSize: number}} `pageSize`: documents listed at a time (PAGE; a test may lower it)
 */
export function mountReview(app) {
  const { $, engine } = app;
  const calls = engine.extract;
  const pg = $("rpage");
  const chooseMsg = $("choose-msg");

  const state = {
    run: null, // the Run on show, as the server last gave it
    setupId: null, // the setup it was run from (Run on all), or null
    revision: null, // that setup's `revision` as this run read it; null when it has been built again since
    level: "required", // the section listed: "required" or "optional"
    path: "", // the field filter ("" for every field)
    status: "", // the problem filter: "" for every problem, a status, or "set_aside"
    docs: [], // the documents listed so far
    total: 0, // how many documents the filters matched when they were listed (the ones settled since included)
    listed: false, // the run's documents have been listed once
    doc: null, // the doc_id shown
    problem: null, // the path of the problem picked in it
    page: 0, // its page shown, from 0
    turned: false, // the person has turned the page since picking a problem: a place found late moves nothing
    reveal: false, // scroll to the picked problem's mark once it is drawn
    pages: Infinity, // its page count, once a page past the end was asked for
    size: null, // the page image's [width, height] in points, once it has loaded
  };
  const api = { pageSize: PAGE };
  const decided = new Map(); // keyOf(doc, path) -> what was decided, as its card says it
  const deciding = new Set(); // keyOf(doc, path) of the decisions on their way
  let listing = 0; // the list asked for last
  let choices = 0; // the choice's lists asked for last
  let listFailed = ""; // what #choose-msg said when the choice's lists last couldn't be read ("" when they could)
  let running = false; // Run on all is saving, starting or following its job
  let job = null; // the run job followed: {handle, stopping}

  app.extract.linked = () => state.run !== null && state.setupId !== null && app.setup?.current?.id === state.setupId;
  app.extract.onShow = () => {
    refreshChoices();
  };
  // Change the setup builds a new setup: it is still the one this run came from, changed
  app.bus.on("extract:setup", ({ setup, rebuilt }) => {
    if (!rebuilt || !app.extract.linked()) return;
    state.setupId = setup.id;
    state.revision = null;
  });

  const hideOverlays = () => {
    for (const o of document.querySelectorAll(".overlay")) o.hidden = true;
  };
  const seeDetails = $("see-details");
  const isAside = () => state.status === "set_aside";
  const isOptional = () => !isAside() && state.level === "optional";
  const docOf = (docId) => state.docs.find((d) => d.doc_id === docId) ?? null;
  const settledOf = (d) => new Set((d.problems ?? []).filter((p) => decided.has(keyOf(d.doc_id, p.path))).map((p) => p.path));
  /** The listed documents whose every problem has been settled here since they were listed. */
  const settledListed = () => state.docs.filter((d) => d.problems && settledOf(d).size === d.problems.length).length;

  // ------------------------------------------------------------------ Run on all

  /**
   * Save the setup when it is not saved as it is, then read its batch with it; a setup with no batch picks one in
   * the Extract modal's Run on a batch mode first (see the top).
   */
  async function runOnAll({ quietly = false } = {}) {
    const setup = app.setup?.current;
    if (running || !setup) return;
    running = true;
    try {
      await app.setup.settled(); // an edit sent as the click took the focus from its box is part of what is run
      if (app.setup.current?.id !== setup.id) return;
      let name = app.setup.savedName;
      if (name === null || app.setup.changed) {
        name = await (quietly ? app.setup.saveChanges() : app.setup.promptSave());
        if (name === null) return; // closed without saving
      }
      if (setup.batch) await follow(setup, setup.batch, name);
      else app.extract.openRunOn(name, (batch) => follow(setup, batch, name));
    } finally {
      running = false;
    }
  }

  /** Start the run job of the saved template `name` on the batch `batch` and follow it in the run panel. */
  async function follow(setup, batch, name) {
    const panel = $("running");
    const run = { handle: null, stopping: false };
    job = run;
    hideOverlays();
    $("run-stages").replaceChildren();
    $("run-readout").textContent = "Starting…";
    $("run-cancel").disabled = false;
    panel.hidden = false;
    let ended;
    try {
      run.handle = await calls.startRun({ extraction: name, batch, ai: $("ai-fallback").checked });
      if (run.stopping) run.handle.cancel().catch(() => {}); // Cancel was pressed while it was starting
      run.handle.onUpdate = (j) => showJob(j, run);
      ended = await run.handle.done;
    } catch (e) {
      panel.hidden = true;
      app.notice(`The run couldn't be started: ${e.message}`);
      return;
    } finally {
      job = null;
    }
    showJob(ended, run);
    panel.hidden = true;
    const runId = ended.result?.run;
    if (ended.status === "error" || !runId) {
      app.notice(`The run stopped: ${ended.error || "an unknown error"}`);
      return;
    }
    if (!(await openRun(runId, { setupId: setup.id }))) return;
    if (ended.status === "cancelled") {
      const { done = 0, total = 0 } = ended.result;
      app.notice(`The run was stopped after ${number(done)} of ${plural(total, "document")}; Run on all again carries on from there.`);
    }
  }

  /** The run job's stages, and how many documents it has read. */
  function showJob(j, run) {
    const current = showStages($("run-stages"), j);
    const { done, total } = j.result ?? {};
    let text = typeof total === "number" ? `${number(done)} / ${plural(total, "document")}` : "Starting…";
    if (current?.key === "ai") text = `${current.label}${current.detail ? `: ${current.detail}` : "…"}`;
    if (run.stopping && j.status === "running") text = "Stopping once the documents being read are written…";
    $("run-readout").textContent = text;
  }

  $("run").addEventListener("click", () => runOnAll());
  $("run-cancel").addEventListener("click", () => {
    const run = job;
    if (run === null || run.stopping) return;
    run.stopping = true;
    $("run-cancel").disabled = true;
    $("run-readout").textContent = "Stopping once the documents being read are written…";
    run.handle?.cancel().catch((e) => {
      run.stopping = false;
      $("run-cancel").disabled = false;
      $("run-readout").textContent = `The run couldn't be stopped: ${e.message}`;
    });
  });

  // ------------------------------------------------------------------ opening a run

  /**
   * Open the review of the run `id` (`setupId`: the setup it was run from); resolves whether it opened. A run that
   * can't be read says why in the notice.
   */
  async function openRun(id, { setupId = null } = {}) {
    let run;
    try {
      run = await calls.run(id);
    } catch (e) {
      app.notice(`That run couldn't be opened: ${e.message}`);
      return false;
    }
    show(run, setupId);
    return true;
  }

  /** Show the review of `run`, from its first document with problems. */
  function show(run, setupId) {
    hideOverlays();
    app.extract.setMode("extract");
    const revision = setupId === null ? null : app.setup.revision;
    Object.assign(state, { run, setupId, revision, level: "required", path: "", status: "", docs: [], total: 0, listed: false, doc: null, problem: null, page: 0 });
    decided.clear();
    const fieldPick = $("filter-path");
    fieldPick.replaceChildren(
      new Option("Every field", ""),
      ...fieldOptions(fieldTable(run.fields)),
    );
    fieldPick.disabled = false;
    $("filter-status").value = "";
    const missing = $("filter-missing"); // only a run made before Blank and Not located were told apart has it
    missing.hidden = missing.disabled = !state.run.fields.some((f) => f.missing > 0);
    app.notice("");
    app.extract.showView("review");
    renderToolbar();
    renderStats();
    renderDocs();
    renderDoc();
    list();
  }

  /** The field filter's options for `rows`: each field's title, with its path when another field has the same title. */
  function fieldOptions(rows) {
    const taken = (title) => rows.filter((r) => r.title === title).length > 1;
    return rows.map((row) => new Option(taken(row.title) ? `${row.title} (${row.path})` : row.title, row.path));
  }

  /** The numbers `stats` ({key, value, label}), as the review's tiles. */
  function statTiles(stats) {
    return stats.map((s) => {
      const el = make("div", { className: "stat" }, make("b", { textContent: s.value }), make("span", { textContent: s.label }));
      el.dataset.stat = s.key;
      return el;
    });
  }

  function renderToolbar() {
    const run = state.run;
    $("doc-pick").textContent = [run.name, plural(run.summary.total, "document"), run.extraction].join(" · ");
  }

  function renderStats() {
    $("stats").replaceChildren(...statTiles(runStats(state.run.summary)), seeDetails);
    seeDetails.hidden = state.run.fields.length === 0;
    renderLevels();
  }

  /** The section switch, with how many documents each section lists, and the Optional section's note. */
  function renderLevels() {
    const texts = new Map(levelSwitch(state.run.summary).map((s) => [s.level, s.text]));
    for (const btn of $("level-pick").querySelectorAll("[data-level]")) {
      btn.textContent = texts.get(btn.dataset.level);
      btn.setAttribute("aria-pressed", String(btn.dataset.level === state.level));
      btn.disabled = isAside(); // the documents set aside have no problems of either level
    }
    $("optional-note").hidden = !isOptional();
  }

  /** List the section `level` ("required" or "optional"), the filters as they are. */
  function showLevel(level) {
    if (state.level === level) return;
    state.level = level;
    renderLevels();
    list();
  }

  /** The section the field `path` (as the field filter has it) is in; the one shown for every field (""). */
  function levelOf(path) {
    const row = fieldTable(state.run.fields).find((r) => r.path === path);
    if (row === undefined) return state.level;
    return row.required ? "required" : "optional";
  }

  /** See details: the run's totals and each field's results, to read and close. */
  function renderDetails() {
    const { tiles, key, sections } = fieldBreakdown(state.run.fields);
    const { complete, with_problems: withProblems } = state.run.summary;
    $("details-what").textContent = `${plural(sections.reduce((n, s) => n + s.rows.length, 0), "field")} read from ${plural(complete + withProblems, "document")}. A value is one field on one document.`;
    $("details-tiles").replaceChildren(...statTiles(tiles));
    $("details-key").replaceChildren(
      ...key.map((part) => {
        const item = make("li", { textContent: part.label });
        item.dataset.part = part.key;
        return item;
      }),
    );
    $("details-sections").replaceChildren(...sections.map(detailsSection));
  }

  /** A section of See details: its heading and its table. */
  function detailsSection(section) {
    const heads = ["Field", "Results", "Found %", ...section.rows[0].parts.map((p) => p.label), "Reviewed"];
    const head = make("tr", {}, ...heads.map((text) => make("th", { scope: "col", textContent: text })));
    const table = make("table", { className: "field-table" }, make("thead", {}, head), make("tbody", {}, ...section.rows.map(detailsRow)));
    table.ariaLabel = section.label;
    return make("div", { className: "details-section" }, make("h3", { className: "label", textContent: `${section.label} · ${section.rows.length}` }), table);
  }

  /** A row of See details: the field, why it has its level, a bar of how its values ended and their counts. */
  function detailsRow(row) {
    const name = make("th", { scope: "row", textContent: row.title });
    if (row.items !== null) name.append(make("small", { textContent: plural(row.items, "item") }));
    if (row.reason) name.append(make("small", { textContent: row.reason }));
    const counted = row.parts.filter((p) => p.n > 0);
    const total = counted.reduce((sum, p) => sum + p.n, 0);
    const bar = make("div", { className: "value-bar", role: "img" });
    bar.ariaLabel = counted.map((p) => `${p.label} ${number(p.n)}`).join(", ") || "No values";
    for (const part of counted) {
      const piece = make("span", { title: `${part.label}: ${number(part.n)} of ${number(total)}` });
      piece.dataset.part = part.key;
      piece.style.flexGrow = String(part.n);
      bar.append(piece);
    }
    const counts = [...row.parts.map((p) => p.n), row.reviewed].map((n) => make("td", { textContent: number(n) }));
    const tr = make("tr", {}, name, make("td", {}, bar), make("td", { textContent: row.foundText }), ...counts);
    tr.dataset.path = row.path;
    return tr;
  }

  function openDetails() {
    renderDetails();
    $("field-details").hidden = false;
    $("details-close").focus();
  }

  function closeDetails() {
    $("field-details").hidden = true;
    seeDetails.focus();
  }

  seeDetails.addEventListener("click", openDetails);
  $("details-close").addEventListener("click", closeDetails);
  $("field-details").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeDetails();
  });
  $("field-details").addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDetails();
  });

  // ------------------------------------------------------------------ back to the setup, and to the review

  /** Show the setup the run came from: on screen already, or its template reopened (see the top). */
  async function backToSetup() {
    if (state.run === null) return;
    if (app.extract.linked()) {
      app.setup.show();
      return;
    }
    const name = state.run.extraction;
    const button = $("back-setup");
    button.disabled = true;
    app.notice(`Opening “${name}”: its samples are read again…`);
    try {
      const setup = await calls.reopen(name);
      state.setupId = setup.id;
      app.setup.open(setup, { savedName: name });
      state.revision = app.setup.revision;
      app.notice("");
    } catch (e) {
      app.notice(`The setup of this run couldn't be opened: ${e.message}`);
    } finally {
      button.disabled = false;
    }
  }

  function showReview() {
    app.extract.showView("review");
    renderToolbar();
    zoom.refresh();
  }

  /** Go to the review of the run: straight back when the setup is as it was read, else asking first (see the top). */
  async function goToReview() {
    await app.setup.settled(); // an edit sent as the click took the focus from its box is a change
    if (state.run === null || !app.extract.linked()) return;
    if (state.revision === app.setup.revision) {
      showReview();
      return;
    }
    const decisions = state.run.fields.reduce((n, f) => n + (f.reviewed ?? 0), 0);
    $("reprocess-decisions").textContent = reprocessNote(decisions);
    $("reprocess-box").hidden = false;
    $("reprocess-go").focus();
  }

  $("back-setup").addEventListener("click", backToSetup);
  $("go-review").addEventListener("click", goToReview);
  $("reprocess-cancel").addEventListener("click", () => {
    $("reprocess-box").hidden = true;
  });
  $("reprocess-go").addEventListener("click", () => {
    $("reprocess-box").hidden = true;
    runOnAll({ quietly: true });
  });
  $("reprocess-box").addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("reprocess-box").hidden = true;
  });

  // ------------------------------------------------------------------ the documents

  /** Ask for the documents the filters match: from the first (a new list), or the next page (`more`). */
  async function list(more = false) {
    const run = state.run;
    const token = ++listing;
    // the server lists only documents that still have problems: one listed here and settled since is no longer in
    // its list, so the next page starts that many earlier
    const offset = more ? state.docs.length - settledListed() : 0;
    const limit = api.pageSize;
    let page;
    try {
      page = isAside()
        ? await calls.setAside(run.id, { offset, limit })
        : await calls.problems(run.id, { path: state.path, status: state.status, level: state.level, offset, limit });
    } catch (e) {
      if (token === listing) app.notice(`The documents couldn't be listed: ${e.message}`);
      return;
    }
    if (token !== listing || state.run !== run) return;
    const seen = new Set(more ? state.docs.map((d) => d.doc_id) : []);
    const fresh = page.documents.filter((d) => !seen.has(d.doc_id));
    state.docs = more ? [...state.docs, ...fresh] : fresh;
    state.total = page.total + (more ? settledListed() : 0); // as many as the filters matched when first listed
    state.listed = true;
    renderDocs();
    if (!more) selectDoc(state.docs[0]?.doc_id ?? null);
  }

  function renderDocs() {
    const aside = isAside();
    const problems = isOptional() ? "optional problems" : "problems";
    $("docs-label").textContent = `${aside ? "Documents set aside" : `Documents with ${problems}`} · ${number(state.total)}`;
    const items = state.docs.map((d) => {
      const li = make(
        "li",
        { tabIndex: 0, className: d.doc_id === state.doc ? "on" : "" },
        make("span", { className: "doc-name", textContent: d.name }),
        make("small", { textContent: aside ? asideLine(d) : docLine(d.problems, settledOf(d)) }),
      );
      li.dataset.name = d.name;
      li.dataset.doc = d.doc_id;
      li.addEventListener("click", () => selectDoc(d.doc_id));
      li.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        selectDoc(d.doc_id);
      });
      return li;
    });
    if (items.length === 0 && state.listed) {
      let text = state.path || state.status ? "No document has a problem of this kind." : `No document has ${isOptional() ? "an optional" : "a"} problem left.`;
      if (aside) text = "No document was set aside.";
      items.push(make("li", { className: "empty", textContent: text }));
    }
    $("docs").replaceChildren(...items);
    $("docs-more").hidden = state.docs.length >= state.total;
    $("docs-legend").textContent = aside ? ASIDE_LEGEND : `${number(state.total - settledListed())} of ${number(state.total)} still have ${problems}`;
  }

  /** Show the document `docId` (null for none), on the page of its first problem with a place. */
  function selectDoc(docId) {
    const d = docOf(docId);
    state.doc = d?.doc_id ?? null;
    const first = d?.problems?.find((p) => p.page !== null) ?? d?.problems?.[0] ?? null;
    state.problem = first?.path ?? null;
    state.page = first?.page ?? 0;
    state.pages = Infinity;
    state.turned = false;
    state.reveal = true;
    for (const li of $("docs").children) li.classList.toggle("on", li.dataset.doc === state.doc);
    renderDoc();
    if (d !== null) locate(d);
  }

  /** Ask where the document's problems with no place should be, and point at them (see the top; once per listing). */
  async function locate(d) {
    if (isAside() || d.located || !d.problems?.some((p) => !p.rect)) return;
    d.located = true;
    const run = state.run;
    let places;
    try {
      ({ places } = await calls.places(run.id, d.doc_id));
    } catch {
      return; // its file is gone, say: the caption keeps saying to look on the page, and the page says why it can't
    }
    for (const p of d.problems) {
      if (!p.rect && places[p.path]) Object.assign(p, places[p.path]);
    }
    if (state.run !== run || state.doc !== d.doc_id) return;
    const picked = d.problems.find((p) => p.path === state.problem);
    if (!state.turned && picked?.rect && picked.page !== state.page) {
      state.page = picked.page;
      showPage();
    } else {
      drawMarks();
      renderCaption();
    }
  }

  function renderDoc() {
    const d = docOf(state.doc);
    $("rdoc-name").textContent = d?.name ?? "";
    $("rdoc-kind").textContent = d === null ? "" : isAside() ? "not read" : `template ${state.run.extraction}`;
    showPage();
    renderProblems();
  }

  $("filter-path").addEventListener("change", () => {
    state.path = $("filter-path").value;
    state.level = levelOf(state.path);
    renderLevels();
    list();
  });
  $("filter-status").addEventListener("change", () => {
    state.status = $("filter-status").value;
    $("filter-path").disabled = isAside(); // a document set aside has no fields read
    renderLevels();
    list();
  });
  for (const btn of $("level-pick").querySelectorAll("[data-level]")) {
    btn.addEventListener("click", () => showLevel(btn.dataset.level));
  }
  $("docs-more").addEventListener("click", () => list(true));

  // ------------------------------------------------------------------ the page

  /** Show the page `state.page` of the document shown, with its problems' places. */
  function showPage() {
    const d = docOf(state.doc);
    if (d === null) {
      pg.replaceChildren();
      state.size = null;
      renderPager();
      renderCaption();
      return;
    }
    const src = calls.runPageUrl(state.run.id, d.doc_id, state.page, DPI);
    if (pg.querySelector("img")?.getAttribute("src") !== src) {
      const img = make("img", { alt: `${d.name}, page ${state.page + 1}` });
      img.addEventListener("load", () => {
        if (!img.isConnected) return;
        state.size = [(img.naturalWidth * 72) / DPI, (img.naturalHeight * 72) / DPI];
        zoom.refresh();
        drawMarks();
      });
      img.addEventListener("error", () => pageFailed(img, d.doc_id));
      img.src = src;
      pg.replaceChildren(img);
    }
    drawMarks();
    renderPager();
    renderCaption();
  }

  /**
   * The page image `img` of `docId` failed: ask for it through the engine to learn why. A 404 past the first page is
   * past the document's last, so its pages stop there; anything else (the server unreachable, the file unreadable,
   * the page there after all) says so under the page, with Try again.
   */
  async function pageFailed(img, docId) {
    if (!img.isConnected) return;
    const page = state.page;
    let reason = "";
    try {
      await calls.runPage(state.run.id, docId, page, DPI);
      reason = "it didn't load";
    } catch (e) {
      if (e.status === 404 && page > 0) {
        if (!img.isConnected || state.doc !== docId || state.page !== page) return;
        state.pages = page; // a page past the document's last: back to its last
        state.page = page - 1;
        showPage();
        return;
      }
      reason = e.message;
    }
    if (!img.isConnected || state.doc !== docId || state.page !== page) return;
    const retry = make("button", { type: "button", className: "btn quiet small", textContent: "Try again" });
    retry.addEventListener("click", () => {
      pg.replaceChildren(); // a new image of the same page
      showPage();
    });
    $("rcap").replaceChildren(`This page couldn't be shown: ${reason}. `, retry);
  }

  /** Outline where each problem on the page shown was read, or looked for; the picked one heavy. */
  function drawMarks() {
    for (const el of pg.querySelectorAll(".rmark")) el.remove();
    const d = docOf(state.doc);
    const img = pg.querySelector("img");
    if (!d?.problems || state.size === null || !img?.complete) return;
    const [width, height] = state.size;
    for (const p of d.problems) {
      if (p.page !== state.page || !p.rect) continue;
      const [x0, y0, x1, y1] = p.rect;
      const picked = p.path === state.problem;
      const mark = make("div", { className: `rmark${noValue(p.status) ? " is-missing" : ""}${picked ? " is-picked" : ""}`, title: p.title });
      mark.dataset.path = p.path;
      Object.assign(mark.style, { left: pct(x0 / width), top: pct(y0 / height), width: pct((x1 - x0) / width), height: pct((y1 - y0) / height) });
      if (picked) mark.append(make("span", { className: "rtag", textContent: markTag(p.status) }));
      pg.append(mark);
    }
    if (!state.reveal) return;
    const mark = pg.querySelector(".rmark.is-picked");
    if (mark === null) return;
    state.reveal = false;
    mark.scrollIntoView({ block: "center", inline: "nearest" });
  }

  function renderPager() {
    const shown = docOf(state.doc) !== null;
    $("rpage-of").textContent = shown ? `Page ${state.page + 1}` : "";
    $("rprev-p").disabled = !shown || state.page === 0;
    $("rnext-p").disabled = !shown || state.page + 1 >= state.pages;
  }

  function renderCaption() {
    const d = docOf(state.doc);
    let text = "";
    if (d !== null && isAside()) text = "Not read: no value of it is in the results";
    else if (d !== null) {
      const p = d.problems.find((x) => x.path === state.problem);
      text = p && !p.rect ? `${p.title}: its place on the page isn't known; look for it on the page` : CAPTION;
    }
    $("rcap").textContent = text;
  }

  function turnPage(step) {
    const next = state.page + step;
    if (docOf(state.doc) === null || next < 0 || next >= state.pages) return;
    state.page = next;
    state.turned = true;
    state.reveal = false;
    showPage();
  }

  $("rprev-p").addEventListener("click", () => turnPage(-1));
  $("rnext-p").addEventListener("click", () => turnPage(1));

  const zoom = mountZoom({
    col: pg.closest(".col-page"),
    bar: $("review-zoom"),
    below: $("rcap"),
    size: () => state.size,
    apply(scale, fit) {
      pg.style.width = fit === "width" ? "" : `${Math.floor(state.size[0] * scale)}px`;
    },
  });

  // ------------------------------------------------------------------ the problems and the decisions

  function renderProblems() {
    const d = docOf(state.doc);
    const box = $("problems");
    $("problems-label").textContent = isAside() ? "Why it was set aside" : "Problems in this document";
    if (d === null) box.replaceChildren();
    else if (isAside()) box.replaceChildren(asideCard(d));
    else box.replaceChildren(...d.problems.map((p) => problemCard(d, p)));
  }

  /** A set-aside document's card: why it was not read, the closest template, and what to do with it. */
  function asideCard(d) {
    const closest = d.closest_template
      ? `The closest template is ${d.closest_template}, ${Math.round((d.closest_score ?? 0) * 100)}% alike: too far from it to be read with it.`
      : "No template came close.";
    return make(
      "div",
      { className: "problem" },
      make("div", { className: "head" }, make("b", { textContent: d.reason })),
      make("p", { className: "fc-ins-hint", textContent: closest }),
      make("p", {
        className: "fc-ins-hint",
        textContent: "It is not in the values; the export lists it on its set_aside sheet. To read it, run Extract from PDFs again with just these documents, for a template of their own.",
      }),
    );
  }

  /**
   * A problem's card: the field, the problem, what was read and why, why the field is optional (on an optional
   * field's card), and the decisions (or the one made).
   */
  function problemCard(d, p) {
    const reasons = p.reasons.length ? ` · ${p.reasons.join("; ")}` : "";
    const el = make(
      "div",
      { className: `problem${p.path === state.problem ? " on" : ""}` },
      make("div", { className: "head" }, make("b", { textContent: problemName(p) }), ` · ${problemLabel(p.status)}`),
      make("p", { className: "fc-ins-hint" }, "Read: ", make("code", { textContent: p.raw || "nothing" }), reasons),
    );
    if (p.required === false) {
      el.append(make("p", { className: "fc-ins-hint why", textContent: p.required_reason ? `Optional: ${p.required_reason}` : "Optional" }));
    }
    el.dataset.path = p.path;
    el.addEventListener("click", (e) => {
      if (!e.target.closest("button")) pick(p.path);
    });
    const done = decided.get(keyOf(d.doc_id, p.path));
    if (done !== undefined) {
      el.append(make("p", { className: "state", textContent: done }));
      return el;
    }
    const input = make("input", {
      type: "text",
      className: "fc-in",
      value: p.raw ?? "",
      placeholder: "Type the value from the page",
      autocomplete: "off",
      spellcheck: false,
    });
    input.setAttribute("aria-label", `The value of ${p.title}`);
    const act = (decision, text, dark = false) => {
      const b = make("button", { type: "button", className: `btn small ${dark ? "dark" : "quiet"}`, textContent: text });
      b.dataset.act = decision;
      b.addEventListener("click", () => decide(d, p, decision, input));
      return b;
    };
    const row = make("div", { className: "row" }, act("value", "Use this value", true));
    if (p.value !== null && p.value !== undefined) row.append(act("accept", "Accept as read")); // a value was read
    row.append(act("not_in_document", "Not in this document"));
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      decide(d, p, "value", input);
    });
    el.append(input, row);
    setBusy(el, deciding.has(keyOf(d.doc_id, p.path))); // drawn again while its decision is on its way
    return el;
  }

  /** Pick the problem `path` of the document shown: to its page and its place, highlighted, even if already picked. */
  function pick(path) {
    const p = docOf(state.doc)?.problems?.find((x) => x.path === path);
    if (!p) return;
    state.problem = path;
    state.turned = false;
    state.reveal = true;
    for (const card of $("problems").children) card.classList.toggle("on", card.dataset.path === path);
    if (p.page !== null && p.page !== state.page) {
      state.page = p.page;
      showPage();
    } else {
      drawMarks();
      renderCaption();
    }
  }

  /** The card on show of the problem `path` of the document `docId`, or null (another document is shown). */
  function cardOf(docId, path) {
    if (state.doc !== docId) return null;
    return [...$("problems").children].find((card) => card.dataset.path === path) ?? null;
  }

  /** Disable (or enable again) a card's input and buttons while its decision is on its way. */
  function setBusy(card, busy) {
    for (const el of card?.querySelectorAll("input, button") ?? []) el.disabled = busy;
  }

  /**
   * Record a decision on the problem `p` of the document `d` (`input` its card's value box); its card, the stats and
   * the list follow. Only that card is drawn again: the others keep what is typed in them, and their own decisions
   * on their way.
   */
  async function decide(d, p, decision, input) {
    const key = keyOf(d.doc_id, p.path);
    if (deciding.has(key) || decided.has(key)) return; // one on its way already, or made
    const value = decision === "value" ? input.value.trim() : undefined;
    if (decision === "value" && !value) {
      input.focus();
      return;
    }
    const run = state.run;
    deciding.add(key);
    setBusy(cardOf(d.doc_id, p.path), true);
    try {
      const body = { doc_id: d.doc_id, path: p.path, decision, ...(decision === "value" ? { value } : {}) };
      const next = await calls.decide(run.id, body);
      if (state.run !== run) return;
      state.run = next;
      decided.set(key, decisionText(decision, decision === "accept" ? p.value : value));
      app.notice("");
      renderStats();
      renderDocs();
      settleCard(d, p);
    } catch (e) {
      app.notice(`That decision wasn't recorded: ${e.message}`);
    } finally {
      deciding.delete(key);
      if (!decided.has(key)) setBusy(cardOf(d.doc_id, p.path), false);
    }
  }

  /** Draw the decided card of `p` again, settled; focus, when it was in that card, goes to the next open card. */
  function settleCard(d, p) {
    const card = cardOf(d.doc_id, p.path);
    if (card === null) return;
    const focused = card.contains(document.activeElement) || document.activeElement === document.body;
    const fresh = problemCard(d, p);
    fresh.tabIndex = -1;
    card.replaceWith(fresh);
    if (!focused) return;
    const cards = [...$("problems").children];
    const next = [...cards.slice(cards.indexOf(fresh) + 1), ...cards].find((c) => c.querySelector("input:not(:disabled)"));
    (next?.querySelector("input") ?? fresh).focus();
  }

  // ------------------------------------------------------------------ export and send to fill

  /** Download the run's results as `format`; a format the server can't make says so (Parquet's button goes). */
  async function exportAs(format, button) {
    if (state.run === null || button.disabled) return;
    button.disabled = true;
    try {
      const file = await calls.exportRun(state.run.id, format);
      downloadBytes(file.bytes, file.filename, file.type);
    } catch (e) {
      if (format === "parquet" && e.code === "not_supported") {
        button.hidden = true;
        app.notice(`Parquet isn't available on this server: ${e.message}`);
      } else {
        app.notice(`The export couldn't be made: ${e.message}`);
      }
    } finally {
      button.disabled = false;
    }
  }

  /** `{form, error}`: the fill form a saved extraction template was made for (its destination, or null), and why it
   * couldn't be read ("" when it could). */
  async function destinationOf(name) {
    try {
      return { form: (await calls.extraction(name)).setup?.destination ?? null, error: "" };
    } catch (e) {
      return { form: null, error: `The form “${name}” was made for couldn't be read (${e.message}); choose the form.` };
    }
  }

  /** The run's documents as a dataset, on the fill start screen as Your data (see the top). */
  async function sendToFill() {
    const run = state.run;
    const button = $("send-fill");
    if (run === null || button.disabled) return;
    button.disabled = true;
    try {
      const [sent, { form, error }] = await Promise.all([calls.toData(run.id), destinationOf(run.extraction)]);
      hideOverlays();
      app.extract.setMode("fill");
      app.notice("");
      // said on the start screen itself, under Your data: the notice under the toolbar is behind its overlay
      const note = [sendNotice(sent), error].filter(Boolean).join(" ");
      app.start.showStart({ dataset: sent.dataset, form, note });
    } catch (e) {
      app.notice(`The results couldn't be sent to fill: ${e.message}`);
    } finally {
      button.disabled = false;
    }
  }

  $("export-xlsx").addEventListener("click", (e) => exportAs("xlsx", e.currentTarget));
  $("export-parquet").addEventListener("click", (e) => exportAs("parquet", e.currentTarget));
  $("send-fill").addEventListener("click", sendToFill);

  // ------------------------------------------------------------------ the choice's saved runs and templates

  /** A row of the choice's lists: a button with `text` and `detail` under it. */
  function choiceItem(text, detail, onClick) {
    const button = make("button", { type: "button" }, text, make("small", { textContent: detail }));
    button.addEventListener("click", onClick);
    return make("li", {}, button);
  }

  /** List the saved runs and extraction templates under the choice; when they can't be read, the choice says so. */
  async function refreshChoices() {
    $("saved").hidden = false;
    const token = ++choices;
    let runs;
    let saved;
    try {
      [runs, saved] = await Promise.all([calls.runs(), calls.list()]);
    } catch (e) {
      if (token === choices) {
        listFailed = `The saved runs and extractions couldn't be listed: ${e.message}`;
        app.say(chooseMsg, listFailed);
      }
      return;
    }
    if (token !== choices) return;
    if (listFailed && chooseMsg.textContent === listFailed) app.say(chooseMsg, ""); // listed now: that is no longer so
    listFailed = "";
    $("saved-runs-group").hidden = runs.length === 0;
    $("saved-runs").replaceChildren(...runs.map((r) => choiceItem(runLine(r), startedText(r.started), () => openSaved(r.id))));
    $("saved-extractions-group").hidden = saved.length === 0;
    $("saved-extractions").replaceChildren(
      ...saved.map((x) => choiceItem(x.name, `${plural(x.fields, "field")} · ${plural(x.samples, "sample")}`, () => reopen(x.name))),
    );
  }

  /** Open a saved run's review from the choice; a refusal is said on the choice. */
  async function openSaved(id) {
    app.say(chooseMsg, "");
    try {
      show(await calls.run(id), null);
    } catch (e) {
      app.say(chooseMsg, e.message);
    }
  }

  /** Reopen a saved extraction template on the setup screen (its samples are read again). */
  async function reopen(name) {
    app.say(chooseMsg, `Opening “${name}”: its samples are read again…`);
    try {
      const setup = await calls.reopen(name);
      app.say(chooseMsg, "");
      app.setup.open(setup, { savedName: name });
    } catch (e) {
      app.say(chooseMsg, e.message);
    }
  }

  /** Open the command line run in the folder typed in #run-folder. */
  async function openFolder() {
    const folder = $("run-folder").value.trim();
    if (!folder) {
      app.say(chooseMsg, "Type the full path of a run's folder on this machine.");
      return;
    }
    const button = $("open-run-folder");
    button.disabled = true;
    app.say(chooseMsg, "");
    try {
      show(await calls.openRun(folder), null);
    } catch (e) {
      app.say(chooseMsg, e.message);
    } finally {
      button.disabled = false;
    }
  }

  $("open-run-folder").addEventListener("click", openFolder);
  $("run-folder").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    openFolder();
  });

  refreshChoices(); // the page opens on the choice
  api.openRun = openRun;
  api.refreshChoices = refreshChoices;
  return api;
}
