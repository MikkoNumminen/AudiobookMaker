"""Tests for src/tts_normalizer_en.py — English text normalizer.

One TestPassX class per pass, plus a top-level integration block that
runs the public entry point on representative book paragraphs.

The Finnish normalizer has ~419 tests across 16 passes (~26/pass);
this file aims for a similar density on the 11 phase-1 English passes.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import LanguageMismatchError
from src.tts_normalizer_en import (
    _pass_a_metadata_strip,
    _pass_b_whitespace_quotes,
    _pass_c_abbreviations,
    _pass_d_roman_in_context,
    _pass_e_ordinal_digits,
    _pass_f_years,
    _pass_g_cardinal,
    _pass_h_decimals,
    _pass_i_fractions,
    _pass_j_periods,
    _pass_k_whitespace,
    _roman_to_int,
    normalize_english_text,
)


# ---------------------------------------------------------------------------
# Pass A — metadata strip
# ---------------------------------------------------------------------------


class TestPassAMetadataStrip:
    def test_isbn_13_stripped(self):
        out = _pass_a_metadata_strip("ISBN: 978-0-385-72353-1 reads.")
        assert "978" not in out

    def test_isbn_10_stripped(self):
        out = _pass_a_metadata_strip("ISBN 0-385-72353-X here.")
        assert "385" not in out

    def test_doi_stripped(self):
        out = _pass_a_metadata_strip(
            "See doi:10.1234/example.5678 for details."
        )
        assert "10.1234" not in out

    def test_doi_url_stripped(self):
        out = _pass_a_metadata_strip(
            "Source: https://doi.org/10.1234/foo cited."
        )
        assert "doi.org" not in out

    def test_copyright_stripped(self):
        out = _pass_a_metadata_strip(
            "© 2003 Tom Holland. The book begins."
        )
        assert "2003" not in out
        assert "begins" in out

    def test_cc_license_stripped(self):
        out = _pass_a_metadata_strip("Available under CC-BY-SA 4.0 license.")
        assert "CC-BY" not in out

    def test_all_rights_reserved_stripped(self):
        out = _pass_a_metadata_strip("All rights reserved. Then text.")
        assert "rights" not in out.lower()

    def test_idempotent(self):
        text = "© 2003 Author."
        once = _pass_a_metadata_strip(text)
        twice = _pass_a_metadata_strip(once)
        assert once == twice

    def test_no_metadata_passes_through(self):
        text = "Caesar crossed the Rubicon."
        assert _pass_a_metadata_strip(text) == text

    def test_empty_string(self):
        assert _pass_a_metadata_strip("") == ""


# ---------------------------------------------------------------------------
# Pass B — whitespace and quotes
# ---------------------------------------------------------------------------


class TestPassBWhitespaceQuotes:
    def test_smart_single_quotes_to_ascii(self):
        out = _pass_b_whitespace_quotes("\u2018hello\u2019")
        assert out == "'hello'"

    def test_smart_double_quotes_to_ascii(self):
        out = _pass_b_whitespace_quotes("\u201chello\u201d")
        assert out == '"hello"'

    def test_em_dash_to_spaced_hyphen(self):
        out = _pass_b_whitespace_quotes("then\u2014now")
        assert "-" in out
        assert "\u2014" not in out

    def test_en_dash_to_hyphen(self):
        out = _pass_b_whitespace_quotes("1914\u20131918")
        assert "1914-1918" == out

    def test_nbsp_to_space(self):
        out = _pass_b_whitespace_quotes("hello\u00a0world")
        assert out == "hello world"

    def test_ellipsis_collapsed(self):
        out = _pass_b_whitespace_quotes("wait... what")
        assert "\u2026" in out

    def test_long_dot_run_collapsed(self):
        out = _pass_b_whitespace_quotes("end.....stop")
        assert "\u2026" in out

    def test_toc_dot_leader_dropped(self):
        out = _pass_b_whitespace_quotes("Chapter One ........... 5")
        assert "5" not in out

    def test_empty_string(self):
        assert _pass_b_whitespace_quotes("") == ""

    def test_idempotent(self):
        text = "Hello \u2014 world."
        once = _pass_b_whitespace_quotes(text)
        twice = _pass_b_whitespace_quotes(once)
        assert once == twice


# ---------------------------------------------------------------------------
# Pass C — abbreviations
# ---------------------------------------------------------------------------


class TestPassCAbbreviations:
    def test_mr(self):
        assert "Mister" in _pass_c_abbreviations("Mr. Smith arrived.")

    def test_mrs(self):
        assert "Misses" in _pass_c_abbreviations("Mrs. Smith arrived.")

    def test_dr(self):
        assert "Doctor" in _pass_c_abbreviations("Dr. Watson observed.")

    def test_prof(self):
        assert "Professor" in _pass_c_abbreviations("Prof. Plum did it.")

    def test_ie(self):
        assert "that is" in _pass_c_abbreviations("namely, i.e. clearly")

    def test_eg(self):
        assert "for example" in _pass_c_abbreviations("such as e.g. cats")

    def test_etc(self):
        assert "et cetera" in _pass_c_abbreviations("dogs, cats, etc.")

    def test_vs(self):
        assert "versus" in _pass_c_abbreviations("Caesar vs. Pompey")

    def test_us_united_states(self):
        out = _pass_c_abbreviations("U.S. policy")
        assert "United States" in out

    def test_uk_united_kingdom(self):
        out = _pass_c_abbreviations("the U.K. government")
        assert "United Kingdom" in out

    def test_no_number(self):
        assert "Number" in _pass_c_abbreviations("No. 7")

    def test_vol_chapter(self):
        out = _pass_c_abbreviations("Vol. III, Ch. 4")
        assert "Volume" in out and "Chapter" in out

    def test_pages(self):
        assert "pages" in _pass_c_abbreviations("see pp. 23-25")

    def test_st_saint_before_capital(self):
        out = _pass_c_abbreviations("St. Peter")
        assert "Saint" in out

    def test_st_street_otherwise(self):
        out = _pass_c_abbreviations("Main St.")
        assert "Street" in out

    def test_am_pm_lowercase(self):
        out = _pass_c_abbreviations("at 3 a.m. sharp")
        assert "a m" in out

    def test_idempotent(self):
        text = "Mr. Smith and Dr. Watson"
        once = _pass_c_abbreviations(text)
        twice = _pass_c_abbreviations(once)
        assert once == twice


# ---------------------------------------------------------------------------
# Roman numeral converter (Pass D helper)
# ---------------------------------------------------------------------------


class TestRomanToInt:
    @pytest.mark.parametrize("roman,expected", [
        ("I", 1), ("II", 2), ("III", 3), ("IV", 4), ("V", 5),
        ("IX", 9), ("X", 10), ("XIV", 14), ("XL", 40), ("L", 50),
        ("XC", 90), ("C", 100), ("CD", 400), ("D", 500),
        ("CM", 900), ("M", 1000), ("MCMLXXXIV", 1984),
    ])
    def test_valid_romans(self, roman, expected):
        assert _roman_to_int(roman) == expected

    def test_lowercase_accepted(self):
        assert _roman_to_int("xiv") == 14

    @pytest.mark.parametrize("invalid", ["IIII", "VV", "ABC", "", "Q"])
    def test_invalid_returns_none(self, invalid):
        assert _roman_to_int(invalid) is None


# ---------------------------------------------------------------------------
# Pass D — Roman in context
# ---------------------------------------------------------------------------


class TestPassDRomanInContext:
    def test_chapter_iv_to_four(self):
        out = _pass_d_roman_in_context("Chapter IV begins.")
        assert "four" in out

    def test_volume_v_to_five(self):
        out = _pass_d_roman_in_context("Volume V is heavy.")
        assert "five" in out

    def test_book_xii_to_twelve(self):
        out = _pass_d_roman_in_context("Book XII opens.")
        assert "twelve" in out

    def test_louis_xiv_to_regnal(self):
        out = _pass_d_roman_in_context("Louis XIV ruled.")
        assert "the fourteenth" in out

    def test_henry_viii_to_regnal(self):
        out = _pass_d_roman_in_context("Henry VIII founded.")
        assert "the eighth" in out

    def test_pope_john_xxiii_to_regnal(self):
        # "John" is in the regnal context list; XXIII follows John.
        out = _pass_d_roman_in_context("Pope John XXIII convened.")
        assert "the twenty" in out  # twenty-third

    def test_no_context_word_left_alone(self):
        # Bare Roman with no preceding context word should not expand.
        out = _pass_d_roman_in_context("IV")
        assert out == "IV"

    def test_pronoun_i_not_touched(self):
        # Single-letter "I" must never expand (token regex requires len ≥ 2).
        out = _pass_d_roman_in_context("I went home.")
        assert out == "I went home."

    def test_word_mix_not_touched(self):
        # "MIX" is a valid Roman numeral (1009) but also a word — without
        # a context word before it, leave it alone.
        out = _pass_d_roman_in_context("the MIX of styles")
        assert "MIX" in out
        assert "1009" not in out and "thousand" not in out

    def test_lowercase_roman_left_alone(self):
        # Real Roman numerals in book text are uppercase; lowercase
        # tokens are almost always normal words.
        out = _pass_d_roman_in_context("Chapter iv begins.")
        # The token regex is uppercase-only, so "iv" should pass through.
        assert "iv" in out


# ---------------------------------------------------------------------------
# Pass E — ordinal digits
# ---------------------------------------------------------------------------


class TestPassEOrdinalDigits:
    @pytest.mark.parametrize("inp,expected_word", [
        ("1st", "first"), ("2nd", "second"), ("3rd", "third"),
        ("4th", "fourth"), ("21st", "twenty-first"),
        ("22nd", "twenty-second"), ("100th", "hundredth"),
    ])
    def test_ordinals(self, inp, expected_word):
        out = _pass_e_ordinal_digits(inp)
        assert expected_word in out

    def test_case_insensitive_suffix(self):
        out = _pass_e_ordinal_digits("21ST place")
        assert "twenty-first" in out

    def test_no_digits_passes_through(self):
        text = "no ordinals here"
        assert _pass_e_ordinal_digits(text) == text

    def test_empty_string(self):
        assert _pass_e_ordinal_digits("") == ""


# ---------------------------------------------------------------------------
# Pass F — years
# ---------------------------------------------------------------------------


class TestPassFYears:
    def test_year_after_in(self):
        out = _pass_f_years("In 1917 the war ended.")
        assert "nineteen seventeen" in out

    def test_year_after_by(self):
        out = _pass_f_years("by 1492 they sailed.")
        assert "fourteen ninety-two" in out

    def test_year_2004_pair_read(self):
        out = _pass_f_years("around 2004 perhaps")
        assert "two thousand and four" in out or "two thousand four" in out

    def test_year_range_with_hyphen(self):
        out = _pass_f_years("1914-1918 was war.")
        assert "nineteen fourteen" in out
        assert "nineteen eighteen" in out
        assert "to" in out

    def test_year_range_with_en_dash(self):
        out = _pass_f_years("1914\u20131918 was war.")
        assert "to" in out

    def test_decade_with_s(self):
        out = _pass_f_years("the 1920s roared")
        assert "twenties" in out

    def test_apostrophe_decade(self):
        out = _pass_f_years("the '60s vibe")
        assert "sixties" in out

    def test_year_without_preposition_left_for_g(self):
        # Pass F should NOT touch a bare "1917" without a year preposition;
        # Pass G handles it as a cardinal.
        out = _pass_f_years("1917 pages of footnotes")
        assert "nineteen" not in out  # pass F left it alone


# ---------------------------------------------------------------------------
# Pass G — cardinal integers
# ---------------------------------------------------------------------------


class TestPassGCardinal:
    def test_small_integer(self):
        out = _pass_g_cardinal("there were 7 men")
        assert "seven" in out

    def test_large_integer(self):
        out = _pass_g_cardinal("over 1000 soldiers")
        assert "thousand" in out.lower()

    def test_negative_integer(self):
        out = _pass_g_cardinal("temperature was -5 today")
        assert "minus" in out
        assert "five" in out

    def test_thousands_separator(self):
        out = _pass_g_cardinal("a city of 1,234 people")
        assert "one thousand" in out.lower()

    def test_no_decimal_digits_eaten(self):
        # The cardinal regex must NOT match digits that are part of a
        # decimal — Pass H handles those.
        out = _pass_g_cardinal("3.14")
        assert "3.14" == out  # untouched

    def test_no_digits_passes_through(self):
        text = "no numbers"
        assert _pass_g_cardinal(text) == text


# ---------------------------------------------------------------------------
# Pass H — decimals
# ---------------------------------------------------------------------------


class TestPassHDecimals:
    def test_pi(self):
        out = _pass_h_decimals("3.14")
        assert "three point one four" in out

    def test_negative_decimal(self):
        out = _pass_h_decimals("-2.5")
        assert "minus" in out

    def test_zero_point(self):
        out = _pass_h_decimals("0.5")
        assert "zero point five" in out

    def test_no_decimals_passes_through(self):
        text = "no decimals"
        assert _pass_h_decimals(text) == text


# ---------------------------------------------------------------------------
# Pass I — fractions
# ---------------------------------------------------------------------------


class TestPassIFractions:
    def test_one_half(self):
        out = _pass_i_fractions("1/2")
        assert "one half" in out

    def test_three_quarters(self):
        out = _pass_i_fractions("3/4")
        assert "three quarters" in out

    def test_two_thirds(self):
        out = _pass_i_fractions("2/3")
        assert "two thirds" in out

    def test_one_sixteenth(self):
        out = _pass_i_fractions("1/16")
        assert "one sixteenth" in out

    def test_no_fractions_passes_through(self):
        text = "no fractions"
        assert _pass_i_fractions(text) == text

    def test_zero_denominator_left_alone(self):
        out = _pass_i_fractions("1/0")
        assert "1/0" == out


# ---------------------------------------------------------------------------
# Pass J — periods
# ---------------------------------------------------------------------------


class TestPassJPeriods:
    def test_loose_period_collapsed(self):
        assert _pass_j_periods("end .") == "end."

    def test_no_change(self):
        assert _pass_j_periods("end.") == "end."

    def test_empty_string(self):
        assert _pass_j_periods("") == ""


# ---------------------------------------------------------------------------
# Pass K — whitespace
# ---------------------------------------------------------------------------


class TestPassKWhitespace:
    def test_collapse_multiple_spaces(self):
        assert _pass_k_whitespace("a  b   c") == "a b c"

    def test_collapse_tabs_to_space(self):
        assert _pass_k_whitespace("a\t\tb") == "a b"

    def test_collapse_many_newlines(self):
        out = _pass_k_whitespace("a\n\n\n\nb")
        assert out == "a\n\nb"

    def test_strip_leading_trailing(self):
        assert _pass_k_whitespace("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# Public entry point — language guard + integration
# ---------------------------------------------------------------------------


class TestNormalizeEnglishText:
    def test_language_guard_raises_on_finnish(self):
        with pytest.raises(LanguageMismatchError):
            normalize_english_text("hello", _lang="fi")

    def test_language_guard_raises_on_unknown(self):
        with pytest.raises(LanguageMismatchError):
            normalize_english_text("hello", _lang="de")

    def test_language_guard_accepts_explicit_en(self):
        out = normalize_english_text("hello", _lang="en")
        assert out == "hello"

    def test_no_lang_kwarg_for_back_compat(self):
        out = normalize_english_text("hello")
        assert out == "hello"

    def test_empty_string(self):
        assert normalize_english_text("") == ""

    def test_full_pipeline_book_paragraph(self):
        text = (
            "In 1917 King Henry XII reigned over Chapter IV. "
            "Mr. Smith and Dr. Watson, vs. Mrs. Hudson, gathered."
        )
        out = normalize_english_text(text)
        # Year expanded
        assert "nineteen seventeen" in out
        # Regnal Roman expanded
        assert "the twelfth" in out
        # Cardinal-context Roman expanded
        assert "four" in out
        # Abbreviations expanded
        assert "Mister" in out
        assert "Doctor" in out
        assert "versus" in out

    def test_no_finnish_tokens_in_output(self):
        """Sanity: nothing English-normalized should look Finnish."""
        text = "Chapter IV, page 17, in 1492 by King Henry VIII."
        out = normalize_english_text(text)
        # These are Finnish-only tokens that must never appear.
        for finnish in ("neljäs", "viides", "kahdeksas", "vuonna",
                        "tuhat", "luvulla"):
            assert finnish not in out.lower()
