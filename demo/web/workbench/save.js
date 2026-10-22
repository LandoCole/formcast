// Save: put the template on screen in the server's template store under a name. A saved template is
// scored against every PDF opened later, so the match prompt offers it.

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
      await engine.templates.put(name, session.template);
      state.savedName = name;
      box.hidden = true;
      app.notice(`Saved as “${name}”. Opening this form again offers it.`);
    } catch (e) {
      say(msg, e.message);
    } finally {
      $("save-go").disabled = false;
    }
  });
}
