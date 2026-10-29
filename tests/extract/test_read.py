"""extract(): one document read with a template, every path ending in a status and never a silent wrong value."""

import copy
import dataclasses
import datetime as dt
import time
from collections import Counter

import pymupdf
import pytest

from formcast.extract.layout import LAYOUT_MATCH, layout_score, learn_layout
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
    """What each path should read for ``client``; owner rows the client lacks, and answers left blank, are not in it."""
    dob = ""
    if client.dob:
        m, d, y = client.dob.split("/")
        dob = dt.date(int(y), int(m), int(d)).isoformat()
    out = {
        "investor.legal_name": client.legal_name,
        "investor.tax_id": client.tax_id,
        "investor.dob": dob,
        "investor.type": client.investor_type,
        "investor.country": COUNTRY_CODES.get(client.country, ""),
        "address.street": client.street,
        "address.city_state_zip": client.city_state_zip,
        "wire.bank": client.bank,
        "wire.account": client.account,
    }
    for i, (name, percent) in enumerate(client.owners):
        out[f"owners[{i}].name"] = name
        out[f"owners[{i}].percent"] = percent
    return {path: value for path, value in out.items() if value}


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


def _numbers_read(template, units: str, amount: str):
    """``units`` (an integer path) and ``amount`` (a number path) read beside their labels on a one-page copy."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y, label, value in ((100, "Number of units:", units), (130, "Commitment:", amount)):
        page.insert_text((72, y), label, fontsize=10)
        page.insert_text((240, y), value, fontsize=10)
    numbers = ExtractionTemplate(
        template_id="n", name="n", form=template.form, layout=Layout(lines=[], page_sizes=[]),
        schema={"type": "object", "properties": {"units": {"type": "integer"}, "amount": {"type": "number"}}},
        readers={"units": Reader(strategies=[AnchorRead(labels=["Number of units"], page=0)]),
                 "amount": Reader(strategies=[AnchorRead(labels=["Commitment"], page=0)])},
    )  # fmt: skip
    return fields(extract(numbers, doc.tobytes()))


def test_a_number_that_is_not_whole_fails_an_integer_path_and_is_kept_as_read(template):
    got = _numbers_read(template, "12.7", "1,234.50")
    assert (got["units"].status, got["units"].raw, got["units"].value) == ("failed_check", "12.7", "12.7")
    assert any("is not of type 'integer'" in r for r in got["units"].reasons)
    assert (got["amount"].value, got["amount"].reasons) == (1234.5, ["layout not matched"])
    got = _numbers_read(template, "1,200", "$3,000")
    assert (got["units"].value, got["amount"].value) == (1200, 3000)


@pytest.mark.parametrize("written", ["1,5", "12,34", "1,2345", ",5", "1,234,56"])
def test_commas_count_only_as_thousands_separators(template, written):
    got = _numbers_read(template, written, written)
    for path in ("units", "amount"):
        assert (got[path].status, got[path].value) == ("failed_check", written), path


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


@pytest.mark.parametrize("kind", ["fillable", "flattened"])
def test_a_blank_street_does_not_read_the_city_line_under_it(template, kind):
    client = dataclasses.replace(clients(3)[0], street="")
    got = fields(extract(template, make_form(client, kind=kind)))
    assert (got["address.street"].status, got["address.street"].raw) == ("missing", None)
    assert (got["address.city_state_zip"].status, got["address.city_state_zip"].value) == (
        "found", client.city_state_zip,
    )  # fmt: skip


def test_an_anchor_read_never_takes_the_forms_printed_words(template):
    # right of "Investor type:" there are only the printed option labels: on a matched layout nothing is read
    reads_type_row = with_reader(template, "investor.legal_name", AnchorRead(labels=["Investor type"], page=0))
    got = fields(extract(reads_type_row, make_form(CLIENT, kind="flattened", checked=())))["investor.legal_name"]
    assert (got.status, got.raw) == ("missing", None)
    # on an unmatched layout the form's printed words are not known, and the read is unsure, never found
    unmatched = reads_type_row.model_copy(update={"layout": Layout(lines=[], page_sizes=[])})
    got = fields(extract(unmatched, make_form(CLIENT, kind="flattened", checked=())))["investor.legal_name"]
    assert got.status == "unsure"


def _contact_form(phone: str, fax: str, street: str, city: str) -> bytes:
    """A one-page form with colon-less labels: Phone and Fax on one row, Street address and its city line below."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in ((72, 100, "Phone"), (330, 100, "Fax"), (72, 140, "Street address"),
                       (72, 160, "City, state and ZIP"), (72, 300, "Thank you for your business.")):  # fmt: skip
        page.insert_text((x, y), text, fontsize=10)
    for x, y, text in ((150, 100, phone), (360, 100, fax), (200, 140, street), (200, 160, city)):
        if text:
            page.insert_text((x, y), text, fontsize=10)
    return doc.tobytes()


def test_a_blank_answer_before_a_printed_label_reads_nothing_on_a_matched_layout(template):
    contact = ExtractionTemplate(
        template_id="contact", name="contact", form=template.form,
        schema={"type": "object", "properties": {k: {"type": "string"} for k in ("phone", "fax", "street")}},
        layout=learn_layout([], blank=_contact_form("", "", "", "")),
        readers={
            "phone": Reader(strategies=[AnchorRead(labels=["Phone"], page=0)]),
            "fax": Reader(strategies=[AnchorRead(labels=["Fax"], page=0)]),
            "street": Reader(strategies=[AnchorRead(labels=["Street"], page=0, side="below")]),
        },
    )  # fmt: skip
    filled = _contact_form("", "555 0100", "", "Madison, WI 53703")
    result = extract(contact, filled)
    got = fields(result)
    assert result.matched == "layout"
    assert (got["phone"].status, got["phone"].raw) == ("missing", None)  # not "Fax 555 0100"
    assert (got["street"].status, got["street"].raw) == ("missing", None)  # not "City, state and ZIP"
    assert (got["fax"].status, got["fax"].value) == ("found", "555 0100")


def _joined_contact_form(phone: str, fax: str, *, colon: bool) -> bytes:
    """A one-page form whose Phone and Fax labels are one printed line, as a word processor writes a row."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    phone_label, fax_label = ("Phone:", "Fax:") if colon else ("Phone", "Fax")
    page.insert_text((72, 100), phone_label + " " * 60 + fax_label, fontsize=10)
    page.insert_text((72, 300), "Thank you for your business.", fontsize=10)
    for x, text in ((120, phone), (330, fax)):
        if text:
            page.insert_text((x, 100), text, fontsize=10)
    return doc.tobytes()


@pytest.mark.parametrize("colon", [True, False])
@pytest.mark.parametrize("paths", [("phone", "fax"), ("phone",)])
def test_two_labels_printed_as_one_line_each_read_their_own_answer(template, colon, paths):
    readers = {
        "phone": Reader(strategies=[AnchorRead(labels=["Phone"], page=0)]),
        "fax": Reader(strategies=[AnchorRead(labels=["Fax"], page=0)]),
    }
    contact = ExtractionTemplate(
        template_id="contact", name="contact", form=template.form,
        schema={"type": "object", "properties": {k: {"type": "string"} for k in paths}},
        layout=learn_layout([], blank=_joined_contact_form("", "", colon=colon)),
        readers={k: readers[k] for k in paths},
    )  # fmt: skip
    assert [ln.text for ln in contact.layout.lines][0] == "phone fax"  # one printed line holds both labels
    result = extract(contact, _joined_contact_form("608 555 0142", "555 0100", colon=colon))
    got = fields(result)
    assert result.matched == "layout"
    assert (got["phone"].status, got["phone"].value) == ("found", "608 555 0142")  # not the fax number
    if "fax" in paths:
        assert (got["fax"].status, got["fax"].value) == ("found", "555 0100")
    got = fields(extract(contact, _joined_contact_form("", "555 0100", colon=colon)))
    assert (got["phone"].status, got["phone"].raw) == ("missing", None)  # a blank phone does not read the fax


def test_an_anchor_read_that_another_field_also_read_is_unsure_and_so_is_the_other(template):
    # the country read by the legal name's label reads what the legal name's widget reads
    misread = with_reader(template, "investor.country", AnchorRead(labels=["Investor legal name"], page=0))
    got = fields(extract(misread, make_form(CLIENT, kind="fillable")))
    country, name = got["investor.country"], got["investor.legal_name"]
    assert (country.raw, country.strategy) == (CLIENT.legal_name, "anchor")
    assert country.status in ("unsure", "failed_check") and "also read as investor.legal_name" in country.reasons
    assert (name.status, name.strategy, name.value) == ("unsure", "widget", CLIENT.legal_name)
    assert name.reasons == ["also read as investor.country"]


def test_an_anchor_read_inside_another_fields_box_is_unsure(template):
    # part of the legal name, read by its label with a narrow width: not the same text, but inside its box
    narrow = with_reader(
        template, "address.street", AnchorRead(labels=["Investor legal name"], page=0, max_width=125)
    )
    got = fields(extract(narrow, make_form(CLIENT, kind="flattened")))
    street, name = got["address.street"], got["investor.legal_name"]
    assert street.raw and street.raw != CLIENT.legal_name and CLIENT.legal_name.startswith(street.raw)
    assert (street.status, street.reasons) == ("unsure", ["overlaps investor.legal_name's box"])
    assert (name.status, name.reasons) == ("found", [])


@pytest.mark.parametrize("edition", ["revised", "swapped"])
def test_a_box_whose_neighbourhood_moved_gives_way_to_the_label_read(template, edition):
    client = clients(3)[1]
    pdf = make_form(client, kind="flattened", edition=edition)
    result = extract(template, pdf)
    assert result.matched == "layout"
    got = fields(result)
    moved = ["investor.legal_name", "investor.tax_id"] if edition == "revised" else [
        "investor.tax_id", "investor.dob", "investor.country",
    ]  # fmt: skip
    want = truth(client)
    for path in moved:
        assert (got[path].strategy, got[path].status) == ("anchor", "unsure"), path
        assert got[path].value == want[path] and "layout differs near this box" in got[path].reasons, path
    for path in ("address.street", "owners[0].name"):
        assert (got[path].strategy, got[path].status, got[path].value) == ("box", "found", want[path])
    # with no label to fall back on, the field is missing and says why
    box_only = with_reader(template, "investor.tax_id", template.readers["investor.tax_id"].strategies[1])
    tax_id = fields(extract(box_only, pdf))["investor.tax_id"]
    assert (tax_id.status, tax_id.raw) == ("missing", None)
    assert "layout differs near this box" in tax_id.reasons
    # a fillable copy of the same edition still reads its widgets, all found
    assert_reads(extract(template, make_form(client, kind="fillable", edition=edition)), client)


def test_an_answer_that_runs_into_the_next_label_is_missing_and_says_so(template):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Name: Dana Keller Date of birth: 05/01/1980", fontsize=10)
    named = ExtractionTemplate(
        template_id="t", name="t", schema={"type": "object", "properties": {"name": {"type": "string"}}},
        form=template.form, layout=Layout(lines=[], page_sizes=[]),
        readers={"name": Reader(strategies=[AnchorRead(labels=["Name"], page=0)])},
    )  # fmt: skip
    got = fields(extract(named, doc.tobytes()))["name"]
    assert (got.status, got.raw, got.reasons) == ("missing", None, ["answer runs into the next label"])


def edge_copies() -> list[tuple[str, bytes, Client]]:
    """Filled copies the corpus folder lacks, in both kinds: optional answers left blank, and the two other editions."""
    people = clients(6, seed=11)
    blanks = [
        dataclasses.replace(clients(3)[0], street=""),  # the city line is then the first line under the address label
        dataclasses.replace(people[0], street="", dob="", owners=[]),
        dataclasses.replace(
            people[1], legal_name="", country="", city_state_zip="", owners=[("", ""), ("Dana Keller", "")]
        ),
    ]
    copies = []
    for kind in ("fillable", "flattened"):
        for n, client in enumerate(blanks):
            copies.append((f"blank_{n}_{kind}", make_form(client, kind=kind), client))
        for edition in ("revised", "swapped"):
            for n, client in enumerate(people[2:4]):
                copies.append((f"{edition}_{n}_{kind}", make_form(client, kind=kind, edition=edition), client))
    return copies


def test_the_corpus_reads_with_no_wrong_found_value(template, corpus_dir):
    people = clients(20)
    found = Counter()
    seen = Counter()
    filled_scores, other_scores = [], []
    started = time.perf_counter()
    files = sorted(corpus_dir.glob("*.pdf"))
    results = [extract(template, f, doc_id=f.name, file=f.name) for f in files]
    per_doc = (time.perf_counter() - started) / len(files)
    # the copies with blank answers and the other editions: no found value differs from the truth, a blank answer
    # is missing or absent, and nothing else is asked of them (a moved row may well be unsure)
    edition_scores: dict[str, list[float]] = {"revised": [], "swapped": []}
    for name, pdf, client in edge_copies():
        result = extract(template, pdf)
        assert result.error is None and result.matched == "layout", name
        want = truth(client)
        for field in result.fields:
            if field.status == "found":
                assert field.value == want.get(field.path), (name, field)
            if field.path not in want:
                assert field.status in ("missing", "absent"), (name, field)
        if name.startswith(tuple(edition_scores)):
            with DocText(pdf) as doc:
                edition_scores[name.split("_")[0]].append(layout_score(template.layout, doc))
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
    print("edition layout scores: " + ", ".join(f"{k} {min(v):.3f}-{max(v):.3f}" for k, v in edition_scores.items()))
    print(f"seconds per document: {per_doc:.4f}")
    assert all(s == 1.0 for s in share.values()), share
    # LAYOUT_MATCH sits at least 0.03 from both margins
    assert min(filled_scores) - LAYOUT_MATCH >= 0.03 and LAYOUT_MATCH - max(other_scores) >= 0.03


def test_other_form_reads_nothing_found(template):
    result = extract(template, other_form())
    assert result.matched == "none" and result.error is None
    assert not any(f.status == "found" for f in result.fields)
