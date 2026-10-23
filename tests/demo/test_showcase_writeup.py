"""The showcase page's write-up: How it works, What the AI agent adds, Results, Credits.

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
REPO = "https://github.com/LandoCole/formcast"

SECTIONS = ["How it works", "What the AI agent adds", "Results", "Credits and code"]
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


def test_credits_link_the_paper_the_models_and_the_repo(parsed):
    credits = parsed.sections[SECTIONS.index("Credits and code")]
    hrefs = [a.get("href") for a in credits.links]
    assert "https://arxiv.org/abs/2509.16506" in hrefs
    assert "https://huggingface.co/jbarrow/FFDNet-S-cpu" in hrefs
    assert "https://huggingface.co/jbarrow/FFDNet-L-cpu" in hrefs
    assert "Joe Barrow" in text_of(credits) and "Apache-2.0" in text_of(credits)
    assert REPO in hrefs
    assert "not public" not in HTML
    assert "Code: github.com/LandoCole/formcast" in " ".join(text_of(parsed.footer).split())
    assert REPO in [a.get("href") for a in parsed.footer.links]
    assert "data-todo" not in HTML
    for s in [*parsed.sections, parsed.footer]:
        for a in s.links:
            assert a.get("href") not in ("#", ""), (s.label, a)
            assert "data-todo" not in a, (s.label, a)


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


def headline(group: str) -> dict:
    return next(r for r in RESULTS["agent_dev"] if r["headline"] and r["group"] == group and r["sessions_of"] is None)


def test_the_results_table_shows_the_headline_run_by_form_type(open_page):
    page = open_page()
    assert page.eval_on_selector("#results", "el => el.dataset.state") == "ready"
    heads = page.eval_on_selector_all("#agent-table thead th", "ths => ths.map(th => th.textContent)")
    assert heads == ["Form type", "Blanks found", "Linked to the right field", "Filled correctly"]
    rows = rows_text(page, "#agent-table")
    assert [r[0] for r in rows] == ["Fillable PDFs", "Flat PDFs", "Scanned copies", "All forms"]
    for row, group in zip(rows, ("fillable", "digital", "scanned", "all")):
        h = headline(group)
        assert row[1:] == [f"{round(h[k] * 100)}%" for k in ("recall_50", "path", "fill_acc")], group


def test_the_lead_says_what_was_tested(open_page):
    page = open_page()
    lead = page.inner_text("#results-lead")
    forms = headline("fillable")["sessions"] + headline("digital")["sessions"]
    assert lead == (
        f"Scored box by box against an answer key on {forms} public forms, "
        f"with Claude {headline('fillable')['label']} as the agent."
    )


def test_every_measure_and_form_type_is_explained(open_page):
    page = open_page()
    terms = page.eval_on_selector_all("#agent-defs dt", "dts => dts.map(dt => dt.textContent)")
    assert terms == ["Blanks found", "Linked to the right field", "Filled correctly",
                     "Fillable PDFs", "Flat PDFs", "Scanned copies"]  # fmt: skip
    assert "found by design" in page.inner_text("#agent-note")


def test_the_results_leave_out_the_development_detail(open_page):
    page = open_page()
    text = page.inner_text("#results")
    for gone in ("held-out", "Held-out", "Caveats", "round", "Luna", "tokens", "@", "results.json", "dev forms"):
        assert gone not in text, gone
    for note in RESULTS["caveats"]:
        assert note not in text


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


@pytest.mark.parametrize("width", [375, 500, 700, 800, 900, 1280])
def test_diagram_labels_stay_near_their_drawn_size(open_page, width):
    page = open_page(viewport=(width, 900), mobile=width < 760)
    shown = page.eval_on_selector_all(
        ".diagram svg",
        "ss => ss.filter(s => getComputedStyle(s).display !== 'none')"
        ".map(s => [s.getBoundingClientRect().width / s.viewBox.baseVal.width, s.classList.contains('tall')])",
    )
    assert len(shown) == 1
    scale, tall = shown[0]
    assert tall == (width <= 860)
    # 10 px sub-labels: never blown up past 1.25x, never shrunk much below 8 px
    assert 0.8 <= scale <= 1.25, (width, scale)


def test_the_agent_section_lists_what_its_tools_are_for_not_results(parsed):
    """The list is the design's job description (main spec 7.1), so it is not worded as shown results,
    and it leaves out "printed text that only looks like a blank": the agent judged that wrongly more
    often than not (main spec 14.3, the not_a_blank round), and that escape was removed."""
    agent = text_of(parsed.sections[SECTIONS.index("What the AI agent adds")])
    assert "Its tools are built for:" in agent
    assert "That is enough to" not in agent
    assert "looks like a blank" not in agent
    assert "How well it does these is measured under Results." in agent
