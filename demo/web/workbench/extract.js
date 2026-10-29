// The way in, and the page's two modes. The page opens on "What would you like to do?" (#choose): Fill a PDF opens
// the start screen (start.js) as it always was, and Extract from PDFs switches the page to its extraction mode,
// offered only when the server can extract (the engine has `extract`). The toolbar's name button of either mode
// (#doc-name filling, #doc-pick extracting) brings the choice back, and so does the start screen's Back while no form
// is open.
//
// The mode is `body[data-mode]`, "fill" or "extract": the parts of the page that belong to one mode carry
// `data-for="fill"` or `data-for="extract"`, and extract.css shows only the current mode's (its toolbar groups and
// its main views). Switching mode leaves the other mode's work as it is: a form open for filling is still there,
// with its undo history, when the person comes back to Fill. The bus hears `mode` ({mode}) on every switch.
//
// What extracting shows (the Extract modal, the setup screen, the review) comes with the modules that fill
// #extract-views.

/**
 * @param {object} app see workbench.js
 * @returns {{show: () => void, setMode: (mode: "fill" | "extract") => void}}
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

  fill.addEventListener("click", () => {
    choose.hidden = true;
    setMode("fill");
    app.start.showStart();
  });
  extract.addEventListener("click", () => {
    if (!canExtract) return;
    choose.hidden = true;
    setMode("extract");
  });
  $("doc-name").addEventListener("click", show);
  $("doc-pick").addEventListener("click", show);

  return { show, setMode };
}
