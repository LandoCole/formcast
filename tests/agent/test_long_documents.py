"""Long documents: after a page pass, the agent's images and page-map text cover the pages that need it."""

from __future__ import annotations

from formcast.agent.api import LONG_DOCUMENT_PAGES, MAX_AGENT_IMAGE_PAGES, _boxed_pages, _focus
from formcast.pagemap.model import FormMap, PageMap
from formcast.template.model import Field, FormInfo, Source, Template

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def _page(index: int) -> PageMap:
    return PageMap(
        index=index, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
        words=[], lines=[], rules=[], widgets=[], candidates=[],
    )  # fmt: skip


def _template(pages: int, boxes: dict[int, tuple[int, int]]) -> Template:
    """``boxes``: page -> (linked, unlinked) box counts."""
    fields = []
    for page, (linked, unlinked) in boxes.items():
        for i in range(linked + unlinked):
            f = Field(id=f"c{page}_{i}", type="text", page=page, rect=(100, 20 + 20 * i, 300, 35 + 20 * i), origin="detector")
            if i < linked:
                f.source = Source(paths=["a"])
            fields.append(f)
    return Template(
        template_id="t", schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=pages, page_sizes=[(612, 792)] * pages, kinds=["digital"] * pages),
        fields=fields,
    )  # fmt: skip


def test_a_short_document_keeps_every_page_with_boxes():
    fm = FormMap(pages=[_page(i) for i in range(LONG_DOCUMENT_PAGES)])
    assert _focus(_template(LONG_DOCUMENT_PAGES, {n: (1, 1) for n in range(LONG_DOCUMENT_PAGES)}), fm) is None


def test_a_long_document_with_few_form_pages_keeps_them_all():
    fm = FormMap(pages=[_page(i) for i in range(90)])
    assert _focus(_template(90, {40: (2, 3), 41: (5, 0)}), fm) is None


def test_a_long_document_focuses_on_the_pages_the_pass_left_most_unlinked():
    pages = 90
    boxes = {n: (5, 0) for n in range(30, 60)}  # 30 form pages, all linked...
    boxes |= {n: (1, n - 59) for n in range(60, 75)}  # ...and 15 with 1 to 15 unlinked boxes
    tpl, fm = _template(pages, boxes), FormMap(pages=[_page(i) for i in range(pages)])
    focus = _focus(tpl, fm)
    assert focus == set(range(75 - MAX_AGENT_IMAGE_PAGES, 75))
    assert _boxed_pages(tpl, fm) == set(range(30, 75))  # the summary text still covers every page with boxes
