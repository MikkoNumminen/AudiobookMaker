"""Tests for Finnish clock times that are not written `klo HH:MM`.

Found while building a listening test (2026-08-18): "Kello on 20:30" came out
"kaksikymmentä kolmeenkymmeneen" — "twenty to thirty". The clock pass required
`klo` or `kello` to sit IMMEDIATELY before the digits, so an ordinary sentence
with a linking verb fell through to Pass X, the colon-ratio pass, which read
the time as a ratio.

The prefix requirement itself is deliberate and stays: a bare `HH:MM` is
genuinely ambiguous with sports scores, ratios and chapter ranges, and the
cost of guessing wrong there is worse than the cost of a missed time.
"""
from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


class TestPrefixedTimes:
    @pytest.mark.parametrize("text", ["klo 20:30", "kello 20:30"])
    def test_the_original_forms_still_work(self, text):
        out = normalize_text(text, "fi")
        assert "kaksikymmentä" in out and "kolmekymmentä" in out
        assert ":" not in out

    def test_klo_is_spoken_in_full(self):
        """The abbreviation is read aloud as the whole word."""
        assert normalize_text("klo 20:30", "fi").startswith("kello")


class TestLinkingVerbs:
    """The reported defect: an ordinary sentence, not a bare timestamp."""

    @pytest.mark.parametrize("text,verb", [
        ("Kello on 20:30", "on"),
        ("Kello oli 20:30", "oli"),
        ("Kello olisi 20:30", "olisi"),
    ])
    def test_a_linking_verb_no_longer_breaks_the_match(self, text, verb):
        out = normalize_text(text, "fi")
        assert "kaksikymmentä kolmekymmentä" in out, out
        assert verb in out, "the linking verb was dropped from the sentence"

    def test_the_ratio_reading_is_gone(self):
        """`kolmeenkymmeneen` is the illative the ratio pass produces."""
        assert "kolmeenkymmeneen" not in normalize_text("Kello on 20:30", "fi")

    def test_capitalisation_is_preserved(self):
        """The sentence still starts with a capital after rewriting."""
        assert normalize_text("Kello on 20:30", "fi").startswith("Kello on")

    def test_no_colon_survives(self):
        assert ":" not in normalize_text("Kello on 20:30", "fi")


class TestRatiosAreStillRatios:
    """The prefix requirement exists to protect these. It must keep doing so."""

    def test_a_bare_ratio_is_untouched_by_the_clock_pass(self):
        assert "yksi viiteen" in normalize_text("suhde 1:5", "fi")

    def test_a_bare_time_is_still_left_alone(self):
        """Deliberate: `20:30` with no prefix is ambiguous with a score.

        Pinning the CURRENT behaviour, not endorsing it — if bare times ever
        need handling, this test is the place that says what changes.
        """
        out = normalize_text("Juna lähtee 20:30", "fi")
        assert "kolmeenkymmeneen" in out

    def test_a_word_that_merely_starts_with_kello_does_not_match(self):
        out = normalize_text("kellotaulu 1:5", "fi")
        assert "yksi viiteen" in out


class TestInvalidTimes:
    def test_an_impossible_time_is_not_read_as_a_clock(self):
        """Hour 25 / minute 99 fail validation and fall through, which is the
        documented behaviour for pathological input."""
        out = normalize_text("Kello on 25:99", "fi")
        assert "kello kaksikymmentä viisi yhdeksänkymmentä yhdeksän" not in out

    @pytest.mark.parametrize("text", ["klo 00:00", "kello 23:59"])
    def test_the_range_boundaries_are_valid(self, text):
        assert ":" not in normalize_text(text, "fi")
