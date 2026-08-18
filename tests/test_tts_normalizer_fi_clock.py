"""Tests for Finnish clock times in ordinary prose.

Found while building a listening test (2026-08-18): "Kello on 20:30" came out
"kaksikymmentä kolmeenkymmeneen" — "twenty to thirty". The pass required
`klo`/`kello` to sit immediately before the digits, so a sentence with a verb
in between fell through to Pass X, the colon-ratio pass.

Reviewing that fix surfaced a worse one underneath it: the minute was spoken
as a bare cardinal, so 20:05 read as "kaksikymmentä viisi" (twenty-five) and
20:00 as "kaksikymmentä nolla" (twenty zero). Widening the pass made a
long-standing bug fire far more often, which is how it was noticed.

These assert exact full strings. Substring checks let the first version of
this file pass while asserting the absence of a string no code path could
produce. Note `_expand_dates_and_times` is also covered directly in
tests/test_tts_normalizer_fi.py; this file goes through `normalize_text`
because the reported defect was an interaction BETWEEN passes.
"""
from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


def _n(text: str) -> str:
    return normalize_text(text, "fi").strip()


class TestMinutesAreSpokenCorrectly:
    """The bug the widening exposed."""

    def test_a_leading_zero_is_spoken(self):
        """20:05 as "kaksikymmentä viisi" is twenty-FIVE."""
        assert _n("Kello on 20:05") == "Kello on kaksikymmentä nolla viisi"

    def test_single_digit_hour_and_minute(self):
        assert _n("Kello on 03:07") == "Kello on kolme nolla seitsemän"

    def test_a_whole_hour_drops_the_minutes(self):
        """"kaksikymmentä nolla" is "twenty zero"; Finnish says the hour."""
        assert _n("klo 20:00") == "kello kaksikymmentä"

    def test_a_normal_minute_is_unchanged(self):
        assert _n("kello 20:30") == "kello kaksikymmentä kolmekymmentä"

    def test_seconds_are_read_not_stranded(self):
        """`(\\d{2})\\b` stopped at the second colon and a later pass welded
        the seconds on with a hyphen."""
        assert _n("Kello on 20:30:45") == (
            "Kello on kaksikymmentä kolmekymmentä neljäkymmentä viisi"
        )


class TestPrefixReach:
    """Each of these fell through to the ratio pass before."""

    def test_a_linking_verb(self):
        assert _n("Kello on 20:30") == "Kello on kaksikymmentä kolmekymmentä"

    @pytest.mark.parametrize("sentence,expected", [
        ("Kello on nyt 20:30", "Kello on nyt kaksikymmentä kolmekymmentä"),
        ("Kello oli tasan 20:30", "Kello oli tasan kaksikymmentä kolmekymmentä"),
    ])
    def test_an_adverb_as_well_as_a_verb(self, sentence, expected):
        """Enumerating verb forms did not survive contact with real prose."""
        assert _n(sentence) == expected

    def test_a_compound_clock_noun(self):
        """`\\b` needs a standalone token, so "Herätyskello" used to miss."""
        assert _n("Herätyskello on 7:00") == "Herätyskello on seitsemän"

    def test_two_times_in_one_sentence(self):
        """Claiming only the first left half the sentence a ratio."""
        assert _n("Kello on 12:30 ja 13:45") == (
            "Kello on kaksitoista kolmekymmentä ja kolmetoista "
            "neljäkymmentä viisi"
        )

    def test_a_range_converts_both_and_leaves_no_hyphen(self):
        """The hyphen used to weld onto the spoken number and the second time
        was read as a ratio. The dash becomes a plain gap: nothing here knows
        whether the writer meant "from ... to" or a list."""
        assert _n("kello 20:30-21:45") == (
            "kello kaksikymmentä kolmekymmentä kaksikymmentä yksi "
            "neljäkymmentä viisi"
        )


class TestCapitalisation:
    def test_klo_expands_to_kello_keeping_the_capital(self):
        """Lower-casing it unconditionally started sentences with a
        lower-case word, and only for writers who wrote `Klo`."""
        assert _n("Klo 20:30 alkaa esitys.") == (
            "Kello kaksikymmentä kolmekymmentä alkaa esitys."
        )

    def test_a_written_out_prefix_is_echoed_as_written(self):
        assert _n("Kello on 20:30").startswith("Kello on")


class TestRatiosAreStillRatios:
    """The prefix requirement exists to protect these."""

    def test_a_bare_ratio_with_a_two_digit_second_term(self):
        """A single-digit minute cannot match the clock regex at all, so a
        `1:5` guard would pass even with the prefix rule deleted."""
        assert _n("suhde 1:50") == "suhde yksi viiteenkymmeneen"

    def test_a_word_merely_containing_kello_does_not_match(self):
        """"kellotaulu" does not END in kello, so it is not a clock prefix.
        Two-digit minute on purpose, so the guard is actually exercised."""
        assert "kolmeenkymmeneen" in _n("kellotaulu 20:30")

    def test_a_bare_time_is_still_left_alone(self):
        """Deliberate: `20:30` with no prefix is ambiguous with a score.

        Pinning CURRENT behaviour, not endorsing it. If bare times ever need
        handling, this test is what records the change.
        """
        assert "kolmeenkymmeneen" in _n("Juna lähtee 20:30")

    def test_a_distant_prefix_does_not_reach(self):
        """Two intervening words is the limit; beyond that a `kello` earlier
        in the sentence must not capture an unrelated ratio."""
        assert "kolmeenkymmeneen" in _n("Kello soi ja sitten tuli 20:30")


class TestInvalidTimes:
    def test_an_impossible_time_falls_through_to_the_ratio_pass(self):
        """Pins the REAL output. The previous version of this test asserted
        the absence of a string no code path could produce, so it passed
        whether or not the validation guard existed."""
        assert _n("Kello on 25:99") == (
            "Kello on kaksikymmentä viisi yhdeksäänkymmeneen yhdeksään"
        )

    def test_an_impossible_second_leaves_the_whole_match_alone(self):
        """Half-converting is worse than not converting."""
        assert "kolmekymmentä neljäkymmentä" not in _n("Kello on 20:30:99")

    @pytest.mark.parametrize("text,expected", [
        ("klo 00:00", "kello nolla"),
        ("kello 23:59", "kello kaksikymmentä kolme viisikymmentä yhdeksän"),
    ])
    def test_the_range_boundaries_are_valid(self, text, expected):
        assert _n(text) == expected
