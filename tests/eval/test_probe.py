import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import probe  # noqa: E402  (eval/probe.py)
import score  # noqa: E402  (eval/score.py)
from formcast.schema import flatten_schema, validate_data  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FORMS = REPO / "forms"
MANIFEST = json.loads((FORMS / "manifest.json").read_text())["forms"]


def _obj(**props):
    return {"type": "object", "properties": props}


def _gt(path, rect, page=0):
    return score.GTField(page=page, rect=rect, type="text", path=path)


def _walk(value):
    """Every scalar in a record, depth first."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def _tokens(records):
    return [v for r in records for v in _walk(r) if isinstance(v, str) and v.isalpha() and v.isupper()]


def _trigrams(token):
    return {token[i : i + 3] for i in range(len(token) - 2)}


# ---------------------------------------------------------------- K and enums


def test_k_covers_largest_enum():
    schema = _obj(
        size={"enum": ["a", "b", "c", "d", "e"]},
        tag={"enum": ["x", "y"]},
        name={"type": "string"},
    )
    records = probe.probe_records(schema, [], seed="t")
    assert len(records) == 5
    assert [r["size"] for r in records] == ["a", "b", "c", "d", "e"]
    assert [r["tag"] for r in records] == ["x", "y", "x", "y", "x"]


def test_k_is_at_least_two_and_counts_multi_select_options():
    assert len(probe.probe_records(_obj(name={"type": "string"}), [], seed="t")) == 2
    schema = _obj(picks={"type": "array", "uniqueItems": True, "items": {"enum": ["a", "b", "c", "d"]}})
    records = probe.probe_records(schema, [], seed="t")
    assert len(records) == 4
    assert [set(r["picks"]) for r in records] == [{"a", "b"}, {"b", "c"}, {"c", "d"}, {"d", "a"}]
    seen = {x for r in records for x in r["picks"]}
    assert seen == {"a", "b", "c", "d"}


def test_non_string_enum_values_keep_their_type():
    schema = _obj(n={"enum": [1, 2, 3]})
    assert [r["n"] for r in probe.probe_records(schema, [], seed="t")] == [1, 2, 3]


def test_booleans_alternate():
    records = probe.probe_records(_obj(flag={"type": "boolean"}, size={"enum": list("abcd")}), [], seed="t")
    assert [r["flag"] for r in records] == [True, False, True, False]


# -------------------------------------------------------------------- strings


def test_tokens_have_unique_trigrams():
    schema = _obj(**{f"s{i}": {"type": "string"} for i in range(40)}, size={"enum": list("abcde")})
    records = probe.probe_records(schema, [], seed="t")
    tokens = _tokens(records)
    assert len(tokens) == 40 * 5
    assert all(len(t) == probe.TOKEN_LEN for t in tokens)
    seen = set()
    for t in tokens:
        grams = _trigrams(t)
        assert not (grams & seen), t
        seen |= grams


def test_max_length_shortens_tokens():
    schema = _obj(code={"type": "string", "maxLength": 4}, one={"type": "string", "maxLength": 1})
    for r in probe.probe_records(schema, [], seed="t"):
        assert len(r["code"]) == 4
        assert len(r["one"]) == 1
        assert r["code"].isupper() and r["one"].isupper()


def test_min_length_is_honoured():
    schema = _obj(long={"type": "string", "minLength": 9})
    for r in probe.probe_records(schema, [], seed="t"):
        assert len(r["long"]) >= 9


def test_email_and_other_formats_conform():
    schema = _obj(
        mail={"type": "string", "format": "email"},
        when={"type": "string", "format": "date-time"},
    )
    for r in probe.probe_records(schema, [], seed="t"):
        assert re.fullmatch(r"[a-z]+@probe\.test", r["mail"])
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["when"])


def test_deterministic_for_same_seed_and_different_across_seeds():
    schema = _obj(a={"type": "string"}, b={"type": "integer", "minimum": 0}, d={"type": "string", "format": "date"})
    assert probe.probe_records(schema, [], seed="x") == probe.probe_records(schema, [], seed="x")
    assert probe.probe_records(schema, [], seed="x") != probe.probe_records(schema, [], seed="y")


# --------------------------------------------------------------------- dates


def test_dates_distinct_parts():
    schema = _obj(**{f"d{i}": {"type": "string", "format": "date"} for i in range(4)})
    records = probe.probe_records(schema, [], seed="t")
    parts = {"y": [], "m": [], "d": []}
    for r in records:
        for v in r.values():
            y, m, d = map(int, v.split("-"))
            assert 1950 <= y <= 2020 and 1 <= d <= 28 and 1 <= m <= 12
            parts["y"].append(y)
            parts["m"].append(m)
            parts["d"].append(d)
    for name, values in parts.items():
        assert len(values) == len(set(values)), name  # 8 dates: every day, month, year differs
    assert validate_data(schema, records[0]) == []


# ------------------------------------------------------------------- numbers


def test_numbers_are_distinct_and_in_range():
    schema = _obj(
        a={"type": "integer", "minimum": 0},
        b={"type": "number", "minimum": 0},
        c={"type": "integer", "minimum": 1, "maximum": 12},
        d={"type": "integer", "minimum": 0, "maximum": 5000},
    )
    records = probe.probe_records(schema, [], seed="t")
    for key in ("a", "b", "d"):
        values = [r[key] for r in records]
        assert len(set(values)) == len(values)
        assert all(v >= 100 for v in values)  # three digits where the range allows
    assert all(1 <= r["c"] <= 12 for r in records)
    assert all(r["d"] <= 5000 for r in records)
    assert all(isinstance(r["a"], int) for r in records)


# -------------------------------------------------------------------- tables


TABLE = _obj(
    rows={
        "type": "array",
        "items": _obj(item={"type": "string"}, qty={"type": "integer", "minimum": 0}),
    }
)


def test_table_rows_default_to_two_without_gt():
    for r in probe.probe_records(TABLE, None, seed="t"):
        assert len(r["rows"]) == 2


def test_table_rows_gt_plus_one_capped_by_maxitems():
    gt = [_gt("rows[].item", (10, 10 + 20 * i, 90, 25 + 20 * i)) for i in range(3)]
    gt += [_gt("rows[].qty", (100, 10 + 20 * i, 150, 25 + 20 * i)) for i in range(3)]
    assert probe.table_rows_in_gt(gt) == {"rows": 3}
    records = probe.probe_records(TABLE, gt, seed="t")
    assert all(len(r["rows"]) == 4 for r in records)
    cells = [row["item"] for r in records for row in r["rows"]]
    assert len(set(cells)) == len(cells)  # every cell fresh

    capped = json.loads(json.dumps(TABLE))
    capped["properties"]["rows"]["maxItems"] = 3
    assert all(len(r["rows"]) == 3 for r in probe.probe_records(capped, gt, seed="t"))


def test_nested_objects_and_list_rows():
    schema = _obj(
        person=_obj(name={"type": "string"}, home=_obj(city={"type": "string"})),
        notes={"type": "array", "items": {"type": "string"}},
    )
    gt = [_gt("notes[]", (10, 10 + 20 * i, 90, 25 + 20 * i)) for i in range(4)]
    records = probe.probe_records(schema, gt, seed="t")
    assert all(len(r["notes"]) == 5 for r in records)
    assert all(isinstance(r["person"]["home"]["city"], str) for r in records)
    assert probe.table_rows_in_gt(gt) == {"notes": 4}


def test_gt_rows_order_by_page_then_top_and_share_tops_within_2pt():
    gt = [
        _gt("t[].a", (10, 100.0, 50, 112)),  # 0: page 0, second row
        _gt("t[].a", (10, 50.0, 50, 62)),  # 1: page 0, first row
        _gt("t[].a", (10, 30.0, 50, 42), page=1),  # 2: page 1, third row
        _gt("t[].b", (60, 51.5, 90, 62)),  # 3: first row of column b
        _gt("t[].a", (10, 200.0, 50, 212)),  # 4: not a... plain column a, row 3
        _gt("name", (10, 5, 50, 15)),  # 5: not a table box
        _gt("t[].a", (70, 100.5, 120, 112)),  # 6: same top as 0, to its right -> same row
    ]
    rows = probe.gt_rows(gt)
    assert 5 not in rows
    assert rows[1] == 0
    assert rows[0] == 1
    assert rows[6] == 1
    assert rows[4] == 2
    assert rows[2] == 3
    assert rows[3] == 0
    # table path = column path before the last "[]"; rows = max over its columns
    assert probe.table_rows_in_gt(gt) == {"t": 4}


def test_gt_rows_keep_sparse_columns_aligned_with_the_table():
    gt = [
        _gt("t[].num", (10, 100.0, 50, 112)),
        _gt("t[].num", (10, 200.0, 50, 212)),
        _gt("t[].num", (10, 300.0, 50, 312)),
        _gt("t[].title", (60, 100.5, 90, 112)),  # row 0
        _gt("t[].title", (60, 300.0, 90, 312)),  # row 2, though it is the column's second box
    ]
    rows = probe.gt_rows(gt)
    assert [rows[i] for i in range(5)] == [0, 1, 2, 0, 2]
    assert probe.table_rows_in_gt(gt) == {"t": 3}


def test_gt_rows_continue_across_pages_and_cluster_per_page():
    gt = [
        _gt("t[].a", (10, 700.0, 50, 712), page=0),
        _gt("t[].b", (60, 40.0, 90, 52), page=1),
        _gt("t[].a", (10, 40.5, 50, 52), page=1),
    ]
    rows = probe.gt_rows(gt)
    assert (rows[0], rows[1], rows[2]) == (0, 1, 1)


def test_two_tables_do_not_share_rows():
    gt = [_gt("a[].x", (10, 10, 50, 20)), _gt("b[].x", (10, 10, 50, 20)), _gt("b[].x", (10, 40, 50, 50))]
    assert probe.table_rows_in_gt(gt) == {"a": 1, "b": 2}


def test_table_path_uses_last_array_marker():
    gt = [_gt("short_term.transactions[].description", (10, 10, 50, 20)),
          _gt("short_term.transactions[].description", (10, 30, 50, 40)),
          _gt("short_term.transactions[].proceeds", (60, 10, 90, 20))]
    assert probe.table_rows_in_gt(gt) == {"short_term.transactions": 2}


# ----------------------------------------------------------- patterns, errors


PATTERN = _obj(ssn={"type": "string", "pattern": r"^\d{3}-\d{2}-\d{4}$"}, name={"type": "string"})


def test_pattern_without_override_raises():
    with pytest.raises(ValueError, match=r"fx: no probe value for pattern path ssn; add forms/probes/fx\.json"):
        probe.probe_records(PATTERN, [], seed="fx")


def test_pattern_override_is_used_per_record():
    overrides = {"ssn": ["123-45-6789", "987-65-4321"]}
    records = probe.probe_records(PATTERN, [], overrides=overrides, seed="fx")
    assert [r["ssn"] for r in records] == ["123-45-6789", "987-65-4321"]


def test_override_scalar_applies_to_every_record_and_short_list_raises():
    scalar = probe.probe_records(PATTERN, [], overrides={"ssn": "111-22-3333"}, seed="fx")
    assert {r["ssn"] for r in scalar} == {"111-22-3333"}
    with pytest.raises(ValueError, match="ssn"):
        probe.probe_records(PATTERN, [], overrides={"ssn": ["111-22-3333"]}, seed="fx")


def test_override_for_unknown_path_raises():
    with pytest.raises(ValueError, match="nope"):
        probe.probe_records(_obj(a={"type": "string"}), [], overrides={"nope": "x"}, seed="t")


def test_table_cell_override_is_per_record_list_of_rows():
    schema = _obj(rows={"type": "array", "items": _obj(code={"type": "string", "pattern": r"^\d{2}$"})})
    overrides = {"rows[].code": [["11", "12"], ["21", "22"]]}
    records = probe.probe_records(schema, [], overrides=overrides, seed="t")
    assert [[row["code"] for row in r["rows"]] for r in records] == [["11", "12"], ["21", "22"]]


def test_tokens_share_no_trigram_with_enum_options_or_consts():
    schema = _obj(
        size={"enum": ["SMALL", "LARGE", "ABCDEF"]},
        picks={"type": "array", "items": {"enum": ["Quick Brown", "fox-jumps"]}},
        fixed={"const": "HELLO WORLD"},
        **{f"tok{i}": {"type": "string"} for i in range(150)},
    )
    given = {"SMA", "MAL", "ALL", "LAR", "ARG", "RGE", "GE"} | {"ABC", "BCD", "CDE", "DEF"}
    given |= {"QUI", "UIC", "ICK", "BRO", "ROW", "OWN", "FOX", "JUM", "UMP", "MPS", "HEL", "ELL", "LLO", "WOR", "ORL", "RLD"}
    for r in probe.probe_records(schema, [], seed="t"):
        for key, value in r.items():
            if key.startswith("tok"):
                assert not _trigrams(value) & given, value


def test_override_digit_groups_are_kept_out_of_generated_numbers_and_years():
    schema = _obj(
        phone={"type": "string", "pattern": r"^\d{3}-\d{4}$"},
        amount={"type": "integer", "minimum": 100, "maximum": 130},
        when={"type": "string", "format": "date"},
    )
    overrides = {"phone": [f"{100 + i}-19{50 + i}" for i in range(2)] + [f"{110 + i}-2000" for i in range(0)]}
    for r in probe.probe_records(schema, [], overrides=overrides, seed="t"):
        reserved = {100, 101}
        assert r["amount"] not in reserved
        assert int(r["when"][:4]) not in {1950, 1951}


def test_invalid_record_raises_with_messages():
    schema = _obj(b={"type": "string", "not": {}})  # nothing can satisfy this
    with pytest.raises(ValueError, match="invalid"):
        probe.probe_records(schema, [], seed="t")


def test_load_overrides(tmp_path):
    assert probe.load_overrides("definitely_not_a_form") is None
    (tmp_path / "f.json").write_text('{"a": ["1", "2"]}')
    assert probe.load_overrides("f", probes_dir=tmp_path) == {"a": ["1", "2"]}


# ---------------------------------------------------------------- the corpus


def _pdf(form):
    return FORMS / form["split"] / f"{form['id']}.pdf"


@pytest.mark.parametrize("form", MANIFEST, ids=lambda f: f["id"])
def test_records_validate_for_every_corpus_schema(form):
    schema = json.loads((FORMS / "schemas" / f"{form['id']}.json").read_text())
    gt = score.load_gt(form, _pdf(form))
    records = probe.probe_records(
        schema, gt, overrides=probe.load_overrides(form["id"]), seed=form["id"], form_id=form["id"]
    )
    assert len(records) >= 2
    for rec in records:
        assert validate_data(schema, rec) == []
    again = probe.probe_records(
        schema, gt, overrides=probe.load_overrides(form["id"]), seed=form["id"], form_id=form["id"]
    )
    assert again == records
    # Enum options are given, not generated: tokens must not share a trigram with their letters either.
    option_text = " ".join(o for p in flatten_schema(schema) for o in p.enum or []).upper()
    seen = {g for run in re.findall(r"[A-Z]+", option_text) for g in _trigrams(run)}
    for t in _tokens(records):
        if len(t) >= 3 and t not in set(option_text.split()):
            grams = _trigrams(t)
            assert not (grams & seen), (form["id"], t)
            seen |= grams


def test_big_forms_get_shorter_tokens_instead_of_repeated_trigrams():
    schema = _obj(**{f"s{i}": {"type": "string"} for i in range(1500)})
    records = probe.probe_records(schema, [], seed="t")  # 3000 tokens of 6 letters would need 12000 trigrams
    seen = set()
    for t in _tokens(records):
        assert 3 <= len(t) <= probe.TOKEN_LEN
        assert not _trigrams(t) & seen
        seen |= _trigrams(t)


def test_i9_sparse_column_boxes_share_the_row_of_the_table():
    form = next(f for f in MANIFEST if f["id"] == "i9")
    gt = score.load_gt(form, _pdf(form))
    rows = probe.gt_rows(gt)
    by_path = {}
    for i, g in enumerate(gt):
        if g.path and g.path.startswith("reverifications[]."):
            by_path.setdefault(g.path, []).append((g.page, g.rect[1], rows[i]))
    number = sorted(by_path["reverifications[].document_number"])
    title = sorted(by_path["reverifications[].document_title"])
    # every document_title box sits in the row of the document_number box at the same height
    for page, top, row in title:
        assert any(p == page and abs(t - top) <= 2 and r == row for p, t, r in number), (page, top, row)
    assert len({r for _, _, r in number}) == len(number)
    assert title[-1][2] == number[-1][2]  # the second title box matches the third number box
