"""Language-aware text normalization dispatcher.

Single entry point for all TTS text normalization. Routes to the
per-language module based on the ``lang`` argument and never lets
Finnish rules touch English text or vice versa.

Why this module exists: a previous bug had
``scripts/generate_chatterbox_audiobook.py`` calling the Finnish
normalizer unconditionally on every run, including ``--language en``
runs. Roman numerals got expanded as Finnish ordinals, numbers got
case-inflected, loanwords got respelled. The fix is structural —
every caller goes through ``normalize_text(text, lang)`` and each
backend raises ``LanguageMismatchError`` if invoked with the wrong
language. Cross-contamination becomes architecturally impossible.
"""

from __future__ import annotations

import re

SUPPORTED_LANGS: tuple[str, ...] = ("fi", "en")

# A line that ends a paragraph without terminal punctuation — in practice a
# heading. Reported by a listener, not by any automated check: after the
# title, "the beef starts with no pause".
#
# A blank line means nothing to the synth. The sentence splitter breaks on
# terminal punctuation, so an unpunctuated heading is not a sentence end and
# gets glued to the first line of the body inside one chunk, where no
# inter-chunk gap can reach it. The model then reads title and opening
# sentence as one breath.
#
# A period is the cue the model does respond to, and it is how a human reads
# a heading aloud anyway. Only lines ending in a letter or digit qualify:
# a heading already ending in `.`, `:`, `?` or `!` has its cue, and one
# ending in a comma is a continuation rather than a heading.
_UNPUNCTUATED_PARAGRAPH_END_RE = re.compile(
    r"(?m)(?<=[^\W_])[ \t]*$(?=\n[ \t]*\n)",
)


def terminate_paragraphs(text: str) -> str:
    """Give an unpunctuated paragraph-final line a sentence terminator.

    Runs before the language backends so every later pass — sentence
    splitting, chunking, seam-gap selection — sees the heading as the
    sentence it is read as.
    """
    if not text:
        return text
    return _UNPUNCTUATED_PARAGRAPH_END_RE.sub(".", text)


class LanguageMismatchError(ValueError):
    """A per-language normalizer was invoked with the wrong language."""


def normalize_text(
    text: str,
    lang: str,
    *,
    year_shortening: str = "radio",
    drop_citations: bool = True,
) -> str:
    """Dispatch to the per-language normalizer.

    Args:
        text: Input text.
        lang: Language code. Must be one of ``SUPPORTED_LANGS``.
        year_shortening: Forwarded to the Finnish normalizer; ignored
            for English.
        drop_citations: Forwarded to the Finnish normalizer; ignored
            for English.

    Returns:
        Normalized text. For English (phase 1 of the rollout) this
        is currently a pass-through — the English normalizer lands
        in PR 2. The pass-through is the *correct* fallback: an
        unnormalized English read is vastly better than one
        mis-normalized through Finnish rules.

    Raises:
        ValueError: If ``lang`` is not in ``SUPPORTED_LANGS``.
    """
    if not text:
        return text

    lang = lang.lower()

    # Before dispatch: a heading with no terminal punctuation is glued to
    # the body text and read without a pause. See terminate_paragraphs.
    text = terminate_paragraphs(text)

    if lang == "fi":
        from src.tts_normalizer_fi import normalize_finnish_text
        out = normalize_finnish_text(
            text,
            drop_citations=drop_citations,
            year_shortening=year_shortening,
            _lang="fi",
        )
    elif lang == "en":
        from src.tts_normalizer_en import normalize_english_text
        out = normalize_english_text(text, _lang="en")
    else:
        raise ValueError(
            f"Unsupported lang {lang!r}; expected one of {SUPPORTED_LANGS}."
        )

    # Final gate — applied here rather than inside each backend so that a
    # language module cannot forget it. Any Unicode symbol still standing
    # after normalization is one the synth cannot phonemize: English drops
    # it silently and mis-narrates the sentence, Finnish early-stops and
    # loses the rest of the chunk. See src/tts_symbols.py.
    from src.tts_symbols import strip_unspeakable
    return strip_unspeakable(out, lang)
