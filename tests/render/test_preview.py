import io

import pymupdf
from PIL import Image

from formcast.pagemap.model import Candidate, FormMap, PageMap
from formcast.render.preview import preview
from formcast.template.model import Column, Field, FormInfo, Source, TableSegment, Template

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _two_page_pdf(tmp_path, rotate_second=False):
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=12)
    if rotate_second:
        doc[1].set_rotation(90)
    path = tmp_path / "two.pdf"
    doc.save(path)
    doc.close()
    return path


def _template() -> Template:
    seg = TableSegment(page=1, rect=(72, 300, 540, 400), row_top=320, row_height=20, max_rows=4,
                       columns=[Column(key="a", x=(72, 300)), Column(key="b", x=(300, 540))])  # fmt: skip
    return Template(
        template_id="t",
        schema={"type": "object"},
        form=FormInfo(
            fingerprint="", page_count=2, page_sizes=[(612, 792), (612, 792)], kinds=["digital"] * 2
        ),
        fields=[
            Field(id="f1", type="text", label="Full name", page=0, rect=(120, 86, 300, 100),
                  source=Source(paths=["name"])),
            Field(id="f2", type="checkbox", label="Yes", page=0, rect=(72, 140, 82, 150)),
            Field(id="f3", type="signature", page=1, rect=(72, 600, 300, 620)),
            Field(id="t1", type="table", label="Rows", segments=[seg]),
        ],
    )  # fmt: skip


def _pixels(png: bytes) -> Image.Image:
    assert png.startswith(PNG_MAGIC)
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_preview_returns_one_png_per_page(tmp_path):
    pngs = preview(_template(), _two_page_pdf(tmp_path), dpi=72)
    assert len(pngs) == 2
    img = _pixels(pngs[0])
    assert img.size == (612, 792)
    # the text field box outline is drawn in a non-gray colour near its top-left corner
    r, g, b = img.getpixel((200, 86))
    assert not (r == g == b)


def test_preview_dpi_scales_and_rotation_is_respected(tmp_path):
    pngs = preview(_template(), _two_page_pdf(tmp_path, rotate_second=True), dpi=144)
    assert _pixels(pngs[0]).size == (1224, 1584)
    assert _pixels(pngs[1]).size == (1584, 1224)  # rotated page renders landscape


def test_preview_with_candidates(tmp_path):
    cand = Candidate(id="c0_0", kind="text", rect=(300, 500, 500, 520), conf=0.7, detector="fake")
    fm = FormMap(pages=[
        PageMap(index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                words=[], lines=[], rules=[], widgets=[], candidates=[cand]),
        PageMap(index=1, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
                words=[], lines=[], rules=[], widgets=[]),
    ])  # fmt: skip
    plain = preview(_template(), _two_page_pdf(tmp_path), dpi=72)
    with_cands = preview(_template(), _two_page_pdf(tmp_path), dpi=72, show_candidates=fm)
    assert len(with_cands) == 2
    assert _pixels(plain[0]).getpixel((400, 500)) == (255, 255, 255)
    assert _pixels(with_cands[0]).getpixel((400, 500)) != (255, 255, 255)


def test_preview_accepts_pdf_bytes(tmp_path):
    data = _two_page_pdf(tmp_path).read_bytes()
    assert len(preview(_template(), data, dpi=50)) == 2


def test_preview_with_data_renders_the_filled_pdf(tmp_path):
    pdf = _two_page_pdf(tmp_path)
    plain = preview(_template(), pdf, dpi=72)
    filled = preview(_template(), pdf, dpi=72, data={"name": "WWWWWWWWWWWW"})
    assert len(filled) == 2
    assert filled[0] != plain[0]
    # the filled value is drawn inside the f1 box (120, 86, 300, 100)
    a, b = _pixels(plain[0]), _pixels(filled[0])
    diff = [(x, y) for x in range(125, 295, 2) for y in range(88, 99) if a.getpixel((x, y)) != b.getpixel((x, y))]
    assert diff
