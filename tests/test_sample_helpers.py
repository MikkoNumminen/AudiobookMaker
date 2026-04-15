"""Unit tests for src.sample_helpers."""
from __future__ import annotations

import pytest

from src.sample_helpers import (
    DEFAULT_SAMPLE_CHARS,
    compute_sample_output_path,
    extract_sample_text,
)


class TestExtractSampleText:
    def test_short_text_returned_unchanged(self) -> None:
        text = "Hei maailma."
        assert extract_sample_text(text, max_chars=500) == "Hei maailma."

    def test_text_at_exact_limit_returned_unchanged(self) -> None:
        text = "x" * 500
        assert extract_sample_text(text, max_chars=500) == text

    def test_long_text_trimmed_at_sentence_boundary(self) -> None:
        text = (
            "First sentence is here. Second sentence follows. Third one too. "
            + ("padding " * 100)
        )
        result = extract_sample_text(text, max_chars=80)
        assert result.endswith(".")
        assert len(result) <= 80
        assert "First sentence is here." in result

    def test_question_mark_counts_as_sentence_end(self) -> None:
        text = "Onko tämä Suomi? " + ("jatkoa " * 200)
        result = extract_sample_text(text, max_chars=50)
        assert result.endswith("?")

    def test_exclamation_counts_as_sentence_end(self) -> None:
        text = "Tervetuloa! " + ("muu sisalto " * 200)
        result = extract_sample_text(text, max_chars=50)
        assert result.endswith("!")

    def test_no_sentence_boundary_falls_back_to_raw_truncation(self) -> None:
        # Long uninterrupted run with no punctuation — falls back to raw cut.
        text = "abcdefgh " * 200
        result = extract_sample_text(text, max_chars=50)
        assert len(result) <= 50
        assert result == text[:50].rstrip()

    def test_sentence_in_first_half_is_ignored(self) -> None:
        # Period at position 5 is too early — truncation should NOT use it.
        text = "Hi. " + ("xxxxx " * 200)
        result = extract_sample_text(text, max_chars=200)
        # We expect the raw fallback because there's no boundary in the
        # second half of the snippet.
        assert len(result) <= 200
        assert result.startswith("Hi.")

    def test_leading_whitespace_stripped(self) -> None:
        text = "   \n\nHei. Maailma."
        assert extract_sample_text(text) == "Hei. Maailma."

    def test_default_chars_constant(self) -> None:
        assert DEFAULT_SAMPLE_CHARS == 500

    def test_newline_after_period_recognized(self) -> None:
        text = "Eka lause.\n" + ("toinen " * 200)
        result = extract_sample_text(text, max_chars=30)
        assert result.endswith(".")


class TestComputeSampleOutputPath:
    def test_basic_mp3(self) -> None:
        assert compute_sample_output_path("kirja.mp3") == "kirja_sample.mp3"

    def test_numbered_mp3_keeps_number(self) -> None:
        assert (
            compute_sample_output_path("texttospeech_4.mp3")
            == "texttospeech_4_sample.mp3"
        )

    def test_full_path_preserved(self) -> None:
        result = compute_sample_output_path("C:/foo/bar/kirja.mp3")
        # On Windows the result uses os-native separator; comparing components.
        assert result.replace("\\", "/").endswith("/foo/bar/kirja_sample.mp3")

    def test_non_mp3_extension_preserved(self) -> None:
        assert compute_sample_output_path("clip.wav") == "clip_sample.wav"

    def test_no_extension(self) -> None:
        # Path with no suffix — _sample is appended to the bare name.
        assert compute_sample_output_path("clip") == "clip_sample"

    @pytest.mark.parametrize("name", ["a.b.c.mp3", "weird_name_with_dots.x.mp3"])
    def test_compound_dots_only_split_on_last(self, name: str) -> None:
        # Path.stem only strips the LAST suffix, so 'a.b.c.mp3' → stem 'a.b.c'.
        result = compute_sample_output_path(name)
        assert result.endswith("_sample.mp3")
        assert ".mp3" in result
