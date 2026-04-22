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
    stub._run_checks = lambda: None
    # Release helpers are real methods bound to the stub so _on_*_done
    # can invoke them via the try/finally block.
    stub._release_rec_process = (
        lambda: vr.VoiceRecorderDialog._release_rec_process(stub)
    )
    stub._release_play_process = (
        lambda: vr.VoiceRecorderDialog._release_play_process(stub)
    )
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


# ---------------------------------------------------------------------------
# _on_record_done — cleanup on exception
# ---------------------------------------------------------------------------


class TestOnRecordDoneCleanup:
    def test_rec_process_released_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the done callback fires, _rec_process must be None —
        holding a dead subprocess handle wastes a file descriptor and
        confuses later logic that tests ``self._rec_process``."""
        wav = _write_wav(tmp_path / "rec.wav", [0] * 50)

        stub = _make_dialog_stub()
        stub._wav_path = wav
        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0  # already exited
        stub._rec_process = fake_proc
        # Stub the preflight thread so nothing actually runs
        monkeypatch.setattr(vr.threading, "Thread",
                            lambda *_a, **_kw: MagicMock(start=lambda: None))

        vr.VoiceRecorderDialog._on_record_done(stub)

        assert stub._recording is False
        assert stub._rec_process is None

    def test_rec_process_released_on_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If something inside _on_record_done blows up (e.g. a widget
        call fails), the process handle must still get released via the
        try/finally guard."""
        wav = _write_wav(tmp_path / "rec.wav", [0] * 50)

        stub = _make_dialog_stub()
        stub._wav_path = wav
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # still alive
        stub._rec_process = fake_proc
        # Arrange for a widget call to raise so we can verify the finally
        # branch still fires.
        stub._status_var.set.side_effect = RuntimeError("widget gone")
        monkeypatch.setattr(vr.threading, "Thread",
                            lambda *_a, **_kw: MagicMock(start=lambda: None))

        with pytest.raises(RuntimeError):
            vr.VoiceRecorderDialog._on_record_done(stub)

        fake_proc.terminate.assert_called_once()
        assert stub._rec_process is None

    def test_release_is_idempotent_with_no_process(self) -> None:
        """Calling the release helper with no handle is a no-op."""
        stub = _make_dialog_stub()
        stub._rec_process = None
        vr.VoiceRecorderDialog._release_rec_process(stub)
        assert stub._rec_process is None


# ---------------------------------------------------------------------------
# _on_play_done — cleanup parity with record side
# ---------------------------------------------------------------------------


class TestOnPlayDoneCleanup:
    def test_play_process_released_when_already_exited(self) -> None:
        """A finished ffplay process must be dropped without trying to
        terminate() it again."""
        stub = _make_dialog_stub()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0  # already exited
        stub._play_process = fake_proc

        vr.VoiceRecorderDialog._on_play_done(stub)

        fake_proc.terminate.assert_not_called()
        assert stub._play_process is None

    def test_play_process_terminated_when_still_alive(self) -> None:
        """If ffplay is still alive when the done callback fires (e.g. a
        hard cancel), terminate + null the handle."""
        stub = _make_dialog_stub()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # still alive
        stub._play_process = fake_proc

        vr.VoiceRecorderDialog._on_play_done(stub)

        fake_proc.terminate.assert_called_once()
        assert stub._play_process is None

    def test_release_is_idempotent_with_no_process(self) -> None:
        """Calling the release helper with no handle is a no-op."""
        stub = _make_dialog_stub()
        stub._play_process = None
        vr.VoiceRecorderDialog._release_play_process(stub)
        assert stub._play_process is None
