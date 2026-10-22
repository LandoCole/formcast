// AI jobs: Build with AI, Adapt (from the match prompt) and area revise (drag on empty page space,
// then type an instruction). A job runs on the server; the panel lists its steps as they come and
// offers Cancel. Cancel is seen between steps, so the panel says it stops after the step running
// now. A finished job's template loads with one undo step, so Undo returns to the template from
// before the AI. A cancelled or failed job closes the panel and leaves the template alone; its
// partial result (which includes the step that was running) can still be loaded from the notice.

const NOUNS = { build: "build", adapt: "adapt", revise: "revision" };
const TITLES = { build: "AI build", adapt: "AI adapt", revise: "AI revision" };

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** One step in words. */
function stepText(s) {
  if (s.finished) return `Step ${s.step} · finished${s.summary ? `: ${s.summary}` : ""}`;
  const refused = s.ops_rejected ? `, ${s.ops_rejected} refused` : "";
  return `Step ${s.step} · ${plural(s.ops_applied, "edit")} made${refused} · ${plural(s.unmapped_paths, "data field")} not linked yet`;
}

/**
 * @param {object} app see workbench.js
 * @returns {{adapt: (template: object, name: string) => void}}
 */
export function mountJobs(app) {
  const { $, engine, session, view } = app;
  const ai = engine.capabilities.ai;
  const panel = $("job");
  const status = $("job-status");
  const ask = $("ask");
  let handle = null;
  let stopping = false;
  let region = null;

  $("build-ai").hidden = !ai;
  $("ai-note").hidden = ai;

  function showSteps(job) {
    $("job-steps").replaceChildren(
      ...job.steps.map((s) => {
        const li = document.createElement("li");
        li.textContent = stepText(s);
        return li;
      }),
    );
    if (job.status === "running" && !stopping) status.textContent = `Working on step ${job.steps.length + 1}…`;
  }

  function setButtons({ cancel = false, load = false, close = false }) {
    $("job-cancel").hidden = !cancel;
    $("job-cancel").disabled = false;
    $("job-load").hidden = !load;
    $("job-close").hidden = !close;
  }

  /** Put a job's template on screen (one undo step), unless the form changed since it started. */
  function load(job, generation, partial) {
    if (session.generation !== generation) {
      app.notice("That result is for a form that is no longer open.");
      return;
    }
    const what = partial ? "partial result" : "result";
    app.track(session.replaceTemplate(job.template, { source: { kind: "ai", text: `AI ${NOUNS[job.kind]}` } })).then((res) => {
      if (res.error === undefined) app.notice(`Loaded the AI's ${what}. Undo goes back to the template before it.`);
    });
  }

  /**
   * Run a job: `start` resolves a JobHandle. The panel stays open (and the page behind it waits)
   * until the job ends.
   */
  async function run(kind, start) {
    const generation = session.generation;
    stopping = false;
    $("job-title").textContent = TITLES[kind];
    $("job-steps").replaceChildren();
    status.textContent = "Starting…";
    setButtons({ cancel: true });
    panel.hidden = false;
    await app.track(session.idle());
    try {
      handle = await start();
    } catch (e) {
      status.textContent = e.message;
      setButtons({ close: true });
      return;
    }
    handle.onUpdate = showSteps;
    if (stopping) handle.cancel().catch(() => {}); // Cancel was pressed while the job was starting
    let job;
    try {
      job = await handle.done;
    } catch (e) {
      status.textContent = e.message;
      setButtons({ close: true });
      return;
    } finally {
      handle = null;
    }
    showSteps(job);
    if (job.status === "done") {
      const unplaced = (job.unplaceable ?? []).map((u) => u.path).filter(Boolean);
      status.textContent = `Done${job.summary ? `: ${job.summary}` : "."}${unplaced.length ? ` Not placed: ${unplaced.join(", ")}.` : ""}`;
      setButtons({ load: true, close: true });
      $("job-load").onclick = () => {
        panel.hidden = true;
        load(job, generation, false);
      };
      $("job-load").focus();
      return;
    }
    panel.hidden = true;
    const n = plural(job.steps.length, "step");
    const text =
      job.status === "cancelled"
        ? `The AI ${NOUNS[kind]} was cancelled after ${n}. The template is unchanged.`
        : `The AI ${NOUNS[kind]} stopped after ${n}: ${job.error ?? "an unknown error"} The template on screen is unchanged.`;
    const action = job.template ? { label: "Load partial result", run: () => load(job, generation, true) } : null;
    app.notice(text, action);
  }

  $("job-cancel").addEventListener("click", async () => {
    stopping = true;
    $("job-cancel").disabled = true;
    status.textContent = "Stopping after this step…";
    if (handle === null) return; // still starting: run() cancels it once it has started
    try {
      await handle.cancel();
    } catch (e) {
      status.textContent = e.message;
    }
  });
  $("job-close").addEventListener("click", () => {
    panel.hidden = true;
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
    if (!ai || app.template === null) return;
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
  };
}
