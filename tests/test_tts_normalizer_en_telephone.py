"""Tests for Pass P (telephone numbers) of the English TTS normalizer."""

from __future__ import annotations

import pytest

from src._en_pass_p_telephone import _pass_p_telephone


def test_parens_area_code():
    out = _pass_p_telephone("(555) 123-4567")
    assert out == "five five five, one two three, four five six seven"


def test_parens_no_space():
    out = _pass_p_telephone("(555)123-4567")
    assert out == "five five five, one two three, four five six seven"


def test_us_dashes():
    out = _pass_p_telephone("555-123-4567")
    assert out == "five five five, one two three, four five six seven"


def test_us_spaces():
    out = _pass_p_telephone("555 123 4567")
    assert out == "five five five, one two three, four five six seven"


def test_us_with_country_code_dashes():
    out = _pass_p_telephone("1-800-555-1234")
    assert out == "one, eight zero zero, five five five, one two three four"


def test_us_with_country_code_spaces():
    out = _pass_p_telephone("1 800 555 1234")
    assert out == "one, eight zero zero, five five five, one two three four"


def test_international_plus_one():
    out = _pass_p_telephone("+1 555 123 4567")
    assert out == (
        "plus one, five five five, one two three, four five six seven"
    )


def test_international_plus_44():
    out = _pass_p_telephone("+44 555 123 4567")
    assert out == (
        "plus four four, five five five, one two three, four five six seven"
    )


def test_international_dashes():
    out = _pass_p_telephone("+1-555-123-4567")
    assert out == (
        "plus one, five five five, one two three, four five six seven"
    )


def test_idempotent():
    once = _pass_p_telephone("(555) 123-4567")
    twice = _pass_p_telephone(once)
    assert once == twice


def test_empty_string():
    assert _pass_p_telephone("") == ""


def test_non_phone_text_passthrough():
    txt = "The quick brown fox jumps over the lazy dog."
    assert _pass_p_telephone(txt) == txt


def test_short_digit_run_not_matched():
    # 12-34 is not a phone-shaped pattern; leave it for cardinal pass.
    assert _pass_p_telephone("12-34") == "12-34"


def test_medium_digit_run_not_matched():
    # 123-4567 by itself (no area code) is not matched.
    assert _pass_p_telephone("123-4567") == "123-4567"


def test_year_like_not_matched():
    assert _pass_p_telephone("The year 2025 was great.") == (
        "The year 2025 was great."
    )


def test_isbn_like_not_matched():
    # Long digit runs with dashes shouldn't trigger. ISBN-like.
    txt = "978-0-13-110362-7"
    assert _pass_p_telephone(txt) == txt


def test_embedded_in_sentence():
    out = _pass_p_telephone("Call (555) 123-4567 today.")
    assert out == (
        "Call five five five, one two three, four five six seven today."
    )


def test_embedded_us_dashes():
    out = _pass_p_telephone("Phone: 555-123-4567, thanks.")
    assert out == (
        "Phone: five five five, one two three, four five six seven, thanks."
    )


def test_two_phones_in_one_string():
    out = _pass_p_telephone("Call 555-123-4567 or (555) 987-6543.")
    assert out == (
        "Call five five five, one two three, four five six seven"
        " or five five five, nine eight seven, six five four three."
    )


def test_eleven_digit_run_not_matched():
    # A raw 11-digit run with no separators must not be treated as a phone.
    assert _pass_p_telephone("15551234567") == "15551234567"


def test_fourteen_digit_run_not_matched():
    assert _pass_p_telephone("12345678901234") == "12345678901234"


def test_leading_plus_without_phone_shape():
    # "+5" alone isn't a phone.
    assert _pass_p_telephone("Score +5 points.") == "Score +5 points."


def test_parens_preserves_surrounding_punctuation():
    out = _pass_p_telephone("(Call (555) 123-4567!)")
    assert out == (
        "(Call five five five, one two three, four five six seven!)"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short"])
