"""Draft the ``value`` of each ground-truth choice box (see ``score`` for the GT format).

A choice box on a path with options (an enum, or the item enum of a multi-select
array) stands for one of them; a Yes/No pair on a boolean path stands for true or
false. This script proposes those values for review:

    python eval/gt_choice_values.py --form <id> [--write-draft]
    python eval/gt_choice_values.py --all [--write-draft]

For every choice GT entry with a path and no ``value`` it takes the box's label
text (the GT label, the widget's on-state name, and the PDF words on the box's line
to its right, else to its left, up to the next choice box) and matches it, case- and
punctuation-insensitively, against the path's options: an option matches when all of
its words are in the text, and a longer option beats one it contains. Boolean paths
take true/false from a leading or trailing Yes/No word. Where the text is
ambiguous, absent or two sources disagree the proposal is null. Nothing here is
final: a reviewer confirms each against the PDF.

``--write-draft`` writes ``forms/gt/drafts/<id>.values.json``::

    {key: {"kind": "fields"|"widget_paths", "needs_value": bool, "page", "rect", "label",
           "path", "path_type", "transforms", "options": [...]|null, "on_state"?,
           "near_text": {"right": str, "left": str}, "proposed": value|null,
           "confidence": "high"|"text"|null, "evidence": str, "widgets"?: [...]}}

``key`` is ``"fields[<index>]"`` (the entry's index in the GT file) or the
``widget_paths`` widget name. Widgets sharing a name get one entry whose ``proposed``
is an object keyed by on-state name (the form a ``widget_paths`` ``value`` takes) and
whose ``widgets`` list has one item per widget.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score  # noqa: E402  (eval/score.py)
from formcast.geometry import Rect  # noqa: E402
from formcast.pagemap.extract import open_pdf  # noqa: E402
from formcast.schema import SchemaPath, flatten_schema  # noqa: E402

DRAFT_DIR = score.GT_DIR / "drafts"
SCHEMA_DIR = ROOT / "forms" / "schemas"
# How far (points) from a box its label words are looked for.
NEAR = 150.0
# A gap wider than this (points) between two words ends the label.
MAX_GAP = 40.0
# A word is on a box's line when its vertical span overlaps the box's by this share
# of the smaller of the two heights.
LINE_OVERLAP = 0.4
# A Yes/No word at the start of a text decides a boolean only in a text this short (words);
# at the end it always does.
MAX_YESNO_WORDS = 4

_STOP = frozenset({"a", "an", "the", "of", "and", "or", "to", "in", "is", "for", "if", "your"})
_YES = frozenset({"yes", "true"})
_NO = frozenset({"no", "false"})


# ---------- text matching ----------


def tokens(text: str) -> list[str]:
    """Lower-case words of ``text``: punctuation, underscores, camelCase and letter/digit joins split, plural s dropped."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[a-zA-Z])(?=[0-9])|(?<=[0-9])(?=[a-zA-Z])", " ", str(text))
    out = []
    for t in re.split(r"[^0-9a-zA-Z]+", text.lower()):
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if t:
            out.append(t)
    return out


def options_of(sp: SchemaPath) -> list | None:
    """The values a choice box on ``sp`` can stand for: ``[True, False]`` for a boolean,
    the enum (or a multi-select array's item enum), else None (no fixed options)."""
    if sp.type == "boolean":
        return [True, False]
    return list(sp.enum) if sp.enum else None


def _core(text_tokens: list[str]) -> frozenset[str]:
    core = frozenset(t for t in text_tokens if t not in _STOP)
    return core or frozenset(text_tokens)


def match_option(text: str, options: list[str]) -> tuple[str | None, str]:
    """The one option whose words are all in ``text`` (a more specific option beats one it
    contains), with a note saying why; ``(None, note)`` when none or several fit."""
    have = set(tokens(text))
    if not have:
        return None, "no text"
    full = [o for o in options if (c := _core(tokens(o))) and c <= have]
    # drop an option that is contained in a more specific matching one
    full = [o for o in full if not any(_core(tokens(o)) < _core(tokens(p)) for p in full)]
    if len(full) == 1:
        return full[0], f"{text!r} contains {full[0]!r}"
    if len(full) > 1:
        return None, f"{text!r} fits several options {full}"
    return None, f"{text!r} matches no option"


def match_initial(on_state: str, options: list[str]) -> tuple[str | None, str]:
    """A one-letter on-state names the one option starting with that letter."""
    if len(on_state) != 1 or not on_state.isalpha():
        return None, f"on-state {on_state!r} is not a single letter"
    hits = [o for o in options if o[:1].lower() == on_state.lower()]
    if len(hits) == 1:
        return hits[0], f"on-state {on_state!r} is the initial of {hits[0]!r}"
    return None, f"on-state {on_state!r} is the initial of {len(hits)} options"


def match_yes_no(text: str) -> tuple[bool | None, str]:
    """True/False when ``text`` ends in a Yes/No word, or is a short text starting with one.

    A lone ``Y``/``N`` counts. A Yes/No word at the start of a longer text ("No known
    allergies") is part of an answer and does not decide.
    """
    toks = tokens(text)
    if len(toks) == 1:
        toks = {"y": ["yes"], "n": ["no"]}.get(toks[0], toks)
    ends = {toks[-1]} if toks else set()
    if toks and len(toks) <= MAX_YESNO_WORDS:
        ends.add(toks[0])
    yes, no = bool(ends & _YES), bool(ends & _NO)
    if yes == no:
        return None, f"{text!r} has no (or both) Yes/No word at its ends"
    return yes, f"{text!r} says {'Yes' if yes else 'No'}"


# ---------- text near a box ----------


def _vspan_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return inter > 0 and inter >= LINE_OVERLAP * min(a[1] - a[0], b[1] - b[0])


def nearby_text(words: list, rect: Rect, other_boxes: list[Rect]) -> dict[str, str]:
    """The words on ``rect``'s line to its right and to its left.

    ``words`` are PyMuPDF ``get_text("words")`` tuples of the box's page, ``other_boxes``
    the other choice boxes there. Each side runs up to ``NEAR`` points and stops at a gap
    over ``MAX_GAP``. The words between this box and a neighbour on the line belong to
    whichever of the two they sit closer to (on a tie to the box on their left), so each
    label is given to one box only. Characters with no letter or digit (a ballot-box
    glyph) are ignored.
    """
    vs = (rect[1], rect[3])
    line = [w for w in words if _vspan_overlap(vs, (w[1], w[3])) and any(c.isalnum() for c in w[4])]
    same_line = [b for b in other_boxes if _vspan_overlap(vs, (b[1], b[3]))]
    nxt = min((b for b in same_line if b[0] >= rect[2] - 1), key=lambda b: b[0], default=None)
    prv = max((b for b in same_line if b[2] <= rect[0] + 1), key=lambda b: b[2], default=None)

    right = sorted((w for w in line if w[0] >= rect[0] - 2 and w[2] > rect[2] + 1), key=lambda w: w[0])
    left = sorted((w for w in line if w[2] <= rect[0] + 1), key=lambda w: -w[2])
    if nxt is not None:
        right = [w for w in right if w[2] <= nxt[0] + 1]
    else:
        right = [w for w in right if w[0] < rect[2] + NEAR]
    if prv is not None:
        left = [w for w in left if w[0] >= prv[2] - 1]
    else:
        left = [w for w in left if w[2] > rect[0] - NEAR]

    def run(ws, start, sign):
        """The words from the box outwards until a gap over ``MAX_GAP``."""
        out, last = [], start
        for w in ws:
            near, far = (w[0], w[2]) if sign > 0 else (w[2], w[0])
            if sign * (near - last) > MAX_GAP:
                break
            out.append(w)
            last = far
        return out

    r = run(right, rect[2], 1)
    left_run = run(left, rect[0], -1)

    # a run between two boxes goes to the nearer one, on a tie to the left one
    if r and nxt is not None and nxt[0] - r[-1][2] < max(r[0][0] - rect[2], 0) - 1:
        r = []
    if left_run and prv is not None and left_run[-1][0] - prv[2] <= max(rect[0] - left_run[0][2], 0) + 1:
        left_run = []
    return {"right": " ".join(w[4] for w in r), "left": " ".join(w[4] for w in left_run[::-1])}


# ---------- proposals ----------


def _label_text(label: str) -> str:
    """The option part of a GT label ``"Question: option"``."""
    return label.rsplit(": ", 1)[-1].strip() if ": " in label else label.strip()


def _try(sp: SchemaPath, opts: list, text: str, *, is_state: bool = False, shared: bool = False):
    """``(value or None, why)`` for one piece of text against the path's options.

    A check box on its own is made with the on-state "Yes" (or "On") whatever it asks, so
    an on-state saying Yes means something only for widgets sharing a name."""
    if sp.type == "boolean":
        value, why = match_yes_no(text)
        if is_state and value is True and not shared:
            return None, f"on-state {text!r} is the default of a lone check box"
        return value, why
    value, why = match_option(text, opts)
    if value is None and is_state:
        value, why = match_initial(text, opts)
    return value, why


def propose(
    sp: SchemaPath, label: str, on_state: str | None, near: dict[str, str], shared: bool = False
) -> tuple[object, str | None, str]:
    """``(value or None, confidence, evidence)`` for one box on path ``sp``.

    The GT label and the on-state name decide first (confidence "high"; two that name
    different values leave the proposal null). Without a hit from them the words to the
    box's right, then those to its left, are tried (confidence "nearby"). On a path with
    no options the box's own text is the proposal (confidence "text"). ``shared``: other
    widgets have this widget's field name.
    """
    opts = options_of(sp)
    if opts is None:  # no fixed options: the box stands for its own text
        text = _label_text(label) or near["right"] or near["left"]
        if text:
            return text, "text", f"no options on {sp.path!r}; the box's own text {text!r}"
        return None, None, f"no options on {sp.path!r} and no text near the box"

    notes: list[str] = []
    hits: list[tuple[str, object, str]] = []
    for name, text, is_state in (("label", _label_text(label), False), ("on-state", on_state or "", True)):
        if not text:
            continue
        value, why = _try(sp, opts, text, is_state=is_state, shared=shared)
        if value is None:
            notes.append(f"{name}: {why}")
        else:
            hits.append((name, value, why))
    if len({json.dumps(v) for _, v, _ in hits}) > 1:
        return None, None, "sources disagree: " + "; ".join(f"{n}: {w}" for n, _, w in hits)
    if hits:
        return hits[0][1], "high", "; ".join(f"{n}: {w}" for n, _, w in hits)

    for name in ("right", "left"):
        if not near[name]:
            continue
        value, why = _try(sp, opts, near[name])
        if value is not None:
            return value, "nearby", f"{name} text: {why}"
        notes.append(f"{name} text: {why}")
    if sp.type == "boolean":
        return None, None, "no Yes/No word (a box on a boolean path means true); " + "; ".join(notes)
    return None, None, "; ".join(notes) or "no text near the box"


# ---------- drafting ----------


def _entry_for(gt, on_state, sp, near, label_is_name: bool, shared: bool) -> dict:
    # a widget with no label of its own is labelled with its field name, which says nothing
    proposed, conf, evidence = propose(sp, "" if label_is_name else gt.label, on_state, near, shared)
    out = {
        "page": gt.page,
        "rect": [round(v, 2) for v in gt.rect],
        "label": gt.label,
        "near_text": near,
        "proposed": proposed,
        "confidence": conf,
        "evidence": evidence,
    }
    if on_state is not None:
        out["on_state"] = on_state
    return out


def draft_values(form: dict, pdf_path, schema: dict, gt_dir: Path | None = None) -> dict:
    """Proposed values for the choice GT entries of ``form`` that lack one (see the module doc)."""
    paths = {p.path: p for p in flatten_schema(schema)}
    entries = score.gt_entries(form, pdf_path, gt_dir) or []
    choice = [(k, g, st) for k, g, st in entries if g.type == "choice" and g.path]
    boxes: dict[int, list[Rect]] = {}
    for _, g, _ in choice:
        boxes.setdefault(g.page, []).append(g.rect)
    doc = open_pdf(pdf_path)
    try:
        words = {p: doc[p].get_text("words") for p in boxes}
    finally:
        doc.close()

    groups: dict[str, list] = {}
    for k, g, st in choice:
        groups.setdefault(k, []).append((g, st))
    fillable = form["kind"] == "fillable"
    out: dict[str, dict] = {}
    for key, members in groups.items():
        if all(g.value is not None for g, _ in members):
            continue
        g0 = members[0][0]
        if g0.path not in paths:
            continue  # test_gt_paths_resolve_in_the_schema reports this
        sp = paths[g0.path]
        items = []
        for g, st in members:
            others = [r for r in boxes[g.page] if r != g.rect]
            near = nearby_text(words[g.page], g.rect, others)
            items.append(_entry_for(g, st, sp, near, fillable and g.label == key, len(members) > 1))
        head = {
            "kind": "widget_paths" if fillable else "fields",
            "needs_value": sp.type != "boolean",
            "path": g0.path,
            "path_type": sp.type if sp.type != "array" else f"array of {sp.item_type}",
            "transforms": g0.transforms,
            "options": options_of(sp),
        }
        entry = {**head, **items[0]}
        if len(items) > 1:
            entry["widgets"] = items
            states = [i.get("on_state") for i in items]
            if all(states) and len(set(states)) == len(states):
                entry["proposed"] = {s: i["proposed"] for s, i in zip(states, items)}
                entry["confidence"] = "high" if all(i["confidence"] for i in items) else None
                entry["evidence"] = "per widget, see widgets"
            else:
                entry["proposed"] = None
                entry["confidence"] = None
                entry["evidence"] = "widgets share a name but not distinct on-states; see widgets"
        out[key] = entry
    return out


def summarize(draft: dict) -> dict[str, int]:
    """Box counts: choice boxes needing a value by how they were proposed (high = from the
    label or on-state, nearby = from page words only, text = an open-ended path's own
    text, none = unsure), and boolean-path boxes by whether they got a Yes/No."""
    out = dict.fromkeys(("need", "high", "nearby", "text", "none", "bool", "bool_yes_no"), 0)
    for e in draft.values():
        for box in e.get("widgets", [e]):
            if e["needs_value"]:
                out["need"] += 1
                out[box["confidence"] or "none"] += 1
            else:
                out["bool"] += 1
                out["bool_yes_no"] += box["confidence"] is not None
    return out


# ---------- command line ----------


def _form_paths(form: dict) -> tuple[Path, Path]:
    return ROOT / "forms" / form["split"] / f"{form['id']}.pdf", SCHEMA_DIR / f"{form['id']}.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--form", help="manifest form id")
    ap.add_argument("--all", action="store_true", help="every manifest form")
    ap.add_argument("--write-draft", action="store_true", help="write forms/gt/drafts/<id>.values.json")
    args = ap.parse_args(argv)
    manifest = json.loads((ROOT / "forms" / "manifest.json").read_text())["forms"]
    if bool(args.form) == bool(args.all):
        ap.error("give exactly one of --form and --all")
    forms = manifest if args.all else [f for f in manifest if f["id"] == args.form]
    if not forms:
        ap.error(f"unknown form {args.form!r}")
    for form in forms:
        pdf, schema_file = _form_paths(form)
        draft = draft_values(form, pdf, json.loads(schema_file.read_text()))
        s = summarize(draft)
        print(f"{form['id']}: {s['need']} boxes need a value (high {s['high']}, nearby {s['nearby']}, "
              f"text {s['text']}, unsure {s['none']}); {s['bool']} boolean boxes ({s['bool_yes_no']} Yes/No)")
        if args.write_draft:
            DRAFT_DIR.mkdir(parents=True, exist_ok=True)
            (DRAFT_DIR / f"{form['id']}.values.json").write_text(json.dumps(draft, indent=1) + "\n")
        elif args.form:
            for key, e in draft.items():
                print(f"  {key}: {e['path']} -> {json.dumps(e['proposed'])}  [{e['evidence']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
