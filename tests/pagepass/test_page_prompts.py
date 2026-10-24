from __future__ import annotations

from formcast.llm import ImagePart, TextPart
from formcast.pagemap.model import FormMap, PageMap
from formcast.pagepass.marks import marks_for
from formcast.pagepass.prompts import SYSTEM_PROMPT, page_messages
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
    assert "a box marked 4 is c0_4" in note.text
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
