"""The showcase page's write-up: How it works, What the AI agent adds, Results, Limits, Credits.

The static checks parse ``demo/web/showcase/index.html``; the rest open the page in Chromium (the
real ``results.json`` and samples, Google Fonts answered with an empty stylesheet, Hugging Face
blocked) and read what the Results section rendered. The copy rules are the portfolio's
``tools/check_site.py`` lists, copied here because Task 15 syncs this page into the site.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHOWCASE = ROOT / "demo" / "web" / "showcase"
HTML = (SHOWCASE / "index.html").read_text(encoding="utf-8")
RESULTS = json.loads((SHOWCASE / "results.json").read_text(encoding="utf-8"))
PAGE = "/demo/web/showcase/index.html"

SECTIONS = ["How it works", "What the AI agent adds", "Results", "Limits", "Credits and code"]
# landon-portfolio/tools/check_site.py
BANNED_WORDS = [
    "revolutioni", "seamless", "empower", "cutting-edge", "game-chang",
    "leverag", "synerg", "happy coding", "keep digging", "world-class",
    "in today's",
]  # fmt: skip
EXCLUDED_TERMS = ["Extractit", "Citigroup", "Ranking_me_crazy", "company_classifier", "PageRank"]
PLACEHOLDER = re.compile(r"\[[A-Z][^\]]*\]")
FIRST_PERSON = re.compile(r"\b(I(?!-)|I'm|I've|me|my|we|we're|our|us)\b")  # "I-9" is a form
DECIMAL = re.compile(r"(?<![-\w.])\d*\.\d+")  # not a licence id such as Apache-2.0
HYPE = ["powerful", "blazing", "effortless", "state-of-the-art", "magic", "incredible", "amazing", "best-in-class"]


class Section:
    def __init__(self, attrs: dict):
        self.attrs = attrs
        self.label = ""
        self.text: list[str] = []  # visible text outside <template>, <script>, <style>
        self.links: list[dict] = []


class Sections(HTMLParser):
    """Collects every ``section.sec`` (its .label text, its visible text and links) and the footer."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self.footer: Section | None = None
        self.stack: list[tuple[str, set[str]]] = []
        self.current: Section | None = None
        self.depth = 0  # stack depth of the open section or footer
        self.in_label = False
        self.label_depth = 0
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        classes = set(a.get("class", "").split())
        if tag in self.VOID:
            return
        self.stack.append((tag, classes))
        if self.current is None and tag == "section" and "sec" in classes and "first" not in classes:
            self.current, self.depth = Section(a), len(self.stack)
            self.sections.append(self.current)
        elif self.current is None and tag == "footer":
            self.current, self.depth = Section(a), len(self.stack)
            self.footer = self.current
        elif self.current is not None:
            if "label" in classes and not self.current.label and not self.in_label:
                self.in_label, self.label_depth = True, len(self.stack)
            if tag in ("template", "script", "style"):
                self.hidden += 1
            if tag == "a":
                self.current.links.append(a)

    def handle_endtag(self, tag):
        if tag in self.VOID or not self.stack:
            return
        while self.stack:
            open_tag, _ = self.stack.pop()
            if self.current is not None and open_tag in ("template", "script", "style"):
                self.hidden -= 1
            if self.in_label and len(self.stack) < self.label_depth:
                self.in_label = False
            if self.current is not None and len(self.stack) < self.depth:
                self.current = None
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.current is None or self.hidden:
            return
        if self.in_label:
            self.current.label += data.strip()
        self.current.text.append(data)


@pytest.fixture(scope="module")
def parsed() -> Sections:
    p = Sections()
    p.feed(HTML)
    p.close()
    return p


def text_of(section: Section) -> str:
    return re.sub(r"\s+", " ", "".join(section.text)).strip()


# ---------------------------------------------------------------- static page


def test_the_write_up_sections_come_in_order_after_the_tool(parsed):
    assert [s.label for s in parsed.sections] == SECTIONS
    assert HTML.index('class="tool"') < HTML.index(">How it works<")
    for s in parsed.sections:
        assert text_of(s) != s.label  # every section has body copy


def test_no_numbers_with_decimals_are_written_into_the_sections(parsed):
    for s in parsed.sections:
        found = DECIMAL.findall(text_of(s))
        assert not found, f"{s.label}: {found} (results come from results.json)"


def test_the_copy_follows_the_site_rules(parsed):
    for s in [*parsed.sections, parsed.footer]:
        text = text_of(s)
        low = text.lower()
        for word in BANNED_WORDS + HYPE:
            assert word not in low, (s.label, word)
        for term in EXCLUDED_TERMS:
            assert term.lower() not in low, (s.label, term)
        assert not PLACEHOLDER.findall(text), s.label
        assert not FIRST_PERSON.findall(text), (s.label, FIRST_PERSON.findall(text))
    for term in EXCLUDED_TERMS:
        assert term.lower() not in HTML.lower()


def test_credits_link_the_paper_the_models_and_the_pending_repo(parsed):
    credits = parsed.sections[SECTIONS.index("Credits and code")]
    hrefs = [a.get("href") for a in credits.links]
    assert "https://arxiv.org/abs/2509.16506" in hrefs
    assert "https://huggingface.co/jbarrow/FFDNet-S-cpu" in hrefs
    assert "https://huggingface.co/jbarrow/FFDNet-L-cpu" in hrefs
    assert "Joe Barrow" in text_of(credits) and "Apache-2.0" in text_of(credits)
    repo = [a for a in credits.links if a.get("id") == "repo-link"]
    assert len(repo) == 1 and repo[0]["href"] == "#" and "data-todo" in repo[0]
    # the site's rule: an href="#" link is a pending item and carries data-todo
    for s in [*parsed.sections, parsed.footer]:
        for a in s.links:
            if a.get("href") == "#":
                assert "data-todo" in a, (s.label, a)


def test_the_footer_matches_the_other_demo_pages(parsed):
    assert parsed.footer is not None and "foot" in parsed.footer.attrs.get("class", "")
    assert "CommonForms" in text_of(parsed.footer)


def test_the_diagram_is_inline_svg_in_drafting_style():
    how = HTML[HTML.index(">How it works<") : HTML.index(">What the AI agent adds<")]
    assert how.count("<svg") == 2  # one laid out for wide screens, one for phones
    assert 'role="img"' in how and "aria-label=" in how
    assert "<img" not in how and "fill=\"#" not in how and "stroke=\"#" not in how  # colours come from tokens
    css = (SHOWCASE / "showcase.css").read_text(encoding="utf-8")
    assert re.search(r"\.diagram [^{]*\{[^}]*stroke: var\(--ink\)[^}]*stroke-width: 1;", css)
    assert re.search(r"\.diagram [^{]*\{[^}]*font-family: var\(--mono\)", css)


# ---------------------------------------------------------------- rendered page


@pytest.fixture
def open_page(server, browser):
    contexts = []

    def _open(viewport=(1280, 900), mobile=False, color_scheme="light", results_route=None):
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            is_mobile=mobile,
            has_touch=mobile,
            color_scheme=color_scheme,
        )
        contexts.append(context)
        context.route(
            re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"),
            lambda route: route.fulfill(status=200, body="", content_type="text/css"),
        )
        context.route(re.compile(r"https://huggingface\.co/.*"), lambda route: route.abort())
        if results_route is not None:
            context.route(re.compile(r".*/showcase/results\.json$"), results_route)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{server}{PAGE}")
        page.wait_for_function("document.querySelector('#results').dataset.state !== 'loading'", timeout=10_000)
        page.errors = errors
        return page

    yield _open
    for c in contexts:
        c.close()


CELLS = "[...tr.cells].map(c => [...c.childNodes].map(n => n.textContent.trim()).join(' ').trim())"


def rows_text(page, selector: str, body: str = "tbody") -> list[list[str]]:
    """Each data row's cells as text (a row header's name and detail joined by a space), CSS case aside."""
    return page.eval_on_selector_all(f"{selector} {body} tr:not(.grp)", f"trs => trs.map(tr => {CELLS})")


def agent(run: str, group: str, sessions_of: str | None = None) -> dict:
    return next(
        r for r in RESULTS["agent_dev"] if r["run"] == run and r["group"] == group and r["sessions_of"] == sessions_of
    )


def test_the_agent_table_shows_the_headline_and_the_comparison_rows(open_page):
    page = open_page()
    assert page.eval_on_selector("#results", "el => el.dataset.state") == "ready"
    rows = rows_text(page, "#agent-table")
    assert len(rows) == len(RESULTS["agent_dev"])  # every group and run
    head = agent("2026-10-21_defer-placement", "all")
    shown = page.eval_on_selector_all(
        "#agent-table tbody tr.pick", "trs => trs.map(tr => tr.closest('tbody').dataset.group)"
    )
    assert shown == ["all", "fillable", "digital", "scanned"]
    first = page.eval_on_selector("#agent-table tbody[data-group='all'] tr.pick", f"tr => {CELLS}")
    assert first[0].startswith("Haiku 5.5") and "current code" in first[0]
    want = [str(head["sessions"]), *(f"{head[k]:.2f}" for k in ("recall_50", "precision_30", "path", "joint", "fill_acc"))]
    assert first[1:7] == want
    assert first[7] == f"{head['steps']:.1f}" and first[8] == f"{round(head['input_tokens'] / 1000)}K"
    labels = " | ".join(r[0] for r in rows)
    for run in RESULTS["agent_dev"]:
        assert run["detail"] in labels
    # the paired Luna row sits right after the partial round
    all_rows = [r[0] for r in rows_text(page, "#agent-table", "tbody[data-group='all']")]
    i = next(i for i, r in enumerate(all_rows) if "14 of 24 sessions" in r)
    assert "round 1 on the same 14 sessions" in all_rows[i + 1]


def test_explanations_and_caveats_come_from_results_json(open_page):
    page = open_page()
    results = page.inner_text("#results")
    for caveat in RESULTS["caveats"]:
        assert caveat in results
    for part in ("groups", "metrics", "detectors"):
        for entry in RESULTS[part].values():
            assert entry["text"] in results, entry
    for key, note in RESULTS["notes"].items():
        for text in note if isinstance(note, list) else [note]:
            assert text in results, key
    assert "14 of 24 sessions" in results
    caveats_after_table = page.evaluate(
        "() => !!(document.querySelector('#agent-table').compareDocumentPosition(document.querySelector('#caveats'))"
        " & Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    assert caveats_after_table
    for source in RESULTS["generated_from"]:
        assert source in results


def test_the_heldout_status_is_stated_plainly(open_page):
    page = open_page()
    text = page.inner_text("#heldout")
    assert "1 of 9" in text
    assert RESULTS["heldout_status"]["note"] in text
    assert RESULTS["heldout_status"]["source"] in text


def test_the_browser_models_table_shows_all_three_setups(open_page):
    page = open_page()
    rows = rows_text(page, "#browser-table")
    assert len(rows) == len(RESULTS["browser_models"]) == 12
    for detector in ("FFDNet-L", "FFDNet-L-onnx", "FFDNet-S-onnx"):
        label = RESULTS["detectors"][detector]["label"]
        assert sum(1 for r in rows if r[0].startswith(label)) == 4, detector
    s = next(
        r for r in RESULTS["browser_models"]
        if (r["kind"], r["variant"], r["detector"]) == ("flat", "fake_scan", "FFDNet-S-onnx")
    )  # fmt: skip
    groups = page.eval_on_selector_all("#browser-table tbody", "bs => bs.map(b => b.querySelector('tr.grp').textContent)")
    i = groups.index(s["label"])
    row = rows_text(page, "#browser-table", f"tbody:nth-of-type({i + 1})")[2]
    assert row[1:] == [f"{s[k]:.2f}" for k in ("recall_50", "recall_30", "precision_30")]


def test_the_detection_tables_cover_both_splits(open_page):
    page = open_page()
    assert len(rows_text(page, "#detection-dev")) == len(RESULTS["detection"]["dev"])
    assert len(rows_text(page, "#detection-heldout")) == len(RESULTS["detection"]["heldout"])
    assert "Native scan" in page.text_content("#detection-heldout")


def test_the_lead_and_reading_are_worked_out_from_the_numbers(open_page):
    page = open_page()
    head = agent("2026-10-21_defer-placement", "all")
    lead = page.inner_text("#results-lead")
    assert f"{round(head['path'] * 100)}%" in lead
    for g in ("fillable", "digital", "scanned"):
        assert f"{round(agent('2026-10-21_defer-placement', g)['path'] * 100)}%" in lead
    reading = page.inner_text("#agent-reading")
    assert f"{head['recall_50']:.2f}" in reading and f"{agent('2026-10-21_seed-fill', 'all')['recall_50']:.2f}" in reading


def test_the_rendered_copy_follows_the_site_rules(open_page):
    page = open_page()
    text = page.inner_text("main")
    low = text.lower()
    for word in BANNED_WORDS + HYPE:
        assert word not in low, word
    assert not FIRST_PERSON.findall(page.inner_text("#results"))
    assert not page.errors


def test_a_missing_results_file_says_so(open_page):
    page = open_page(results_route=lambda route: route.fulfill(status=404, body="not found"))
    assert page.eval_on_selector("#results", "el => el.dataset.state") == "failed"
    assert "couldn't be loaded" in page.inner_text("#results-lead")
    assert page.inner_text("#agent-table") == ""
    assert not page.errors


def test_no_horizontal_scroll_at_375_px(open_page):
    page = open_page(viewport=(375, 812), mobile=True)
    assert page.evaluate("innerWidth") == 375
    scroll = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
    assert scroll <= 375, f"page scrolls sideways: {scroll}"
    boxes = page.eval_on_selector_all(".tablebox", "bs => bs.map(b => [b.clientWidth, b.scrollWidth, getComputedStyle(b).overflowX])")
    assert boxes and all(cw <= 343 and ox == "auto" for cw, _, ox in boxes)
    assert any(sw > cw for cw, sw, _ in boxes)  # the wide tables scroll inside their box
    shown = page.eval_on_selector_all(".diagram svg", "ss => ss.map(s => getComputedStyle(s).display)")
    assert shown == ["none", "block"]  # the phone layout of the diagram


def test_the_diagram_and_tables_follow_the_dark_theme(open_page):
    light = open_page()
    dark = open_page(color_scheme="dark")
    stroke = "el => getComputedStyle(el).stroke"
    assert light.eval_on_selector(".diagram .wide .bx", stroke) == "rgb(16, 15, 15)"
    assert dark.eval_on_selector(".diagram .wide .bx", stroke) == "rgb(206, 205, 195)"
    assert dark.eval_on_selector(".diagram .wide .t", "el => getComputedStyle(el).fill") == "rgb(206, 205, 195)"
    assert dark.eval_on_selector("#agent-table td", "el => getComputedStyle(el).color") == "rgb(206, 205, 195)"
