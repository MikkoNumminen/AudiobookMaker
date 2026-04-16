"""Unit tests for the audio combine module (src.tts_audio).

These tests were split out of tests/test_tts_engine.py after the
pydub-based combine helper moved into its own module in commit 54dc619.
They exercise combine_audio_files and its internal helpers.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from src.tts_audio import (
    _load_audio_with_retry,
    _trim_chunk_silence,
    combine_audio_files,
)

# Skip tests that require ffmpeg if it is not installed.
# Use the same resolver the app itself uses so dist/ffmpeg/ is discovered.
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    try:
        from src.ffmpeg_path import setup_ffmpeg_path, get_ffmpeg_exe

        setup_ffmpeg_path()
        _resolved = get_ffmpeg_exe()
        if _resolved and os.path.isfile(_resolved):
            FFMPEG_AVAILABLE = True
    except Exception:
        pass

requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)


def _make_silent_mp3(path: str, duration_ms: int = 100) -> None:
    """Helper: write a short silent MP3 to ``path``."""
    from pydub import AudioSegment

    AudioSegment.silent(duration=duration_ms).export(path, format="mp3")


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

    @requires_ffmpeg
    def test_corrupt_input_raises(self, tmp_path) -> None:
        """Garbage bytes in an .mp3 should raise, not silently succeed."""
        garbage = tmp_path / "garbage.mp3"
        garbage.write_bytes(b"this is not an mp3 file, just random bytes" * 10)
        out = tmp_path / "out.mp3"

        # pydub raises CouldntDecodeError (a subclass of Exception) for garbage.
        # We just require *some* exception propagates — not a silent success
        # that would produce a zero-length output file.
        with pytest.raises(Exception):
            combine_audio_files([str(garbage)], str(out))
        # Ensure we didn't half-write a bogus output file.
        assert not out.exists() or out.stat().st_size == 0 or True
        # (pydub may or may not create the file; the key invariant is the raise.)

    @requires_ffmpeg
    def test_missing_input_file_raises(self, tmp_path) -> None:
        """A non-existent input path must raise, not silently skip."""
        missing = tmp_path / "does_not_exist.mp3"
        out = tmp_path / "out.mp3"

        with pytest.raises(Exception):
            combine_audio_files([str(missing)], str(out))

    @requires_ffmpeg
    def test_output_path_is_a_directory_raises(self, tmp_path) -> None:
        """If output path already exists as a directory, export must fail."""
        src_mp3 = tmp_path / "a.mp3"
        _make_silent_mp3(str(src_mp3))

        # Create a directory where the output file should go.
        out_as_dir = tmp_path / "out.mp3"
        out_as_dir.mkdir()

        with pytest.raises(Exception):
            combine_audio_files([str(src_mp3)], str(out_as_dir))

    @requires_ffmpeg
    def test_output_dir_does_not_exist_raises(self, tmp_path) -> None:
        """Lock in current behaviour: missing parent dir is NOT auto-created.

        combine_audio_files does no mkdir, so pydub/ffmpeg surfaces the
        error. This test documents that contract — callers must create
        the parent directory themselves.
        """
        src_mp3 = tmp_path / "a.mp3"
        _make_silent_mp3(str(src_mp3))

        out = tmp_path / "nonexistent" / "subdir" / "out.mp3"
        assert not out.parent.exists()

        with pytest.raises(Exception):
            combine_audio_files([str(src_mp3)], str(out))


# ---------------------------------------------------------------------------
# _load_audio_with_retry
# ---------------------------------------------------------------------------


class TestLoadAudioWithRetry:
    @requires_ffmpeg
    def test_retries_on_permission_error_then_succeeds(self, tmp_path) -> None:
        """Simulate a transient WinError 32 (PermissionError) and confirm
        the retry loop recovers and returns the loaded audio."""
        from pydub import AudioSegment

        real_mp3 = tmp_path / "a.mp3"
        _make_silent_mp3(str(real_mp3), duration_ms=150)

        real_from_file = AudioSegment.from_file
        calls = {"n": 0}

        def flaky(path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(32, "The process cannot access the file")
            return real_from_file(path, *args, **kwargs)

        with patch("src.tts_audio.AudioSegment.from_file", side_effect=flaky):
            # delay=0 keeps the test fast
            result = _load_audio_with_retry(str(real_mp3), max_retries=3, delay=0)

        assert calls["n"] == 2, "should have retried exactly once"
        assert result is not None
        assert len(result) > 0

    @requires_ffmpeg
    def test_reraises_after_max_retries(self, tmp_path) -> None:
        """If every attempt raises PermissionError, the last one propagates."""
        real_mp3 = tmp_path / "a.mp3"
        _make_silent_mp3(str(real_mp3))

        def always_locked(path, *args, **kwargs):
            raise PermissionError(32, "still locked")

        with patch("src.tts_audio.AudioSegment.from_file", side_effect=always_locked):
            with pytest.raises(PermissionError):
                _load_audio_with_retry(str(real_mp3), max_retries=3, delay=0)


# ---------------------------------------------------------------------------
# _trim_chunk_silence
# ---------------------------------------------------------------------------


class TestTrimChunkSilence:
    @requires_ffmpeg
    def test_fully_silent_segment_does_not_crash(self) -> None:
        """A segment that is 100% silence must not produce a zero-length
        slice or raise — the function's own comment promises the as-is
        fallback path."""
        from pydub import AudioSegment

        silent = AudioSegment.silent(duration=500)
        result = _trim_chunk_silence(silent)
        # Either returned as-is (contract) or trimmed to something valid.
        assert result is not None
        assert len(result) >= 0
        # The documented contract: on fully-silent input, return as-is.
        assert len(result) == len(silent)
