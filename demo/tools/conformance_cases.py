"""Plain data for the conformance fixtures: inputs only, no calls into formcast.

``export_conformance.py`` runs each case through the real Python library and records the
answer; the JavaScript ports are then held to those answers. Cases are JSON values, so a
few rules keep them faithful across the language boundary:

* no float whose value is a whole number below 1e16 (JSON cannot tell ``2.0`` from ``2``);
* no integer above 2**53 (JavaScript would round it), so a huge number is a numeric string;
* date formats stay within the directives every platform's C library agrees on.

``export_conformance.py`` checks the first two rules before it writes anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Marker for the ``[]`` path segment in ``parse_path`` / ``format_path`` cases (JSON has no
#: way to tell it from a property literally named ``"[]"``).
A_ITEM = {"array_item": True}


def T(op: str, **args: Any) -> dict:
    """One chain step."""
    return {"op": op, "args": args}


# ------------------------------------------------------------------ transforms


def _apply_cases() -> list[dict]:
    cases: list[dict] = []

    def add(group: str, label: str, values: list, *chain: dict) -> None:
        cases.append({"id": f"{group}/{label}", "kind": "apply", "values": values, "chain": list(chain)})

    def each(group: str, step: dict, values: list, labels: list[str] | None = None) -> None:
        """One single-value case per value, all through the same one-step chain."""
        for i, v in enumerate(values):
            add(group, labels[i] if labels else f"{i}-{json.dumps(v, ensure_ascii=True)[:24]}", [v], step)

    # split
    s = T("split", sep="-", index=2)
    each("split", s, ["123-45-6789", "a-b", "", None, 12345, False, ["a-b-c"], {"a": 1}, "--x", "é-ü-ñ"])
    add("split", "negative", ["Ada Lovelace"], T("split", sep=" ", index=-1))
    add("split", "negative-out", ["Ada Lovelace"], T("split", sep=" ", index=-3))
    add("split", "out-of-range", ["Ada Lovelace"], T("split", sep=" ", index=3))
    add("split", "multi-char-sep", ["a::b::c"], T("split", sep="::", index=1))
    add("split", "maps-over-values", ["a-b", "c-d"], T("split", sep="-", index=1))
    add("split", "empty-sep", ["abc"], T("split", sep="", index=0))
    add("split", "missing-index", ["abc"], T("split", sep="-"))
    add("split", "missing-sep", ["abc"], T("split", index=0))
    add("split", "index-string", ["abc"], T("split", sep="-", index="1"))
    add("split", "index-float", ["abc"], T("split", sep="-", index=1.5))
    add("split", "index-bool", ["abc"], T("split", sep="-", index=True))
    add("split", "sep-number", ["abc"], T("split", sep=1, index=0))
    add("split", "sep-null", ["abc"], T("split", sep=None, index=0))
    add("split", "extra-arg", ["abc"], T("split", sep="-", index=0, bogus=1))
    add("split", "two-extra-args", ["abc"], T("split", sep="-", index=0, zeta=1, alpha=2))

    # slice
    each("slice", T("slice", start=1, end=3), ["abcdef", "", None, 123456, "ab", True, "José", "a\U0001f600b"])
    add("slice", "start-only", ["abcdef"], T("slice", start=2))
    add("slice", "end-only", ["abcdef"], T("slice", end=2))
    add("slice", "no-args", ["abcdef"], T("slice"))
    add("slice", "negative-start", ["abcdef"], T("slice", start=-2))
    add("slice", "negative-end", ["abcdef"], T("slice", start=0, end=-1))
    add("slice", "end-before-start", ["abcdef"], T("slice", start=4, end=2))
    add("slice", "start-past-end", ["abc"], T("slice", start=9))
    add("slice", "emoji", ["a\U0001f600b"], T("slice", start=1, end=2))
    add("slice", "list", [["a", "b", "c"]], T("slice", start=0, end=2))
    add("slice", "list-negative", [["a", "b", "c"]], T("slice", start=-2))
    add("slice", "list-empty", [[]], T("slice", start=1))
    add("slice", "dict-input", [{"a": 1}], T("slice", start=0, end=1))
    add("slice", "start-string", ["abc"], T("slice", start="1"))
    add("slice", "end-null", ["abc"], T("slice", end=None))
    add("slice", "maps-over-values", ["abc", "def"], T("slice", start=1))

    # char_at
    each("char_at", T("char_at", index=1), ["abc", "", None, 123, "é", "a\U0001f600b", False, ["a"]])
    add("char_at", "negative", ["abc"], T("char_at", index=-1))
    add("char_at", "out-of-range", ["abc"], T("char_at", index=9))
    add("char_at", "negative-out-of-range", ["abc"], T("char_at", index=-4))
    add("char_at", "missing-index", ["abc"], T("char_at"))
    add("char_at", "index-string", ["abc"], T("char_at", index="0"))

    # join
    j = T("join", sep=" ")
    add("join", "names", ["Ada", "Lovelace"], j)
    add("join", "skips-none-and-empty", ["Ada", None, "", "Lovelace"], j)
    add("join", "all-missing", [None, ""], j)
    add("join", "no-values", [], j)
    add("join", "single", ["Ada"], j)
    add("join", "single-none", [None], j)
    add("join", "numbers", [1, 2, 3], T("join", sep="/"))
    add("join", "bools", [True, False], T("join", sep=","))
    add("join", "zero-is-kept", [0, "x"], T("join", sep="-"))
    add("join", "lone-list", [["a", "b"]], T("join", sep=", "))
    add("join", "lone-list-with-missing", [["a", None, "", "b"]], T("join", sep="+"))
    add("join", "lone-empty-list", [[]], j)
    add("join", "two-lists", [["a"], ["b"]], j)
    add("join", "list-among-scalars", ["a", ["b"]], j)
    add("join", "dict-among-scalars", ["a", {"k": 1}], j)
    add("join", "empty-sep", ["a", "b", "c"], T("join", sep=""))
    add("join", "unicode", ["José", "Zürich", "北京"], T("join", sep=" · "))
    add("join", "missing-sep", ["a", "b"], T("join"))
    add("join", "sep-number", ["a", "b"], T("join", sep=1))
    add("join", "extra-arg", ["a", "b"], T("join", sep="-", bogus=True))

    # upper / lower / title / strip
    texts = ["ada Lovelace", "", None, 12, True, "straße", "ﬁsh", "ΟΔΥΣΣΕΥΣ", "José Zürich 北京", ["a"], {"a": 1}]
    for op in ("upper", "lower", "title", "strip"):
        each(op, T(op), texts)
        add(op, "maps-over-values", ["aB", "cD"], T(op))
        add(op, "extra-arg", ["a"], T(op, bogus=1))
    add("title", "mixed", ["ada LOVELACE-king don't"], T("title"))
    add("title", "apostrophes-and-hyphens", ["o'neil mc-donald jr."], T("title"))
    add("title", "digits", ["3rd place 21ST"], T("title"))
    add("title", "whitespace", ["  two  spaces\tand\ttabs\nnewline "], T("title"))
    add("title", "hyphen-edges", ["-a-b-", "--"], T("title"))
    add("title", "accents", ["élan vital ÉCOLE"], T("title"))
    add("title", "emoji", ["\U0001f600abc DEF"], T("title"))
    add("title", "nbsp-and-ideographic-space", ["a b　c"], T("title"))
    add("title", "control-space", ["a\x1fb"], T("title"))
    add("title", "zero-width-space", ["a​b"], T("title"))
    add("title", "bom", ["a﻿b"], T("title"))
    add("strip", "spaces-and-newlines", ["  a b \n"], T("strip"))
    add("strip", "nbsp-and-ideographic-space", ["  x 　"], T("strip"))
    add("strip", "control-separators", ["\x1fx\x1c"], T("strip"))
    add("strip", "next-line", ["\x85x\x85"], T("strip"))
    add("strip", "bom-is-not-space", ["﻿x"], T("strip"))
    add("strip", "zero-width-space-is-not-space", ["​x​"], T("strip"))
    add("strip", "only-spaces", ["   "], T("strip"))
    add("upper", "dotted-i", ["i̇ı"], T("upper"))
    # numbers rendered as text follow Python's float repr (exponent form below 1e-4 and from 1e16)
    each("float-text", T("upper"), [1e16, 1e15 + 0.5, 1234567890123456.5, 1e-05, 0.0001, 0.00012345, 1.5e-07, 123456.789,
                                    2.5, -0.1, 1e22, 1.7976931348623157e308, 5e-324, 123456789012345.6, 0.1, -1e16, 12345678901234567890.0])
    add("float-text", "join", [1e16, 0.0001, 1e-05, 3, -7, 2.5], T("join", sep=" "))
    add("float-text", "prefix", [1.5e-10], T("prefix", value="#"))

    # date_format
    d = T("date_format", format="%m/%d/%Y")
    each(
        "date_format",
        d,
        ["2024-03-07", "", None, "07/03/2024", "2024-3-7", "20240307", "2024-02-30", "2023-02-29", 20240307, " ",
         ["2024-03-07"], "2024-03-07 ", "0000-01-01", "2024-13-01", "2024-00-10", "abc", True, {"d": 1}, "it's",
         'say "hi"', "tab\there", "2024-03-07\n", "​", "José"],
    )
    for label, fmt, value in [
        ("leap-day", "%d %b %Y", "2024-02-29"),
        ("long", "%A, %B %d, %Y", "2024-03-07"),
        ("weekday-short", "%a %b %d", "2024-03-07"),
        ("year2", "%y%m%d", "2024-03-07"),
        ("compact", "%Y%m%d", "2024-03-07"),
        ("day-of-year", "%j", "2024-03-07"),
        ("day-of-year-last", "%j", "2024-12-31"),
        ("time-parts", "%H:%M:%S %p", "2024-03-07"),
        ("literal-percent", "100%% on %Y", "2024-03-07"),
        ("no-directives", "static text", "2024-03-07"),
        ("unpadded", "%-m/%-d/%Y", "2024-03-07"),
        ("unpadded-two-digits", "%-m/%-d/%Y", "2024-11-27"),
        ("month-names", "%B %b", "2024-09-01"),
        ("sunday", "%A %a", "2024-03-10"),
        ("first-of-year", "%Y-%m-%d %j", "2024-01-01"),
        ("max-date", "%d.%m.%Y", "9999-12-31"),
        ("min-date", "%d.%m.%Y", "0001-01-01"),
        ("unicode-literal", "%d de %B é北", "2024-03-07"),
        ("year-1000", "%Y", "1000-01-01"),
        ("year-1999", "%Y %y", "1999-12-31"),
        ("year-2005", "%y", "2005-06-15"),
    ]:
        add("date_format", label, [value], T("date_format", format=fmt))
    add("date_format", "maps-over-values", ["2024-03-07", "2025-12-25"], T("date_format", format="%d/%m"))
    add("date_format", "empty-format", ["2024-03-07"], T("date_format", format=""))
    add("date_format", "missing-format", ["2024-03-07"], T("date_format"))
    add("date_format", "format-number", ["2024-03-07"], T("date_format", format=1))

    # date_part
    for part in ("month", "day", "year", "year2"):
        for pad in (0, 1, 2, 6):
            add("date_part", f"{part}-pad{pad}", ["2024-03-07"], T("date_part", part=part, pad=pad))
        add("date_part", f"{part}-default-pad", ["2024-03-07"], T("date_part", part=part))
    add("date_part", "year2-2005", ["2005-01-01"], T("date_part", part="year2"))
    add("date_part", "year2-2000", ["2000-01-01"], T("date_part", part="year2"))
    add("date_part", "year2-1999-pad4", ["1999-01-01"], T("date_part", part="year2", pad=4))
    add("date_part", "year-0001", ["0001-01-01"], T("date_part", part="year"))
    add("date_part", "double-digit-month", ["2024-11-30"], T("date_part", part="month", pad=2))
    p = T("date_part", part="day")
    each("date_part", p, ["", None, "07/03/2024", "2024-3-7", "20240307", "2024-02-30", 20240307, " ", ["2024-03-07"],
                          "2024-04-31", "2100-02-29", "2000-02-29"], None)
    add("date_part", "maps-over-values", ["2024-03-07", "", None, "2025-01-31"], T("date_part", part="day", pad=2))
    add("date_part", "bad-part", ["2024-03-07"], T("date_part", part="week"))
    add("date_part", "part-number", ["2024-03-07"], T("date_part", part=1))
    add("date_part", "missing-part", ["2024-03-07"], T("date_part"))
    add("date_part", "negative-pad", ["2024-03-07"], T("date_part", part="day", pad=-1))
    add("date_part", "pad-string", ["2024-03-07"], T("date_part", part="day", pad="2"))
    add("date_part", "two-errors", ["2024-03-07"], T("date_part", part="week", pad=-1))
    add("date_part", "part-and-pad-and-extra", ["2024-03-07"], T("date_part", part="week", pad=-1, extra=1))

    # number_format
    n = T("number_format")
    each(
        "number_format",
        n,
        [1234.5, 1234567, "1234.5", 0, "0", -1234.5, -0.001, 0.125, 2.675, 12, 0.30000000000000004, 1e-07, 1e21, 1e30,
         "1000000000000000000000000000000", "  12.5  ", "+5", ".5", "5.", "-.5", "007", "1e5", "abc", " ", "", None,
         "1,234", "nan", "inf", "-inf", "1_000", True, False, ["1"], {"n": 1}, 123456789.123456789, 5e-324, 1.7976931348623157e308,
         "0.005", "0.015", "-0.005", "99.995", "999999.999", "٣", "12\n", "1 2"],
    )
    for label, args, value in [
        ("half-up-0-5", {"decimals": 0}, 0.5),
        ("half-up-1-5", {"decimals": 0}, 1.5),
        ("half-up-2-5", {"decimals": 0}, 2.5),
        ("half-up-neg-0-5", {"decimals": 0}, -0.5),
        ("half-up-neg-2-5", {"decimals": 0}, -2.5),
        ("negative-rounds-to-zero", {"decimals": 0}, -0.4),
        ("negative-string-rounds-to-zero", {"decimals": 2}, "-0.004"),
        ("decimals-0", {"decimals": 0}, 1234567),
        ("decimals-1", {"decimals": 1}, 3.14159),
        ("decimals-12", {"decimals": 12}, 0.30000000000000004),
        ("decimals-12-big", {"decimals": 12}, "123456789012345678901234567890.5"),
        ("decimals-13", {"decimals": 13}, 1),
        ("decimals-negative", {"decimals": -1}, 1),
        ("decimals-float", {"decimals": 1.5}, 1),
        ("decimals-string", {"decimals": "2"}, 1),
        ("thousands-empty", {"thousands": ""}, 1234.5),
        ("thousands-space-prefix", {"thousands": " ", "prefix": "~"}, 1234.5),
        ("thousands-multi", {"thousands": "'''"}, 1234567),
        ("thousands-dot", {"thousands": ".", "decimals": 0}, 1234567),
        ("thousands-number", {"thousands": 1}, 1),
        ("prefix-dollar", {"prefix": "$"}, 1234.5),
        ("prefix-negative", {"prefix": "$"}, -1234.5),
        ("prefix-number", {"prefix": 5}, 1),
        ("small-seven-places", {"decimals": 7}, 1e-07),
        ("tiny-twelve-places", {"decimals": 12}, 1e-12),
        ("huge-no-decimals", {"decimals": 0}, "123456789012345678901234567890123456789"),
        ("exact-float-digits", {"decimals": 12}, 123456789.123456789),
        ("thousand-boundary", {"decimals": 0}, 999999.5),
        ("three-digits", {"decimals": 0}, 999),
        ("four-digits", {"decimals": 0}, 1000),
        ("rounds-up-to-next-group", {"decimals": 0}, 999.5),
        ("extra-arg", {"bogus": 1}, 1),
        ("float-1e21-no-decimals", {"decimals": 0}, 1e21),
        ("float-1e30-no-decimals", {"decimals": 0}, 1e30),
        ("float-1e16", {"decimals": 0}, 1e16),
        ("float-1e22-two", {"decimals": 2}, 1.5e22),
        ("rounds-to-28-digits-tie-even", {"decimals": 1}, "123456789012345678901234567.85"),
        ("rounds-to-28-digits-tie-odd", {"decimals": 1}, "123456789012345678901234567.95"),
        ("rounds-to-28-digits-down", {"decimals": 0}, "12345678901234567890123456781234"),
        ("rounds-to-28-digits-up", {"decimals": 0}, "12345678901234567890123456789999"),
        ("rounds-to-28-digits-carry", {"decimals": 0}, "99999999999999999999999999995"),
        ("rounds-to-28-digits-negative", {"decimals": 2, "prefix": "$"}, "-12345678901234567890123456789.99"),
        ("rounds-to-28-digits-thousands", {"decimals": 3, "thousands": " "}, "0.1234567890123456789012345678901"),
    ]:
        add("number_format", label, [value], T("number_format", **args))
    add("number_format", "maps-over-values", [1, "", None, "2.5", 1000], T("number_format", decimals=1))

    # bool_to_check
    each(
        "bool_to_check",
        T("bool_to_check"),
        [True, False, None, 1, 0, "yes", "Y", "true", "no", "False", "0", "", "anything else", [], ["a"], {}, {"a": 1},
         " Yes ", "OFF", "n", " ", "00", "nope", " false ", "NO", "off", "On", " ", "falsey", 2.5, -1, 1e30],
    )

    # equals / contains / default / prefix / suffix
    for v in ("a", 3, True, None, 1, [1, 2], {"a": 1}, "", 2.5):
        add("equals", f"value-{json.dumps(v)}-vs-string", ["a"], T("equals", value=v))
        add("equals", f"value-{json.dumps(v)}-vs-1", [1], T("equals", value=v))
        add("equals", f"value-{json.dumps(v)}-vs-true", [True], T("equals", value=v))
        add("equals", f"value-{json.dumps(v)}-vs-none", [None], T("equals", value=v))
        add("equals", f"value-{json.dumps(v)}-vs-list", [[1, 2]], T("equals", value=v))
        add("equals", f"value-{json.dumps(v)}-vs-dict", [{"a": 1}], T("equals", value=v))
    add("equals", "zero-vs-false", [0], T("equals", value=False))
    add("equals", "nested", [{"a": [1, {"b": True}]}], T("equals", value={"a": [1, {"b": 1}]}))
    add("equals", "dict-key-order", [{"a": 1, "b": 2}], T("equals", value={"b": 2, "a": 1}))
    add("equals", "list-order", [[1, 2]], T("equals", value=[2, 1]))
    add("equals", "string-vs-number", ["1"], T("equals", value=1))
    add("equals", "empty-string", [""], T("equals", value=""))
    add("equals", "unicode", ["José"], T("equals", value="José"))
    add("equals", "missing-value", ["a"], T("equals"))
    add("equals", "maps-over-values", ["a", "b", None], T("equals", value="a"))
    add("equals", "extra-arg", ["a"], T("equals", value="a", other=1))

    for label, item, value in [
        ("list-has", ["email", "sms"], "email"),
        ("list-lacks", ["email", "sms"], "fax"),
        ("list-number", [1, 2, 3], 2),
        ("list-true-is-one", [1, 2], True),
        ("list-one-is-true", [True], 1),
        ("list-zero-is-false", [0], False),
        ("list-string-number", ["1"], 1),
        ("list-none", [None], None),
        ("list-nested", [[1, 2], [3]], [3]),
        ("list-dict", [{"a": 1}], {"a": 1}),
        ("list-empty", [], "x"),
        ("text-substring", "hello", "ell"),
        ("text-not-substring", "hello", "xyz"),
        ("text-empty-needle", "hello", ""),
        ("text-empty-haystack", "", "x"),
        ("text-both-empty", "", ""),
        ("text-number-needle", "hello", 1),
        ("text-none-needle", "hello", None),
        ("text-list-needle", "hello", ["h"]),
        ("text-case", "Hello", "hello"),
        ("text-unicode", "José Zürich", "ü"),
        ("dict-key", {"k": 1}, "k"),
        ("dict-missing-key", {"k": 1}, "z"),
        ("dict-number-key", {"k": 1}, 1),
        ("dict-none-key", {"k": 1}, None),
        ("dict-list-key", {"k": 1}, [1]),
        ("dict-dict-key", {"k": 1}, {"k": 1}),
        ("dict-empty", {}, "k"),
        ("none-item", None, "x"),
        ("number-item", 12, 1),
        ("bool-item", True, True),
        ("zero-item", 0, 0),
    ]:
        add("contains", label, [item], T("contains", value=value))
    add("contains", "missing-value", ["abc"], T("contains"))
    add("contains", "maps-over-values", ["abc", "xyz", None], T("contains", value="b"))

    d = T("default", value="N/A")
    each("default", d, [None, "", "x", 0, False, [], {}, " ", "0", 5, ["a"]])
    add("default", "list-default", [None], T("default", value=["a", 1]))
    add("default", "dict-default", [""], T("default", value={"k": [1]}))
    add("default", "none-default", [""], T("default", value=None))
    add("default", "number-default", [None], T("default", value=0))
    add("default", "maps-over-values", [None, "", "x"], d)
    add("default", "missing-value", [None], T("default"))

    for op in ("prefix", "suffix"):
        step = T(op, value="#")
        each(op, step, ["42", 42, "", None, 0, False, True, "José", [], ["a"], {"a": 1}, 2.5])
        add(op, "maps-over-values", ["a", "", None, "b"], step)
        add(op, "empty-value", ["x"], T(op, value=""))
        add(op, "unicode-value", ["x"], T(op, value=" · é"))
        add(op, "value-number", ["x"], T(op, value=1))
        add(op, "value-null", ["x"], T(op, value=None))
        add(op, "missing-value", ["x"], T(op))

    # chains and chain semantics
    add("chain", "empty-single", ["x"])
    add("chain", "empty-multi", ["x", "y"])
    add("chain", "empty-none", [None])
    add("chain", "empty-no-values", [])
    add("chain", "empty-one-list", [["a", "b"]])
    add("chain", "no-values-op", [], T("upper"))
    add("chain", "map-then-join", ["a", "b"], T("upper"), T("join", sep="+"))
    add("chain", "join-then-upper", ["Ada", "Lovelace"], T("join", sep=" "), T("upper"))
    add("chain", "join-after-scalar", ["a"], T("upper"), T("join", sep="-"))
    add("chain", "join-twice", ["a", "b"], T("join", sep="-"), T("join", sep="+"))
    add("chain", "join-lone-list-then-map", [["b", "a"]], T("join", sep=","), T("upper"))
    add("chain", "map-keeps-list", ["a", "b", "c"], T("upper"), T("lower"), T("prefix", value="_"))
    add("chain", "split-then-title", ["ada-LOVELACE"], T("split", sep="-", index=1), T("title"))
    add("chain", "date-part-then-number", ["2024-03-07"], T("date_part", part="month", pad=2), T("number_format", decimals=0))
    add("chain", "three-step", ["  1234.5  "], T("strip"), T("number_format", prefix="$"), T("suffix", value=" USD"))
    add("chain", "blank-then-default", [""], T("date_format", format="%Y"), T("default", value="N/A"))
    add("chain", "none-then-default", [None], T("number_format"), T("default", value="N/A"))
    add("chain", "multi-names", ["ada", "lovelace", "king"], T("title"), T("join", sep=" "), T("prefix", value="Dr. "))
    add("chain", "address-lines", ["12 Main St", None, "", "Apt 4", "Zürich"], T("join", sep=", "), T("upper"))
    add("chain", "checkbox-equals", ["married"], T("equals", value="married"), T("bool_to_check"))
    add("chain", "checkbox-contains", [["email", "sms"]], T("contains", value="sms"))
    add("chain", "bool-from-equals-none", [None], T("equals", value="x"), T("bool_to_check"))
    add("chain", "slice-then-join", [["a", "b", "c"]], T("slice", start=1), T("join", sep="|"))
    add("chain", "slice-list-then-upper", [["a", "b", "c"]], T("slice", start=1), T("upper"))
    add("chain", "char-at-each", ["Ada", "Bob"], T("char_at", index=0), T("join", sep=""))
    add("chain", "initials", ["Ada", "", "King", None, "Lovelace"], T("char_at", index=0), T("join", sep=". "))
    add("chain", "second-step-error", ["a", "b"], T("upper"), T("date_part", part="day"))
    add("chain", "error-on-second-value", ["2024-03-07", "bad"], T("date_part", part="day"))
    add("chain", "unknown-op", ["x"], T("nope"))
    add("chain", "unknown-op-after-valid", ["x"], T("upper"), T("nope"))
    add("chain", "unknown-op-quote", ["x"], T("it's"))
    add("chain", "unknown-op-case", ["x"], T("UPPER"))
    add("chain", "unknown-op-empty-name", ["x"], T(""))
    add("chain", "two-unknown-ops", ["x"], T("nope"), T("nada"))
    add("chain", "unknown-and-bad-args", ["x"], T("nope"), T("split", sep="-"), T("upper"))
    add("chain", "all-errors-collected", ["x"], T("split", sep="", index="a"), T("date_part", part="week"))
    add("chain", "bad-args-checked-before-running", ["x"], T("upper"), T("date_part", part="week"))
    add("chain", "unicode-op-name", ["x"], T("é北"))

    return cases


def _validate_cases() -> list[dict]:
    cases: list[dict] = []

    def add(label: str, *chain: dict) -> None:
        cases.append({"id": f"validate/{label}", "kind": "validate", "chain": list(chain)})

    add("empty")
    add("valid-all", T("split", sep="-", index=0), T("join", sep=" "), T("slice"), T("upper"), T("lower"),
        T("title"), T("strip"), T("char_at", index=-1), T("bool_to_check"), T("equals", value=[1]),
        T("contains", value=None), T("default", value={"a": 1}), T("prefix", value="p"), T("suffix", value="s"),
        T("date_format", format="%Y"), T("date_part", part="year2", pad=3), T("number_format", decimals=12))
    add("unknown", T("nope"))
    add("unknown-quote", T("it's"))
    add("unknown-both-quotes", T("it's \"x\""))
    add("unknown-unicode", T("naïve"))
    add("unknown-newline", T("a\nb"))
    add("unknown-empty", T(""))
    add("unknown-long", T("x" * 80))
    add("required-missing", T("split"))
    add("required-all-missing", T("split"), T("char_at"), T("date_format"), T("equals"), T("prefix"))
    add("extra-one", T("upper", bogus=1))
    add("extra-many", T("upper", zebra=1, apple=2, mango=3))
    add("extra-unicode", T("upper", **{"é": 1, "a b": 2}))
    add("extra-and-missing", T("split", sep="-", other=1))
    add("type-string", T("split", sep=1, index=0))
    add("type-integer-string", T("split", sep="-", index="0"))
    add("type-integer-float", T("split", sep="-", index=0.5))
    add("type-integer-bool", T("split", sep="-", index=False))
    add("type-integer-null", T("split", sep="-", index=None))
    add("type-integer-list", T("split", sep="-", index=[1]))
    add("type-integer-dict", T("split", sep="-", index={"a": 1}))
    add("type-string-dict", T("join", sep={"a": [1, "x'y"]}))
    add("type-string-long-list", T("join", sep=[1, 2.5, "a", None, True, {"k": "v"}]))
    add("type-string-unicode", T("join", sep=["é", "北京", "\U0001f600", "​", "\t"]))
    add("type-string-control-chars", T("join", sep=["\x00\x07\x1b\x7f\x80\x9f\xa0\xad"]))
    add("type-string-escapes", T("join", sep=["a\\b", "q\"q", "s's", "both'\"", "\r\n"]))
    add("minlength-empty", T("split", sep="", index=0))
    add("minlength-format", T("date_format", format=""))
    add("enum-bad", T("date_part", part="week"))
    add("enum-bad-quote", T("date_part", part="it's"))
    add("enum-number", T("date_part", part=1))
    add("enum-bool", T("date_part", part=True))
    add("enum-null", T("date_part", part=None))
    add("enum-list", T("date_part", part=["day"]))
    add("minimum", T("date_part", part="day", pad=-1))
    add("minimum-huge", T("date_part", part="day", pad=-10_000_000))
    add("maximum", T("number_format", decimals=13))
    add("maximum-and-minimum", T("number_format", decimals=13), T("number_format", decimals=-1))
    add("minimum-float", T("date_part", part="day", pad=-0.5))
    add("integer-valued-float-is-integer", T("date_part", part="day", pad=1e16))
    add("default-ignored", T("slice", start=0, end=1))
    add("order-of-errors", T("date_part", pad=-1, part="week", extra=1))
    add("errors-per-step", T("nope"), T("split", sep=1), T("number_format", decimals=99), T("upper", x=1))
    add("any-value-schema", T("equals", value=None), T("contains", value={"a": [1]}), T("default", value=[]))
    return cases


TRANSFORM_CASES: list[dict] = _apply_cases() + _validate_cases()


# --------------------------------------------------------------------- schemas

SCHEMA = {
    "type": "object",
    "properties": {
        "applicant": {
            "type": "object",
            "description": "The person applying",
            "properties": {
                "first name": {"type": "string"},
                "dob": {"type": "string", "format": "date", "description": "Date of birth"},
                "sex": {"type": "string", "enum": ["F", "M", "X"]},
                "age": {"type": "integer"},
                "height": {"type": "number"},
                "smoker": {"type": "boolean"},
            },
        },
        "aliases": {"type": "array", "items": {"type": "string"}},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "dose": {"type": "number"},
                },
            },
        },
    },
}

_REUSE_DEFS = {
    "A": {"type": "object", "properties": {"q": {"type": "string"}}},
    "B": {"type": "object", "properties": {"x": {"$ref": "#/$defs/A"}}},
}


def _cycle_through_union(wrap):
    ref = {"$ref": "#/$defs/B"}
    return {
        "type": "object",
        "$defs": {"B": {"type": "object", "properties": {"x": wrap(ref)}}},
        "properties": {"p": wrap(ref)},
    }


def _schema_cases() -> list[dict]:
    cases: list[dict] = []

    def add(label: str, schema: Any) -> None:
        cases.append({"id": f"flatten/{label}", "kind": "flatten", "schema": schema})

    add("sample", SCHEMA)
    add("empty-object", {"type": "object"})
    add("empty-properties", {"type": "object", "properties": {}})
    add("scalars", {"type": "object", "properties": {
        "s": {"type": "string"}, "i": {"type": "integer"}, "n": {"type": "number"}, "b": {"type": "boolean"},
        "z": {"type": "null"}, "u": {}, "w": {"type": "weird"}, "d": {"type": "string", "format": "date"},
        "e": {"type": "string", "format": "email"}, "dt": {"type": "string", "format": "date-time"},
        "di": {"type": "integer", "format": "date"},
    }})
    add("descriptions", {"type": "object", "description": "root", "properties": {
        "a": {"type": "string", "description": "A field"},
        "b": {"type": "string", "description": ""},
        "c": {"type": "string", "description": 5},
        "d": {"type": "object", "description": "obj", "properties": {"x": {"type": "string", "description": "é北"}}},
    }})
    add("nested-objects", {"type": "object", "properties": {
        "a": {"type": "object", "properties": {"b": {"type": "object", "properties": {"c": {"type": "string"}}}}},
    }})
    add("enums", {"type": "object", "properties": {
        "tags": {"type": "array", "items": {"enum": ["a", "b"]}},
        "level": {"type": "integer", "enum": [1, 2]},
        "flag": {"enum": [True, "x"]},
        "mixed": {"enum": [None, 1, 2.5, "s", False, [1, "a"], {"k": [True, None]}]},
        "unicode": {"type": "string", "enum": ["Sí", "José", "北京", "\U0001f600", "a\"b", "tab\t", "\x7f"]},
        "empty": {"type": "string", "enum": []},
        "obj-with-enum": {"type": "object", "enum": ["x"], "properties": {"a": {"type": "string"}}},
        "arr-with-enum": {"type": "array", "enum": [[1]], "items": {"type": "string"}},
        "not-a-list": {"type": "string", "enum": "abc"},
        "float-exp": {"enum": [1e-07, 1.5e22, 0.1, -0.25, 123456.789]},
        "nested-float": {"enum": [[0.5, 1e-05]]},
    }})
    add("format-date-variants", {"type": "object", "properties": {
        "a": {"type": "string", "format": "date"},
        "b": {"type": ["string", "null"], "format": "date"},
        "c": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "d": {"type": "string", "format": "date", "enum": ["2024-01-01"]},
        "e": {"type": "array", "items": {"type": "string", "format": "date"}},
    }})
    add("arrays", {"type": "object", "properties": {
        "aliases": {"type": "array", "items": {"type": "string"}},
        "no-items": {"type": "array"},
        "items-true": {"type": "array", "items": True},
        "items-empty": {"type": "array", "items": {}},
        "items-null": {"type": "array", "items": None},
        "items-ints": {"type": "array", "items": {"type": "integer"}},
        "grid": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
        "cube": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": {"type": "object", "properties": {"v": {"type": "number"}}}}}},
        "dates": {"type": "array", "items": {"type": "string", "format": "date"}},
        "tuple": {"type": "array", "items": [{"type": "string"}]},
    }})
    add("tables", {"type": "object", "properties": {
        "medications": SCHEMA["properties"]["medications"],
        "visits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "format": "date"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "doc": {"type": "object", "properties": {"name": {"type": "string"}}},
                    "meds": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"n": {"type": "string"}, "kind": {"enum": ["a"]}}},
                    },
                    "grid": {"type": "array", "items": {"type": "array", "items": {"type": "object", "properties": {"z": {"type": "string"}}}}},
                },
            },
        },
    }})
    add("table-of-enum-items", {"type": "object", "properties": {
        "rows": {"type": "array", "items": {"type": "object", "properties": {"c": {"type": "string", "enum": ["x", "y"]}}}},
        "plain": {"type": "array", "items": {"type": "string", "enum": ["p", "q"]}},
        "objects-in-objects": {"type": "object", "properties": {
            "inner": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "integer"}}}}}},
    }})
    add("awkward-names", {"type": "object", "properties": {
        "a.b": {"properties": {"x": {"type": "string"}}},
        "a[b": {"type": "string"},
        'a"b': {"type": "string"},
        "a b": {"type": "string"},
        "": {"type": "object", "properties": {"x": {"type": "string"}}},
        "a]b": {"type": "string"},
        "back\\slash": {"type": "string"},
        "tab\tx": {"type": "string"},
        "[]": {"type": "array", "items": {"type": "object", "properties": {"[]": {"type": "string"}}}},
        "é北": {"type": "string"},
        "nbsp x": {"type": "string"},
        "bom﻿x": {"type": "string"},
        "sep\x1fx": {"type": "string"},
        "emoji\U0001f600": {"type": "string"},
        "q\"\\": {"properties": {"r s": {"type": "string"}}},
    }})
    add("missing-type-inferred", {"properties": {
        "o": {"properties": {"x": {}}},
        "l": {"items": {"type": "string"}},
        "n": {"items": {"type": "object", "properties": {"k": {"type": "string"}}}},
    }})
    add("nullable-types", {"type": "object", "properties": {
        "a": {"type": ["string", "null"]},
        "b": {"type": ["null", "integer"]},
        "c": {"type": ["null"]},
        "d": {"type": []},
        "e": {"type": ["integer", "string"]},
        "f": {"type": ["array", "null"], "items": {"type": "string"}},
        "g": {"type": ["object", "null"], "properties": {"x": {"type": "string"}}},
        "h": {"type": 5, "properties": {"x": {"type": "string"}}},
        "i": {"type": "null", "enum": [None]},
    }})
    add("unions", {"type": "object", "properties": {
        "a": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "description": "d"},
        "b": {"oneOf": [{"type": "null"}, {"type": "integer"}]},
        "c": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
        "d": {"anyOf": [{"type": "null"}, {"type": "null"}]},
        "e": {"anyOf": []},
        "f": {"anyOf": [], "oneOf": [{"type": "number"}]},
        "g": {"anyOf": [{"type": "array", "items": {"type": "object", "properties": {"x": {"type": "string"}}}}, {"type": "null"}]},
        "h": {"anyOf": "x"},
        "i": {"oneOf": [{"type": "object", "properties": {"x": {"type": "string"}}}, {"type": "object", "properties": {"y": {"type": "string"}}}]},
        "j": {"anyOf": [{"type": "string"}], "oneOf": [{"type": "integer"}]},
        "k": {"anyOf": [True, {"type": "integer"}]},
        "l": {"anyOf": [{"properties": {"a": {"type": "string"}}}, {"type": "null"}]},
        "m": {"type": "string", "anyOf": [{"type": "integer"}]},
    }})
    add("allof", {"type": "object", "properties": {
        "p": {"allOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"properties": {"b": {"type": "integer"}}},
        ]},
        "q": {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}}], "properties": {"a": {"type": "integer"}, "z": {"type": "string"}}},
        "r": {"allOf": [{"properties": {"a": {"type": "string"}}}, {"properties": {"a": {"type": "integer"}}}]},
        "s": {"allOf": []},
        "t": {"allOf": [{"type": "string"}, {"type": "integer"}]},
        "u": {"allOf": [{"description": "first"}, {"type": "string", "description": "second"}]},
        "v": {"allOf": [{"type": "array", "items": {"type": "string"}}, {"items": {"type": "integer"}}]},
        "w": {"allOf": "ab"},
        "x": {"allOf": [{"enum": ["a"]}, {"enum": ["b"]}]},
        "y": {"allOf": [5, None, {"type": "string"}]},
        "z": {"allOf": [{"properties": {"a": {"type": "string"}}}, {"properties": "nope"}]},
    }})
    add("refs", {
        "type": "object",
        "$defs": {
            "Addr": {"type": "object", "properties": {"zip": {"type": "string"}}},
            "Name": {"type": "string", "description": "A name"},
            "Tags": {"type": "array", "items": {"$ref": "#/$defs/Name"}},
            "Old": {"$ref": "#/$defs/Name"},
            "a/b": {"type": "integer"},
            "a~b": {"type": "number"},
            "sp ace": {"type": "boolean"},
            "ünï": {"type": "string", "format": "date"},
            "list": [{"type": "string"}, {"type": "integer"}],
        },
        "definitions": {"Legacy": {"type": "object", "properties": {"k": {"type": "string"}}}},
        "properties": {
            "home": {"$ref": "#/$defs/Addr", "description": "Home address"},
            "past": {"type": "array", "items": {"$ref": "#/$defs/Addr"}},
            "name": {"$ref": "#/$defs/Name"},
            "name2": {"$ref": "#/$defs/Name", "description": "Own description"},
            "tags": {"$ref": "#/$defs/Tags"},
            "old": {"$ref": "#/$defs/Old"},
            "slash": {"$ref": "#/$defs/a~1b"},
            "tilde": {"$ref": "#/$defs/a~0b"},
            "space": {"$ref": "#/$defs/sp%20ace"},
            "pct": {"$ref": "#/$defs/%C3%BCn%C3%AF"},
            "idx": {"$ref": "#/$defs/list/1"},
            "legacy": {"$ref": "#/definitions/Legacy"},
            "root": {"$ref": "#"},
            "twice": {"$ref": "#/properties/home"},
            "ref-and-siblings": {"$ref": "#/$defs/Name", "type": "integer", "format": "date"},
            "ref-nonstring": {"$ref": 5, "type": "string"},
        },
    })
    add("sibling-refs", {"type": "object", "properties": {
        "a": {"$ref": "#/$defs/Addr"}, "b": {"$ref": "#/$defs/Addr"},
        "c": {"type": "array", "items": {"$ref": "#/$defs/Addr"}},
    }, "$defs": {"Addr": {"type": "object", "properties": {"zip": {"type": "string"}, "n": {"$ref": "#/$defs/Num"}}}, "Num": {"type": "integer"}}})
    add("recursive-ref", {
        "type": "object",
        "$defs": {"Node": {"type": "object", "properties": {"name": {"type": "string"}, "child": {"$ref": "#/$defs/Node"}}}},
        "properties": {"root": {"$ref": "#/$defs/Node"}},
    })
    add("recursive-ref-array", {
        "type": "object",
        "$defs": {"Tree": {"type": "object", "properties": {
            "label": {"type": "string"},
            "kids": {"type": "array", "items": {"$ref": "#/$defs/Tree"}},
        }}},
        "properties": {"tree": {"$ref": "#/$defs/Tree"}},
    })
    add("recursive-ref-root", {
        "type": "object",
        "properties": {"name": {"type": "string"}, "parent": {"$ref": "#"}, "kids": {"type": "array", "items": {"$ref": "#"}}},
    })
    add("recursive-ref-via-chain", {
        "type": "object",
        "$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"type": "object", "properties": {"x": {"$ref": "#/$defs/A"}, "y": {"type": "string"}}}},
        "properties": {"p": {"$ref": "#/$defs/A"}},
    })
    add("recursive-ref-untyped-target", {
        "$defs": {"N": {"properties": {"c": {"$ref": "#/$defs/N"}}}, "L": {"items": {"$ref": "#/$defs/L"}}, "E": {"$ref": "#/$defs/E"}},
        "properties": {"n": {"$ref": "#/$defs/N"}, "l": {"$ref": "#/$defs/L"}, "e": {"$ref": "#/$defs/E"}},
    })
    add("ref-reused-by-sibling-allof", {"type": "object", "$defs": _REUSE_DEFS, "properties": {"p": {"allOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}]}}})
    add("ref-reused-by-sibling-oneof", {"type": "object", "$defs": _REUSE_DEFS, "properties": {"p": {"oneOf": [{"$ref": "#/$defs/B"}, {"$ref": "#/$defs/A"}]}}})
    add("cycle-through-allof", _cycle_through_union(lambda r: {"allOf": [r]}))
    add("cycle-through-oneof", _cycle_through_union(lambda r: {"oneOf": [r, {"type": "null"}]}))
    add("cycle-through-anyof", _cycle_through_union(lambda r: {"anyOf": [{"type": "null"}, r]}))
    add("cycle-through-array-items", {
        "type": "object",
        "$defs": {"B": {"type": "object", "properties": {"kids": {"type": "array", "items": {"allOf": [{"$ref": "#/$defs/B"}]}}}}},
        "properties": {"p": {"oneOf": [{"$ref": "#/$defs/B"}, {"type": "null"}]}},
    })
    add("cycle-two-defs", {
        "type": "object",
        "$defs": {"A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}}, "B": {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}, "t": {"type": "string"}}}},
        "properties": {"start": {"$ref": "#/$defs/A"}, "also": {"$ref": "#/$defs/B"}},
    })
    add("root-ref", {"$ref": "#/$defs/Root", "$defs": {"Root": {"type": "object", "properties": {"a": {"type": "string"}}}}})
    add("root-allof", {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}}, {"properties": {"b": {"type": "integer"}}}]})
    add("root-untyped-properties", {"properties": {"a": {"type": "string"}}})
    add("root-nullable-object", {"type": ["object", "null"], "properties": {"a": {"type": "string"}}})
    add("root-anyof-object", {"anyOf": [{"type": "object", "properties": {"a": {"type": "string"}}}, {"type": "null"}]})
    add("properties-not-a-dict", {"type": "object", "properties": ["a"]})
    add("property-schema-not-a-dict", {"type": "object", "properties": {"a": True, "b": None, "c": 5, "d": "string", "e": [], "f": {"type": "string"}}})
    add("object-without-properties", {"type": "object", "properties": {"a": {"type": "object"}, "b": {"type": "object", "properties": None}}})
    add("array-of-objects-without-properties", {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "object"}}}})
    add("keyword-noise", {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.org/x.json", "title": "T",
        "type": "object", "required": ["a"], "additionalProperties": False,
        "properties": {"a": {"type": "string", "minLength": 1, "pattern": "^x", "default": "y", "examples": ["x"]},
                       "b": {"type": "integer", "minimum": 0, "maximum": 9, "multipleOf": 3}},
    })
    add("property-order", {"type": "object", "properties": {"z": {"type": "string"}, "a": {"type": "string"}, "m": {"type": "string"}, "Z": {"type": "string"}, "_": {"type": "string"}}})
    add("unicode-everywhere", {"type": "object", "title": "Formulaire", "properties": {
        "prénom": {"type": "string", "description": "Prénom (José, Zürich, 北京)"},
        "taille": {"type": "string", "enum": ["Sí", "No"]},
    }})

    # invalid schemas
    add("invalid-root-array", {"type": "array", "items": {"type": "string"}})
    add("invalid-root-string", {"type": "string"})
    add("invalid-root-untyped", {})
    add("invalid-root-enum", {"enum": ["a"]})
    add("invalid-root-list", [])
    add("invalid-root-null", None)
    add("invalid-root-true", True)
    add("invalid-root-number", 5)
    add("invalid-root-string-value", "object")
    add("invalid-root-nullable-only", {"type": ["null"]})
    add("invalid-root-array-ref", {"$ref": "#/$defs/L", "$defs": {"L": {"type": "array", "items": {"type": "string"}}}})
    add("invalid-missing-def", {"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}})
    add("invalid-missing-def-quote", {"type": "object", "properties": {"a": {"$ref": "#/$defs/it's"}}})
    add("invalid-missing-def-unicode", {"type": "object", "properties": {"a": {"$ref": "#/$defs/é北\U0001f600"}}})
    add("invalid-missing-def-escapes", {"type": "object", "properties": {"a": {"$ref": '#/$defs/a"b\'c\\d\te'}}})
    add("invalid-missing-nested", {"type": "object", "$defs": {"A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/Gone"}}}}, "properties": {"a": {"$ref": "#/$defs/A"}}})
    add("invalid-missing-in-items", {"type": "object", "properties": {"a": {"type": "array", "items": {"$ref": "#/nope/x"}}}})
    add("invalid-missing-in-allof", {"type": "object", "properties": {"a": {"allOf": [{"$ref": "#/$defs/Gone"}]}}})
    add("invalid-missing-in-anyof", {"type": "object", "properties": {"a": {"anyOf": [{"$ref": "#/$defs/Gone"}, {"type": "null"}]}}})
    add("invalid-list-index-out-of-range", {"type": "object", "$defs": {"l": [{"type": "string"}]}, "properties": {"a": {"$ref": "#/$defs/l/5"}}})
    add("invalid-list-index-not-digits", {"type": "object", "$defs": {"l": [{"type": "string"}]}, "properties": {"a": {"$ref": "#/$defs/l/x"}}})
    add("invalid-list-index-negative", {"type": "object", "$defs": {"l": [{"type": "string"}]}, "properties": {"a": {"$ref": "#/$defs/l/-1"}}})
    add("invalid-ref-to-scalar", {"type": "object", "$defs": {"n": 5}, "properties": {"a": {"$ref": "#/$defs/n"}}})
    add("invalid-ref-to-string", {"type": "object", "$defs": {"n": "string"}, "properties": {"a": {"$ref": "#/$defs/n"}}})
    add("invalid-ref-to-list", {"type": "object", "$defs": {"n": []}, "properties": {"a": {"$ref": "#/$defs/n"}}})
    add("invalid-ref-to-null", {"type": "object", "$defs": {"n": None}, "properties": {"a": {"$ref": "#/$defs/n"}}})
    add("invalid-ref-through-scalar", {"type": "object", "$defs": {"n": "x"}, "properties": {"a": {"$ref": "#/$defs/n/deeper"}}})
    add("invalid-ref-bad-percent-escape", {"type": "object", "properties": {"a": {"$ref": "#/$defs/%E0%A4%A"}}})
    add("invalid-remote-ref", {"type": "object", "properties": {"a": {"$ref": "http://example.com/x.json"}}})
    add("invalid-remote-ref-fragment", {"type": "object", "properties": {"a": {"$ref": "other.json#/$defs/A"}}})
    add("invalid-remote-ref-relative", {"type": "object", "properties": {"a": {"$ref": "defs.json"}}})
    add("invalid-remote-ref-unicode", {"type": "object", "properties": {"a": {"$ref": "é.json"}}})
    add("invalid-ref-not-pointer", {"type": "object", "properties": {"a": {"$ref": "#defs"}}})
    add("invalid-ref-anchor", {"type": "object", "properties": {"a": {"$ref": "#foo"}}})
    add("invalid-ref-empty-string", {"type": "object", "properties": {"a": {"$ref": ""}}})
    add("invalid-ref-empty-pointer", {"type": "object", "properties": {"a": {"$ref": "#/"}}})
    add("invalid-remote-ref-in-root", {"$ref": "http://example.com/root.json"})
    add("invalid-first-error-wins", {"type": "object", "properties": {"a": {"$ref": "http://x/y"}, "b": {"$ref": "#/$defs/Missing"}}})
    return cases


def _path_cases() -> list[dict]:
    cases: list[dict] = []
    parse_ok = [
        "a", "a.b", "a[].b", 'applicant["first name"]', '["first name"].x', 'a["x.y"][].b', 'a["q\\"uote"]',
        'a["b\\\\c"]', 'a["[]"]', "a[][]", "[]", "[].a", 'a[""]', '[""]', "é北.x", "a\U0001f600.b", 'a["\U0001f600"]',
        'a["tab\tx"]', 'a[]["b c"].d', '["a"]["b"]', "a.b.c.d.e", "_a1.-b", 'a["]"]', 'a["["]', 'a["\\\\"]',
        'a["\\"\\\\"]', "a ", "a﻿.b", "a​.b",
    ]
    for path in parse_ok:
        cases.append({"id": f"parse_path/{path!r}", "kind": "parse_path", "path": path})
    parse_bad = [
        "", "a.", ".a", "a..b", 'a["x', "a[x]", 'a["x"', 'a["x"]b', "a[", "a]", "]", "a.[]", "a b", " a", "a ", "a.b c",
        'a["x\\n"]', 'a["x\\', 'a["x"] .b', "a[\"x\"] ", "\U0001f600 x", "x\U0001f600 y", 'é["a', 'é北["a\\q"]', "a\"b",
        "a\\b", "a[]x", "[]x", 'a[""', '["', "[", "[x]", 'a["b"]]', "a. ", "a.\x1f", "x.\U0001f600　",
    ]
    for path in parse_bad:
        cases.append({"id": f"parse_path/bad/{path!r}", "kind": "parse_path", "path": path})
    format_cases = [
        ["a"], ["a", "b"], ["a", A_ITEM, "b"], ["a", "first name"], ["first name", "x"], ["a", "x.y", A_ITEM, "b"],
        ["a", 'q"uote'], ["a", "b\\c"], ["a", "[]"], ["a", A_ITEM, A_ITEM], [A_ITEM], [A_ITEM, "a"], ["a", ""], [""],
        ["é北", "x"], ["a", "\U0001f600"], ["a", "tab\tx"], ["a", "nbsp "], ["a", "bom﻿"], ["a", "zw​"],
        ["a", "sep\x1f"], ["a", "nel\x85"], ["a", "[", "]"], ["a", ".", "b"], ["a", "q\"\\"], [], ["a", "-b", "_c"],
        ["a", "1"], ["1", "2"],
    ]
    for segs in format_cases:
        label = json.dumps(segs, ensure_ascii=True)
        cases.append({"id": f"format_path/{label}", "kind": "format_path", "segments": segs})
    return cases


SCHEMA_CASES: list[dict] = _schema_cases() + _path_cases()


def dev_schema_cases(repo_root: Path) -> list[dict]:
    """``flatten`` cases for the dev-split form schemas (never the held-out ones)."""
    manifest = json.loads((repo_root / "forms" / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for form in manifest["forms"]:
        if form.get("split") != "dev":
            continue
        schema = json.loads((repo_root / "forms" / "schemas" / f"{form['id']}.json").read_text(encoding="utf-8"))
        out.append({"id": f"flatten/dev-form/{form['id']}", "kind": "flatten", "schema": schema})
    return out
