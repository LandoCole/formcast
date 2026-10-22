// Holds the browser fill (core/fill/) to the answers recorded from Python's fill
// (fixtures/fill.json): per field the binding, widget and value, per table cell the value, and the
// report's warning codes with their counts (the codes the browser can produce). Each output PDF is
// re-opened with pdf-lib to check the values really are in it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  PDFCheckBox,
  PDFDocument,
  PDFName,
  PDFTextField,
} from "../../demo/web/vendor/pdf-lib/pdf-lib.esm.min.js";
import { fromUserSpace, iou } from "../../demo/web/core/geometry.js";
import { cellKey, widgetNameFor } from "../../demo/web/core/fill/bind.js";
import { fitText, makeMeasure, unencodableChars } from "../../demo/web/core/fill/fit.js";
import { fill, smartPdf } from "../../demo/web/core/fill/index.js";

const ROOT = new URL("../../", import.meta.url);
const fixture = JSON.parse(readFileSync(new URL("./fixtures/fill.json", import.meta.url), "utf8"));

/** The template a case names: stored in the fixture, or a committed archived copy. */
function templateFor(name) {
  const stored = fixture.templates[name];
  if (stored.template) return structuredClone(stored.template);
  return JSON.parse(readFileSync(new URL(stored.file, ROOT), "utf8"));
}

function pdfFor(path) {
  return new Uint8Array(readFileSync(new URL(path, ROOT)));
}

function codes(report) {
  const out = {};
  for (const w of report.warnings) {
    if (fixture.skipped_codes.includes(w.code)) continue;
    out[w.code] = (out[w.code] ?? 0) + 1;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** The rect a pdf-lib widget sits at, in the template's space (top-left origin, crop box). */
function widgetRect(page, widget) {
  const r = widget.getRectangle();
  const c = page.getCropBox();
  return fromUserSpace([r.x, r.y, r.x + r.width, r.y + r.height], [c.x, c.y, c.x + c.width, c.y + c.height]);
}

function onState(widget) {
  const as = widget.getAppearanceState();
  return as !== undefined && as !== PDFName.of("Off");
}

/** What the output PDF holds in the widget a report entry names: its text, or on/off. */
function heldValue(doc, entry, kind) {
  const field = doc.getForm().getField(entry.widget);
  if (kind === "choice") {
    assert.ok(field instanceof PDFCheckBox || field.constructor.name === "PDFRadioGroup", entry.widget);
    const widgets = field.acroField.getWidgets();
    if (widgets.length === 1) return onState(widgets[0]);
    // several kids share the name: the one at the reported rect
    const page = doc.getPages()[entry.page];
    const best = widgets.reduce((a, w) => (iou(widgetRect(page, w), entry.rect) > iou(widgetRect(page, a), entry.rect) ? w : a));
    return onState(best);
  }
  assert.ok(field instanceof PDFTextField, entry.widget);
  return field.getText() ?? null;
}

const CHOICE = new Set(["checkbox", "radio_option", "choice_mark"]);

for (const c of fixture.cases) {
  test(`fill ${c.id}`, async () => {
    const template = templateFor(c.input.template);
    const data = structuredClone(c.input.data);
    const { pdfBytes, report } = await fill(template, pdfFor(c.input.pdf), data);
    assert.ok(pdfBytes instanceof Uint8Array);
    assert.equal(report.writer, "browser");

    // (i) per field, (ii) per cell, (iii) codes and counts
    const fields = report.fields.map(({ id, binding, widget, value }) => ({ id, binding, widget, value }));
    assert.deepEqual(fields, c.output.fields);
    const cells = report.tables.flatMap((t) => t.cells.map(({ row, key, value }) => ({ table: t.id, row, key, value })));
    assert.deepEqual(cells, c.output.cells);
    assert.deepEqual(codes(report), c.output.codes);

    // the output really holds what the report says
    const doc = await PDFDocument.load(pdfBytes);
    for (const f of report.fields) {
      if (f.binding === "none") continue;
      const kind = CHOICE.has(f.type) ? "choice" : "text";
      const held = heldValue(doc, { widget: f.widget, page: f.page, rect: f.widget_rect }, kind);
      assert.deepEqual(held, kind === "choice" ? f.value : f.value ?? null, `${f.id} in ${f.widget}`);
    }
    for (const t of report.tables) {
      for (const cell of t.cells) {
        const held = heldValue(doc, { widget: cell.widget, page: cell.page, rect: cell.widget_rect }, "text");
        assert.equal(held, cell.value ?? null, `${t.id} row ${cell.row} ${cell.key}`);
      }
    }
  });
}

test("the fixture covers the browser codes, a table, every binding and the non-Latin records", () => {
  const seen = new Set(fixture.cases.flatMap((c) => Object.keys(c.output.codes)));
  for (const code of ["missing", "unmapped_data", "shrunk", "overflow", "truncated", "unencodable", "rows_dropped",
    "widget_not_found", "type_mismatch", "not_shown"]) {
    assert.ok(seen.has(code), `no case produces ${code}`);
  }
  const bindings = new Set(fixture.cases.flatMap((c) => c.output.fields.map((f) => f.binding)));
  assert.deepEqual([...bindings].sort(), ["adopted", "created", "named", "none"]);
  assert.ok(fixture.cases.some((c) => c.output.cells.length > 0));
  const text = JSON.stringify(fixture.cases.map((c) => c.input.data));
  for (const s of ["Sí", "José", "Zürich", "北京"]) assert.ok(text.includes(s), s);
});

test("the report has the library's shape plus the writer", async () => {
  const c = fixture.cases.find((x) => x.id === "cropbox-table/non-latin");
  const { report } = await fill(templateFor(c.input.template), pdfFor(c.input.pdf), c.input.data);
  assert.deepEqual(Object.keys(report), [
    "template_id", "fingerprint_ok", "strict", "fields", "tables", "paths", "gaps", "warnings", "counts", "writer",
  ]);
  assert.equal(report.template_id, "table_crop");
  assert.equal(report.strict, false);
  assert.deepEqual(Object.keys(report.fields[0]), [
    "id", "type", "page", "paths", "binding", "widget", "widget_rect", "value", "font_size", "warnings",
  ]);
  const table = report.tables[0];
  assert.deepEqual([table.rows_provided, table.rows_written, table.rows_dropped, table.rows_per_segment], [3, 2, 1, [2]]);
  assert.deepEqual(Object.keys(table.cells[0]), [
    "row", "key", "segment", "page", "rect", "binding", "widget", "widget_rect", "value", "font_size", "warnings",
  ]);
  assert.equal(table.cells[0].widget, "items_r0_desc");
  assert.deepEqual(report.paths["items[].desc"].rendered, { "items[0]": "José's 北京 order", "items[1]": "Zürich" });
  assert.deepEqual(report.gaps, ["unmapped"]);
  assert.equal(report.counts.fields_written, 1);
  assert.equal(report.counts.fields_empty, 2);
  assert.equal(report.counts.cells_written, 3);
  const unenc = report.warnings.filter((w) => w.code === "unencodable");
  assert.deepEqual(unenc.map((w) => w.field), ["items", "note"]); // by page, then top
  assert.match(unenc.find((w) => w.field === "items").message, /^characters '北京' are outside the standard PDF fonts/);
  assert.ok(report.warnings.every((w) => typeof w.severity === "string" && "rect" in w && "page" in w));
});

test("non-Latin text never throws and keeps the full value in the field", async () => {
  const c = fixture.cases.find((x) => x.id === "rotated90/non-latin");
  const { pdfBytes, report } = await fill(templateFor("hand"), pdfFor(c.input.pdf), c.input.data);
  const name = report.fields.find((f) => f.id === "name");
  assert.equal(name.value, "北京 Sí");
  assert.ok(name.warnings.some((w) => w.code === "unencodable" && w.message.includes("'北京'")));
  const doc = await PDFDocument.load(pdfBytes);
  assert.equal(doc.getForm().getTextField("name").getText(), "北京 Sí");
});

test("created widgets sit at the template's rects on a rotated page and under a crop box", async () => {
  for (const id of ["rotated90/probe", "cropbox/probe", "cropbox-table/probe"]) {
    const c = fixture.cases.find((x) => x.id === id);
    const template = templateFor(c.input.template);
    const { pdfBytes, report } = await fill(template, pdfFor(c.input.pdf), c.input.data);
    const doc = await PDFDocument.load(pdfBytes);
    const page = doc.getPages()[0];
    const rects = [
      ...report.fields.map((f) => [f.widget, template.fields.find((t) => t.id === f.id).rect]),
      ...report.tables.flatMap((t) => t.cells.map((cell) => [cell.widget, cell.rect])),
    ];
    for (const [name, want] of rects) {
      const widgets = doc.getForm().getField(name).acroField.getWidgets();
      assert.equal(widgets.length, 1, name);
      const got = widgetRect(page, widgets[0]);
      for (let i = 0; i < 4; i++) assert.ok(Math.abs(got[i] - want[i]) < 0.5, `${id} ${name}: ${got} vs ${want}`);
      const mk = widgets[0].getAppearanceCharacteristics();
      assert.equal(mk?.getRotation() ?? 0, page.getRotation().angle, `${id} ${name} /MK /R`);
    }
  }
});

test("a choice_mark is a checkbox whose on look is an ellipse and whose off look is empty", async () => {
  const c = fixture.cases.find((x) => x.id === "cropbox/probe");
  const { pdfBytes } = await fill(templateFor("hand_crop"), pdfFor(c.input.pdf), c.input.data);
  const doc = await PDFDocument.load(pdfBytes);
  const box = doc.getForm().getCheckBox("zurich");
  assert.ok(box.isChecked());
  const [widget] = box.acroField.getWidgets();
  const normal = widget.getNormalAppearance();
  assert.deepEqual(normal.keys().map((k) => k.decodeText()).sort(), ["Off", "Yes"]);
  const decode = (ref) => new TextDecoder().decode(doc.context.lookup(ref).getContents());
  const on = decode(normal.get(PDFName.of("Yes")));
  assert.equal((on.match(/ c\n/g) ?? []).length, 4, on);
  assert.match(on, /\nS\nQ\n$/);
  assert.equal(decode(normal.get(PDFName.of("Off"))), "");
  assert.equal(widget.getAppearanceCharacteristics()?.getCaptions?.()?.normal, undefined);
});

test("checkFingerprint: true cannot be honoured for a template with a fingerprint", async () => {
  const template = templateFor("fw9_as_published");
  await assert.rejects(
    fill(template, pdfFor("forms/dev/fw9.pdf"), {}, { checkFingerprint: true }),
    { message: "the browser cannot check fingerprints; pass checkFingerprint: false" },
  );
  // a template without one has nothing to check
  const hand = templateFor("hand");
  const { report } = await fill(hand, pdfFor("tests/web/fixtures/pdfs/rotated90.pdf"), {}, { checkFingerprint: true });
  assert.equal(report.fingerprint_ok, true);
});

test("a PDF with the wrong page count, not a PDF, or encrypted, is refused", async () => {
  await assert.rejects(fill(templateFor("hand"), pdfFor("forms/dev/fw9.pdf"), {}), {
    message: "page count mismatch: template has 1 pages, PDF has 6",
  });
  await assert.rejects(fill(templateFor("hand"), new TextEncoder().encode("not a pdf"), {}), { message: "cannot open PDF" });
  // pdf-lib cannot decrypt, even with an empty user password (PyMuPDF can)
  await assert.rejects(fill(templateFor("wadoc_auth_as_published"), pdfFor("forms/dev/wadoc_auth.pdf"), {}), {
    message: "encrypted PDF",
  });
});

test("an unknown transform is refused before anything is written", async () => {
  const template = templateFor("hand");
  template.fields[0].source.transforms = [{ op: "shout", args: {} }];
  await assert.rejects(fill(template, pdfFor("tests/web/fixtures/pdfs/rotated90.pdf"), {}), /^Error: field 'name' transforms: /);
});

test("smartPdf adds an empty field for every template field the PDF lacks", async () => {
  const wadoc = await PDFDocument.load(await smartPdf(templateFor("wadoc_auth_as_published"), pdfFor("tests/web/fixtures/pdfs/wadoc_auth_decrypted.pdf")));
  const names = wadoc.getForm().getFields().map((f) => f.getName());
  assert.equal(names.length, 27);
  assert.ok(wadoc.getForm().getFields().every((f) => !(f instanceof PDFTextField) || f.getText() === undefined));
  const fw9In = await PDFDocument.load(pdfFor("forms/dev/fw9.pdf"));
  const fw9Out = await PDFDocument.load(await smartPdf(templateFor("fw9_as_published"), pdfFor("forms/dev/fw9.pdf")));
  const before = new Set(fw9In.getForm().getFields().map((f) => f.getName()));
  const added = fw9Out.getForm().getFields().map((f) => f.getName()).filter((n) => !before.has(n));
  assert.deepEqual(added, ["f1"]);
});

test("a refill overwrites the values of the first fill and re-adopts its own widgets", async () => {
  const pdf = pdfFor("tests/web/fixtures/pdfs/cropbox.pdf");
  const template = templateFor("hand_crop");
  const first = await fill(template, pdf, { name: "First", code: "1111", answer: "Sí", city: "Zürich" });
  const second = await fill(template, first.pdfBytes, { name: "Second", answer: "No" });
  assert.deepEqual(second.report.fields.map((f) => [f.id, f.binding, f.widget, f.value]), [
    ["name", "adopted", "name", "Second"],
    ["code", "adopted", "code", null],
    ["agree", "adopted", "agree", false],
    ["zurich", "adopted", "zurich", false],
  ]);
  assert.ok(!second.report.warnings.some((w) => w.code === "adopted"));
  const doc = await PDFDocument.load(second.pdfBytes);
  assert.equal(doc.getForm().getFields().length, 4);
  assert.equal(doc.getForm().getTextField("code").getText(), undefined);
});

test("unencodableChars: what cp1252 cannot encode, distinct, in order", () => {
  assert.equal(unencodableChars("José Zürich € – “q”"), "");
  assert.equal(unencodableChars("北京 Sí 北 ā"), "北京ā");
  assert.equal(unencodableChars("\u0081\u0080x"), "\u0081\u0080");
});

test("fitText: auto size from the box height, shrink, overflow, comb truncation", async () => {
  const doc = await PDFDocument.create();
  const measure = makeMeasure(await doc.embedFont("Helvetica"));
  const auto = fitText("Ann", [0, 0, 200, 20], { fontSize: "auto", minFontSize: 6, measure });
  assert.equal(auto.status, "ok");
  assert.equal(auto.fontSize, 12);
  const small = fitText("Ann", [0, 0, 200, 10], { fontSize: "auto", minFontSize: 6, measure });
  assert.equal(small.fontSize, 8 / 1.15); // (height - 2) / line height
  const shrunk = fitText("A fairly long value", [0, 0, 60, 20], { fontSize: "auto", minFontSize: 6, measure });
  assert.deepEqual([shrunk.status, shrunk.fontSize], ["shrunk", 7]);
  const over = fitText("A very long value indeed, far too long", [0, 0, 30, 20], { fontSize: 10, minFontSize: 6, measure });
  assert.deepEqual([over.status, over.fontSize, over.text], ["overflow", 6, "A very long value indeed, far too long"]);
  const comb = fitText("123456", [0, 0, 80, 20], { fontSize: "auto", minFontSize: 6, measure, comb: 4 });
  assert.deepEqual([comb.text, comb.truncated], ["1234", true]);
  // measured as the library does: every character counts (AFM widths), no cp1252 byte reads as
  // U+00B7, a control as the missing width
  assert.equal(measure("北", 10), measure("\u00b7", 10));
  assert.equal(measure("\u0001", 10), measure(" ", 10));
  assert.equal(measure("Zürich", 1000), 611 + 556 + 333 + 222 + 500 + 556);
  assert.equal(measure("“q” – €", 1000), 333 + 556 + 333 + 278 + 556 + 278 + 556);
  assert.equal(measure("北京 Sí", 1000), measure("··", 1000) + measure(" Sí", 1000));
});

test("bind names: cells read <table>_r<row>_<column>, path punctuation becomes _", () => {
  assert.equal(widgetNameFor(cellKey("items", 2, "desc")), "items_r2_desc");
  assert.equal(widgetNameFor(cellKey("t", 0, "sched.time")), "t_r0_sched_time");
  assert.equal(widgetNameFor(cellKey("t", 1, '["dose mg"]')), 't_r1_"dose mg"');
  assert.equal(widgetNameFor("a.b[0]"), "a_b_0");
});
