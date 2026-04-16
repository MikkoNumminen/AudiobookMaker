"""Unit tests for the language dispatcher (src.tts_normalizer).

The dispatcher is the joint between Finnish and English normalization.
If it routes to the wrong per-language module, a whole Chatterbox
audiobook can silently get mis-read — Roman numerals as Finnish
ordinals in an English book, etc. The tests below lock in the current
routing contract so regressions fail loudly.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer import SUPPORTED_LANGS, normalize_text


# ---------------------------------------------------------------------------
# Dispatch by language code
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_fi_runs_finnish_normalizer(self) -> None:
        # "vuonna 1995" exercises Finnish year expansion — a transformation
        # the English normalizer does not produce.
        out = normalize_text("vuonna 1995", "fi")
        assert not any(ch.isdigit() for ch in out)
        assert "tuhat yhdeks\u00e4nsataa" in out

    def test_dispatch_en_runs_english_normalizer(self) -> None:
        # "Dr." -> "Doctor" and "1995" -> "nineteen ninety-five" are
        # English-only transforms.
        out = normalize_text("Dr. Smith arrived in 1995.", "en")
        assert "Doctor Smith" in out
        assert "nineteen ninety-five" in out
        assert "Dr." not in out
        assert "1995" not in out

    def test_dispatch_fi_matches_direct_finnish_call(self) -> None:
        # The dispatcher must produce the exact same bytes as calling the
        # Finnish backend directly — no hidden pre/post processing.
        from src.tts_normalizer_fi import normalize_finnish_text

        text = "Kustaa II syntyi vuonna 1594."
        assert normalize_text(text, "fi") == normalize_finnish_text(text)

    def test_dispatch_en_matches_direct_english_call(self) -> None:
        from src.tts_normalizer_en import normalize_english_text

        text = "Dr. Smith arrived in 1995."
        assert normalize_text(text, "en") == normalize_english_text(text)


# ---------------------------------------------------------------------------
# Unknown language codes
# ---------------------------------------------------------------------------


class TestUnknownLanguage:
    def test_unknown_lang_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported lang"):
            normalize_text("hello world", "de")

    def test_unknown_lang_xx_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported lang"):
            normalize_text("hello world", "xx")

    def test_unknown_lang_with_empty_text_returns_empty(self) -> None:
        # Current behaviour: the empty-text short-circuit runs BEFORE the
        # language check, so an unsupported lang + empty text does NOT
        # raise. Locking this in — callers rely on empty strings being
        # cheap no-ops regardless of configuration state.
        assert normalize_text("", "xx") == ""


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.parametrize("lang", ["fi", "en"])
    def test_empty_text_returns_empty(self, lang: str) -> None:
        assert normalize_text("", lang) == ""

    def test_whitespace_only_text_does_not_crash_fi(self) -> None:
        # Finnish normalizer is a near-passthrough on whitespace.
        out = normalize_text("   ", "fi")
        assert isinstance(out, str)

    def test_whitespace_only_text_does_not_crash_en(self) -> None:
        # English normalizer collapses whitespace-only input to empty
        # (pass K runs `.strip()` equivalent). Locking in.
        out = normalize_text("   ", "en")
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Case-insensitive language codes
# ---------------------------------------------------------------------------


class TestLanguageCaseInsensitivity:
    def test_uppercase_fi_is_accepted(self) -> None:
        from src.tts_normalizer_fi import normalize_finnish_text

        text = "vuonna 1995"
        assert normalize_text(text, "FI") == normalize_finnish_text(text)

    def test_uppercase_en_is_accepted(self) -> None:
        from src.tts_normalizer_en import normalize_english_text

        text = "Dr. Smith"
        assert normalize_text(text, "EN") == normalize_english_text(text)

    def test_mixed_case_language_code_is_accepted(self) -> None:
        # "Fi" / "eN" — the dispatcher lowercases before routing.
        assert normalize_text("vuonna 1995", "Fi") == normalize_text(
            "vuonna 1995", "fi"
        )
        assert normalize_text("Dr. Smith", "eN") == normalize_text(
            "Dr. Smith", "en"
        )


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


class TestModuleSurface:
    def test_supported_langs_tuple_is_fi_en(self) -> None:
        # If this changes, every caller assuming {"fi", "en"} needs review.
        assert SUPPORTED_LANGS == ("fi", "en")
