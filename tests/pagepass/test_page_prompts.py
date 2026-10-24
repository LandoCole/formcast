from __future__ import annotations

from formcast.llm import ImagePart, TextPart
from formcast.pagemap.model import FormMap, PageMap
from formcast.pagepass.marks import marks_for
from formcast.pagepass.prompts import IMAGE_NOTE, IMAGE_NOTE_FULL_IDS, SYSTEM_PROMPT, page_messages
from formcast.template.model import Field, FormInfo, Template

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}


def _page(index: int) -> PageMap:
    return PageMap(
        index=index, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
        words=[], lines=[], rules=[], widgets=[], candidates=[],
    )  # fmt: skip


def test_page_messages_carry_image_note_and_page_text():
    fields = [
        Field(id="c0_4", type="text", page=0, rect=(100, 100, 200, 120)),
        Field(id="c0_9", type="checkbox", page=0, rect=(100, 140, 112, 152)),
        Field(id="c1_7", type="text", page=1, rect=(100, 100, 200, 120)),
    ]
    template = Template(
        template_id="t", schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=2, page_sizes=[(612, 792)] * 2, kinds=["digital"] * 2),
        fields=fields,
    )  # fmt: skip
    marks = marks_for(fields[:2])
    system, user = page_messages(template, FormMap(pages=[_page(0), _page(1)]), 0, b"png-bytes", marks)
    assert (system.role, system.content) == ("system", SYSTEM_PROMPT)
    assert user.role == "user"
    image, note, text = user.content
    assert [type(p) for p in user.content] == [ImagePart, TextPart, TextPart]
    assert image.data == b"png-bytes"
    assert note.text == IMAGE_NOTE.format(example="a box marked 4 is c0_4")
    assert "The number drawn in or next to a box is the part of its id after the underscore: on this page a box marked 4 is c0_4. Use the image" in note.text
    assert "SCHEMA PATHS:\n- name: string" in text.text
    assert "TRANSFORMS:\n- " in text.text
    assert "BOXES ON THIS PAGE (2; ids as in the page map):\n- c0_4: text\n- c0_9: checkbox" in text.text
    assert "c1_7" not in text.text
    assert "PAGE MAP (extracted text and geometry, page 0):" in text.text
    assert "== page 0 |" in text.text and "== page 1 |" not in text.text


def test_system_prompt_has_the_table_block_rule_and_no_agent_aside():
    assert "a block is done only when every line with boxes for that record is in a table." in " ".join(
        SYSTEM_PROMPT.split()
    )
    assert "formcast agent" not in SYSTEM_PROMPT


def test_note_names_full_ids_when_marks_fell_back_to_them():
    fields = [
        Field(id="w0_3", type="text", page=0, rect=(100, 100, 200, 120)),
        Field(id="c0_3", type="text", page=0, rect=(100, 140, 200, 160)),
    ]
    template = Template(
        template_id="t", schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]),
        fields=fields,
    )  # fmt: skip
    marks = marks_for(fields)
    assert marks == {"w0_3": "w0_3", "c0_3": "c0_3"}
    _, user = page_messages(template, FormMap(pages=[_page(0)]), 0, b"png", marks)
    assert user.content[1].text == IMAGE_NOTE_FULL_IDS.format(example="a box marked w0_3 is w0_3")
    assert "after the underscore" not in user.content[1].text


def test_system_prompt_has_split_value_rule():
    sentence = (
        "A record value split over several boxes on one printed line (an area code and a number, a month "
        "and a year) gets one table per part, all with the same list path and column key, each column with "
        "the transform that picks its part (slice, date_part)."
    )
    assert sentence in " ".join(SYSTEM_PROMPT.split())


# --- final review: each page's call carries its whole page map ---------------------------------------


def _dense_page(index: int, n: int) -> PageMap:
    from formcast.pagemap.model import Candidate

    cands = [
        Candidate(id=f"c{index}_{i}", kind="text", rect=(50, 10 + i * 3, 250, 12 + i * 3), conf=0.9, detector="fake")
        for i in range(n)
    ]
    return _page(index).model_copy(update={"candidates": cands})


def _page_map_text(form_map: FormMap, page: int) -> str:
    template = Template(
        template_id="t", schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=len(form_map.pages), page_sizes=[(612, 792)] * len(form_map.pages),
                      kinds=["digital"] * len(form_map.pages)),
        fields=[Field(id=f"c{page}_0", type="text", page=page, rect=(50, 10, 250, 12))],
    )  # fmt: skip
    _, user = page_messages(template, form_map, page, b"png", {f"c{page}_0": "0"})
    return user.content[2].text.split(f"PAGE MAP (extracted text and geometry, page {page}):\n", 1)[1]


def test_a_page_gets_its_whole_page_map_when_the_form_summary_is_truncated():
    from formcast.pagemap.summarize import _page_body, _page_header, summarize

    fm = FormMap(pages=[_dense_page(i, 250) for i in range(3)])
    assert "[truncated" in summarize(fm)  # the whole form is over 24000 chars
    text = _page_map_text(fm, 1)
    page = fm.pages[1]
    for line in _page_header(page) + _page_body(page):
        assert line in text.splitlines()
    assert "== page 0 |" not in text and "== page 2 |" not in text
    assert "truncated" not in text and "query helpers" not in text
    assert text.startswith("FORM MAP: 3 page(s).")


def test_a_page_longer_than_the_cap_is_cut_with_a_note_that_names_no_query_helpers():
    fm = FormMap(pages=[_dense_page(0, 900)])
    text = _page_map_text(fm, 0).rstrip("\n")
    assert len(text) <= 24000
    note = text.splitlines()[-1]
    assert note.startswith("[truncated: ") and note.endswith(" of 900 item lines on this page dropped]")
    assert "query helpers" not in text and "c0_0 text" in text
