"""Tests for src/tts_normalizer.py — the language-aware dispatcher.

Two responsibilities under test:
1. **Routing**: ``normalize_text`` dispatches to the right backend
   based on ``lang``.
2. **Cross-contamination prevention**: each per-language backend
   refuses to run when invoked with the wrong ``_lang`` kwarg, and
   the dispatcher itself rejects unknown languages.

This file exists to make the bug class "Finnish rules accidentally
applied to English text" architecturally impossible to reintroduce.
"""

from __future__ import annotations

import re

import pytest

from src.tts_normalizer import (
    LanguageMismatchError,
    SUPPORTED_LANGS,
    normalize_text,
)
from src.tts_normalizer_fi import normalize_finnish_text


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRouting:
    def test_finnish_routes_to_fi_backend(self) -> None:
        """`lang='fi'` produces Finnish output — verify by sentinel."""
        out = normalize_text("vuonna 1500", "fi")
        # Finnish normalizer should produce Finnish number words.
        assert "tuhat" in out.lower() or "viisi" in out.lower()

    def test_english_passes_through_unchanged_for_now(self) -> None:
        """Phase 1: EN dispatcher returns input unchanged.

        PR 2 will replace this with the real English normalizer; for
        now the contract is "definitely no Finnish rewrites".
        """
        text = "In the year 1500 King Henry IV reigned."
        assert normalize_text(text, "en") == text

    def test_lang_is_case_insensitive(self) -> None:
        """`'FI'` and `'fi'` both work."""
        assert normalize_text("hello", "EN") == "hello"
        # FI route just needs to not raise.
        normalize_text("vuonna 1500", "FI")

    def test_empty_text_short_circuits(self) -> None:
        assert normalize_text("", "fi") == ""
        assert normalize_text("", "en") == ""

    def test_unsupported_language_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported lang"):
            normalize_text("hello", "de")

    def test_supported_langs_constant_is_authoritative(self) -> None:
        for lang in SUPPORTED_LANGS:
            # Should not raise — every advertised lang must dispatch.
            normalize_text("test", lang)


# ---------------------------------------------------------------------------
# Cross-contamination — the Finnish backend refuses non-FI input
# ---------------------------------------------------------------------------


class TestFinnishLanguageGuard:
    def test_finnish_backend_raises_on_english_lang_kwarg(self) -> None:
        """Calling normalize_finnish_text with _lang='en' must raise."""
        with pytest.raises(LanguageMismatchError):
            normalize_finnish_text("Chapter IV", _lang="en")

    def test_finnish_backend_raises_on_unknown_lang_kwarg(self) -> None:
        with pytest.raises(LanguageMismatchError):
            normalize_finnish_text("hello", _lang="de")

    def test_finnish_backend_accepts_explicit_fi_lang(self) -> None:
        """_lang='fi' is the dispatcher's own contract — must work."""
        out = normalize_finnish_text("vuonna 1500", _lang="fi")
        assert isinstance(out, str)

    def test_finnish_backend_accepts_no_lang_kwarg_for_back_compat(
        self,
    ) -> None:
        """Legacy direct callers (419 existing tests + dev scripts)
        must keep working without specifying _lang."""
        out = normalize_finnish_text("vuonna 1500")
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# The actual bug this whole architecture exists to prevent
# ---------------------------------------------------------------------------


class TestNoFinnishRewritesOnEnglishInput:
    """Regression suite for the Roman-numeral-as-Finnish-ordinal bug.

    If any of these fail, the Chatterbox subprocess (or any other
    caller) is leaking Finnish normalization into an English run.
    """

    # Tokens that *can only* come from the Finnish normalizer. If any
    # of these appear in the dispatcher's English output, something
    # is routing wrong.
    FINNISH_SENTINELS = [
        r"\bneljäs\b",        # Pass L: IV → "neljäs"
        r"\bviides\b",        # Pass L: V  → "viides"
        r"\bkahdeksas\b",     # Pass L: VIII → "kahdeksas"
        r"\bkymmenes\b",      # Pass L: X  → "kymmenes"
        r"\btuhat\b",         # Pass G: 1000+
        r"\bvuonna\b",        # already Finnish
        r"\bluvulla\b",       # Pass C: -luvulla
        r"-luvulla",          # Pass C
    ]

    @pytest.mark.parametrize("sample", [
        "Chapter IV opens with Caesar at the Rubicon.",
        "King Henry VIII founded the Church of England.",
        "In 1500 the world looked very different.",
        "Pope John XXIII convened the council in 1962.",
        "Volume V, page 234, footnote 17.",
        "The 19th century was a turning point.",
    ])
    def test_english_dispatcher_yields_no_finnish_tokens(
        self, sample: str
    ) -> None:
        out = normalize_text(sample, "en")
        for pattern in self.FINNISH_SENTINELS:
            assert not re.search(pattern, out, flags=re.IGNORECASE), (
                f"Finnish sentinel {pattern!r} appeared in English "
                f"normalizer output: {out!r}"
            )

    def test_finnish_dispatcher_still_produces_finnish(self) -> None:
        """Sanity check the other direction — FI must keep working."""
        out = normalize_text("Kustaa II vuonna 1500", "fi")
        # At minimum, the year should expand to Finnish words.
        assert any(tok in out.lower() for tok in (
            "tuhat", "viisi", "sata"
        )), f"Finnish normalizer produced no Finnish words: {out!r}"
