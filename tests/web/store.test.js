// store.js: does a template belong to this PDF.
import assert from "node:assert/strict";
import { test } from "node:test";

import { checkTemplateFits } from "../../demo/web/core/store.js";

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
