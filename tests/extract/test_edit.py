"""Edits to a template: each one changes the schema or one path's readers and gives back a template that still validates."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from formcast.data.mapping import Transform
from formcast.errors import FormcastError
from formcast.extract.edit import (
    Edit,
    MakeOptionSet,
    MarkAbsent,
    RemoveField,
    RenameField,
    Restore,
    SetCategory,
    SetBox,
    SetClean,
    SetField,
    SetLabels,
    SetOptionBox,
    SplitOptionSet,
    apply_edit,
)
from formcast.extract.model import (
    RESERVED_NAMES,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    ExtractionTemplate,
    Reader,
    SampleRecord,
    SetupInfo,
    WidgetRead,
)
from formcast.schema import REASON_KEY
from tests.extract.corpus import FIELD_RECTS, SHIFT, TYPE_BOXES, clients, hand_template, make_form, truth


def with_top_level_field(template, name):
    return ExtractionTemplate.model_validate({
        **template.model_dump(by_alias=True),
        "schema": {**template.schema_, "properties": {**template.schema_["properties"], name: {"type": "string"}}},
    })


def _prop(template, *names):
    node = template.schema_
    for name in names:
        node = node["properties"][name]
    return node


def test_set_field_edits_the_schema_of_the_path_and_type_date_means_string_with_format():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.dob", title="Born", type="date", required=True))
    prop = t.schema_["properties"]["investor"]["properties"]["dob"]
    assert (prop["title"], prop["type"], prop["format"]) == ("Born", "string", "date")
    assert "dob" in t.schema_["properties"]["investor"]["required"]


def test_an_item_path_edits_the_items_schema_once():
    t = apply_edit(hand_template(), SetField(op="set_field", path="owners[2].percent", type="number", maximum=100))
    item = t.schema_["properties"]["owners"]["items"]["properties"]["percent"]
    assert (item["type"], item["maximum"]) == ("number", 100)


def test_clearing_a_pattern_and_an_enum():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.tax_id", pattern=""))
    assert "pattern" not in t.schema_["properties"]["investor"]["properties"]["tax_id"]
    t = apply_edit(t, SetField(op="set_field", path="investor.type", enum=[]))
    assert "enum" not in _prop(t, "investor", "type")


def test_a_pattern_and_an_enum_are_set_and_a_field_that_is_not_mentioned_keeps_its_other_keys():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.country", pattern="^[A-Z]{2}$", enum=["US", "CA", "MX"]))
    country = _prop(t, "investor", "country")
    assert (country["pattern"], country["enum"]) == ("^[A-Z]{2}$", ["US", "CA", "MX"])
    assert country["title"] == "Country" and country["description"].startswith("The investor's country")


def test_a_pattern_that_is_not_a_regular_expression_is_refused():
    with pytest.raises(ValidationError, match="pattern"):
        SetField(op="set_field", path="investor.tax_id", pattern="(")


def test_changing_the_type_swaps_the_format_and_yes_no_is_a_boolean():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.dob", type="text"))
    assert _prop(t, "investor", "dob")["type"] == "string" and "format" not in _prop(t, "investor", "dob")
    t = apply_edit(t, SetField(op="set_field", path="investor.dob", type="yes/no"))
    assert _prop(t, "investor", "dob")["type"] == "boolean"
    t = apply_edit(t, SetField(op="set_field", path="investor.dob", type="integer", minimum=0))
    assert (_prop(t, "investor", "dob")["type"], _prop(t, "investor", "dob")["minimum"]) == ("integer", 0)


def test_required_false_takes_the_field_out_of_required_and_drops_an_empty_list():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.tax_id", required=False))
    assert "required" not in t.schema_["properties"]["investor"]
    t = apply_edit(t, SetField(op="set_field", path="owners[0].name", required=True))
    assert t.schema_["properties"]["owners"]["items"]["required"] == ["name"]


def test_a_persons_required_change_is_set_by_you():
    t = apply_edit(hand_template(), SetField(op="set_field", path="investor.tax_id", required=False))
    assert _prop(t, "investor", "tax_id")[REASON_KEY] == "Set by you"
    t = apply_edit(t, SetField(op="set_field", path="owners[1].percent", required=True))
    assert _prop(t, "owners")["items"]["properties"]["percent"][REASON_KEY] == "Set by you"
    t = apply_edit(t, SetField(op="set_field", path="investor.dob", title="Born"))  # no level change, no reason
    assert REASON_KEY not in _prop(t, "investor", "dob")


def test_a_required_change_with_a_reason_keeps_it():
    t = apply_edit(
        hand_template(), SetField(op="set_field", path="investor.dob", required=True, required_reason="the clerk asked")
    )
    assert _prop(t, "investor", "dob")[REASON_KEY] == "the clerk asked"
    assert "dob" in t.schema_["properties"]["investor"]["required"]


def test_an_edit_gives_back_a_copy_and_leaves_the_template_it_was_given_alone():
    base = hand_template()
    before = base.model_dump(by_alias=True)
    apply_edit(base, SetField(op="set_field", path="investor.dob", title="Born"))
    apply_edit(base, SetBox(op="set_box", path="investor.tax_id", page=0, rect=(1, 2, 3, 4)))
    apply_edit(base, RemoveField(op="remove_field", path="investor.dob"))
    assert base.model_dump(by_alias=True) == before


def test_set_box_replaces_the_box_and_keeps_the_order():
    t = apply_edit(hand_template(), SetBox(op="set_box", path="investor.tax_id", page=0, rect=(100, 100, 200, 114)))
    kinds = [s.kind for s in t.readers["investor.tax_id"].strategies]
    assert kinds == ["widget", "box", "anchor"] and t.readers["investor.tax_id"].strategies[1].rect == (100, 100, 200, 114)
    t = apply_edit(hand_template(), SetBox(op="set_box", path="wire.bank", page=0, rect=(1, 2, 3, 4)))
    assert [s.kind for s in t.readers["wire.bank"].strategies] == ["box", "phrase"]


def test_set_box_on_a_path_with_no_reader_makes_one_and_takes_the_path_out_of_absent():
    t = apply_edit(hand_template(), MarkAbsent(op="mark_absent", path="investor.country"))
    t = apply_edit(t, SetBox(op="set_box", path="investor.country", page=1, rect=(5, 6, 50, 20)))
    assert [s.kind for s in t.readers["investor.country"].strategies] == ["box"]
    assert "investor.country" not in t.absent  # a path with a reading is on the form


def test_set_option_box_replaces_one_answers_drawn_box_and_leaves_the_widget_marks():
    t = apply_edit(hand_template(), SetOptionBox(op="set_option_box", path="investor.type", answer="trust", page=0, rect=(1, 2, 3, 4)))
    widget_read, drawn = t.readers["investor.type"].strategies
    assert all(m.widget for m in widget_read.options.values())
    assert (drawn.options["trust"].page, drawn.options["trust"].rect) == (0, (1, 2, 3, 4))
    assert list(drawn.options) == ["individual", "joint", "trust", "llc"]


def test_set_option_box_adds_the_drawn_choice_read_after_the_widget_one_when_missing():
    t = apply_edit(hand_template(), SetOptionBox(op="set_option_box", path="investor.country", answer="CA", page=0, rect=(1, 2, 3, 4)))
    kinds = [s.kind for s in t.readers["investor.country"].strategies]
    assert kinds == ["widget", "choice", "box", "anchor"]
    choice = t.readers["investor.country"].strategies[1]
    assert isinstance(choice, ChoiceRead) and choice.options["CA"].rect == (1, 2, 3, 4)
    t = apply_edit(t, SetOptionBox(op="set_option_box", path="investor.country", answer="US", page=0, rect=(5, 6, 7, 8)))
    assert list(t.readers["investor.country"].strategies[1].options) == ["CA", "US"]


def test_set_option_box_refuses_an_answer_the_field_does_not_have():
    with pytest.raises(FormcastError, match="no answer 'maybe'"):
        apply_edit(hand_template(), SetOptionBox(op="set_option_box", path="investor.type", answer="maybe", page=0, rect=(1, 2, 3, 4)))
    with pytest.raises(FormcastError, match="no answer"):  # no enum and no choice options at all
        apply_edit(hand_template(), SetOptionBox(op="set_option_box", path="investor.legal_name", answer="x", page=0, rect=(1, 2, 3, 4)))


def test_set_labels_replaces_or_removes_the_label_read():
    t = apply_edit(hand_template(), SetLabels(op="set_labels", path="investor.tax_id", labels=["TIN"], side="below"))
    a = t.readers["investor.tax_id"].strategies[-1]
    assert (a.labels, a.side) == (["TIN"], "below")
    t = apply_edit(t, SetLabels(op="set_labels", path="investor.tax_id", labels=[]))
    assert [s.kind for s in t.readers["investor.tax_id"].strategies] == ["widget", "box"]


def test_set_labels_keeps_the_rest_of_the_label_read_and_keeps_the_side_unless_given():
    t = apply_edit(hand_template(), SetLabels(op="set_labels", path="address.street", labels=["Street address"]))
    a = t.readers["address.street"].strategies[-1]
    assert (a.labels, a.side, a.max_width, a.page) == (["Street address"], "below", 468, 0)


def test_set_labels_appends_a_label_read_when_there_is_none_and_removing_none_changes_nothing():
    t = apply_edit(hand_template(), SetLabels(op="set_labels", path="wire.account", labels=["Account no."], side="below"))
    assert [s.kind for s in t.readers["wire.account"].strategies] == ["phrase", "anchor"]
    assert t.readers["wire.account"].strategies[-1].side == "below"
    same = apply_edit(hand_template(), SetLabels(op="set_labels", path="wire.account", labels=[]))
    assert same.readers == hand_template().readers


def test_blank_labels_are_refused():
    with pytest.raises(ValidationError, match="label"):
        SetLabels(op="set_labels", path="investor.tax_id", labels=["TIN", "  "])


def test_set_clean_replaces_the_transforms_and_makes_a_reader_when_there_is_none():
    clean = [Transform(op="digits")]
    t = apply_edit(hand_template(), SetClean(op="set_clean", path="investor.dob", clean=clean))
    assert t.readers["investor.dob"].clean == clean and len(t.readers["investor.dob"].strategies) == 3
    t = apply_edit(t, SetClean(op="set_clean", path="investor.dob", clean=[]))
    assert t.readers["investor.dob"].clean == []
    t = apply_edit(t, MarkAbsent(op="mark_absent", path="wire.bank"))
    t = apply_edit(t, SetClean(op="set_clean", path="wire.bank", clean=clean))
    assert t.readers["wire.bank"].strategies == [] and "wire.bank" not in t.absent


def test_mark_absent_drops_the_reader_and_restore_brings_back_an_empty_field():
    t = apply_edit(hand_template(), MarkAbsent(op="mark_absent", path="wire.bank"))
    assert "wire.bank" in t.absent and "wire.bank" not in t.readers
    t = apply_edit(t, Restore(op="restore", path="wire.bank"))
    assert "wire.bank" not in t.absent
    assert "wire.bank" not in t.readers


def test_marking_absent_twice_and_restoring_a_present_field_change_nothing():
    once = apply_edit(hand_template(), MarkAbsent(op="mark_absent", path="wire.bank"))
    assert apply_edit(once, MarkAbsent(op="mark_absent", path="wire.bank")).absent == ["wire.bank"]
    restored = apply_edit(hand_template(), Restore(op="restore", path="wire.bank"))
    assert restored.model_dump() == hand_template().model_dump()


def test_remove_field_removes_every_item_path_and_its_readings():
    t = hand_template().model_copy(update={"samples": [SampleRecord(file="a", sha256="0" * 64,
        opinion={"owners[0].name": "Ana", "investor.tax_id": "1"})]})
    t = apply_edit(t, RemoveField(op="remove_field", path="owners[1].name"))
    assert not any(p.startswith("owners[") and p.endswith(".name") for p in t.readers)
    assert t.samples[0].opinion == {"investor.tax_id": "1"}
    assert "name" not in t.schema_["properties"]["owners"]["items"]["properties"]
    assert any(p.endswith(".percent") for p in t.readers)


def test_remove_field_drops_the_name_from_required_the_absent_list_and_the_lines_that_became_it():
    base = hand_template().model_copy(update={
        "absent": ["investor.country"],
        "setup": SetupInfo(start="lines", lines=["tax", "owners"],
                           line_paths={"tax": ["investor.tax_id", "investor.dob"], "owners": ["owners[].name"]}),
    })
    t = apply_edit(base, RemoveField(op="remove_field", path="investor.tax_id"))
    assert "required" not in t.schema_["properties"]["investor"]
    assert "investor.tax_id" not in t.readers
    assert t.setup.line_paths == {"tax": ["investor.dob"], "owners": ["owners[].name"]}
    t = apply_edit(t, RemoveField(op="remove_field", path="investor.country"))
    assert t.absent == []
    t = apply_edit(t, RemoveField(op="remove_field", path="owners[3].name"))
    assert t.setup.line_paths["owners"] == []


def test_rename_field_moves_the_field_with_its_place_level_reader_and_readings():
    base = hand_template().model_copy(update={
        "absent": ["investor.country"],
        "samples": [SampleRecord(file="a", sha256="0" * 64, opinion={"investor.tax_id": "1", "investor.dob": "2"})],
        "setup": SetupInfo(start="lines", lines=["tax"], line_paths={"tax": ["investor.tax_id", "investor.dob"]}),
    })
    t = apply_edit(base, RenameField(op="rename_field", path="investor.tax_id", name="tin"))
    names = list(_prop(t, "investor")["properties"])
    assert names == [n.replace("tax_id", "tin") for n in _prop(base, "investor")["properties"]]
    assert _prop(t, "investor", "tin") == _prop(base, "investor", "tax_id")
    assert _prop(t, "investor")["required"] == ["tin"] and "tax_id" not in _prop(t, "investor")["properties"]
    assert t.readers["investor.tin"] == base.readers["investor.tax_id"] and "investor.tax_id" not in t.readers
    assert t.samples[0].opinion == {"investor.tin": "1", "investor.dob": "2"}
    assert t.setup.line_paths == {"tax": ["investor.tin", "investor.dob"]}
    assert base.readers["investor.tax_id"] and "tax_id" in _prop(base, "investor")["properties"]  # the original stays

    t = apply_edit(base, RenameField(op="rename_field", path="investor.country", name="nation"))
    assert t.absent == ["investor.nation"]


def test_rename_field_on_an_item_renames_it_in_every_item():
    base = hand_template().model_copy(update={
        "samples": [SampleRecord(file="a", sha256="0" * 64, opinion={"owners[0].name": "Ana", "owners[3].name": "Bo"})],
        "setup": SetupInfo(start="lines", lines=["owners"], line_paths={"owners": ["owners[].name"]}),
    })
    t = apply_edit(base, RenameField(op="rename_field", path="owners[2].name", name="owner"))
    assert not any(p.endswith(".name") for p in t.readers)
    assert [p for p in t.readers if p.endswith(".owner")] == [p.replace(".name", ".owner") for p in base.readers if p.endswith(".name")]
    assert t.samples[0].opinion == {"owners[0].owner": "Ana", "owners[3].owner": "Bo"}
    assert t.setup.line_paths == {"owners": ["owners[].owner"]}


def test_rename_field_to_its_own_name_changes_nothing():
    base = hand_template()
    assert apply_edit(base, RenameField(op="rename_field", path="investor.tax_id", name="tax_id")) == base


@pytest.mark.parametrize("name", ["", " ", "tax id", "tax.id", "tax[0]", "[]", 'tax"id', "tax\\id"])
def test_rename_field_refuses_a_name_a_path_cannot_hold(name):
    with pytest.raises(FormcastError, match="letters, numbers"):
        apply_edit(hand_template(), RenameField(op="rename_field", path="investor.tax_id", name=name))


@pytest.mark.parametrize("name", [*RESERVED_NAMES, "Document_Name"])
def test_rename_field_refuses_a_reserved_name_at_the_top_level_and_lists_them(name):
    base = with_top_level_field(hand_template(), "holder")
    with pytest.raises(FormcastError, match="reserved") as refused:
        apply_edit(base, RenameField(op="rename_field", path="holder", name=name))
    assert all(reserved in str(refused.value) for reserved in RESERVED_NAMES)


def test_a_reserved_name_is_allowed_inside_a_group_and_a_field_can_be_renamed_away_from_one():
    t = apply_edit(hand_template(), RenameField(op="rename_field", path="investor.tax_id", name="name"))
    assert "investor.name" in t.readers  # its column is investor.name, not the document's
    t = apply_edit(with_top_level_field(hand_template(), "name"), RenameField(op="rename_field", path="name", name="legal_name"))
    assert "legal_name" in t.schema_["properties"] and "name" not in t.schema_["properties"]


def following(template=None):
    """A template whose names follow its titles."""
    base = template or hand_template()
    return base.model_copy(update={"setup": SetupInfo(start="ai", name_follows_title=True)})


def test_while_names_follow_titles_a_title_gives_the_field_its_name_and_it_keeps_everything_else():
    base = following(hand_template().model_copy(update={
        "samples": [SampleRecord(file="a", sha256="0" * 64, opinion={"investor.tax_id": "1"})]}))
    t = apply_edit(base, SetField(op="set_field", path="investor.tax_id", title="Taxpayer number"))
    assert _prop(t, "investor", "taxpayer_number")["title"] == "Taxpayer number"
    assert _prop(t, "investor")["required"] == ["taxpayer_number"]
    assert t.readers["investor.taxpayer_number"] == base.readers["investor.tax_id"]
    assert t.samples[0].opinion == {"investor.taxpayer_number": "1"}


def test_while_names_follow_titles_a_repeated_groups_field_moves_in_every_item():
    t = apply_edit(following(), SetField(op="set_field", path="owners[2].name", title="Owner"))
    assert "owners[3].owner" in t.readers and not any(p.endswith(".name") for p in t.readers)


@pytest.mark.parametrize("title", ["", "#", "名前"])
def test_while_names_follow_titles_a_title_needs_something_to_make_a_name_from(title):
    with pytest.raises(FormcastError, match="letter or number"):
        apply_edit(following(), SetField(op="set_field", path="investor.tax_id", title=title))


def test_while_names_follow_titles_a_taken_or_reserved_name_is_refused():
    with pytest.raises(FormcastError, match="already a field named 'dob'"):
        apply_edit(following(), SetField(op="set_field", path="investor.tax_id", title="DOB"))
    with pytest.raises(FormcastError, match="reserved"):
        apply_edit(following(with_top_level_field(hand_template(), "holder")), SetField(op="set_field", path="holder", title="Document name"))


def test_once_saved_a_title_leaves_the_name_alone():
    base = hand_template().model_copy(update={"setup": SetupInfo(start="ai", name_follows_title=False)})
    t = apply_edit(base, SetField(op="set_field", path="investor.tax_id", title="Taxpayer number"))
    assert _prop(t, "investor", "tax_id")["title"] == "Taxpayer number"


def test_set_category_moves_a_field_with_its_level_and_reader_and_an_emptied_category_goes():
    base = following(with_top_level_field(hand_template(), "holder"))
    t = apply_edit(base, SetCategory(op="set_category", path="holder", category="Tax details"))
    assert _prop(t, "tax_details")["title"] == "Tax details" and "holder" not in t.schema_["properties"]
    t = apply_edit(t, SetCategory(op="set_category", path="tax_details.holder", category=""))
    assert "holder" in t.schema_["properties"] and "tax_details" not in t.schema_["properties"]
    t = apply_edit(following(), SetCategory(op="set_category", path="investor.tax_id", category=""))
    assert "tax_id" in t.schema_["required"] and "required" not in _prop(t, "investor")
    assert t.readers["tax_id"] == hand_template().readers["investor.tax_id"]


def test_set_category_refuses_a_saved_template():
    with pytest.raises(FormcastError, match="keep their category"):
        apply_edit(hand_template(), SetCategory(op="set_category", path="investor.country", category=""))


def test_moving_a_nested_field_up_into_its_own_category_keeps_the_category():
    base = following(hand_template())
    wire = base.schema_["properties"].pop("wire")
    base.schema_["properties"]["outer"] = {"type": "object", "properties": {"wire": wire}}
    base = base.model_copy(update={"readers": {
        p.replace("wire.", "outer.wire."): r for p, r in base.readers.items()}})
    t = apply_edit(base, SetCategory(op="set_category", path="outer.wire.bank", category="outer"))
    assert "bank" in _prop(t, "outer")["properties"]


def test_set_category_finds_a_category_by_its_title():
    template = following()
    title = _prop(template, "wire").setdefault("title", "Wire instructions")
    t = apply_edit(template, SetCategory(op="set_category", path="investor.country", category=title.upper()))
    assert "country" in _prop(t, "wire")["properties"]


def test_set_category_refuses_what_cannot_move():
    with pytest.raises(FormcastError, match="repeated group stays"):
        apply_edit(following(), SetCategory(op="set_category", path="owners[1].name", category="Investor"))
    with pytest.raises(FormcastError, match="repeated group, so"):
        apply_edit(following(), SetCategory(op="set_category", path="investor.country", category="owners"))
    with pytest.raises(FormcastError, match="letter or number"):
        apply_edit(following(), SetCategory(op="set_category", path="investor.country", category="#"))


def test_rename_field_refuses_the_name_of_another_field_beside_it():
    with pytest.raises(FormcastError, match="already a field named 'dob'"):
        apply_edit(hand_template(), RenameField(op="rename_field", path="investor.tax_id", name="dob"))


def test_removing_the_last_field_of_a_group_removes_the_group():
    t = apply_edit(hand_template(), RemoveField(op="remove_field", path="wire.bank"))
    assert "bank" not in _prop(t, "wire")["properties"]
    t = apply_edit(t, RemoveField(op="remove_field", path="wire.account"))
    assert "wire" not in t.schema_["properties"]
    t = apply_edit(t, RemoveField(op="remove_field", path="owners[0].name"))
    t = apply_edit(t, RemoveField(op="remove_field", path="owners[0].percent"))
    assert "owners" not in t.schema_["properties"] and not any(p.startswith("owners") for p in t.readers)


def test_an_unknown_path_is_refused():
    with pytest.raises(FormcastError, match="no field"):
        apply_edit(hand_template(), SetField(op="set_field", path="nope", title="x"))
    for edit in (
        SetBox(op="set_box", path="owners[9].name", page=0, rect=(1, 2, 3, 4)),
        SetLabels(op="set_labels", path="investor", labels=["x"]),
        SetClean(op="set_clean", path="nope", clean=[]),
        MarkAbsent(op="mark_absent", path="nope"),
        Restore(op="restore", path="nope"),
        RemoveField(op="remove_field", path="owners"),
    ):
        with pytest.raises(FormcastError, match="no field"):
            apply_edit(hand_template(), edit)


def test_an_edit_is_read_by_its_op_and_unknown_keys_are_refused():
    parse = TypeAdapter(Edit).validate_python
    assert isinstance(parse({"op": "mark_absent", "path": "wire.bank"}), MarkAbsent)
    assert isinstance(parse({"op": "set_box", "path": "wire.bank", "page": 0, "rect": [1, 2, 3, 4]}), SetBox)
    with pytest.raises(ValidationError):
        parse({"op": "set_field", "path": "wire.bank", "colour": "red"})
    with pytest.raises(ValidationError):
        parse({"op": "explode", "path": "wire.bank"})


# ------------------------------------------------------------------ boxes drawn on a sample


def _two_samples():
    """The hand template with two flattened samples: one in place, one pushed down the page by ``SHIFT``."""
    people = clients(2, seed=21)
    pdfs = [make_form(people[0], kind="flattened"), make_form(people[1], kind="flattened", shift=SHIFT)]
    records = [SampleRecord(file=f"s{n}.pdf", sha256=hashlib.sha256(p).hexdigest(), opinion=truth(c))
               for n, (p, c) in enumerate(zip(pdfs, people))]  # fmt: skip
    return hand_template().model_copy(update={"samples": records}), pdfs


def _down(rect, dy):
    return (rect[0], rect[1] + dy, rect[2], rect[3] + dy)


def test_a_box_drawn_on_a_shifted_sample_is_moved_into_the_layouts_place():
    t, pdfs = _two_samples()
    drawn = _down(FIELD_RECTS["dob"], SHIFT)  # where the date of birth is on the shifted sample
    on_it = apply_edit(t, SetBox(path="investor.dob", page=0, rect=drawn, sample=1), pdfs=pdfs)
    assert on_it.readers["investor.dob"].strategies[1].rect == pytest.approx(FIELD_RECTS["dob"], abs=0.5)
    in_place = apply_edit(t, SetBox(path="investor.dob", page=0, rect=FIELD_RECTS["dob"], sample=0), pdfs=pdfs)
    assert in_place.readers["investor.dob"].strategies[1].rect == pytest.approx(FIELD_RECTS["dob"], abs=0.5)
    as_given = apply_edit(t, SetBox(path="investor.dob", page=0, rect=drawn))  # no sample: the layout's place
    assert as_given.readers["investor.dob"].strategies[1].rect == drawn
    square = _down(TYPE_BOXES["trust"][1], SHIFT)
    option = apply_edit(t, SetOptionBox(path="investor.type", answer="trust", page=0, rect=square, sample=1), pdfs=pdfs)
    trust = option.readers["investor.type"].strategies[1].options["trust"]
    assert trust.rect == pytest.approx(TYPE_BOXES["trust"][1], abs=0.5)


def test_a_box_drawn_on_a_sample_needs_the_templates_own_sample_pdfs():
    t, pdfs = _two_samples()
    edit = SetBox(path="investor.dob", page=0, rect=FIELD_RECTS["dob"], sample=1)
    with pytest.raises(FormcastError, match="sample PDFs"):
        apply_edit(t, edit)
    with pytest.raises(FormcastError, match="not the template's samples"):
        apply_edit(t, edit, pdfs=pdfs[::-1])
    with pytest.raises(FormcastError, match="no sample 2"):
        apply_edit(t, edit.model_copy(update={"sample": 2}), pdfs=pdfs)
    with pytest.raises(FormcastError, match="no page 5"):
        apply_edit(t, edit.model_copy(update={"page": 5}), pdfs=pdfs)


# ------------------------------------------------------------------- option sets


BOX_A, BOX_B = (60.0, 500.0, 72.0, 512.0), (60.0, 520.0, 72.0, 532.0)


def with_checkboxes(template=None, *, opinions=None, setup=None):
    """A template with three yes-or-no fields in ``investor``: read as a form field, a choice, and a drawn box."""
    base = (template or hand_template()).model_dump(by_alias=True)
    investor = base["schema"]["properties"]["investor"]
    investor["properties"] = {
        **investor["properties"],
        "is_trust": {"type": "boolean", "title": "Trust"},
        "is_llc": {"type": "boolean", "title": "LLC"},
        "is_other": {"type": "boolean", "title": "Other (see instructions)"},
    }
    t = ExtractionTemplate.model_validate({**base, "setup": setup, "samples": opinions or []})
    t.readers["investor.is_trust"] = Reader(strategies=[WidgetRead(names=["trust_box", "trust_box_2024"]), BoxRead(page=0, rect=BOX_A)])
    t.readers["investor.is_llc"] = Reader(strategies=[
        ChoiceRead(options={"yes": ChoiceMark(widget="llc_box")}),
        ChoiceRead(options={"yes": ChoiceMark(page=0, rect=BOX_B)}),
    ])
    t.readers["investor.is_other"] = Reader(strategies=[BoxRead(page=0, rect=BOX_B)])
    return t


def link(template, *paths, title="Tax classification"):
    return apply_edit(template, MakeOptionSet(op="make_option_set", path=paths[0], others=list(paths[1:]), title=title))


def test_linked_checkboxes_become_one_field_with_an_answer_for_each_box():
    t = link(with_checkboxes(), "investor.is_trust", "investor.is_llc", "investor.is_other")
    node = _prop(t, "investor", "tax_classification")
    assert (node["type"], node["title"]) == ("string", "Tax classification")
    assert node["enum"] == ["Trust", "LLC", "Other (see instructions)"]
    keys = list(_prop(t, "investor")["properties"])
    assert not {"is_trust", "is_llc", "is_other"} & set(keys)
    assert keys.index("tax_classification") == len(keys) - 1  # where the first of them was
    widgets, boxes = t.readers["investor.tax_classification"].strategies
    assert widgets.options == {"Trust": ChoiceMark(widget="trust_box"), "LLC": ChoiceMark(widget="llc_box")}
    assert boxes.options == {
        "Trust": ChoiceMark(page=0, rect=BOX_A),
        "LLC": ChoiceMark(page=0, rect=BOX_B),
        "Other (see instructions)": ChoiceMark(page=0, rect=BOX_B),
    }
    assert not {"investor.is_trust", "investor.is_llc", "investor.is_other"} & set(t.readers)


def test_a_linked_set_is_required_when_one_of_its_fields_was():
    base = apply_edit(with_checkboxes(), SetField(op="set_field", path="investor.is_llc", required=True))
    t = link(base, "investor.is_trust", "investor.is_llc")
    assert "tax_classification" in _prop(t, "investor")["required"]
    assert "is_llc" not in _prop(t, "investor")["required"]
    assert _prop(t, "investor", "tax_classification")[REASON_KEY] == "Set by you"


def test_the_samples_readings_of_the_boxes_become_the_answer_of_the_set():
    sample = SampleRecord(file="a.pdf", sha256="0" * 64, opinion={"investor.is_trust": False, "investor.is_llc": True})
    blank = SampleRecord(file="b.pdf", sha256="1" * 64, opinion={"investor.is_trust": False, "investor.is_llc": False})
    both = SampleRecord(file="c.pdf", sha256="2" * 64, opinion={"investor.is_trust": True, "investor.is_llc": "yes"})
    asked = SampleRecord(file="d.pdf", sha256="3" * 64, opinion={"investor.tax_id": "1"})
    t = link(with_checkboxes(opinions=[sample, blank, both, asked]), "investor.is_trust", "investor.is_llc")
    assert t.samples[0].opinion == {"investor.tax_classification": "LLC"}
    assert t.samples[1].opinion == {"investor.tax_classification": None}
    assert t.samples[2].opinion == {"investor.tax_classification": "Trust, LLC"}
    assert t.samples[3].opinion == {"investor.tax_id": "1"}  # the model was not asked about these boxes


def test_a_set_takes_over_the_lines_its_fields_came_from():
    setup = SetupInfo(start="lines", detect="auto", lines=["boxes"], line_paths={"boxes": ["investor.is_trust", "investor.is_llc", "investor.dob"]})
    t = link(with_checkboxes(setup=setup.model_dump()), "investor.is_trust", "investor.is_llc")
    assert t.setup.line_paths == {"boxes": ["investor.dob", "investor.tax_classification"]}


def test_a_box_joins_a_set_that_already_exists():
    t = link(with_checkboxes(), "investor.is_trust", "investor.is_llc")
    t = link(t, "investor.tax_classification", "investor.is_other", title="Classification")
    assert _prop(t, "investor", "classification")["enum"] == ["Trust", "LLC", "Other (see instructions)"]


def test_a_field_with_no_title_answers_with_its_name_in_words():
    t = with_checkboxes()
    del _prop(t, "investor", "is_llc")["title"]
    assert _prop(link(t, "investor.is_trust", "investor.is_llc"), "investor", "tax_classification")["enum"] == ["Trust", "Is llc"]


def test_a_set_can_keep_the_name_of_one_of_its_fields():
    t = link(with_checkboxes(), "investor.is_trust", "investor.is_llc", title="Is LLC")
    assert _prop(t, "investor", "is_llc")["enum"] == ["Trust", "LLC"]
    assert "investor.is_trust" not in t.readers


@pytest.mark.parametrize(("paths", "title", "why"), [
    (("investor.is_trust", "investor.tax_id"), "Set", "Taxpayer ID is not a checkbox"),
    (("investor.is_trust", "owners[0].name"), "Set", "repeated group"),
    (("investor.is_trust", "investor.gone"), "Set", "no field investor.gone"),
    (("investor.is_trust", "investor.is_trust"), "Set", "at least two"),
    (("investor.is_trust", "investor.is_llc"), "  ", "a letter or number"),
    (("investor.is_trust", "investor.is_llc"), "Tax ID", "already a field"),
])
def test_what_cannot_be_linked_is_refused_with_why(paths, title, why):
    with pytest.raises(FormcastError, match=why):
        link(with_checkboxes(), *paths, title=title)


def test_a_field_with_no_checkbox_on_the_form_cannot_be_linked():
    t = apply_edit(with_checkboxes(), MarkAbsent(op="mark_absent", path="investor.is_llc"))
    with pytest.raises(FormcastError, match="LLC has no checkbox"):
        link(t, "investor.is_trust", "investor.is_llc")


def test_a_set_comes_apart_into_a_yes_or_no_field_for_each_answer():
    t = link(with_checkboxes(), "investor.is_trust", "investor.is_llc", "investor.is_other")
    apart = apply_edit(t, SplitOptionSet(op="split_option_set", path="investor.tax_classification"))
    assert "tax_classification" not in _prop(apart, "investor")["properties"]
    assert _prop(apart, "investor", "trust") == {"type": "boolean", "title": "Trust"}
    assert _prop(apart, "investor", "other_see_instructions")["title"] == "Other (see instructions)"
    assert apart.readers["investor.llc"].strategies == [
        ChoiceRead(options={"yes": ChoiceMark(widget="llc_box")}),
        ChoiceRead(options={"yes": ChoiceMark(page=0, rect=BOX_B)}),
    ]


def test_coming_apart_gives_each_box_the_samples_reading():
    sample = SampleRecord(file="a.pdf", sha256="0" * 64, opinion={"investor.is_trust": False, "investor.is_llc": True})
    t = link(with_checkboxes(opinions=[sample]), "investor.is_trust", "investor.is_llc")
    apart = apply_edit(t, SplitOptionSet(op="split_option_set", path="investor.tax_classification"))
    assert apart.samples[0].opinion == {"investor.trust": False, "investor.llc": True}


def test_only_a_set_comes_apart():
    with pytest.raises(FormcastError, match="not an option set"):
        apply_edit(hand_template(), SplitOptionSet(op="split_option_set", path="investor.tax_id"))
