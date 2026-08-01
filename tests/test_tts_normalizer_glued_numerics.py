"""Numbers welded to the token beside them.

A follow-up to the glued-token fixes in PR #184, covering the cases that
existed but were absent from the corpus that prompted that work, plus
two found while fixing them.

Every one is the same shape: a token reaching the synth in a form no
human would say, and none of them changes audio duration, so no
measurement can find them.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


# ---------------------------------------------------------------------------
# Digit fused AFTER letters — the mirror of the fixed `3D` case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,src,expected", [
    ("en", "MP3 file", "three"),
    ("en", "GPT4 said", "four"),
    ("en", "the H2O molecule", "two"),
    ("fi", "MP3-tiedosto", "kolme"),
    ("fi", "A4-arkki", "neljä"),
])
def test_digit_after_letters_is_split(lang, src, expected):
    """`MP3` became `MPthree`, `A4` became `Aneljä`."""
    out = normalize_text(src, lang)
    assert expected in out
    assert f"P{expected}" not in out
    assert f"A{expected}" not in out


# ---------------------------------------------------------------------------
# Version numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,expected", [("en", "point"), ("fi", "piste")])
def test_three_part_version_is_not_a_decimal(lang, expected):
    """`3.20.0` read as `kolme pilkku kaksi.nolla` — a period mid-token."""
    out = normalize_text("versio 3.20.0", lang)
    assert out.count(expected) == 2
    assert ".0" not in out


def test_finnish_version_uses_piste_not_pilkku():
    """Finnish separates decimals with a comma, so a dot is a dot."""
    out = normalize_text("versio 1.2.3", "fi")
    assert "piste" in out
    assert "pilkku" not in out


def test_finnish_date_is_not_mistaken_for_a_version():
    """`3.5.2026` is the same shape. Pass T claims it first."""
    out = normalize_text("tehtiin 3.5.2026", "fi")
    assert "toukokuuta" in out
    assert "piste" not in out


@pytest.mark.parametrize("lang,src", [("en", "pi is 3.14"), ("fi", "arvo on 3,14")])
def test_two_part_decimals_are_untouched(lang, src):
    assert "piste" not in normalize_text(src, lang)


# ---------------------------------------------------------------------------
# Currency magnitude suffixes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("it cost $1.5M", "one point five million dollars"),
    ("a $25M round", "twenty-five million dollars"),
    ("$3B valuation", "three billion dollars"),
    ("$500K salary", "five hundred thousand dollars"),
])
def test_currency_magnitude_survives_the_digit_capital_split(src, expected):
    """A regression the digit-capital split introduced.

    The currency pass already understood `$1.5M`, but only while the
    suffix was attached. Splitting it first gave "one dollar and fifty
    cents M" — wrong by a factor of a million.
    """
    assert expected in normalize_text(src, "en")


def test_iso_currency_code_beats_the_acronym_sweep():
    """`USD` is a three-letter all-caps token.

    The acronym pass spelled it out before the currency pass could use
    it, and `2.5M USD` then had its `M` read as "meters".
    """
    out = normalize_text("2.5M USD raised", "en")
    assert "million dollars" in out
    assert "meters" not in out


@pytest.mark.parametrize("src,expected", [
    ("maksoi 1,5 M€", "miljoonaa euroa"),
    ("hinta 500 k€", "tuhatta euroa"),
])
def test_finnish_magnitude_currency(src, expected):
    """`M€` had no entry, so the euro was dropped by the symbol gate."""
    out = normalize_text(src, "fi")
    assert expected in out
    assert "€" not in out


@pytest.mark.parametrize("lang,src", [
    ("en", "a 4K screen"), ("en", "solid 3D letters"), ("fi", "3D-kirjaimina"),
])
def test_non_currency_digit_capital_still_splits(lang, src):
    """The currency exception must not swallow the original fix."""
    out = normalize_text(src, lang)
    assert "K " in out or "D" in out
    assert "fourK" not in out and "threeD" not in out


# ---------------------------------------------------------------------------
# Fractions vs pairs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("1/2 of it", "one half"),
    ("3/4 done", "three quarters"),
    ("2/3 majority", "two thirds"),
    ("1/100 chance", "one hundredth"),
])
def test_genuine_fractions_still_read_as_fractions(src, expected):
    assert expected in normalize_text(src, "en")


@pytest.mark.parametrize("src,expected", [
    ("a 50/50 split", "fifty fifty"),
    ("15/75 pricing", "fifteen seventy-five"),
])
def test_a_pair_is_not_a_fraction(src, expected):
    """Any `N/M` used to invent an ordinal: `50/50` → "fifty fiftieths"."""
    out = normalize_text(src, "en")
    assert expected in out
    assert "fiftieths" not in out
    assert "seventy-fifths" not in out


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("pages 42-45", "forty-two to forty-five"),
    ("a 10-20 range", "ten to twenty"),
    ("3-4 people", "three to four"),
])
def test_english_numeric_ranges(src, expected):
    """English had a year-range rule but no general one, so `42-45`
    kept a hyphen welded between two number words."""
    assert expected in normalize_text(src, "en")


def test_year_ranges_keep_their_own_reading():
    assert "nineteen fourteen to nineteen eighteen" in normalize_text(
        "the 1914-1918 war", "en")


def test_iso_date_is_not_a_range():
    assert "July" in normalize_text("it was 2026-07-31", "en")


# ---------------------------------------------------------------------------
# A hyphen is only a minus when it starts a number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,src", [
    ("en", "COVID-19 cases"), ("en", "the T-1000 model"),
    ("en", "GPT-4 said"), ("fi", "COVID-19 tapaukset"),
])
def test_hyphenated_identifier_is_not_arithmetic(lang, src):
    """`COVID-19` was read as "C O V I Dminus nineteen" in English.

    The hyphen there is a compound boundary. Reading it as a sign also
    glued the word onto the letter before it.
    """
    out = normalize_text(src, lang)
    assert "minus" not in out
    assert "miinus" not in out


@pytest.mark.parametrize("lang,src,expected", [
    ("en", "a value of -19", "minus nineteen"),
    ("en", "it fell -5 points", "minus five"),
    ("fi", "arvo on -19", "miinus yhdeksäntoista"),
    ("fi", "laski -5 pistettä", "miinus viisi"),
])
def test_a_real_negative_still_reads_as_minus(lang, src, expected):
    """Finnish had the opposite bug: it never read a minus at all,
    leaving the hyphen welded to the number word."""
    assert expected in normalize_text(src, lang)


@pytest.mark.parametrize("src", ["1500-luvulla", "sivut 42-45", "3D-kirjaimina"])
def test_finnish_compounds_and_ranges_are_not_minus(src):
    assert "miinus" not in normalize_text(src, "fi")
