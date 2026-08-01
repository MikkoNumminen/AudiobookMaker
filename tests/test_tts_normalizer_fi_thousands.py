"""Pass U — Finnish writes thousands with a space, not a comma.

`24 000`, `231 369`, `3 755 242`. Nothing joined the groups, so Pass G
saw two or three independent integers and read them out one after
another. `24 000 partikkelin` was narrated "kaksikymmentä neljä nolla
partikkelin" — twenty-four, zero — and `3 755 242` became three separate
numbers in a row.

Every large figure in a Finnish text was gibberish, delivered fluently.
Found by transcribing a narrated report whose whole subject was numbers.
English was never affected: it separates thousands with a comma, which
num2words already understood.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


@pytest.mark.parametrize("src,expected", [
    ("24 000", "neljätuhatta"),
    ("231 369", "yksituhatta"),
    ("92 457", "kaksituhatta"),
    ("3 755 242", "kolmemiljoonaa"),
])
def test_space_separated_groups_read_as_one_number(src, expected):
    assert expected in normalize_text(src, "fi")


def test_the_reported_phrase():
    out = normalize_text("24 000 partikkelin kenttä", "fi")
    assert "neljätuhatta" in out
    assert "neljä nolla" not in out


def test_non_breaking_space_is_also_a_separator():
    """Word processors emit U+00A0 here and it is invisible in a diff."""
    assert "neljätuhatta" in normalize_text("24 000 partikkelia", "fi")


# ---------------------------------------------------------------------------
# Numbers that merely sit next to each other must stay apart
# ---------------------------------------------------------------------------

def test_a_year_followed_by_a_number_is_not_merged():
    """`1917 500` is two numbers. A thousands group starts with 1-3 digits."""
    out = normalize_text("vuonna 1917 500 ihmistä", "fi")
    assert "seitsemäntoista" in out   # 1917 intact
    assert "viisisataa" in out        # 500 intact


def test_two_small_numbers_separated_by_a_word():
    out = normalize_text("sivu 7 ja 123 muuta", "fi")
    assert "seitsemän" in out
    assert "satakaksikymmentä" in out


def test_a_two_digit_group_is_not_a_thousands_group():
    """Only groups of exactly three digits follow the separator."""
    out = normalize_text("7 12 asiaa", "fi")
    assert "seitsemän" in out
    assert "kaksitoista" in out


def test_english_is_untouched_by_this_pass():
    """English uses commas and num2words already handled them."""
    assert "thousand" in normalize_text("24,000 particles", "en")
