// AI jobs: the one-shot mapping (from the start screen and the fill drawer's "Add data"), Build with
// AI, Adapt (from the match prompt) and the chat's revise (below). One at a time. A job runs on the
// server; its panel (#job) sits at the top of the right column, beside the page, never over it, so the
// person can look through the PDF, zoom and turn the pages while it runs. It lists the job's steps as
// they come and offers Cancel, or Hide (the job goes on). Cancel is seen between steps, so the panel says
// it stops after the step running now. A finished job's template loads with one undo step, so Undo
// returns to the template from before it. A cancelled job closes the panel and leaves the template
// alone; one that fails, or can't be started or followed, keeps the panel open with why, Try again (the
// same job afresh) and Close.
//
// A result not loaded from the panel (a job run in the background, or the partial result of a
// cancelled or failed one, which includes the step that was running) is kept here, with the job's
// snapshot, and offered on its own line under the toolbar until it is loaded or discarded. Other
// messages don't replace it. Opening another form discards it (it belongs to the PDF it ran on).
//
// A one-shot job lists its stages (read the form, design the schema, map the boxes, read and map the
// data) with the agent's steps under them, and the tokens used so far. When it ends with the panel
// open and the template on screen as it was when the job started, its result loads at once: the
// template, unless the job only mapped data for the template on screen, and the data's records, which
// open in the fill drawer with the first one filled. Other jobs then offer Load result in the panel.
// The template edited meanwhile, any job's result is offered under the toolbar instead, as a hidden
// job's is, so it never replaces those edits unasked.
//
// The chat (the right column's Chat tab, core/chat.js, created in workbench.js): each message runs one revise job
// (`ask`, the panel's `send`), with the area last dragged on empty page space (with AI on, a drag marks one for the
// chat and brings up the Chat tab) and the chat's last exchanges. The chat says how it goes, not the job panel. Its
// finished template is held as a proposal, not loaded: the chat shows the agent's summary and the number of edits,
// and Apply puts it on screen as one undo step, only while the template on screen is still the one the job was sent
// (else it is refused as stale, as the chat then says); Discard drops it. A job that fails or is cancelled changes
// nothing, its partial result dropped.

const NOUNS = { build: "build", adapt: "adapt", revise: "revision", oneshot: "mapping" };
const TITLES = { build: "AI build", adapt: "AI adapt", revise: "AI revision", oneshot: "One-shot mapping" };

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** One step in words. Step 0 is a build's page pass, reported once it is done, before the agent's steps. */
function stepText(s) {
  const name = s.step === 0 ? "Page pass" : `Step ${s.step}`;
  if (s.finished) return `${name} · finished${s.summary ? `: ${s.summary}` : ""}`;
  const refused = s.ops_rejected ? `, ${s.ops_rejected} refused` : "";
  const note = s.note && s.step > 0 ? ` · ${s.note}` : ""; // what the reply did: traces a step that made no edit
  return `${name} · ${plural(s.ops_applied, "edit")} made${refused} · ${plural(s.unmapped_paths, "data field")} not linked yet${note}`;
}

/** The agent's steps so far (the page pass is not one). */
const agentSteps = (job) => job.steps.filter((s) => s.step > 0).length;

const stepTokens = (s) => (s ? (s.input_tokens ?? 0) + (s.output_tokens ?? 0) : 0);

/**
 * A one-shot job's tokens: its usage so far, plus a box mapping still running (counted into the usage
 * when it ends): the page pass's event, and the last agent step (whose counts are the agent's running total).
 */
const tokenCount = (job) => {
  const u = job.usage ?? {};
  let n = (u.input_tokens ?? 0) + (u.output_tokens ?? 0);
  if (job.stages?.some((st) => st.key === "boxes" && st.status === "running")) {
    n += stepTokens(job.steps.find((s) => s.step === 0)) + stepTokens(job.steps.filter((s) => s.step > 0).at(-1));
  }
  return n;
};
const tokens = (job) => (tokenCount(job) ? `${tokenCount(job).toLocaleString()} tokens` : "no model calls");

/** A finished one-shot job in a sentence, from its stages' details. */
function oneshotSummary(job) {
  const said = (job.stages ?? []).filter((st) => st.status === "done" && st.detail && st.key !== "form" && st.key !== "data");
  return `${said.map((st) => st.detail).join("; ")}${said.length ? "; " : ""}${tokens(job)}.`;
}

/**
 * Show a job's stages in the list `el` (an `ol.stages`), each with its status and detail; the list is hidden when
 * the job has none. The one-shot panel and extraction's build panel use it.
 * @param {HTMLElement} el
 * @param {{stages?: {key: string, label: string, status: string, detail?: string}[]}} job
 * @returns {object | null} the stage running now, if any
 */
export function showStages(el, job) {
  el.hidden = !job.stages?.length;
  if (el.hidden) return null;
  el.replaceChildren(
    ...job.stages.map((st) => {
      const li = document.createElement("li");
      li.dataset.status = st.status;
      li.append(st.label);
      if (st.detail) {
        const d = document.createElement("span");
        d.className = "detail";
        d.textContent = st.detail;
        li.append(d);
      }
      return li;
    }),
  );
  return job.stages.find((st) => st.status === "running") ?? null;
}

/**
 * @param {object} app see workbench.js
 * @returns {{ask: import("../core/chat.js").Send, adapt: (template: object) => void, oneshot: Function}}
 */
export function mountJobs(app) {
  const { $, engine, session, view } = app;
  const ai = engine.capabilities.ai;
  const panel = $("job");
  const status = $("job-status");
  let handle = null;
  let stopping = false;
  let running = false; // a job is starting or running
  let detached = false; // its panel was closed with Hide
  let token = 0; // counts up per job, and when a new form drops the job running: a dropped job's end is ignored
  let kind = "build"; // the running (or last) job's kind
  let refused = ""; // why the last cancel didn't go through, shown until the next one
  let offered = null; // the result on offer: {job, generation, partial}
  let asking = 0; // the chat's revise jobs running (a cancelled one runs on until its step ends)
  let again = null; // the last job asked for, as Try again starts it afresh: {jobKind, start, opts}

  $("build-ai").hidden = !ai;
  $("ai-note").hidden = ai;

  function showSteps(job) {
    const current = showStages($("job-stages"), job);
    $("job-steps").replaceChildren(
      ...job.steps.map((s) => {
        const li = document.createElement("li");
        li.textContent = stepText(s);
        return li;
      }),
    );
    let next = (job.kind === "build" || job.kind === "oneshot") && job.steps.length === 0 ? "the page pass" : `step ${agentSteps(job) + 1}`;
    if (job.kind === "oneshot" && current?.key !== "boxes") next = current ? current.label.toLowerCase() : "the next stage";
    const used = job.kind === "oneshot" && tokenCount(job) > 0 ? ` · ${tokens(job)} so far` : "";
    if (job.status === "running" && !stopping) status.textContent = `${refused}Working on ${next}…${used}`;
  }

  function setButtons({ cancel = false, load = false, close = false, background = false, retry = false }) {
    $("job-cancel").hidden = !cancel;
    $("job-cancel").disabled = false;
    $("job-load").hidden = !load;
    $("job-retry").hidden = !retry;
    $("job-close").hidden = !close;
    $("job-close").textContent = background ? "Hide" : "Close";
    panel.dataset.state = retry ? "error" : "";
  }

  /**
   * Put a job's template on screen (one undo step), unless the form changed since it started; then
   * hand a one-shot job's data to the fill drawer. `replace: false`: the job only mapped data.
   */
  function load(job, generation, partial, { replace = true } = {}) {
    if (session.generation !== generation) {
      app.notice("That result is for a form that is no longer open.");
      return;
    }
    const data = job.result?.data ?? null;
    if (!replace) {
      if (data) app.showData(data);
      return;
    }
    const what = partial ? "partial result" : "result";
    const text = job.kind === "oneshot" ? "AI one-shot mapping" : `AI ${NOUNS[job.kind]}`;
    app.track(session.replaceTemplate(job.template, { source: { kind: "ai", text } })).then((res) => {
      if (res.error !== undefined) return;
      app.notice(`Loaded the AI's ${what}. Undo goes back to the template before it.${partial ? "" : " Save keeps it as a template."}`);
      if (data && !partial) app.showData(data);
    });
  }

  /** Offer a job's template until it is loaded or discarded. */
  function offer(job, generation, partial, text, opts = {}) {
    offered = { job, generation, partial, opts };
    $("offer-text").textContent =
      `${text} Loading it replaces the template on screen, including any edits made since the job started; Undo goes back.`;
    $("offer-load").textContent = partial ? "Load partial result" : "Load result";
    $("offer").hidden = false;
  }

  function clearOffer() {
    offered = null;
    $("offer").hidden = true;
  }

  $("offer-load").addEventListener("click", () => {
    const o = offered;
    clearOffer();
    if (o !== null) load(o.job, o.generation, o.partial, o.opts);
  });
  $("offer-dismiss").addEventListener("click", clearOffer);
  app.onReset(clearOffer);

  /**
   * Run a job: `start` resolves a JobHandle. The panel shows it beside the page (see the top); Hide
   * closes it and the job goes on, its result offered when it ends (loading it then replaces the
   * template on screen, edits made meanwhile included, as one undo step).
   */
  async function run(jobKind, start, opts = {}) {
    if (running || asking > 0) {
      app.notice("An AI job is already running. Wait for it to end, or cancel it.");
      return;
    }
    running = true;
    detached = false;
    kind = jobKind;
    const mine = ++token;
    again = { jobKind, start, opts };
    const generation = session.generation;
    let before = null; // the template on screen when the job started: still there at its end, nothing was edited
    stopping = false;
    refused = "";
    $("job-title").textContent = TITLES[kind];
    $("job-steps").replaceChildren();
    $("job-stages").replaceChildren();
    $("job-stages").hidden = true;
    status.textContent = "Starting…";
    setButtons({ cancel: true, close: true, background: true });
    panel.hidden = false;
    panel.scrollIntoView?.({ block: "nearest" });
    let job;
    try {
      await app.track(session.idle());
      before = session.template;
      let started;
      try {
        started = await start();
      } catch (e) {
        if (mine === token) ended(e.message);
        return;
      }
      if (mine !== token) {
        started.cancel().catch(() => {}); // its form was closed while it was starting
        return;
      }
      handle = started;
      handle.onUpdate = (j) => {
        if (mine === token) showSteps(j);
      };
      if (stopping) cancel(); // Cancel was pressed while the job was starting
      try {
        job = await started.done;
      } catch (e) {
        if (mine === token) ended(e.message);
        return;
      }
    } finally {
      if (mine === token) {
        handle = null;
        running = false;
      }
    }
    if (mine !== token) return;
    showSteps(job);
    if (job.status === "done") {
      const unplaced = (job.unplaceable ?? []).map((u) => u.path).filter(Boolean);
      const what = jobKind === "oneshot" ? `: ${oneshotSummary(job)}` : job.summary ? `: ${job.summary}` : ".";
      const summary = `${what}${unplaced.length ? ` Not placed: ${unplaced.join(", ")}.` : ""}`;
      const edited = session.generation === generation && session.template !== before && opts.replace !== false;
      if (detached || edited) {
        panel.hidden = true;
        app.notice("");
        offer(job, generation, false, `The AI ${NOUNS[jobKind]} finished${summary}`, opts);
        return;
      }
      if (jobKind === "oneshot") {
        status.textContent = `Done${summary}`;
        panel.hidden = true;
        load(job, generation, false, opts);
        return;
      }
      status.textContent = `Done${summary}`;
      setButtons({ load: true, close: true });
      $("job-load").onclick = () => {
        panel.hidden = true;
        load(job, generation, false);
      };
      $("job-load").focus();
      return;
    }
    const n = plural(agentSteps(job), "step");
    const text =
      job.status === "cancelled"
        ? `The AI ${NOUNS[jobKind]} was cancelled after ${n}. The template is unchanged.`
        : `The AI ${NOUNS[jobKind]} stopped after ${n}: ${job.error ?? "an unknown error"} The template on screen is unchanged.`;
    const failed = job.status !== "cancelled" && !detached && session.generation === generation;
    if (failed) {
      // the panel keeps saying why, with Try again; a partial result is offered under the toolbar too
      status.textContent = text;
      setButtons({ close: true, retry: true });
    } else {
      panel.hidden = true;
    }
    if (job.template && opts.replace !== false) {
      app.notice("");
      offer(job, generation, true, text, opts);
    } else if (!failed) {
      app.notice(text);
    }
  }

  /** The job could not be started or followed: say why, with Try again and a way out. */
  function ended(message) {
    if (detached) {
      app.notice(message);
      return;
    }
    status.textContent = message;
    setButtons({ close: true, retry: true });
  }

  async function cancel() {
    try {
      await handle.cancel();
    } catch (e) {
      // not stopped: say so, and let the person try again or close the panel
      stopping = false;
      refused = `The job couldn't be stopped: ${e.message} `;
      status.textContent = refused.trim();
      $("job-cancel").disabled = false;
    }
  }

  $("job-cancel").addEventListener("click", () => {
    stopping = true;
    refused = "";
    $("job-cancel").disabled = true;
    status.textContent = "Stopping after this step…";
    if (handle !== null) cancel(); // else still starting: run() cancels it once it has started
  });
  $("job-close").addEventListener("click", () => {
    panel.hidden = true;
    if (running) {
      detached = true;
      app.notice(`The AI ${NOUNS[kind]} goes on running. Its result will be offered under the toolbar when it ends.`);
    }
  });
  $("job-retry").addEventListener("click", () => {
    if (running || again === null || app.template === null) return;
    const { jobKind, start, opts } = again;
    run(jobKind, start, opts);
  });
  app.onReset(() => {
    // the panel and the job belong to the form they ran on: another form drops them (the job is asked to stop)
    panel.hidden = true;
    if (!running) return;
    token += 1;
    running = false;
    handle?.cancel().catch(() => {});
    handle = null;
  });

  $("build-ai").addEventListener("click", () => {
    if (app.template === null || app.state.reading) return;
    // no schema yet (a PDF opened on its own): the one-shot job designs one, then links the boxes
    const props = session.template.schema?.properties;
    if (typeof engine.oneshot === "function" && !(props && Object.keys(props).length)) {
      run("oneshot", () => engine.oneshot(session.doc, { check: true }), { replace: true });
      return;
    }
    run("build", () => engine.build(session.doc, session.template.schema));
  });

  // ---------------------------------------------------------------- the chat's revise

  app.bus.on("region", ({ page, rect }) => {
    if (!ai || app.template === null || app.state.reading) {
      view.clearRegion();
      return;
    }
    app.chat.setArea({ page, rect });
    app.showSideTab("chat");
  });

  /**
   * The chat's `send` (core/chat.js): run a revise job for `message`, with `area` and `history`, and answer with its
   * template held (see the top). Rejects with why when the job can't run, fails, or is cancelled.
   */
  async function ask(message, area, history, signal) {
    if (app.template === null) throw new Error("No form is open.");
    if (app.state.reading) throw new Error("The form is still being read. Ask once its boxes are in.");
    if (running) throw new Error("An AI job is already running. Wait for it to end, or cancel it.");
    asking += 1;
    let job;
    let sent;
    const generation = session.generation;
    try {
      await app.track(session.idle());
      sent = session.template;
      const region = area === null ? null : { page: area.page, rect: area.rect };
      const started = await engine.revise(sent, session.doc, message, region, history);
      if (signal.aborted) started.cancel().catch(() => {});
      else signal.addEventListener("abort", () => started.cancel().catch(() => {}), { once: true });
      job = await started.done;
    } finally {
      asking -= 1;
    }
    if (session.generation !== generation) throw new Error("Another form was opened meanwhile.");
    if (job.status === "cancelled") throw new Error("Cancelled.");
    if (job.status !== "done") throw new Error(`The AI revision stopped: ${job.error ?? "an unknown error"} The template is unchanged.`);
    const unplaced = (job.unplaceable ?? []).map((u) => u.path).filter(Boolean);
    const reply = `${job.summary || "Done."}${unplaced.length ? ` Not placed: ${unplaced.join(", ")}.` : ""}`;
    const edits = job.steps.reduce((n, s) => n + (s.ops_applied ?? 0), 0);
    if (edits === 0) return { reply, changes: [], apply: null, discard: () => {} };
    let proposal = job.template;
    return {
      reply,
      changes: [`${plural(edits, "edit")} to the template`],
      apply: async () => {
        await app.track(session.idle());
        if (proposal === null || session.generation !== generation || session.template !== sent) {
          throw Object.assign(new Error("The template changed since this was proposed."), { code: "stale" });
        }
        const res = await app.track(session.replaceTemplate(proposal, { source: { kind: "ai", text: "AI revision" } }));
        if (res.error !== undefined) throw new Error(res.error);
        proposal = null;
      },
      discard: () => {
        proposal = null;
      },
    };
  }

  return {
    ask,

    /** Start an adapt job from a stored template (the seed is already on screen). */
    adapt(template) {
      run("adapt", () => engine.build(session.doc, null, { startFrom: template }));
    },

    /**
     * Start a one-shot job on the document on screen. With `template` it maps only the data, for that
     * template (nothing on screen is replaced); otherwise it designs the schema (unless `schema` is given)
     * and maps the boxes, and its template replaces the seed on screen.
     * @param {{schema?: object | string | null, template?: object | null, dataset?: object | null,
     *   saved?: string | null, check?: boolean}} opts `dataset` is a summary from engine.data
     */
    oneshot({ schema = null, template = null, dataset = null, saved = null, check = true } = {}) {
      if (typeof engine.oneshot !== "function") return;
      const data = dataset?.id ?? null;
      run("oneshot", () => engine.oneshot(session.doc, { schema, template, data, saved, check }), { replace: template === null });
    },
  };
}
