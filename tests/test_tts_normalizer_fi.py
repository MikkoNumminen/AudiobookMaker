"""Unit tests for the Finnish normalizer (src.tts_normalizer_fi).

These tests were split out of tests/test_tts_engine.py after the
normalizer moved into its own module in commit 54dc619. The tests
exercise normalize_finnish_text and its helpers (_expand_acronyms,
_roman_to_int).
"""

from __future__ import annotations

import pytest

from src.tts_normalizer_fi import (
    _expand_acronyms,
    _roman_to_int,
    normalize_finnish_text,
)


# ---------------------------------------------------------------------------
# normalize_finnish_text
# ---------------------------------------------------------------------------


class TestNormalizeFinnishText:
    def test_normalize_finnish_text_expands_year_numbers(self) -> None:
        result = normalize_finnish_text("vuonna 1500")
        assert not any(ch.isdigit() for ch in result)
        assert "vuonna" in result

    def test_normalize_finnish_text_handles_century_expressions(self) -> None:
        result = normalize_finnish_text("1500-luvulla")
        assert "luvulla" in result
        assert not any(ch.isdigit() for ch in result)

    def test_normalize_finnish_text_english_passes_through(self) -> None:
        # Finnish-only normalizer: plain English without digits is unchanged.
        text = "This is an English sentence."
        assert normalize_finnish_text(text) == text

    def test_normalize_finnish_text_empty_string(self) -> None:
        assert normalize_finnish_text("") == ""

    def test_normalize_finnish_text_drops_citations(self) -> None:
        result = normalize_finnish_text(
            "Tämä on väite (Pihlajamäki 2005).", drop_citations=True
        )
        assert "Pihlajamäki" not in result
        assert "2005" not in result

    def test_normalize_finnish_text_keeps_citations_when_disabled(self) -> None:
        result = normalize_finnish_text(
            "Tämä on väite (Pihlajamäki 2005).", drop_citations=False
        )
        assert "Pihlajamäki" in result

    def test_compound_numbers_get_spaces(self) -> None:
        # num2words 0.5.14 glues Finnish compound-number morphemes
        # together (e.g. 1889 -> "kahdeksansataakahdeksankymmentäyhdeksän").
        # Post-processor must insert spaces at morpheme boundaries so
        # Chatterbox-TTS tokenizes and pronounces them correctly.
        out = normalize_finnish_text("1889")
        assert " " in out
        assert "kahdeksansataa kahdeksankymmentä" in out
        assert "kahdeksankymmentä yhdeksän" in out
        assert "kahdeksansataakahdeksankymmentäyhdeksän" not in out

        # Two-digit compound: 42 -> "neljäkymmentä kaksi".
        assert normalize_finnish_text("42") == "neljäkymmentä kaksi"

        # Hundreds + teens: 1917 -> spaces between sataa and seitsemäntoista.
        out_1917 = normalize_finnish_text("1917")
        assert "yhdeksänsataa seitsemäntoista" in out_1917

        # Standalone teens must NOT be split — they are single word units.
        assert normalize_finnish_text("15") == "viisitoista"
        assert normalize_finnish_text("11") == "yksitoista"

        # Clean hundreds (no trailing morpheme) must not get a spurious space.
        assert normalize_finnish_text("1500") == "tuhat viisisataa"


# ---------------------------------------------------------------------------
# Governor-word case detection (Pass G)
# ---------------------------------------------------------------------------


class TestFinnishGovernorCases:
    """Numerals must inflect to agree with their governor word.

    The reference for every expected form is
    ``docs/finnish_governor_cases.md`` (VISK §772 + Kielikello).
    """

    @pytest.mark.parametrize("text,expected_substrs", [
        pytest.param(
            "sivulta 42", ("neljältäkymmeneltä", "kahdelta"), id="sivulta_ablative",
        ),
        pytest.param(
            "sivulla 42", ("neljälläkymmenellä", "kahdella"), id="sivulla_adessive",
        ),
        pytest.param(
            "sivulle 42", ("neljällekymmenelle", "kahdelle"), id="sivulle_allative",
        ),
        pytest.param("luvussa 3", ("kolmessa",), id="luvussa_inessive"),
        pytest.param("kappaleessa 7", ("seitsemässä",), id="kappaleessa_inessive"),
        pytest.param("kohdassa 4", ("neljässä",), id="kohdassa_inessive"),
        pytest.param("rivillä 12", ("kahdellatoista",), id="rivilla_adessive"),
    ])
    def test_governor_inflection(self, text, expected_substrs):
        out = normalize_finnish_text(text)
        for sub in expected_substrs:
            assert sub in out, f"{sub!r} missing from {out!r}"

    def test_sivulta_clears_digits(self) -> None:
        # Regression — verify digits actually get rewritten.
        out = normalize_finnish_text("sivulta 42")
        assert "42" not in out

    def test_klo_stays_nominative(self) -> None:
        # "klo 14" — kello is a frozen adverbial, hour stays nominative.
        # Expected reading: "kello neljätoista" (clock fourteen).
        out = normalize_finnish_text("klo 14")
        assert "neljätoista" in out
        assert "neljässätoista" not in out

    def test_kello_stays_nominative(self) -> None:
        out = normalize_finnish_text("kello 8")
        assert "kahdeksan" in out
        assert "kahdeksalla" not in out

    def test_viisi_kertaa_numeral_stays_nominative(self) -> None:
        # VISK §772: "X kertaa" → number in nominative, kertaa keeps
        # its (frozen) partitive.
        out = normalize_finnish_text("5 kertaa")
        assert out == "viisi kertaa"

    def test_kolme_prosenttia_numeral_stays_nominative(self) -> None:
        out = normalize_finnish_text("3 prosenttia")
        assert out == "kolme prosenttia"

    def test_vuotta_numeral_stays_nominative(self) -> None:
        out = normalize_finnish_text("10 vuotta")
        assert "kymmenen vuotta" in out

    def test_bare_integer_with_no_governor_is_nominative(self) -> None:
        # Regression: unchanged behaviour for unmarked bare ints.
        assert normalize_finnish_text("42") == "neljäkymmentä kaksi"
        assert normalize_finnish_text("15") == "viisitoista"

    def test_governor_match_is_case_insensitive(self) -> None:
        # "Sivulta 42" at sentence start must still detect the governor.
        out = normalize_finnish_text("Sivulta 42")
        assert "neljältäkymmeneltä" in out

    def test_governor_scan_window_is_three_words(self) -> None:
        # Governor exactly 3 word tokens before the number — must hit.
        out = normalize_finnish_text("sivulta tämän erittäin 42")
        assert "neljältäkymmeneltä" in out

    def test_governor_beyond_three_words_is_ignored(self) -> None:
        # Governor 4 words before the number — must NOT hit; fall
        # back to nominative.
        out = normalize_finnish_text("sivulta yksi kaksi kolme neljä 42")
        assert "neljäkymmentä kaksi" in out
        assert "neljältäkymmeneltä" not in out


# ---------------------------------------------------------------------------
# year_shortening flag (Kielikello radio convention)
# ---------------------------------------------------------------------------


class TestYearShortening:
    """The ``year_shortening`` kwarg chooses between radio and full case."""

    def test_radio_default_keeps_year_nominative(self) -> None:
        # vuodesta 1917 → "vuodesta tuhat yhdeksänsataa seitsemäntoista"
        out = normalize_finnish_text("vuodesta 1917 alkaen")
        assert "tuhat" in out
        assert "yhdeksänsataa" in out
        assert "seitsemäntoista" in out
        # Must NOT contain the elative form of the year.
        assert "tuhannesta" not in out

    def test_full_mode_emits_elative(self) -> None:
        out = normalize_finnish_text(
            "vuodesta 1917 alkaen", year_shortening="full"
        )
        assert "tuhannesta" in out
        assert "seitsemästätoista" in out

    def test_full_mode_emits_illative_for_vuoteen(self) -> None:
        out = normalize_finnish_text(
            "vuoteen 1900 mennessä", year_shortening="full"
        )
        assert "tuhanteen" in out

    def test_radio_mode_ignores_vuoteen(self) -> None:
        out = normalize_finnish_text("vuoteen 1900 mennessä")
        assert "tuhanteen" not in out
        assert "tuhat" in out

    def test_short_integer_with_year_governor_is_not_a_year(self) -> None:
        # "vuoden 5" — 5 is below the 1000 year threshold, so the
        # year-governor override does not apply. Since "vuoden" is
        # nominative in the governor table anyway, the numeral stays
        # nominative.
        out = normalize_finnish_text("vuoden 5 jälkeen")
        assert "viisi" in out


# ---------------------------------------------------------------------------
# Page abbreviation expansion (Pass E) leaves digits for Pass G
# ---------------------------------------------------------------------------


class TestPageAbbreviation:
    def test_s_abbrev_expands_to_sivu_and_inflects_via_governor(self) -> None:
        # "s. 42" → Pass E emits "sivu 42", then Pass G sees "sivu"
        # as a nominative governor and expands 42 in nominative.
        out = normalize_finnish_text("s. 42")
        assert "sivu" in out
        assert "neljäkymmentä kaksi" in out
        assert "s." not in out

    def test_ss_abbrev_expands_to_sivut(self) -> None:
        out = normalize_finnish_text("ss. 42")
        assert "sivut" in out


# ---------------------------------------------------------------------------
# Pass K — Finnish abbreviation expansion
# ---------------------------------------------------------------------------


class TestAbbreviationExpansion:
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("Esim. tämä", "esimerkiksi", id="esim"),
        pytest.param("ja niin edelleen jne.", "ja niin edelleen", id="jne"),
        pytest.param("koirat, kissat yms.", "ynnä muuta sellaista", id="yms"),
        pytest.param("vuonna 500 eaa.", "ennen ajanlaskun alkua", id="eaa"),
        pytest.param("syntyi 100 eKr.", "ennen Kristusta", id="ekr"),
        pytest.param("prof. Mäkinen puhui", "professori", id="prof"),
        pytest.param("dos. Korhonen kirjoitti", "dosentti", id="dos"),
    ])
    def test_expansion(self, text, expected_substr):
        assert expected_substr in normalize_finnish_text(text)

    def test_mm_with_period_is_muun_muassa(self) -> None:
        result = normalize_finnish_text("Tämä on mm. hyvä.")
        assert "muun muassa" in result
        assert "millimetriä" not in result

    def test_esim_removes_abbrev_form(self) -> None:
        result = normalize_finnish_text("Esim. tämä")
        assert "esim." not in result.lower()

    def test_tri_before_capital_name(self) -> None:
        result = normalize_finnish_text("tri Virtanen tuli")
        assert "tohtori Virtanen" in result
        assert "tri Virtanen" not in result

    def test_tri_not_expanded_without_capital_name(self) -> None:
        # `tri` alone (followed by lowercase) must NOT be expanded
        result = normalize_finnish_text("tri on lyhenne")
        assert "tri" in result
        assert "tohtori" not in result

    def test_case_insensitive_match(self) -> None:
        for variant in ("Ts. tämä on", "TS. tämä on", "ts. tämä on"):
            result = normalize_finnish_text(variant)
            assert "toisin sanoen" in result, f"Failed for: {variant!r}"

    def test_prof_removes_abbrev_form(self) -> None:
        result = normalize_finnish_text("prof. Mäkinen puhui")
        assert "prof." not in result

    def test_dos_removes_abbrev_form(self) -> None:
        result = normalize_finnish_text("dos. Korhonen kirjoitti")
        assert "dos." not in result

    def test_abbrev_does_not_eat_random_word_ending_in_same_letters(self) -> None:
        # "nero" should not be treated as if it starts with "ns." or "nk."
        result = normalize_finnish_text("nero on lahjakas")
        assert "nero" in result


# ---------------------------------------------------------------------------
# Pass M — measurement unit / currency symbol expansion
# ---------------------------------------------------------------------------


class TestUnitSymbolExpansion:
    @pytest.mark.parametrize("text,expected_substr", [
        pytest.param("5 %", "viisi prosenttia", id="percent_with_space"),
        pytest.param("5%", "viisi prosenttia", id="percent_without_space"),
        pytest.param("3 \u2030", "kolme promillea", id="per_mille"),
        pytest.param("20 \u20ac", "kaksikymmentä euroa", id="euros"),
        pytest.param("$5", "viisi dollaria", id="dollars_prefix"),
        pytest.param("3 km", "kolme kilometriä", id="kilometers"),
        pytest.param("15 cm", "viisitoista senttimetriä", id="centimeters"),
        pytest.param("2 kg", "kaksi kilogrammaa", id="kilograms"),
        pytest.param("20 \u00b0C", "kaksikymmentä celsiusastetta", id="celsius_positive"),
        pytest.param("5 min", "viisi minuuttia", id="minutes"),
    ])
    def test_unit_expansion(self, text, expected_substr):
        assert expected_substr in normalize_finnish_text(text)

    def test_millimeters_unit_not_abbrev(self) -> None:
        # `5 mm` (digit + mm, no period) must expand as unit, not abbreviation
        result = normalize_finnish_text("5 mm")
        assert "viisi millimetriä" in result
        assert "muun muassa" not in result

    def test_temperature_celsius_negative(self) -> None:
        # Negative temperature: -5 °C — the number part is negative
        result = normalize_finnish_text("-5 \u00b0C")
        assert "celsiusastetta" in result

    def test_unit_does_not_match_without_digit_prefix(self) -> None:
        result = normalize_finnish_text("kilometrin matka")
        assert "kilometrin" in result
        # Should not be expanded (no digit prefix)
        assert "kilometriä" not in result

    def test_mm_alone_without_digit_is_not_a_unit(self) -> None:
        # `mm.` with a period is the abbreviation "muun muassa", not a unit
        result = normalize_finnish_text("mm. on hyvä")
        assert "muun muassa" in result
        assert "millimetriä" not in result

    def test_cm_before_km_disambiguation(self) -> None:
        result = normalize_finnish_text("5 cm ja 10 km")
        assert "viisi senttimetriä" in result
        assert "kymmenen kilometriä" in result


# ---------------------------------------------------------------------------
# Pass D (range polish) — per-endpoint governor inflection via Pass G
# ---------------------------------------------------------------------------


class TestRangePolish:
    def test_year_range_radio_default_both_nominative(self) -> None:
        # Default radio mode: year governor overrides case to nominative
        result = normalize_finnish_text("vuosina 1914-1918")
        # Both endpoints contain tuhat yhdeksänsataa (nominative prefix)
        assert result.count("tuhat yhdeksänsataa") >= 2
        # Essive form must NOT appear
        assert "tuhantena" not in result

    def test_year_range_full_mode_both_inflect(self) -> None:
        result = normalize_finnish_text(
            "vuosina 1914-1918", year_shortening="full"
        )
        # Essive form must appear for both endpoints
        assert result.count("tuhantena") >= 2

    def test_range_with_vuodesta_governor_full_mode(self) -> None:
        result = normalize_finnish_text(
            "vuodesta 1917-1920", year_shortening="full"
        )
        # Both endpoints should have the elative form
        assert result.count("tuhannesta") >= 2

    def test_range_with_no_governor_falls_back_to_nominative(self) -> None:
        result = normalize_finnish_text("1500-1800")
        # Both endpoints in nominative (no governor)
        assert "tuhat viisisataa" in result
        assert "tuhat kahdeksansataa" in result


# ---------------------------------------------------------------------------
# Pass L — Roman numeral expansion
# ---------------------------------------------------------------------------


class TestRomanNumeralExpansion:
    # -- Regnal ordinals -------------------------------------------------------

    def test_kustaa_ii_aadolf(self) -> None:
        result = normalize_finnish_text("Kustaa II Aadolf oli kuningas")
        assert "Kustaa toinen Aadolf oli kuningas" == result

    @pytest.mark.parametrize("text,expected_substr,roman", [
        pytest.param("paavi Pius IX", "yhdeksäs", "IX", id="pius_ix"),
        pytest.param("Leo XIII", "kolmastoista", "XIII", id="leo_xiii"),
        pytest.param("Katariina II", "toinen", "II", id="katariina_ii"),
        pytest.param("Henrik VIII", "kahdeksas", "VIII", id="henrik_viii"),
        pytest.param("kuningas Juhana III", "kolmas", "III", id="juhana_iii"),
        pytest.param("tsaari Nikolai II", "toinen", "II", id="nikolai_ii"),
        pytest.param("luku IV käsittelee", "neljäs", "IV", id="luku_iv"),
        pytest.param("XIX vuosisata", "yhdeksästoista", "XIX", id="xix_vuosisata"),
        pytest.param("XX luvulla", "kahdeskymmenes", "XX", id="xx_luvulla"),
        pytest.param("pykälä XII", "kahdestoista", "XII", id="pykala_xii"),
    ])
    def test_regnal_and_chapter_expansion(self, text, expected_substr, roman):
        result = normalize_finnish_text(text)
        assert expected_substr in result
        assert roman not in result

    # -- Cardinal fallback -----------------------------------------------------

    def test_ii_alone_no_context(self) -> None:
        result = normalize_finnish_text("II oli aikakausi")
        assert "kaksi" in result
        assert "II" not in result

    def test_iv_with_unknown_preceding_word(self) -> None:
        result = normalize_finnish_text("Talo IV")
        # cardinal fallback — no regnal/title/section context
        assert "neljä" in result
        assert "IV" not in result

    def test_mcm_year(self) -> None:
        # MCM = 1900, no context → cardinal
        result = normalize_finnish_text("MCM")
        assert "tuhat yhdeksänsataa" in result
        assert "MCM" not in result

    # -- Blacklist checks ------------------------------------------------------

    @pytest.mark.parametrize("text,token", [
        pytest.param("DC power", "DC", id="dc"),
        pytest.param("lähetä CV", "CV", id="cv"),
        pytest.param("tämän kauden MVP", "MVP", id="mvp"),
        pytest.param("LCD näyttö", "LCD", id="lcd"),
    ])
    def test_blacklisted_not_expanded(self, text, token):
        result = normalize_finnish_text(text)
        assert token in result

    # -- Single-letter guard ---------------------------------------------------

    def test_standalone_i_not_expanded(self) -> None:
        result = normalize_finnish_text("I said no")
        # Single I must not be expanded (regex requires 2+ chars)
        assert "I" in result

    def test_standalone_v_not_expanded(self) -> None:
        # Single V should not be touched
        result = normalize_finnish_text("Olen paikalla. V kertaa.")
        assert "V " in result or "V." in result

    def test_standalone_x_not_expanded(self) -> None:
        result = normalize_finnish_text("X marks the spot")
        assert "X" in result

    # -- Edge cases ------------------------------------------------------------

    def test_invalid_roman_stays_unchanged(self) -> None:
        # IIII is non-canonical (canonical is IV); _roman_to_int returns None.
        # The regex matches IIII but the round-trip canonicity check rejects it,
        # so the token is left unchanged.
        assert _roman_to_int("IIII") is None
        result = normalize_finnish_text("IIII")
        assert "IIII" in result

    def test_roman_at_sentence_start(self) -> None:
        result = normalize_finnish_text("IX vuosisadalla")
        assert "yhdeksäs" in result
        assert "IX" not in result

    def test_multiple_romans_in_one_sentence(self) -> None:
        result = normalize_finnish_text("Kustaa II ja Juhana III")
        assert "toinen" in result
        assert "kolmas" in result
        assert "II" not in result
        assert "III" not in result

    # -- _roman_to_int unit tests ----------------------------------------------

    @pytest.mark.parametrize("roman,expected", [
        ("IV", 4), ("IX", 9), ("XIV", 14), ("MCM", 1900),
    ])
    def test_roman_to_int_basic_values(self, roman, expected):
        assert _roman_to_int(roman) == expected

    @pytest.mark.parametrize("invalid", ["IIII", "VV", ""])
    def test_roman_to_int_invalid_returns_none(self, invalid):
        assert _roman_to_int(invalid) is None


# ---------------------------------------------------------------------------
# Pass A extension — metadata paren drop
# ---------------------------------------------------------------------------


class TestMetadataParenDrop:
    @pytest.mark.parametrize("text,absent", [
        pytest.param("(ISBN 978-951-123-456-7)", "ISBN", id="isbn_label"),
        pytest.param("(ISBN 978-951-123-456-7)", "978", id="isbn_digits"),
        pytest.param("(DOI 10.1234/abcd)", "DOI", id="doi_label"),
        pytest.param("(DOI 10.1234/abcd)", "10.1234", id="doi_digits"),
        pytest.param(
            "(Creative Commons Nimeä 4.0 Kansainvälinen)",
            "Creative Commons",
            id="creative_commons",
        ),
        pytest.param("(CC BY 4.0)", "CC BY", id="cc_by"),
    ])
    def test_paren_dropped(self, text, absent):
        result = normalize_finnish_text(text, drop_citations=True)
        assert absent not in result

    def test_nonmetadata_paren_untouched(self) -> None:
        result = normalize_finnish_text("(tämä on huomautus)", drop_citations=True)
        assert "tämä on huomautus" in result

    def test_metadata_paren_kept_when_drop_citations_false(self) -> None:
        result = normalize_finnish_text("(DOI 10.1234/abcd)", drop_citations=False)
        assert "DOI" in result


# ---------------------------------------------------------------------------
# Pass J1 — ellipsis collapse
# ---------------------------------------------------------------------------


class TestEllipsisCollapse:
    def test_three_dots_surrounded_by_space(self) -> None:
        result = normalize_finnish_text("Hmm ... hän sanoi")
        assert "…" in result
        assert "..." not in result

    def test_four_dots_surrounded_by_space(self) -> None:
        result = normalize_finnish_text("Odottakaa .... valmis")
        assert "…" in result
        assert "...." not in result

    def test_decimal_not_collapsed(self) -> None:
        # Decimals like 1.5 should not be affected — Pass F handles those
        result = normalize_finnish_text("1.5 prosenttia")
        assert "…" not in result

    def test_url_dots_not_collapsed(self) -> None:
        # Dots inside a word (no surrounding whitespace) must not collapse
        result = normalize_finnish_text("example.com on osoite")
        assert "…" not in result


# ---------------------------------------------------------------------------
# Pass J2 — TOC dot-leader drop
# ---------------------------------------------------------------------------


class TestTocDotLeaderDrop:
    def test_toc_line_dropped(self) -> None:
        result = normalize_finnish_text("RAJAT..............42")
        assert "RAJAT" in result
        assert "42" not in result
        # No long dot run left
        assert "......." not in result

    def test_lowercase_toc(self) -> None:
        result = normalize_finnish_text("Johdanto.........1")
        assert "Johdanto" in result
        assert "........." not in result

    def test_toc_with_leading_spaces(self) -> None:
        result = normalize_finnish_text("   Luku 1 .............. 5")
        assert "Luku" in result
        assert ".............." not in result

    def test_real_ellipsis_preserved(self) -> None:
        # Only 3 dots with no digit after — Pass J2 must not touch this;
        # Pass J1 converts it to Unicode ellipsis
        result = normalize_finnish_text("Hmm... valmis")
        # Pass J1 did not fire (no surrounding whitespace), so the dots survive
        # as-is OR Pass J1 fires — either way no Pass J2 damage
        assert "valmis" in result

    def test_five_dots_followed_by_digit(self) -> None:
        result = normalize_finnish_text("RAJAT..... 42")
        assert "RAJAT" in result
        assert "....." not in result


# ---------------------------------------------------------------------------
# Pass J3 — ISBN strip
# ---------------------------------------------------------------------------


class TestIsbnStrip:
    def test_isbn_13_with_hyphens(self) -> None:
        result = normalize_finnish_text("Kirja ISBN 978-951-123-456-7 on hyvä")
        assert "ISBN" not in result
        assert "978" not in result
        assert "Kirja" in result
        assert "hyvä" in result

    def test_isbn_13_without_prefix(self) -> None:
        result = normalize_finnish_text("9789511234567 on kirja")
        assert "9789511234567" not in result
        assert "kirja" in result

    def test_isbn_13_with_spaces(self) -> None:
        result = normalize_finnish_text("ISBN 978 951 123 456 7")
        assert "ISBN" not in result
        assert "978" not in result

    def test_isbn_in_sentence(self) -> None:
        # The metadata-paren drop handles parens; ISBN strip handles bare numbers
        result = normalize_finnish_text("(ISBN: 978-951-123-456-7)", drop_citations=True)
        assert "978" not in result

    def test_non_isbn_digits_preserved(self) -> None:
        result = normalize_finnish_text("Vuonna 1918 tapahtui")
        assert "1918" not in result  # Pass G expands it to words
        # But the year word form should be present
        assert "yhdeksäntoista" in result or "tuhat" in result


# ---------------------------------------------------------------------------
# TestAcronymExpansion
# ---------------------------------------------------------------------------


class TestAcronymExpansion:
    """Tests for _expand_acronyms (Pass N) and its integration via normalize_finnish_text."""

    # --- Positive expansion ---

    @pytest.mark.parametrize("text,expected", [
        pytest.param("EU on liitto", "Euroopan unioni on liitto", id="eu"),
        pytest.param("YK päätti", "Yhdistyneet kansakunnat päätti", id="yk"),
        pytest.param("USA oli", "Yhdysvallat oli", id="usa"),
        # NATO reads as a word, not letter-by-letter
        pytest.param("NATO jäsenyys", "Nato jäsenyys", id="nato_word"),
        pytest.param("ALR sääti", "A L R sääti", id="alr_letter_by_letter"),
        # Hyphen is a non-word char so \b fires between ABGB and -.
        pytest.param("ABGB-laki", "A B G B-laki", id="abgb_with_hyphen"),
        pytest.param("BGB § 242", "B G B § 242", id="bgb_with_section"),
        pytest.param("HGB", "H G B", id="hgb"),
        pytest.param(
            "EU ja YK",
            "Euroopan unioni ja Yhdistyneet kansakunnat",
            id="multiple_acronyms",
        ),
        pytest.param("EU päätti.", "Euroopan unioni päätti.", id="sentence_start"),
        pytest.param(
            "jäsenyys EU.", "jäsenyys Euroopan unioni.", id="sentence_end",
        ),
        # Finnish inflection uses colon: `EU:n`. The colon is a non-word char
        # so \b fires between `EU` and `:` — EU IS matched and expanded.
        pytest.param("EU:n", "Euroopan unioni:n", id="eu_colon_suffix"),
        pytest.param("YK:n", "Yhdistyneet kansakunnat:n", id="yk_colon_suffix"),
        # Ensures `ABGB` is NOT partially replaced as `A` + `BGB` expansion.
        pytest.param("ABGB ja BGB", "A B G B ja B G B", id="longest_first_abgb_bgb"),
    ])
    def test_acronym_expansion(self, text, expected):
        assert _expand_acronyms(text) == expected

    # --- Negative (don't expand) ---

    @pytest.mark.parametrize("original", [
        # `eu` is a Finnish negative prefix and must NOT be expanded
        pytest.param("eu on suomen kielessä tavu", id="lowercase_eu"),
        pytest.param("XYZ on akronyymi", id="unknown_acronym"),
        # `NATOn` is one word token — no \b inside it; NATO is NOT matched.
        # This is by design: we only expand exact standalone tokens.
        pytest.param("NATOn jäsenyys", id="partial_match_naton"),
    ])
    def test_untouched(self, original):
        assert _expand_acronyms(original) == original

    # --- Integration: normalize_finnish_text passes through Pass N ---

    def test_eu_expanded_via_normalize(self) -> None:
        result = normalize_finnish_text("EU on liitto")
        assert "Euroopan unioni" in result


# ---------------------------------------------------------------------------
# Session 4 polish — § expansion, Pass H morpheme ordering, Pass D short ranges
# ---------------------------------------------------------------------------


class TestSectionSignExpansion:
    """Pass M now recognizes `§` as a prefix symbol that expands to
    `pykälä`. Subsequent digits are inflected by Pass G via the
    `pykälä` before-governor (nominative default)."""

    def test_section_sign_with_space(self) -> None:
        result = normalize_finnish_text("§ 5")
        assert "pykälä" in result
        assert "viisi" in result
        assert "§" not in result

    def test_section_sign_without_space(self) -> None:
        result = normalize_finnish_text("§5 on laki")
        assert "pykälä viisi" in result
        assert "§" not in result

    def test_section_sign_with_larger_number(self) -> None:
        # Regression check: § 242 must not trigger the Pass H ordering bug
        result = normalize_finnish_text("§ 242")
        assert "pykälä" in result
        assert "kaksisataa neljäkymmentä kaksi" in result
        assert "sataan" not in result.split()[1:]  # no false illative split


class TestPassHMorphemeOrdering:
    """Pass H's morpheme splitter must prefer partitive `sataa` /
    `kymmentä` over illative `sataan` / `kymmeneen` inside compound
    numbers so it doesn't steal a leading letter from the next
    morpheme. See the explicit-ordering list in `_FI_MORPHEME_STEMS`."""

    def test_242_nominative_splits_correctly(self) -> None:
        result = normalize_finnish_text("242")
        # Must be `kaksisataa neljäkymmentä kaksi`
        # NOT `kaksisataan eljäkymmentä kaksi` (the old bug).
        assert "kaksisataa neljäkymmentä kaksi" in result
        # The bad form has a standalone `eljäkymmentä` preceded by a
        # space (the orphaned first letter after the false split).
        assert " eljäkymmentä" not in result
        assert "kaksisataan " not in result

    def test_345_nominative_splits_correctly(self) -> None:
        result = normalize_finnish_text("345")
        assert "kolmesataa neljäkymmentä viisi" in result
        assert "sataan" not in result

    def test_1889_still_splits_correctly(self) -> None:
        # Regression from session 1.
        result = normalize_finnish_text("1889")
        assert "kahdeksansataa kahdeksankymmentä yhdeksän" in result


class TestShortRangeGovernorInflection:
    """Pass D now matches 1–4 digit ranges so short ranges like
    `sivuilta 42–45` also travel through Pass G's governor detection
    and both endpoints inflect correctly."""

    def test_sivuilta_plural_ablative_range(self) -> None:
        # `sivuilta 42-45` → both endpoints in ablative
        result = normalize_finnish_text("sivuilta 42-45")
        # Ablative forms of 42 and 45 both appear
        assert "neljältäkymmeneltä kahdelta" in result
        assert "neljältäkymmeneltä viideltä" in result

    def test_sivulta_singular_ablative_short_range(self) -> None:
        result = normalize_finnish_text("sivulta 3-5")
        assert "kolmelta" in result
        assert "viideltä" in result

    def test_riveilta_range(self) -> None:
        result = normalize_finnish_text("riveiltä 10-12")
        assert "kymmeneltä" in result
        assert "kahdeltatoista" in result

    def test_bare_short_range_nominative_fallback(self) -> None:
        # No governor → both endpoints in nominative (acceptable)
        result = normalize_finnish_text("5-2")
        assert "viisi" in result
        assert "kaksi" in result

    def test_year_range_still_works(self) -> None:
        # Regression check — long year ranges continue to work.
        result = normalize_finnish_text("vuonna 1500-1800")
        assert "tuhat viisisataa" in result
        assert "tuhat kahdeksansataa" in result
