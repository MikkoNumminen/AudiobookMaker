"""Tests for src/tts_symbols.py — symbol expansion and the final gate.

This file exists because of a defect found in a real conversion. The
passage ``0 to −90px at 884×900`` contains U+2212 MINUS SIGN and U+00D7
MULTIPLICATION SIGN. Neither was expanded, so both reached the synth
glued to a word, and the two languages failed differently:

* English swallowed the glyphs and narrated "0 to 90 pecs at 884 900" —
  a negative offset read aloud as a positive one, with nothing in the
  pipeline noticing.
* Finnish early-stopped at the unphonemizable token and lost the rest of
  the chunk. All five band-guard retries reproduced it, because the cause
  was the input rather than sampling noise.

The tests below lock in both layers of the fix: the explicit expansion
table, and the catch-all gate that guarantees no *unlisted* symbol can
ever reach the synth glued to a word again.
"""

from __future__ import annotations

import logging
import unicodedata

import pytest

from src.tts_normalizer import normalize_text
from src.tts_symbols import expand_symbols, strip_unspeakable

MINUS = "−"
TIMES = "×"


# ---------------------------------------------------------------------------
# Layer 1 — the expansion table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,word", [("en", "minus"), ("fi", "miinus")])
def test_minus_sign_becomes_a_word(lang, word):
    assert word in expand_symbols(f"{MINUS}90", lang)


@pytest.mark.parametrize("lang,word", [("en", "by"), ("fi", "kertaa")])
def test_multiplication_between_digits_reads_as_dimensions(lang, word):
    assert expand_symbols(f"884{TIMES}900", lang).split() == ["884", word, "900"]


def test_multiplication_outside_digits_reads_as_multiplication_in_english():
    # Only English distinguishes the two readings; Finnish says "kertaa"
    # for both, which is why this test is English-only.
    assert "times" in expand_symbols(f"a {TIMES} b", "en")


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_expansion_never_glues_the_word_to_a_neighbour(lang):
    """The whole point: `884×900` must not become one bogus token."""
    out = expand_symbols(f"884{TIMES}900", lang)
    assert "884" in out.split()
    assert "900" in out.split()


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_expansion_collapses_the_padding_it_introduces(lang):
    assert "  " not in expand_symbols(f"5 {MINUS} 2", lang)


def test_unknown_language_is_left_alone():
    """Layer 1 declines rather than guessing; layer 2 still cleans up."""
    assert expand_symbols(f"884{TIMES}900", "de") == f"884{TIMES}900"


# ---------------------------------------------------------------------------
# Layer 2 — the catch-all gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glyph", ["→", "☃", "√", "¥", "°"])
def test_unlisted_symbols_are_replaced_not_deleted(glyph):
    """A space keeps two words apart; deletion would fuse them.

    This is the property that makes the fix hold for glyphs nobody has
    enumerated yet — the actual guarantee being asked for.
    """
    out = strip_unspeakable(f"a{glyph}b")
    assert glyph not in out
    assert out.split() == ["a", "b"]


def test_gate_leaves_letters_digits_and_punctuation_alone():
    text = "Hello, world! It's 42 things (really) — a test; yes: no?"
    assert strip_unspeakable(text) == text


@pytest.mark.parametrize("ch", ["ä", "ö", "å", "é", "ü"])
def test_gate_preserves_non_ascii_letters(ch):
    assert ch in strip_unspeakable(f"sana{ch}sana")


def test_gate_logs_what_it_dropped(caplog):
    """An unanticipated glyph must surface in the log, not in the audio."""
    with caplog.at_level(logging.WARNING):
        strip_unspeakable("a → b", "fi")
    assert "U+2192" in caplog.text
    assert "RIGHTWARDS ARROW" in caplog.text


def test_gate_is_silent_when_there_is_nothing_to_drop(caplog):
    with caplog.at_level(logging.WARNING):
        strip_unspeakable("perfectly ordinary text", "en")
    assert caplog.text == ""


def test_every_glyph_in_the_expansion_table_would_be_caught_by_the_gate():
    """The two layers must agree on what counts as unspeakable.

    If a glyph in the table were NOT in an unspeakable category, layer 2
    would not be a genuine backstop for it — a future edit that dropped
    the table entry would leave the glyph reaching the synth silently.
    """
    from src.tts_symbols import _SHARED_TABLE, _TIMES, _UNSPEAKABLE_CATEGORIES

    for glyph in list(_SHARED_TABLE) + [_TIMES]:
        assert unicodedata.category(glyph) in _UNSPEAKABLE_CATEGORIES, glyph


# ---------------------------------------------------------------------------
# End to end, through the dispatcher — the actual regression
# ---------------------------------------------------------------------------

def test_english_regression_negative_offset_is_not_silently_dropped():
    """Was narrated as "0 to 90 pecs at 884 900" — sign lost, meaning flipped."""
    out = normalize_text(f"0 to {MINUS}90px at 884{TIMES}900", "en")
    assert MINUS not in out
    assert TIMES not in out
    assert "minus" in out
    assert "by" in out
    assert "pixels" in out


def test_finnish_regression_chunk_is_fully_expanded():
    """Was truncated mid-chunk: the synth early-stopped on the raw glyph."""
    out = normalize_text(
        f"0:sta {MINUS}90 pikseliin koossa 884{TIMES}900", "fi"
    )
    assert MINUS not in out
    assert TIMES not in out
    assert ":" not in out
    assert "miinus" in out
    assert "kertaa" in out


@pytest.mark.parametrize("lang,word", [("en", "times"), ("fi", "kertaa")])
def test_multiplier_idiom_is_not_glued_to_the_number(lang, word):
    """`5x` normalized to the non-word `fivex` / `viisix`.

    The `x` is a letter, so the layer-2 gate is no help here — this one
    has to be caught by name.
    """
    out = expand_symbols("now 5x", lang)
    assert word in out
    assert "5x" not in out


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_algebraic_x_is_not_a_multiplier(lang):
    """`3 ≤ x` is a variable. Only an `x` touching a digit is a multiplier."""
    assert expand_symbols("3 ≤ x", lang).rstrip().endswith("x")


def test_finnish_postfix_dollar_is_spoken_not_dropped():
    """Finnish writes currency after the number: `2,08 $`.

    Only the prefix form (`$5`) had a rule, so the postfix `$` survived
    normalization and truncated its chunk. The gate would now turn it
    into a space, which is safe but silent — losing the currency without
    saying so. The unit table has to claim it first.
    """
    out = normalize_text("yläraja: 2,08 $.", "fi")
    assert "dollaria" in out
    assert "$" not in out


def test_finnish_prefix_dollar_still_works():
    assert "dollaria" in normalize_text("$5 maksoi", "fi")


def test_finnish_euro_is_unaffected():
    assert "euroa" in normalize_text("hinta 20 €", "fi")


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_no_unspeakable_symbol_ever_survives_the_dispatcher(lang):
    """The guarantee, stated as a property over every symbol category.

    Whatever the per-language backends do or fail to do, the dispatcher's
    output is free of symbol codepoints.
    """
    from src.tts_symbols import _UNSPEAKABLE_CATEGORIES

    soup = "arvo " + " ".join(
        chr(cp) for cp in range(0x2190, 0x2300)
    ) + " loppu"
    out = normalize_text(soup, lang)
    offenders = [c for c in out if unicodedata.category(c) in _UNSPEAKABLE_CATEGORIES]
    assert offenders == []
