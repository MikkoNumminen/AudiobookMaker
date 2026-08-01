"""Abbreviations must match whole tokens, never the tail of a word.

`_get_abbrev_re` built dotted abbreviations from their literal text with
no leading anchor. The trailing period ended the match; nothing started
it. So `p.` matched the last two characters of any word ending in p:

    "without a gap. Changing pages"  ->  "without a gapage Changing pages"
    "wake up. Then"                  ->  "wake upage Then"

Every English word ending in p before a sentence period was corrupted.
Found by transcribing narrated audio — no duration check can see it,
because "gapage" takes about as long to say as "gap", so the chunk stays
a perfectly normal length while saying a word that does not exist.

Finnish was never affected: `_fi_abbrev_re` has always anchored with \\b.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text
from src.tts_normalizer_en import _get_abbrev_re


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", [
    "gap", "map", "cap", "top", "step", "group",
    "trip", "stop", "help", "ship", "drop", "sleep",
])
def test_a_word_ending_in_p_survives_a_sentence_period(word):
    out = normalize_text(f"there was a {word}. Then it moved.", "en")
    assert f"{word}." in out
    assert "page" not in out


def test_the_reported_sentence():
    """Verbatim from the post that exposed this."""
    out = normalize_text(
        "the music carries on without a gap. Changing pages never turns "
        "your sound off.",
        "en",
    )
    assert "gapage" not in out
    assert "without a gap." in out


def test_sentence_final_up_is_not_mangled():
    """`up.` was on record as a Grandmom quirk to reword around.

    It was this bug: the engine was faithfully narrating "upage".
    """
    assert "upage" not in normalize_text("I had to look it up. Then I knew.", "en")


# ---------------------------------------------------------------------------
# The abbreviations still have to work
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("see p. 42", "page"),
    ("see pp. 12", "pages"),
    ("Mr. Smith", "Mister"),
    ("e.g. this one", "for example"),
    ("etc. and so on", "et cetera"),
])
def test_real_abbreviations_still_expand(src, expected):
    assert expected in normalize_text(src, "en")


def test_abbreviation_at_the_start_of_the_text():
    """\\b must not require a preceding character to exist."""
    assert "page" in normalize_text("p. 42 has it", "en")


def test_abbreviation_after_an_opening_bracket():
    assert "page" in normalize_text("(p. 42)", "en")


# ---------------------------------------------------------------------------
# The builder itself
# ---------------------------------------------------------------------------

def test_dotted_abbreviation_is_anchored_at_the_start():
    assert _get_abbrev_re("p.").search("gap.") is None
    assert _get_abbrev_re("p.").search("p. 42") is not None


def test_bare_abbreviation_is_anchored_at_both_ends():
    assert _get_abbrev_re("vs").search("versus") is None
    assert _get_abbrev_re("vs").search("a vs b") is not None


def test_a_symbol_initial_entry_would_still_match():
    """The leading \\b is conditional for a reason.

    `\\b` in front of a non-word character means the opposite of "start
    of a word", so applying it unconditionally would make a future
    symbol-initial entry match nothing at all.
    """
    assert _get_abbrev_re("§.").search("see §. here") is not None
