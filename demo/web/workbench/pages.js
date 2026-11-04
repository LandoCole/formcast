// Finding the blanks page by page. A PDF opened with "I'll pick the form pages myself" is read with no
// detection, and a page an automatic reading skipped as running text was not searched either. On such a
// page the page bar offers "Find the blanks on this page": the server runs detection on that page alone
// (engine.detectPage) and keeps its boxes in the form map, so Build with AI later works from them, and the
// new boxes join the template on screen as one undo step. Beside it, a word on the page: searched with how
// many boxes, skipped as prose, not searched yet. What each page is comes from the reading's last progress
// report (`doc.reading`, start.js), updated here as pages are searched.
//
// With AI on, the page bar also offers "Label and map N boxes with AI" on a page with boxes that have no label or no
// data mapping yet (`fillInBoxes`): one model call for that page alone (engine.fillInPage) that fills in only what
// each of those boxes lacks. A box that has a mapping keeps it and a box that has a label keeps it, whoever set them;
// the result goes on screen as one undo step. Edits made while the call runs are kept: what the AI gave is laid over
// them only where a box still lacks it.

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

const mapped = (f) => (f.source?.paths?.length ?? 0) > 0;
const labelled = (f) => (f.label ?? "").trim() !== "";

/** The boxes of page `n` the AI is asked to label and map: those with no data mapping or no label yet. */
export function fillInBoxes(template, n) {
  return (template?.fields ?? []).filter((f) => f.page === n && f.rect && (!mapped(f) || !labelled(f)));
}

/**
 * `current` with what `result` (a fill-in of page `n` started from an earlier template) gave the boxes that still
 * lack it: a mapping (with its type and required mark) for a box with none, a label for a box with none; and the
 * tables it added. Returns `result` itself when nothing changed meanwhile.
 */
export function layFillIn(current, sent, result) {
  if (current === sent) return result;
  const byId = new Map(result.fields.map((f) => [f.id, f]));
  const have = new Set(current.fields.map((f) => f.id));
  const fields = current.fields.map((f) => {
    const r = byId.get(f.id);
    if (!r) return f;
    let out = f;
    if (!mapped(f) && mapped(r)) out = { ...out, source: r.source, type: r.type, required: r.required, required_reason: r.required_reason, required_by: r.required_by };
    if (!labelled(f) && labelled(r)) out = { ...out, label: r.label };
    return out;
  });
  const added = result.fields.filter((f) => !have.has(f.id) && !sent.fields.some((g) => g.id === f.id));
  return { ...current, fields: [...fields, ...added] };
}

/** @param {object} app see workbench.js; returns `{render}`, called whenever the panels redraw */
export function mountPages(app) {
  const { $, engine, session, state } = app;
  const button = $("detect-page");
  const readout = $("page-state");
  const fillIn = $("fill-in-page");
  const searching = new Set(); // `${doc id}:${page}` being searched now
  const filling = new Set(); // `${doc id}:${page}` being labelled and mapped now

  const pageInfo = (doc, n) => doc?.reading?.pages?.[n] ?? null;
  const unsearched = (info) => info !== null && info.kind !== "fillable" && (info.skipped === "prose" || info.skipped === "manual");

  function renderFillIn() {
    const doc = session.doc;
    const on = typeof engine.fillInPage === "function" && session.template !== null && doc != null && !state.reading;
    const busy = on && filling.has(`${doc.id}:${state.page}`);
    const n = on ? fillInBoxes(session.template, state.page).length : 0;
    fillIn.hidden = !busy && n === 0;
    fillIn.disabled = busy;
    fillIn.textContent = busy ? "Labelling and mapping…" : `Label and map ${plural(n, "box", "boxes")} with AI`;
    fillIn.title = busy ? "" : "Only the boxes with no label or no data mapping yet: boxes that have them keep them.";
  }

  function render() {
    renderFillIn();
    const doc = session.doc;
    const info = typeof engine.detectPage === "function" && session.template !== null ? pageInfo(doc, state.page) : null;
    if (info === null) {
      button.hidden = true;
      readout.textContent = "";
      return;
    }
    const busy = searching.has(`${doc.id}:${state.page}`);
    button.hidden = !busy && !unsearched(info);
    button.disabled = busy;
    button.textContent = busy ? "Finding the blanks…" : "Find the blanks on this page";
    button.title = info.skipped === "prose" ? "Skipped as running text when the form was read. Search it anyway." : "";
    if (busy) readout.textContent = "";
    else if (info.kind === "fillable") readout.textContent = "fillable page";
    else if (info.skipped === "prose") readout.textContent = "skipped as prose";
    else if (info.skipped === "manual") readout.textContent = "not searched";
    else readout.textContent = `${plural(info.boxes, "blank")} found`;
    if (doc.detect === "manual") {
      const searched = doc.reading.pages.filter((p) => p.kind !== "fillable" && !p.skipped).length;
      readout.textContent += ` · ${plural(searched, "page")} searched`;
    }
  }

  button.addEventListener("click", async () => {
    const doc = session.doc;
    const n = state.page;
    const key = `${doc.id}:${n}`;
    if (searching.has(key)) return;
    searching.add(key);
    render();
    try {
      const out = await engine.detectPage(doc, n);
      const info = pageInfo(doc, n);
      if (info !== null) Object.assign(info, { skipped: null, boxes: out.boxes, state: "done" });
      if (session.doc === doc && out.fields.length) {
        await app.track(
          session.replaceTemplate(
            (current) => {
              const have = new Set(current.fields.map((f) => f.id));
              return { ...current, fields: [...current.fields, ...out.fields.filter((f) => !have.has(f.id))] };
            },
            { source: { kind: "seed", text: `Blanks found on page ${n + 1}` } },
          ),
        );
      }
      if (session.doc === doc) {
        app.notice(out.boxes ? `Page ${n + 1}: ${plural(out.boxes, "blank")} found.` : `Page ${n + 1}: no blanks found.`);
      }
    } catch (e) {
      if (session.doc === doc) app.notice(`Page ${n + 1} couldn't be searched: ${e.message}`);
    } finally {
      searching.delete(key);
      app.render();
    }
  });

  fillIn.addEventListener("click", async () => {
    const doc = session.doc;
    const n = state.page;
    const key = `${doc.id}:${n}`;
    const sent = session.template;
    if (filling.has(key) || sent === null) return;
    filling.add(key);
    renderFillIn();
    try {
      const out = await engine.fillInPage(sent, doc, n);
      if (session.doc !== doc) return;
      if (out.status === "error" || out.status === "unparsed") {
        app.notice(`Page ${n + 1} couldn't be labelled and mapped: ${out.error ?? "the AI's answer couldn't be read"}. Try again.`);
        return;
      }
      if (out.applied > 0) {
        await app.track(
          session.replaceTemplate((current) => layFillIn(current, sent, out.template), {
            source: { kind: "ai", text: `AI labelled and mapped page ${n + 1}` },
          }),
        );
      }
      const left = fillInBoxes(session.template, n).length;
      app.notice(
        `Page ${n + 1}: ${plural(out.applied, "edit")} by the AI${left ? `; ${plural(left, "box", "boxes")} still without a label or a mapping` : ""}. Undo goes back.`,
      );
    } catch (e) {
      if (session.doc === doc) app.notice(`Page ${n + 1} couldn't be labelled and mapped: ${e.message}`);
    } finally {
      filling.delete(key);
      app.render();
    }
  });

  return { render };
}
