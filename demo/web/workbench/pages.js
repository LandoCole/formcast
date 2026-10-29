// Finding the blanks page by page. A PDF opened with "I'll pick the form pages myself" is read with no
// detection, and a page an automatic reading skipped as running text was not searched either. On such a
// page the page bar offers "Find the blanks on this page": the server runs detection on that page alone
// (engine.detectPage) and keeps its boxes in the form map, so Build with AI later works from them, and the
// new boxes join the template on screen as one undo step. Beside it, a word on the page: searched with how
// many boxes, skipped as prose, not searched yet. What each page is comes from the reading's last progress
// report (`doc.reading`, start.js), updated here as pages are searched.

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** @param {object} app see workbench.js; returns `{render}`, called whenever the panels redraw */
export function mountPages(app) {
  const { $, engine, session, state } = app;
  const button = $("detect-page");
  const readout = $("page-state");
  const searching = new Set(); // `${doc id}:${page}` being searched now

  const pageInfo = (doc, n) => doc?.reading?.pages?.[n] ?? null;
  const unsearched = (info) => info !== null && info.kind !== "fillable" && (info.skipped === "prose" || info.skipped === "manual");

  function render() {
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

  return { render };
}
