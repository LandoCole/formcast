// The linking panels together: the schema list, the inspector and the history, shown for the
// template on screen. Both layouts call this on every render, so they show the same thing.

import { schemaLabel } from "./schemalist.js";

/**
 * Show a template (or the empty state for null) in the schema list, inspector and history.
 * @param {{schemaList: import("./schemalist.js").SchemaList, inspector: import("./inspector.js").Inspector,
 *   history: import("./history.js").HistoryPanel}} parts
 * @param {{template: object | null, schemaPaths: import("./schema.js").SchemaPath[], selected: string | null,
 *   editWarnings?: Record<string, string[]>, canUndo: boolean, canRedo: boolean}} state `editWarnings`:
 *   the session's warnings by field id
 * @returns {string} the schema heading ("Schema" with no template; see `schemaLabel`)
 */
export function showLinkPanels({ schemaList, inspector, history }, { template, schemaPaths, selected, editWarnings = {}, canUndo, canRedo }) {
  if (template === null) {
    schemaList.set([], { fields: [] });
    inspector.show(null);
    history.set({ fields: [], history: [] }, { canUndo: false, canRedo: false });
    return "Schema";
  }
  schemaList.set(schemaPaths, template);
  const field = template.fields.find((f) => f.id === selected) ?? null;
  schemaList.setPicked(field?.source?.paths?.[0] ?? null);
  inspector.show(field, { schemaPaths, warnings: field ? (editWarnings[field.id] ?? []) : [] });
  history.set(template, { canUndo, canRedo });
  return schemaLabel(schemaPaths, template);
}
