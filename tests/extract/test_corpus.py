"""The generated corpus: its forms carry the answers where the template says, and the hand-written template is valid."""

import re

import jsonschema
import pymupdf
import pytest

from formcast.extract.model import ExtractionTemplate, concrete_paths
from tests.extract.corpus import (
    FIELD_RECTS,
    INVESTOR_TYPES,
    OWNER_ROWS,
    TYPE_BOXES,
    clients,
    hand_template,
    make_form,
    other_form,
)


def _open(data: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=data, filetype="pdf")


def _widgets(data: bytes) -> dict[str, str]:
    with _open(data) as doc:
        return {w.field_name: w.field_value for page in doc for w in page.widgets()}


def _words_in(data: bytes, rect: tuple, page: int = 0) -> str:
    with _open(data) as doc:
        r = pymupdf.Rect(rect)
        words = [w for w in doc[page].get_text("words") if r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
    return " ".join(w[4] for w in sorted(words, key=lambda w: (round(w[1]), w[0])))


def _word_y0(data: bytes, text: str, page: int = 0) -> float:
    with _open(data) as doc:
        return next(w[1] for w in doc[page].get_text("words") if w[4] == text)


def test_a_fillable_form_carries_its_answers_in_named_widgets():
    client = clients(3)[2]  # the trust
    values = _widgets(make_form(client, kind="fillable"))
    assert values["investor_name"] == client.legal_name
    assert values["tax_id"] == client.tax_id
    assert values["dob"] == client.dob
    assert values["country"] == client.country
    assert values["address_1"] == client.street
    assert values["address_2"] == client.city_state_zip
    for i, (name, percent) in enumerate(client.owners, start=1):
        assert values[f"owner_{i}_name"] == name
        assert values[f"owner_{i}_pct"] == percent
    assert client.investor_type == "trust"
    assert [v for v in INVESTOR_TYPES if values[f"type_{v}"] != "Off"] == ["trust"]


def test_a_flattened_form_has_no_widgets_and_its_answers_are_page_text():
    client = clients(2)[1]
    data = make_form(client, kind="flattened")
    assert _widgets(data) == {}
    with _open(data) as doc:
        text = doc[0].get_text()
    for answer in (client.legal_name, client.tax_id, client.dob, client.street, client.city_state_zip):
        assert answer in text
    for name, percent in client.owners:
        assert name in text and percent in text
    # the answers are drawn inside the rectangles the widgets would have had
    assert _words_in(data, FIELD_RECTS["tax_id"]) == client.tax_id
    assert _words_in(data, FIELD_RECTS["investor_name"]) == client.legal_name
    # the check mark of the client's type is an X inside its square, and only that one
    marks = {v: _words_in(data, rect) for v, (_, rect) in TYPE_BOXES.items()}
    assert marks == {v: ("X" if v == client.investor_type else "") for v in INVESTOR_TYPES}


def test_the_blank_form_has_the_labels_and_no_answers():
    for kind in ("fillable", "flattened"):
        data = make_form(None, kind=kind)
        with _open(data) as doc:
            text = doc[0].get_text()
        for label in ("Client Information Form", "Investor legal name:", "Taxpayer ID:", "Date of birth or formation:",
                      "Country:", "Investor type:", "Individual", "Joint", "Trust", "LLC", "Mailing address",
                      "Beneficial owners (25% or more)", "Funds will be sent from"):  # fmt: skip
            assert label in text
        assert all(v in ("", "Off") for v in _widgets(data).values())
        assert all(_words_in(data, rect) == "" for rect in FIELD_RECTS.values())
        assert all(_words_in(data, rect) == "" for _, rect in TYPE_BOXES.values())
    assert set(_widgets(make_form(None, kind="fillable"))) == set(FIELD_RECTS) | {f"type_{v}" for v in INVESTOR_TYPES}


def test_shift_moves_the_first_page_content_down():
    client = clients(1)[0]
    plain = make_form(client, kind="flattened")
    moved = make_form(client, kind="flattened", shift=14.0)
    assert _word_y0(moved, "Taxpayer") == pytest.approx(_word_y0(plain, "Taxpayer") + 14.0)
    # page 1 does not move
    assert _word_y0(moved, "Signature", page=1) == pytest.approx(_word_y0(plain, "Signature", page=1))
    # the widgets of a fillable copy move with it
    with _open(make_form(client, kind="fillable", shift=14.0)) as doc:
        rects = {w.field_name: w.rect for w in doc[0].widgets()}
    assert rects["tax_id"].y0 == pytest.approx(FIELD_RECTS["tax_id"][1] + 14.0)


def test_the_wire_paragraph_is_page_text_in_both_kinds_and_not_a_widget():
    client = clients(1)[0]
    for kind in ("fillable", "flattened"):
        data = make_form(client, kind=kind)
        with _open(data) as doc:
            text = " ".join(doc[0].get_text().split())
        assert f"Funds will be sent from {client.bank}, ABA 114000093, account {client.account}, in the name" in text
        assert client.bank not in " ".join(str(v) for v in _widgets(data).values())


def test_page_one_is_agreement_text_ending_in_a_signature():
    with _open(make_form(None, kind="fillable")) as doc:
        assert len(doc) == 2
        sentences = doc[1].get_text().count(".")
        assert sentences >= 40
        assert doc[1].get_text().split()[-1] == "Signature"
        assert doc[0].rect == pymupdf.Rect(0, 0, 612, 792)


def test_rotate_extra_page_and_painted_options():
    client = clients(1)[0]
    with _open(make_form(client, kind="fillable", rotate=90)) as doc:
        assert [p.rotation for p in doc] == [90, 90]
        assert {w.field_name: w.rect for w in doc[0].widgets()}["tax_id"].y0 == pytest.approx(FIELD_RECTS["tax_id"][1])
    with _open(make_form(client, kind="flattened", extra_page=True)) as doc:
        assert len(doc) == 3 and doc[2].get_text().strip() == ""
    # painted: the value is also in the page's own content, at the widget. A widget's appearance stream already
    # shows its value to get_text, so the two are told apart by taking the widgets away.
    def page_text_without_widgets(data: bytes) -> str:
        with _open(data) as doc:
            page = doc[0]
            while (w := page.first_widget) is not None:
                page.delete_widget(w)
            return page.get_text()

    plain = make_form(client, kind="fillable")
    painted = make_form(client, kind="fillable", painted=True)
    assert client.tax_id not in page_text_without_widgets(plain)
    assert client.tax_id in page_text_without_widgets(painted)
    assert _widgets(painted)["tax_id"] == client.tax_id
    assert _words_in(painted, FIELD_RECTS["tax_id"]).split() == [client.tax_id] * 2


def test_labels_replace_printed_wordings():
    data = make_form(clients(1)[0], kind="fillable", labels={"Taxpayer ID:": "TIN:"})
    with _open(data) as doc:
        text = doc[0].get_text()
    assert "TIN:" in text and "Taxpayer ID:" not in text and "Investor legal name:" in text


def test_clients_are_deterministic_and_well_formed():
    assert clients(8) == clients(8)
    assert clients(8, seed=1) != clients(8, seed=0)
    assert clients(3) == clients(10)[:3]
    people = clients(40)
    assert {c.investor_type for c in people} == set(INVESTOR_TYPES)
    assert len({c.tax_id for c in people}) > 30
    for c in people:
        assert re.fullmatch(r"\d{2}-\d{7}|\d{3}-\d{2}-\d{4}", c.tax_id)
        assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", c.dob)
        assert c.account.isdigit()
        assert 1 <= len(c.owners) <= OWNER_ROWS
        assert sum(int(p.rstrip("%")) for _, p in c.owners) <= 100


def test_the_same_client_gives_the_same_content_each_time():
    client = clients(1)[0]
    for kind in ("fillable", "flattened"):
        with _open(make_form(client, kind=kind)) as a, _open(make_form(client, kind=kind)) as b:
            assert [p.get_text("words") for p in a] == [p.get_text("words") for p in b]


def test_other_form_is_a_different_layout():
    with _open(other_form()) as doc:
        assert "Taxpayer" in doc[0].get_text() or "W-9" in doc[0].get_text()
    assert other_form() != make_form(None, kind="fillable")


def test_the_hand_template_is_valid_and_reads_every_schema_path_once():
    template = hand_template()
    assert ExtractionTemplate.model_validate(template.model_dump(by_alias=True)) == template
    assert set(template.readers) == set(concrete_paths(template.schema_))
    assert template.layout.lines == [] and template.layout.page_sizes == []
    assert template.form.page_count == 2 and template.form.page_sizes == [(612, 792), (612, 792)]
    assert [s.kind for s in template.readers["investor.tax_id"].strategies] == ["widget", "box", "anchor"]
    assert [s.kind for s in template.readers["investor.type"].strategies] == ["choice", "choice"]
    assert [s.kind for s in template.readers["wire.account"].strategies] == ["phrase"]
    assert hand_template() is not template and hand_template().form == template.form


def test_the_hand_template_schema_checks_a_record():
    schema = hand_template().schema_
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    good = {"investor": {"tax_id": "84-2219037", "type": "llc", "dob": "1980-02-03"}, "owners": [{"name": "A"}] * 4}
    assert not list(validator.iter_errors(good))
    for bad in (
        {"investor": {"tax_id": "842219037"}},
        {"investor": {"type": "llc"}},
        {"investor": {"tax_id": "84-2219037", "type": "club"}},
        {"investor": {"tax_id": "84-2219037"}, "owners": [{"name": "A"}] * 5},
    ):
        assert list(validator.iter_errors(bad))


def test_the_corpus_folder_has_the_mix_the_later_tests_expect(corpus_dir):
    files = sorted(corpus_dir.glob("*.pdf"))
    assert [f.name for f in files] == [f"client_{n:05d}.pdf" for n in range(1, 23)]
    people = clients(20)
    widget_counts = []
    for f in files:
        with pymupdf.open(f) as doc:
            widget_counts.append(sum(1 for page in doc for _ in page.widgets()))
    assert [n > 0 for n in widget_counts[:12]] == [True] * 12  # fillable
    assert widget_counts[12:19] == [0] * 7  # six flattened and the shifted one
    assert widget_counts[19] > 0  # the rotated fillable copy
    assert widget_counts[20:] == [widget_counts[20]] * 2  # the two other forms are the same form
    assert _widgets(files[0].read_bytes())["tax_id"] == people[0].tax_id
    assert pymupdf.open(files[19])[0].rotation == 90
    assert pymupdf.open(files[18])[0].get_text("words") != pymupdf.open(files[17])[0].get_text("words")
    assert len(pymupdf.open(files[20])) != 2  # the W-9 has its own page count and layout
