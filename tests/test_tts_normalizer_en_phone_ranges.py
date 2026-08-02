"""Local phone numbers and chained ranges.

Two leftovers from the glued-numeric work, both cases where a hyphen
sat between two number words with nothing claiming it.

`555-1234` is genuinely ambiguous: a three-digit group, a dash and a
four-digit group is a local phone number AND the shape of a numeric
range like `100-2000`. Matching it unconditionally would read ordinary
ranges out digit by digit, so it needs a word that makes it a phone.
The failure mode is then a missed phone number rather than a mangled
range, which is the right way round.

`1-2-3` matched only its first pair, because the range pattern consumes
the digits either side of a dash and leaves the next one welded on.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


# ---------------------------------------------------------------------------
# Local 7-digit numbers, with a cue
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "call 555-1234 now",
    "phone us at 555-1234",
    "dial 555-1234",
    "ring me on 555-1234",
    "fax 555-1234",
])
def test_cued_local_number_is_read_digit_by_digit(src):
    out = normalize_text(src, "en")
    assert "five five five" in out
    assert "one two three four" in out


def test_the_cue_word_survives():
    assert normalize_text("call 555-1234", "en").startswith("call ")


# ---------------------------------------------------------------------------
# ...and without a cue it stays a range
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("pages 100-2000", "one hundred to two thousand"),
    ("the 100-2000 band", "one hundred to two thousand"),
])
def test_an_uncued_number_pair_is_still_a_range(src, expected):
    """This is the whole reason the cue is required."""
    out = normalize_text(src, "en")
    assert expected in out
    assert "one zero zero" not in out


def test_longer_phone_forms_are_unaffected():
    for src, want in [
        ("call (555) 123-4567", "four five six seven"),
        ("call +1-555-123-4567", "plus one"),
    ]:
        assert want in normalize_text(src, "en")


# ---------------------------------------------------------------------------
# Chained ranges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("pages 1-2-3", "one to two to three"),
    ("the 10-20-30 series", "ten to twenty to thirty"),
])
def test_a_chain_is_fully_expanded(src, expected):
    """One pass left the second dash welded between two number words."""
    out = normalize_text(src, "en")
    assert expected in out
    assert "-" not in out.replace("twenty-", "").replace("thirty-", "")


def test_a_plain_two_part_range_still_works():
    assert "forty-two to forty-five" in normalize_text("pages 42-45", "en")


def test_an_iso_date_is_not_a_chained_range():
    """`2026-07-31` has three dash-separated groups and must stay a date."""
    out = normalize_text("it was 2026-07-31", "en")
    assert "July" in out
    assert "to" not in out.split("July")[0]
