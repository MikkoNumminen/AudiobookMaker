"""End-to-end synthesis test using the REAL Piper engine.

Unlike ``test_integration.py`` which uses a stub engine, this test
verifies the actual Piper → combine_audio_files pipeline produces a
playable MP3. Catches regressions in the voice loader, text
preprocessor, WAV chunking, or ffmpeg muxing step.

Skips gracefully if piper-tts, ffmpeg, or a cached Piper voice model
is not available in the environment.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from src.tts_piper import PiperTTSEngine, _PIPER_VOICES


def _find_cached_voice() -> tuple[Path, str] | None:
    """Locate any cached Piper voice anywhere under the cache root.

    Returns (directory_containing_files, voice_id) or None. Searches
    recursively because the adapter's flat layout and the auto-
    downloader's per-voice subdir layout both exist in the wild.
    """
    root = Path.home() / ".audiobookmaker" / "piper_voices"
    if not root.exists():
        return None
    for spec in _PIPER_VOICES:
        for onnx in root.rglob(spec.onnx_filename):
            json_path = onnx.with_name(spec.json_filename)
            if json_path.exists():
                return (onnx.parent, spec.id)
    return None


@pytest.mark.slow
def test_piper_real_synthesis_produces_playable_mp3(tmp_path, monkeypatch) -> None:
    try:
        import piper  # noqa: F401
    except ImportError:
        pytest.skip("piper-tts not installed")

    from src.ffmpeg_path import setup_ffmpeg_path, get_ffmpeg_exe
    setup_ffmpeg_path()
    if not get_ffmpeg_exe():
        pytest.skip("ffmpeg not available")

    found = _find_cached_voice()
    if found is None:
        pytest.skip(
            "No Piper voice cached under ~/.audiobookmaker/piper_voices; "
            "download one to enable this E2E test."
        )
    voice_dir, voice_id = found

    # Point the adapter at the directory containing the voice files.
    monkeypatch.setattr("src.tts_piper._cache_dir", lambda: voice_dir)

    out = tmp_path / "out.mp3"
    spec = next(s for s in _PIPER_VOICES if s.id == voice_id)
    PiperTTSEngine().synthesize(
        "This is a short test. A second sentence follows.",
        str(out),
        spec.id,
        spec.language,
    )

    assert out.exists(), "Piper produced no output file"
    size = os.path.getsize(out)
    assert size > 1024, f"Output MP3 suspiciously small: {size} bytes"

    with open(out, "rb") as f:
        head = f.read(3)
    is_id3 = head[:3] == b"ID3"
    is_sync = (
        len(head) >= 2
        and head[0] == 0xFF
        and head[1] in (0xFB, 0xFA, 0xF3, 0xE3)
    )
    assert is_id3 or is_sync, f"Not an MP3: first bytes {head!r}"

    from pydub import AudioSegment
    audio = AudioSegment.from_mp3(str(out))
    assert len(audio) > 0, "MP3 has zero duration"
