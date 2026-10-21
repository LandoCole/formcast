"""Widget binding: named, adopted, created. Pure logic, no PDF."""

from __future__ import annotations

from formcast.render.binding import (
    ExistingWidget,
    Target,
    bind,
    cell_key,
    widget_name_for,
)


def W(name, kind="text", rect=(100, 100, 200, 112), page=0, index=0, xref=None, on_state=None, max_len=None, options=()):
    return ExistingWidget(page, index, xref if xref is not None else 100 + index, name, kind, rect, on_state, max_len, options)


def T(key, ftype="text", rect=(100, 100, 200, 112), page=0, existing=None, field_id=None):
    return Target(key, field_id or key, ftype, page, rect, existing)


def codes(b):
    return [w.code for w in b.warnings]


def test_widget_name_for():
    assert cell_key("t_meds", 2, "drug") == "t_meds[2].drug"
    assert widget_name_for("t_meds[2].drug") == "t_meds_r2_drug"
    assert widget_name_for("f_name") == "f_name"


def test_named_single_widget_used_regardless_of_rect():
    w = W("name", rect=(400, 700, 500, 712), index=3)
    b = bind([T("f", rect=(10, 10, 50, 22), existing="name")], [w])
    assert b["f"].how == "named"
    assert b["f"].widget is w
    assert b["f"].new_name is None
    assert b["f"].warnings == []


def test_radio_kid_picked_by_rect():
    kids = [
        ExistingWidget(0, 0, 10, "group_1", "radio", (475, 76, 486, 83), "Yes_5", None, ()),
        ExistingWidget(0, 1, 11, "group_1", "radio", (505, 76, 516, 83), "No", None, ()),
    ]
    b = bind([Target("f_no", "f_no", "radio_option", 0, (504, 75, 517, 84), "group_1")], kids)
    assert b["f_no"].how == "named" and b["f_no"].widget.on_state == "No"


def test_named_tie_broken_by_centre_distance():
    # Both kids lie fully inside the target, so their IoU is equal; the nearer centre wins.
    far = W("g", "radio", rect=(0, 0, 10, 10), index=0, on_state="far")
    near = W("g", "radio", rect=(15, 0, 25, 10), index=1, on_state="near")
    target = T("f", "radio_option", rect=(0, 0, 30, 10), existing="g")
    assert bind([target], [far, near])["f"].widget.on_state == "near"
    assert bind([target], [near, far])["f"].widget.on_state == "near"


def test_named_widget_not_found_when_no_kid_overlaps():
    kids = [W("g", "radio", rect=(0, 0, 10, 10), index=0), W("g", "radio", rect=(20, 0, 30, 10), index=1)]
    b = bind([T("f", "radio_option", rect=(100, 100, 110, 110), existing="g")], kids)
    assert b["f"].how == "none" and b["f"].widget is None
    assert codes(b["f"]) == ["widget_not_found"]
    w = b["f"].warnings[0]
    assert w.field == "f" and w.page == 0 and w.rect == (100, 100, 110, 110)


def test_named_widget_missing_name_not_found():
    b = bind([T("f", existing="nope")], [W("other")])
    assert b["f"].how == "none" and codes(b["f"]) == ["widget_not_found"]


def test_named_widget_on_other_page_not_found():
    b = bind([T("f", page=0, existing="name")], [W("name", page=1)])
    assert b["f"].how == "none" and codes(b["f"]) == ["widget_not_found"]


def test_type_mismatch():
    b = bind([T("f", "checkbox", existing="name")], [W("name", "text")])
    assert b["f"].how == "none" and b["f"].widget is None
    assert codes(b["f"]) == ["type_mismatch"]


def test_type_mismatch_button():
    b = bind([T("f", "text", existing="name")], [W("name", "button")])
    assert codes(b["f"]) == ["type_mismatch"]


def test_mismatched_named_widget_is_not_adopted_by_others():
    w = W("name", "text", rect=(100, 100, 200, 112))
    b = bind([T("f", "checkbox", existing="name"), T("g", "text", rect=(90, 95, 210, 120))], [w])
    assert b["f"].how == "none"
    assert b["g"].how == "created"


def test_adopts_widget_under_table_cell():
    w = W("Row1", rect=(102, 106, 150, 118), index=2)
    key = cell_key("t", 0, "drug")
    b = bind([Target(key, "t", "text", 0, (100, 100, 200, 124), None)], [w])
    assert b[key].how == "adopted" and b[key].widget is w and b[key].new_name is None
    assert codes(b[key]) == ["adopted"]
    msg = b[key].warnings[0]
    assert "Row1" in msg.message and msg.severity == "info"


def test_adoption_picks_highest_iou():
    small = W("small", rect=(110, 105, 130, 117), index=0)
    big = W("big", rect=(100, 100, 200, 124), index=1)
    b = bind([T("f", rect=(100, 100, 200, 124))], [small, big])
    assert b["f"].widget is big


def test_adoption_needs_centre_inside():
    w = W("w", rect=(190, 100, 260, 112))  # centre at x=225, outside
    b = bind([T("f", rect=(100, 100, 200, 124))], [w])
    assert b["f"].how == "created"


def test_adoption_other_page_ignored():
    b = bind([T("f", page=0)], [W("w", page=1)])
    assert b["f"].how == "created"


def test_adoption_skips_claimed_and_incompatible_widgets():
    named = W("named", rect=(100, 100, 150, 112), index=0)
    cb = W("cb", "checkbox", rect=(100, 100, 150, 112), index=1)  # incompatible with text
    free = W("free", rect=(100, 100, 200, 112), index=2)
    targets = [
        T("a", rect=(0, 0, 400, 400), existing="named"),
        T("b", rect=(100, 100, 200, 112)),
    ]
    b = bind(targets, [named, cb, free])
    assert b["a"].how == "named" and b["a"].widget is named
    assert b["b"].how == "adopted" and b["b"].widget is free


def test_widgets_sharing_a_named_name_are_not_adopted():
    kid1 = W("grp", "radio", rect=(0, 0, 10, 10), index=0, on_state="A")
    kid2 = W("grp", "radio", rect=(100, 100, 110, 110), index=1, on_state="B")
    targets = [
        T("one", "radio_option", rect=(0, 0, 10, 10), existing="grp"),
        T("two", "radio_option", rect=(100, 100, 110, 110)),  # sits on kid2, which is not named
    ]
    b = bind(targets, [kid1, kid2])
    assert b["two"].how == "created"


def test_earlier_adoption_claims_widget_in_page_top_left_order():
    w = W("w", rect=(100, 100, 200, 112))
    targets = [
        T("lower", rect=(100, 104, 200, 130)),  # listed first but lower on the page
        T("upper", rect=(100, 98, 200, 120)),
    ]
    b = bind(targets, [w])
    assert b["upper"].how == "adopted"
    assert b["lower"].how == "created"


def test_adoption_order_is_page_then_top_then_left_then_key():
    w = W("w", rect=(100, 100, 200, 112))
    targets = [T("b", rect=(100, 100, 200, 124)), T("a", rect=(100, 100, 200, 124))]
    b = bind(targets, [w])
    assert b["a"].how == "adopted" and b["b"].how == "created"
    w0 = W("p0", page=0, rect=(100, 100, 200, 112))
    t_p1 = T("x", page=1, rect=(100, 100, 200, 124))
    t_p0 = T("y", page=0, rect=(100, 100, 200, 124))
    b = bind([t_p1, t_p0], [w0, W("p1", page=1, rect=(100, 100, 200, 112), index=1)])
    assert b["x"].widget.name == "p1" and b["y"].widget.name == "p0"


def test_choice_mark_never_adopts():
    cb = W("cb", "checkbox", rect=(100, 100, 112, 112))
    b = bind([T("m", "choice_mark", rect=(95, 95, 120, 120))], [cb])
    assert b["m"].how == "created" and b["m"].new_name == "m"


def test_choice_mark_ignores_existing_widget_name():
    cb = W("cb", "checkbox", rect=(100, 100, 112, 112))
    b = bind([T("m", "choice_mark", rect=(95, 95, 120, 120), existing="cb")], [cb])
    assert b["m"].how == "created" and b["m"].widget is None and b["m"].warnings == []


def test_created_names_unique():
    existing = [W("f1", rect=(0, 0, 5, 5), index=0)]
    targets = [
        T("f1", rect=(300, 300, 400, 312)),
        T(cell_key("t_meds", 0, "drug"), rect=(300, 320, 400, 332), field_id="t_meds"),
        T(cell_key("t_meds", 1, "drug"), rect=(300, 340, 400, 352), field_id="t_meds"),
    ]
    b = bind(targets, existing)
    assert b["f1"].how == "created" and b["f1"].new_name == "f1_2"
    assert b["t_meds[0].drug"].new_name == "t_meds_r0_drug"
    assert b["t_meds[1].drug"].new_name == "t_meds_r1_drug"


def test_created_name_collides_with_earlier_new_name():
    existing = [W("a", rect=(0, 0, 5, 5), index=0), W("a_2", rect=(0, 10, 5, 15), index=1)]
    targets = [T("a", rect=(300, 300, 400, 312)), T("a_2", rect=(300, 320, 400, 332)), T("a_3", rect=(300, 340, 400, 352))]
    b = bind(targets, existing)
    assert b["a"].new_name == "a_3"
    assert b["a_2"].new_name == "a_2_2"
    assert b["a_3"].new_name == "a_3_2"  # "a_3" was taken by the earlier new name


def test_signature_widget_not_writable_named_and_adopted():
    sig = W("sig", "signature", rect=(100, 100, 200, 112), index=0)
    b = bind([T("f", "signature", existing="sig")], [sig])
    assert b["f"].how == "named" and b["f"].widget is sig
    assert codes(b["f"]) == ["not_writable"]
    assert "signed in a PDF viewer" in b["f"].warnings[0].message
    b = bind([T("f", "signature", rect=(100, 98, 200, 124))], [sig])
    assert b["f"].how == "adopted" and codes(b["f"]) == ["adopted", "not_writable"]


def test_signature_field_adopts_text_widget_normally():
    b = bind([T("f", "signature", rect=(100, 98, 200, 124))], [W("t")])
    assert b["f"].how == "adopted" and codes(b["f"]) == ["adopted"]


def test_every_target_gets_a_binding():
    targets = [T("a"), T("b", existing="zzz"), T("c", "choice_mark")]
    assert set(bind(targets, [])) == {"a", "b", "c"}
