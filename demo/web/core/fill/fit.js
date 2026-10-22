// Text fitting: a port of src/formcast/render/fit.py (fill design §5.5). Choose a font size (and
// wrapping) for a value in a box. Pure: widths come from a `measure(text, size)` function, which
// `makeMeasure` builds from a pdf-lib standard font.
//
// Over-long text is never cut here, except where a hard character limit forces it (`maxLen` /
// `comb`); it is kept whole at `minFontSize` and reported as overflow.

import { PY_SPACE_CLASS, codePoints } from "../pycompat.js";

export const AUTO_MAX_SIZE = 12.0;
export const SHRINK_STEP = 0.5;
export const PAD = 2.0; // horizontal padding per side
export const LINE_HEIGHT = 1.15; // multiple of font size
const VPAD = 2.0; // total vertical padding

// cp1252 bytes 0x80-0x9F: the characters Windows-1252 puts there (five are unassigned).
const CP1252_EXTRA = new Set(
  Array.from("€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ", (ch) => ch.codePointAt(0)),
);

/**
 * Whether Python's `cp1252` codec can encode the character with this code point.
 * @param {number} cp
 * @returns {boolean}
 */
export function cp1252Encodable(cp) {
  return cp < 0x80 || (cp >= 0xa0 && cp <= 0xff) || CP1252_EXTRA.has(cp);
}

/**
 * Distinct characters of `text` that cp1252 cannot encode, in order of first appearance.
 * @param {string} text
 * @returns {string}
 */
export function unencodableChars(text) {
  const out = [];
  for (const ch of codePoints(text)) {
    if (!out.includes(ch) && !cp1252Encodable(ch.codePointAt(0))) out.push(ch);
  }
  return out.join("");
}

/** cp1252 byte of a code point (0x80-0x9F for the extras), or -1 when cp1252 has none. */
const CP1252_BYTE = new Map(
  Array.from("€\u0081‚ƒ„…†‡ˆ‰Š‹Œ\u008dŽ\u008f\u0090‘’“”•–—˜™š›œ\u009džŸ", (ch, i) => [ch.codePointAt(0), 0x80 + i]),
);

function cp1252Byte(cp) {
  if (cp < 0x80 || (cp >= 0xa0 && cp <= 0xff)) return cp;
  const b = CP1252_BYTE.get(cp);
  return b !== undefined && cp1252Encodable(cp) ? b : -1;
}

function utf8Length(cp) {
  return cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
}

/**
 * A width function for a pdf-lib standard font that measures exactly like PyMuPDF's
 * `get_text_length` (the library fits text with it), quirks included:
 * - each character is mapped to its cp1252 byte (U+00B7 when cp1252 has none) and that byte is
 *   looked up as a Unicode glyph, so the cp1252 extras (0x80-0x9F) and controls take the font's
 *   missing width (the width of a space in Helvetica, Times and Courier);
 * - PyMuPDF steps through the string by the UTF-8 length of each character, so the one, two or
 *   three characters after a non-ASCII character are skipped ("Zürich" is measured without its "r").
 * Matching it keeps `shrunk` / `overflow` the same as the library's; the second point is a
 * PyMuPDF bug the library inherits.
 * @param {{widthOfTextAtSize: (text: string, size: number) => number}} font
 * @returns {(text: string, size: number) => number}
 */
export function makeMeasure(font) {
  const missing = font.widthOfTextAtSize(" ", 1000);
  const widths = new Map(); // glyph code -> width at size 1000
  const glyphWidth = (code) => {
    if (!widths.has(code)) {
      let w = missing;
      if ((code >= 0x20 && code < 0x7f) || (code >= 0xa0 && code <= 0xff)) {
        try {
          w = font.widthOfTextAtSize(String.fromCodePoint(code), 1000);
        } catch {
          w = missing;
        }
      }
      widths.set(code, w);
    }
    return widths.get(code);
  };
  return (text, size) => {
    const chars = codePoints(text);
    let total = 0;
    for (let pos = 0; pos < chars.length; ) {
      const cp = chars[pos].codePointAt(0);
      pos += utf8Length(cp);
      const code = cp1252Byte(cp);
      total += glyphWidth(code < 0 ? 0xb7 : code);
    }
    return (total * size) / 1000;
  };
}

function breakWord(word, measure, size, width) {
  const chunks = [];
  let cur = "";
  for (const ch of codePoints(word)) {
    if (cur && measure(cur + ch, size) > width) {
      chunks.push(cur);
      cur = ch;
    } else {
      cur += ch;
    }
  }
  if (cur) chunks.push(cur);
  return chunks;
}

// Python's str.split() with no argument: runs of whitespace separate, ends are dropped.
const PY_WS = new RegExp(`[${PY_SPACE_CLASS}]+`);

function pySplit(s) {
  return s.split(PY_WS).filter((w) => w !== "");
}

/** Greedy word wrap; "\n" starts a new paragraph; over-long words break by characters. */
export function wrap(text, measure, size, width) {
  const lines = [];
  for (const para of text.split("\n")) {
    let cur = "";
    for (const word of pySplit(para)) {
      const cand = cur ? `${cur} ${word}` : word;
      if (measure(cand, size) <= width) {
        cur = cand;
        continue;
      }
      if (cur) {
        lines.push(cur);
        cur = "";
      }
      if (measure(word, size) <= width) {
        cur = word;
      } else {
        const chunks = breakWord(word, measure, size, width);
        lines.push(...chunks.slice(0, -1));
        cur = chunks[chunks.length - 1];
      }
    }
    lines.push(cur);
  }
  return lines;
}

/** start, then successively smaller sizes in SHRINK_STEP steps, ending exactly at the minimum. */
function* sizes(start, minFontSize) {
  let size = start;
  yield size;
  while (size > minFontSize) {
    size = Math.max(size - SHRINK_STEP, minFontSize);
    yield size;
  }
}

/**
 * @typedef {object} Fit
 * @property {string} text  the value to write (cut only to maxLen / comb)
 * @property {number} fontSize
 * @property {number} lines
 * @property {"ok"|"shrunk"|"overflow"} status
 * @property {boolean} truncated
 * @property {string} unencodable  distinct characters cp1252 cannot encode
 */

/**
 * Decide the font size (and line count) for `text` in `rect`.
 * @param {string} text
 * @param {number[]} rect
 * @param {{fontSize: number|"auto", minFontSize: number, measure: (text: string, size: number) => number,
 *   multiline?: boolean, maxLines?: number|null, maxLen?: number|null, comb?: number|null}} opts
 * @returns {Fit}
 */
export function fitText(text, rect, { fontSize, minFontSize, measure, multiline = false, maxLines = null, maxLen = null, comb = null }) {
  let limit = maxLen;
  if (comb) limit = limit === null ? comb : Math.min(limit, comb);
  let truncated = false;
  const chars = codePoints(text);
  if (limit !== null && chars.length > limit) {
    text = chars.slice(0, limit).join("");
    truncated = true;
  }

  const width = rect[2] - rect[0];
  const height = rect[3] - rect[1];
  let start = fontSize === "auto" ? Math.min(AUTO_MAX_SIZE, (height - VPAD) / LINE_HEIGHT) : Number(fontSize);
  start = Math.max(start, minFontSize);
  const bad = unencodableChars(text);
  const done = (size, lines, fits) => ({
    text,
    fontSize: size,
    lines,
    status: !fits ? "overflow" : size < start ? "shrunk" : "ok",
    truncated,
    unencodable: bad,
  });

  if (comb) {
    const cell = width / comb;
    let size = start;
    let fits = true;
    for (size of sizes(start, minFontSize)) {
      const widest = Math.max(0, ...codePoints(text).map((ch) => measure(ch, size)));
      fits = widest <= cell && size * LINE_HEIGHT <= height - VPAD;
      if (fits) break;
    }
    return done(size, 1, fits);
  }

  const avail = width - 2 * PAD;
  if (multiline || maxLines !== null) {
    let size = start;
    let lines = 1;
    let fits = true;
    for (size of sizes(start, minFontSize)) {
      lines = wrap(text, measure, size, avail).length;
      fits = lines * size * LINE_HEIGHT <= height - VPAD && (maxLines === null || lines <= maxLines);
      if (fits) break;
    }
    return done(size, lines, fits);
  }

  let size = start;
  let fits = true;
  for (size of sizes(start, minFontSize)) {
    fits = measure(text, size) <= avail;
    if (fits) break;
  }
  return done(size, 1, fits);
}
