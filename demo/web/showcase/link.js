// The Link step: the schema (the sample's, or one the visitor pastes), the schema list with
// placement status, the inspector for the selected box, and the history with undo and redo.

import { HistoryPanel } from "../core/history.js";
import { Inspector } from "../core/inspector.js";
import { showLinkPanels } from "../core/panels.js";
import { flattenSchema, placeholderRecord } from "../core/schema.js";
import { SchemaList } from "../core/schemalist.js";

/** @param {object} app see showcase.js */
export function mountLink(app) {
  const { $, bus, session, state, say } = app;
  const schemaList = new SchemaList($("schemalist"), { bus, draggable: !app.touch });
  const inspector = new Inspector($("inspector"), { bus });
  const history = new HistoryPanel($("history"), { bus });
  const error = $("schema-error");

  $("link-hint").textContent = app.touch
    ? "Tap a box on the page, then pick its data field below."
    : "Drag a field onto a box, or select a box and pick its data field below.";

  /** Swap the template's schema (undoable). Boxes keep their links, flagged when a path is gone. */
  async function useSchema(schema, record) {
    if (app.template === null) return;
    // built from the template when its turn comes, so edits still in flight are kept
    const res = await session.replaceTemplate((t) => ({ ...structuredClone(t), schema: structuredClone(schema) }));
    if (res.error === undefined) app.setRecord(record);
  }

  $("use-schema").addEventListener("click", () => {
    say(error, "");
    let schema;
    try {
      schema = JSON.parse($("schema-text").value);
    } catch (e) {
      say(error, `This isn't valid JSON: ${e.message}`);
      return;
    }
    let record;
    try {
      flattenSchema(schema);
      record = placeholderRecord(schema);
    } catch (e) {
      say(error, `This schema can't be used: ${e.message}`);
      return;
    }
    if (app.template === null) {
      say(error, "Open a form first.");
      return;
    }
    useSchema(schema, record);
  });

  $("sample-schema").addEventListener("click", () => {
    say(error, "");
    if (state.sample) useSchema(state.sample.schema, state.sample.record);
  });

  bus.on("pick", ({ path }) => {
    const t = app.template;
    const field = t?.fields.find((f) => (f.source?.paths ?? []).includes(path));
    if (field) {
      say($("op-msg"), "");
      app.select(field.id);
    } else {
      say($("op-msg"), `No box uses ${path} yet. ${app.touch ? "Tap a box and pick it in the inspector." : "Drag it onto a box, or pick it in the inspector."}`);
    }
  });

  app.onRender(() => {
    $("sample-schema").hidden = state.sample === null;
    $("schema-label").textContent = showLinkPanels({ schemaList, inspector, history }, {
      template: app.template,
      schemaPaths: app.schemaPaths(),
      selected: state.selected,
      editWarnings: session.fieldWarnings,
      canUndo: session.canUndo,
      canRedo: session.canRedo,
    });
  });
}
