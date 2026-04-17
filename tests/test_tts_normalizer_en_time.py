"""Tests for Pass N — English time-of-day normalization.

Mirrors the TestPassX pattern used in tests/test_tts_normalizer_en.py,
but split into a dedicated file the way Pass O (dates) has its own
tests/test_tts_normalizer_en_dates.py module.

Pass N (src/tts_normalizer_en.py ~648-677) verbalizes HH:MM strings:

    - 1–12 whole hour    -> "<hour> o'clock"  (civilian reading)
    - 0 or 13–23 whole   -> "<hour> hundred hours"  (military reading)
    - Minutes 1–9        -> "oh one" ... "oh nine"
    - Minutes 10–59      -> "<cardinal>" (num2words, "twenty-one" style)

The underlying regex (_EN_TIME_RE) only matches a 1–2 digit hour
followed by a colon and exactly two digits for the minute, bounded by
word boundaries. That means "3:1 ratio" and "version 2:0" are not
touched.
"""

from __future__ import annotations

import pytest

pytest.importorskip("num2words")

from src.tts_normalizer_en import (  # noqa: E402
    _pass_n_time,
    normalize_english_text,
)


# ---------------------------------------------------------------------------
# Basic 12h with am/pm (full pipeline — Pass C expands "a.m."/"p.m.")
# ---------------------------------------------------------------------------


class TestBasic12hAmPm:
    def test_pm_suffix(self):
        out = normalize_english_text("Meet at 3:00 p.m.")
        assert "three o'clock" in out
        assert "p m" in out

    def test_am_suffix(self):
        out = normalize_english_text("Alarm at 6:00 a.m.")
        assert "six o'clock" in out
        assert "a m" in out

    def test_pm_without_periods_pass_n_only(self):
        # Pass N itself doesn't care about am/pm; it just rewrites the
        # digits. The suffix word rides along untouched.
        assert _pass_n_time("3:00 pm") == "three o'clock pm"

    def test_am_without_periods_pass_n_only(self):
        assert _pass_n_time("6:00 am") == "six o'clock am"


# ---------------------------------------------------------------------------
# 12h with minutes
# ---------------------------------------------------------------------------


class TestTwelveHourWithMinutes:
    @pytest.mark.parametrize("text,expected", [
        pytest.param("3:15", "three fifteen", id="3_15"),
        pytest.param("3:30", "three thirty", id="3_30"),
        pytest.param("3:45", "three forty-five", id="3_45"),
        pytest.param("11:59", "eleven fifty-nine", id="11_59"),
        pytest.param("7:20", "seven twenty", id="7_20"),
    ])
    def test_minute_bearing(self, text, expected):
        assert _pass_n_time(text) == expected

    def test_3_15_pm_full_pipeline(self):
        out = normalize_english_text("Starts 3:15 p.m. sharp.")
        assert "three fifteen" in out
        assert "p m" in out


# ---------------------------------------------------------------------------
# 24h hours with minutes
# ---------------------------------------------------------------------------


class TestTwentyFourHourWithMinutes:
    @pytest.mark.parametrize("text,expected", [
        pytest.param("13:30", "thirteen thirty", id="13_30"),
        pytest.param("14:30", "fourteen thirty", id="14_30"),
        pytest.param("15:00", "fifteen hundred hours", id="15_00_military"),
        pytest.param("18:30", "eighteen thirty", id="18_30"),
        pytest.param("20:45", "twenty forty-five", id="20_45"),
        pytest.param("23:59", "twenty-three fifty-nine", id="23_59"),
    ])
    def test_24h(self, text, expected):
        assert _pass_n_time(text) == expected


# ---------------------------------------------------------------------------
# Minute teens — "oh one" .. "oh nine" for :01..:09
# ---------------------------------------------------------------------------


class TestMinuteTeens:
    @pytest.mark.parametrize("text,expected", [
        pytest.param("8:01", "eight oh one", id="8_01"),
        pytest.param("8:02", "eight oh two", id="8_02"),
        pytest.param("8:03", "eight oh three", id="8_03"),
        pytest.param("8:04", "eight oh four", id="8_04"),
        pytest.param("8:05", "eight oh five", id="8_05"),
        pytest.param("8:06", "eight oh six", id="8_06"),
        pytest.param("8:07", "eight oh seven", id="8_07"),
        pytest.param("8:08", "eight oh eight", id="8_08"),
        pytest.param("8:09", "eight oh nine", id="8_09"),
    ])
    def test_oh_minutes(self, text, expected):
        assert _pass_n_time(text) == expected

    def test_oh_minute_in_24h(self):
        # 24h hour + oh-minute still uses the "oh N" form (not military).
        assert _pass_n_time("17:07") == "seventeen oh seven"


# ---------------------------------------------------------------------------
# Noon / midnight
# ---------------------------------------------------------------------------


class TestNoonAndMidnight:
    def test_noon_12_00_pm(self):
        # Pass N renders 12:00 as "twelve o'clock" regardless of am/pm —
        # it does NOT emit "noon" as a special word.
        assert _pass_n_time("12:00 pm") == "twelve o'clock pm"

    def test_midnight_12_00_am(self):
        # Likewise: no "midnight" substitution — just "twelve o'clock".
        assert _pass_n_time("12:00 am") == "twelve o'clock am"

    def test_midnight_24h_0_00(self):
        # The 0:00 case uses the military reading per the docstring.
        assert _pass_n_time("0:00") == "zero hundred hours"

    def test_noon_bare(self):
        assert _pass_n_time("12:00") == "twelve o'clock"


# ---------------------------------------------------------------------------
# Minute sampling 0–59
# ---------------------------------------------------------------------------


class TestMinuteSampling:
    @pytest.mark.parametrize("text,expected", [
        pytest.param("5:00", "five o'clock", id="5_00"),
        pytest.param("5:15", "five fifteen", id="5_15"),
        pytest.param("5:30", "five thirty", id="5_30"),
        pytest.param("5:45", "five forty-five", id="5_45"),
        pytest.param("5:59", "five fifty-nine", id="5_59"),
    ])
    def test_sample_minutes(self, text, expected):
        assert _pass_n_time(text) == expected


# ---------------------------------------------------------------------------
# Negative cases — strings that must NOT be modified
# ---------------------------------------------------------------------------


class TestNonTimeStringsUntouched:
    def test_ratio_with_single_digit_minute(self):
        # Regex requires two digits after the colon — "3:1" doesn't match.
        assert _pass_n_time("3:1 ratio") == "3:1 ratio"

    def test_version_string(self):
        # Same rule — "2:0" is a single-digit minute, left alone.
        assert _pass_n_time("version 2:0") == "version 2:0"

    @pytest.mark.parametrize("text", ["25:99", "10:75", "24:00", "99:99"])
    def test_out_of_range_left_alone(self, text):
        # Regex itself excludes hour>=24 and minute>=60, so these
        # pass through untouched.
        assert _pass_n_time(text) == text

    def test_plain_prose_unchanged(self):
        assert _pass_n_time("no times in this sentence") == (
            "no times in this sentence"
        )

    def test_empty_string(self):
        assert _pass_n_time("") == ""

    def test_idempotent(self):
        once = _pass_n_time("3:30")
        twice = _pass_n_time(once)
        assert once == twice
