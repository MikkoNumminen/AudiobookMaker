"""In-app voice recording dialog for Chatterbox voice cloning.

Provides a modal tkinter dialog that lets users record a voice clip,
run preflight quality checks, and return the WAV path for use as
Chatterbox reference audio.  All audio analysis uses the stdlib ``wave``
and ``struct`` modules so the main app process does not need numpy.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import platform as _plat
import shutil
import struct
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

# ---------------------------------------------------------------------------
# Quality-floor thresholds (mirrored from scripts/record_voice_sample.py)
# ---------------------------------------------------------------------------

MIN_SAMPLE_RATE_HZ = 16_000
MIN_DURATION_S = 5.0
MAX_DURATION_S = 30.0
MIN_SNR_DB = 15.0
MIN_RMS_DBFS = -35.0
MAX_RMS_DBFS = -10.0
MAX_CLIP_RATIO = 0.0005  # 0.05 %

RECORD_DURATION_S = 15.0
RECORD_SAMPLE_RATE = 22_050
VOICE_SAMPLES_DIR = Path("voice_samples")

# ---------------------------------------------------------------------------
# i18n strings
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "fi": {
        "title":        "Aanitallennus",
        "ready":        "Valmis",
        "recording":    "Tallennetaan...",
        "processing":   "Analysoidaan...",
        "duration":     "Kesto",
        "record":       "Tallenna",
        "stop":         "Pysayta",
        "play":         "Kuuntele",
        "rerecord":     "Tallenna uudelleen",
        "use_clip":     "Kayta tata",
        "cancel":       "Peruuta",
        "status":       "Tila",
        "quality":      "Laatutarkistukset",
        "chk_rate":     "Naytetaajuus >= 16 kHz",
        "chk_dur":      "Kesto 5-30 s",
        "chk_clip":     "Clipping < 0.05 %",
        "chk_loud":     "Aanekkyys -35 ... -10 dBFS",
        "chk_snr":      "SNR >= 15 dB",
        "no_ffmpeg":    "ffmpeg-ohjelmaa ei loydy. Asenna ffmpeg ennen tallennusta.",
        "rec_failed":   "Tallennus epaonnistui",
        "no_devices":   "Mikrofonilaitetta ei loydy.",
        "dev_label":    "Mikrofoni",
        "dev_default":  "(oletus)",
    },
    "en": {
        "title":        "Voice Recording",
        "ready":        "Ready",
        "recording":    "Recording...",
        "processing":   "Processing...",
        "duration":     "Duration",
        "record":       "Record",
        "stop":         "Stop",
        "play":         "Play",
        "rerecord":     "Re-record",
        "use_clip":     "Use this clip",
        "cancel":       "Cancel",
        "status":       "Status",
        "quality":      "Quality checks",
        "chk_rate":     "Sample rate >= 16 kHz",
        "chk_dur":      "Duration 5-30 s",
        "chk_clip":     "Clipping < 0.05%",
        "chk_loud":     "Loudness -35 to -10 dBFS",
        "chk_snr":      "SNR >= 15 dB",
        "no_ffmpeg":    "ffmpeg not found. Install ffmpeg before recording.",
        "rec_failed":   "Recording failed",
        "no_devices":   "No microphone device found.",
        "dev_label":    "Microphone",
        "dev_default":  "(default)",
    },
}

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RecordingResult:
    success: bool
    wav_path: Optional[Path] = None
    error: str = ""


# ---------------------------------------------------------------------------
# Preflight analysis — stdlib only (wave + struct)
# ---------------------------------------------------------------------------


def _read_wav_samples(path: Path) -> tuple[int, int, list[int]]:
    """Read a 16-bit PCM WAV and return (sample_rate, n_channels, samples).

    Returns raw int16 sample values as a flat list.
    """
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        n_ch = wf.getnchannels()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    # 16-bit signed PCM
    count = len(raw) // 2
    samples = list(struct.unpack(f"<{count}h", raw))
    return rate, n_ch, samples


def _downmix_mono(samples: list[int], n_channels: int) -> list[int]:
    if n_channels == 1:
        return samples
    mono: list[int] = []
    for i in range(0, len(samples), n_channels):
        avg = sum(samples[i : i + n_channels]) // n_channels
        mono.append(avg)
    return mono


def _rms_dbfs(samples: list[int]) -> float:
    if not samples:
        return -100.0
    sq_sum = sum(s * s for s in samples)
    rms = math.sqrt(sq_sum / len(samples))
    # Full-scale for int16 is 32768
    if rms <= 0:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


def _clipping_ratio(samples: list[int]) -> float:
    if not samples:
        return 0.0
    clipped = sum(1 for s in samples if s >= 32767 or s <= -32768)
    return clipped / len(samples)


def _estimate_snr(samples: list[int], sample_rate: int) -> float:
    """Rough SNR via frame-power sorting (bottom 10% = noise, top 50% = signal)."""
    frame_len = max(1, int(0.03 * sample_rate))
    n_frames = len(samples) // frame_len
    if n_frames < 10:
        return 30.0  # too short to estimate, assume OK
    powers: list[float] = []
    for i in range(n_frames):
        start = i * frame_len
        frame = samples[start : start + frame_len]
        p = sum(s * s for s in frame) / frame_len
        powers.append(p)
    powers.sort()
    noise_end = max(1, n_frames // 10)
    signal_start = n_frames - max(1, n_frames // 2)
    noise_power = sum(powers[:noise_end]) / noise_end
    signal_power = sum(powers[signal_start:]) / (n_frames - signal_start)
    noise_power = max(noise_power, 1e-12)
    signal_power = max(signal_power, 1e-12)
    return 10.0 * math.log10(signal_power / noise_power)


@dataclass
class _CheckResult:
    name: str
    passed: bool
    detail: str


def run_preflight(path: Path) -> list[_CheckResult]:
    """Run quality checks and return a list of results."""
    checks: list[_CheckResult] = []
    try:
        rate, n_ch, raw_samples = _read_wav_samples(path)
    except Exception as exc:
        checks.append(_CheckResult("read", False, str(exc)))
        return checks

    mono = _downmix_mono(raw_samples, n_ch)
    duration = len(mono) / rate

    checks.append(_CheckResult(
        "sample_rate", rate >= MIN_SAMPLE_RATE_HZ,
        f"{rate} Hz",
    ))
    checks.append(_CheckResult(
        "duration", MIN_DURATION_S <= duration <= MAX_DURATION_S,
        f"{duration:.1f} s",
    ))

    cr = _clipping_ratio(mono)
    checks.append(_CheckResult(
        "clipping", cr <= MAX_CLIP_RATIO,
        f"{cr * 100:.3f}%",
    ))

    rms = _rms_dbfs(mono)
    checks.append(_CheckResult(
        "loudness", MIN_RMS_DBFS <= rms <= MAX_RMS_DBFS,
        f"{rms:.1f} dBFS",
    ))

    snr = _estimate_snr(mono, rate)
    checks.append(_CheckResult(
        "snr", snr >= MIN_SNR_DB,
        f"{snr:.1f} dB",
    ))

    return checks


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _ffmpeg_binary() -> str:
    # Try project-local ffmpeg first (bundled builds)
    try:
        from src.ffmpeg_path import get_ffmpeg_path  # type: ignore
        p = get_ffmpeg_path()
        if p:
            return str(p)
    except Exception:
        pass
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    raise FileNotFoundError("ffmpeg not found")


def _ffplay_binary() -> str:
    ff = shutil.which("ffplay")
    if ff:
        return ff
    # Try next to ffmpeg
    try:
        ffmpeg = _ffmpeg_binary()
        ffplay = Path(ffmpeg).parent / ("ffplay.exe" if os.name == "nt" else "ffplay")
        if ffplay.exists():
            return str(ffplay)
    except Exception:
        pass
    raise FileNotFoundError("ffplay not found")


def _list_dshow_devices() -> list[str]:
    """Parse ffmpeg dshow device list and return audio device names."""
    try:
        ffmpeg = _ffmpeg_binary()
    except FileNotFoundError:
        return []
    cmd = [ffmpeg, "-hide_banner", "-f", "dshow",
           "-list_devices", "true", "-i", "dummy"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW
                          if os.name == "nt" else 0)
    text = proc.stderr
    devices: list[str] = []
    in_audio = False
    for line in text.splitlines():
        if "DirectShow audio devices" in line:
            in_audio = True
            continue
        if "DirectShow video devices" in line:
            in_audio = False
            continue
        if in_audio and '"' in line:
            # Lines look like: [dshow ...] "Device Name" (audio)
            # or: [dshow ...]  "Device Name"
            start = line.index('"') + 1
            end = line.index('"', start)
            name = line[start:end]
            # Skip "Alternative name" entries
            if "@device" not in name:
                devices.append(name)
    return devices


def _timestamped_path() -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return VOICE_SAMPLES_DIR / f"sample_{ts}.wav"


# ---------------------------------------------------------------------------
# VoiceRecorderDialog
# ---------------------------------------------------------------------------


class VoiceRecorderDialog:
    """Modal dialog for recording voice clips for Chatterbox cloning."""

    def __init__(self, parent: tk.Tk, ui_lang: str = "fi"):
        self._parent = parent
        self._lang = ui_lang if ui_lang in _STRINGS else "fi"
        self._result_path: Optional[Path] = None
        self._wav_path: Optional[Path] = None
        self._recording = False
        self._rec_process: Optional[subprocess.Popen] = None
        self._rec_start: float = 0.0
        self._play_process: Optional[subprocess.Popen] = None
        self._check_widgets: list[tuple[tk.Label, tk.Label]] = []

        self._build_dialog()

    # -- i18n helper --------------------------------------------------------

    def _s(self, key: str) -> str:
        return _STRINGS.get(self._lang, _STRINGS["fi"]).get(key, key)

    # -- dialog construction ------------------------------------------------

    def _build_dialog(self) -> None:
        self._dlg = tk.Toplevel(self._parent)
        self._dlg.title(self._s("title"))
        self._dlg.resizable(False, False)
        self._dlg.grab_set()
        self._dlg.protocol("WM_DELETE_WINDOW", self._on_cancel)

        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # -- Device selector ------------------------------------------------
        dev_frame = ttk.Frame(main)
        dev_frame.pack(fill=tk.X, **pad)
        ttk.Label(dev_frame, text=self._s("dev_label") + ":").pack(side=tk.LEFT)
        self._dev_var = tk.StringVar()
        self._dev_combo = ttk.Combobox(dev_frame, textvariable=self._dev_var,
                                        state="readonly", width=40)
        self._dev_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        self._populate_devices()

        # -- Record button --------------------------------------------------
        self._rec_btn = tk.Button(
            main, text=self._s("record"), width=18, height=2,
            bg="#cc3333", fg="white", activebackground="#ee4444",
            font=("TkDefaultFont", 12, "bold"),
            command=self._toggle_record,
        )
        self._rec_btn.pack(**pad)

        # -- Status / duration / progress -----------------------------------
        info_frame = ttk.Frame(main)
        info_frame.pack(fill=tk.X, **pad)

        ttk.Label(info_frame, text=self._s("status") + ":").grid(
            row=0, column=0, sticky=tk.W)
        self._status_var = tk.StringVar(value=self._s("ready"))
        ttk.Label(info_frame, textvariable=self._status_var).grid(
            row=0, column=1, sticky=tk.W, padx=(6, 0))

        ttk.Label(info_frame, text=self._s("duration") + ":").grid(
            row=1, column=0, sticky=tk.W)
        self._dur_var = tk.StringVar(value="0:00 / 15s")
        ttk.Label(info_frame, textvariable=self._dur_var).grid(
            row=1, column=1, sticky=tk.W, padx=(6, 0))

        self._progress = ttk.Progressbar(main, maximum=RECORD_DURATION_S,
                                          length=350)
        self._progress.pack(fill=tk.X, **pad)

        # -- Quality checks frame -------------------------------------------
        qf = ttk.LabelFrame(main, text=self._s("quality"), padding=6)
        qf.pack(fill=tk.X, **pad)

        check_keys = [
            ("chk_rate", "sample_rate"),
            ("chk_dur", "duration"),
            ("chk_clip", "clipping"),
            ("chk_loud", "loudness"),
            ("chk_snr", "snr"),
        ]
        self._check_widgets = []
        for i, (label_key, _) in enumerate(check_keys):
            indicator = tk.Label(qf, text="[ ]", width=4, anchor=tk.CENTER)
            indicator.grid(row=i, column=0)
            lbl = tk.Label(qf, text=self._s(label_key), anchor=tk.W)
            lbl.grid(row=i, column=1, sticky=tk.W, padx=(4, 0))
            self._check_widgets.append((indicator, lbl))

        # -- Bottom buttons -------------------------------------------------
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        self._play_btn = ttk.Button(btn_frame, text=self._s("play"),
                                     command=self._play, state=tk.DISABLED)
        self._play_btn.pack(side=tk.LEFT, padx=4)

        self._rerecord_btn = ttk.Button(btn_frame, text=self._s("rerecord"),
                                         command=self._rerecord,
                                         state=tk.DISABLED)
        self._rerecord_btn.pack(side=tk.LEFT, padx=4)

        self._use_btn = ttk.Button(btn_frame, text=self._s("use_clip"),
                                    command=self._use_clip, state=tk.DISABLED)
        self._use_btn.pack(side=tk.LEFT, padx=4)

        self._cancel_btn = ttk.Button(btn_frame, text=self._s("cancel"),
                                       command=self._on_cancel)
        self._cancel_btn.pack(side=tk.RIGHT, padx=4)

        # Centre the dialog on parent
        self._dlg.update_idletasks()
        w = self._dlg.winfo_width()
        h = self._dlg.winfo_height()
        px = self._parent.winfo_rootx() + (self._parent.winfo_width() - w) // 2
        py = self._parent.winfo_rooty() + (self._parent.winfo_height() - h) // 2
        self._dlg.geometry(f"+{max(px, 0)}+{max(py, 0)}")

    # -- device enumeration -------------------------------------------------

    def _populate_devices(self) -> None:
        if _plat.system() != "Windows":
            self._dev_combo.configure(values=[self._s("dev_default")])
            self._dev_var.set(self._s("dev_default"))
            return
        devices = _list_dshow_devices()
        display = [self._s("dev_default")] + devices
        self._dev_combo.configure(values=display)
        self._dev_var.set(display[0])

    def _selected_device_spec(self) -> Optional[str]:
        """Return the ffmpeg input spec for the selected device, or None."""
        sel = self._dev_var.get()
        if sel == self._s("dev_default"):
            return None  # let ffmpeg pick default
        if _plat.system() == "Windows":
            return f"audio={sel}"
        return sel

    # -- recording control --------------------------------------------------

    def _toggle_record(self) -> None:
        if self._recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        try:
            ffmpeg = _ffmpeg_binary()
        except FileNotFoundError:
            messagebox.showerror(self._s("rec_failed"),
                                 self._s("no_ffmpeg"),
                                 parent=self._dlg)
            return

        VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        self._wav_path = _timestamped_path()

        device_spec = self._selected_device_spec()
        system = _plat.system()
        if system == "Windows":
            fmt = "dshow"
            inp = device_spec or "audio=default"
        elif system == "Darwin":
            fmt = "avfoundation"
            inp = device_spec or ":1"
        else:
            fmt = "alsa"
            inp = device_spec or "default"

        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", fmt, "-i", inp,
            "-t", str(RECORD_DURATION_S),
            "-ac", "1",
            "-ar", str(RECORD_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            "-y", str(self._wav_path),
        ]

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._rec_process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except Exception as exc:
            messagebox.showerror(self._s("rec_failed"), str(exc),
                                 parent=self._dlg)
            return

        self._recording = True
        self._rec_start = time.time()
        self._rec_btn.configure(text=self._s("stop"), bg="#991111")
        self._status_var.set(self._s("recording"))
        self._play_btn.configure(state=tk.DISABLED)
        self._rerecord_btn.configure(state=tk.DISABLED)
        self._use_btn.configure(state=tk.DISABLED)
        self._dev_combo.configure(state=tk.DISABLED)
        self._reset_checks()
        self._tick_progress()

    def _tick_progress(self) -> None:
        if not self._recording:
            return
        elapsed = time.time() - self._rec_start
        secs = int(elapsed)
        self._dur_var.set(f"{secs // 60}:{secs % 60:02d} / {int(RECORD_DURATION_S)}s")
        self._progress["value"] = min(elapsed, RECORD_DURATION_S)

        # Check if ffmpeg finished on its own (duration reached)
        if self._rec_process and self._rec_process.poll() is not None:
            self._on_record_done()
            return
        self._dlg.after(200, self._tick_progress)

    def _stop_record(self) -> None:
        if self._rec_process and self._rec_process.poll() is None:
            # Send 'q' to ffmpeg stdin to stop gracefully
            try:
                self._rec_process.stdin.write(b"q")  # type: ignore[union-attr]
                self._rec_process.stdin.flush()  # type: ignore[union-attr]
            except Exception:
                self._rec_process.terminate()
        # Wait for the process in a thread to avoid blocking UI
        threading.Thread(target=self._wait_record, daemon=True).start()

    def _wait_record(self) -> None:
        if self._rec_process:
            try:
                self._rec_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._rec_process.kill()
        self._dlg.after(0, self._on_record_done)

    def _on_record_done(self) -> None:
        self._recording = False
        self._rec_btn.configure(text=self._s("record"), bg="#cc3333")
        self._dev_combo.configure(state="readonly")

        if self._wav_path and self._wav_path.exists() and self._wav_path.stat().st_size > 44:
            self._status_var.set(self._s("processing"))
            self._dlg.update_idletasks()
            # Run checks in a thread to keep UI responsive
            threading.Thread(target=self._run_checks, daemon=True).start()
        else:
            self._status_var.set(self._s("rec_failed"))

    def _run_checks(self) -> None:
        assert self._wav_path is not None
        checks = run_preflight(self._wav_path)
        self._dlg.after(0, self._display_checks, checks)

    def _display_checks(self, checks: list[_CheckResult]) -> None:
        check_order = ["sample_rate", "duration", "clipping", "loudness", "snr"]
        check_map = {c.name: c for c in checks}
        all_pass = True
        for i, key in enumerate(check_order):
            ind, lbl = self._check_widgets[i]
            chk = check_map.get(key)
            if chk is None:
                continue
            if chk.passed:
                ind.configure(text="[OK]", fg="green")
            else:
                ind.configure(text="[X]", fg="red")
                all_pass = False
            # Append detail to label
            base_text = lbl.cget("text").split("  (")[0]  # strip old detail
            lbl.configure(text=f"{base_text}  ({chk.detail})")

        self._status_var.set(self._s("ready"))
        self._play_btn.configure(state=tk.NORMAL)
        self._rerecord_btn.configure(state=tk.NORMAL)
        self._use_btn.configure(state=tk.NORMAL)

        # Update duration display from actual file
        chk_dur = check_map.get("duration")
        if chk_dur:
            self._dur_var.set(chk_dur.detail)

    def _reset_checks(self) -> None:
        check_keys = ["chk_rate", "chk_dur", "chk_clip", "chk_loud", "chk_snr"]
        for i, key in enumerate(check_keys):
            ind, lbl = self._check_widgets[i]
            ind.configure(text="[ ]", fg="black")
            lbl.configure(text=self._s(key))

    # -- playback -----------------------------------------------------------

    def _play(self) -> None:
        if not self._wav_path or not self._wav_path.exists():
            return
        # Kill any existing playback
        if self._play_process and self._play_process.poll() is None:
            self._play_process.terminate()
            return

        system = _plat.system()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if system == "Darwin":
            cmd = ["afplay", str(self._wav_path)]
        else:
            try:
                ffplay = _ffplay_binary()
                cmd = [ffplay, "-nodisp", "-autoexit",
                       "-loglevel", "quiet", str(self._wav_path)]
            except FileNotFoundError:
                messagebox.showwarning("Playback",
                                       "ffplay not found", parent=self._dlg)
                return
        try:
            self._play_process = subprocess.Popen(
                cmd, creationflags=creationflags)
        except Exception as exc:
            messagebox.showwarning("Playback", str(exc), parent=self._dlg)

    # -- re-record / use / cancel -------------------------------------------

    def _rerecord(self) -> None:
        # Clean up old file
        if self._wav_path and self._wav_path.exists():
            try:
                self._wav_path.unlink()
            except OSError:
                pass
        self._wav_path = None
        self._progress["value"] = 0
        self._dur_var.set("0:00 / 15s")
        self._status_var.set(self._s("ready"))
        self._play_btn.configure(state=tk.DISABLED)
        self._rerecord_btn.configure(state=tk.DISABLED)
        self._use_btn.configure(state=tk.DISABLED)
        self._reset_checks()

    def _use_clip(self) -> None:
        self._result_path = self._wav_path
        self._cleanup_and_close()

    def _on_cancel(self) -> None:
        # If recording, stop it
        if self._recording:
            self._stop_record()
        # Clean up the file if user cancels
        if self._wav_path and self._wav_path.exists() and self._result_path is None:
            try:
                self._wav_path.unlink()
            except OSError:
                pass
        self._result_path = None
        self._cleanup_and_close()

    def _cleanup_and_close(self) -> None:
        if self._play_process and self._play_process.poll() is None:
            self._play_process.terminate()
        if self._rec_process and self._rec_process.poll() is None:
            self._rec_process.terminate()
        self._dlg.grab_release()
        self._dlg.destroy()

    # -- public API ---------------------------------------------------------

    def show(self) -> Optional[Path]:
        """Show the dialog modally and return the WAV path, or None."""
        self._dlg.wait_window()
        return self._result_path
