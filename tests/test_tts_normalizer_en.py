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
    _pass_l_currency,
    _pass_m_units,
    _pass_n_time,
    _roman_to_int,
    normalize_english_text,
)


# ---------------------------------------------------------------------------
# Pass A — metadata strip
# ---------------------------------------------------------------------------


class TestPassAMetadataStrip:
    @pytest.mark.parametrize("text,absent", [
        pytest.param("ISBN: 978-0-385-72353-1 reads.", "978", id="isbn_13"),
        pytest.param("ISBN 0-385-72353-X here.", "385", id="isbn_10"),
        pytest.param("See doi:10.1234/example.5678 for details.", "10.1234", id="doi"),
        pytest.param("Source: https://doi.org/10.1234/foo cited.", "doi.org", id="doi_url"),
        pytest.param("Available under CC-BY-SA 4.0 license.", "CC-BY", id="cc_license"),
    ])
    def test_strips(self, text, absent):
        assert absent not in _pass_a_metadata_strip(text)

    def test_copyright_stripped(self):
        out = _pass_a_metadata_strip(
            "© 2003 Tom Holland. The book begins."
        )
        assert "2003" not in out
        assert "begins" in out

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
    @pytest.mark.parametrize("text,expected", [
        pytest.param("\u2018hello\u2019", "'hello'", id="smart_single_quotes"),
        pytest.param("\u201chello\u201d", '"hello"', id="smart_double_quotes"),
        pytest.param("1914\u20131918", "1914-1918", id="en_dash"),
        pytest.param("hello\u00a0world", "hello world", id="nbsp"),
    ])
    def test_exact_replacements(self, text, expected):
        assert _pass_b_whitespace_quotes(text) == expected

    def test_em_dash_to_spaced_hyphen(self):
        out = _pass_b_whitespace_quotes("then\u2014now")
        assert "-" in out
        assert "\u2014" not in out

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
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("Mr. Smith arrived.", "Mister", id="mr"),
        pytest.param("Mrs. Smith arrived.", "Misses", id="mrs"),
        pytest.param("Dr. Watson observed.", "Doctor", id="dr"),
        pytest.param("Prof. Plum did it.", "Professor", id="prof"),
        pytest.param("namely, i.e. clearly", "that is", id="ie"),
        pytest.param("such as e.g. cats", "for example", id="eg"),
        pytest.param("dogs, cats, etc.", "et cetera", id="etc"),
        pytest.param("Caesar vs. Pompey", "versus", id="vs"),
        pytest.param("U.S. policy", "United States", id="us"),
        pytest.param("the U.K. government", "United Kingdom", id="uk"),
        pytest.param("No. 7", "Number", id="no_number"),
        pytest.param("see pp. 23-25", "pages", id="pages"),
        pytest.param("St. Peter", "Saint", id="st_saint"),
        pytest.param("Main St.", "Street", id="st_street"),
        pytest.param("at 3 a.m. sharp", "a m", id="am_lowercase"),
    ])
    def test_expansion(self, text, expected_substr):
        assert expected_substr in _pass_c_abbreviations(text)

    def test_vol_chapter(self):
        out = _pass_c_abbreviations("Vol. III, Ch. 4")
        assert "Volume" in out and "Chapter" in out

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
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("Chapter IV begins.", "four", id="chapter_iv"),
        pytest.param("Volume V is heavy.", "five", id="volume_v"),
        pytest.param("Book XII opens.", "twelve", id="book_xii"),
        pytest.param("Louis XIV ruled.", "the fourteenth", id="louis_xiv_regnal"),
        pytest.param("Henry VIII founded.", "the eighth", id="henry_viii_regnal"),
        pytest.param("Pope John XXIII convened.", "the twenty", id="pope_john_xxiii"),
    ])
    def test_expansion(self, text, expected_substr):
        assert expected_substr in _pass_d_roman_in_context(text)

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
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("In 1917 the war ended.", "nineteen seventeen", id="after_in"),
        pytest.param("by 1492 they sailed.", "fourteen ninety-two", id="after_by"),
        pytest.param("the 1920s roared", "twenties", id="decade_s"),
        pytest.param("the '60s vibe", "sixties", id="apostrophe_decade"),
    ])
    def test_year_expansion(self, text, expected_substr):
        assert expected_substr in _pass_f_years(text)

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

    def test_year_without_preposition_left_for_g(self):
        # Pass F should NOT touch a bare "1917" without a year preposition;
        # Pass G handles it as a cardinal.
        out = _pass_f_years("1917 pages of footnotes")
        assert "nineteen" not in out  # pass F left it alone


# ---------------------------------------------------------------------------
# Pass L — currency
# ---------------------------------------------------------------------------


class TestPassLCurrency:
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("$5 today", "five dollars", id="dollar_simple"),
        pytest.param("$1 only", "one dollar", id="dollar_singular"),
        pytest.param("¥500", "yen", id="yen_no_decimal"),
        pytest.param("paid 10 USD", "ten dollars", id="iso_usd"),
    ])
    def test_single_substr(self, text, expected_substr):
        assert expected_substr in _pass_l_currency(text)

    def test_dollar_with_cents(self):
        out = _pass_l_currency("$5.99")
        assert "five dollars" in out
        assert "ninety-nine cents" in out

    def test_dollar_with_thousands_separator(self):
        out = _pass_l_currency("$1,234.56")
        assert "thousand" in out.lower()
        assert "cents" in out

    def test_dollar_magnitude_million(self):
        out = _pass_l_currency("$1.5M")
        assert "million" in out
        assert "dollars" in out

    def test_pound_with_pence(self):
        out = _pass_l_currency("£3.50")
        assert "pounds" in out
        assert "pence" in out

    def test_euro_with_cents(self):
        out = _pass_l_currency("€2.50")
        assert "euros" in out
        assert "cents" in out

    def test_iso_code_with_magnitude(self):
        out = _pass_l_currency("worth 2.5M EUR")
        assert "million" in out
        assert "euros" in out

    def test_no_currency_passes_through(self):
        text = "no money mentioned"
        assert _pass_l_currency(text) == text

    def test_idempotent(self):
        text = "$5.99 each"
        once = _pass_l_currency(text)
        twice = _pass_l_currency(once)
        assert once == twice

    def test_zero_cents_omits_and_clause(self):
        # $5.00 should read "five dollars" not "five dollars and zero cents".
        out = _pass_l_currency("$5.00")
        assert "and" not in out
        assert "five dollars" in out


# ---------------------------------------------------------------------------
# Pass M — units
# ---------------------------------------------------------------------------


class TestPassMUnits:
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("5 km away", "kilometers", id="distance_km"),
        pytest.param("1 mi only", "one mile", id="distance_singular"),
        pytest.param("55 mph limit", "miles per hour", id="speed_mph"),
        pytest.param("100 kph zone", "kilometers per hour", id="speed_kph"),
        pytest.param("15 kg total", "kilograms", id="mass_kg"),
        pytest.param("180 lbs gross", "pounds", id="mass_lbs"),
        pytest.param("273°K cold", "kelvin", id="temp_kelvin"),
        pytest.param("8 GB RAM", "gigabytes", id="data_gb"),
        pytest.param("256 mb file", "megabytes", id="data_mb_lower"),
        pytest.param("3 GHz processor", "gigahertz", id="freq_ghz"),
        pytest.param("2 l of water", "liters", id="volume_liter"),
        pytest.param("1.5 km away", "kilometers", id="decimal_amount"),
    ])
    def test_unit_expansion(self, text, expected_substr):
        assert expected_substr in _pass_m_units(text)

    def test_temperature_fahrenheit(self):
        out = _pass_m_units("32 °F freezing")
        assert "degrees Fahrenheit" in out

    def test_temperature_celsius_no_space(self):
        out = _pass_m_units("100°C boiling")
        assert "degrees Celsius" in out

    def test_no_units_passes_through(self):
        text = "no units here"
        assert _pass_m_units(text) == text

    def test_idempotent(self):
        text = "5 km drive"
        once = _pass_m_units(text)
        twice = _pass_m_units(once)
        assert once == twice


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
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("1/2", "one half", id="one_half"),
        pytest.param("3/4", "three quarters", id="three_quarters"),
        pytest.param("2/3", "two thirds", id="two_thirds"),
        pytest.param("1/16", "one sixteenth", id="one_sixteenth"),
    ])
    def test_fraction_expansion(self, text, expected_substr):
        assert expected_substr in _pass_i_fractions(text)

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

class TestPassNTime:
    @pytest.mark.parametrize("text,expected", [
        pytest.param("1:00", "one o'clock", id="1_oclock"),
        pytest.param("12:00", "twelve o'clock", id="12_oclock_noon"),
        pytest.param("3:30", "three thirty", id="3_30"),
        pytest.param("3:45", "three forty-five", id="3_45"),
        pytest.param("9:05", "nine oh five", id="9_05"),
        pytest.param("11:59", "eleven fifty-nine", id="11_59"),
        pytest.param("13:00", "thirteen hundred hours", id="13_00_military"),
        pytest.param("15:00", "fifteen hundred hours", id="15_00_military"),
        pytest.param("23:59", "twenty-three fifty-nine", id="23_59"),
        pytest.param("18:30", "eighteen thirty", id="18_30"),
        pytest.param("0:00", "zero hundred hours", id="0_00_midnight"),
        pytest.param("0:15", "zero fifteen", id="0_15"),
    ])
    def test_times(self, text, expected):
        assert _pass_n_time(text) == expected

    # With a.m./p.m. suffix — full pipeline (Pass C expands a.m./p.m.)
    def test_pm_suffix_full_pipeline(self):
        out = normalize_english_text("Meet me at 3:30 p.m.")
        assert "three thirty" in out
        assert "p m" in out

    def test_am_suffix_full_pipeline(self):
        out = normalize_english_text("The alarm rang at 6:45 a.m.")
        assert "six forty-five" in out
        assert "a m" in out

    # Idempotence — the regex won't re-fire on letters.
    def test_idempotent(self):
        once = _pass_n_time("3:30")
        twice = _pass_n_time(once)
        assert once == twice

    # Empty / passthrough
    def test_empty_string(self):
        assert _pass_n_time("") == ""

    def test_non_time_text(self):
        assert _pass_n_time("no times in this sentence") == (
            "no times in this sentence"
        )

    @pytest.mark.parametrize("text", ["25:99", "10:75"])
    def test_invalid_time_left_alone(self, text):
        assert _pass_n_time(text) == text

    # Multiple times in one sentence
    def test_multiple_times_in_sentence(self):
        out = _pass_n_time("Open 9:00 until 17:30.")
        assert "nine o'clock" in out
        assert "seventeen thirty" in out

    # Inline within prose
    def test_time_in_sentence(self):
        out = _pass_n_time("The train leaves at 7:15 sharp.")
        assert "seven fifteen" in out


    def test_no_finnish_tokens_in_output(self):
        """Sanity: nothing English-normalized should look Finnish."""
        text = "Chapter IV, page 17, in 1492 by King Henry VIII."
        out = normalize_english_text(text)
        # These are Finnish-only tokens that must never appear.
        for finnish in ("neljäs", "viides", "kahdeksas", "vuonna",
                        "tuhat", "luvulla"):
            assert finnish not in out.lower()
