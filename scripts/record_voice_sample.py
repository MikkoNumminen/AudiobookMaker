"""Record a voice sample, run preflight quality checks, optionally clone.

The end-to-end "record your own voice, hear it read text" tool. Runs on
Mac and Windows (same arguments, different ffmpeg input format picked
automatically). Designed so the user can:

1. Record a ~12 s clip of themselves speaking Finnish via ffmpeg.
2. Have the clip validated against the v7 quality floor (sample rate,
   clipping, SNR, duration, loudness, leading/trailing silence).
3. Immediately synthesize a test sentence in their cloned voice by
   shelling out to ``dev_chatterbox_fi.py`` with ``--ref-audio`` set
   to the new clip.

The tool does NOT pull in any new Python dependencies. It uses ffmpeg
(already bundled in the project's build pipeline) for recording and
playback, and the standard library + numpy + scipy (already in the
.venv) for the preflight analysis. The synthesis leg runs in the
``.venv-chatterbox`` virtualenv in a subprocess.

Quick usage::

    # Record and immediately clone a test sentence on Mac CPU
    python scripts/record_voice_sample.py \\
        --synthesize "Terve. Tämä on minun ääneni testi."

    # List input devices so you can pick the right mic
    python scripts/record_voice_sample.py --list-devices

    # Reuse an existing recording (skip recording step)
    python scripts/record_voice_sample.py \\
        --use-existing voice_samples/mikko_001.wav \\
        --synthesize "Uusi testi samalla äänellä."

    # Production run on the Windows GPU machine
    python scripts\\record_voice_sample.py \\
        --synthesize-file chapter_01.txt \\
        --tts-device cuda
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import platform as _plat
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.io import wavfile

# ---------------------------------------------------------------------------
# Constants — v7 quality floor thresholds
# ---------------------------------------------------------------------------
#
# These numbers are the minimum a reference clip must hit before we will
# feed it to Chatterbox-Finnish. They come from the audiobook rubric and
# the Finnish-NLP maintainers' own recommendations on reference clip
# quality. Tighter than "can Chatterbox ingest it" but looser than
# "studio-grade" — aimed at a quiet room + built-in Mac mic.

MIN_SAMPLE_RATE_HZ = 16000
"""Below 16 kHz Finnish sibilants (/s/, /ʃ/) collapse into noise and
Chatterbox's speaker encoder loses timbre detail."""

MIN_DURATION_S = 5.0
"""Below 5 s the speaker encoder has too little material to build a
stable embedding; voice character drifts heavily."""

MAX_DURATION_S = 30.0
"""Above 30 s Chatterbox only uses the leading slice, so anything
longer is wasted. We cap at 30 s to save disk and clarify intent."""

MIN_SNR_DB = 15.0
"""Signal-to-noise floor. Below 15 dB the clip is audibly noisy and
the noise floor gets cloned into the output alongside your voice."""

MIN_RMS_DBFS = -35.0
"""Minimum loudness in dBFS. Below this (quieter than a whisper in a
quiet room) the encoder has trouble distinguishing voice from noise."""

MAX_RMS_DBFS = -10.0
"""Maximum loudness. Above this the recording is either too close to
the mic (pops and plosives) or approaching clipping."""

MAX_CLIP_RATIO = 0.0005
"""At most 0.05% of samples may be at ±full-scale. Above this the
recording has audible clipping distortion and the clip should be
rejected."""

DEFAULT_SAMPLE_RATE_HZ = 22050
DEFAULT_DURATION_S = 12.0
DEFAULT_VOICE_SAMPLES_DIR = Path("voice_samples")
DEFAULT_SYNTH_OUTPUT_DIR = Path("out")

# Recording script reused from the main AudiobookMaker pipeline.
CHATTERBOX_SCRIPT = Path("dev_chatterbox_fi.py")
CHATTERBOX_VENV_PY = Path(".venv-chatterbox/bin/python")
CHATTERBOX_VENV_PY_WINDOWS = Path(".venv-chatterbox/Scripts/python.exe")


# ---------------------------------------------------------------------------
# Preflight data types
# ---------------------------------------------------------------------------


@dataclass
class PreflightCheck:
    """One quality check against a reference clip."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        mark = "✓" if self.passed else "✗"
        return f"  {mark} {self.name}: {self.detail}"


@dataclass
class PreflightResult:
    """Aggregate preflight outcome for a clip."""

    path: Path
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(PreflightCheck(name, passed, detail))

    def render(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [f"Preflight {verdict} for {self.path}"]
        lines += [str(c) for c in self.checks]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preflight analysis — importable, testable without audio hardware
# ---------------------------------------------------------------------------


def _to_mono_float(samples: np.ndarray) -> np.ndarray:
    """Return a 1-D float32 array in the range [-1, 1].

    Accepts int16, int32, float32, float64, mono or stereo. Stereo is
    downmixed by averaging channels. Int formats are scaled to the
    [-1, 1] float range using the original dtype's full-scale value;
    float formats are only renormalized if the peak magnitude exceeds
    1.0 (which should never happen for a well-formed WAV but can
    occur if the caller hands us a raw signal).
    """
    original_kind = samples.dtype.kind  # 'i' = int, 'f' = float, 'u' = uint
    if samples.ndim == 2:
        mono = samples.astype(np.float64).mean(axis=1)
    else:
        mono = samples.astype(np.float64)
    if original_kind == "f":
        peak = float(np.max(np.abs(mono))) or 1.0
        if peak > 1.001:
            mono = mono / peak
    elif original_kind == "u":
        # Unsigned PCM (e.g. 8-bit) is centred on the midpoint.
        info = np.iinfo(samples.dtype)
        midpoint = (info.max + info.min + 1) / 2.0
        mono = (mono - midpoint) / max(info.max - midpoint, 1)
    else:
        # Signed integer PCM — scale by the dtype's positive max.
        info = np.iinfo(samples.dtype)
        mono = mono / float(info.max + 1)
    return mono.astype(np.float32)


def _rms_dbfs(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms <= 0:
        return -np.inf
    return 20.0 * np.log10(rms)


def _estimate_snr_db(samples: np.ndarray, sample_rate: int) -> float:
    """Rough speech-vs-silence power ratio estimate.

    Splits the clip into 30 ms frames, sorts by power, uses the bottom
    10% as the noise estimate and the top 30% as the signal estimate.
    This is NOT a calibrated SNR — it is a "is there more speech than
    noise?" heuristic. Real SNR estimation needs a VAD plus a long
    clean-noise segment, which we do not have here. Still sufficient
    to reject obviously noisy clips.
    """
    frame_len = max(1, int(0.03 * sample_rate))
    if samples.size < frame_len * 10:
        # Clip too short to estimate anything reliable.
        return 30.0
    n_frames = samples.size // frame_len
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    powers = (frames.astype(np.float64) ** 2).mean(axis=1)
    powers = np.sort(powers)
    noise_power = max(float(np.mean(powers[: max(1, n_frames // 10)])), 1e-12)
    signal_power = max(
        float(np.mean(powers[-max(1, n_frames * 3 // 10) :])), 1e-12
    )
    return 10.0 * np.log10(signal_power / noise_power)


def _auto_trim_silence(
    samples: np.ndarray, sample_rate: int, threshold_db: float = -40.0
) -> np.ndarray:
    """Trim leading/trailing silence below ``threshold_db`` dBFS.

    Uses a simple frame-based power threshold. Keeps a 100 ms pad on
    each side so sentence-initial breath and sentence-final vowel
    decay survive intact — Finnish especially hates aggressive tail
    trimming (see Silero VAD tuning notes in dev_chatterbox_fi.py).
    """
    frame_len = max(1, int(0.03 * sample_rate))
    if samples.size < frame_len * 5:
        return samples
    n_frames = samples.size // frame_len
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    with np.errstate(divide="ignore"):
        frame_db = 10.0 * np.log10(
            np.maximum((frames.astype(np.float64) ** 2).mean(axis=1), 1e-12)
        )
    above = np.where(frame_db > threshold_db)[0]
    if above.size == 0:
        return samples
    pad_frames = max(1, int(0.1 * sample_rate / frame_len))
    start = max(0, int(above[0]) - pad_frames) * frame_len
    end = min(
        samples.size, (int(above[-1]) + 1 + pad_frames) * frame_len
    )
    return samples[start:end]


def preflight_clip(path: Path) -> PreflightResult:
    """Run every v7-floor check on a WAV clip and return the result.

    Pure analysis — does not mutate the clip. Auto-trimming is a
    separate step (see :func:`apply_trim`) because the caller may want
    to save the trimmed version, not just report on the untrimmed one.
    """
    result = PreflightResult(path=path)
    if not path.exists():
        result.add("exists", False, f"file not found: {path}")
        return result

    try:
        sample_rate, samples = wavfile.read(str(path))
    except Exception as exc:  # noqa: BLE001
        result.add("read", False, f"cannot read WAV: {exc}")
        return result

    # Sample rate
    result.add(
        "sample rate",
        sample_rate >= MIN_SAMPLE_RATE_HZ,
        f"{sample_rate} Hz (need ≥ {MIN_SAMPLE_RATE_HZ} Hz)",
    )

    # Channels (downmix OK, just report)
    n_channels = 2 if samples.ndim == 2 else 1
    result.add(
        "channels",
        True,
        f"{n_channels} channel(s){' (will downmix)' if n_channels > 1 else ''}",
    )

    mono = _to_mono_float(samples)
    duration_s = mono.size / sample_rate

    # Duration
    result.add(
        "duration",
        MIN_DURATION_S <= duration_s <= MAX_DURATION_S,
        f"{duration_s:.1f} s (need {MIN_DURATION_S:.0f}–{MAX_DURATION_S:.0f})",
    )

    # Clipping — count samples at ±full scale
    clipped = int(np.sum(np.abs(mono) >= 0.999))
    clip_ratio = clipped / max(mono.size, 1)
    result.add(
        "clipping",
        clip_ratio <= MAX_CLIP_RATIO,
        f"{clipped} sample(s) clipped ({clip_ratio * 100:.3f}% "
        f"— max {MAX_CLIP_RATIO * 100:.2f}%)",
    )

    # RMS loudness
    rms = _rms_dbfs(mono)
    result.add(
        "loudness",
        MIN_RMS_DBFS <= rms <= MAX_RMS_DBFS,
        f"RMS {rms:.1f} dBFS (need {MIN_RMS_DBFS:.0f}…{MAX_RMS_DBFS:.0f})",
    )

    # SNR (rough)
    snr = _estimate_snr_db(mono, sample_rate)
    result.add(
        "SNR (estimate)",
        snr >= MIN_SNR_DB,
        f"{snr:.1f} dB (need ≥ {MIN_SNR_DB:.0f} dB)",
    )

    return result


def apply_trim(path: Path, out_path: Optional[Path] = None) -> Path:
    """Load a WAV, trim leading/trailing silence, save to out_path.

    If ``out_path`` is None, overwrites the source in place. Returns
    the path of the written file.
    """
    sample_rate, samples = wavfile.read(str(path))
    mono = _to_mono_float(samples)
    trimmed = _auto_trim_silence(mono, sample_rate)
    if out_path is None:
        out_path = path
    # Save as 16-bit PCM — that is what Chatterbox's ref encoder expects.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    int16 = np.clip(trimmed * 32767.0, -32768, 32767).astype(np.int16)
    wavfile.write(str(out_path), sample_rate, int16)
    return out_path


# ---------------------------------------------------------------------------
# Recording — ffmpeg subprocess, avfoundation/dshow depending on platform
# ---------------------------------------------------------------------------


def _ffmpeg_binary() -> str:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError(
            "ffmpeg not found on PATH — install it with `brew install "
            "ffmpeg` on Mac or from https://ffmpeg.org on Windows."
        )
    return ff


def _input_format_for_platform() -> str:
    """Return the ffmpeg input format name for the current OS."""
    system = _plat.system()
    if system == "Darwin":
        return "avfoundation"
    if system == "Windows":
        return "dshow"
    # Linux fallback — most setups have ALSA.
    return "alsa"


def list_input_devices() -> str:
    """Ask ffmpeg for a human-readable list of audio input devices."""
    fmt = _input_format_for_platform()
    cmd = [_ffmpeg_binary(), "-hide_banner", "-f", fmt]
    if fmt == "avfoundation":
        cmd += ["-list_devices", "true", "-i", ""]
    elif fmt == "dshow":
        cmd += ["-list_devices", "true", "-i", "dummy"]
    else:
        cmd += ["-list_devices", "true", "-i", "default"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # ffmpeg writes the list to stderr; stdout is empty.
    return proc.stderr


def _default_input_spec() -> str:
    """Return the ffmpeg input spec for the default audio device."""
    system = _plat.system()
    if system == "Darwin":
        # avfoundation format: ":<audio_index>". Index 0 is typically
        # the first virtual device; the MacBook built-in mic is
        # usually [1]. We cannot know for sure without listing, so the
        # user is expected to pass --input-device when the default is
        # wrong. ":1" is the MacBook Pro Microphone in the common case.
        return ":1"
    if system == "Windows":
        # On Windows the user will pass --input-device "Microphone
        # (Realtek)" or similar. No guaranteed default name.
        return "audio=default"
    return "default"


def record_clip(
    output_path: Path,
    duration_s: float = DEFAULT_DURATION_S,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    input_device: Optional[str] = None,
) -> Path:
    """Record a mono WAV clip via ffmpeg and return the output path.

    Blocks until recording finishes. The caller is responsible for
    showing a countdown before calling this — ffmpeg starts capturing
    the instant it is launched.
    """
    fmt = _input_format_for_platform()
    spec = input_device or _default_input_spec()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        fmt,
        "-i",
        spec,
        "-t",
        f"{duration_s}",
        "-ac",
        "1",
        "-ar",
        str(sample_rate_hz),
        "-c:a",
        "pcm_s16le",
        "-y",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path


def play_clip(path: Path) -> None:
    """Play the WAV back through the default output device, blocking."""
    system = _plat.system()
    if system == "Darwin":
        subprocess.run(["afplay", str(path)], check=False)
    elif system == "Windows":
        # Fallback to ffplay (ships with ffmpeg).
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            check=False,
        )
    else:
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            check=False,
        )


# ---------------------------------------------------------------------------
# Chatterbox synthesis leg — shell out to dev_chatterbox_fi.py
# ---------------------------------------------------------------------------


def _chatterbox_python() -> Path:
    if _plat.system() == "Windows":
        return CHATTERBOX_VENV_PY_WINDOWS
    return CHATTERBOX_VENV_PY


def synthesize_with_cloned_voice(
    ref_wav: Path,
    text: str,
    output_mp3: Path,
    device: str = "cpu",
    chunk_chars: int = 35,
) -> int:
    """Run dev_chatterbox_fi.py with the reference clip and return the
    subprocess exit code. Uses v7 hyper-params (the script defaults)."""
    venv_py = _chatterbox_python()
    if not venv_py.exists():
        raise RuntimeError(
            f"Chatterbox venv Python not found at {venv_py}. "
            "Install it from the GUI's \"Install engines…\" panel on "
            "Windows, or recreate the .venv-chatterbox virtualenv manually "
            "on Mac (see docs/QUICKSTART_DEV.md)."
        )
    if not CHATTERBOX_SCRIPT.exists():
        raise RuntimeError(f"{CHATTERBOX_SCRIPT} not found.")
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(venv_py),
        str(CHATTERBOX_SCRIPT),
        "--text",
        text,
        "--ref-audio",
        str(ref_wav),
        "--output",
        str(output_mp3),
        "--finnish-finetune",
        "--device",
        device,
        "--chunk-chars",
        str(chunk_chars),
    ]
    print(f"▶ running Chatterbox: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# Countdown + interactive shell niceties
# ---------------------------------------------------------------------------


def _countdown(seconds: int = 3, message: str = "Recording starts in") -> None:
    for i in range(seconds, 0, -1):
        print(f"  {message} {i}…", flush=True)
        try:
            import time as _time

            _time.sleep(1)
        except KeyboardInterrupt:
            print("Cancelled.")
            sys.exit(130)
    print("  🔴 RECORDING — speak now", flush=True)


def _read_text_source(args: argparse.Namespace) -> Optional[str]:
    if args.synthesize:
        return args.synthesize
    if args.synthesize_file:
        return Path(args.synthesize_file).read_text(encoding="utf-8").strip()
    return None


def _timestamped_wav(stem: str = "sample") -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_VOICE_SAMPLES_DIR / f"{stem}_{ts}.wav"


def _timestamped_mp3(stem: str = "cloned") -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_SYNTH_OUTPUT_DIR / f"{stem}_{ts}.mp3"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--list-devices",
        action="store_true",
        help="Print available input devices and exit.",
    )
    p.add_argument(
        "--input-device",
        default=None,
        help=(
            "ffmpeg input device spec. On Mac avfoundation this is an "
            "index like ':1' (built-in mic). On Windows dshow this is "
            "'audio=Microphone Name'. Default picks the OS-specific "
            "built-in mic."
        ),
    )
    p.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Recording length in seconds (default {DEFAULT_DURATION_S}).",
    )
    p.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE_HZ,
        help=f"Recording sample rate in Hz (default {DEFAULT_SAMPLE_RATE_HZ}).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Where to save the recorded WAV. Default: "
            f"{DEFAULT_VOICE_SAMPLES_DIR}/sample_<timestamp>.wav"
        ),
    )
    p.add_argument(
        "--use-existing",
        type=Path,
        default=None,
        help=(
            "Skip recording; run preflight on this existing WAV instead. "
            "Useful for cloning with a previously-saved clip."
        ),
    )
    p.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip v7 quality-floor checks (dev escape hatch — not recommended).",
    )
    p.add_argument(
        "--skip-trim",
        action="store_true",
        help="Skip auto-trim of leading/trailing silence.",
    )
    p.add_argument(
        "--no-playback",
        action="store_true",
        help="Do not auto-play the recording after capture.",
    )
    p.add_argument(
        "--no-countdown",
        action="store_true",
        help="Skip the 3-second countdown before recording starts.",
    )
    p.add_argument(
        "--synthesize",
        type=str,
        default=None,
        help=(
            "After preflight, synthesize this text via Chatterbox-Finnish "
            "using the recorded clip as --ref-audio."
        ),
    )
    p.add_argument(
        "--synthesize-file",
        type=Path,
        default=None,
        help="Synthesize text loaded from a file (UTF-8, all contents).",
    )
    p.add_argument(
        "--synthesis-output",
        type=Path,
        default=None,
        help=(
            "Where to save the cloned MP3. Default: "
            f"{DEFAULT_SYNTH_OUTPUT_DIR}/cloned_<timestamp>.mp3"
        ),
    )
    p.add_argument(
        "--tts-device",
        choices=["cpu", "cuda", "mps"],
        default="cpu",
        help=(
            "Chatterbox inference device. Default 'cpu' (Mac dev). Use "
            "'cuda' on the Windows GPU machine for fast production runs."
        ),
    )
    p.add_argument(
        "--chunk-chars",
        type=int,
        default=35,
        help=(
            "Per-chunk character limit passed to the Finnish sentence-"
            "aware chunker. 35 forces one sentence per chunk, which "
            "bypasses Chatterbox's early-EOS bug on multi-sentence "
            "inputs. Do not raise this without testing."
        ),
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.list_devices:
        print(list_input_devices())
        return 0

    # Decide whether to record or reuse an existing clip.
    if args.use_existing:
        clip_path = args.use_existing
        if not clip_path.exists():
            print(f"✗ --use-existing path not found: {clip_path}",
                  file=sys.stderr)
            return 2
        print(f"Reusing existing clip: {clip_path}")
    else:
        clip_path = args.output or _timestamped_wav()
        print(f"Target recording path: {clip_path}")
        print(f"  duration: {args.duration:.1f} s, "
              f"sample rate: {args.sample_rate} Hz, "
              f"mono, 16-bit PCM")
        if not args.no_countdown:
            _countdown()
        try:
            record_clip(
                clip_path,
                duration_s=args.duration,
                sample_rate_hz=args.sample_rate,
                input_device=args.input_device,
            )
        except subprocess.CalledProcessError as exc:
            print(f"✗ ffmpeg recording failed (exit {exc.returncode}). "
                  f"Try `--list-devices` and pass an explicit "
                  f"--input-device.", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        print(f"  saved {clip_path}")
        if not args.no_playback:
            print("  playing back…")
            play_clip(clip_path)

    # Optional: auto-trim leading/trailing silence.
    if not args.skip_trim:
        trimmed = apply_trim(clip_path)
        print(f"  auto-trimmed leading/trailing silence → {trimmed}")

    # Preflight.
    if not args.skip_preflight:
        result = preflight_clip(clip_path)
        print(result.render())
        if not result.passed:
            print(
                "\n✗ v7 quality floor not met. Fix the issues above and "
                "re-record (or pass --skip-preflight to override).",
                file=sys.stderr,
            )
            return 3

    # Optional: synthesize cloned speech.
    text = _read_text_source(args)
    if text is None:
        print("\nNo --synthesize text provided; stopping after clip save.")
        return 0

    synth_path = args.synthesis_output or _timestamped_mp3()
    print(f"\nSynthesizing {len(text)} chars of text in your cloned voice…")
    try:
        rc = synthesize_with_cloned_voice(
            ref_wav=clip_path,
            text=text,
            output_mp3=synth_path,
            device=args.tts_device,
            chunk_chars=args.chunk_chars,
        )
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 4
    if rc != 0:
        print(f"✗ Chatterbox exited with {rc}", file=sys.stderr)
        return rc

    print(f"\n✅ Done. Cloned MP3: {synth_path}")
    if not args.no_playback:
        print("  playing back…")
        play_clip(synth_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
