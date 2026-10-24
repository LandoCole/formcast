"""Compact text rendering of a form map: the agent's first view of the form.

Per page: a header (kind, size, text source), the detector candidates in
reading order, then text lines that look like field prompts but have no field
box nearby (likely missed fields), then the widgets of fillable pages. One
line per item, e.g.::

    c0_3 text conf=.91 [130,288,500,302] label(left)="Address" sub="Street|City|State|Zip"
    c0_7 choice conf=.62 [72,140,82,150] right="Yes"
    l0_9 [72,347,117,364] "Phone #" +rule
    l0_12 [72,560,120,570] "Signature" +rule_above
    w0_2 checkbox name="agree" [72,140,82,150] right="Yes"

``label(rel)`` is the linked label line and how it sits relative to the box;
``sub`` lists small-font captions printed just under the box; ``right`` is the
text starting just right of the box (a choice box's option, or the next
label). A prompt is a line with at least two letters ending in ``:``, ``#`` or ``?``, one followed by a
rule (``+rule``), or a caption printed right under a rule (``+rule_above``,
like "Signature" under a signature line); only prompts with no field box
within 24pt are listed.
When the text exceeds ``max_chars`` every page keeps the same share of its
item lines and the summary says how many were dropped.
"""

from __future__ import annotations

import json
import re
import unicodedata

from formcast.geometry import Rect
from formcast.pagemap.model import FormMap, PageMap, TextLine
from formcast.pagemap.query import (
    EDGE_TOL,
    find_label,
    median_font_size,
    right_text,
    sublabels,
)

# A prompt ends in one of these, or is followed by a rule; it has some letters
# (amounts and numbers in a table never are prompts).
PROMPT_END = re.compile(r"[:#?]\s*$")
LETTER = re.compile(r"[^\W\d_]")
# A prompt with a field box this close (points, edge to edge) is not "unlabeled".
PROMPT_NEARBY = 24.0
# A rule "follows" a line when it starts within this many points of its end.
RULE_FOLLOW_GAP = 30.0
# A caption starts at most this far below the rule it is printed under.
CAPTION_MAX_GAP = 4.0
MAX_TEXT = 60
MAX_OPTIONS = 8
# Characters reserved for each page's truncation note and the global one.
PAGE_NOTE_RESERVE = 110
GLOBAL_NOTE_RESERVE = 140

_SECTION_TITLES = (
    "candidates (reading order):",
    "unlabeled prompts (no field box nearby):",
    "widgets:",
)


def _q(text: str, limit: int = MAX_TEXT) -> str:
    """``text`` as a JSON string, whitespace collapsed, cut to ``limit`` characters.

    Control and private-use characters (often checkbox or bullet glyphs from
    symbol fonts) are written as ``\\uXXXX`` so they stay visible.
    """
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return "".join(
        f"\\u{ord(ch):04x}" if unicodedata.category(ch)[0] == "C" else ch
        for ch in json.dumps(text, ensure_ascii=False)
    )


def _box(r: Rect) -> str:
    return "[" + ",".join(str(round(v)) for v in r) + "]"


def _conf(c: float) -> str:
    s = f"{c:.2f}"
    return s[1:] if s.startswith("0") else s


def _context(page: PageMap, rect: Rect, label: tuple[TextLine, str] | None) -> str:
    """The ``label(...)``, ``sub`` and ``right`` parts for a field box."""
    parts = []
    label_id = None
    if label is not None:
        line, relation = label
        label_id = line.id
        parts.append(f"label({relation})={_q(line.text)}")
    subs = [ln.text for ln in sublabels(page, rect) if ln.id != label_id]
    if subs:
        parts.append(f"sub={_q('|'.join(subs), 2 * MAX_TEXT)}")
    right = right_text(page, rect)
    if right is not None and right.id != label_id:
        parts.append(f"right={_q(right.text)}")
    return " ".join(parts)


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _followed_by_rule(page: PageMap, line: TextLine) -> bool:
    x0, y0, x1, y1 = line.rect
    height = max(y1 - y0, 1.0)
    for rule in page.rules:
        if rule.orientation != "h":
            continue
        rx0, ry, rx1, _ = rule.rect
        on_row = (y0 + y1) / 2 <= ry <= y1 + 0.5 * height
        # The rule starts past the line's middle: a border running under the
        # whole text (a table cell) is not a fill-in line.
        starts_after = (x0 + x1) / 2 <= rx0 <= x1 + RULE_FOLLOW_GAP
        if on_row and starts_after and rx1 > x1 + EDGE_TOL:
            return True
    return False


def _caption_under_rule(page: PageMap, line: TextLine, median: float | None) -> bool:
    """True when ``line`` sits right under an ``h`` rule spanning it, like the
    "Signature" printed under a signature line. Not for text larger than body
    text, nor for text with a spanning rule right below it as well (a cell)."""
    if median is not None and line.font_size and line.font_size > median:
        return False
    x0, y0, x1, y1 = line.rect

    def spanning(r: Rect) -> bool:
        return r[0] <= x0 + EDGE_TOL and r[2] >= x1 - EDGE_TOL

    h_rules = [r.rect for r in page.rules if r.orientation == "h" and spanning(r.rect)]
    over = any(-EDGE_TOL <= y0 - r[1] <= CAPTION_MAX_GAP for r in h_rules)
    # Closed by a rule right below too: a table cell, not a caption.
    under = any(-EDGE_TOL <= r[1] - y1 <= CAPTION_MAX_GAP for r in h_rules)
    return over and not under


def _near_field(page: PageMap, rect: Rect) -> bool:
    boxes = [c.rect for c in page.candidates] + [w.rect for w in page.widgets]
    for b in boxes:
        dx = max(0.0, b[0] - rect[2], rect[0] - b[2])
        dy = max(0.0, b[1] - rect[3], rect[1] - b[3])
        if (dx * dx + dy * dy) ** 0.5 <= PROMPT_NEARBY:
            return True
    return False


def _page_body(page: PageMap) -> list[str]:
    body: list[str] = []
    used_labels: set[int] = set()

    if page.candidates:
        body.append(_SECTION_TITLES[0])
        for c in page.candidates:
            label = None
            if c.label_line_id is not None:
                line = next((ln for ln in page.lines if ln.id == c.label_line_id), None)
                if line is not None:
                    label = (line, c.label_relation or "?")
                    used_labels.add(line.id)
            body.append(
                _join(f"{c.id} {c.kind} conf={_conf(c.conf)} {_box(c.rect)}",
                      _context(page, c.rect, label))
            )

    widget_lines = []
    for i, w in enumerate(page.widgets):
        label = find_label(page, w.rect, w.kind)
        if label is not None:
            used_labels.add(label[0].id)
        extras = []
        if w.tooltip:
            extras.append(f"tip={_q(w.tooltip)}")
        if w.options:
            shown = w.options[:MAX_OPTIONS]
            more = f"|+{len(w.options) - len(shown)} more" if len(w.options) > len(shown) else ""
            extras.append(f"options={_q('|'.join(shown) + more, 2 * MAX_TEXT)}")
        widget_lines.append(
            _join(f"w{page.index}_{i} {w.kind} name={_q(w.name)} {_box(w.rect)}",
                  _context(page, w.rect, label), *extras)
        )

    prompts = []
    median = median_font_size(page)
    for line in page.lines:
        if line.id in used_labels or len(LETTER.findall(line.text)) < 2:
            continue
        ruled = _followed_by_rule(page, line)
        caption = not ruled and _caption_under_rule(page, line, median)
        if not (ruled or caption or PROMPT_END.search(line.text)):
            continue
        if _near_field(page, line.rect):
            continue
        marker = "+rule" if ruled else "+rule_above" if caption else ""
        prompts.append(
            _join(f"l{page.index}_{line.id} {_box(line.rect)} {_q(line.text)}", marker)
        )
    if prompts:
        body.append(_SECTION_TITLES[1])
        body.extend(prompts)

    if widget_lines:
        body.append(_SECTION_TITLES[2])
        body.extend(widget_lines)
    return body


def _page_header(page: PageMap) -> list[str]:
    rot = f" | rot={page.rotation}" if page.rotation else ""
    head = [
        f"== page {page.index} | {page.kind} | {round(page.width)}x{round(page.height)}pt"
        f" | text={page.text_source}{rot} | {len(page.candidates)} candidates,"
        f" {len(page.widgets)} widgets, {len(page.lines)} lines, {len(page.rules)} rules =="
    ]
    head += [f"note: {n}" for n in page.notes]
    return head


def _is_item(line: str) -> bool:
    return line not in _SECTION_TITLES


def _preamble(form_map: FormMap) -> str:
    """The summary's first line, without the pointer to the query helpers."""
    source = f" ({form_map.source_name})" if form_map.source_name else ""
    return (
        f"FORM MAP{source}: {len(form_map.pages)} page(s). Rects are [x0,y0,x1,y1] in PDF"
        " points, origin top-left of the unrotated page. Ids: c=detected candidate,"
        " w=widget, l=text line."
    )


def summarize_page(form_map: FormMap, page: int, *, max_chars: int = 24000) -> str:
    """One page of the form map as text, for a reader without the query helpers.

    The preamble (without its pointer to the query helpers), then the page's whole section, as
    :func:`summarize` writes it. Only a page whose own text runs past ``max_chars`` is cut, at an
    item line, with a note saying how many item lines were dropped. A page the form map lacks
    gives the preamble alone.
    """
    preamble = _preamble(form_map)
    pm = next((p for p in form_map.pages if p.index == page), None)
    if pm is None:
        return preamble
    top = [preamble] + _page_header(pm)
    body = _page_body(pm)
    full = "\n".join(top + body)
    if len(full) <= max_chars:
        return full
    budget = max_chars - len("\n".join(top)) - PAGE_NOTE_RESERVE
    kept, used = [], 0
    for ln in body:
        if used + len(ln) + 1 > budget:
            break
        kept.append(ln)
        used += len(ln) + 1
    while kept and not _is_item(kept[-1]):
        kept.pop()
    items = sum(_is_item(ln) for ln in body)
    dropped = items - sum(_is_item(ln) for ln in kept)
    note = f"[truncated: {dropped} of {items} item lines on this page dropped]"
    return "\n".join(top + kept + [note])[:max_chars]


def summarize(form_map: FormMap, *, max_chars: int = 24000) -> str:
    """The form map as compact text of at most ``max_chars`` characters."""
    preamble = _preamble(form_map) + " Use the query helpers for all text and geometry."
    pages = [(_page_header(p), _page_body(p)) for p in form_map.pages]
    full = "\n".join([preamble] + [ln for head, body in pages for ln in head + body])
    if len(full) <= max_chars:
        return full

    fixed = len(preamble) + 1 + GLOBAL_NOTE_RESERVE
    fixed += sum(sum(len(ln) + 1 for ln in head) + PAGE_NOTE_RESERVE for head, _ in pages)
    body_total = sum(len(ln) + 1 for _, body in pages for ln in body)
    avail = max_chars - fixed
    if avail > 0 and body_total > 0:
        out_pages, dropped_all, items_all = [], 0, 0
        for head, body in pages:
            budget = avail * sum(len(ln) + 1 for ln in body) / body_total
            kept, used = [], 0
            for ln in body:
                if used + len(ln) + 1 > budget:
                    break
                kept.append(ln)
                used += len(ln) + 1
            while kept and not _is_item(kept[-1]):
                kept.pop()
            items = sum(_is_item(ln) for ln in body)
            dropped = items - sum(_is_item(ln) for ln in kept)
            dropped_all += dropped
            items_all += items
            note = (
                [f"[truncated: {dropped} of {items} item lines on this page dropped;"
                 " use the query helpers for the rest]"]
                if dropped else []
            )
            out_pages.append(head + kept + note)
        global_note = (
            f"[summary truncated to {max_chars} chars: {dropped_all} of {items_all}"
            " item lines dropped, the same share from each page]"
        )
        text = "\n".join([preamble, global_note] + [ln for page in out_pages for ln in page])
        if len(text) <= max_chars:
            return text
    tail = f"\n[truncated: summary cut at {max_chars} chars]"
    if max_chars <= len(tail):
        return tail.strip()[:max_chars]
    return full[: max_chars - len(tail)] + tail
