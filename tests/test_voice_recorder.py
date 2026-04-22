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
import types
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# Dialog stub for subprocess-lifecycle tests
# ---------------------------------------------------------------------------


def _make_dialog_stub() -> Any:
    """Return an object that quacks enough like VoiceRecorderDialog for the
    subprocess-lifecycle unit tests.  Avoids any real Tk construction."""
    stub = types.SimpleNamespace()
    stub._lang = "en"
    stub._recording = False
    stub._rec_process = None
    stub._play_process = None
    stub._wav_path = None
    stub._rec_start = 0.0
    stub._dlg = MagicMock()
    stub._rec_btn = MagicMock()
    stub._status_var = MagicMock()
    stub._dur_var = MagicMock()
    stub._progress = MagicMock()
    stub._play_btn = MagicMock()
    stub._rerecord_btn = MagicMock()
    stub._use_btn = MagicMock()
    stub._dev_combo = MagicMock()
    stub._dev_var = MagicMock()
    stub._dev_var.get.return_value = "(default)"
    stub._check_widgets = []

    # Bind the real helpers
    stub._s = lambda key: vr._STRINGS["en"].get(key, key)
    stub._selected_device_spec = (
        lambda: vr.VoiceRecorderDialog._selected_device_spec(stub)
    )
    stub._reset_checks = lambda: None
    stub._tick_progress = lambda: None
    return stub


# ---------------------------------------------------------------------------
# _start_record — Popen failure and state ordering
# ---------------------------------------------------------------------------


class TestStartRecordPopenFailure:
    def test_popen_failure_leaves_state_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Popen raises, _recording must NOT be True and _rec_process
        must be None — otherwise _tick_progress keeps scheduling forever."""
        monkeypatch.setattr(vr, "VOICE_SAMPLES_DIR", tmp_path / "voice_samples")
        monkeypatch.setattr(vr, "_ffmpeg_binary", lambda: "ffmpeg")

        def boom(*_a: Any, **_kw: Any) -> None:
            raise OSError("simulated Popen failure")

        monkeypatch.setattr(vr.subprocess, "Popen", boom)
        errors: list[tuple[str, str]] = []
        monkeypatch.setattr(
            vr.messagebox, "showerror",
            lambda title, msg, **_kw: errors.append((title, msg)),
        )

        stub = _make_dialog_stub()
        vr.VoiceRecorderDialog._start_record(stub)

        assert stub._recording is False, (
            "_recording must stay False when Popen fails — otherwise "
            "_tick_progress keeps scheduling forever"
        )
        assert stub._rec_process is None
        assert stub._wav_path is None
        assert errors, "user must see an error dialog on Popen failure"

    def test_popen_failure_clears_stale_rec_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale handle from a previous session must not survive a
        Popen failure in the next attempt."""
        monkeypatch.setattr(vr, "VOICE_SAMPLES_DIR", tmp_path / "voice_samples")
        monkeypatch.setattr(vr, "_ffmpeg_binary", lambda: "ffmpeg")
        monkeypatch.setattr(vr.subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
        monkeypatch.setattr(vr.messagebox, "showerror",
                            lambda *a, **k: None)

        stub = _make_dialog_stub()
        stub._rec_process = MagicMock()  # stale handle

        vr.VoiceRecorderDialog._start_record(stub)
        assert stub._rec_process is None

    def test_rec_process_set_before_recording_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When Popen succeeds, _rec_process must be populated and
        _recording flipped to True — the timer depends on both."""
        monkeypatch.setattr(vr, "VOICE_SAMPLES_DIR", tmp_path / "voice_samples")
        monkeypatch.setattr(vr, "_ffmpeg_binary", lambda: "ffmpeg")

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        monkeypatch.setattr(vr.subprocess, "Popen",
                            lambda *a, **k: fake_proc)

        stub = _make_dialog_stub()
        vr.VoiceRecorderDialog._start_record(stub)

        assert stub._rec_process is fake_proc
        assert stub._recording is True
