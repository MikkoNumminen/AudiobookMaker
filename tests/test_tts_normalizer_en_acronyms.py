"""Tests for Pass S — English acronym handling."""

from __future__ import annotations

import pytest

from src._en_pass_s_acronyms import _pass_s_acronyms


class TestCommonAcronyms:
    def test_fbi(self) -> None:
        assert _pass_s_acronyms("the FBI investigated") == "the F B I investigated"

    def test_cia(self) -> None:
        assert _pass_s_acronyms("a CIA agent") == "a C I A agent"

    def test_usa(self) -> None:
        assert _pass_s_acronyms("made in USA") == "made in U S A"

    def test_ibm(self) -> None:
        assert _pass_s_acronyms("IBM stock rose") == "I B M stock rose"

    def test_bbc(self) -> None:
        assert _pass_s_acronyms("on the BBC tonight") == "on the B B C tonight"

    def test_led(self) -> None:
        assert _pass_s_acronyms("LED lights") == "L E D lights"

    def test_acronym_at_end(self) -> None:
        assert _pass_s_acronyms("worked at IBM") == "worked at I B M"


class TestWhitelist:
    @pytest.mark.parametrize(
        "word",
        ["NASA", "NATO", "UNESCO", "OPEC", "LASER", "IKEA", "RAM", "ROM", "RADAR", "SCUBA", "BAFTA"],
    )
    def test_whitelist_preserved(self, word: str) -> None:
        sentence = f"the {word} launch"
        assert _pass_s_acronyms(sentence) == sentence

    def test_nasa_in_context(self) -> None:
        assert _pass_s_acronyms("the NASA launch") == "the NASA launch"

    def test_laser_in_context(self) -> None:
        assert _pass_s_acronyms("a LASER beam") == "a LASER beam"


class TestMixedSentences:
    def test_acronym_plus_whitelist(self) -> None:
        assert (
            _pass_s_acronyms("the FBI visited NASA today")
            == "the F B I visited NASA today"
        )

    def test_multiple_acronyms(self) -> None:
        assert (
            _pass_s_acronyms("the FBI and CIA cooperate")
            == "the F B I and C I A cooperate"
        )


class TestHeadingRun:
    def test_three_caps_in_row_left_alone(self) -> None:
        # Middle token has ALL-CAPS neighbors on both sides.
        result = _pass_s_acronyms("NEW CHAPTER ONE")
        assert result == "NEW CHAPTER ONE"

    def test_long_heading_run(self) -> None:
        result = _pass_s_acronyms("KESKI JA AJALLA TIME")
        assert result == "KESKI JA AJALLA TIME"

    def test_two_caps_not_heading(self) -> None:
        # Only two caps in a row — edges should still be spelled.
        # "FBI CIA" — FBI has no left ALL-CAPS neighbor, CIA has no right.
        result = _pass_s_acronyms("the FBI CIA")
        assert result == "the F B I C I A"


class TestSingleLetters:
    def test_pronoun_i(self) -> None:
        assert _pass_s_acronyms("I went home") == "I went home"

    def test_article_a(self) -> None:
        assert _pass_s_acronyms("A cat sat") == "A cat sat"

    def test_mixed_single_with_acronym(self) -> None:
        assert _pass_s_acronyms("I saw the FBI") == "I saw the F B I"


class TestPassthrough:
    def test_lowercase_untouched(self) -> None:
        assert _pass_s_acronyms("the quick brown fox") == "the quick brown fox"

    def test_mixed_case_untouched(self) -> None:
        assert _pass_s_acronyms("The Quick Brown Fox") == "The Quick Brown Fox"

    def test_empty_string(self) -> None:
        assert _pass_s_acronyms("") == ""

    def test_whitespace_only(self) -> None:
        assert _pass_s_acronyms("   ") == "   "

    def test_already_spaced(self) -> None:
        # Already letter-by-letter — each letter is single char, won't match.
        assert _pass_s_acronyms("F B I") == "F B I"

    def test_six_plus_letters_untouched(self) -> None:
        # Token longer than 5 letters — not an acronym by this heuristic.
        assert _pass_s_acronyms("HELLOOO world") == "HELLOOO world"


class TestIdempotence:
    def test_idempotent_simple(self) -> None:
        once = _pass_s_acronyms("the FBI investigated")
        twice = _pass_s_acronyms(once)
        assert once == twice

    def test_idempotent_whitelist(self) -> None:
        once = _pass_s_acronyms("the NASA launch")
        twice = _pass_s_acronyms(once)
        assert once == twice

    def test_idempotent_mixed(self) -> None:
        once = _pass_s_acronyms("the FBI visited NASA with IBM")
        twice = _pass_s_acronyms(once)
        assert once == twice


class TestPunctuation:
    def test_acronym_before_comma(self) -> None:
        assert _pass_s_acronyms("the FBI, however,") == "the F B I, however,"

    def test_acronym_before_period(self) -> None:
        assert _pass_s_acronyms("from the FBI.") == "from the F B I."
