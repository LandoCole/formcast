// core/engine.js: the engine interface check.
import assert from "node:assert/strict";
import { test } from "node:test";

import { CAPABILITIES, EXTRACT_METHODS, OPTIONAL_METHODS, assertEngine } from "../../demo/web/core/engine.js";

function engine(extra = {}) {
  const fn = async () => {};
  return {
    capabilities: { detectLive: true, ai: false, matches: false, store: false, records: false, scanned: false },
    open: fn, seed: fn, apply: fn, fill: fn, smartPdf: fn,
    ...extra,
  };
}

test("the capability flags are the spec's", () => {
  assert.deepEqual(CAPABILITIES, ["detectLive", "ai", "matches", "store", "records", "scanned"]);
});

test("a complete engine passes and is returned", () => {
  const e = engine({ detect: async () => [] });
  assert.equal(assertEngine(e), e);
});

test("a missing required method is named", () => {
  for (const name of ["open", "seed", "apply", "fill", "smartPdf"]) {
    assert.throws(() => assertEngine(engine({ [name]: undefined })), new TypeError(`engine.${name} must be a function`));
  }
});

test("optional methods must be functions when present", () => {
  assert.throws(() => assertEngine(engine({ revise: "soon" })), /engine.revise must be a function when present/);
  assertEngine(engine({ build: undefined }));
});

test("extract, when present, is an object holding every extraction method", () => {
  const fn = async () => {};
  const extract = Object.fromEntries(EXTRACT_METHODS.map((name) => [name, fn]));
  assertEngine(engine({ extract }));
  assertEngine(engine({ extract: undefined }));
  assert.ok(OPTIONAL_METHODS.includes("extract") && EXTRACT_METHODS.includes("addFiles") && EXTRACT_METHODS.includes("toData"));
  assert.throws(() => assertEngine(engine({ extract: fn })), /engine.extract must be an object of methods when present/);
  assert.throws(() => assertEngine(engine({ extract: { ...extract, addFiles: undefined } })), /engine.extract.addFiles must be a function/);
});

test("capabilities must hold a boolean for each flag and nothing else", () => {
  assert.throws(() => assertEngine(engine({ capabilities: null })), /object of booleans/);
  assert.throws(() => assertEngine(engine({ capabilities: { ...engine().capabilities, ai: "no" } })), /capabilities.ai must be a boolean/);
  const partial = engine().capabilities;
  delete partial.scanned;
  assert.throws(() => assertEngine(engine({ capabilities: partial })), /capabilities.scanned/);
  assert.throws(() => assertEngine(engine({ capabilities: { ...engine().capabilities, teleport: true } })), /unknown capability teleport/);
  assert.throws(() => assertEngine(null), TypeError);
});
