"""Tiny ffmpeg wrappers for the chunked-analyze orchestrator.

Two operations only:

* :func:`probe_duration` — read the source duration via ``ffprobe`` so
  we can plan chunks without decoding the whole file.
* :func:`slice_audio` — cut ``[start, end]`` out of the source into a
  16 kHz mono WAV so the per-chunk analyses see a consistent format
  (the same one ASR + diarization want internally).

Everything is dependency-injected so tests don't have to spawn real
ffmpeg processes. The default factory shells out via :mod:`subprocess`;
tests pass a fake that records the argv and writes a stub file.

Errors surface as :class:`FfmpegError` with the captured stderr tail
attached so the orchestrator can log a useful message rather than a
bare "exit 1".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class FfmpegError(RuntimeError):
    """Raised when ffmpeg / ffprobe exits non-zero or can't be located."""


@dataclass(frozen=True)
class SliceRequest:
    """One ffmpeg slice job. Pure data, no I/O."""

    source: Path
    out_path: Path
    start_seconds: float
    end_seconds: float


# ---------------------------------------------------------------------------
# Subprocess factory shape — injectable so tests never exec a real ffmpeg.
# ---------------------------------------------------------------------------

#: ``(argv, env) -> CompletedProcess`` factory. Default uses
#: :func:`subprocess.run` with stderr captured. Tests inject a fake
#: that does not actually exec anything.
SubprocessRunner = Callable[[list[str], dict], "subprocess.CompletedProcess"]


def _default_runner(cmd: list[str], env: dict) -> "subprocess.CompletedProcess":
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        creationflags=creationflags,
    )


# ---------------------------------------------------------------------------
# Locating the binaries
# ---------------------------------------------------------------------------


def resolve_ffmpeg_exe() -> str:
    """Return the ffmpeg executable path. Raises if it can't be found.

    Routes through :func:`src.ffmpeg_path.get_ffmpeg_exe` so dev /
    frozen builds resolve the bundled binary. Falls back to PATH for
    installs that have ffmpeg system-wide.
    """
    try:
        from src.ffmpeg_path import get_ffmpeg_exe  # local import, dev/frozen-aware
    except Exception:  # pragma: no cover - defensive
        get_ffmpeg_exe = None  # type: ignore[assignment]

    if get_ffmpeg_exe is not None:
        candidate = get_ffmpeg_exe()
        if candidate:
            return candidate

    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FfmpegError(
        "ffmpeg executable not found. The bundled copy lives next to the "
        "installed app; for dev runs, install ffmpeg or run "
        "scripts/setup_ffmpeg.py."
    )


def resolve_ffprobe_exe() -> str:
    """Return the ffprobe executable path. Raises if missing.

    ffprobe ships next to ffmpeg in every official build, so we look
    in the same directory first and fall back to PATH.
    """
    ffmpeg = resolve_ffmpeg_exe()
    sibling = Path(ffmpeg).with_name(
        "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    )
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("ffprobe")
    if found:
        return found
    raise FfmpegError("ffprobe executable not found alongside ffmpeg.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def probe_duration(
    source: Path,
    *,
    ffprobe_exe: Optional[str] = None,
    runner: Optional[SubprocessRunner] = None,
) -> float:
    """Return the duration of ``source`` in seconds via ffprobe.

    Pulls the duration from the format header rather than decoding the
    file. Works for every container ffmpeg supports (wav, mp3, m4b, …).

    Raises:
        FfmpegError: If ffprobe is missing or returns non-zero, or if
            its JSON output doesn't carry a parseable duration.
    """
    exe = ffprobe_exe or resolve_ffprobe_exe()
    run = runner or _default_runner
    cmd = [
        exe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        "-i", str(source),
    ]
    completed = run(cmd, dict(os.environ))
    if completed.returncode != 0:
        raise FfmpegError(
            f"ffprobe failed with code {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:400]}"
        )
    try:
        payload = json.loads(completed.stdout or "{}")
        return float(payload["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FfmpegError(
            f"could not parse ffprobe output: {exc!r}; raw: "
            f"{(completed.stdout or '')[:200]}"
        ) from exc


def slice_audio(
    request: SliceRequest,
    *,
    ffmpeg_exe: Optional[str] = None,
    runner: Optional[SubprocessRunner] = None,
    sample_rate_hz: int = 16000,
) -> Path:
    """Cut ``[start_seconds, end_seconds]`` of ``source`` to ``out_path``.

    Output is **always** ``sample_rate_hz`` mono PCM-16 WAV. We don't
    try to fast-path ``-c copy`` because the per-chunk ASR + diarize
    pipeline expects normalised audio; an unconditional decode here
    means the analyser never has to second-guess the format.

    Args:
        request: :class:`SliceRequest` describing the cut.
        ffmpeg_exe: Optional override; resolves automatically when None.
        runner: Optional subprocess factory; defaults to a real fork.
        sample_rate_hz: Target sample rate. 16 kHz matches the model
            input rate for both faster-whisper and pyannote.

    Returns:
        The ``out_path`` from the request, after the slice has landed
        on disk.

    Raises:
        FfmpegError: ffmpeg missing, exited non-zero, or wrote no file.
        ValueError: Bad time bounds.
    """
    if request.end_seconds <= request.start_seconds:
        raise ValueError(
            f"slice end ({request.end_seconds}) must be > "
            f"start ({request.start_seconds})"
        )
    exe = ffmpeg_exe or resolve_ffmpeg_exe()
    run = runner or _default_runner
    duration = request.end_seconds - request.start_seconds
    request.out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "-y",
        "-loglevel", "error",
        "-ss", f"{request.start_seconds:.3f}",
        "-i", str(request.source),
        "-t", f"{duration:.3f}",
        "-ac", "1",
        "-ar", str(int(sample_rate_hz)),
        "-acodec", "pcm_s16le",
        str(request.out_path),
    ]
    completed = run(cmd, dict(os.environ))
    if completed.returncode != 0:
        raise FfmpegError(
            f"ffmpeg slice failed with code {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:400]}"
        )
    if not request.out_path.exists():
        raise FfmpegError(
            f"ffmpeg reported success but no file at {request.out_path}"
        )
    return request.out_path
