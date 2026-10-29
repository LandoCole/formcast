"""extract(): one document read with a template, every path ending in a status and never a silent wrong value."""

import copy
import dataclasses
import datetime as dt
import time
from collections import Counter

import pymupdf
import pytest

from formcast.extract.layout import LAYOUT_MATCH, layout_score
from formcast.extract.model import AnchorRead, BoxRead, ExtractionTemplate, Layout, Reader, concrete_paths
from formcast.extract.page import DocText
from formcast.extract.read import extract
from formcast.match import pdf_features
from tests.extract.corpus import (
    FIELD_RECTS,
    OWNER_ROWS,
    ROTATE,
    SHIFT,
    Client,
    clients,
    hand_template,
    make_form,
    other_form,
)

COUNTRY_CODES = {"United States": "US", "USA": "US", "U.S.": "US", "Canada": "CA"}


@pytest.fixture(scope="module")
def template() -> ExtractionTemplate:
    return hand_template()


def truth(client: Client) -> dict[str, str]:
    """What each path should read for ``client``; owner rows the client lacks are not in it."""
    m, d, y = client.dob.split("/")
    out = {
        "investor.legal_name": client.legal_name,
        "investor.tax_id": client.tax_id,
        "investor.dob": dt.date(int(y), int(m), int(d)).isoformat(),
        "investor.type": client.investor_type,
        "investor.country": COUNTRY_CODES[client.country],
        "address.street": client.street,
        "address.city_state_zip": client.city_state_zip,
        "wire.bank": client.bank,
        "wire.account": client.account,
    }
    for i, (name, percent) in enumerate(client.owners):
        out[f"owners[{i}].name"] = name
        out[f"owners[{i}].percent"] = percent
    return out


def fields(result):
    return {f.path: f for f in result.fields}


def with_reader(template: ExtractionTemplate, path: str, *strategies) -> ExtractionTemplate:
    readers = {**template.readers, path: Reader(strategies=list(strategies), clean=template.readers[path].clean)}
    return template.model_copy(update={"readers": readers})


def anchor_only(template: ExtractionTemplate) -> ExtractionTemplate:
    """The template with the tax ID read by its label alone."""
    anchor = template.readers["investor.tax_id"].strategies[-1]
    assert isinstance(anchor, AnchorRead) and anchor.labels == ["Taxpayer ID", "TIN"]
    return with_reader(template, "investor.tax_id", anchor)


CLIENT = dataclasses.replace(clients(1, seed=5)[0], tax_id="84-2219037", owners=[("Dana Keller", "40%")])


def assert_reads(result, client: Client) -> None:
    got = fields(result)
    want = truth(client)
    for path, value in want.items():
        assert got[path].status == "found", (path, got[path])
        assert got[path].value == value, path
    for i in range(len(client.owners), OWNER_ROWS):
        assert got[f"owners[{i}].name"].status == "absent"


def test_a_fillable_copy_reads_every_field_from_its_widgets(template):
    # the wire paragraph is page text with the client's bank in it, so a filled copy is not the blank's fingerprint
    result = extract(template, make_form(CLIENT, kind="fillable"), file="a.pdf")
    assert result.matched == "layout" and result.error is None and result.file == "a.pdf"
    assert result.template_id == "client_information"
    assert [f.path for f in result.fields] == concrete_paths(template.schema_)
    assert_reads(result, CLIENT)
    got = fields(result)
    for path in truth(CLIENT):
        expected = "phrase" if path.startswith("wire.") else "choice" if path == "investor.type" else "widget"
        assert got[path].strategy == expected, path
    assert got["investor.dob"].raw == CLIENT.dob  # the raw text is kept beside the cleaned value
    assert got["investor.country"].raw == CLIENT.country
    record = result.record()
    assert record["investor"]["tax_id"] == "84-2219037"
    assert record["owners"] == [{"name": "Dana Keller", "percent": "40%"}]
    assert dt.datetime.fromisoformat(result.extracted_at).tzinfo is not None


def test_a_copy_with_the_templates_fingerprint_matches_by_fingerprint_and_reads_boxes_in_place(template):
    pdf = make_form(CLIENT, kind="fillable")
    form = template.form.model_copy(update={"fingerprint": pdf_features(pdf).fingerprint_blind})
    same = template.model_copy(update={"form": form})
    by_box = with_reader(same, "investor.tax_id", BoxRead(page=0, rect=FIELD_RECTS["tax_id"]))
    result = extract(by_box, pdf)
    assert result.matched == "fingerprint"
    assert_reads(result, CLIENT)
    tax_id = fields(result)["investor.tax_id"]
    assert (tax_id.strategy, tax_id.rect) == ("box", FIELD_RECTS["tax_id"])


def test_a_flattened_copy_reads_by_box_on_a_matching_layout(template):
    result = extract(template, make_form(CLIENT, kind="flattened"))
    assert result.matched == "layout"
    assert_reads(result, CLIENT)
    got = fields(result)
    assert got["investor.tax_id"].strategy == "box"
    assert got["investor.tax_id"].page == 0 and got["investor.tax_id"].rect == FIELD_RECTS["tax_id"]
    assert got["owners[0].name"].strategy == "box"
    assert got["investor.type"].strategy == "choice" and got["investor.type"].page == 0


def test_a_shifted_flattened_copy_still_reads_by_box_after_the_offset(template):
    result = extract(template, make_form(CLIENT, kind="flattened", shift=SHIFT))
    assert result.matched == "layout"
    assert_reads(result, CLIENT)
    tax_id = fields(result)["investor.tax_id"]
    assert tax_id.strategy == "box"
    assert tax_id.rect[1] == pytest.approx(FIELD_RECTS["tax_id"][1] + SHIFT, abs=1.0)


def test_a_painted_fillable_copy_reads_each_value_once(template):
    painted = make_form(CLIENT, kind="fillable", painted=True)
    result = extract(template, painted)
    assert_reads(result, CLIENT)
    tax_id = fields(result)["investor.tax_id"]
    assert (tax_id.strategy, tax_id.raw) == ("widget", "84-2219037")
    # read by its box, the value painted under the widget and drawn by the widget's appearance is still one value
    by_box = with_reader(template, "investor.tax_id", BoxRead(page=0, rect=FIELD_RECTS["tax_id"]))
    tax_id = fields(extract(by_box, painted))["investor.tax_id"]
    assert (tax_id.strategy, tax_id.raw, tax_id.status) == ("box", "84-2219037", "found")


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_a_bad_tax_id_fails_its_check_and_keeps_its_value(template, kind):
    client = dataclasses.replace(CLIENT, tax_id="8422-19037")
    tax_id = fields(extract(template, make_form(client, kind=kind)))["investor.tax_id"]
    assert tax_id.status == "failed_check"
    assert tax_id.raw == "8422-19037" and tax_id.value == "8422-19037"
    assert any(r.startswith("'8422-19037' does not match") for r in tax_id.reasons)


def test_a_failed_first_read_gives_way_to_a_later_one_that_passes(template):
    # a first strategy aimed at the wrong label reads the legal name, which fails the pattern; the box then passes
    strategies = template.readers["investor.tax_id"].strategies
    wrong = AnchorRead(labels=["Investor legal name"], page=0)
    wrong_first = with_reader(template, "investor.tax_id", wrong, *strategies)
    tax_id = fields(extract(wrong_first, make_form(CLIENT, kind="flattened")))["investor.tax_id"]
    # the two reads disagree, so the one that passes is not trusted outright
    assert (tax_id.status, tax_id.strategy, tax_id.value) == ("unsure", "box", "84-2219037")
    assert tax_id.reasons == [f"the first read (anchor) gave {CLIENT.legal_name!r}, which failed its checks"]


def test_a_fillable_copy_whose_widget_disagrees_with_its_drawn_text_is_unsure(template):
    # the widget's value was changed without redrawing it: the value fails, the drawn text passes
    doc = pymupdf.open(stream=make_form(CLIENT, kind="fillable"))
    for w in doc[0].widgets():
        if w.field_name == "tax_id":
            doc.xref_set_key(w.xref, "V", "(8422-19037)")
    stale = doc.tobytes()
    doc.close()
    tax_id = fields(extract(template, stale))["investor.tax_id"]
    assert (tax_id.status, tax_id.strategy, tax_id.raw) == ("unsure", "box", "84-2219037")
    assert tax_id.reasons == ["the first read (widget) gave '8422-19037', which failed its checks"]


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_a_missing_required_field_is_missing_with_a_reason(template, kind):
    client = dataclasses.replace(CLIENT, tax_id="")
    tax_id = fields(extract(template, make_form(client, kind=kind)))["investor.tax_id"]
    assert tax_id.status == "missing" and tax_id.raw is None and tax_id.value is None
    assert tax_id.reasons == ["'tax_id' is a required property"]


def test_a_blank_form_reports_the_required_tax_id_missing(template):
    result = extract(template, make_form(None, kind="fillable"))
    assert result.matched == "fingerprint"  # the template's form is the blank's
    got = fields(result)
    assert got["investor.tax_id"].status == "missing"
    assert got["investor.tax_id"].reasons == ["'tax_id' is a required property"]
    assert got["investor.legal_name"].status == "missing" and got["investor.legal_name"].reasons == []
    assert all(got[f"owners[{i}].name"].status == "absent" for i in range(OWNER_ROWS))


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_two_checked_type_boxes_fail_the_enum_check(template, kind):
    copy = make_form(CLIENT, kind=kind, checked=("individual", "joint"))
    kind_of = fields(extract(template, copy))["investor.type"]
    assert kind_of.status == "failed_check" and kind_of.strategy == "choice"
    assert kind_of.value == "individual, joint" and kind_of.raw == "individual, joint"
    assert "2 boxes checked" in kind_of.reasons
    assert any("is not one of" in r for r in kind_of.reasons)


def test_no_checked_type_box_reads_nothing(template):
    kind_of = fields(extract(template, make_form(CLIENT, kind="flattened", checked=())))["investor.type"]
    assert (kind_of.status, kind_of.raw) == ("missing", None)


def test_a_label_found_twice_makes_the_anchor_read_unsure(template):
    copy = make_form(CLIENT, kind="flattened", header="Taxpayer ID:")
    tax_id = fields(extract(anchor_only(template), copy))["investor.tax_id"]
    assert (tax_id.status, tax_id.strategy, tax_id.value) == ("unsure", "anchor", "84-2219037")
    assert tax_id.reasons == ["label found in 2 places"]
    # the same read with the label printed once is trusted
    tax_id = fields(extract(anchor_only(template), make_form(CLIENT, kind="flattened")))["investor.tax_id"]
    assert (tax_id.status, tax_id.strategy, tax_id.reasons) == ("found", "anchor", [])


def test_two_wordings_found_once_each_count_as_two_places(template):
    copy = make_form(CLIENT, kind="flattened", header="TIN:")
    tax_id = fields(extract(anchor_only(template), copy))["investor.tax_id"]
    assert tax_id.status == "unsure"


def test_a_copy_that_words_the_label_differently_reads_by_its_other_wording(template):
    copy = make_form(CLIENT, kind="flattened", labels={"Taxpayer ID:": "TIN:"})
    result = extract(anchor_only(template), copy)
    assert result.matched == "layout"
    tax_id = fields(result)["investor.tax_id"]
    assert (tax_id.status, tax_id.strategy, tax_id.value) == ("found", "anchor", "84-2219037")


def test_on_an_unmatched_layout_label_reads_are_unsure_and_boxes_do_not_run(template):
    unmatched = template.model_copy(update={"layout": Layout(lines=[], page_sizes=[])})
    result = extract(unmatched, make_form(CLIENT, kind="flattened"))
    assert result.matched == "none"
    got = fields(result)
    assert (got["investor.tax_id"].status, got["investor.tax_id"].strategy) == ("unsure", "anchor")
    assert got["investor.tax_id"].reasons == ["layout not matched"]
    assert got["wire.account"].status == "unsure" and got["wire.account"].reasons == ["layout not matched"]
    assert got["owners[0].name"].status == "absent"  # read by widget or box only: neither runs here
    assert got["investor.type"].status == "missing"  # drawn boxes are places on a matching layout only
    assert not any(f.status == "found" for f in result.fields)


def test_an_unknown_country_wording_fails_the_allowed_answers_check(template):
    client = dataclasses.replace(CLIENT, country="Untied States")
    country = fields(extract(template, make_form(client, kind="fillable")))["investor.country"]
    assert country.status == "failed_check"
    assert country.raw == "Untied States" and country.value == "Untied States"
    assert any("is not one of" in r for r in country.reasons)


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_empty_owner_rows_are_absent_not_missing(template, kind):
    client = dataclasses.replace(CLIENT, investor_type="individual", owners=[])
    result = extract(template, make_form(client, kind=kind))
    got = fields(result)
    for i in range(OWNER_ROWS):
        assert got[f"owners[{i}].name"].status == "absent" and got[f"owners[{i}].percent"].status == "absent"
    assert "owners" not in result.record()


def test_a_check_on_a_later_owner_row_lands_on_that_row(template):
    # row 1 empty, row 2 with a bad percent, row 3 with no name: the record has two items, the reasons keep their rows
    schema = copy.deepcopy(template.schema_)
    items = schema["properties"]["owners"]["items"]
    items["properties"]["percent"]["pattern"] = r"^\d+%$"
    items["required"] = ["name"]
    checked = ExtractionTemplate.model_validate({**template.model_dump(by_alias=True), "schema": schema})
    client = dataclasses.replace(CLIENT, owners=[("", ""), ("Dana Keller", "4O%"), ("", "10%")])
    result = extract(checked, make_form(client, kind="fillable"))
    got = fields(result)
    assert got["owners[0].name"].status == "absent" and got["owners[0].percent"].status == "absent"
    assert got["owners[1].name"].status == "found"
    assert got["owners[1].percent"].status == "failed_check" and got["owners[1].percent"].raw == "4O%"
    assert any("does not match" in r for r in got["owners[1].percent"].reasons)
    assert got["owners[2].name"].status == "missing"
    assert got["owners[2].name"].reasons == ["'name' is a required property"]
    assert got["owners[2].percent"].status == "found" and got["owners[2].percent"].reasons == []
    assert got["owners[3].name"].status == "absent"
    assert result.record()["owners"] == [{"name": "Dana Keller", "percent": "4O%"}, {"percent": "10%"}]


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_a_rotated_copy_and_a_copy_with_an_extra_page_read_like_the_original(template, kind):
    def summary(pdf: bytes):
        return [(f.path, f.status, f.value) for f in extract(template, pdf).fields]

    original = summary(make_form(CLIENT, kind=kind))
    for variant in (
        make_form(CLIENT, kind=kind, rotate=ROTATE),
        make_form(CLIENT, kind=kind, extra_page=True),
        make_form(CLIENT, kind=kind, rotate=ROTATE, extra_page=True, shift=12),
    ):
        result = extract(template, variant)
        assert result.matched in ("fingerprint", "layout")
        assert summary(variant) == original
    assert_reads(extract(template, make_form(CLIENT, kind=kind, rotate=ROTATE)), CLIENT)


def test_a_path_marked_absent_is_not_on_form(template):
    marked = ExtractionTemplate.model_validate({**template.model_dump(by_alias=True), "absent": ["investor.country"]})
    country = fields(extract(marked, make_form(CLIENT, kind="fillable")))["investor.country"]
    assert (country.status, country.raw, country.value, country.strategy) == ("not_on_form", None, None, None)
    assert "country" not in extract(marked, make_form(CLIENT, kind="fillable")).record()["investor"]


@pytest.mark.parametrize("bad", [b"", b"not a pdf", "missing.pdf"])
def test_an_unopenable_pdf_gives_an_error_result_and_does_not_raise(template, bad, tmp_path):
    pdf = tmp_path / bad if isinstance(bad, str) else bad
    result = extract(template, pdf, doc_id="d1", file="x.pdf")
    assert (result.matched, result.fields, result.doc_id, result.file) == ("none", [], "d1", "x.pdf")
    assert result.error == "cannot open PDF"
    assert result.template_id == "client_information"


def test_an_encrypted_pdf_gives_an_error_result(template):
    doc = pymupdf.open(stream=make_form(CLIENT, kind="flattened"))
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    result = extract(template, locked)
    assert (result.matched, result.error, result.fields) == ("none", "encrypted PDF", [])
    assert result.doc_id  # a doc id even for a file that cannot be read


def test_a_document_mupdf_fails_on_while_read_is_unreadable(template, monkeypatch):
    def broken(self, page, rect):
        raise pymupdf.FileDataError("broken content stream")

    monkeypatch.setattr(DocText, "text_in", broken)
    result = extract(template, make_form(CLIENT, kind="flattened"), doc_id="d1")
    assert (result.matched, result.fields, result.error) == ("none", [], "cannot read PDF: broken content stream")


def test_a_fault_of_the_code_or_template_is_an_internal_error_not_a_bad_pdf(template, monkeypatch):
    def boom(value, sp):
        raise ValueError("boom")

    monkeypatch.setattr("formcast.extract.read._coerce", boom)
    result = extract(template, make_form(CLIENT, kind="fillable"))
    assert (result.matched, result.fields, result.error) == ("none", [], "internal error: ValueError: boom")


def test_an_invalid_schema_is_an_internal_error(template):
    schema = copy.deepcopy(template.schema_)
    schema["properties"]["investor"]["properties"]["legal_name"]["minLength"] = "long"
    bad = ExtractionTemplate.model_validate({**template.model_dump(by_alias=True), "schema": schema})
    result = extract(bad, make_form(CLIENT, kind="fillable"))
    assert result.fields == [] and result.error.startswith("internal error: FormcastError: invalid JSON Schema")


def test_one_bad_answer_in_a_list_fails_the_lists_check(template):
    # a list of answers from a fixed set, read from one text field: an error on its second item is the field's
    tags_schema = {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
    schema = {"type": "object", "properties": {"tags": tags_schema}}
    tags = ExtractionTemplate.model_validate(
        {
            **template.model_dump(by_alias=True),
            "schema": schema,
            "readers": {"tags": {"strategies": [{"kind": "widget", "names": ["country"]}]}},
        }
    )
    got = fields(extract(tags, make_form(dataclasses.replace(CLIENT, country="a, zz"), kind="fillable")))["tags"]
    assert (got.status, got.raw, got.value) == ("failed_check", "a, zz", ["a", "zz"])
    assert got.reasons == ["'zz' is not one of ['a', 'b']"]
    got = fields(extract(tags, make_form(dataclasses.replace(CLIENT, country="a, b"), kind="fillable")))["tags"]
    assert (got.status, got.value, got.reasons) == ("found", ["a", "b"], [])


def test_the_doc_id_defaults_to_the_blind_fingerprint(template):
    pdf = make_form(CLIENT, kind="fillable")
    assert extract(template, pdf).doc_id == pdf_features(pdf).fingerprint_blind
    assert extract(template, pdf, doc_id="batch/a.pdf").doc_id == "batch/a.pdf"


def test_the_corpus_reads_with_no_wrong_found_value(template, corpus_dir):
    people = clients(20)
    found = Counter()
    seen = Counter()
    filled_scores, other_scores = [], []
    started = time.perf_counter()
    files = sorted(corpus_dir.glob("*.pdf"))
    results = [extract(template, f, doc_id=f.name, file=f.name) for f in files]
    per_doc = (time.perf_counter() - started) / len(files)
    for n, (f, result) in enumerate(zip(files, results)):
        assert result.error is None
        with DocText(f) as doc:
            score = layout_score(template.layout, doc)
        if n < len(people):
            filled_scores.append(score)
            assert result.matched in ("fingerprint", "layout"), f.name
            want = truth(people[n])
            for field in result.fields:
                if field.path in want:
                    seen[field.path] += 1
                    found[field.path] += field.status == "found"
                if field.status == "found":
                    assert field.value == want.get(field.path), (f.name, field)
        else:
            other_scores.append(score)
            assert result.matched == "none"
            assert not any(f.status == "found" for f in result.fields), f.name
    share = {path: found[path] / seen[path] for path in seen}
    print(f"\nshare found per path: {share}")
    print(f"layout scores: filled min {min(filled_scores):.3f}, other max {max(other_scores):.3f}")
    print(f"seconds per document: {per_doc:.4f}")
    assert all(s == 1.0 for s in share.values()), share
    # LAYOUT_MATCH sits at least 0.03 from both margins
    assert min(filled_scores) - LAYOUT_MATCH >= 0.03 and LAYOUT_MATCH - max(other_scores) >= 0.03


def test_other_form_reads_nothing_found(template):
    result = extract(template, other_form())
    assert result.matched == "none" and result.error is None
    assert not any(f.status == "found" for f in result.fields)
