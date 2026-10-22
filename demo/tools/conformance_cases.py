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


# ------------------------------------------------------------------------- ops
#
# Edit-op cases run against small templates. A template here is the input to
# ``Template.model_validate`` (defaults are filled in by the library); the exporter stores the
# full template it produces. A case is ``{"id", "kind": "ops", "template": name, "ops": [...]}``:
# the ops are applied one after the other and the case records the template (fields and history)
# and the warnings after the last, or the error the first failing op raised.

OPS_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "smoker": {"type": "boolean"},
        "sex": {"type": "string", "enum": ["F", "M", "X"]},
        "dob": {"type": "string", "format": "date"},
        "address": {
            "type": "object",
            "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
        },
        "aliases": {"type": "array", "items": {"type": "string"}},
        "meds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "dose": {"type": "number"}},
            },
        },
    },
}

_OPS_FORM = {"fingerprint": None, "page_count": 2, "page_sizes": [[612, 792], [400, 500]], "kinds": ["digital", "digital"]}


def _src(*paths: str, **extra: Any) -> dict:
    return {"paths": list(paths), **extra}


def _field(id: str, type: str, page: int, rect: list, **extra: Any) -> dict:
    return {"id": id, "type": type, "page": page, "rect": rect, "origin": "human", **extra}


def _ops_templates() -> dict[str, dict]:
    plain_fields = [
        _field("n1", "text", 0, [72, 100, 300, 120], label="Name", source=_src("name")),
        _field("n2", "text", 0, [72, 140, 300, 160]),
        _field("c1", "checkbox", 0, [320, 100, 332, 112], source=_src("smoker", transforms=[{"op": "bool_to_check"}])),
        _field("c2", "checkbox", 0, [340, 100, 352, 112]),
        _field("r1", "radio_option", 1, [40, 40, 52, 52], source=_src("sex", when={"op": "equals", "args": {"value": "F"}})),
        _field("f3", "text", 1, [40, 80, 300, 100], style={"font_size": 10, "align": "center"}),
        _field("sig", "signature", 1, [40, 400, 300, 440]),
        _field("s1", "text", 1, [40, 120, 300, 140], source=_src("gone")),
        _field("w1", "text", 1, [40, 160, 300, 180], origin="widget", existing_widget="Widget 1"),
    ]
    cols = lambda name_fields, dose_fields, x0, x1, x2, x3: [  # noqa: E731
        {"key": "name", "x": [x0, x1], "fields": name_fields},
        {"key": "dose", "x": [x2, x3], "transforms": [{"op": "number_format", "args": {"decimals": 1}}], "fields": dose_fields},
    ]
    table = {
        "id": "t1",
        "type": "table",
        "label": "Medications",
        "source": _src("meds"),
        "origin": "human",
        "segments": [
            {"page": 0, "rect": [50, 300, 550, 420], "row_top": 320, "row_height": 20, "max_rows": 4,
             "columns": cols(["m1", "m2"], [None, "m3"], 60, 200, 210, 300)},
            {"page": 1, "rect": [30, 200, 370, 300], "row_top": 210, "row_height": 20, "max_rows": 4,
             "columns": cols([], [], 40, 180, 190, 280)},
        ],
    }
    table_fields = [
        _field("n1", "text", 0, [72, 100, 300, 120], label="Name", source=_src("name")),
        _field("m1", "text", 0, [60, 320, 200, 340]),
        _field("m2", "text", 0, [60, 340, 200, 360]),
        _field("m3", "text", 0, [210, 340, 300, 360]),
        _field("p2", "text", 0, [60, 360, 200, 380]),
        _field("p3", "text", 0, [60, 380, 110, 400]),
        _field("p4", "text", 0, [400, 320, 500, 340]),
        _field("p5", "text", 0, [210, 360, 250, 380]),
        table,
    ]
    return {
        "empty": {"template_id": "empty", "schema": OPS_SCHEMA, "form": _OPS_FORM, "fields": []},
        "plain": {"template_id": "plain", "schema": OPS_SCHEMA, "form": _OPS_FORM, "fields": plain_fields},
        "table": {"template_id": "table", "schema": OPS_SCHEMA, "form": _OPS_FORM, "fields": table_fields},
    }


OPS_TEMPLATES: dict[str, dict] = _ops_templates()


def _ops_cases() -> list[dict]:
    cases: list[dict] = []

    def add(tpl: str, label: str, *ops: dict, author: str = "human", note: str = "") -> None:
        cases.append({"id": f"{tpl}/{label}", "kind": "ops", "template": tpl, "ops": list(ops),
                      "author": author, "note": note})

    def af(**kw: Any) -> dict:
        return {"op": "add_field", **kw}

    box = [100, 200, 220, 220]

    # ---- add_field
    add("plain", "add/basic", af(type="text", page=0, rect=box, label="City", source=_src("address.city")))
    add("plain", "add/generated-id-follows-max", af(type="text", page=0, rect=box))
    add("empty", "add/first-generated-id", af(type="text", page=0, rect=box))
    add("plain", "add/explicit-id", af(id="my-box_1", type="checkbox", page=1, rect=[10, 10, 22, 22]))
    add("plain", "add/id-null", af(id=None, type="text", page=0, rect=box))
    add("plain", "add/no-optional-keys", af(type="signature", page=1, rect=[10, 10, 100, 50]))
    add("plain", "add/note-kept", af(type="text", page=0, rect=box), note="drawn by hand")
    add("plain", "add/explicit-null-source-and-style", af(type="text", page=0, rect=box, source=None, style=None))
    add("plain", "add/float-rect", af(type="text", page=0, rect=[100.5, 200.25, 220.75, 220.125]))
    add("plain", "add/edge-tolerance", af(type="text", page=0, rect=[-1.5, -1.5, 100, 100]))
    add("plain", "add/style-partial", af(type="text", page=0, rect=box, style={"font_size": 9.5}))
    add("plain", "add/style-small-size-lowers-min", af(type="text", page=0, rect=box, style={"font_size": 4}))
    add("plain", "add/style-all", af(type="text", page=0, rect=box,
                                     style={"font_size": 8.5, "min_font_size": 5.5, "align": "right", "comb": 10, "multiline": True}))
    add("plain", "add/style-auto", af(type="text", page=0, rect=box, style={"font_size": "auto", "min_font_size": 4.5}))
    add("plain", "add/style-empty", af(type="text", page=0, rect=box, style={}))
    add("plain", "add/style-ignores-unknown-key", af(type="text", page=0, rect=box, style={"align": "center", "color": "red"}))
    add("plain", "add/source-transforms", af(type="text", page=0, rect=box,
                                             source=_src("name", transforms=[{"op": "upper"}, {"op": "slice", "args": {"end": 3}}])))
    add("plain", "add/source-multiple-paths-join", af(type="text", page=0, rect=box,
                                                      source=_src("address.street", "address.city", transforms=[{"op": "join", "args": {"sep": ", "}}])))
    add("plain", "add/source-when", af(type="checkbox", page=0, rect=[100, 200, 112, 212],
                                       source=_src("sex", when={"op": "equals", "args": {"value": "M"}})))
    add("plain", "add/source-join-key", af(type="text", page=0, rect=box, source=_src("name", join=" ")))
    add("plain", "add/source-ignores-unknown-key", af(type="text", page=0, rect=box, source=_src("name", junk=1)))
    add("plain", "add/numeric-strings-are-coerced", af(type="text", page="1", rect=["100", "200", "220.5", "220"]))
    add("plain", "add/overlap-warning", af(type="text", page=0, rect=[72, 102, 300, 122]))
    add("plain", "add/no-overlap-on-other-page", af(type="text", page=1, rect=[72, 100, 300, 120]))
    add("plain", "add/checkbox-string-source-warning", af(type="checkbox", page=0, rect=[100, 200, 112, 212], source=_src("name")))
    add("plain", "add/checkbox-bool-source-no-warning", af(type="checkbox", page=0, rect=[100, 200, 112, 212], source=_src("smoker")))
    add("plain", "add/checkbox-with-when-no-warning", af(type="checkbox", page=0, rect=[100, 200, 112, 212],
                                                         source=_src("name", when={"op": "equals", "args": {"value": "x"}})))
    add("plain", "add/boolean-on-text-warning", af(type="text", page=0, rect=[360, 100, 440, 112], source=_src("smoker")))
    add("plain", "add/boolean-on-text-no-box-in-row", af(type="text", page=0, rect=[360, 300, 440, 312], source=_src("smoker")))
    add("plain", "add/then-duplicate", af(id="dup", type="text", page=0, rect=box), af(id="dup", type="text", page=0, rect=box))
    add("empty", "add/twice-generated", af(type="text", page=0, rect=box), af(type="text", page=0, rect=[100, 300, 220, 320]))
    add("plain", "add/then-move-generated", af(type="text", page=0, rect=box), {"op": "move", "id": "f4", "dx": 5})

    # add_field errors
    add("plain", "add/err-table", af(type="table", page=0, rect=box))
    add("plain", "add/err-duplicate-id", af(id="n1", type="text", page=0, rect=box))
    add("plain", "add/err-bad-id-space", af(id="x y", type="text", page=0, rect=box))
    add("plain", "add/err-bad-id-digit-first", af(id="1abc", type="text", page=0, rect=box))
    add("plain", "add/err-bad-id-too-long", af(id="a" * 65, type="text", page=0, rect=box))
    add("plain", "add/err-id-before-rect", af(id="n1", type="text", page=0, rect=[0, 0, 1, 1]))
    add("plain", "add/err-off-page-right", af(type="text", page=0, rect=[600, 100, 700, 120]))
    add("plain", "add/err-off-page-negative", af(type="text", page=0, rect=[-3, 100, 50, 120]))
    add("plain", "add/err-off-small-page", af(type="text", page=1, rect=[100, 100, 450, 120]))
    add("plain", "add/err-degenerate-width", af(type="text", page=0, rect=[100, 100, 101, 120]))
    add("plain", "add/err-degenerate-inverted", af(type="text", page=0, rect=[200, 100, 100, 120]))
    add("plain", "add/err-page-missing", af(type="text", page=2, rect=box))
    add("plain", "add/err-page-negative", af(type="text", page=-1, rect=box))
    add("plain", "add/err-path-unknown-close", af(type="text", page=0, rect=box, source=_src("nam")))
    add("plain", "add/err-path-unknown-far", af(type="text", page=0, rect=box, source=_src("zzzzzz")))
    add("plain", "add/err-path-second-unknown", af(type="text", page=0, rect=box, source=_src("name", "adress.city")))
    add("plain", "add/err-paths-empty", af(type="text", page=0, rect=box, source=_src()))
    add("plain", "add/err-path-object", af(type="text", page=0, rect=box, source=_src("address")))
    add("plain", "add/err-path-list-of-rows", af(type="text", page=0, rect=box, source=_src("meds")))
    add("plain", "add/err-path-table-column", af(type="text", page=0, rect=[72.25, 100.05, 190.75, 120], source=_src("meds[].name")))
    add("plain", "add/err-path-table-column-tie-rounding", af(type="text", page=0, rect=[72.25, 200.25, 190.75, 220], source=_src("meds[].dose")))
    add("plain", "add/err-chain-unknown", af(type="text", page=0, rect=box, source=_src("name", transforms=[{"op": "shout"}])))
    add("plain", "add/err-chain-bad-args", af(type="text", page=0, rect=box,
                                              source=_src("name", transforms=[{"op": "split", "args": {"sep": ""}}, {"op": "upper", "args": {"x": 1}}])))
    add("plain", "add/err-when-bad-chain", af(type="checkbox", page=0, rect=[100, 200, 112, 212],
                                              source=_src("sex", when={"op": "equals"})))
    add("plain", "add/err-when-on-text", af(type="text", page=0, rect=box,
                                            source=_src("sex", when={"op": "equals", "args": {"value": "F"}})))
    add("plain", "add/err-when-on-signature", af(type="signature", page=0, rect=box,
                                                 source=_src("sex", when={"op": "equals", "args": {"value": "F"}})))

    # ---- remove_field
    add("plain", "remove/basic", {"op": "remove_field", "id": "n2"})
    add("plain", "remove/source-field", {"op": "remove_field", "id": "n1"})
    add("plain", "remove/seeded-widget-as-human", {"op": "remove_field", "id": "w1"})
    add("plain", "remove/err-unknown-similar", {"op": "remove_field", "id": "n3"})
    add("plain", "remove/err-unknown-far", {"op": "remove_field", "id": "zzzz"})
    add("empty", "remove/err-no-fields", {"op": "remove_field", "id": "n1"})
    add("plain", "remove/err-twice", {"op": "remove_field", "id": "n2"}, {"op": "remove_field", "id": "n2"})
    add("plain", "remove/then-move-says-removed", {"op": "remove_field", "id": "n2"}, {"op": "move", "id": "n2", "dx": 1})
    add("plain", "remove/then-add-same-id", {"op": "remove_field", "id": "n2"}, af(id="n2", type="text", page=0, rect=[72, 140, 300, 160]))
    add("plain", "remove/err-unknown-case-sensitive", {"op": "remove_field", "id": "N1"})
    add("plain", "remove/err-unknown-many-similar", {"op": "remove_field", "id": "c"})
    add("table", "remove/err-linked-cell", {"op": "remove_field", "id": "m1"})
    add("table", "remove/err-linked-cell-other-column", {"op": "remove_field", "id": "m3"})
    add("table", "remove/unlinked-plain", {"op": "remove_field", "id": "p2"})
    add("table", "remove/table", {"op": "remove_field", "id": "t1"})
    add("table", "remove/table-then-cell", {"op": "remove_field", "id": "t1"}, {"op": "remove_field", "id": "m1"})

    # ---- move
    add("plain", "move/dx-dy", {"op": "move", "id": "n1", "dx": 10, "dy": -5.5})
    add("plain", "move/dx-only", {"op": "move", "id": "n1", "dx": 10})
    add("plain", "move/dy-only", {"op": "move", "id": "n1", "dy": 4})
    add("plain", "move/zero-offset", {"op": "move", "id": "n1", "dx": 0, "dy": 0})
    add("plain", "move/rect", {"op": "move", "id": "n1", "rect": [10, 10, 100, 30]})
    add("plain", "move/rect-can-resize", {"op": "move", "id": "n1", "rect": [10, 10, 400, 60]})
    add("plain", "move/dx-string", {"op": "move", "id": "n1", "dx": "2.5"})
    add("plain", "move/overlap-warning", {"op": "move", "id": "n2", "dy": -38})
    add("plain", "move/stale-source-warning", {"op": "move", "id": "s1", "dy": 3})
    add("plain", "move/seeded-widget-as-human", {"op": "move", "id": "w1", "dx": 3})
    add("plain", "move/checkbox-warning-on-move", {"op": "move", "id": "c2", "dy": 1})
    add("plain", "move/err-neither", {"op": "move", "id": "n1"})
    add("plain", "move/err-both", {"op": "move", "id": "n1", "dx": 1, "rect": [1, 2, 30, 40]})
    add("plain", "move/err-null-dx-null-rect", {"op": "move", "id": "n1", "dx": None, "rect": None})
    add("plain", "move/err-off-page", {"op": "move", "id": "n1", "dx": 400})
    add("plain", "move/err-off-page-up", {"op": "move", "id": "n1", "dy": -200})
    add("plain", "move/err-rect-degenerate", {"op": "move", "id": "n1", "rect": [10, 10, 11, 30]})
    add("plain", "move/err-segment-on-plain", {"op": "move", "id": "n1", "dx": 1, "segment": 0})
    add("plain", "move/err-unknown-id", {"op": "move", "id": "nn1", "dx": 1})
    add("plain", "move/err-dx-not-a-number", {"op": "move", "id": "n1", "dx": "far"})
    add("plain", "move/err-rect-3-items", {"op": "move", "id": "n1", "rect": [1, 2, 3]})
    add("table", "move/linked-cell-plain-move", {"op": "move", "id": "m1", "dx": 2})
    add("table", "move/table-dx-dy-warns", {"op": "move", "id": "t1", "dx": 5, "dy": 0})
    add("table", "move/table-one-segment", {"op": "move", "id": "t1", "dx": 4, "dy": 6, "segment": 1})
    add("table", "move/table-rect-segment", {"op": "move", "id": "t1", "rect": [60, 305, 560, 425], "segment": 0})
    add("table", "move/table-rect-other-size", {"op": "move", "id": "t1", "rect": [50, 300, 500, 440], "segment": 0})
    add("table", "move/err-table-rect-no-segment", {"op": "move", "id": "t1", "rect": [60, 300, 560, 420]})
    add("table", "move/err-table-bad-segment", {"op": "move", "id": "t1", "dx": 1, "segment": 5})
    add("table", "move/err-table-bad-segment-negative", {"op": "move", "id": "t1", "rect": [1, 2, 300, 400], "segment": -1})
    add("table", "move/err-table-off-page", {"op": "move", "id": "t1", "dx": 200})
    add("table", "move/err-table-rows-no-longer-fit", {"op": "move", "id": "t1", "rect": [50, 300, 550, 380], "segment": 0})
    add("table", "move/err-table-column-leaves-rect", {"op": "move", "id": "t1", "rect": [50, 300, 250, 420], "segment": 0})
    add("table", "move/err-table-second-segment-off-page", {"op": "move", "id": "t1", "dx": 80, "segment": 1})

    # ---- resize
    add("plain", "resize/basic", {"op": "resize", "id": "n1", "rect": [72, 100, 350, 125]})
    add("plain", "resize/overlap-warning", {"op": "resize", "id": "n1", "rect": [72, 100, 300, 150]})
    add("plain", "resize/seeded-widget-as-human", {"op": "resize", "id": "w1", "rect": [40, 160, 200, 175]})
    add("plain", "resize/err-degenerate", {"op": "resize", "id": "n1", "rect": [72, 100, 73, 120]})
    add("plain", "resize/err-off-page", {"op": "resize", "id": "n1", "rect": [72, 100, 700, 120]})
    add("plain", "resize/err-missing-rect", {"op": "resize", "id": "n1"})
    add("plain", "resize/err-null-rect", {"op": "resize", "id": "n1", "rect": None})
    add("plain", "resize/err-segment-on-plain", {"op": "resize", "id": "n1", "rect": [72, 100, 300, 120], "segment": 1})
    add("table", "resize/table-segment", {"op": "resize", "id": "t1", "rect": [50, 300, 560, 430], "segment": 0})
    add("table", "resize/err-table-no-segment", {"op": "resize", "id": "t1", "rect": [50, 300, 560, 430]})
    add("table", "resize/err-table-too-short", {"op": "resize", "id": "t1", "rect": [50, 300, 550, 390], "segment": 0})
    add("table", "resize/err-table-row-top-outside", {"op": "resize", "id": "t1", "rect": [50, 330, 550, 430], "segment": 0})

    # ---- set_label
    add("plain", "label/basic", {"op": "set_label", "id": "n1", "label": "Full name"})
    add("plain", "label/empty", {"op": "set_label", "id": "n1", "label": ""})
    add("plain", "label/non-latin", {"op": "set_label", "id": "n1", "label": "Nombre completo, José, 北京"})
    add("plain", "label/err-unknown", {"op": "set_label", "id": "x1", "label": "x"})
    add("plain", "label/err-not-a-string", {"op": "set_label", "id": "n1", "label": 4})
    add("table", "label/table", {"op": "set_label", "id": "t1", "label": "Drugs"})

    # ---- set_type
    add("plain", "type/text-to-checkbox-string-source-warns", {"op": "set_type", "id": "n1", "type": "checkbox"})
    add("plain", "type/text-to-checkbox-no-source", {"op": "set_type", "id": "n2", "type": "checkbox"})
    add("plain", "type/checkbox-to-text-boolean-warns", {"op": "set_type", "id": "c1", "type": "text"})
    add("plain", "type/radio-to-choice-mark", {"op": "set_type", "id": "r1", "type": "choice_mark"})
    add("plain", "type/same-type", {"op": "set_type", "id": "n1", "type": "text"})
    add("plain", "type/signature", {"op": "set_type", "id": "n2", "type": "signature"})
    add("plain", "type/err-radio-with-when-to-text", {"op": "set_type", "id": "r1", "type": "text"})
    add("plain", "type/err-to-table", {"op": "set_type", "id": "n1", "type": "table"})
    add("plain", "type/err-bad-type", {"op": "set_type", "id": "n1", "type": "dropdown"})
    add("plain", "type/err-stale-source", {"op": "set_type", "id": "s1", "type": "checkbox"})
    add("plain", "type/err-unknown", {"op": "set_type", "id": "n9", "type": "text"})
    add("table", "type/err-table", {"op": "set_type", "id": "t1", "type": "text"})
    add("table", "type/linked-cell-no-source", {"op": "set_type", "id": "m1", "type": "checkbox"})

    # ---- set_source
    add("plain", "source/basic", {"op": "set_source", "id": "n2", "source": _src("address.street")})
    add("plain", "source/replace", {"op": "set_source", "id": "n1", "source": _src("address.city", transforms=[{"op": "title"}])})
    add("plain", "source/none", {"op": "set_source", "id": "n1", "source": None})
    add("plain", "source/none-already-unmapped", {"op": "set_source", "id": "n2", "source": None})
    add("plain", "source/transforms-keep-given-args", {"op": "set_source", "id": "n2",
                                                       "source": _src("dob", transforms=[{"op": "date_format", "args": {"format": "%m/%d/%Y"}}, {"op": "strip", "args": {}}])})
    add("plain", "source/when-on-checkbox", {"op": "set_source", "id": "c2",
                                             "source": _src("sex", when={"op": "contains", "args": {"value": "F"}})})
    add("plain", "source/checkbox-warns", {"op": "set_source", "id": "c2", "source": _src("age")})
    add("plain", "source/overlap-warns-for-touched", {"op": "set_source", "id": "c2", "source": _src("smoker")})
    add("plain", "source/join-and-two-paths", {"op": "set_source", "id": "n2",
                                               "source": _src("address.street", "address.city", transforms=[{"op": "join", "args": {"sep": " "}}], join=" ")})
    add("plain", "source/on-aliases-array", {"op": "set_source", "id": "n2", "source": _src("aliases")})
    add("plain", "source/err-unknown-path", {"op": "set_source", "id": "n2", "source": _src("age_")})
    add("plain", "source/err-empty-paths", {"op": "set_source", "id": "n2", "source": _src()})
    add("plain", "source/err-object-path", {"op": "set_source", "id": "n2", "source": _src("address")})
    add("plain", "source/err-rows-path", {"op": "set_source", "id": "n2", "source": _src("meds")})
    add("plain", "source/err-table-column-path", {"op": "set_source", "id": "n2", "source": _src("meds[].dose")})
    add("plain", "source/err-bad-chain", {"op": "set_source", "id": "n2", "source": _src("name", transforms=[{"op": "slice", "args": {"start": "x"}}])})
    add("plain", "source/err-when-on-text", {"op": "set_source", "id": "n2", "source": _src("sex", when={"op": "equals", "args": {"value": 1}})})
    add("plain", "source/err-missing-source-key", {"op": "set_source", "id": "n2"})
    add("plain", "source/err-source-not-a-dict", {"op": "set_source", "id": "n2", "source": "name"})
    add("plain", "source/err-paths-not-a-list", {"op": "set_source", "id": "n2", "source": {"paths": "name"}})
    add("plain", "source/err-path-not-a-string", {"op": "set_source", "id": "n2", "source": {"paths": [1]}})
    add("plain", "source/err-transform-extra-key", {"op": "set_source", "id": "n2", "source": _src("name", transforms=[{"op": "upper", "x": 1}])})
    add("plain", "source/err-transform-missing-op", {"op": "set_source", "id": "n2", "source": _src("name", transforms=[{"args": {}}])})
    add("plain", "source/err-transform-args-not-a-dict", {"op": "set_source", "id": "n2", "source": _src("name", transforms=[{"op": "upper", "args": []}])})
    add("plain", "source/err-transforms-not-a-list", {"op": "set_source", "id": "n2", "source": _src("name", transforms={"op": "upper"})})
    add("plain", "source/err-join-not-a-string", {"op": "set_source", "id": "n2", "source": _src("name", join=3)})
    add("plain", "source/err-when-not-a-dict", {"op": "set_source", "id": "n2", "source": _src("name", when=3)})
    add("plain", "source/err-unknown-id", {"op": "set_source", "id": "q", "source": None})
    add("table", "source/err-linked-cell", {"op": "set_source", "id": "m1", "source": _src("name")})
    add("table", "source/err-linked-cell-bad-path-first", {"op": "set_source", "id": "m3", "source": _src("nope")})
    add("table", "source/linked-cell-none-ok", {"op": "set_source", "id": "m1", "source": None})
    add("table", "source/unlinked-plain-ok", {"op": "set_source", "id": "p2", "source": _src("address.street")})
    add("table", "source/err-table-two-paths", {"op": "set_source", "id": "t1", "source": _src("meds", "aliases")})
    add("table", "source/err-table-not-array", {"op": "set_source", "id": "t1", "source": _src("name")})
    add("table", "source/err-table-columns-not-in-schema", {"op": "set_source", "id": "t1", "source": _src("aliases")})
    add("table", "source/err-table-when", {"op": "set_source", "id": "t1", "source": _src("meds", when={"op": "equals", "args": {"value": 1}})})
    add("table", "source/table-none", {"op": "set_source", "id": "t1", "source": None})

    # ---- set_style
    add("plain", "style/font-size", {"op": "set_style", "id": "n1", "style": {"font_size": 12}})
    add("plain", "style/merges-keeps-others", {"op": "set_style", "id": "f3", "style": {"multiline": True}})
    add("plain", "style/smaller-size-lowers-min", {"op": "set_style", "id": "n1", "style": {"font_size": 4}})
    add("plain", "style/size-keeps-lower-min", {"op": "set_style", "id": "n1", "style": {"min_font_size": 3}},
        {"op": "set_style", "id": "n1", "style": {"font_size": 5}})
    add("plain", "style/auto", {"op": "set_style", "id": "f3", "style": {"font_size": "auto"}})
    add("plain", "style/comb-and-align", {"op": "set_style", "id": "n1", "style": {"comb": 9, "align": "right"}})
    add("plain", "style/comb-null", {"op": "set_style", "id": "n1", "style": {"comb": None}})
    add("plain", "style/empty", {"op": "set_style", "id": "n1", "style": {}})
    add("plain", "style/fraction-size", {"op": "set_style", "id": "n1", "style": {"font_size": 7.25}})
    add("plain", "style/string-bool", {"op": "set_style", "id": "n1", "style": {"multiline": "yes"}})
    add("plain", "style/err-min-over-explicit-size", {"op": "set_style", "id": "f3", "style": {"min_font_size": 20}})
    add("plain", "style/err-min-over-size-in-same-op", {"op": "set_style", "id": "n1", "style": {"min_font_size": 20, "font_size": 10}})
    add("plain", "style/err-size-negative", {"op": "set_style", "id": "n1", "style": {"font_size": -1}})
    add("plain", "style/err-size-zero", {"op": "set_style", "id": "n1", "style": {"font_size": 0}})
    add("plain", "style/err-size-word", {"op": "set_style", "id": "n1", "style": {"font_size": "big"}})
    add("plain", "style/err-size-null", {"op": "set_style", "id": "n1", "style": {"font_size": None}})
    add("plain", "style/err-min-zero", {"op": "set_style", "id": "n1", "style": {"min_font_size": 0}})
    add("plain", "style/err-align", {"op": "set_style", "id": "n1", "style": {"align": "middle"}})
    add("plain", "style/err-comb-fraction", {"op": "set_style", "id": "n1", "style": {"comb": 2.5}})
    add("plain", "style/err-comb-word", {"op": "set_style", "id": "n1", "style": {"comb": "x"}})
    add("plain", "style/err-multiline-word", {"op": "set_style", "id": "n1", "style": {"multiline": "maybe"}})
    add("plain", "style/err-multiline-two", {"op": "set_style", "id": "n1", "style": {"multiline": 2}})
    add("plain", "style/err-multiline-list", {"op": "set_style", "id": "n1", "style": {"multiline": []}})
    add("plain", "style/err-style-number", {"op": "set_style", "id": "n1", "style": 5})
    add("plain", "style/err-style-null", {"op": "set_style", "id": "n1", "style": None})
    add("plain", "style/err-style-missing", {"op": "set_style", "id": "n1"})
    add("plain", "style/err-several-at-once", {"op": "set_style", "id": "n1", "style": {"align": "x", "comb": "y", "multiline": "z"}})
    add("plain", "style/err-unknown-id", {"op": "set_style", "id": "q", "style": {"comb": 3}})
    add("table", "style/on-table", {"op": "set_style", "id": "t1", "style": {"font_size": 8}})

    # ---- op shape
    add("plain", "shape/err-unknown-op", {"op": "frobnicate", "id": "n1"})
    add("plain", "shape/err-op-missing", {"id": "n1"})
    add("plain", "shape/err-op-null", {"op": None})
    add("plain", "shape/err-op-number", {"op": 5})
    add("plain", "shape/err-extra-key", {"op": "set_label", "id": "n1", "label": "x", "colour": "red"})
    add("plain", "shape/err-extra-and-missing", af(type="text", rect=box, zzz=1))
    add("plain", "shape/err-id-missing", {"op": "remove_field"})
    add("plain", "shape/err-id-number", {"op": "remove_field", "id": 5})
    add("plain", "shape/err-id-null", {"op": "remove_field", "id": None})
    add("plain", "shape/err-type-word", af(type="txt", page=0, rect=box))
    add("plain", "shape/err-type-null", af(type=None, page=0, rect=box))
    add("plain", "shape/err-page-word", af(type="text", page="a", rect=box))
    add("plain", "shape/err-page-fraction", af(type="text", page=0.5, rect=box))
    add("plain", "shape/err-page-null", af(type="text", page=None, rect=box))
    add("plain", "shape/err-page-missing", af(type="text", rect=box))
    add("plain", "shape/err-rect-short", af(type="text", page=0, rect=[1, 2, 3]))
    add("plain", "shape/err-rect-long", af(type="text", page=0, rect=[1, 2, 3, 4, 5]))
    add("plain", "shape/err-rect-words", af(type="text", page=0, rect=[1, "b", 3, "c"]))
    add("plain", "shape/err-rect-null-item", af(type="text", page=0, rect=[1, None, 3, 4]))
    add("plain", "shape/err-rect-string", af(type="text", page=0, rect="abcd"))
    add("plain", "shape/err-rect-null", af(type="text", page=0, rect=None))
    add("plain", "shape/err-label-null", af(type="text", page=0, rect=box, label=None))
    add("plain", "shape/err-long-got-is-cut", af(type="text", page=0, rect=box, label=["x" * 60, "y" * 60]))
    add("plain", "shape/err-got-non-latin", af(type="text", page=0, rect=box, label=["José", "北京"]))
    add("plain", "shape/err-author", {"op": "set_label", "id": "n1", "label": "x"}, author="robot")
    add("plain", "shape/err-author-after-bad-op", {"op": "set_label", "id": "nope", "label": "x"}, author="robot")
    add("plain", "shape/err-second-op-fails-first-kept-out", {"op": "set_label", "id": "n1", "label": "A"},
        {"op": "set_label", "id": "n1", "label": 3})
    add("plain", "shape/chain-of-edits", af(type="text", page=0, rect=box), {"op": "set_label", "id": "f4", "label": "City"},
        {"op": "set_source", "id": "f4", "source": _src("address.city")},
        {"op": "resize", "id": "f4", "rect": [100, 200, 260, 222]}, {"op": "set_type", "id": "f4", "type": "text"},
        {"op": "set_style", "id": "f4", "style": {"align": "center"}}, {"op": "remove_field", "id": "f4"})

    # ---- templates with tables: warnings from table cover and linked cells
    add("table", "table/add-field-over-linked-cell-overlaps", af(type="text", page=0, rect=[62, 322, 198, 338]))
    add("table", "table/err-add-field-in-column-path", af(type="text", page=0, rect=[500, 350, 540, 370], source=_src("meds[].dose")))
    add("table", "table/move-table-warns-covered-and-mismatched", {"op": "move", "id": "t1", "dx": 0, "dy": 0.5})
    add("table", "table/move-cell-then-table", {"op": "move", "id": "m1", "dy": 1}, {"op": "move", "id": "t1", "dx": 1, "segment": 0})
    add("table", "table/err-move-table-away-from-linked-cells", {"op": "move", "id": "t1", "dy": 40, "segment": 0})
    return cases


OPS_CASES: list[dict] = _ops_cases()


# ---------------------------------------------------------------------- values
# kind "resolve": ``resolve(template, data)`` from ``formcast.render.values``. A case names a template
# and gives a record; the record is schema-valid (the browser has no validator, so Python must not
# report a record-level ``invalid``; the exporter checks). Table rows that cannot supply cells are
# still ``invalid`` warnings of the table, and those are compared.

VALUES_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "first": {"type": "string"},
        "last": {"type": "string"},
        "nick": {"type": "string"},
        "code": {"type": "string"},
        "note": {"type": "string"},
        "consent": {"type": "string"},
        "email": {"type": "string"},
        "odd key": {"type": "string"},
        "age": {"type": "integer"},
        "smoker": {"type": "boolean"},
        "sex": {"type": "string", "enum": ["F", "M", "X"]},
        "dob": {"type": "string", "format": "date"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "address": {
            "type": "object",
            "properties": {"street": {"type": "string"}, "city": {"type": "string"}, "zip": {"type": "string"}},
        },
        "meds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "dose": {"type": "number"},
                    "sched": {"type": "object", "properties": {"time": {"type": "string"}}},
                    "dose mg": {"type": "number"},
                    "refills": {"type": "array", "items": {"type": "string"}},
                    "pharmacy": {"type": "string"},
                },
            },
        },
        "grid": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "nums": {"type": "array", "items": {"type": "integer"}},
        "loose": {"type": "array"},
    },
}


def _vf(id: str, type: str, paths: list[str] | None = None, **src: Any) -> dict:
    """A box on page 0 with an optional source; ``src`` holds transforms/join/when."""
    box = {"id": id, "type": type, "page": 0, "rect": [72, 100, 300, 120], "origin": "human"}
    if paths is not None:
        box["source"] = {"paths": paths, **src}
    return box


def _vcol(key: str, x0: float, x1: float, transforms: list | None = None, fields: list | None = None) -> dict:
    return {"key": key, "x": [x0, x1], "transforms": transforms or [], "fields": fields or []}


def _vtable(id: str, path: str, segments: list[dict]) -> dict:
    return {"id": id, "type": "table", "origin": "human", "source": {"paths": [path]}, "segments": segments}


def _vseg(page: int, row_top: float, max_rows: int, columns: list[dict]) -> dict:
    return {"page": page, "rect": [30, row_top - 10, 560, row_top + 20 * max_rows + 10], "row_top": row_top,
            "row_height": 20, "max_rows": max_rows, "columns": columns}


def _values_templates() -> dict[str, dict]:
    form = {"fingerprint": None, "page_count": 2, "page_sizes": [[612, 792], [612, 792]], "kinds": ["digital", "digital"]}
    eq = lambda v: {"op": "equals", "args": {"value": v}}  # noqa: E731
    fields = [
        # ---- text: sources, joins, every transform family
        _vf("t_name", "text", ["name"]),
        _vf("t_full", "text", ["first", "last"], transforms=[T("join", sep=" ")]),
        _vf("t_implicit", "text", ["first", "last"]),
        _vf("t_partial", "text", ["first", "nick", "ghost"], transforms=[T("join", sep="-")]),
        _vf("t_tags_join", "text", ["tags"], join="; "),
        _vf("t_tags_join_empty", "text", ["tags"], join=""),
        _vf("t_tags", "text", ["tags"]),
        _vf("t_upper", "text", ["name"], transforms=[T("upper")]),
        _vf("t_split_title", "text", ["name"], transforms=[T("split", sep=" ", index=-1), T("lower"), T("title")]),
        _vf("t_date", "text", ["dob"], transforms=[T("date_format", format="%m/%d/%Y")]),
        _vf("t_date_part", "text", ["dob"], transforms=[T("date_part", part="year2")]),
        _vf("t_money", "text", ["age"], transforms=[T("number_format", decimals=2, thousands=",", prefix="$")]),
        _vf("t_slice", "text", ["last"], transforms=[T("slice", start=0, end=2), T("upper")]),
        _vf("t_char", "text", ["first"], transforms=[T("char_at", index=0)]),
        _vf("t_default", "text", ["note"], transforms=[T("default", value="N/A")]),
        _vf("t_frame", "text", ["note"], transforms=[T("strip"), T("prefix", value="Note: "), T("suffix", value=".")]),
        _vf("t_equals", "text", ["sex"], transforms=[eq("F")]),
        _vf("t_age", "text", ["age"]),
        _vf("t_smoker", "text", ["smoker"]),
        _vf("t_odd", "text", ['["odd key"]']),
        _vf("t_city", "text", ["address.city"]),
        _vf("t_sig", "signature", ["name"]),
        # ---- text problems
        _vf("t_missing", "text", ["address.street"]),
        _vf("t_array_path", "text", ["meds[].name"]),
        _vf("t_bad_path", "text", ["a..b"]),
        _vf("t_transform_error", "text", ["code"], transforms=[T("date_format", format="%Y")]),
        _vf("t_object", "text", ["address"]),
        _vf("t_nested_list", "text", ["grid"]),
        _vf("t_bad_chain", "text", ["name"], transforms=[T("shout")]),
        _vf("t_default_then_upper", "text", ["nick"], transforms=[T("default", value="none"), T("upper")]),
        # ---- skipped: no source / no paths
        _vf("t_no_source", "text"),
        _vf("t_no_paths", "text", []),
        # ---- choice boxes
        _vf("c_smoker", "checkbox", ["smoker"]),
        _vf("c_consent", "checkbox", ["consent"]),
        _vf("c_sex_f", "radio_option", ["sex"], when=eq("F")),
        _vf("c_sex_m", "radio_option", ["sex"], when=eq("M")),
        _vf("c_tags_b", "checkbox", ["tags"], when={"op": "contains", "args": {"value": "b"}}),
        _vf("c_note_x", "choice_mark", ["note"], when={"op": "contains", "args": {"value": "x"}}),
        _vf("c_age_42", "checkbox", ["age"], when=eq(42)),
        _vf("c_age", "checkbox", ["age"]),
        _vf("c_age_chain", "radio_option", ["age"], transforms=[T("number_format", decimals=0)], when=eq("42")),
        _vf("c_missing", "checkbox", ["address.city"]),
        _vf("c_error", "checkbox", ["code"], transforms=[T("date_part", part="year")]),
        _vf("c_bad_when", "checkbox", ["sex"], when={"op": "shout", "args": {}}),
        _vf("c_two_paths", "checkbox", ["smoker", "consent"], when={"op": "contains", "args": {"value": "true"}}),
        # ---- linked cells (plain boxes the table's columns point at; no source of their own)
        {"id": "m1", "type": "text", "page": 0, "rect": [60, 310, 200, 330], "origin": "human"},
        {"id": "m2", "type": "text", "page": 0, "rect": [60, 330, 200, 350], "origin": "human"},
        {"id": "m3", "type": "text", "page": 0, "rect": [210, 330, 300, 350], "origin": "human"},
        # ---- tables
        _vtable("tbl_meds", "meds", [
            _vseg(0, 310, 2, [
                _vcol("name", 60, 200, fields=["m1", "m2"]),
                _vcol("dose", 210, 300, transforms=[T("number_format", decimals=1)], fields=[None, "m3"]),
                _vcol("sched.time", 310, 360),
                _vcol('["dose mg"]', 370, 420),
                _vcol("refills", 430, 500),
            ]),
            _vseg(1, 210, 2, [_vcol("name", 40, 180), _vcol("dose", 190, 280, transforms=[T("number_format", decimals=1)])]),
        ]),
        _vtable("tbl_meds_err", "meds", [
            _vseg(0, 500, 3, [
                _vcol("name", 60, 200, transforms=[T("date_part", part="year")]),
                _vcol("dose", 210, 300, transforms=[T("default", value="-")]),
                _vcol("bogus", 310, 360),
            ]),
        ]),
        _vtable("tbl_grid", "grid", [
            _vseg(0, 600, 2, [_vcol(k, 60 + 40 * i, 90 + 40 * i) for i, k in enumerate(["0", "1", "-1", "5", "x", " 1 "])]),
            _vseg(1, 600, 2, [_vcol("0", 60, 100)]),
        ]),
        _vtable("tbl_nums", "nums", [_vseg(0, 700, 2, [_vcol("0", 60, 100)])]),
        # table sources that are not lists of rows
        _vtable("tbl_object", "address", [_vseg(1, 100, 1, [_vcol("city", 60, 100)])]),
        _vtable("tbl_text", "name", [_vseg(1, 130, 1, [_vcol("0", 60, 100)])]),
        _vtable("tbl_tags", "tags", [_vseg(1, 160, 3, [_vcol("0", 60, 100)])]),
        _vtable("tbl_loose", "loose", [_vseg(1, 400, 5, [_vcol("a", 60, 100), _vcol("0", 110, 150)])]),
    ]
    bare = [  # paths the schema doesn't have, so every record value is unmapped
        _vf("b_nothing", "text", ["ghost"]),
        _vf("b_choice", "checkbox", ["ghost.deeper"]),
        _vtable("b_tbl", "ghost", [_vseg(0, 100, 2, [_vcol("a", 60, 100)])]),
    ]
    return {
        "main": {"template_id": "main", "schema": VALUES_SCHEMA, "form": form, "fields": fields},
        "bare": {"template_id": "bare", "schema": VALUES_SCHEMA, "form": form, "fields": bare},
    }


VALUES_TEMPLATES: dict[str, dict] = _values_templates()


def _values_cases() -> list[dict]:
    meds_full = [
        {"name": "Aspirin", "dose": 81.5, "sched": {"time": "am"}, "dose mg": 5, "refills": ["x", "y"]},
        {"name": "Statin", "dose": 20.25, "sched": {"time": "pm"}, "dose mg": 10, "refills": []},
        {"name": "Zinc", "dose": 1500, "sched": {}, "refills": ["z"]},
    ]
    full = {
        "name": "Ann Lee", "first": "Ann", "last": "Lee", "nick": "Annie", "code": "abc", "note": "  see x notes ",
        "consent": "yes", "age": 42, "smoker": True, "sex": "F", "dob": "1990-03-07", "tags": ["a", "b"],
        "odd key": "odd", "address": {"street": "1 Main St", "city": "Zürich", "zip": "8001"},
        "meds": meds_full, "grid": [["a", "b", "c"], ["d", "e"]], "nums": [1, 2], "loose": [{"a": "p"}, ["q"]],
    }
    records = {
        "full": full,
        "empty": {},
        "blanks": {"name": "   ", "first": "", "last": " ", "note": "", "tags": [], "meds": [], "grid": [],
                   "nums": [], "loose": [], "address": {"city": ""}},
        "falsy": {"age": 0, "smoker": False, "consent": "no", "sex": "X", "note": "0", "tags": [""], "nick": "  ",
                  "first": "Bo", "last": "Li"},
        "off-word": {"consent": " Off ", "smoker": True, "sex": "M", "tags": ["b"], "note": "x marks", "age": 41,
                     "name": "José Núñez", "first": "José", "last": "Núñez", "dob": "2001-12-31"},
        "non-latin": {"name": "北京 Sí", "first": "北京", "last": "Sí", "note": "line\nbreak", "address": {"city": "Zürich"}},
        "overflow": {
            "meds": [{"name": f"Drug {i}", "dose": d} for i, d in enumerate([1.5, 3, 4.5, 6, 7.5], 1)] + [{"name": "Last", "dose": 3.25}],
            "grid": [["r0c0", "r0c1"], ["r1c0"], [], ["r3c0", "r3c1", "r3c2"], ["r4"]],
            "nums": [1, 2, 3, 4],
            "loose": [{"a": "p"}, ["q"], None, "s", 7],
        },
        "partial-rows": {
            "meds": [{"name": "Only name"}, {"dose": 2.5}, {"name": "Aspirin", "dose": 0, "refills": ["a", "b", "c"]}],
        },
        "unmapped": {
            "email": "a@b.c", "name": "Ann", "mystery": 1, "address": {"city": "X", "country": "CH"},
            "meds": [{"name": "A", "pharmacy": "Corner", "bogus": True}, {"name": "B", "pharmacy": ""}],
            "extra": {"nested": [1, 2]},
        },
    }
    cases: list[dict] = []
    for name, record in records.items():
        cases.append({"id": f"main/{name}", "kind": "resolve", "template": "main", "data": record})
    for name in ("full", "empty", "unmapped"):
        cases.append({"id": f"bare/{name}", "kind": "resolve", "template": "bare", "data": records[name]})
    return cases


VALUES_CASES: list[dict] = _values_cases()


# ------------------------------------------------------------------------ fill
# kind "fill": ``fill(template, pdf, data, check_fingerprint=False)`` from ``formcast.render.fill``.
# A case names a template and a PDF and gives a record. Templates are the archived agent templates
# (copied once into ``tests/web/fixtures/templates/`` so the fixtures never need the git-ignored
# extraction; plan Ruling 1) or the hand templates below; PDFs are dev forms or the two generated
# fixtures (``tests/web/fixtures/pdfs/``, made by the exporter with PyMuPDF). Records are
# schema-valid, as for the values group (the browser has no validator).

#: archived template name -> where the exporter copies it from (the extracted archive)
FILL_ARCHIVED: dict[str, str] = {
    "fw9_as_published": "eval/results/templates/2026-10-21_defer-placement/anthropic_eval/fw9_as_published.json",
    "wadoc_auth_as_published": "eval/results/templates/2026-10-21_defer-placement/anthropic_eval/wadoc_auth_as_published.json",
}

#: generated PDF name -> how to make it: one Letter page with a few labels, rotated or cropped; or
#: a dev form saved without its encryption. pdf-lib cannot read encrypted PDFs (forms/dev/wadoc_auth.pdf
#: has an owner password and an empty user password, which PyMuPDF opens transparently), so the
#: browser fills the copy PyMuPDF saves, as the showcase's sample bundles will ship it.
FILL_PDFS: dict[str, dict] = {
    "rotated90.pdf": {"rotation": 90, "cropbox": None},
    "cropbox.pdf": {"rotation": 0, "cropbox": [36, 36, 576, 756]},
    "wadoc_auth_decrypted.pdf": {"source": "forms/dev/wadoc_auth.pdf"},
}

#: labels drawn on the generated pages: (x, baseline y, text), unrotated page space
FILL_PDF_LABELS: list[tuple[float, float, str]] = [
    (72, 96, "Name"), (72, 136, "Code"), (36, 190, "I agree"), (114, 189, "Zurich"), (72, 296, "Items"),
    (72, 396, "Note"),
]

FILL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "code": {"type": "string"},
        "answer": {"type": "string", "enum": ["Sí", "No"]},
        "city": {"type": "string"},
        "note": {"type": "string"},
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": {"desc": {"type": "string"}, "qty": {"type": "integer"}}},
        },
    },
}

_FILL_FORM = {"fingerprint": None, "page_count": 1, "page_sizes": [[612, 792]], "kinds": ["digital"]}
_FILL_FORM_CROP = {"fingerprint": None, "page_count": 1, "page_sizes": [[540, 720]], "kinds": ["digital"]}
_FW9_FORM = {"fingerprint": None, "page_count": 6, "page_sizes": [[611.976, 791.968]] * 6, "kinds": ["fillable"] * 6}

_EQ = lambda v: {"op": "equals", "args": {"value": v}}  # noqa: E731


def _fill_templates() -> dict[str, dict]:
    hand = [  # the 4-field hand template: text, comb text, checkbox, choice_mark
        _field("name", "text", 0, [72, 100, 300, 120], source=_src("name")),
        _field("code", "text", 0, [72, 140, 160, 160], source=_src("code"), style={"comb": 4}),
        _field("agree", "checkbox", 0, [72, 180, 84, 192], source=_src("answer", when=_EQ("Sí"))),
        _field("zurich", "choice_mark", 0, [100, 178, 160, 194], source=_src("city", when=_EQ("Zürich"))),
    ]
    table = [
        _field("note", "text", 0, [72, 400, 300, 440], source=_src("note"), style={"multiline": True}),
        _field("yes", "radio_option", 0, [320, 400, 332, 412], source=_src("answer", when=_EQ("Sí"))),
        _field("unmapped", "text", 0, [320, 420, 500, 436]),
        {"id": "items", "type": "table", "origin": "human", "source": _src("items"), "segments": [
            {"page": 0, "rect": [60, 290, 400, 345], "row_top": 300, "row_height": 20, "max_rows": 2,
             "columns": [{"key": "desc", "x": [72, 300]},
                         {"key": "qty", "x": [310, 380], "transforms": [{"op": "number_format", "args": {"decimals": 0}}]}]},
        ]},
    ]
    fw9_rect = lambda x0, y0, x1, y1: [x0, y0, x1, y1]  # noqa: E731
    binding = [  # names, kinds and adoption against forms/dev/fw9.pdf's own widgets
        _field("named_text", "text", 0, fw9_rect(58.6, 118.0, 576.0, 132.0), source=_src("name"),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].f1_01[0]"),
        _field("wrong_kind", "text", 0, fw9_rect(73.0, 180.2, 81.0, 188.2), source=_src("code"),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].Boxes3a-b_ReadOrder[0].c1_1[0]"),
        _field("no_widget", "checkbox", 0, fw9_rect(180.0, 180.2, 188.0, 188.2), source=_src("answer", when=_EQ("Sí")),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].nothing_here[0]"),
        _field("named_box", "checkbox", 0, fw9_rect(252.0, 180.2, 260.0, 188.2), source=_src("answer", when=_EQ("Sí")),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].Boxes3a-b_ReadOrder[0].c1_1[2]"),
        _field("adopt_text", "text", 0, fw9_rect(55, 140, 580, 158), source=_src("city")),
        _field("adopt_box", "radio_option", 0, fw9_rect(320, 178, 336, 190), source=_src("answer", when=_EQ("No"))),
        _field("comb_cut", "text", 0, fw9_rect(417.6, 372.0, 460.8, 396.0), source=_src("code"),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].f1_11[0]"),
        _field("long_note", "text", 0, fw9_rect(58.6, 334.0, 576.0, 348.0), source=_src("note"),
               origin="widget", existing_widget="topmostSubform[0].Page1[0].f1_10[0]"),
    ]
    return {
        "hand": {"template_id": "hand", "schema": FILL_SCHEMA, "form": _FILL_FORM, "fields": hand},
        "hand_crop": {"template_id": "hand_crop", "schema": FILL_SCHEMA, "form": _FILL_FORM_CROP, "fields": hand},
        "table_crop": {"template_id": "table_crop", "schema": FILL_SCHEMA, "form": _FILL_FORM_CROP, "fields": table},
        "fw9_binding": {"template_id": "fw9_binding", "schema": FILL_SCHEMA, "form": _FW9_FORM, "fields": binding},
    }


FILL_TEMPLATES: dict[str, dict] = _fill_templates()


def _fill_cases() -> list[dict]:
    long_name = "Northwind Regional Cooperative of Independent Hardware, Lumber and Building Supply Dealers " * 2
    fw9 = {
        "probe": {
            "vendor_id": "V-1042", "legal_name": "Ann Lee", "business_name": "Lee Consulting LLC",
            "tax_classification": "llc", "llc_tax_classification": "s_corporation", "has_foreign_partners": True,
            "exempt_payee_code": "5", "address": {"street": "1 Main St", "city": "Springfield", "state": "IL",
                                                    "postal_code": "62701"},
            "ssn": "123-45-6789", "ein": "12-3456789", "remit_email": "ap@example.com", "certified_on": "2026-10-01",
        },
        "long": {
            "legal_name": long_name.strip(), "tax_classification": "other",
            "other_classification": "Unincorporated association of members with a very long description",
            "has_foreign_partners": False, "address": {"street": "4000 Commerce Parkway, Building 7, Suite 1200"},
        },
        "non-latin": {
            "legal_name": "José Núñez", "business_name": "北京 Trading Sí", "tax_classification": "c_corporation",
            "address": {"street": "Bahnhofstrasse 1", "city": "Zürich", "postal_code": "8001"},
        },
    }
    wadoc = {
        "probe": {
            "individual": {"first_name": "Ann", "middle_name": "Q", "last_name": "Lee", "date_of_birth": "1980-05-17",
                           "doc_number": "998877"},
            "releasing_organization": {"name": "Riverside Correctional Center",
                                       "address": {"street": "200 River Rd", "city": "Olympia", "state": "WA",
                                                   "postal_code": "98501"}, "phone": "360-555-0100"},
            "recipient_organization": {"name": "Harbor Legal Aid", "address": {"city": "Tacoma", "state": "WA"}},
            "information_requested": "Medical and education records from 2020 to 2025",
            "purpose": "Continuity of care", "signed_on": "2026-10-01",
            "witness": {"name": "Bo Li", "signed_on": "2026-10-01"},
        },
        "required-only": {
            "individual": {"first_name": "José", "last_name": "Núñez"},
            "releasing_organization": {"name": "北京 Clinic"}, "recipient_organization": {"name": "Zürich Hospital"},
        },
    }
    hand = {
        "probe": {"name": "Ann Lee", "code": "1234", "answer": "Sí", "city": "Zürich"},
        "latin1": {"name": "José Núñez", "code": "ÄB12", "answer": "Sí", "city": "Zürich"},
        "non-latin": {"name": "北京 Sí", "code": "北京12345", "answer": "No", "city": "北京"},
    }
    table = {
        "probe": {"note": "First line of the note\nsecond line", "answer": "Sí",
                  "items": [{"desc": "Bolts", "qty": 40}, {"desc": "Washers", "qty": 120}]},
        "non-latin": {"note": "北京 " + "long words " * 30, "answer": "No",
                      "items": [{"desc": "José's 北京 order", "qty": 3}, {"desc": "Zürich"}, {"desc": "dropped", "qty": 1}]},
    }
    binding = {
        "probe": {"name": "Ann Lee", "code": "123456", "answer": "No", "city": "Springfield", "note": long_name.strip()},
    }

    def case(id: str, template: str, pdf: str, data: dict) -> dict:
        return {"id": id, "kind": "fill", "template": template, "pdf": pdf, "data": data}

    out = [case(f"fw9/{k}", "fw9_as_published", "forms/dev/fw9.pdf", v) for k, v in fw9.items()]
    out += [case(f"wadoc/{k}", "wadoc_auth_as_published", "tests/web/fixtures/pdfs/wadoc_auth_decrypted.pdf", v) for k, v in wadoc.items()]
    out += [case(f"fw9-binding/{k}", "fw9_binding", "forms/dev/fw9.pdf", v) for k, v in binding.items()]
    out += [case(f"rotated90/{k}", "hand", "tests/web/fixtures/pdfs/rotated90.pdf", v) for k, v in hand.items()]
    out += [case(f"cropbox/{k}", "hand_crop", "tests/web/fixtures/pdfs/cropbox.pdf", v) for k, v in hand.items()]
    out += [case(f"cropbox-table/{k}", "table_crop", "tests/web/fixtures/pdfs/cropbox.pdf", v) for k, v in table.items()]
    return out


FILL_CASES: list[dict] = _fill_cases()
