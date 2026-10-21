import pymupdf
import pytest

from formcast.render.fit import Fit, fit_text, unencodable_chars

R = (0, 0, 100, 14)


def test_short_text_auto_fits_at_start_size():
    f = fit_text("Ada", R, font_size="auto", min_font_size=6)
    assert f.status == "ok" and f.font_size == pytest.approx(min(12.0, 12 / 1.15), abs=0.01)


def test_long_text_shrinks():
    f = fit_text("Alexandra Wilhelmina Featherstonehaugh", R, font_size="auto", min_font_size=4)
    assert f.status == "shrunk" and 4 <= f.font_size < 10
    assert pymupdf.get_text_length(f.text, "helv", f.font_size) <= 96


def test_overflow_keeps_full_text():
    s = "x" * 200
    f = fit_text(s, R, font_size="auto", min_font_size=6)
    assert f.status == "overflow" and f.text == s and f.font_size == 6 and not f.truncated


def test_numeric_font_size_is_start():
    assert fit_text("Ada", R, font_size=8, min_font_size=6).font_size == 8


def test_multiline_wraps_then_shrinks():
    f = fit_text("word " * 40, (0, 0, 100, 60), font_size="auto", min_font_size=4, multiline=True)
    assert f.lines > 1 and f.status in ("ok", "shrunk")


def test_max_lines_limits_wrap():
    f = fit_text("word " * 40, (0, 0, 100, 60), font_size="auto", min_font_size=6, max_lines=2)
    assert f.status == "overflow" and f.font_size == 6 and f.lines > 2
    g = fit_text("word " * 4, (0, 0, 100, 60), font_size="auto", min_font_size=4, max_lines=2)
    assert g.lines <= 2 and g.status in ("ok", "shrunk")


def test_comb_truncates_to_n():
    f = fit_text("1234567890", (0, 0, 90, 14), font_size="auto", min_font_size=6, comb=9)
    assert f.text == "123456789" and f.truncated


def test_unencodable():
    assert unencodable_chars("Nguyễn 王") == "ễ王"
    assert fit_text("Nguyễn", R, font_size="auto", min_font_size=6).unencodable == "ễ"


def test_unencodable_distinct_in_order():
    assert unencodable_chars("王ễ王ễ") == "王ễ"
    assert unencodable_chars("café ñ €") == ""


def test_max_len_cuts_before_fitting():
    f = fit_text("abcdefghij", R, font_size=8, min_font_size=6, max_len=4)
    assert f.text == "abcd" and f.truncated and f.status == "ok"
    g = fit_text("abcd", R, font_size=8, min_font_size=6, max_len=4)
    assert not g.truncated


def test_never_starts_below_min():
    f = fit_text("Ada", (0, 0, 100, 6), font_size="auto", min_font_size=6)
    assert f.font_size == 6


def test_auto_capped_at_12():
    f = fit_text("Ada", (0, 0, 100, 60), font_size="auto", min_font_size=6)
    assert f.font_size == 12.0


def test_overlong_word_broken_by_chars():
    f = fit_text("x" * 100, (0, 0, 50, 200), font_size=10, min_font_size=10, multiline=True)
    assert f.lines > 1 and f.status == "ok" and f.text == "x" * 100


def test_multiline_overflow_reports_lines_at_min():
    f = fit_text("word " * 200, (0, 0, 100, 30), font_size="auto", min_font_size=6, multiline=True)
    assert f.status == "overflow" and f.font_size == 6 and f.text == "word " * 200 and f.lines > 1


def test_comb_shrinks_to_cell_width():
    f = fit_text("WWWWWWWWW", (0, 0, 45, 30), font_size="auto", min_font_size=2, comb=9)
    assert f.status == "shrunk"
    assert pymupdf.get_text_length("W", "helv", f.font_size) <= 45 / 9 + 1e-6


def test_fit_is_frozen():
    f = fit_text("Ada", R, font_size=8, min_font_size=6)
    assert isinstance(f, Fit)
    with pytest.raises(Exception):
        f.font_size = 1  # type: ignore[misc]
