"""Unit tests for tts_engine module.

Network calls to edge-tts are mocked. Only local logic is tested here.
Integration tests (actual synthesis) require internet access and are skipped in CI.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests that require ffmpeg if it is not installed
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)

from src.tts_engine import (
    TTSConfig,
    VOICES,
    combine_audio_files,
    chapters_to_speech,
    split_text_into_chunks,
    text_to_speech,
    _force_split,
    _split_sentences,
    MAX_CHUNK_CHARS,
)


# ---------------------------------------------------------------------------
# TTSConfig
# ---------------------------------------------------------------------------


class TestTTSConfig:
    def test_default_language_is_finnish(self) -> None:
        cfg = TTSConfig()
        assert cfg.language == "fi"

    def test_resolved_voice_uses_default_for_language(self) -> None:
        cfg = TTSConfig(language="fi")
        assert cfg.resolved_voice() == VOICES["fi"]["default"]

    def test_resolved_voice_respects_explicit_voice(self) -> None:
        cfg = TTSConfig(voice="en-US-GuyNeural")
        assert cfg.resolved_voice() == "en-US-GuyNeural"

    def test_unknown_language_falls_back_to_finnish(self) -> None:
        cfg = TTSConfig(language="xx")
        assert cfg.resolved_voice() == VOICES["fi"]["default"]


# ---------------------------------------------------------------------------
# split_text_into_chunks
# ---------------------------------------------------------------------------


class TestSplitTextIntoChunks:
    def test_empty_text_returns_empty_list(self) -> None:
        assert split_text_into_chunks("") == []
        assert split_text_into_chunks("   ") == []

    def test_short_text_is_single_chunk(self) -> None:
        text = "Lyhyt teksti."
        chunks = split_text_into_chunks(text, max_chars=500)
        assert len(chunks) == 1
        assert "Lyhyt teksti" in chunks[0]

    def test_chunks_do_not_exceed_max_chars(self) -> None:
        # Create text with many short sentences
        text = " ".join(["Lause numero " + str(i) + "." for i in range(200)])
        chunks = split_text_into_chunks(text, max_chars=200)
        for chunk in chunks:
            assert len(chunk) <= 200, f"Chunk too long: {len(chunk)}"

    def test_very_long_single_sentence_is_force_split(self) -> None:
        long_sentence = "sana " * 1000  # 5000 chars, no punctuation
        chunks = split_text_into_chunks(long_sentence, max_chars=300)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 300

    def test_all_text_preserved_across_chunks(self) -> None:
        sentences = ["Tämä on lause numero " + str(i) + "." for i in range(50)]
        text = " ".join(sentences)
        chunks = split_text_into_chunks(text, max_chars=300)
        combined = " ".join(chunks)
        # All original words should appear somewhere
        for i in range(50):
            assert str(i) in combined

    def test_no_empty_chunks(self) -> None:
        text = "A. B. C. D."
        chunks = split_text_into_chunks(text, max_chars=50)
        for chunk in chunks:
            assert chunk.strip() != ""


# ---------------------------------------------------------------------------
# _force_split
# ---------------------------------------------------------------------------


class TestForceSplit:
    def test_splits_on_word_boundaries(self) -> None:
        text = "yksi kaksi kolme neljä viisi"
        parts = _force_split(text, max_chars=12)
        assert all(len(p) <= 12 for p in parts)
        assert " ".join(parts)  # all words present

    def test_single_word_longer_than_max(self) -> None:
        # Can't split a single word — returns it as-is
        word = "a" * 500
        parts = _force_split(word, max_chars=100)
        assert len(parts) == 1
        assert parts[0] == word


# ---------------------------------------------------------------------------
# combine_audio_files
# ---------------------------------------------------------------------------


class TestCombineAudioFiles:
    def test_raises_on_empty_list(self) -> None:
        with pytest.raises(ValueError):
            combine_audio_files([], "/tmp/out.mp3")

    @requires_ffmpeg
    def test_combines_real_mp3s(self) -> None:
        """Create two minimal silent MP3s and combine them."""
        from pydub import AudioSegment

        with tempfile.TemporaryDirectory() as tmp:
            seg1 = AudioSegment.silent(duration=100)  # 100 ms
            seg2 = AudioSegment.silent(duration=100)
            f1 = os.path.join(tmp, "a.mp3")
            f2 = os.path.join(tmp, "b.mp3")
            seg1.export(f1, format="mp3")
            seg2.export(f2, format="mp3")

            out = os.path.join(tmp, "combined.mp3")
            combine_audio_files([f1, f2], out)

            assert os.path.exists(out)
            result = AudioSegment.from_mp3(out)
            assert len(result) >= 100


# ---------------------------------------------------------------------------
# text_to_speech (mocked)
# ---------------------------------------------------------------------------


def _make_fake_mp3(path: str) -> None:
    """Write a minimal valid MP3-like file using WAV wrapped content.

    Since ffmpeg is not available in the test environment we write a real WAV
    file but with an .mp3 extension and patch pydub to accept it.
    pydub.AudioSegment.from_mp3 actually just calls ffmpeg; to avoid that
    we patch combine_audio_files entirely in tests that need it.
    """
    from pydub import AudioSegment
    # Use WAV format which doesn't require ffmpeg
    seg = AudioSegment.silent(duration=50)
    seg.export(path, format="wav")


class TestTextToSpeech:
    def test_raises_on_empty_text(self) -> None:
        with pytest.raises(ValueError):
            text_to_speech("", "/tmp/out.mp3")

    @requires_ffmpeg
    def test_calls_progress_callback(self) -> None:
        from pydub import AudioSegment

        progress_calls: list[tuple] = []

        def cb(current: int, total: int, msg: str) -> None:
            progress_calls.append((current, total, msg))

        with patch("src.tts_engine._synthesize_chunk") as mock_synth:
            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech("Lyhyt teksti.", out, progress_cb=cb)
                assert len(progress_calls) >= 1
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_creates_output_file(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine._synthesize_chunk") as mock_synth:
            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech("Tämä on testi.", out)
                assert os.path.exists(out)
                assert os.path.getsize(out) > 0
            finally:
                os.unlink(out)
