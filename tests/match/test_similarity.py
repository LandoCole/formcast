"""Form signature and ``similarity``, calibrated on the dev forms only (never forms/heldout/).

The cases: a re-saved copy, a filled copy, a one-line text edit, a fake scan (as the detection
baseline makes them) and different forms. Form maps are built without detectors or OCR, so no
model loads.
"""

import io
import itertools
import sys
import zlib
from pathlib import Path

import pymupdf
import pytest

from formcast import Similarity, similarity
from formcast.match import (
    MINHASH_EMPTY,
    MINHASH_SIZE,
    _minhash,
    compute_signature,
    fingerprint,
    fingerprint_document,
)
from formcast.pagemap.build import DetectPolicy, build_form_map
from formcast.pagemap.ocr import render
from formcast.template.model import Template, new_template

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import baseline_detection  # noqa: E402  (eval/baseline_detection.py: fake_scan)

DEV = ROOT / "forms" / "dev"
FW9, FW4, I9, WADOC, NDGUARD = (DEV / f"{n}.pdf" for n in ("fw9", "fw4", "i9", "wadoc_auth", "ndguard"))
ALL_DEV = sorted(DEV.glob("*.pdf"))
SCHEMA = {"type": "object"}
NO_DETECT = DetectPolicy(digital=(), scanned=())

_templates: dict[Path, Template] = {}


def template_for(pdf: Path) -> Template:
    """A template for a dev form (widget fields only), cached for the module."""
    if pdf not in _templates:
        fm = build_form_map(pdf, detectors={}, policy=NO_DETECT, ocr="never")
        _templates[pdf] = new_template(SCHEMA, fm)
    return _templates[pdf]


def labelled(pdf: Path) -> Template:
    """Like ``template_for`` but every field has a label taken from the form's own text lines,
    standing in for what a detector or an agent would write."""
    fm = build_form_map(pdf, detectors={}, policy=NO_DETECT, ocr="never")
    lines = [ln.text for pm in fm.pages for ln in pm.lines if len(ln.text.split()) >= 2]
    t = new_template(SCHEMA, fm).model_copy(deep=True)
    for f, text in zip(t.fields, lines[3:]):
        f.label = text
    return t


# ---------- variants of a dev form ----------


def resaved(pdf: Path) -> bytes:
    return pymupdf.open(pdf).tobytes(garbage=4, deflate=True)


def filled(pdf: Path) -> bytes:
    doc = pymupdf.open(pdf)
    for page in doc:
        for w in page.widgets() or []:
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
                w.field_value = "Jordan Q. Example 123"
                w.update()
            elif w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                w.field_value = True
                w.update()
    return doc.tobytes()


def edited(pdf: Path) -> bytes:
    """Redact the first text line of four or more words on page 1 and write different text there."""
    doc = pymupdf.open(pdf)
    page = doc[0]
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if len("".join(s["text"] for s in line["spans"]).split()) >= 4:
                rect, size = pymupdf.Rect(line["bbox"]), line["spans"][0]["size"]
                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()
                page.insert_text((rect.x0, rect.y1 - 2), "Revised 3-2027 Cat. No. 99999Z", fontsize=min(size, 9))
                return doc.tobytes(garbage=4, deflate=True)
    raise AssertionError("no text line to edit")


def fake_scan(pdf: Path, form_id: str) -> bytes:
    """Image-only copy: each page rendered, degraded as the detection baseline does, as a JPEG."""
    src, out = pymupdf.open(pdf), pymupdf.open()
    for i, page in enumerate(src):
        img = baseline_detection.fake_scan(render(page, 144), zlib.crc32(f"{form_id}:{i}".encode()))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    return out.tobytes()


@pytest.fixture(scope="module")
def fw9_template():
    return template_for(FW9)


@pytest.fixture(scope="module")
def wadoc_template():
    return template_for(WADOC)


@pytest.fixture(scope="module")
def fw9_filled_bytes():
    return filled(FW9)


@pytest.fixture(scope="module")
def fw9_edited_bytes():
    return edited(FW9)


@pytest.fixture(scope="module")
def wadoc_fake_scan_bytes():
    return fake_scan(WADOC, "wadoc_auth")


# ---------- the signature ----------


def test_signature_shape_and_build_form_map():
    fm = build_form_map(FW9, detectors={}, policy=NO_DETECT, ocr="never")
    sig = fm.signature
    assert sig is not None and sig.version == 1
    assert len(sig.ink) == len(sig.text) == len(fm.pages) == 6
    assert all(len(t) == MINHASH_SIZE for t in sig.text)
    assert template_for(FW9).form.signature == sig


def test_minhash_is_fixed_across_runs_and_machines():
    # Seeded constants: if these change, every stored signature stops matching.
    assert _minhash("name address city state zip code")[:3] == [959601326, 129848419, 219030013]
    assert _minhash("") == [MINHASH_EMPTY] * MINHASH_SIZE


def test_signature_ignores_field_values_and_resaving(fw9_filled_bytes):
    base = compute_signature(pymupdf.open(FW9))
    assert compute_signature(pymupdf.open(stream=fw9_filled_bytes)) == base
    assert compute_signature(pymupdf.open(stream=resaved(FW9))) == base


def test_fingerprint_is_unchanged_by_signatures():
    # fingerprint() keeps its definition: archived templates stay valid.
    assert fingerprint(FW9) == fingerprint_document(pymupdf.open(FW9))
    assert fingerprint(resaved(FW9)) == fingerprint(FW9)


def test_signature_round_trips_in_json(fw9_template):
    assert Template.from_json(fw9_template.to_json()).form.signature == fw9_template.form.signature


def test_templates_without_a_signature_still_load(fw9_template):
    data = fw9_template.model_dump(by_alias=True)
    del data["form"]["signature"]
    assert Template.model_validate(data).form.signature is None


# ---------- similarity ----------


def test_same_pdf_is_one(fw9_template):
    s = similarity(fw9_template, FW9)
    assert s.score == 1.0 and s.basis == "fingerprint" and s.parts["fingerprint"] == 1.0
    assert s.explanation == "same blank form"
    assert isinstance(s, Similarity)


def test_resaved_copy_is_one(fw9_template):
    assert similarity(fw9_template, resaved(FW9)).score == 1.0


def test_filled_copy_is_reuse(fw9_template, fw9_filled_bytes):
    assert similarity(fw9_template, fw9_filled_bytes).score >= 0.95


def test_one_line_edit_is_adapt(fw9_template, fw9_edited_bytes):
    assert fingerprint(fw9_edited_bytes) != fw9_template.form.fingerprint  # a real revision, not a no-op
    s = similarity(fw9_template, fw9_edited_bytes)
    assert s.basis == "signature"
    assert 0.60 <= s.score < 0.95
    assert "text" in s.explanation


def test_one_line_edit_on_a_one_page_form_is_adapt(wadoc_template):
    s = similarity(wadoc_template, edited(WADOC))
    assert 0.60 <= s.score < 0.95


def test_fake_scan_is_adapt_or_better(wadoc_template, wadoc_fake_scan_bytes):
    s = similarity(wadoc_template, wadoc_fake_scan_bytes)
    assert s.score >= 0.60
    assert "text" not in s.parts  # a scan has no text layer to compare
    assert "no text layer" in s.explanation


@pytest.mark.parametrize("pdf", [FW9, FW4, I9, NDGUARD], ids=lambda p: p.stem)
def test_fake_scans_of_other_forms(pdf):
    assert similarity(template_for(pdf), fake_scan(pdf, pdf.stem)).score >= 0.60


def test_different_forms_are_low(fw9_template):
    for other in [FW4, I9, WADOC, NDGUARD]:
        assert similarity(fw9_template, other).score < 0.60, other.name


@pytest.mark.slow  # ~1 minute: every dev form against every other, and against their fake scans
def test_all_dev_pairs_are_low_and_a_scan_never_matches_another_form():
    scans = {p: fake_scan(p, p.stem) for p in ALL_DEV if pymupdf.open(p).page_count <= 6}
    for a, b in itertools.permutations(ALL_DEV, 2):
        assert similarity(template_for(a), b).score < 0.60, (a.name, b.name)
    for a in ALL_DEV:
        for b, scan in scans.items():
            if a != b:
                assert similarity(template_for(a), scan).score < 0.60, (a.name, b.name, "scan")


def test_page_count_mismatch_is_named(fw9_template):
    s = similarity(fw9_template, WADOC)
    assert s.parts["pages"] < 1.0 and "page count differs" in s.explanation


def test_old_template_uses_labels():
    t = labelled(FW9)
    t.form.signature = None
    t.form.fingerprint = "0" * 16
    s = similarity(t, FW9)
    assert s.basis == "labels"
    assert "no stored signature" in s.explanation
    assert s.score >= 0.60


def test_old_template_with_labels_is_low_for_other_forms():
    t = labelled(I9)
    t.form.signature = None
    t.form.fingerprint = None
    assert similarity(t, I9).score >= 0.60
    for other in [FW9, FW4, WADOC, NDGUARD]:
        assert similarity(t, other).score < 0.60, other.name


def test_old_template_without_labels_says_so(fw9_template):
    t = fw9_template.model_copy(deep=True)
    t.form.signature = None
    t.form.fingerprint = None
    s = similarity(t, FW9)
    assert s.basis == "labels" and s.score < 0.60 and "no field labels" in s.explanation


def test_explanation_is_plain(fw9_template, fw9_edited_bytes):
    e = similarity(fw9_template, fw9_edited_bytes).explanation
    assert "page" in e and "%" in e and "layout" in e


def test_bad_pdf_raises_formcast_error(fw9_template):
    from formcast import FormcastError

    with pytest.raises(FormcastError):
        similarity(fw9_template, b"not a pdf")
