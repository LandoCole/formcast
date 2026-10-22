// The Link step: the schema (the sample's, or one the visitor pastes), the schema list with
// placement status, the inspector for the selected box, and the history with undo and redo.

import { HistoryPanel } from "../core/history.js";
import { Inspector } from "../core/inspector.js";
import { flattenSchema, placeholderRecord } from "../core/schema.js";
import { SchemaList, placementStatus } from "../core/schemalist.js";

/** @param {object} app see showcase.js */
export function mountLink(app) {
  const { $, bus, state, say } = app;
  const schemaList = new SchemaList($("schemalist"), { bus, draggable: !app.phone });
  const inspector = new Inspector($("inspector"), { bus });
  const history = new HistoryPanel($("history"), { bus });
  const error = $("schema-error");

  $("link-hint").textContent = app.phone
    ? "Tap a box on the page, then pick its data field below."
    : "Drag a field onto a box, or select a box and pick its data field below.";

  /** Swap the template's schema (undoable). Boxes keep their links, flagged when a path is gone. */
  function useSchema(schema, record) {
    const t = app.template;
    if (t === null) return;
    app.commit({ ...structuredClone(t), schema: structuredClone(schema) });
    app.setRecord(record);
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
      say($("op-msg"), `No box uses ${path} yet. ${app.phone ? "Tap a box and pick it in the inspector." : "Drag it onto a box, or pick it in the inspector."}`);
    }
  });

  app.onRender(() => {
    const t = app.template;
    $("sample-schema").hidden = state.sample === null;
    if (t === null) {
      schemaList.set([], { fields: [] });
      inspector.show(null);
      history.set({ fields: [], history: [] }, { canUndo: false, canRedo: false });
      $("schema-label").textContent = "Schema";
      return;
    }
    const paths = app.schemaPaths();
    schemaList.set(paths, t);
    const { status } = placementStatus(paths, t);
    const placed = [...status.values()].filter((s) => s === "placed").length;
    const title = typeof t.schema?.title === "string" && t.schema.title ? `${t.schema.title} · ` : "";
    $("schema-label").textContent = `Schema · ${title}${placed} of ${status.size} placed`;
    const field = t.fields.find((f) => f.id === state.selected) ?? null;
    schemaList.setPicked(field?.source?.paths?.[0] ?? null);
    inspector.show(field, { schemaPaths: paths, warnings: field ? (state.warnings[field.id] ?? []) : [] });
    history.set(t, { canUndo: state.undo.canUndo, canRedo: state.undo.canRedo });
  });
}
