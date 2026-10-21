from formcast.geometry import points_to_norm
from formcast.pagemap.build import build_form_map, link_labels
from formcast.pagemap.model import Candidate, FormMap, PageMap, Rule, TextLine, Word
from formcast.pagemap.summarize import summarize

PAGE = (0.0, 0.0, 612.0, 792.0)
NAME_BOX = (120.0, 86.0, 300.0, 100.0)
YES_BOX = (72.0, 140.0, 82.0, 150.0)
ADDRESS_BOX = (130.0, 288.0, 500.0, 302.0)


class FakeDetector:
    def __init__(self, name, boxes):
        self.name = name
        self.boxes = [(kind, points_to_norm(rect, PAGE), conf) for kind, rect, conf in boxes]

    def detect(self, image):
        return list(self.boxes)


def _address_map(address_form_pdf):
    det = FakeDetector(
        "FFDNet-L",
        [("text", NAME_BOX, 0.91), ("choice", YES_BOX, 0.4), ("text", ADDRESS_BOX, 0.8)],
    )
    return build_form_map(address_form_pdf, detectors={"FFDNet-L": det})


def test_summary_lists_candidates_with_labels(address_form_pdf):
    text = summarize(_address_map(address_form_pdf))
    assert len(text) <= 24000
    assert "page 0" in text and "digital" in text and "612x792" in text and "text=pdf" in text
    assert 'c0_0 text conf=.91 [120,86,300,100] label(left)="Name:"' in text
    assert 'right="Yes"' in text
    assert 'label(left)="Address" sub="Street|City"' in text
    assert "truncated" not in text


def test_unlabeled_prompt_without_candidate_is_listed(address_form_pdf):
    text = summarize(_address_map(address_form_pdf))
    lines = text.splitlines()
    prompt = [line for line in lines if '"Phone #"' in line]
    assert len(prompt) == 1
    # "Name:" has a candidate next to it, so it is not repeated as a prompt.
    assert not any(line.startswith("l0_") and '"Name:"' in line for line in lines)
    assert lines.index(prompt[0]) > max(
        i for i, line in enumerate(lines) if line.startswith("c0_")
    )


def _caption_page(with_box: bool) -> FormMap:
    rect = (72.0, 560.0, 120.0, 570.0)
    words = [Word(text="Signature", rect=rect, line_id=0, source="pdf")]
    lines = [TextLine(id=0, text="Signature", rect=rect, word_ids=[0], font_size=9)]
    cands = (
        [Candidate(id="c0_0", kind="signature", rect=(72, 530, 300, 559), conf=0.7, detector="t")]
        if with_box else []
    )
    page = PageMap(index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                   words=words, lines=lines, rules=[Rule(rect=(72, 559, 300, 559), orientation="h")],
                   widgets=[], candidates=cands)
    link_labels(page)
    return FormMap(pages=[page])


def test_caption_under_a_rule_without_a_box_is_a_prompt():
    assert '"Signature" +rule_above' in summarize(_caption_page(with_box=False))
    with_box = summarize(_caption_page(with_box=True))
    assert "+rule_above" not in with_box
    assert 'c0_0 signature conf=.70 [72,530,300,559] label(below)="Signature"' in with_box


def test_glyph_characters_are_escaped():
    rect = (30.0, 100.0, 80.0, 110.0)
    words = [Word(text="\x89Yes\uf063", rect=rect, line_id=0, source="pdf")]
    lines = [TextLine(id=0, text="\x89Yes\uf063", rect=rect, word_ids=[0], font_size=10)]
    cands = [Candidate(id="c0_0", kind="choice", rect=(30, 101, 39, 109), conf=0.8, detector="t")]
    page = PageMap(index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                   words=words, lines=lines, rules=[], widgets=[], candidates=cands)
    text = summarize(FormMap(pages=[page]))
    assert 'right="\\u0089Yes\\uf063"' in text
    assert "\x89" not in text


def test_table_cells_are_not_prompts():
    # Grid cells: an amount and a word, each between the row's top and bottom borders.
    cells = [("$10,000", (100.0, 102.0, 135.0, 111.0)), ("Total", (150.0, 102.0, 175.0, 111.0))]
    words = [Word(text=t, rect=r, line_id=i, source="pdf") for i, (t, r) in enumerate(cells)]
    lines = [TextLine(id=i, text=t, rect=r, word_ids=[i], font_size=9)
             for i, (t, r) in enumerate(cells)]
    rules = [Rule(rect=(95, 101, 185, 101), orientation="h"),
             Rule(rect=(95, 112, 185, 112), orientation="h")]
    page = PageMap(index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                   words=words, lines=lines, rules=rules, widgets=[], candidates=[])
    assert "prompts" not in summarize(FormMap(pages=[page]))


def test_fillable_summary_lists_widgets(fillable_form_pdf):
    text = summarize(build_form_map(fillable_form_pdf, detectors={}))
    assert "fillable" in text
    assert 'w0_0 text name="applicant_name" [120,86,300,100] label(left)="Name:"' in text
    assert 'w0_1 checkbox name="agree" [72,140,82,150] right="Yes"' in text


def _big_map(pages=2, per_page=400):
    out = []
    for p in range(pages):
        cands, words, lines = [], [], []
        for i in range(per_page):
            y = 20 + (i % 70) * 10
            x = 20 + (i // 70) * 95
            cands.append(Candidate(id=f"c{p}_{i}", kind="text", rect=(x, y, x + 80, y + 8),
                                   conf=0.5, detector="t"))
        for i in range(30):
            rect = (10, 10 + i * 25, 60, 20 + i * 25)
            words.append(Word(text=f"Q{i}:", rect=rect, line_id=i, source="pdf"))
            lines.append(TextLine(id=i, text=f"Q{i}:", rect=rect, word_ids=[i], font_size=10))
        out.append(PageMap(index=p, width=612, height=792, rotation=0, kind="digital",
                           text_source="pdf", words=words, lines=lines, rules=[], widgets=[],
                           candidates=cands))
    return FormMap(pages=out)


def test_truncation_is_proportional_and_announced():
    fm = _big_map()
    full = summarize(fm, max_chars=10**7)
    assert "truncated" not in full
    text = summarize(fm, max_chars=6000)
    assert len(text) <= 6000
    assert "truncated" in text
    assert "== page 0" in text and "== page 1" in text
    kept = [sum(1 for line in text.splitlines() if line.startswith(f"c{p}_")) for p in (0, 1)]
    assert kept[0] > 0 and abs(kept[0] - kept[1]) <= 2
    assert "dropped" in text


def test_tiny_budget_still_respects_max_chars():
    text = summarize(_big_map(pages=5), max_chars=200)
    assert len(text) <= 200
    assert "truncated" in text
