// Save: put the template on screen in the server's template store under a name, with the PDF it was
// made for and, when your data was mapped by the AI or a saved mapping, that data mapping. A saved
// template opens from the start screen's Saved templates (no detection, no AI), is scored against every
// PDF opened later (so the match prompt offers it), fills tables with the same columns without AI, and
// takes records programmatically: POST /api/templates/<name>/fill.

/** Store names, as the server accepts them (demo/server/store.py). */
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$/;

/** A store name made from a file name stem. */
function suggest(stem) {
  const name = stem.replace(/[^A-Za-z0-9 _.-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "").slice(0, 80);
  return name || "template";
}

/** @param {object} app see workbench.js */
export function mountSave(app) {
  const { $, engine, session, state, say } = app;
  const box = $("save-box");
  const msg = $("save-msg");

  $("save").addEventListener("click", () => {
    if (app.template === null) return;
    $("save-what").textContent =
      "Saves the boxes and their links, the data schema and this PDF, so the form opens from Saved templates without detection or AI" +
      (app.data?.mapping && app.data.mode !== "direct" ? ", and your data mapping, so a file with the same columns fills without AI." : ".");
    $("save-name").value = state.savedName ?? suggest(app.stem());
    say(msg, "");
    box.hidden = false;
    $("save-name").select();
  });
  $("save-cancel").addEventListener("click", () => {
    box.hidden = true;
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape") box.hidden = true;
    if (e.key === "Enter" && e.target === $("save-name")) $("save-go").click();
  });
  $("save-go").addEventListener("click", async () => {
    const name = $("save-name").value.trim();
    if (!NAME_RE.test(name)) {
      say(msg, "Use 1 to 80 letters, digits, spaces, dots, dashes or underscores, starting with a letter or digit.");
      return;
    }
    $("save-go").disabled = true;
    say(msg, "Saving…");
    try {
      await app.track(session.idle()); // edits still on their way are part of what gets saved
      const mapping = app.data?.mapping && app.data.mode !== "direct" ? app.data.mapping : null;
      await engine.templates.put(name, session.template, { doc: session.doc, mapping });
      state.savedName = name;
      box.hidden = true;
      const kept = mapping ? ", its PDF and your data mapping" : " and its PDF";
      app.notice(
        `Saved “${name}”${kept}. Open it from Saved templates; fill it from code with POST /api/templates/${encodeURIComponent(name)}/fill (a record, records, a CSV or Excel file, or text).`,
      );
    } catch (e) {
      say(msg, e.message);
    } finally {
      $("save-go").disabled = false;
    }
  });
}
