"""Unit tests for src/voice_recorder.py.

Only the parts that don't require a live microphone, ffmpeg, or an
actual Tk display are covered here: the stdlib WAV reader's sample-
width guard, and the subprocess-lifecycle logic around Popen failures
and done-callback cleanup.  GUI construction itself is not exercised —
the dialog is driven by stubbing just the handful of Tk attributes
each unit under test touches.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from src import voice_recorder as vr


# ---------------------------------------------------------------------------
# Synthetic WAV fixtures
# ---------------------------------------------------------------------------


def _write_wav(path: Path, samples: list[int], sampwidth: int = 2,
               rate: int = 22050, n_channels: int = 1) -> Path:
    """Write a raw PCM WAV with the requested sample width."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        if sampwidth == 2:
            raw = struct.pack(f"<{len(samples)}h", *samples)
        elif sampwidth == 1:
            # 8-bit WAVs are unsigned per the spec
            raw = bytes((s + 128) & 0xFF for s in samples)
        elif sampwidth == 4:
            raw = struct.pack(f"<{len(samples)}i", *samples)
        else:
            raise AssertionError(f"unsupported sampwidth for fixture: {sampwidth}")
        wf.writeframes(raw)
    return path


# ---------------------------------------------------------------------------
# _read_wav_samples — sample-width guard
# ---------------------------------------------------------------------------


class TestReadWavSamples:
    def test_accepts_16_bit_pcm(self, tmp_path: Path) -> None:
        path = _write_wav(tmp_path / "ok.wav", [0, 100, -100, 200])
        rate, n_ch, samples = vr._read_wav_samples(path)
        assert rate == 22050
        assert n_ch == 1
        assert samples == [0, 100, -100, 200]

    def test_rejects_8_bit_wav(self, tmp_path: Path) -> None:
        path = _write_wav(tmp_path / "eight.wav", [0, 50, -50], sampwidth=1)
        with pytest.raises(ValueError, match="8 bits"):
            vr._read_wav_samples(path)

    def test_rejects_32_bit_wav(self, tmp_path: Path) -> None:
        path = _write_wav(tmp_path / "thirty_two.wav", [0, 1000, -1000],
                          sampwidth=4)
        with pytest.raises(ValueError, match="32 bits"):
            vr._read_wav_samples(path)

    def test_error_mentions_expected_format(self, tmp_path: Path) -> None:
        path = _write_wav(tmp_path / "eight.wav", [0], sampwidth=1)
        with pytest.raises(ValueError, match="16-bit PCM"):
            vr._read_wav_samples(path)
