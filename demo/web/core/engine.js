// The engine interface (spec §7.1): one object per engine, every method async, capability flags
// telling the UI what to show. The browser engine and the workbench's server engine both take it.

/** The capability flags every engine declares. */
export const CAPABILITIES = Object.freeze(["detectLive", "ai", "matches", "store", "records", "scanned"]);

/** Methods every engine has. */
export const REQUIRED_METHODS = Object.freeze(["open", "seed", "apply", "fill", "smartPdf"]);
/** Methods an engine may have; the UI offers them only when present. */
export const OPTIONAL_METHODS = Object.freeze(["detect", "build", "revise", "matches"]);

/**
 * Check that `engine` has the interface's methods and a `capabilities` object holding a boolean
 * for each of {@link CAPABILITIES} (and nothing else). Returns the engine.
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
    if (name in engine && engine[name] !== undefined && typeof engine[name] !== "function") {
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
