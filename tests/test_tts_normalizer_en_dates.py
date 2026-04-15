"""Tests for Pass O — English date expression normalization."""

from __future__ import annotations

import pytest

pytest.importorskip("num2words")

from src._en_pass_o_dates import _pass_o_dates  # noqa: E402
from src.tts_normalizer_en import normalize_english_text  # noqa: E402


# ---------------------------------------------------------------------------
# Core format coverage
# ---------------------------------------------------------------------------


def test_month_abbrev_first_with_year():
    assert _pass_o_dates("Jan 5, 1901") == (
        "January fifth nineteen oh one"
    )


def test_day_first_full_month_with_year():
    assert _pass_o_dates("5 January 1901") == (
        "the fifth of January nineteen oh one"
    )


def test_month_full_first_with_year():
    assert _pass_o_dates("January 5, 1901") == (
        "January fifth nineteen oh one"
    )


def test_us_slash_format():
    assert _pass_o_dates("1/5/2020") == (
        "January fifth twenty twenty"
    )


def test_iso_format():
    assert _pass_o_dates("2020-01-05") == (
        "January fifth twenty twenty"
    )


def test_yearless_abbrev():
    assert _pass_o_dates("Jan 5") == "January fifth"


# ---------------------------------------------------------------------------
# Year-less variants
# ---------------------------------------------------------------------------


def test_yearless_day_first():
    assert _pass_o_dates("5 January") == "the fifth of January"


def test_yearless_full_month_first():
    assert _pass_o_dates("July 4") == "July fourth"


def test_yearless_with_ordinal_suffix():
    assert _pass_o_dates("July 4th") == "July fourth"


# ---------------------------------------------------------------------------
# Month abbreviation coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("abbrev, full", [
    ("Jan", "January"),
    ("Feb", "February"),
    ("Mar", "March"),
    ("Apr", "April"),
    ("Jun", "June"),
    ("Jul", "July"),
    ("Aug", "August"),
    ("Sep", "September"),
    ("Sept", "September"),
    ("Oct", "October"),
    ("Nov", "November"),
    ("Dec", "December"),
])
def test_every_abbreviation(abbrev, full):
    out = _pass_o_dates(f"{abbrev} 5, 1901")
    assert out == f"{full} fifth nineteen oh one"


def test_may_full_word_only():
    # May has no distinct abbreviation — ensure the full word still works.
    assert _pass_o_dates("May 1, 1945") == (
        "May first nineteen forty-five"
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_passthrough():
    assert _pass_o_dates("") == ""


def test_plain_text_passthrough():
    assert _pass_o_dates("hello world") == "hello world"


def test_invalid_month_left_alone():
    assert _pass_o_dates("Foo 5, 1901") == "Foo 5, 1901"


def test_invalid_day_left_alone():
    # February 30 doesn't exist — leave unchanged.
    assert _pass_o_dates("Feb 30, 1901") == "Feb 30, 1901"


def test_invalid_iso_day_left_alone():
    assert _pass_o_dates("2020-13-05") == "2020-13-05"


def test_idempotent():
    once = _pass_o_dates("January 5, 1901")
    twice = _pass_o_dates(once)
    assert once == twice


def test_idempotent_iso():
    once = _pass_o_dates("2020-01-05")
    twice = _pass_o_dates(once)
    assert once == twice


def test_date_in_sentence():
    out = _pass_o_dates("He was born on Jan 5, 1901 in Paris.")
    assert "January fifth nineteen oh one" in out
    assert "Paris" in out


def test_abbrev_with_trailing_period():
    # "Jan." (abbreviation with period) also recognized.
    assert _pass_o_dates("Jan. 5, 1901") == (
        "January fifth nineteen oh one"
    )


def test_day_first_with_of():
    assert _pass_o_dates("5th of January 1901") == (
        "the fifth of January nineteen oh one"
    )


# ---------------------------------------------------------------------------
# Integration with full pipeline — ensures ordering with Pass F works.
# ---------------------------------------------------------------------------


def test_full_pipeline_month_first():
    assert normalize_english_text("January 5, 1901") == (
        "January fifth nineteen oh one"
    )


def test_full_pipeline_iso():
    assert normalize_english_text("2020-01-05") == (
        "January fifth twenty twenty"
    )


def test_full_pipeline_us_slash():
    assert normalize_english_text("1/5/2020") == (
        "January fifth twenty twenty"
    )


def test_full_pipeline_non_date_unaffected():
    # Bare year without date context should still use Pass F rules.
    # Pass F (years) keeps num2words' raw output, which uses a hyphen
    # between "oh" and the unit digit. That's fine — Pass O only
    # post-processes the year when it consumes it as part of a date.
    out = normalize_english_text("in 1901")
    assert "nineteen" in out and "one" in out
