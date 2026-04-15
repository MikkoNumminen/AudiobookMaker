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

SUPPORTED_LANGS: tuple[str, ...] = ("fi", "en")


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

    if lang == "fi":
        from src.tts_normalizer_fi import normalize_finnish_text
        return normalize_finnish_text(
            text,
            drop_citations=drop_citations,
            year_shortening=year_shortening,
            _lang="fi",
        )

    if lang == "en":
        from src.tts_normalizer_en import normalize_english_text
        return normalize_english_text(text, _lang="en")

    raise ValueError(
        f"Unsupported lang {lang!r}; expected one of {SUPPORTED_LANGS}."
    )
