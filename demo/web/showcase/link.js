// The Link step: Auto map (the AI agent's mapping for a sample, carried onto the boxes by
// position; automap.js), the schema (the sample's, or one the visitor pastes), the schema list
// with placement status, the inspector for the selected box, and undo and redo.
//
// Auto map is honest about what it is: the samples are pre-mapped, and the note under the button
// says so and names the model that built the mapping (from agent-scores.json). It is offered only
// for a sample whose template still has the sample's schema; a visitor's own PDF or pasted schema
// has no prepared mapping. Its result is one undo step, and everything stays editable by hand.

import { Inspector } from "../core/inspector.js";
import { showLinkPanels } from "../core/panels.js";
import { flattenSchema, placeholderRecord } from "../core/schema.js";
import { SchemaList } from "../core/schemalist.js";
import { carryMapping } from "./automap.js";
import { fetchWithin } from "./limits.js";

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** @param {object} app see showcase.js */
export function mountLink(app) {
  const { $, bus, session, state, scan, say } = app;
  const schemaList = new SchemaList($("schemalist"), { bus, draggable: !app.touch });
  const inspector = new Inspector($("inspector"), { bus });
  const error = $("schema-error");
  let mapping = null; // the Auto map in progress: {token} of the document it runs on
  let mapped = null; // what the last Auto map did on this document, or why it failed: the readout

  $("link-hint").textContent = app.touch
    ? "Tap a box on the page, then pick its data field below."
    : "Drag a field onto a box, or select a box and pick its data field below.";

  /** Swap the template's schema (undoable). Boxes keep their links, flagged when a path is gone. */
  async function useSchema(schema, records, opts) {
    if (app.template === null) return;
    // built from the template when its turn comes, so edits still in flight are kept
    const res = await session.replaceTemplate((t) => ({ ...structuredClone(t), schema: structuredClone(schema) }));
    if (res.error === undefined) app.setRecords(records, opts);
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
    useSchema(schema, [{ title: "A made-up record", record }], { placeholder: true });
  });

  $("sample-schema").addEventListener("click", () => {
    say(error, "");
    if (state.sample) useSchema(state.sample.schema, state.sample.records);
  });

  $("undo").addEventListener("click", () => session.undo());
  $("redo").addEventListener("click", () => session.redo());
  let ownReplace = false; // the next "replace" is Auto map's own
  bus.on("template", ({ reason }) => {
    if (reason === "replace" && ownReplace) {
      ownReplace = false;
      return;
    }
    // an undo, a redo or a template from elsewhere (Detect again, a schema): the readout may no longer be true
    if (mapped === null || reason === "apply") return;
    mapped = null;
    app.render();
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

  // ---------------------------------------------------------------- Auto map

  /** Why Auto map can't run on what is on screen, or "" when it can. */
  function blocked() {
    const t = app.template;
    if (t === null) return "Open a form first.";
    if (state.sample === null) {
      return "Auto map is for the sample forms: there is no prepared mapping for your PDF. In the full library the agent maps any form for you.";
    }
    if (JSON.stringify(t.schema) !== JSON.stringify(state.sample.schema)) {
      return "Auto map uses the sample's schema: there is no prepared mapping for yours. In the full library the agent maps to any schema for you.";
    }
    if (t.fields.length === 0) return "Detect the boxes first; Auto map links them.";
    return "";
  }

  /** The agent's template for the sample (fetched on first use). */
  async function agentTemplate(sample) {
    sample.agent ??= fetchWithin(new URL("agent-template.json", sample.base), app.fetchTimeoutMs, (res) => res.json()).catch((e) => {
      sample.agent = null;
      throw e;
    });
    return sample.agent;
  }

  async function autoMap() {
    if (mapping !== null || blocked()) return;
    const mine = { token: session.generation };
    mapping = mine;
    mapped = null;
    $("automap-status").textContent = "Mapping…";
    app.render();
    const failed = (message) => {
      mapped = { token: mine.token, text: `Auto map didn't finish: ${message}` };
    };
    try {
      const agent = await agentTemplate(state.sample);
      if (mine.token !== session.generation) return;
      let result = null;
      const res = await session.replaceTemplate((t) => {
        result = carryMapping(t, agent);
        ownReplace = true; // the template event comes right after this returns
        return result.template;
      });
      if (res.stale || mine.token !== session.generation) return;
      if (res.error !== undefined) {
        ownReplace = false;
        failed(res.error);
        return;
      }
      // the boxes are redrawn before the browser paints, so the links land in turn from not linked
      scan.land(result.mapped);
      app.render();
      const { length: n } = result.mapped;
      const rest = n < result.boxes ? " The rest are yours to link by hand." : "";
      mapped = { token: mine.token, text: `Mapped ${n} of ${plural(result.boxes, "box", "boxes")}.${rest}` };
    } catch (e) {
      failed(e.message);
    } finally {
      if (mapping === mine) mapping = null;
      app.render();
    }
  }

  $("automap").addEventListener("click", () => autoMap());

  // ---------------------------------------------------------------- render

  app.onRender(() => {
    const reason = blocked();
    const busy = mapping !== null && mapping.token === session.generation;
    $("automap").disabled = reason !== "" || busy;
    const sample = state.sample;
    const model = sample?.scores?.model_label ?? sample?.scores?.model ?? "an AI model";
    $("automap-note").textContent = sample === null
      ? ""
      : `These sample forms are pre-mapped. The mapping was built by the AI agent (${model}) in an earlier, text-only formcast ` +
        "eval run and is matched to your boxes by position. In the full library the model maps any form for you from images of its pages.";
    $("automap-note").hidden = sample === null;
    if (!busy) {
      const done = mapped !== null && mapped.token === session.generation ? mapped.text : "";
      // why Auto map is off wins over an old readout (say, after pasting a schema)
      $("automap-status").textContent = reason || done;
    }
    $("sample-schema").hidden = sample === null;
    $("undo").disabled = !session.canUndo;
    $("redo").disabled = !session.canRedo;
    $("schema-label").textContent = showLinkPanels({ schemaList, inspector }, {
      template: app.template,
      schemaPaths: app.schemaPaths(),
      selected: state.selected,
      editWarnings: session.fieldWarnings,
    });
  });
}
