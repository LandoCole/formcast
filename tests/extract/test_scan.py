"""scan(): group a batch of PDFs by layout before any template exists, and pick a sample from a group."""

import itertools
from pathlib import Path

import pymupdf
import pytest

from formcast.errors import FormcastError
from formcast.extract.scan import SCAN_SAME, ScanGroup, ScanReport, sample, scan
from formcast.match import pdf_features, signature_similarity
from tests.extract.corpus import ROTATE, SHIFT, clients, make_form, other_form


@pytest.fixture
def report(corpus_dir):
    return scan(sorted(corpus_dir.glob("*.pdf")))


def test_the_corpus_scans_to_one_main_group_and_the_other_form_apart(corpus_dir):
    report = scan(sorted(corpus_dir.glob("*.pdf")))
    assert len(report.main.files) == 20 and report.main.fillable == 13 and report.main.flattened == 7
    assert sorted(len(g.files) for g in report.groups[1:]) == [2]
    assert report.main.page_count == 2 and report.groups[1].page_count == 6
    assert report.unreadable == [] and report.seconds > 0


def test_progress_is_reported_after_each_document_readable_or_not(corpus_dir, tmp_path):
    files = sorted(corpus_dir.glob("*.pdf"))
    seen: list[tuple[int, int]] = []
    scan(iter(files), on_progress=lambda done, total: seen.append((done, total)))  # a generator has a length too
    assert seen == [(n, 22) for n in range(1, 23)]
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 this is not a pdf")
    seen.clear()
    report = scan([files[0], broken, tmp_path / "missing.pdf"], on_progress=lambda d, t: seen.append((d, t)))
    assert len(report.unreadable) == 2 and seen == [(1, 3), (2, 3), (3, 3)]


def test_the_rotated_and_the_shifted_copy_stay_with_their_own_layout(corpus_dir, report):
    main = {p.rsplit("/", 1)[-1] for p in report.main.files}
    # client_00019 is the shifted flattened copy and client_00020 the rotated fillable one
    assert {"client_00019.pdf", "client_00020.pdf"} <= main
    assert {p.rsplit("/", 1)[-1] for p in report.groups[1].files} == {"client_00021.pdf", "client_00022.pdf"}


def test_scan_accepts_paths_as_strings_and_in_any_order(corpus_dir):
    files = sorted(corpus_dir.glob("*.pdf"), reverse=True)
    report = scan(str(p) for p in files)
    assert len(report.main.files) == 20 and len(report.groups) == 2


def test_groups_are_largest_first_and_numbered(report):
    sizes = [len(g.files) for g in report.groups]
    assert sizes == sorted(sizes, reverse=True)
    assert [g.id for g in report.groups] == [1, 2]
    assert report.main is report.groups[0]


def test_an_empty_scan_has_no_main_group():
    report = scan([])
    assert report.groups == [] and report.main is None and report.unreadable == []


def test_the_same_blind_fingerprint_is_one_group_without_comparing_signatures(tmp_path, monkeypatch):
    for n in range(3):
        (tmp_path / f"blank_{n}.pdf").write_bytes(other_form())
    monkeypatch.setattr("formcast.extract.scan.signature_similarity", lambda a, b: pytest.fail("compared"))
    report = scan(sorted(tmp_path.glob("*.pdf")))
    assert [len(g.files) for g in report.groups] == [3]


def test_a_copy_with_an_extra_page_is_a_group_of_its_own(tmp_path):
    (tmp_path / "a.pdf").write_bytes(make_form(clients(1)[0], kind="flattened"))
    (tmp_path / "b.pdf").write_bytes(make_form(clients(2)[1], kind="flattened"))
    (tmp_path / "c.pdf").write_bytes(make_form(clients(3)[2], kind="flattened", extra_page=True))
    report = scan(sorted(tmp_path.glob("*.pdf")))
    assert [(len(g.files), g.page_count) for g in report.groups] == [(2, 2), (1, 3)]


def test_same_page_count_but_another_page_size_is_another_group(tmp_path):
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=595.0, height=842.0)  # A4 pages, the corpus form's page count
    (tmp_path / "a4.pdf").write_bytes(doc.tobytes())
    (tmp_path / "letter.pdf").write_bytes(make_form(clients(1)[0], kind="flattened"))
    # two blank pages have no text, and their ink agrees with anything blank: only the sizes can tell
    assert sorted(len(g.files) for g in scan(sorted(tmp_path.glob("*.pdf"))).groups) == [1, 1]


def test_a_stricter_threshold_splits_the_group_and_a_zero_one_merges_everything(corpus_dir):
    files = sorted(corpus_dir.glob("*.pdf"))
    assert len(scan(files, same=0.99).groups) > 2
    assert [len(g.files) for g in scan(files, same=0.0).groups if g.page_count == 2] == [20]


def test_first_page_only_groups_the_corpus_the_same_way(corpus_dir):
    report = scan(sorted(corpus_dir.glob("*.pdf")), first_page_only=True)
    assert (len(report.main.files), report.main.fillable, report.main.flattened) == (20, 13, 7)
    assert [len(g.files) for g in report.groups] == [20, 2]


def test_a_copy_with_other_later_pages_is_not_merged_by_first_page_only(tmp_path):
    (tmp_path / "a.pdf").write_bytes(make_form(None, kind="fillable"))
    (tmp_path / "b.pdf").write_bytes(make_form(None, kind="fillable", extra_page=True))
    report = scan(sorted(tmp_path.glob("*.pdf")), first_page_only=True)
    assert sorted(g.page_count for g in report.groups) == [2, 3]


def test_a_corrupt_file_is_listed_as_unreadable_and_the_scan_goes_on(tmp_path):
    (tmp_path / "a.pdf").write_bytes(make_form(clients(1)[0], kind="fillable"))
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    (tmp_path / "empty.pdf").write_bytes(b"")
    doc = pymupdf.open()
    doc.new_page()
    (tmp_path / "locked.pdf").write_bytes(
        doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    )
    report = scan([tmp_path / "a.pdf", tmp_path / "broken.pdf", tmp_path / "empty.pdf", tmp_path / "missing.pdf",
                   tmp_path / "locked.pdf"])
    assert len(report.main.files) == 1
    reasons = {path.rsplit("/", 1)[-1]: why for path, why in report.unreadable}
    assert reasons == {
        "broken.pdf": "cannot open PDF",
        "empty.pdf": "cannot open PDF",
        "missing.pdf": "cannot open PDF",
        "locked.pdf": "encrypted PDF",
    }


def test_scan_never_runs_field_detection(corpus_dir, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("field detection ran during scan()")

    monkeypatch.setattr("formcast.pagemap.build.build_form_map", refuse)
    monkeypatch.setattr("formcast.pagemap.build.detect_on_page", refuse)
    monkeypatch.setattr("formcast.pagemap.detect.detect_page", refuse)
    assert len(scan(sorted(corpus_dir.glob("*.pdf")), first_page_only=True).main.files) == 20


FORMS_DEV = Path(__file__).resolve().parents[2] / "forms" / "dev"
# other layouts that pass the page count and size test against the corpus form: 2 pages of Letter
STRANGERS = ("columbus_medhist.pdf", "f8949.pdf", "csudh_health.pdf")


def blank_letter_pages(count: int = 2) -> bytes:
    doc = pymupdf.open()
    for _ in range(count):
        doc.new_page(width=612.0, height=792.0)
    return doc.tobytes()


def test_scan_same_sits_between_same_layout_pairs_and_other_forms():
    """The calibration SCAN_SAME records: every pair of one layout scores above it, every cross pair below.

    The cross pairs are real forms with the corpus form's page count and sizes, so the scan does compare them (the
    repo's 6-page W-9 never gets as far as the signature).
    """
    people = clients(8)
    docs = {
        "fill": make_form(people[0], kind="fillable"),
        "fill2": make_form(people[1], kind="fillable"),
        "flat": make_form(people[2], kind="flattened"),
        "flat2": make_form(people[3], kind="flattened"),
        "shift": make_form(people[4], kind="flattened", shift=SHIFT),
        "rot": make_form(people[5], kind="fillable", rotate=ROTATE),
        "rot_flat": make_form(people[6], kind="flattened", rotate=ROTATE),
    }
    sigs = {k: pdf_features(v).signature for k, v in docs.items()}
    strangers = []
    for name in STRANGERS:
        features = pdf_features(FORMS_DEV / name)
        assert len(features.page_sizes) == 2
        assert all(abs(w - 612) < 1 and abs(h - 792) < 1 for w, h in features.page_sizes)
        strangers.append(features.signature)
    assert min(signature_similarity(a, b) for a, b in itertools.combinations(sigs.values(), 2)) > SCAN_SAME
    assert max(signature_similarity(s, other) for s in sigs.values() for other in strangers) < SCAN_SAME


def test_other_two_page_letter_forms_stay_out_of_the_main_group(corpus_dir):
    files = sorted(corpus_dir.glob("*.pdf")) + [FORMS_DEV / name for name in STRANGERS]
    report = scan(files)
    assert len(report.main.files) == 20
    assert not {str(FORMS_DEV / name) for name in STRANGERS} & set(report.main.files)


def test_a_blank_two_page_letter_pdf_does_not_join_the_corpus_group(corpus_dir, tmp_path):
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(blank_letter_pages())
    files = sorted(corpus_dir.glob("*.pdf"))
    for order in (files + [blank], [blank] + files):  # a text-less first member must not take a form in either
        report = scan(order)
        assert len(report.main.files) == 20 and str(blank) not in report.main.files
        assert str(blank) in [f for g in report.groups for f in g.files]


def test_a_text_less_group_takes_other_text_less_documents(tmp_path):
    for n in range(2):
        doc = pymupdf.open()
        page = doc.new_page(width=612.0, height=792.0)
        page.draw_rect(pymupdf.Rect(100, 100 + 20 * n, 300, 300), fill=(0, 0, 0))  # ink, but no text
        doc.new_page(width=612.0, height=792.0)
        (tmp_path / f"scan_{n}.pdf").write_bytes(doc.tobytes())
    assert [len(g.files) for g in scan(sorted(tmp_path.glob("*.pdf"))).groups] == [2]


def test_a_groups_closeness_is_its_first_members_to_the_mains(report):
    assert report.main.closeness is None
    assert report.groups[1].closeness is not None and report.groups[1].closeness < 0.1  # the W-9, a stranger


def test_a_group_split_off_by_a_stricter_threshold_is_close_to_the_main_one(corpus_dir):
    report = scan(sorted(corpus_dir.glob("*.pdf"))[:20], same=0.99)  # the client forms only
    assert report.main.closeness is None and len(report.groups) > 2
    assert all(g.closeness is not None and 0.5 < g.closeness < 0.99 for g in report.groups[1:])


# ----------------------------------------------------------------- sample


def kinds(report: ScanReport, picked: list[str]) -> tuple[int, int]:
    fillable = set(report.main.fillable_files)
    return sum(p in fillable for p in picked), sum(p not in fillable for p in picked)


def test_a_spread_sample_mixes_fillable_and_flattened(report):
    picked = sample(report.main, 5)
    assert len(picked) == len(set(picked)) == 5 and set(picked) <= set(report.main.files)
    assert kinds(report, picked) == (3, 2)  # in proportion to 13 and 7
    assert picked == [p for p in report.main.files if p in picked]  # in the group's own order


def test_a_small_spread_sample_still_has_one_of_each(report):
    for n in (2, 3):
        fillable, flattened = kinds(report, sample(report.main, n))
        assert fillable >= 1 and flattened >= 1


def test_a_spread_sample_is_evenly_spaced_within_a_kind(report):
    flat = [p for p in report.main.files if p not in set(report.main.fillable_files)]
    group = ScanGroup(id=1, files=flat, fillable=0, flattened=len(flat), page_count=2)
    assert sample(group, 3) == [flat[1], flat[3], flat[5]]  # spaced over the whole kind, not the first three


def test_a_group_of_one_kind_samples_that_kind(report):
    only = report.groups[1]  # the blank W-9 copies: no widget values
    assert only.fillable == 0
    assert len(sample(only, 1)) == 1 and sample(only, 1)[0] in only.files


def test_a_sample_larger_than_the_group_is_the_whole_group(report):
    assert sample(report.main, 99) == report.main.files


def test_a_first_sample_is_the_first_members(report):
    assert sample(report.main, 3, "first") == report.main.files[:3]


def test_a_sample_of_none_is_refused(report):
    with pytest.raises(FormcastError, match="at least 1"):
        sample(report.main, 0)


def test_a_first_page_scan_reads_each_document_holding_the_pdf_lock(tmp_path, monkeypatch):
    import formcast.extract.scan as scan_mod
    from formcast.pagemap.extract import PDF_LOCK

    owned: list[bool] = []
    real = scan_mod.open_pdf

    def watching(pdf):
        owned.append(PDF_LOCK._is_owned())
        return real(pdf)

    monkeypatch.setattr(scan_mod, "open_pdf", watching)
    paths = []
    for n, client in enumerate(clients(2)):
        paths.append(tmp_path / f"{n}.pdf")
        paths[-1].write_bytes(make_form(client, kind="fillable"))
    scan(paths, first_page_only=True)
    assert owned == [True, True] and not PDF_LOCK._is_owned()
