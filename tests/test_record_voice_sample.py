"""Unit tests for scripts/record_voice_sample.py.

Only the preflight / analysis logic is exercised here. Audio hardware
is mocked by writing synthetic WAV fixtures to a temp directory and
passing them to the public API. No ffmpeg, no microphone, no
Chatterbox — this test suite runs on any machine.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

wavfile = pytest.importorskip("scipy.io.wavfile", reason="scipy not installed")

# ---------------------------------------------------------------------------
# Load the script as a module despite its non-package path.
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "record_voice_sample.py"
)
_spec = importlib.util.spec_from_file_location(
    "record_voice_sample", str(_SCRIPT_PATH)
)
assert _spec is not None
rvs = importlib.util.module_from_spec(_spec)
sys.modules["record_voice_sample"] = rvs
assert _spec.loader is not None
_spec.loader.exec_module(rvs)


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _write_wav(
    path: Path,
    samples: np.ndarray,
    sample_rate: int = 22050,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.dtype == np.float32 or samples.dtype == np.float64:
        int16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    else:
        int16 = samples.astype(np.int16)
    wavfile.write(str(path), sample_rate, int16)
    return path


def _speech_like(
    sample_rate: int,
    duration_s: float,
    amplitude: float = 0.3,
    noise: float = 0.005,
) -> np.ndarray:
    """Generate a mono synthetic 'speech' clip — sum of a 200 Hz tone
    and a 440 Hz tone modulated by a slow envelope, plus a small
    amount of noise. Good enough to pass SNR and RMS thresholds."""
    t = np.arange(int(duration_s * sample_rate)) / sample_rate
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 1.5 * t)
    voice = (
        np.sin(2 * np.pi * 200 * t) + 0.7 * np.sin(2 * np.pi * 440 * t)
    ) * envelope * amplitude
    voice += np.random.default_rng(0).normal(0, noise, size=t.shape)
    return voice.astype(np.float32)


# ---------------------------------------------------------------------------
# _to_mono_float
# ---------------------------------------------------------------------------


class TestToMonoFloat:
    def test_int16_scaled_to_unit_range(self) -> None:
        samples = np.array([32767, -32768, 0, 16384], dtype=np.int16)
        mono = rvs._to_mono_float(samples)
        assert mono.dtype == np.float32
        assert abs(mono[0] - 1.0) < 1e-3
        assert abs(mono[1] + 1.0) < 1e-3
        assert mono[2] == 0.0

    def test_stereo_downmixed_by_averaging(self) -> None:
        stereo = np.array([[100, 200], [0, 0], [-100, -200]], dtype=np.int16)
        mono = rvs._to_mono_float(stereo)
        assert mono.shape == (3,)
        assert abs(mono[0] - (150 / 32768.0)) < 1e-3

    def test_float_input_normalized_if_above_unit(self) -> None:
        samples = np.array([2.0, -4.0, 1.0], dtype=np.float64)
        mono = rvs._to_mono_float(samples)
        assert np.max(np.abs(mono)) <= 1.001


# ---------------------------------------------------------------------------
# _rms_dbfs
# ---------------------------------------------------------------------------


class TestRmsDbfs:
    def test_silence_is_negative_infinity(self) -> None:
        assert rvs._rms_dbfs(np.zeros(1000, dtype=np.float32)) == float("-inf")

    def test_full_scale_sine_is_around_minus_three_db(self) -> None:
        # Sine at amplitude 1.0 has RMS ≈ 0.707 ≈ -3 dBFS
        t = np.arange(22050) / 22050
        sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        assert -3.5 < rvs._rms_dbfs(sine) < -2.5

    def test_quiet_signal_is_very_negative(self) -> None:
        assert rvs._rms_dbfs(np.full(1000, 0.001, dtype=np.float32)) < -50


# ---------------------------------------------------------------------------
# _estimate_snr_db
# ---------------------------------------------------------------------------


class TestEstimateSnrDb:
    def test_pure_tone_has_low_snr_by_heuristic(self) -> None:
        # A pure sine has no silence frames → heuristic sees low contrast
        # between the "bottom 10%" and "top 30%" power. The function is
        # designed for real speech with pauses. Pure tone is a known
        # under-estimate case; just sanity check it does not crash.
        t = np.arange(22050) / 22050
        sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        snr = rvs._estimate_snr_db(sine, 22050)
        assert np.isfinite(snr)

    def test_speech_like_signal_reports_high_snr(self) -> None:
        signal = _speech_like(22050, duration_s=10.0, amplitude=0.3,
                              noise=0.002)
        snr = rvs._estimate_snr_db(signal, 22050)
        assert snr > rvs.MIN_SNR_DB

    def test_very_noisy_signal_reports_low_snr(self) -> None:
        rng = np.random.default_rng(1)
        noise = rng.normal(0, 0.3, size=22050 * 10).astype(np.float32)
        snr = rvs._estimate_snr_db(noise, 22050)
        assert snr < rvs.MIN_SNR_DB


# ---------------------------------------------------------------------------
# _auto_trim_silence
# ---------------------------------------------------------------------------


class TestAutoTrimSilence:
    def test_leading_and_trailing_silence_removed(self) -> None:
        sr = 22050
        voice = _speech_like(sr, duration_s=2.0, amplitude=0.4)
        lead = np.zeros(sr, dtype=np.float32)
        trail = np.zeros(sr, dtype=np.float32)
        full = np.concatenate([lead, voice, trail])
        trimmed = rvs._auto_trim_silence(full, sr)
        assert trimmed.size < full.size
        # Speech portion (with pad) is kept.
        assert trimmed.size > voice.size * 0.8

    def test_all_silence_returned_unchanged(self) -> None:
        # If no frame crosses the threshold, return the original clip
        # rather than an empty array.
        silence = np.zeros(22050, dtype=np.float32)
        trimmed = rvs._auto_trim_silence(silence, 22050)
        assert trimmed.size == silence.size

    def test_pad_preserves_edge_samples(self) -> None:
        sr = 22050
        voice = _speech_like(sr, duration_s=1.0, amplitude=0.4)
        trimmed = rvs._auto_trim_silence(voice, sr)
        # 100 ms pad on each side means the clip is not aggressively
        # clipped — length should be close to original.
        assert trimmed.size >= int(0.9 * voice.size)


# ---------------------------------------------------------------------------
# preflight_clip — end-to-end with synthetic WAVs
# ---------------------------------------------------------------------------


class TestPreflightClip:
    def test_clean_speech_like_clip_passes(self, tmp_path: Path) -> None:
        clip = tmp_path / "clean.wav"
        _write_wav(clip, _speech_like(22050, 10.0, 0.3, 0.002), 22050)
        result = rvs.preflight_clip(clip)
        assert result.passed, result.render()

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        result = rvs.preflight_clip(tmp_path / "nope.wav")
        assert not result.passed
        assert any(c.name == "exists" for c in result.checks)

    def test_sample_rate_below_threshold_fails(
        self, tmp_path: Path
    ) -> None:
        clip = tmp_path / "lowrate.wav"
        _write_wav(clip, _speech_like(8000, 10.0), sample_rate=8000)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "sample rate" and not c.passed for c in result.checks
        )

    def test_too_short_clip_fails(self, tmp_path: Path) -> None:
        clip = tmp_path / "short.wav"
        _write_wav(clip, _speech_like(22050, 2.0), 22050)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "duration" and not c.passed for c in result.checks
        )

    def test_too_long_clip_fails(self, tmp_path: Path) -> None:
        clip = tmp_path / "long.wav"
        _write_wav(clip, _speech_like(22050, 40.0), 22050)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "duration" and not c.passed for c in result.checks
        )

    def test_clipped_clip_fails(self, tmp_path: Path) -> None:
        # Fill the clip with ±full-scale values → clipping everywhere.
        sr = 22050
        samples = np.full(sr * 10, 1.0, dtype=np.float32)
        clip = tmp_path / "clipped.wav"
        _write_wav(clip, samples, sr)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "clipping" and not c.passed for c in result.checks
        )

    def test_silent_clip_fails_loudness(self, tmp_path: Path) -> None:
        sr = 22050
        silence = np.zeros(sr * 10, dtype=np.float32)
        clip = tmp_path / "silent.wav"
        _write_wav(clip, silence, sr)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "loudness" and not c.passed for c in result.checks
        )

    def test_noisy_clip_fails_snr(self, tmp_path: Path) -> None:
        sr = 22050
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 0.3, size=sr * 10).astype(np.float32)
        clip = tmp_path / "noisy.wav"
        _write_wav(clip, noise, sr)
        result = rvs.preflight_clip(clip)
        assert not result.passed
        assert any(
            c.name == "SNR (estimate)" and not c.passed
            for c in result.checks
        )

    def test_unreadable_wav_fails_cleanly(self, tmp_path: Path) -> None:
        # Write garbage bytes to a .wav path.
        bad = tmp_path / "broken.wav"
        bad.write_bytes(b"not a wav file")
        result = rvs.preflight_clip(bad)
        assert not result.passed
        assert any(c.name == "read" for c in result.checks)


# ---------------------------------------------------------------------------
# apply_trim — round-trip with a fixture
# ---------------------------------------------------------------------------


class TestApplyTrim:
    def test_trim_writes_a_shorter_clip_with_speech_preserved(
        self, tmp_path: Path
    ) -> None:
        sr = 22050
        voice = _speech_like(sr, duration_s=2.0, amplitude=0.4)
        silence = np.zeros(sr, dtype=np.float32)
        full = np.concatenate([silence, voice, silence])
        in_path = _write_wav(tmp_path / "padded.wav", full, sr)
        out_path = tmp_path / "trimmed.wav"
        rvs.apply_trim(in_path, out_path)
        read_sr, read_samples = wavfile.read(str(out_path))
        assert read_sr == sr
        # Trimmed size should be near the original speech size (+ pad).
        assert 0.8 * voice.size < read_samples.size < 1.5 * voice.size


# ---------------------------------------------------------------------------
# CLI parser — surface-level smoke
# ---------------------------------------------------------------------------


class TestCli:
    def test_list_devices_flag(self) -> None:
        args = rvs.parse_args(["--list-devices"])
        assert args.list_devices is True

    def test_synthesize_text_stored(self) -> None:
        args = rvs.parse_args(["--synthesize", "Hei maailma"])
        assert args.synthesize == "Hei maailma"
        assert args.synthesize_file is None

    def test_tts_device_choices(self) -> None:
        args = rvs.parse_args(["--tts-device", "cuda"])
        assert args.tts_device == "cuda"

    def test_default_chunk_chars_is_thirty_five(self) -> None:
        args = rvs.parse_args([])
        assert args.chunk_chars == 35

    def test_invalid_tts_device_rejected(self) -> None:
        with pytest.raises(SystemExit):
            rvs.parse_args(["--tts-device", "tpu"])
