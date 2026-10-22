// Builds three templates the way the browser engine will: start from the browser-style seed for
// forms/dev/fw9.pdf page 0 (no fingerprint) and apply scripted edit ops through ops.js.
// Used by test_browser_templates.py, which checks Python accepts and replays them.
//
//   node tests/web/build_templates.mjs <output.json>
import { readFileSync, writeFileSync } from "node:fs";

import { applyOp } from "../../demo/web/core/ops.js";

const seed = JSON.parse(readFileSync(new URL("./fixtures/fw9_seed.json", import.meta.url), "utf8"));
const AT = "2026-10-21T00:00:00+00:00";

/** Apply `ops` in order from the seed; every op must be accepted. */
function build(templateId, ops) {
  let template = { ...structuredClone(seed), template_id: templateId };
  const warnings = [];
  for (const op of ops) {
    const result = applyOp(template, op, { author: "human", note: `scripted ${op.op}`, at: AT });
    template = result.template;
    warnings.push(...result.warnings);
  }
  return { template, warnings };
}

const rect = (x0, y0, x1, y1) => [x0, y0, x1, y1];

const scripts = {
  // names, a new field, a checkbox with a condition
  names: [
    { op: "set_source", id: "w0_0", source: { paths: ["legal_name"], transforms: [{ op: "strip" }] } },
    { op: "set_source", id: "w0_1", source: { paths: ["business_name"] } },
    { op: "add_field", type: "text", page: 0, rect: rect(58.6, 360, 300, 374), label: "Remit email", source: { paths: ["remit_email"] } },
    { op: "move", id: "f1", dx: 4, dy: 2.5 },
    { op: "resize", id: "f1", rect: rect(62.6, 362.5, 320, 376.5) },
    { op: "set_label", id: "w0_0", label: "Name" },
    { op: "set_type", id: "w0_2", type: "checkbox" },
    {
      op: "set_source",
      id: "w0_2",
      source: { paths: ["tax_classification"], when: { op: "equals", args: { value: "individual_sole_proprietor" } } },
    },
    { op: "set_style", id: "w0_14", style: { font_size: 9, align: "left" } },
    { op: "remove_field", id: "w0_17" },
  ],
  // the tax id split over comb boxes, with an explicit id and a signature
  taxid: [
    { op: "set_source", id: "w0_18", source: { paths: ["ssn"], transforms: [{ op: "split", args: { sep: "-", index: 0 } }] } },
    { op: "set_source", id: "w0_19", source: { paths: ["ssn"], transforms: [{ op: "split", args: { sep: "-", index: 1 } }] } },
    { op: "set_source", id: "w0_20", source: { paths: ["ssn"], transforms: [{ op: "split", args: { sep: "-", index: 2 } }] } },
    { op: "set_style", id: "w0_18", style: { comb: 3, align: "center" } },
    { op: "set_style", id: "w0_20", style: { comb: 4 } },
    { op: "add_field", id: "sign-here", type: "signature", page: 0, rect: rect(100, 600, 400, 640), label: "Signature" },
    { op: "move", id: "sign-here", rect: rect(100, 610, 400, 650) },
    { op: "resize", id: "w0_21", rect: rect(417.6, 420, 450, 444) },
    { op: "set_type", id: "w0_22", type: "signature" },
    { op: "remove_field", id: "w0_22" },
  ],
  // choice boxes mapped by condition, a date taken apart, a boolean
  choices: [
    { op: "set_source", id: "w0_3", source: { paths: ["tax_classification"], when: { op: "equals", args: { value: "c_corporation" } } } },
    { op: "set_source", id: "w0_4", source: { paths: ["tax_classification"], when: { op: "equals", args: { value: "s_corporation" } } } },
    { op: "set_source", id: "w0_11", source: { paths: ["has_foreign_partners"], transforms: [{ op: "bool_to_check" }] } },
    { op: "add_field", type: "text", page: 0, rect: rect(100, 500, 140, 514), source: { paths: ["certified_on"], transforms: [{ op: "date_part", args: { part: "month", pad: 2 } }] } },
    { op: "add_field", type: "text", page: 0, rect: rect(150, 500, 190, 514), source: { paths: ["certified_on"], transforms: [{ op: "date_part", args: { part: "day", pad: 2 } }] } },
    { op: "move", id: "f2", dx: 3 },
    { op: "resize", id: "f1", rect: rect(100, 498, 142, 516) },
    { op: "set_type", id: "w0_5", type: "radio_option" },
    { op: "set_label", id: "w0_5", label: "Trust or estate" },
    { op: "remove_field", id: "w0_6" },
  ],
};

const out = Object.fromEntries(Object.entries(scripts).map(([name, ops]) => [name, build(`fw9-${name}`, ops).template]));
const target = process.argv[2];
if (!target) {
  console.error("usage: node tests/web/build_templates.mjs <output.json>");
  process.exit(2);
}
writeFileSync(target, JSON.stringify(out, null, 1));
