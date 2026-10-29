// The engine interface (spec §7.1): one object per engine, every method async, capability flags
// telling the UI what to show. The browser engine and the workbench's server engine both take it.

/** The capability flags every engine declares. */
export const CAPABILITIES = Object.freeze(["detectLive", "ai", "matches", "store", "records", "scanned"]);

/** Methods every engine has. */
export const REQUIRED_METHODS = Object.freeze(["open", "seed", "apply", "fill", "smartPdf"]);
/**
 * Methods an engine may have; the UI offers them only when present. `extract` is a group: an object holding
 * every one of {@link EXTRACT_METHODS} (the server engine's extraction calls, see engines/server/client.js).
 */
export const OPTIONAL_METHODS = Object.freeze(["detect", "build", "revise", "matches", "oneshot", "readProgress", "waitForReading", "detectPage", "extract"]);

/** The methods of the optional `extract` group: batches, setups, saved extraction templates and runs. */
export const EXTRACT_METHODS = Object.freeze([
  "createBatch", "addFiles", "batch", "files", "pickSample", "pageUrl",
  "startSetup", "setup", "reopen", "edit", "learn", "marks", "samplePageUrl", "startLine",
  "save", "list", "extraction", "remove",
  "startRun", "runs", "openRun", "run", "problems", "setAside", "decide", "runPageUrl", "exportUrl", "exportRun", "toData",
]);  // prettier-ignore

/** Optional method groups: the group's name -> the methods it must hold. */
const GROUPS = { extract: EXTRACT_METHODS };

/**
 * Check that `engine` has the interface's methods (each optional one a function when present, an optional group
 * an object holding all its methods) and a `capabilities` object holding a boolean for each of
 * {@link CAPABILITIES} (and nothing else). Returns the engine.
 * @template T
 * @param {T} engine
 * @returns {T}
 * @throws {TypeError} naming the first thing missing or wrong
 */
export function assertEngine(engine) {
  if (engine === null || typeof engine !== "object") throw new TypeError("an engine is an object");
  for (const name of REQUIRED_METHODS) {
    if (typeof engine[name] !== "function") throw new TypeError(`engine.${name} must be a function`);
  }
  for (const name of OPTIONAL_METHODS) {
    const value = engine[name];
    if (value === undefined) continue;
    if (name in GROUPS) {
      if (value === null || typeof value !== "object") throw new TypeError(`engine.${name} must be an object of methods when present`);
      for (const method of GROUPS[name]) {
        if (typeof value[method] !== "function") throw new TypeError(`engine.${name}.${method} must be a function`);
      }
    } else if (typeof value !== "function") {
      throw new TypeError(`engine.${name} must be a function when present`);
    }
  }
  const caps = engine.capabilities;
  if (caps === null || typeof caps !== "object" || Array.isArray(caps)) {
    throw new TypeError("engine.capabilities must be an object of booleans");
  }
  for (const name of CAPABILITIES) {
    if (typeof caps[name] !== "boolean") throw new TypeError(`engine.capabilities.${name} must be a boolean`);
  }
  for (const name of Object.keys(caps)) {
    if (!CAPABILITIES.includes(name)) throw new TypeError(`unknown capability ${name}; have ${CAPABILITIES.join(", ")}`);
  }
  return engine;
}
