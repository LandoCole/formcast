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

/**
 * A width function for a pdf-lib standard font that measures like the library's `_width`
 * (src/formcast/render/fit.py): the appearance writes the value as cp1252 bytes, so each character
 * is drawn as its own glyph; a character cp1252 cannot encode counts as U+00B7 and one the font has
 * no glyph for (a control) as the font's missing width (the width of a space in Helvetica, Times
 * and Courier).
 * @param {{widthOfTextAtSize: (text: string, size: number) => number}} font
 * @returns {(text: string, size: number) => number}
 */
export function makeMeasure(font) {
  const missing = font.widthOfTextAtSize(" ", 1000);
  const widths = new Map(); // code point -> width at size 1000
  const glyphWidth = (cp) => {
    if (!widths.has(cp)) {
      let w = missing;
      if ((cp >= 0x20 && cp < 0x7f) || cp >= 0xa0) {
        try {
          w = font.widthOfTextAtSize(String.fromCodePoint(cp), 1000);
        } catch {
          w = missing;
        }
      }
      widths.set(cp, w);
    }
    return widths.get(cp);
  };
  return (text, size) => {
    let total = 0;
    for (const ch of codePoints(text)) {
      const cp = ch.codePointAt(0);
      total += glyphWidth(cp1252Encodable(cp) ? cp : 0xb7);
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
