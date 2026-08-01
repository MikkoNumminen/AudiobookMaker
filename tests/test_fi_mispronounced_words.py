"""Pass I sub-pass 5 — respelling native Finnish words the model gets wrong.

The Chatterbox-Finnish model reliably mispronounces certain ordinary
Finnish words. `pyytämisen` came out as "tyytämisen" — the word-initial
plosive shifting from p to t — on the original take, on a fresh re-roll,
and again in an isolated probe sentence. `pyy-tämisen` was correct every
time.

The respelling is a pronunciation hint, not a spelling change: the hyphen
nudges the phoneme boundary and is inaudible in the output.

Kept separate from `foreign_names`, which is case-sensitive and about
proper nouns from other languages. These are ordinary Finnish words that
appear sentence-initially, so they are matched regardless of case.
"""

from __future__ import annotations

import pytest

from src.fi_loanwords import _respell_mispronounced
from src.tts_normalizer import normalize_text


# ---------------------------------------------------------------------------
# The sub-pass in isolation
# ---------------------------------------------------------------------------

WORDS = {"pyytämisen": "pyy-tämisen", "katkaise": "kat-kaise"}


def test_word_is_respelled():
    assert _respell_mispronounced("mitään pyytämisen arvoista", WORDS) == (
        "mitään pyy-tämisen arvoista")


def test_matching_is_case_insensitive():
    """These are ordinary words; they appear at the start of sentences."""
    assert "yy-tämisen" in _respell_mispronounced("Pyytämisen arvoista", WORDS)


def test_a_leading_capital_is_preserved():
    """Respelling must not lowercase a sentence start."""
    out = _respell_mispronounced("Pyytämisen arvoista", WORDS)
    assert out.startswith("Pyy-tämisen")


def test_only_whole_words_match():
    """A substring hit would corrupt an unrelated word."""
    assert _respell_mispronounced("katkaisemme sen", WORDS) == "katkaisemme sen"


def test_longest_key_wins():
    words = {"pyy": "PYY", "pyytämisen": "OK"}
    assert _respell_mispronounced("pyytämisen", words) == "OK"


def test_empty_table_is_a_no_op():
    assert _respell_mispronounced("mitään tekstiä", {}) == "mitään tekstiä"


# ---------------------------------------------------------------------------
# End to end through the normalizer, against the real lexicon
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    ("Eikä tarjolla ollut mitään pyytämisen arvoista.", "pyy-tämisen"),
    ("Olin siis esittänyt pyynnön jokaiselle.", "pyy-nnön"),
    ("Asenna se, katkaise verkkoyhteys.", "kat-kaise"),
])
def test_shipped_entries_apply(src, expected):
    assert expected in normalize_text(src, "fi")


def test_respelling_does_not_reach_english():
    """The lexicon is Finnish-only; the dispatcher must keep it there."""
    out = normalize_text("Please install it and katkaise nothing.", "en")
    assert "kat-kaise" not in out


def test_unlisted_finnish_words_are_untouched():
    """The table is a short list of known failures, not a general filter."""
    out = normalize_text("Siinä ei ole riviäkään koodia.", "fi")
    assert "riviäkään" in out
