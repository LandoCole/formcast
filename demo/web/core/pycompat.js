// Python behaviours the conformance ports need, written out explicitly.
//
// transforms.js and schema.js are ports of Python code and must give Python's answers, including the
// text of its error messages. These helpers cover the places where JavaScript's own string, number and
// equality rules differ: repr(), str(), truthiness, ==, str.strip(), str.isspace(), json.dumps(),
// urllib.parse.unquote() and code-point string length.
//
// JSON cannot tell the float 2.0 from the integer 2, so a JavaScript number with a whole value
// below 1e16 is treated as an int and anything else as a float (see pyNumRepr).

/** Characters Python's str.isspace() accepts (JavaScript's \s differs: it has U+FEFF, lacks U+001C..001F and U+0085). */
export const PY_SPACE_CLASS = "\\t-\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";

const SPACE_RE = new RegExp(`^[${PY_SPACE_CLASS}]$`);
const STRIP_RE = new RegExp(`^[${PY_SPACE_CLASS}]+|[${PY_SPACE_CLASS}]+$`, "g");
const NOT_PRINTABLE = /^[\p{C}\p{Z}]$/u;
const DECIMAL_DIGIT = /^\p{Nd}$/u;

/**
 * True for a plain JSON object (not null, not an array).
 * @param {unknown} v
 * @returns {boolean}
 */
export function isDict(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/**
 * Own-property test, so keys like "constructor" or "__proto__" in pasted JSON behave like dict keys.
 * @param {object} obj
 * @param {string} key
 * @returns {boolean}
 */
export function hasKey(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

/**
 * Split a string into code points (Python indexes and measures strings this way).
 * @param {string} s
 * @returns {string[]}
 */
export function codePoints(s) {
  return Array.from(s);
}

/**
 * Compare two strings by code point, as Python's `<` and sorted() do.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
export function compareCodePoints(a, b) {
  const x = codePoints(a);
  const y = codePoints(b);
  const n = Math.min(x.length, y.length);
  for (let i = 0; i < n; i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

/**
 * Python's `ch.isspace()` for one character.
 * @param {string} ch
 * @returns {boolean}
 */
export function pyIsSpace(ch) {
  return SPACE_RE.test(ch);
}

/**
 * Python's `s.strip()` (no argument).
 * @param {string} s
 * @returns {string}
 */
export function pyStrip(s) {
  return s.replace(STRIP_RE, "");
}

/**
 * True when Python's `bool(v)` would be true for this JSON value.
 * @param {unknown} v
 * @returns {boolean}
 */
export function pyTruthy(v) {
  if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (isDict(v)) return Object.keys(v).length > 0;
  return true;
}

/**
 * Python's type name for a JSON value (`int` for whole numbers below 1e16, else `float`).
 * @param {unknown} v
 * @returns {string}
 */
export function pyTypeName(v) {
  if (v === null || v === undefined) return "NoneType";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return isPyInt(v) ? "int" : "float";
  if (typeof v === "string") return "str";
  if (Array.isArray(v)) return "list";
  return "dict";
}

function isPyInt(n) {
  return Number.isInteger(n) && Math.abs(n) < 1e16;
}

/**
 * Python's `repr(float)`: shortest round-trip digits, exponent form below 1e-4 and from 1e16.
 * @param {number} x
 * @returns {string}
 */
export function pyFloatRepr(x) {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x < 0 ? "-inf" : "inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const [mantissa, expText] = Math.abs(x).toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const exp = parseInt(expText, 10);
  const decpt = exp + 1;
  let out;
  if (decpt > -4 && decpt <= 16) {
    if (decpt <= 0) out = `0.${"0".repeat(-decpt)}${digits}`;
    else if (digits.length <= decpt) out = `${digits}${"0".repeat(decpt - digits.length)}.0`;
    else out = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  } else {
    const tail = digits.length > 1 ? `.${digits.slice(1)}` : "";
    out = `${digits[0]}${tail}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  }
  return x < 0 ? `-${out}` : out;
}

/**
 * Python's `repr()` of a JSON number (see the note at the top about ints and floats).
 * @param {number} n
 * @returns {string}
 */
export function pyNumRepr(n) {
  if (isPyInt(n)) return String(n === 0 ? 0 : n);
  return pyFloatRepr(n);
}

/**
 * Python's `repr(str)`: single quotes unless the text has ' and no ", escapes for control and
 * non-printable characters.
 * @param {string} s
 * @returns {string}
 */
export function pyStrRepr(s) {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === quote || ch === "\\") out += `\\${ch}`;
    else if (ch === "\t") out += "\\t";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (cp < 0x20 || cp === 0x7f) out += `\\x${cp.toString(16).padStart(2, "0")}`;
    else if (cp < 0x7f) out += ch;
    else if (NOT_PRINTABLE.test(ch)) {
      if (cp < 0x100) out += `\\x${cp.toString(16).padStart(2, "0")}`;
      else if (cp < 0x10000) out += `\\u${cp.toString(16).padStart(4, "0")}`;
      else out += `\\U${cp.toString(16).padStart(8, "0")}`;
    } else out += ch;
  }
  return out + quote;
}

/**
 * Python's `repr()` of a JSON value.
 * @param {unknown} v
 * @returns {string}
 */
export function pyRepr(v) {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return pyNumRepr(v);
  if (typeof v === "string") return pyStrRepr(v);
  if (Array.isArray(v)) return `[${v.map(pyRepr).join(", ")}]`;
  return `{${Object.entries(v).map(([k, x]) => `${pyStrRepr(k)}: ${pyRepr(x)}`).join(", ")}}`;
}

/**
 * Python's `str()` of a scalar JSON value (strings come back unchanged).
 * @param {unknown} v
 * @returns {string}
 */
export function pyStr(v) {
  return typeof v === "string" ? v : pyRepr(v);
}

/**
 * Python's `==` between two JSON values: `1 == True`, `1 == 1.0`, lists and dicts compare by content.
 * @param {unknown} a
 * @param {unknown} b
 * @returns {boolean}
 */
export function pyEquals(a, b) {
  const numeric = (v) => typeof v === "number" || typeof v === "boolean";
  if (numeric(a) && numeric(b)) return Number(a) === Number(b);
  if (a === null || b === null) return a === b;
  if (typeof a === "string" || typeof b === "string") return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => pyEquals(x, b[i]));
  }
  if (isDict(a) && isDict(b)) {
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every((k) => hasKey(b, k) && pyEquals(a[k], b[k]));
  }
  return false;
}

function jsonString(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    const code = s.charCodeAt(i);
    if (c === '"') out += '\\"';
    else if (c === "\\") out += "\\\\";
    else if (c === "\n") out += "\\n";
    else if (c === "\r") out += "\\r";
    else if (c === "\t") out += "\\t";
    else if (c === "\b") out += "\\b";
    else if (c === "\f") out += "\\f";
    else if (code >= 0x20 && code <= 0x7e) out += c;
    else out += `\\u${code.toString(16).padStart(4, "0")}`;
  }
  return `${out}"`;
}

/**
 * Python's `json.dumps(v)` with default arguments (", " and ": " separators, ASCII-only output).
 * @param {unknown} v
 * @returns {string}
 */
export function pyJsonDumps(v) {
  if (v === null || v === undefined) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") return pyNumRepr(v);
  if (typeof v === "string") return jsonString(v);
  if (Array.isArray(v)) return `[${v.map(pyJsonDumps).join(", ")}]`;
  return `{${Object.entries(v).map(([k, x]) => `${jsonString(k)}: ${pyJsonDumps(x)}`).join(", ")}}`;
}

/**
 * Python's `urllib.parse.unquote()`: percent escapes decoded as UTF-8, bad bytes replaced.
 * @param {string} s
 * @returns {string}
 */
export function pyUnquote(s) {
  if (!s.includes("%")) return s;
  return s.replace(/(?:%[0-9a-fA-F]{2})+/g, (run) => {
    const bytes = new Uint8Array(run.length / 3);
    for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(run.slice(i * 3 + 1, i * 3 + 3), 16);
    return new TextDecoder("utf-8").decode(bytes);
  });
}

/**
 * Value (0 to 9) of a Unicode decimal digit such as "٣", or -1 when `ch` is not a decimal digit.
 * Python's `\d` and `Decimal()` accept these.
 * @param {string} ch
 * @returns {number}
 */
export function decimalDigitValue(ch) {
  if (!DECIMAL_DIGIT.test(ch)) return -1;
  let cp = ch.codePointAt(0);
  let offset = 0;
  while (DECIMAL_DIGIT.test(String.fromCodePoint(cp - 1))) {
    cp -= 1;
    offset += 1;
  }
  return offset % 10;
}

/**
 * Python's `format(x, "g")` (also what an f-string `{x:g}` gives): six significant digits,
 * trailing zeros dropped, exponent form below 1e-4 and from 1e6.
 * @param {number} x
 * @returns {string}
 */
export function pyFormatG(x) {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x < 0 ? "-inf" : "inf";
  if (x === 0) return Object.is(x, -0) ? "-0" : "0";
  const sign = x < 0 ? "-" : "";
  const [mantissa, expText] = Math.abs(x).toExponential(5).split("e");
  const exp = parseInt(expText, 10);
  const trim = (s) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
  if (exp < -4 || exp >= 6) {
    return `${sign}${trim(mantissa)}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  }
  return sign + trim(Math.abs(x).toFixed(5 - exp));
}

/**
 * Python's `format(x, ".<digits>f")`: like toFixed, but an exact tie rounds to even (JavaScript
 * rounds it up), so 0.125 gives "0.12".
 * @param {number} x
 * @param {number} digits
 * @returns {string}
 */
export function pyFixed(x, digits) {
  if (!Number.isFinite(x)) return pyFormatG(x);
  const half = Math.abs(x) * 2 ** (digits + 1); // an exact tie has exactly digits + 1 binary decimals
  if (Number.isInteger(half) && half % 2 === 1) {
    const lower = Math.floor(Math.abs(x) * 10 ** digits);
    const even = lower % 2 === 0 ? lower : lower + 1;
    return (x < 0 ? "-" : "") + (even / 10 ** digits).toFixed(digits);
  }
  return x.toFixed(digits);
}

/**
 * Python's `round(x, 1)` for a float.
 * @param {number} x
 * @returns {number}
 */
export function pyRound1(x) {
  return Number(pyFixed(x, 1));
}

/**
 * Python's `a // b` for floats (it can differ from Math.floor(a / b): 1.0 // 0.1 is 9.0).
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
export function pyFloorDiv(a, b) {
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod !== 0 && b < 0 !== mod < 0) div -= 1;
  if (div === 0) return Math.sign(a / b) < 0 || Object.is(a / b, -0) ? -0 : 0;
  const floored = Math.floor(div);
  return div - floored > 0.5 ? floored + 1 : floored;
}

/**
 * Python's `difflib.SequenceMatcher(None, a, b).ratio()` for two strings (compared by code point).
 * @param {string[]} a code points of the first string
 * @param {string[]} b code points of the second string
 * @returns {number}
 */
function sequenceRatio(a, b) {
  const b2j = new Map();
  b.forEach((ch, j) => {
    if (!b2j.has(ch)) b2j.set(ch, []);
    b2j.get(ch).push(j);
  });
  if (b.length >= 200) {
    const ntest = Math.floor(b.length / 100) + 1; // autojunk drops very common elements
    for (const [ch, idxs] of [...b2j]) if (idxs.length > ntest) b2j.delete(ch);
  }

  const longest = (alo, ahi, blo, bhi) => {
    let besti = alo;
    let bestj = blo;
    let bestsize = 0;
    let j2len = new Map();
    for (let i = alo; i < ahi; i++) {
      const next = new Map();
      for (const j of b2j.get(a[i]) ?? []) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) ?? 0) + 1;
        next.set(j, k);
        if (k > bestsize) [besti, bestj, bestsize] = [i - k + 1, j - k + 1, k];
      }
      j2len = next;
    }
    while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) [besti, bestj, bestsize] = [besti - 1, bestj - 1, bestsize + 1];
    while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) bestsize += 1;
    return [besti, bestj, bestsize];
  };

  let matches = 0;
  const queue = [[0, a.length, 0, b.length]];
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop();
    const [i, j, k] = longest(alo, ahi, blo, bhi);
    if (k) {
      matches += k;
      if (alo < i && blo < j) queue.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
    }
  }
  const length = a.length + b.length;
  return length ? (2 * matches) / length : 1;
}

/**
 * Python's `difflib.get_close_matches(word, possibilities, n, cutoff)`: the best matches, most
 * similar first (equal scores: the later string first, as Python's tuple ordering does).
 * @param {string} word
 * @param {string[]} possibilities
 * @param {number} [n]
 * @param {number} [cutoff]
 * @returns {string[]}
 */
export function getCloseMatches(word, possibilities, n = 3, cutoff = 0.6) {
  const w = codePoints(word);
  const scored = [];
  for (const x of possibilities) {
    const ratio = sequenceRatio(codePoints(x), w);
    if (ratio >= cutoff) scored.push([ratio, x]);
  }
  scored.sort((p, q) => q[0] - p[0] || compareCodePoints(q[1], p[1]));
  return scored.slice(0, n).map((p) => p[1]);
}
