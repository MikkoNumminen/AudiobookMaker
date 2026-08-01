"""Tests for Pass V — Finnish colon-suffixed numerals.

Finnish writes a numeral's case ending after a colon: ``20:een``,
``1990:n``, ``5:llä``. Before Pass V none of these were handled. Pass G
expanded the digits in nominative and left the colon glued to the result,
so every one produced a non-word — ``0:sta`` came out as ``nolla:sta``.

Found while narrating a Finnish text containing ``0:sta −90 pikseliin``.
num2words already knows every Finnish case form, so the pass only has to
read the intended case off the suffix.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


@pytest.mark.parametrize("src,expected", [
    ("0:sta", "nollasta"),          # elative
    ("10:ssä", "kymmenessä"),       # inessive
    ("5:llä", "viidellä"),          # adessive
    ("20:een", "kahteenkymmeneen"),  # illative
    ("2:ksi", "kahdeksi"),          # translative
    ("7:nä", "seitsemänä"),         # essive
    ("3:lle", "kolmelle"),          # allative
    ("4:ltä", "neljältä"),          # ablative
])
def test_case_ending_is_read_off_the_suffix(src, expected):
    assert normalize_text(src, "fi").strip() == expected


def test_genitive_of_a_year():
    assert normalize_text("1990:n", "fi").strip() == (
        "tuhannen yhdeksänsadan yhdeksänkymmenen"
    )


@pytest.mark.parametrize("src", ["0:sta", "10:ssä", "20:een", "luku 3:ssa"])
def test_no_colon_survives_into_the_output(src):
    """The defect was a literal colon wedged inside a spoken word."""
    assert ":" not in normalize_text(src, "fi")


def test_suffix_in_a_sentence_keeps_its_neighbours():
    out = normalize_text("Arvo kasvoi 0:sta 90:een asti.", "fi")
    assert "nollasta" in out
    assert "asti" in out
    assert ":" not in out


# ---------------------------------------------------------------------------
# Things Pass V must NOT touch
# ---------------------------------------------------------------------------

def test_clock_times_are_left_to_pass_t():
    """`20:30` is a time, not a case ending. Pass T owns it."""
    out = normalize_text("kello 20:30", "fi")
    assert "kaksikymmentä" in out
    assert "kolmekymmentä" in out


def test_digit_only_ratios_are_not_treated_as_suffixes():
    """`1:5` has digits after the colon, so THIS pass must miss it.

    Pass X picks it up instead and reads it as a ratio — "yksi viiteen",
    with the second number in the illative. What matters here is only
    that Pass V does not try to read `5` as a case ending.
    """
    out = normalize_text("suhde 1:5", "fi")
    assert "yksi viiteen" in out


def test_unrecognised_suffix_still_loses_the_colon():
    """Fallback: a wrong case reads better than a colon inside a word."""
    out = normalize_text("5:xyz", "fi")
    assert ":" not in out
    assert "viisi" in out
