// store.js: does a template belong to this PDF, reading template files, and remembering without storage.
import assert from "node:assert/strict";
import { test } from "node:test";

import { checkTemplate, checkTemplateFits, readTemplateFile, recall, remember } from "../../demo/web/core/store.js";

const template = (pageSizes) => ({
  formcast_version: "1",
  template_id: "t",
  schema: {},
  form: { fingerprint: null, page_count: pageSizes.length, page_sizes: pageSizes, kinds: pageSizes.map(() => "digital") },
  fields: [],
  history: [],
});
const doc = (sizes) => ({ pageCount: sizes.length, pages: sizes.map(([width, height]) => ({ width, height })) });

test("checkTemplateFits is null for a match, within a point", () => {
  assert.equal(checkTemplateFits(template([[612, 792], [612, 792]]), doc([[612, 792], [612, 792]])), null);
  assert.equal(checkTemplateFits(template([[611.976, 791.968]]), doc([[612, 792]])), null);
  assert.equal(checkTemplateFits(template([[612, 792]]), doc([[612.9, 791.2]])), null);
});

test("checkTemplateFits names a page-count mismatch", () => {
  assert.equal(
    checkTemplateFits(template([[612, 792], [612, 792]]), doc(Array(6).fill([612, 792]))),
    "This template is for a 2-page form; this PDF has 6 pages.",
  );
  assert.equal(
    checkTemplateFits(template([[612, 792], [612, 792]]), doc([[612, 792]])),
    "This template is for a 2-page form; this PDF has 1 page.",
  );
});

test("checkTemplateFits names a page-size mismatch", () => {
  assert.equal(
    checkTemplateFits(template([[612, 792], [612, 792]]), doc([[612, 792], [595.3, 841.9]])),
    "This template expects page 2 to be 612 x 792 pt; this PDF's page 2 is 595.3 x 841.9 pt.",
  );
  assert.match(checkTemplateFits(template([[612, 792]]), doc([[612, 794]])), /page 1/);
});

test("readTemplateFile returns the template, and refuses what is not one", async () => {
  const t = template([[612, 792]]);
  assert.deepEqual(await readTemplateFile(new Blob([JSON.stringify(t)])), t);
  await assert.rejects(readTemplateFile(new Blob(["{nope"])), /not valid JSON/);
  await assert.rejects(readTemplateFile(new Blob(["[1]"])), /not a formcast template/);
  await assert.rejects(readTemplateFile(new Blob([JSON.stringify({ formcast_version: "2" })])), /version "1"/);
  await assert.rejects(readTemplateFile(new Blob([JSON.stringify({ formcast_version: "1", fields: [] })])), /not a complete template/);
  const noCount = structuredClone(t);
  delete noCount.form.page_count;
  await assert.rejects(readTemplateFile(new Blob([JSON.stringify(noCount)])), /not a complete template/);
});

test("checkTemplate checks each field's shape, and a table's segments minimally", () => {
  const plain = { id: "a", type: "text", page: 0, rect: [10, 20, 110, 34] };
  const seg = { page: 0, rect: [10, 100, 300, 160], row_top: 100, row_height: 20, max_rows: 3, columns: [{ key: "n", x: [10, 300] }] };
  const table = { id: "t", type: "table", page: null, rect: null, segments: [seg] };
  const withFields = (fields) => ({ ...template([[612, 792]]), fields });
  assert.equal(checkTemplate(withFields([plain, table])).fields.length, 2);

  const bad = [
    [1],
    [null],
    [[plain]],
    [{ ...plain, id: 7 }],
    [{ ...plain, id: undefined }],
    [{ ...plain, type: 3 }],
    [{ ...plain, page: 0.5 }],
    [{ ...plain, page: "0" }],
    [{ ...plain, page: null }],
    [{ ...plain, rect: [1, 2, 3] }],
    [{ ...plain, rect: [1, 2, 3, "4"] }],
    [{ ...plain, rect: [1, 2, 3, NaN] }],
    [{ ...plain, rect: null }],
    [{ ...table, segments: [] }],
    [{ ...table, segments: "x" }],
    [{ ...table, segments: [{ ...seg, page: "0" }] }],
    [{ ...table, segments: [{ ...seg, rect: [1, 2] }] }],
    [{ ...table, segments: [{ ...seg, max_rows: 1.5 }] }],
    [{ ...table, segments: [{ ...seg, row_height: "20" }] }],
    [{ ...table, segments: [{ ...seg, columns: {} }] }],
    [{ ...table, segments: [{ ...seg, columns: [{ key: 1, x: [10, 300] }] }] }],
    [{ ...table, segments: [{ ...seg, columns: [{ key: "n", x: [10] }] }] }],
  ];
  for (const fields of bad) {
    assert.throws(() => checkTemplate(withFields(fields)), /not a complete template: field 1 /, JSON.stringify(fields));
  }
  assert.throws(
    () => checkTemplate(withFields([plain, { ...plain, id: "b", rect: [1, 2, 3] }])),
    { message: 'This file is not a complete template: field 2 ("b") needs a page number and a rect of four numbers.' },
  );
});

test("remember and recall round-trip through localStorage", () => {
  const data = new Map();
  globalThis.localStorage = {
    setItem: (k, v) => data.set(k, v),
    getItem: (k) => (data.has(k) ? data.get(k) : null),
  };
  try {
    const t = template([[612, 792]]);
    assert.equal(recall("w9"), null);
    assert.equal(remember("w9", t), true);
    assert.deepEqual(recall("w9"), t);
    data.set([...data.keys()][0], "{broken");
    assert.equal(recall("w9"), null);
  } finally {
    delete globalThis.localStorage;
  }
});

test("recall returns null and remember false when localStorage throws or is missing", () => {
  const throwing = () => {
    throw new Error("blocked");
  };
  globalThis.localStorage = { getItem: throwing, setItem: throwing };
  try {
    assert.equal(recall("w9"), null);
    assert.equal(remember("w9", {}), false);
  } finally {
    delete globalThis.localStorage;
  }
  assert.equal(recall("w9"), null); // no localStorage at all
  assert.equal(remember("w9", {}), false);
});
