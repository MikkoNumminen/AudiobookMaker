"""A heading with no terminal punctuation is read without a pause.

Reported by a listener, not by any automated check: after the title,
"the beef starts with no pause". Every narrated file in the corpus had
it — one unpunctuated title each.

A blank line means nothing to the synth. The sentence splitter breaks on
terminal punctuation, so an unpunctuated heading is not a sentence end
and gets glued to the first line of the body inside a single chunk,
where the inter-chunk gap logic can never reach it. Title and opening
sentence come out as one breath.

Measured A/B on the same text, three-word heading plus one body
sentence: 0.28s of internal pause without the period, 0.40s with it.

This is a class no transcript check can catch — no words are missing,
so the word-level verifier passes it cleanly. It took an ear.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import normalize_text, terminate_paragraphs


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def test_unpunctuated_heading_gets_a_terminator():
    assert terminate_paragraphs("Otsikko tässä\n\nRunko.") == (
        "Otsikko tässä.\n\nRunko.")


def test_trailing_whitespace_does_not_hide_the_line_end():
    assert terminate_paragraphs("Otsikko   \n\nRunko.") == "Otsikko.\n\nRunko."


@pytest.mark.parametrize("ending", [".", "!", "?", ":", ",", "…"])
def test_a_heading_that_already_has_punctuation_is_left_alone(ending):
    """`.` `!` `?` already cue a pause. `:` leads in, `,` continues —
    turning either into a full stop would change how the line is read."""
    src = f"Otsikko{ending}\n\nRunko."
    assert terminate_paragraphs(src) == src


def test_a_single_newline_is_not_a_paragraph_break():
    """A wrapped line mid-paragraph must not gain a sentence end."""
    src = "Rivi yksi\nrivi kaksi.\n\nRunko."
    assert terminate_paragraphs(src) == src


def test_text_with_no_blank_line_is_untouched():
    src = "Otsikko\nRunko."
    assert terminate_paragraphs(src) == src


def test_every_paragraph_break_is_handled_not_just_the_first():
    out = terminate_paragraphs("Eka\n\nToka\n\nKolmas.")
    assert out == "Eka.\n\nToka.\n\nKolmas."


def test_empty_input():
    assert terminate_paragraphs("") == ""


def test_is_idempotent():
    once = terminate_paragraphs("Otsikko\n\nRunko.")
    assert terminate_paragraphs(once) == once


# ---------------------------------------------------------------------------
# Through the dispatcher, both languages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,heading,body", [
    ("fi", "Sivustoni pyysi ihmisiä asentamaan sen",
     "Kaverini oli tällä sivustolla."),
    ("en", "My site was asking people to install it",
     "A friend was on this site."),
])
def test_heading_is_terminated_in_both_languages(lang, heading, body):
    out = normalize_text(f"{heading}\n\n{body}", lang)
    # The heading's last word must be followed by a full stop.
    last_word = heading.split()[-1]
    assert f"{last_word}." in out


def test_a_question_heading_keeps_its_question_mark():
    """One corpus file's title ends in `?` and must not gain a period."""
    out = normalize_text("Do the cheap agents pay for themselves?\n\nBody.", "en")
    assert "themselves?" in out
    assert "themselves?." not in out
