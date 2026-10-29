// The way in, the page's two modes, and the Extract modal. The page opens on "What would you like to do?" (#choose):
// Fill a PDF opens the start screen (start.js) as it always was, and Extract from PDFs switches the page to its
// extraction mode and opens the Extract modal (#extract), offered only when the server can extract (the engine has
// `extract`). The toolbar's name button of either mode (#doc-name filling, #doc-pick extracting) brings the choice
// back, and so do the start screen's Back while no form is open and the Extract modal's Back.
//
// The mode is `body[data-mode]`, "fill" or "extract": the parts of the page that belong to one mode carry
// `data-for="fill"` or `data-for="extract"`, and extract.css shows only the current mode's (its toolbar groups, its
// main views, the fill screen's note on turning AI on). Switching mode leaves the other mode's work as it is: a form
// open for filling is still there, with its undo history, when the person comes back to Fill. The bus hears `mode`
// ({mode}) on every switch.
//
// The Extract modal, in the mockup's four groups. 1 · the batch: the PDFs and zips among the files chosen or dropped
// (or in a folder chosen) go to one batch, made at the first upload and added to after; a folder typed is opened on
// the server's machine as a new batch. A file over the server's upload limit is left out and named in
// #extract-msg (engine.extract.addFiles skips it). The batch's line, strip and note (extract-model.js) follow its
// scan, polled until it ends: until the scan is done every cell reads "not scanned yet", and then the strip marks the
// documents the sample would take now (the marks go as soon as the scan starts again or the sample changes). 2 · the
// sample: 3 to 20 documents spread across the batch or the first ones, or the ones checked in the batch's list (I'll
// pick them; 200 files a page, More for the next). 3 · what to extract: the AI's suggestion, the person's own lines, or
// the fields of a saved fill template (the destination). 4 · detection and reading: finding the blanks (by hand, on
// the pages named for every sample: "1, 3-4"), OCR, the unsure rule (always on in this version: plan 1's reader has
// no switch for it) and AI per document (read by a run, later).
//
// Read the sample → (disabled from the click until the building panel opens or the choices are refused, so a double
// click starts one build) picks the sample (`pickSample`) and starts the `extract-setup` job; the building panel
// (#building) shows its stages live (`showStages`). When it is done the panel closes, the bus hears `extract:setup`
// ({setup}) and `openSetup(setup)` of this module's API opens it, once the setup screen sets it. A job that fails, or
// can't start, goes back to the modal with its message; Cancel asks the job to stop and goes back at once, the job's
// end ignored. `openModal(setupInfo)` opens the modal again with a template's choices (a SetupInfo: start, lines,
// destination, detect, pages, ocr, sample_how), for the setup screen's "Change the setup".

import { POLL_MS } from "../engines/server/client.js";
import { batchLine, batchNote, pagesText, parsePages, stripCells } from "./extract-model.js";
import { showStages } from "./jobs.js";

/** The fewest and most documents a sample has (the server's own bounds). */
const MIN_SAMPLE = 3;
const MAX_SAMPLE = 20;
/** Files of the batch listed at a time for I'll pick them (the server's page limit). */
const PICK_PAGE = 200;
/** Only these go up from a folder chosen in the browser: the server takes PDFs and zips of PDFs. */
const UPLOADABLE = /\.(pdf|zip)$/i;
/** The settings' summary line for each way of finding the blanks. */
const DETECT_SUM = { auto: "automatic, prose skipped", all: "automatic, every page", manual: "you pick the pages", off: "form fields and text only" };
/** A setup's `start` for each choice of the "Start from" buttons, and back. */
const START_OF = { ai: "ai", user: "lines", dest: "destination" };
const MODE_OF = { ai: "ai", lines: "user", destination: "dest" };
/** What a strip cell's state means, as its title. */
const CELL_TITLES = { sample: "in the sample", main: "fillable", flat: "flattened", other: "a different layout", pending: "not scanned yet" };

const plural = (n, word, many = `${word}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? word : many}`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * @param {object} app see workbench.js
 * @returns {{show: () => void, setMode: (mode: "fill" | "extract") => void, openModal: (setupInfo?: object) => void,
 *   openSetup?: (setup: object) => void}}
 */
export function mountExtract(app) {
  const { $, bus, engine } = app;
  const choose = $("choose");
  const fill = choose.querySelector('.choice[data-go="fill"]');
  const extract = choose.querySelector('.choice[data-go="extract"]');
  const canExtract = engine.extract !== undefined;
  extract.hidden = !canExtract;

  /** Show the choice (the caller has closed the overlay it came from: one overlay is open at a time). */
  function show() {
    choose.hidden = false;
  }

  /** Switch the page to filling or extracting. */
  function setMode(mode) {
    if (document.body.dataset.mode === mode) return;
    document.body.dataset.mode = mode;
    bus.emit("mode", { mode });
  }

  const api = { show, setMode, openModal: () => {} };

  fill.addEventListener("click", () => {
    choose.hidden = true;
    setMode("fill");
    app.start.showStart();
  });
  extract.addEventListener("click", () => {
    if (!canExtract) return;
    choose.hidden = true;
    api.openModal();
  });
  $("doc-name").addEventListener("click", show);
  $("doc-pick").addEventListener("click", show);

  if (canExtract) mountModal(app, api);
  return api;
}

/** The Extract modal and the building panel (see the top); sets `api.openModal`. */
function mountModal(app, api) {
  const { $, bus, engine } = app;
  const calls = engine.extract;
  const sheet = $("extract");
  const building = $("building");
  const strip = $("batch-strip");
  const picklist = $("picklist");
  const pollMs = app.pollMs ?? POLL_MS;

  const state = {
    batch: null, // the Batch on show, as the server last said
    sample: [], // its file indexes the sample takes now, marked on the strip
    picked: new Set(), // I'll pick them: the files checked
    listed: 0, // I'll pick them: the files listed so far
    total: 0, // I'll pick them: the batch's files when the last page was listed
    mode: "ai", // the "Start from" button pressed
    destination: null, // the destination to select once the list has loaded
    work: Promise.resolve(), // uploads and folder scans, one after another
    progress: "", // an upload under way, shown in place of the batch's line
  };
  let watching = 0; // the scan watch running (a newer one ends an older)
  let previewing = 0; // the sample preview asked for last
  let build = null; // the build running: {handle, cancelled}
  let destinations = null; // the destination list's loading, once

  const say = (text) => app.say($("extract-msg"), text);
  const how = () => $("sample-how").value;

  // ------------------------------------------------------------------ opening

  /** Open the modal (closing any other overlay), with a template's choices when `info` (a SetupInfo) is given. */
  function openModal(info) {
    for (const o of document.querySelectorAll(".overlay")) if (o !== sheet) o.hidden = true;
    api.setMode("extract");
    if (info) apply(info);
    renderMode();
    renderDetect();
    sheet.hidden = false;
  }
  api.openModal = openModal;

  /** Put a SetupInfo's choices in the modal. */
  function apply(info) {
    state.mode = MODE_OF[info.start] ?? "ai";
    $("lines").value = (info.lines ?? []).join("\n");
    state.destination = info.destination ?? null;
    if (state.destination !== null) $("dest-pick").value = state.destination;
    setSelect("detect", info.detect);
    setSelect("ocr", info.ocr);
    $("detect-pages").value = pagesText(searchedOf(info.pages ?? {}));
    if (setSelect("sample-how", info.sample_how)) sampleHowChanged();
  }

  /**
   * The pages a SetupInfo's `pages` ({sample position: [page from 0]}) searched, as the modal's one list for every
   * sample: the list they share, or when they differ the first sample's.
   */
  function searchedOf(pages) {
    const first = Object.keys(pages).sort((a, b) => Number(a) - Number(b))[0];
    return first === undefined ? [] : (pages[first] ?? []);
  }

  /** Select `value` in the select `id` when it has that option; whether it changed. */
  function setSelect(id, value) {
    const select = $(id);
    if (![...select.options].some((o) => o.value === value) || select.value === value) return false;
    select.value = value;
    return true;
  }

  // ------------------------------------------------------------------ 1 · the batch

  /** Show the batch: its line (or the upload under way), its strip with the sample marked, the scan's note. */
  function renderBatch() {
    const b = state.batch;
    $("batch").hidden = b === null && !state.progress;
    $("batch-sum").textContent = state.progress || (b ? batchLine(b) : "");
    const cells = b ? stripCells(b, state.sample) : [];
    strip.replaceChildren(
      ...cells.map((cell) => {
        const li = document.createElement("li");
        li.dataset.state = cell.state;
        li.title = CELL_TITLES[cell.state];
        return li;
      }),
    );
    $("batch-note").textContent = b ? batchNote(b, cells.length) : "";
  }

  /** Show `batch`, the batch's latest state; another batch starts its sample and pick list over. */
  function setBatch(batch) {
    const old = state.batch;
    state.batch = batch;
    if (old?.id !== batch.id) state.picked = new Set();
    if (old?.id !== batch.id || batch.scan?.phase !== "done") state.sample = []; // marked again once the scan is done
    if (old?.id !== batch.id || old.count !== batch.count) relist();
    renderBatch();
  }

  /** Do `task` after the uploads and scans before it; a refusal shows in the message line. */
  function queue(task) {
    state.work = state.work.then(task).catch((e) => {
      state.progress = "";
      renderBatch();
      say(e.message);
    });
    return state.work;
  }

  /** Upload `files` into the batch (a new one for the first upload, or after a folder); `name` names a new one. */
  async function upload(files, name) {
    say("");
    let batch = state.batch;
    if (batch === null || batch.folder) batch = await calls.createBatch(name ? { name } : {});
    state.progress = `Uploading ${plural(files.length, "file")}…`;
    renderBatch();
    let added;
    try {
      added = await calls.addFiles(batch.id, files, {
        onProgress: ({ done, total }) => {
          state.progress = `Uploading, ${done.toLocaleString("en-US")} of ${plural(total, "file")}…`;
          renderBatch();
        },
      });
    } catch (e) {
      state.progress = "";
      setBatch(await calls.batch(batch.id).catch(() => batch)); // what went in before the refusal stays
      watchScan();
      throw e;
    }
    state.progress = "";
    setBatch(added.batch);
    if (added.tooLarge.length) {
      const names = added.tooLarge.join(", ");
      say(`Left out, larger than the server takes in one upload: ${names}. For a large zip, choose the PDFs in it or type their folder on this machine.`);
    }
    watchScan();
  }

  /** Open the folder typed in #folder on the server's machine, as a new batch. */
  async function scanFolder() {
    const folder = $("folder").value.trim();
    if (!folder) {
      say("Type the full path of a folder on this machine.");
      return;
    }
    say("");
    state.progress = "Opening the folder…";
    renderBatch();
    const batch = await calls.createBatch({ folder });
    state.progress = "";
    setBatch(batch);
    watchScan();
  }

  /** Poll the batch until its scan ends, showing each state; then mark the sample it would take. */
  async function watchScan() {
    const token = ++watching;
    while (state.batch.scan?.phase === "scanning") {
      await sleep(pollMs);
      if (token !== watching) return;
      let batch;
      try {
        batch = await calls.batch(state.batch.id);
      } catch (e) {
        say(e.message);
        return;
      }
      if (token !== watching) return;
      setBatch(batch);
    }
    previewSample();
  }

  /**
   * Upload the PDFs and zips among `files` (a folder chosen or dropped holds other files too, which stay here);
   * a new batch is named `folder`, or after a zip given alone.
   */
  function take(files, folder = "") {
    const list = [...files].filter((f) => UPLOADABLE.test(f.name));
    if (list.length === 0) {
      say(files.length ? "There are no PDF or .zip files in that. Choose PDFs, a .zip of them, or a folder of them." : "");
      return;
    }
    const zip = list.length === 1 && /\.zip$/i.test(list[0].name) ? list[0].name.replace(/\.zip$/i, "") : "";
    queue(() => upload(list, folder || zip));
  }

  /** The files chosen in `input`, which is emptied so that choosing the same files again is a change again. */
  const filesOf = (input) => {
    const files = [...input.files];
    input.value = "";
    return files;
  };
  $("drop-files").addEventListener("change", (e) => take(filesOf(e.target)));
  $("drop-folder").addEventListener("change", (e) => {
    const files = filesOf(e.target);
    take(files, files[0]?.webkitRelativePath?.split("/")[0] ?? "");
  });
  const drop = $("drop");
  drop.addEventListener("click", (e) => {
    if (!e.target.closest("label, input")) $("drop-files").click(); // the zone is the choose-files button
  });
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("is-over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("is-over");
    take(e.dataTransfer?.files ?? []);
  });
  $("scan").addEventListener("click", () => queue(scanFolder));
  $("folder").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    queue(scanFolder);
  });

  // ------------------------------------------------------------------ 2 · the sample

  /** The sample size typed, or null (with the message shown) when it is not 3 to 20. */
  function sampleSize() {
    const n = Number($("n-sample").value);
    if (Number.isInteger(n) && n >= MIN_SAMPLE && n <= MAX_SAMPLE) return n;
    say(`Use ${MIN_SAMPLE} to ${MAX_SAMPLE} documents for the sample.`);
    return null;
  }

  /** Mark on the strip the documents the sample takes now: those checked, or those the server would pick. */
  async function previewSample() {
    const token = ++previewing;
    const b = state.batch;
    if (b === null) return;
    if (how() === "pick") {
      state.sample = [...state.picked].sort((x, y) => x - y);
    } else {
      state.sample = []; // the marks of the sample before go at once, never left on show for another
      renderBatch();
      const n = Number($("n-sample").value);
      if (b.scan?.phase !== "done" || !(n >= MIN_SAMPLE && n <= MAX_SAMPLE)) return;
      let picked;
      try {
        picked = await calls.pickSample(b.id, { n, how: how() });
      } catch {
        picked = []; // a batch with no layout: its note says so, and Read the sample gives the reason
      }
      if (token !== previewing || state.batch?.id !== b.id) return;
      state.sample = picked.map((s) => s.index);
    }
    renderBatch();
  }

  /** The pick list from its start again (another batch, or more files in it), when it is shown. */
  function relist() {
    state.listed = 0;
    state.total = 0;
    picklist.replaceChildren();
    showMore();
    if (how() === "pick" && state.batch !== null) listMore();
  }

  /** More, while I'll pick them is chosen and the list has not reached the end of the batch. */
  function showMore() {
    $("pick-more").hidden = how() !== "pick" || state.listed >= state.total;
  }

  /** The next PICK_PAGE files of the batch in the pick list. */
  async function listMore() {
    const b = state.batch;
    let page;
    try {
      page = await calls.files(b.id, state.listed, PICK_PAGE);
    } catch (e) {
      say(e.message);
      return;
    }
    if (state.batch?.id !== b.id || page.files[0]?.index !== state.listed) return; // relisted meanwhile
    if (state.picked.size === 0 && state.listed === 0) {
      const n = Number($("n-sample").value) || 5;
      const seed = state.sample.length ? state.sample : page.files.slice(0, n).map((f) => f.index);
      state.picked = new Set(seed); // start from the sample on the strip
    }
    const main = mainGroup(b);
    picklist.append(...page.files.map((f) => pickRow(f, main)));
    state.listed += page.files.length;
    state.total = page.total;
    showMore();
    previewSample();
  }

  /** The batch's largest scan group's number, or null. */
  function mainGroup(b) {
    const groups = b.scan?.groups ?? [];
    return groups.reduce((best, g) => (best === null || g.size > best.size ? g : best), null)?.group ?? null;
  }

  /** A file's row in the pick list: its checkbox and name, and "a different layout" when it is not the batch's. */
  function pickRow(file, main) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.picked.has(file.index);
    box.addEventListener("change", () => {
      if (box.checked) state.picked.add(file.index);
      else state.picked.delete(file.index);
      previewSample();
    });
    const name = document.createElement("span");
    name.textContent = file.name;
    const label = document.createElement("label");
    label.append(box, name);
    if (main !== null && file.group !== null && file.group !== main) {
      const small = document.createElement("small");
      small.textContent = CELL_TITLES.other;
      label.append(small);
    }
    const li = document.createElement("li");
    li.append(label);
    return li;
  }

  /** The pick list shown for I'll pick them (listed the first time), and the strip's sample marked again. */
  function sampleHowChanged() {
    picklist.hidden = how() !== "pick";
    if (how() === "pick" && state.listed === 0) relist();
    else showMore();
    previewSample();
  }

  $("n-sample").addEventListener("change", () => {
    if (sampleSize() !== null) say("");
    previewSample(); // an invalid size clears the marks
  });
  $("sample-how").addEventListener("change", sampleHowChanged);
  $("pick-more").addEventListener("click", listMore);

  // ------------------------------------------------------------------ 3 · what to extract, 4 · settings

  /** The "Start from" button pressed and its detail: the AI's note, your lines, or the destination list. */
  function renderMode() {
    for (const btn of sheet.querySelectorAll(".seg [data-mode]")) btn.setAttribute("aria-pressed", String(btn.dataset.mode === state.mode));
    for (const div of $("mode-detail").children) div.hidden = div.dataset.detail !== state.mode;
    if (state.mode === "dest") loadDestinations();
  }

  /** Fill the destination list with the saved fill templates, once. */
  function loadDestinations() {
    destinations ??= engine.templates.list().then(
      (list) => {
        const options = list.map((t) => new Option(t.title ? `${t.name} · ${t.title}` : t.name, t.name));
        if (options.length === 0) options.push(new Option("No saved fill templates yet", ""));
        $("dest-pick").replaceChildren(...options);
        if (state.destination !== null) $("dest-pick").value = state.destination;
      },
      (e) => {
        $("dest-pick").replaceChildren(new Option("The saved templates couldn't be listed", ""));
        say(e.message);
        destinations = null; // try again next time
      },
    );
    return destinations;
  }

  for (const btn of sheet.querySelectorAll(".seg [data-mode]")) {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      renderMode();
    });
  }
  $("dest-pick").addEventListener("change", () => {
    state.destination = $("dest-pick").value || null;
  });
  /** The settings' summary, and the pages to search while the blanks are found by hand. */
  function renderDetect() {
    $("settings-sum").textContent = DETECT_SUM[$("detect").value];
    $("pages-row").hidden = $("detect").value !== "manual";
  }
  $("detect").addEventListener("change", renderDetect);

  // ------------------------------------------------------------------ read the sample: build the template

  /**
   * The setup's spec from the modal's choices, or `{error}`. Its `pages` (finding the blanks by hand) are filled in
   * once the sample is picked, by `pagesFor`: `searched` are the pages named, from 0.
   */
  function specOf(batch) {
    const start = START_OF[state.mode];
    const lines = $("lines").value.split("\n").map((line) => line.trim()).filter(Boolean);
    if (start === "lines" && lines.length === 0) return { error: "List what to extract, one thing per line." };
    const destination = start === "destination" ? $("dest-pick").value || null : null;
    if (start === "destination" && destination === null) return { error: "Choose the destination template." };
    const detect = $("detect").value;
    const searched = detect === "manual" ? parsePages($("detect-pages").value) : [];
    if (searched === null) return { error: "Name the pages to search, such as 1 or 1, 3-4." };
    return {
      searched,
      spec: {
        name: batch.name,
        start,
        lines: start === "lines" ? lines : [],
        destination,
        detect,
        pages: {},
        ocr: $("ocr").value,
        sample_how: how(),
      },
    };
  }

  /** The pages searched on each of `count` samples, keyed by its position in the sample (as the library has it). */
  function pagesFor(count, searched) {
    if (searched.length === 0) return {};
    return Object.fromEntries(Array.from({ length: count }, (_, i) => [i, searched]));
  }

  /** What to send `pickSample`, or `{error}`. */
  function sampleOf(batch) {
    if (how() !== "pick") {
      const n = sampleSize();
      return n === null ? { error: $("extract-msg").textContent } : { body: { n, how: how() } };
    }
    const fewest = Math.min(MIN_SAMPLE, batch.count);
    if (state.picked.size < fewest || state.picked.size > MAX_SAMPLE) {
      return { error: `Pick ${fewest} to ${MAX_SAMPLE} documents for the sample.` };
    }
    return { body: { how: "pick", picked: [...state.picked].sort((x, y) => x - y) } };
  }

  /**
   * Read the sample →: #read-sample stays disabled from the click until the building panel opens or the choices are
   * refused, so a second click never starts a second build.
   */
  async function readSample() {
    const button = $("read-sample");
    if (button.disabled) return;
    button.disabled = true;
    let ready;
    try {
      ready = await prepareBuild();
    } finally {
      button.disabled = false; // the building panel opens at once below, over the modal
    }
    if (ready) await runBuild(ready.batch, ready.samples, ready.spec);
  }

  /** The batch, the sample picked and the spec to build with, or null with the reason shown. */
  async function prepareBuild() {
    say("");
    await state.work; // the uploads under way go in first
    const batch = state.batch;
    if (batch === null || batch.count === 0) {
      say("Add the documents first: drop them in, or scan a folder.");
      return null;
    }
    const sample = sampleOf(batch);
    const choices = sample.error ? sample : specOf(batch);
    if (choices.error) {
      say(choices.error);
      return null;
    }
    let samples;
    try {
      samples = (await calls.pickSample(batch.id, sample.body)).map((s) => s.index);
    } catch (e) {
      say(e.message);
      return null;
    }
    return { batch, samples, spec: { ...choices.spec, pages: pagesFor(samples.length, choices.searched) } };
  }

  /** Start the extract-setup job and follow it in the building panel (see the top). */
  async function runBuild(batch, samples, spec) {
    const run = { handle: null, cancelled: false };
    build = run;
    sheet.hidden = true;
    $("build-stages").replaceChildren();
    $("build-readout").textContent = "Starting…";
    building.hidden = false;
    try {
      run.handle = await calls.startSetup({ batch: batch.id, samples, spec });
    } catch (e) {
      backFrom(run, e.message);
      return;
    }
    if (run.cancelled) {
      run.handle.cancel().catch(() => {});
      return;
    }
    run.handle.onUpdate = (job) => {
      if (build === run) showJob(job);
    };
    let job;
    try {
      job = await run.handle.done;
    } catch (e) {
      backFrom(run, e.message);
      return;
    }
    if (build !== run) return; // cancelled meanwhile
    const setup = job.status === "done" ? job.result?.setup : undefined;
    if (setup === undefined) {
      backFrom(run, job.status === "cancelled" ? "The build was cancelled." : job.error || "The template couldn't be built.");
      return;
    }
    build = null;
    building.hidden = true;
    bus.emit("extract:setup", { setup });
    api.openSetup?.(setup);
  }

  /** The build's stages, and how far it has got. */
  function showJob(job) {
    const current = showStages($("build-stages"), job);
    const stages = job.stages ?? [];
    const ended = stages.filter((st) => st.status === "done" || st.status === "skipped").length;
    let text = `${ended} of ${stages.length}`;
    if (job.status === "done") text = "Done.";
    else if (current) text += ` · ${current.label}${current.detail && current.detail !== current.label ? `: ${current.detail}` : "…"}`;
    $("build-readout").textContent = text;
  }

  /** Back to the modal from the build `run` (unless it was cancelled), with `message`. */
  function backFrom(run, message) {
    if (build !== run) return;
    build = null;
    building.hidden = true;
    sheet.hidden = false;
    say(message);
  }

  $("read-sample").addEventListener("click", readSample);
  $("build-cancel").addEventListener("click", () => {
    const run = build;
    if (run === null) return;
    build = null;
    run.cancelled = true;
    run.handle?.cancel().catch(() => {}); // it stops after the stage running now; its end is ignored
    building.hidden = true;
    sheet.hidden = false;
    say("");
  });
  $("extract-back").addEventListener("click", () => {
    sheet.hidden = true;
    api.show();
  });
}
