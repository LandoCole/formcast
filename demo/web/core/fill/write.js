// Write bound values into a PDF as real, editable AcroForm widgets with pdf-lib: the browser
// counterpart of src/formcast/render/write.py (fill design §5.1, §5.3).
//
// Read the widgets a PDF already has, then write one target at a time into a named/adopted widget
// or a new one. Every touched widget gets a fresh appearance (pdf-lib's providers, Helvetica or the
// widget's own base-14 font); a choice_mark gets a custom ellipse look (plan Ruling C).
// NeedAppearances is never set. Widgets of fields no call writes are left as they are; sibling
// kids of a written field share its value, so their appearances are regenerated too.
//
// Where pdf-lib cannot do what PyMuPDF does, the outcome the report records is matched, not the
// bytes: text outside WinAnsi is stored in full in /V and drawn without the characters the font
// lacks (the report says `unencodable`), and the `font_substituted` note is not produced.

import {
  PDFButton,
  PDFCheckBox,
  PDFDropdown,
  PDFHexString,
  PDFName,
  PDFNumber,
  PDFOptionList,
  PDFRadioGroup,
  PDFSignature,
  PDFString,
  PDFTextField,
  StandardFonts,
  TextAlignment,
  rgb,
} from "../../vendor/pdf-lib/pdf-lib.esm.min.js";
import { fromUserSpace, toUserSpace } from "../geometry.js";
import { pyRepr } from "../pycompat.js";
import { makeWarning } from "../values.js";

/** Fonts the written appearance honours, as the library's KEPT_FONTS (ZaDb is drawn as Helvetica here). */
const FONTS = { Helv: StandardFonts.Helvetica, Cour: StandardFonts.Courier, TiRo: StandardFonts.TimesRoman };
export const DEFAULT_FONT = "Helv";
const QUADDING = { left: TextAlignment.Left, center: TextAlignment.Center, right: TextAlignment.Right };
const MARK_INSET = 1.0; // choice_mark ellipse inset from the widget rect, points
const MARK_LINE_WIDTH = 1.0;
const KAPPA = 0.5523; // Bezier control-point factor for a quarter ellipse
const MULTILINE = 1 << 12;
const COMB = 1 << 24;
const OFF = PDFName.of("Off");

// ---------------------------------------------------------------- opening and reading

/**
 * The pdf-lib document plus what the writer needs: its pages' crop boxes and rotations, and the
 * standard fonts (embedded once, registered in the AcroForm /DR under their /DA names).
 * @param {import("../../vendor/pdf-lib/pdf-lib.esm.min.js").PDFDocument} doc
 */
export async function writerContext(doc) {
  const form = doc.getForm();
  const fonts = {};
  for (const [name, std] of Object.entries(FONTS)) fonts[name] = await doc.embedFont(std);
  const pages = doc.getPages().map((page) => {
    const c = page.getCropBox();
    return { page, crop: [c.x, c.y, c.x + c.width, c.y + c.height], rotation: ((page.getRotation().angle % 360) + 360) % 360 };
  });
  return { doc, form, fonts, pages, usedFonts: new Set() };
}

/** Register the fonts the writer used in /DR, so the /DA names it wrote resolve in viewers. */
export function registerFonts(ctx) {
  if (!ctx.usedFonts.size) return;
  const acro = ctx.form.acroForm.dict;
  const context = ctx.doc.context;
  let dr = acro.lookup(PDFName.of("DR"));
  if (!dr) {
    dr = context.obj({});
    acro.set(PDFName.of("DR"), dr);
  }
  let fontDict = dr.lookup(PDFName.of("Font"));
  if (!fontDict) {
    fontDict = context.obj({});
    dr.set(PDFName.of("Font"), fontDict);
  }
  for (const name of ctx.usedFonts) if (!fontDict.has(PDFName.of(name))) fontDict.set(PDFName.of(name), ctx.fonts[name].ref);
}

function kindOf(field) {
  if (field instanceof PDFTextField) return "text";
  if (field instanceof PDFCheckBox) return "checkbox";
  if (field instanceof PDFRadioGroup) return "radio";
  if (field instanceof PDFDropdown) return "combo";
  if (field instanceof PDFOptionList) return "list";
  if (field instanceof PDFSignature) return "signature";
  if (field instanceof PDFButton) return "button";
  return null;
}

/** The button's "on" appearance name (decoded), from /AP /N then /D; null if it has none. */
function onStateOf(widget) {
  for (const key of ["N", "D"]) {
    const ap = widget.dict.lookup(PDFName.of("AP"));
    const states = ap?.lookup?.(PDFName.of(key));
    if (!states || typeof states.keys !== "function") continue;
    for (const k of states.keys()) if (k !== OFF) return k.decodeText();
  }
  return null;
}

function optionPairs(field) {
  return field.acroField.getOptions().map(({ value, display }) => [value.decodeText(), (display ?? value).decodeText()]);
}

/** A widget's rect in template space (top-left origin, crop box). */
function rectOf(ctx, pageIndex, widget) {
  const r = widget.getRectangle();
  return fromUserSpace([r.x, r.y, r.x + r.width, r.y + r.height], ctx.pages[pageIndex].crop);
}

/**
 * Every widget of the document, in page then annotation order (bind.js ExistingWidget, with
 * `ref = {field, widget}` for the writer).
 * @returns {import("./bind.js").ExistingWidget[]}
 */
export function readExisting(ctx) {
  const owner = new Map(); // widget dict -> [field, widget]
  for (const field of ctx.form.getFields()) {
    for (const widget of field.acroField.getWidgets()) owner.set(widget.dict, [field, widget]);
  }
  const out = [];
  ctx.pages.forEach(({ page }, pageIndex) => {
    const annots = page.node.Annots();
    if (!annots) return;
    let index = 0;
    for (let i = 0; i < annots.size(); i++) {
      const dict = annots.lookup(i);
      if (!dict || typeof dict.get !== "function" || dict.get(PDFName.of("Subtype")) !== PDFName.of("Widget")) continue;
      const found = owner.get(dict);
      const position = index++;
      if (!found) continue;
      const [field, widget] = found;
      const kind = kindOf(field);
      if (kind === null) continue;
      const maxLen = kind === "text" ? field.getMaxLength() : undefined;
      out.push({
        page: pageIndex,
        index: position,
        name: field.getName(),
        kind,
        rect: rectOf(ctx, pageIndex, widget),
        on_state: kind === "checkbox" || kind === "radio" ? onStateOf(widget) : null,
        max_len: maxLen ? maxLen : null,
        options: kind === "combo" || kind === "list" ? optionPairs(field).map(([, d]) => d) : [],
        ref: { field, widget },
      });
    }
  });
  return out;
}

// ---------------------------------------------------------------- helpers

function daOf(binding) {
  const { field, widget } = binding.widget.ref;
  return widget.getDefaultAppearance() ?? field.acroField.getDefaultAppearance() ?? "";
}

function keptFont(da) {
  const m = /\/([^\s/[\]()<>{}%]+)\s+[-+\d.]+\s+Tf/.exec(da);
  const name = m ? m[1].toLowerCase() : "";
  return Object.keys(FONTS).find((f) => f.toLowerCase() === name) ?? null;
}

/**
 * The font the written appearance will use: `Helv`, `Cour` or `TiRo` (fit with the same one).
 * @returns {"Helv"|"Cour"|"TiRo"}
 */
export function textFontFor(binding) {
  if (binding.how === "created" || binding.widget === null) return DEFAULT_FONT;
  return keptFont(daOf(binding)) ?? DEFAULT_FONT;
}

/**
 * What a combo/list widget holds for `value` (library Ruling L): an option's export value as is,
 * an option's display text as that option's export value, anything else unchanged.
 */
export function optionValue(binding, value) {
  if (binding.how === "created" || binding.widget === null || !["combo", "list"].includes(binding.widget.kind)) return value;
  const pairs = optionPairs(binding.widget.ref.field);
  if (pairs.some(([e]) => e === value)) return value;
  const hit = pairs.find(([, d]) => d === value); // first option wins on duplicate display texts
  return hit ? hit[0] : value;
}

/**
 * How far the widget's text is turned (0/90/180/270): its /MK /R; a created widget gets the
 * page's /Rotate, so it reads upright on a rotated page.
 */
export function widgetRotation(ctx, page, binding) {
  if (binding.how === "created" || binding.widget === null) return ctx.pages[page].rotation;
  const r = binding.widget.ref.widget.getAppearanceCharacteristics()?.getRotation() ?? 0;
  return ((Math.trunc(r) % 360) + 360) % 360;
}

/** The fitted /DA: the old colour operator kept, the font and size replaced. */
function fittedDa(oldDa, font, size) {
  const color = /(?:^|\s)((?:[-+\d.]+\s+){1,4})(g|rg|k)(?=\s|$)/.exec(oldDa);
  return `${color ? `${color[1]}${color[2]}` : "0 g"} /${font} ${+size.toFixed(4)} Tf`;
}

function placeWidget(ctx, field, page, rect) {
  const [x0, y0, x1, y1] = toUserSpace(rect, ctx.pages[page].crop);
  field.addToPage(ctx.pages[page].page, {
    x: x0, y: y0, width: x1 - x0, height: y1 - y0, font: ctx.fonts[DEFAULT_FONT],
    textColor: rgb(0, 0, 0), backgroundColor: undefined, borderColor: undefined, borderWidth: 0,
  });
  const widgets = field.acroField.getWidgets();
  const widget = widgets[widgets.length - 1];
  const rot = ctx.pages[page].rotation;
  widget.getOrCreateAppearanceCharacteristics().setRotation(rot);
  return widget;
}

function readBack(ctx, page, field, widget) {
  return [field.getName(), rectOf(ctx, page, widget)];
}

const charsets = new WeakMap(); // font -> code points it can draw

/** The characters `font` can draw (newlines kept for a multi-line box, else spaces). */
function drawable(font, text, multiline) {
  if (!charsets.has(font)) charsets.set(font, new Set(font.getCharacterSet()));
  const set = charsets.get(font);
  let out = "";
  for (const ch of text) {
    if (ch === "\n" || ch === "\r") out += multiline ? "\n" : " ";
    else if (ch === "\t") out += " ";
    else if (set.has(ch.codePointAt(0))) out += ch;
  }
  return out;
}

/**
 * Set (or clear) a text/choice field's value and regenerate every widget's appearance. The look is
 * drawn from the characters the font has (pdf-lib throws on the others); /V then gets the full value.
 */
function setTextValue(ctx, field, value, fontName) {
  const font = ctx.fonts[fontName];
  const acro = field.acroField;
  const multiline = field instanceof PDFTextField && (acro.getFlags() & MULTILINE) !== 0;
  acro.dict.set(PDFName.of("V"), PDFHexString.fromText(value ? drawable(font, value, multiline) : ""));
  field.updateAppearances(font);
  if (value) acro.dict.set(PDFName.of("V"), PDFHexString.fromText(value));
  else acro.dict.delete(PDFName.of("V"));
  ctx.usedFonts.add(fontName);
}

// ---------------------------------------------------------------- text

/**
 * The bound existing widget's own text flags (all false for combo/list widgets).
 * @returns {{multiline: boolean, comb: boolean}}
 */
export function textFlags(binding) {
  if (binding.widget.kind !== "text") return { multiline: false, comb: false };
  const flags = binding.widget.ref.field.acroField.getFlags();
  return { multiline: (flags & MULTILINE) !== 0, comb: (flags & COMB) !== 0 };
}

/**
 * Write `text` (`fit.text` when a fit is given) into the bound text/combo/list widget, or a new
 * one at `rect`. `text` null clears it. `style` is the field's style with defaults filled in and
 * `explicit` the style keys the template sets (library Ruling K: only those override an existing
 * widget's own alignment and multi-line flag). Returns `{name, rect, warnings}` (`truncated`,
 * `not_an_option`).
 */
export function writeText(ctx, binding, { page, text, fit, style, explicit, rect }) {
  checkWritable(binding);
  const value = text === null ? null : fit !== null ? fit.text : text;
  let size = fit !== null ? fit.fontSize : null;
  if (size === null && style.font_size !== "auto") size = Number(style.font_size);

  if (binding.how === "created") {
    const field = ctx.form.createTextField(binding.new_name);
    const widget = placeWidget(ctx, field, page, rect);
    if (style.multiline || (fit !== null && fit.lines > 1)) field.enableMultiline();
    if (style.comb) {
      field.setMaxLength(style.comb);
      field.enableCombing();
    }
    field.setAlignment(QUADDING[style.align ?? "left"]);
    const da = fittedDa("0 g", DEFAULT_FONT, size ?? 0);
    field.acroField.dict.set(PDFName.of("DA"), PDFString.of(da));
    setTextValue(ctx, field, value, DEFAULT_FONT);
    const [name, got] = readBack(ctx, page, field, widget);
    return { name, rect: got, warnings: [] };
  }

  const { field, widget } = binding.widget.ref;
  const kind = binding.widget.kind;
  if (!["text", "combo", "list"].includes(kind)) {
    throw new Error(`widget ${pyRepr(binding.widget.name)} is a ${kind} widget, not text/combo/list`);
  }
  const warnings = [];
  let v = value;
  const maxLen = kind === "text" ? field.getMaxLength() : undefined;
  if (v && maxLen && Array.from(v).length > maxLen) {
    warnings.push(["truncated", `value cut to the ${maxLen} characters widget ${pyRepr(field.getName())} allows`]);
    v = Array.from(v).slice(0, maxLen).join("");
  }
  const fontName = textFontFor(binding);
  if (v && (kind === "combo" || kind === "list")) {
    v = optionValue(binding, v);
    if (!optionPairs(field).some(([e]) => e === v)) {
      warnings.push(["not_an_option", `${pyRepr(v)} is not one of the options of widget ${pyRepr(field.getName())}; written anyway`]);
    }
  }
  const oldDa = daOf(binding);
  if (size === null) {
    const m = /([-+\d.]+)\s+Tf/.exec(oldDa);
    size = m ? Number(m[1]) : 0;
  }
  widget.dict.set(PDFName.of("DA"), PDFString.of(fittedDa(oldDa, fontName, size)));
  // library Ruling K: an explicitly set align / multiline overrides the widget's own
  if (explicit.has("align")) {
    const q = PDFNumber.of(QUADDING[style.align]);
    field.acroField.dict.set(PDFName.of("Q"), q);
    widget.dict.set(PDFName.of("Q"), q);
  }
  if (explicit.has("multiline") && kind === "text") {
    if (style.multiline) field.enableMultiline();
    else field.disableMultiline();
  }
  setTextValue(ctx, field, v, fontName);
  const [name, got] = readBack(ctx, binding.widget.page, field, widget);
  return { name, rect: got, warnings: warnings.map(([code, msg]) => makeWarning(code, msg, { page: binding.widget.page, rect: got })) };
}

function checkWritable(binding) {
  if (binding.how === "none") throw new Error(`binding ${pyRepr(binding.key)} has no widget to write`);
  if (binding.how !== "created" && binding.widget?.kind === "signature") {
    throw new Error(`widget ${pyRepr(binding.widget.name)} is a signature field and is not writable`);
  }
}

// ---------------------------------------------------------------- choices

/** Point the field's /V and every kid's /AS at `state` (a decoded name, or "Off"). */
function setButtonState(field, state) {
  const name = PDFName.of(state);
  field.acroField.dict.set(PDFName.of("V"), name);
  for (const kid of field.acroField.getWidgets()) {
    if (kid.dict !== field.acroField.dict) kid.dict.delete(PDFName.of("V"));
    kid.setAppearanceState(state !== "Off" && onStateOf(kid) === state ? name : OFF);
  }
}

/**
 * Turn the bound checkbox / radio kid on (its own on-state) or off. A created widget is a
 * checkbox at `rect` (radio_option included, as in the library).
 */
export function writeChoice(ctx, binding, { page, on, rect }) {
  checkWritable(binding);
  if (binding.how === "created") {
    const field = ctx.form.createCheckBox(binding.new_name);
    const widget = placeWidget(ctx, field, page, rect);
    field.updateAppearances();
    if (on) field.check();
    else field.uncheck();
    const [name, got] = readBack(ctx, page, field, widget);
    return { name, rect: got, warnings: [] };
  }
  const { field, widget } = binding.widget.ref;
  if (!["checkbox", "radio"].includes(binding.widget.kind)) {
    throw new Error(`widget ${pyRepr(binding.widget.name)} is a ${binding.widget.kind} widget, not a button`);
  }
  const state = onStateOf(widget);
  const turnOn = on && state !== null; // a box with no "on" appearance can't show on (fill: not_shown)
  if (turnOn) {
    setButtonState(field, state);
  } else if (binding.widget.kind === "checkbox") {
    setButtonState(field, "Off");
  } else {
    widget.setAppearanceState(OFF); // a radio kid turned off; the group's value goes only if it was this kid's
    const v = field.acroField.dict.lookup(PDFName.of("V"));
    if (state !== null && v === PDFName.of(state)) {
      const still = field.acroField.getWidgets().some((k) => k.getAppearanceState() === PDFName.of(state));
      if (!still) field.acroField.dict.set(PDFName.of("V"), OFF);
    }
  }
  const [name, got] = readBack(ctx, binding.widget.page, field, widget);
  return { name, rect: got, warnings: [] };
}

function ellipse(x0, y0, x1, y1) {
  const n = (v) => +v.toFixed(4);
  const [cx, cy, rx, ry] = [(x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2];
  const [ox, oy] = [rx * KAPPA, ry * KAPPA];
  const c = (...v) => `${v.map(n).join(" ")} c\n`;
  return (
    `${n(cx - rx)} ${n(cy)} m\n` +
    c(cx - rx, cy + oy, cx - ox, cy + ry, cx, cy + ry) +
    c(cx + ox, cy + ry, cx + rx, cy + oy, cx + rx, cy) +
    c(cx + rx, cy - oy, cx + ox, cy - ry, cx, cy - ry) +
    c(cx - ox, cy - ry, cx - rx, cy - oy, cx - rx, cy)
  );
}

/** Replace the checkbox's look: an ellipse inset in the rect when on, nothing when off. */
function setMarkAppearance(ctx, widget, onName) {
  const r = widget.getRectangle();
  const [w, h] = [r.width, r.height];
  const i = MARK_INSET;
  const path = ellipse(i, i, Math.max(w - i, i), Math.max(h - i, i));
  const context = ctx.doc.context;
  const form = (src) =>
    context.register(context.stream(src, { Type: "XObject", Subtype: "Form", BBox: [0, 0, w, h], Resources: {} }));
  const onX = form(`q\n0 G\n${MARK_LINE_WIDTH} w\n${path}S\nQ\n`);
  const offX = form("");
  const normal = context.obj({});
  normal.set(PDFName.of(onName), onX);
  normal.set(OFF, offX);
  widget.dict.set(PDFName.of("AP"), context.obj({ N: normal })); // no /D look, so no check flashes
  const mk = widget.getAppearanceCharacteristics();
  if (mk) mk.dict.delete(PDFName.of("CA"));
}

/**
 * A checkbox over the option text whose "on" look is a stroked ellipse and "off" look empty.
 * Created at `rect`, or an adopted checkbox from an earlier fill rewritten in place.
 */
export function writeChoiceMark(ctx, binding, { page, on, rect }) {
  checkWritable(binding);
  let field;
  let widget;
  let pageIndex = page;
  if (binding.how === "created") {
    field = ctx.form.createCheckBox(binding.new_name);
    widget = placeWidget(ctx, field, page, rect);
  } else {
    if (binding.widget.kind !== "checkbox") throw new Error(`choice_mark widget ${pyRepr(binding.widget.name)} must be a checkbox`);
    ({ field, widget } = binding.widget.ref);
    pageIndex = binding.widget.page;
  }
  const onName = onStateOf(widget) ?? "Yes";
  setMarkAppearance(ctx, widget, onName);
  setButtonState(field, on ? onName : "Off");
  const [name, got] = readBack(ctx, pageIndex, field, widget);
  return { name, rect: got, warnings: [] };
}

/**
 * Whether the existing button a binding names shows "on" now (its /AS), so a box another write
 * turned off is reported off.
 */
export function showsOn(binding) {
  const as = binding.widget.ref.widget.getAppearanceState();
  return as !== undefined && as !== OFF;
}
