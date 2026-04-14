"""Unit tests for the audio combine module (src.tts_audio).

These tests were split out of tests/test_tts_engine.py after the
pydub-based combine helper moved into its own module in commit 54dc619.
They exercise combine_audio_files.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from src.tts_audio import combine_audio_files

# Skip tests that require ffmpeg if it is not installed
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)


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
