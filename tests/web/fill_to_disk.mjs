// Runs the browser fill on fixture cases and writes each output PDF and report to disk, so
// test_browser_fill.py can read them back with PyMuPDF.
//
//   node tests/web/fill_to_disk.mjs <output dir> <case id>...
//
// Writes <output dir>/<n>.pdf and <n>.json (the report) for the n-th case id, in argument order.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { fill } from "../../demo/web/core/fill/index.js";

const ROOT = new URL("../../", import.meta.url);
const fixture = JSON.parse(readFileSync(new URL("./fixtures/fill.json", import.meta.url), "utf8"));

function templateFor(name) {
  const stored = fixture.templates[name];
  return stored.template ?? JSON.parse(readFileSync(new URL(stored.file, ROOT), "utf8"));
}

const [outDir, ...ids] = process.argv.slice(2);
if (!outDir || !ids.length) {
  console.error("usage: node tests/web/fill_to_disk.mjs <output dir> <case id>...");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });
for (const [n, id] of ids.entries()) {
  const c = fixture.cases.find((x) => x.id === id);
  if (!c) throw new Error(`no fill case ${JSON.stringify(id)}`);
  const pdf = new Uint8Array(readFileSync(new URL(c.input.pdf, ROOT)));
  const { pdfBytes, report } = await fill(templateFor(c.input.template), pdf, c.input.data);
  writeFileSync(join(outDir, `${n}.pdf`), pdfBytes);
  writeFileSync(join(outDir, `${n}.json`), JSON.stringify(report, null, 1));
}
