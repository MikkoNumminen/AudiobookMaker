"""Colons that are part of a token, not punctuation.

Three shapes reached the synth with a colon wedged inside a word, found
by linting normalizer output across the whole narration corpus rather
than by tripping over them one at a time:

    1:5              -> "yksi:viisi" / "one:five"   (ratio)
    README:en        -> "README:en"                 (Finnish case clitic)
    input:output     -> "input:output"              (English compound)

A colon is prosodic punctuation to the engine, so each of these became a
pause in the middle of a word.

Pass V (digit + letters) already existed and is covered in
test_tts_normalizer_fi_colon_suffix.py. These are the shapes it did not
claim.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text


# ---------------------------------------------------------------------------
# Ratios — both languages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("1:5", "one to five"),
    ("1:1", "one to one"),
    ("1:7", "one to seven"),
])
def test_english_ratio_reads_as_to(src, expected):
    assert expected in normalize_text(f"the ratio is {src} here", "en")


def test_english_two_digit_ratio_is_read_as_a_clock_time():
    """A genuine ambiguity, pinned so the behaviour is a choice not an accident.

    `1:20` is a valid ratio AND a valid time, and English writes times
    without a prefix, so Pass N claims it first. Finnish has no such
    problem: its time pass requires `klo`/`kello`, so `1:20` there is
    unambiguously a ratio.

    Nothing in the corpus needs the other reading. If an English text
    ever does, the fix is a time context (am/pm, "at", "o'clock") on
    Pass N, not a wider ratio rule.
    """
    assert "one twenty" in normalize_text("the ratio is 1:20 here", "en")


@pytest.mark.parametrize("src,expected", [
    ("1:5", "viiteen"),
    ("1:3", "kolmeen"),
    ("1:20", "kahteenkymmeneen"),
])
def test_finnish_ratio_puts_the_second_number_in_illative(src, expected):
    """Finnish reads a ratio as "yksi viiteen", not "yksi viisi"."""
    out = normalize_text(f"suhde on {src} tässä", "fi")
    assert expected in out
    assert ":" not in out


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_no_colon_survives_a_ratio(lang):
    assert ":" not in normalize_text("1:5 ja 1:7", lang)


# ---------------------------------------------------------------------------
# Clock times must not be mistaken for ratios
# ---------------------------------------------------------------------------

def test_english_clock_time_is_not_a_ratio():
    """The ratio pass runs after Pass N, which has already claimed times."""
    out = normalize_text("the meeting at 12:30 today", "en")
    assert "twelve thirty" in out
    assert "twelve to thirty" not in out


def test_finnish_clock_time_is_not_a_ratio():
    out = normalize_text("kello 20:30 alkaa", "fi")
    assert "kolmekymmentä" in out
    assert "kolmeenkymmeneen" not in out


# ---------------------------------------------------------------------------
# Finnish case clitics on letters and acronyms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", ["README:en", "M:n", "O:n", "ID:llä"])
def test_finnish_clitic_colon_becomes_a_boundary(src):
    """Finnish glues an ending to a non-word token through a colon.

    The colon becomes a hyphen rather than vanishing: deleting it fuses
    the ending onto the stem ("READMEen"), while a hyphen is the boundary
    marker the loanword pass already uses and the engine reads both
    halves.
    """
    out = normalize_text(f"se on {src} tässä", "fi")
    assert ":" not in out
    assert "-" in out


def test_finnish_clitic_does_not_touch_ordinary_punctuation():
    """A colon followed by a space is punctuation and must survive."""
    assert "Huomio:" in normalize_text("Huomio: tämä on tärkeää", "fi")


def test_finnish_clitic_does_not_touch_a_url():
    """`://` is not a case ending."""
    out = normalize_text("osoitteessa https://example.com sijaitsee", "fi")
    assert "https-" not in out


def test_acronym_expanded_to_words_keeps_a_raw_ending():
    """A known limitation, pinned so it is explicit rather than a surprise.

    When an acronym expands to a full Finnish phrase, the case ending
    cannot simply be appended and stay grammatical — `EU:n` should be
    "Euroopan unionin", not "Euroopan unioni-n". Inflecting an arbitrary
    expansion is real morphology and is not attempted. The hyphen is
    still an improvement on the colon, which the engine read as a pause.
    """
    out = normalize_text("EU:n jäsen", "fi")
    assert ":" not in out
    assert "Euroopan unioni-n" in out


# ---------------------------------------------------------------------------
# English word:word compounds
# ---------------------------------------------------------------------------

def test_english_compound_colon_becomes_a_space():
    assert "input output" in normalize_text("the input:output ratio", "en")


def test_english_sentence_colon_is_left_alone():
    assert "Note:" in normalize_text("Note: this matters", "en")


# ---------------------------------------------------------------------------
# Acronyms said as words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["en", "fi"])
def test_json_is_not_spelled_out(lang):
    """Every reader says "Jason"; nobody says "J S O N"."""
    assert "JSON" in normalize_text("reading it as JSON instead", lang)


def test_unknown_acronym_is_still_spelled_out():
    """The whitelist is a whitelist, not an amnesty."""
    assert "W C A G" in normalize_text("the WCAG boundary", "en")
