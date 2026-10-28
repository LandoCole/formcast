// AI jobs: the one-shot mapping (from the start screen and the fill drawer's "Add data"), Build with
// AI, Adapt (from the match prompt) and area revise (drag on empty page space, then type an
// instruction). One at a time. A job runs on the server; the panel lists its steps as
// they come and offers Cancel, or Run in background (the job goes on). Cancel is seen between steps,
// so the panel says it stops after the step running now. A finished job's template loads with one
// undo step, so Undo returns to the template from before it. A cancelled or failed job closes the
// panel and leaves the template alone.
//
// A result not loaded from the panel (a job run in the background, or the partial result of a
// cancelled or failed one, which includes the step that was running) is kept here, with the job's
// snapshot, and offered on its own line under the toolbar until it is loaded or discarded. Other
// messages don't replace it. Opening another form discards it (it belongs to the PDF it ran on).
//
// A one-shot job lists its stages (read the form, design the schema, map the boxes, read and map the
// data) with the agent's steps under them, and the tokens used so far. When it ends with the panel
// open, its result loads at once (the panel is modal, so nothing was edited meanwhile): the template,
// unless the job only mapped data for the template on screen, and the data's records, which open in
// the fill drawer with the first one filled.

const NOUNS = { build: "build", adapt: "adapt", revise: "revision", oneshot: "mapping" };
const TITLES = { build: "AI build", adapt: "AI adapt", revise: "AI revision", oneshot: "One-shot mapping" };

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** One step in words. Step 0 is a build's page pass, reported once it is done, before the agent's steps. */
function stepText(s) {
  const name = s.step === 0 ? "Page pass" : `Step ${s.step}`;
  if (s.finished) return `${name} · finished${s.summary ? `: ${s.summary}` : ""}`;
  const refused = s.ops_rejected ? `, ${s.ops_rejected} refused` : "";
  return `${name} · ${plural(s.ops_applied, "edit")} made${refused} · ${plural(s.unmapped_paths, "data field")} not linked yet`;
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
 * @param {object} app see workbench.js
 * @returns {{adapt: (template: object) => void}}
 */
export function mountJobs(app) {
  const { $, engine, session, view } = app;
  const ai = engine.capabilities.ai;
  const panel = $("job");
  const status = $("job-status");
  const ask = $("ask");
  let handle = null;
  let stopping = false;
  let running = false; // a job is starting or running
  let detached = false; // its panel was closed with "Run in background"
  let kind = "build"; // the running (or last) job's kind
  let refused = ""; // why the last cancel didn't go through, shown until the next one
  let offered = null; // the result on offer: {job, generation, partial}
  let region = null;

  $("build-ai").hidden = !ai;
  $("ai-note").hidden = ai;

  function showStages(job) {
    const list = $("job-stages");
    list.hidden = !job.stages?.length;
    if (list.hidden) return null;
    list.replaceChildren(
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

  function showSteps(job) {
    const current = showStages(job);
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

  function setButtons({ cancel = false, load = false, close = false, background = false }) {
    $("job-cancel").hidden = !cancel;
    $("job-cancel").disabled = false;
    $("job-load").hidden = !load;
    $("job-close").hidden = !close;
    $("job-close").textContent = background ? "Run in background" : "Close";
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
   * Run a job: `start` resolves a JobHandle. The panel is modal while it is open; "Run in background"
   * closes it and the job goes on, its result offered when it ends (loading it then replaces the
   * template on screen, edits made meanwhile included, as one undo step).
   */
  async function run(jobKind, start, opts = {}) {
    if (running) {
      app.notice("An AI job is already running. Wait for it to end, or cancel it.");
      return;
    }
    running = true;
    detached = false;
    kind = jobKind;
    const generation = session.generation;
    stopping = false;
    refused = "";
    $("job-title").textContent = TITLES[kind];
    $("job-steps").replaceChildren();
    $("job-stages").replaceChildren();
    $("job-stages").hidden = true;
    status.textContent = "Starting…";
    setButtons({ cancel: true, close: true, background: true });
    panel.hidden = false;
    $("job-cancel").focus();
    let job;
    try {
      await app.track(session.idle());
      try {
        handle = await start();
      } catch (e) {
        ended(e.message);
        return;
      }
      handle.onUpdate = showSteps;
      if (stopping) cancel(); // Cancel was pressed while the job was starting
      try {
        job = await handle.done;
      } catch (e) {
        ended(e.message);
        return;
      }
    } finally {
      handle = null;
      running = false;
    }
    showSteps(job);
    if (job.status === "done") {
      const unplaced = (job.unplaceable ?? []).map((u) => u.path).filter(Boolean);
      const what = jobKind === "oneshot" ? `: ${oneshotSummary(job)}` : job.summary ? `: ${job.summary}` : ".";
      const summary = `${what}${unplaced.length ? ` Not placed: ${unplaced.join(", ")}.` : ""}`;
      if (detached) {
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
    panel.hidden = true;
    const n = plural(agentSteps(job), "step");
    const text =
      job.status === "cancelled"
        ? `The AI ${NOUNS[jobKind]} was cancelled after ${n}. The template is unchanged.`
        : `The AI ${NOUNS[jobKind]} stopped after ${n}: ${job.error ?? "an unknown error"} The template on screen is unchanged.`;
    if (job.template && opts.replace !== false) {
      app.notice("");
      offer(job, generation, true, text, opts);
    } else {
      app.notice(text);
    }
  }

  /** The job could not be started or followed: say why, with a way out. */
  function ended(message) {
    if (detached) {
      app.notice(message);
      return;
    }
    status.textContent = message;
    setButtons({ close: true });
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

  $("build-ai").addEventListener("click", () => {
    if (app.template === null) return;
    run("build", () => engine.build(session.doc, session.template.schema));
  });

  // ---------------------------------------------------------------- area revise

  function closeAsk() {
    ask.hidden = true;
    region = null;
    view.clearRegion();
  }

  app.bus.on("region", (r) => {
    if (!ai || app.template === null || running) {
      view.clearRegion();
      return;
    }
    region = r;
    const col = $("col-page");
    const marquee = $("pageview").querySelector(".fc-marquee");
    const c = col.getBoundingClientRect();
    const m = marquee?.getBoundingClientRect() ?? c;
    ask.hidden = false;
    const width = ask.offsetWidth;
    const left = Math.max(8, Math.min(m.left - c.left + col.scrollLeft, col.scrollWidth - width - 8));
    ask.style.left = `${left}px`;
    // below the area, or above it when the column has no room left below
    const below = m.bottom - c.top + col.scrollTop + 8;
    const above = m.top - c.top + col.scrollTop - ask.offsetHeight - 8;
    const room = col.scrollTop + col.clientHeight - below - ask.offsetHeight;
    ask.style.top = `${room < 0 && above >= 0 ? above : below}px`;
    ask.scrollIntoView({ block: "nearest" });
    $("ask-text").focus({ preventScroll: true });
  });
  $("ask-cancel").addEventListener("click", closeAsk);
  ask.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAsk();
  });
  $("ask-go").addEventListener("click", () => {
    const instruction = $("ask-text").value.trim();
    if (!instruction) {
      $("ask-text").focus();
      return;
    }
    const area = region;
    closeAsk();
    $("ask-text").value = "";
    run("revise", () => engine.revise(session.template, session.doc, instruction, area));
  });
  app.onReset(closeAsk);

  return {
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
