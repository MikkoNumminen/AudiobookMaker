"""Unit tests for tts_engine module.

Network calls to edge-tts are mocked. Only local logic is tested here.
Integration tests (actual synthesis) require internet access and are skipped in CI.

Finnish-normalizer tests live in ``tests/test_tts_normalizer_fi.py``.
Chunking tests live in ``tests/test_tts_chunking.py``.
Audio combine tests live in ``tests/test_tts_audio.py``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests that require ffmpeg if it is not installed
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)

from src.tts_engine import (
    TTSConfig,
    VOICES,
    text_to_speech,
    _synthesize_chunk,
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

    def test_tts_config_normalize_default_is_true(self) -> None:
        cfg = TTSConfig()
        assert cfg.normalize_text is True

    def test_tts_config_normalize_can_be_disabled(self) -> None:
        cfg = TTSConfig(normalize_text=False)
        assert cfg.normalize_text is False


class TestTTSConfigYearShortening:
    def test_tts_config_year_shortening_default_is_radio(self) -> None:
        cfg = TTSConfig()
        assert cfg.year_shortening == "radio"

    def test_tts_config_year_shortening_can_be_full(self) -> None:
        cfg = TTSConfig(year_shortening="full")
        assert cfg.year_shortening == "full"


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

        with patch("src.tts_engine._synthesize_chunk", autospec=True) as mock_synth:
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
    def test_text_to_speech_calls_normalizer_for_finnish(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_text", autospec=True) as mock_norm, \
             patch("src.tts_engine._synthesize_chunk", autospec=True) as mock_synth:
            mock_norm.side_effect = lambda t, lang, **kw: t + " NORMALIZED"

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "vuonna 1500",
                    out,
                    config=TTSConfig(language="fi", normalize_text=True),
                )
                assert mock_norm.called
                mock_norm.assert_called_with(
                    "vuonna 1500", "fi", year_shortening="radio"
                )
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_text_to_speech_does_not_finnish_normalize_english(self) -> None:
        """English path must NOT invoke the Finnish normalizer.

        The dispatcher routes "en" to a pass-through (or, after PR 2,
        to the English normalizer). What matters here is that the
        Finnish-specific module is never touched on an English run —
        that's the bug class this whole architecture exists to
        prevent.
        """
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_finnish_text", autospec=True) as mock_fi_norm, \
             patch("src.tts_engine._synthesize_chunk", autospec=True) as mock_synth:

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "In the year 1500",
                    out,
                    config=TTSConfig(language="en"),
                )
                assert not mock_fi_norm.called
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_text_to_speech_skips_normalizer_when_disabled(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_text", autospec=True) as mock_norm, \
             patch("src.tts_engine._synthesize_chunk", autospec=True) as mock_synth:

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "vuonna 1500",
                    out,
                    config=TTSConfig(language="fi", normalize_text=False),
                )
                assert not mock_norm.called
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_creates_output_file(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine._synthesize_chunk", autospec=True) as mock_synth:
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

    def test_each_chunk_gets_its_own_retry_budget(self, tmp_path) -> None:
        """Regression for the bounded-wait kill (same bug class as the
        Chatterbox bridge 60s waiter): each chunk is synthesized under its OWN
        _asyncio_run_with_retry — not the whole book under one 10s*chunks
        wall-clock budget that a slow connection would blow mid-flight. The
        per-chunk budget must also exceed the inner per-chunk timeout so a
        genuinely slow (but progressing) chunk is not abandoned."""
        from src.tts_engine import _EDGE_CHUNK_TIMEOUT, split_text_into_chunks

        # Enough text to exceed the 3000-char chunker default -> several chunks.
        text = " ".join(f"Lause numero {i}." for i in range(400))
        timeouts: list[float] = []

        def fake_retry(factory, timeout_s=120, retries=3):
            timeouts.append(timeout_s)
            factory().close()  # realise the coroutine, don't run it (lazy body)
            return None

        with patch("src.tts_engine._asyncio_run_with_retry", side_effect=fake_retry), \
                patch("src.tts_engine.combine_audio_files"):
            text_to_speech(
                text,
                str(tmp_path / "out.mp3"),
                config=TTSConfig(language="fi", normalize_text=False),
            )

        n_chunks = len(split_text_into_chunks(text))
        assert n_chunks > 1, "test text must split into multiple chunks"
        assert len(timeouts) == n_chunks, "one retry budget per chunk, not per book"
        assert all(t >= _EDGE_CHUNK_TIMEOUT for t in timeouts), (
            f"per-chunk budget must cover the inner {_EDGE_CHUNK_TIMEOUT}s "
            f"timeout, got {timeouts}"
        )


# ---------------------------------------------------------------------------
# Edge-TTS chunk timeout
# ---------------------------------------------------------------------------


class TestEdgeChunkTimeout:
    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self) -> None:
        """Edge-TTS chunk synthesis should raise RuntimeError on timeout."""
        import sys

        # Create a coroutine that never completes — simulates a network stall.
        async def hang_forever(*_args, **_kwargs):
            await asyncio.sleep(9999)

        mock_communicate = MagicMock()
        mock_communicate.save = hang_forever

        mock_edge_module = MagicMock()
        mock_edge_module.Communicate.return_value = mock_communicate

        # Inject a fake edge_tts module so the lazy import inside
        # _synthesize_chunk picks it up from sys.modules.
        with patch.dict(sys.modules, {"edge_tts": mock_edge_module}), \
             patch("src.tts_engine._EDGE_CHUNK_TIMEOUT", 0.1):
            with pytest.raises(RuntimeError, match="timed out"):
                await _synthesize_chunk("test", "voice", "+0%", "+0%", "/tmp/out.mp3")


class TestRmtreeWithRetry:
    """The temp-dir cleanup retries transient Windows file-locks. It must NOT
    pass ignore_errors=True (that swallowed the OSError and made the retry dead
    code, leaking the dir on the first lock)."""

    def test_succeeds_after_transient_lock(self, monkeypatch) -> None:
        from src import tts_engine

        calls = {"n": 0}

        def _flaky(path, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("locked")

        slept = {"n": 0}
        monkeypatch.setattr("shutil.rmtree", _flaky)
        monkeypatch.setattr("time.sleep", lambda *_a, **_k: slept.__setitem__("n", slept["n"] + 1))

        assert tts_engine._rmtree_with_retry("/fake/tmp", attempts=3, delay=0.01) is True
        assert calls["n"] == 3, "must keep retrying until rmtree succeeds"
        assert slept["n"] == 2, "sleeps between failures, not after the success"

    def test_gives_up_after_all_attempts(self, monkeypatch) -> None:
        from src import tts_engine

        def _always_fail(path, **kw):
            raise OSError("locked")

        monkeypatch.setattr("shutil.rmtree", _always_fail)
        monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)

        # Best-effort: returns False rather than raising out of a finally block.
        assert tts_engine._rmtree_with_retry("/fake/tmp", attempts=3, delay=0.01) is False
