"""Subprocess runner for the voice-pack analyze stage.

The GUI clone-voice flow shells out to ``scripts/voice_pack_analyze.py``
inside ``.venv-chatterbox/`` rather than importing it in-process. The
reasons:

* ``faster-whisper`` + ``pyannote.audio`` both pull in ctranslate2 /
  torch, and keeping those out of the main GUI venv preserves the
  frozen installer's footprint.
* Same lifecycle pattern as synthesis: if the analyze child dies we
  can restart it without tearing down the GUI.
* A buggy analyze cannot crash the event loop.

This module is the thin wrapper the GUI owns. It knows:

* where the chatterbox Python lives (via
  :func:`src.launcher_bridge.resolve_chatterbox_python`),
* how to build the argv,
* how to parse the well-known progress markers
  ``[voice_pack_analyze] <stage>: <elapsed>s`` that the script emits
  when run with ``-v``,
* how to surface cancel requests.

Everything I/O is injectable so tests drive the runner with a fake
subprocess factory instead of spawning a real Python.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Progress / result types
# ---------------------------------------------------------------------------


# Stages exposed to the GUI. "starting" fires once before the subprocess
# launches; "asr" / "diarize" / "bucket" / "write" fire when the
# corresponding "[voice_pack_analyze] <stage>: <n>s" line is observed;
# "line" is a catch-all for any other stdout line (pip warnings, model
# load chatter) so the log box sees everything; "done" / "error" are
# terminal.
STAGE_STARTING: str = "starting"
STAGE_ASR: str = "asr"
STAGE_DIARIZE: str = "diarize"
STAGE_BUCKET: str = "bucket"
STAGE_WRITE: str = "write"
STAGE_LINE: str = "line"
STAGE_DONE: str = "done"
STAGE_ERROR: str = "error"

_KNOWN_STAGES: frozenset[str] = frozenset(
    {STAGE_ASR, STAGE_DIARIZE, STAGE_BUCKET, STAGE_WRITE}
)

# Matches ``[voice_pack_analyze] asr: 12.34s`` and friends. The stage
# name is any letters; elapsed is an optional float. Case-sensitive
# because the emitter is fixed-case.
_STAMP_RE = re.compile(
    r"\[voice_pack_analyze\]\s+(?P<stage>[a-zA-Z_]+):\s+(?P<elapsed>[0-9]+(?:\.[0-9]+)?)s"
)


@dataclass(frozen=True)
class AnalyzeProgress:
    """One progress event streamed by :func:`run_analyze`.

    ``stage`` is one of the ``STAGE_*`` constants. ``message`` is the
    raw stdout line (stripped). ``elapsed_s`` is present only for the
    four timing stamps; ``None`` otherwise.
    """

    stage: str
    message: str
    elapsed_s: Optional[float] = None


@dataclass
class AnalyzeJobResult:
    """Result returned by :func:`run_analyze` after the subprocess ends.

    Mutable because ``log_lines`` accumulates during the run; everything
    else is settled once the child exits.
    """

    ok: bool
    return_code: int
    transcripts_path: Path
    speakers_yaml_path: Path
    report_path: Path
    log_lines: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Injectable subprocess factory for tests
# ---------------------------------------------------------------------------

# Minimal protocol: object with .stdout (iterable of lines), .returncode
# (settable / readable), .wait(), .terminate(). Tests substitute a fake.
class _ProcessProtocol:  # pragma: no cover - structural typing only
    stdout: Iterable[str]
    returncode: int

    def wait(self) -> int: ...

    def terminate(self) -> None: ...


SubprocessFactory = Callable[[list[str], dict], "_ProcessProtocol"]


def _default_subprocess_factory(cmd: list[str], env: dict) -> subprocess.Popen:
    """Spawn the real subprocess. Stderr merged into stdout, line-buffered."""
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_analyze_argv(
    python_exe: Path,
    script_path: Path,
    wav: Path,
    out_dir: Path,
    *,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    diarizer: str = "pyannote",
) -> list[str]:
    """Build argv for the analyze subprocess.

    Kept public so tests can assert on the argv without launching
    anything, and so the GUI can show the command it's about to run.
    """
    argv: list[str] = [
        str(python_exe),
        str(script_path),
        "--input",
        str(wav),
        "--out",
        str(out_dir),
        "--diarizer",
        diarizer,
        "-v",
    ]
    if num_speakers is not None:
        argv += ["--num-speakers", str(num_speakers)]
    else:
        if min_speakers is not None:
            argv += ["--min-speakers", str(min_speakers)]
        if max_speakers is not None:
            argv += ["--max-speakers", str(max_speakers)]
    return argv


def _parse_stamp(line: str) -> Optional[tuple[str, float]]:
    """Return (stage, elapsed_s) if ``line`` is a timing stamp else None."""
    m = _STAMP_RE.search(line)
    if not m:
        return None
    stage = m.group("stage").lower()
    if stage not in _KNOWN_STAGES:
        return None
    return stage, float(m.group("elapsed"))


def run_analyze(
    wav: Path,
    out_dir: Path,
    *,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    diarizer: str = "pyannote",
    hf_token: Optional[str] = None,
    progress_cb: Optional[Callable[[AnalyzeProgress], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    python_exe: Optional[Path] = None,
    script_path: Optional[Path] = None,
    subprocess_factory: Optional[SubprocessFactory] = None,
    env_overrides: Optional[dict] = None,
) -> AnalyzeJobResult:
    """Run ``voice_pack_analyze`` as a subprocess and stream progress.

    Blocks until the child exits or ``cancel_event`` is set. Progress
    events go to ``progress_cb`` (invoked on the calling thread — the
    GUI is expected to marshal onto the UI thread via ``after(0, …)``
    itself, like synthesis already does).

    ``python_exe`` / ``script_path`` default to the repo's
    ``.venv-chatterbox`` Python and ``scripts/voice_pack_analyze.py``.
    Tests override both with fakes.

    Returns an :class:`AnalyzeJobResult`. When ``ok`` is False the
    caller should surface ``error`` to the user; the artefact paths
    are still populated (they may exist partially, or not at all).
    """
    if python_exe is None:
        from src.launcher_bridge import resolve_chatterbox_python

        resolved = resolve_chatterbox_python()
        if resolved is None:
            raise RuntimeError(
                "Chatterbox Python not found. Install Chatterbox first, "
                "then the Voice Cloner capability."
            )
        python_exe = resolved
    if script_path is None:
        repo_root = Path(__file__).resolve().parent.parent
        script_path = repo_root / "scripts" / "voice_pack_analyze.py"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    argv = build_analyze_argv(
        python_exe=python_exe,
        script_path=script_path,
        wav=Path(wav),
        out_dir=out_dir,
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        diarizer=diarizer,
    )

    # Env: inherit, then add HF_TOKEN if provided. We do NOT pass the
    # token on the CLI because argv is visible in process listings.
    env = {**os.environ}
    if hf_token:
        env["HF_TOKEN"] = hf_token
    if env_overrides:
        env.update(env_overrides)

    factory = subprocess_factory or _default_subprocess_factory

    if progress_cb:
        progress_cb(
            AnalyzeProgress(
                stage=STAGE_STARTING,
                message=f"Starting analyze: {' '.join(argv[:2])} …",
            )
        )

    result = AnalyzeJobResult(
        ok=False,
        return_code=-1,
        transcripts_path=out_dir / "transcripts.jsonl",
        speakers_yaml_path=out_dir / "speakers.yaml",
        report_path=out_dir / "report.md",
    )

    try:
        proc = factory(argv, env)
    except OSError as exc:
        msg = f"Could not launch analyze subprocess: {exc}"
        result.error = msg
        if progress_cb:
            progress_cb(AnalyzeProgress(stage=STAGE_ERROR, message=msg))
        return result

    try:
        _pump_stdout(
            proc=proc,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            log_lines=result.log_lines,
        )
    except _CancelledError:
        result.return_code = -1
        result.error = "Cancelled"
        if progress_cb:
            progress_cb(AnalyzeProgress(stage=STAGE_ERROR, message="Cancelled"))
        return result

    rc = proc.wait()
    result.return_code = int(rc)
    if rc == 0:
        result.ok = True
        if progress_cb:
            progress_cb(AnalyzeProgress(stage=STAGE_DONE, message="Analyze finished."))
    else:
        tail = result.log_lines[-1] if result.log_lines else ""
        result.error = f"analyze exited with code {rc}. Last line: {tail}"
        if progress_cb:
            progress_cb(AnalyzeProgress(stage=STAGE_ERROR, message=result.error))
    return result


class _CancelledError(Exception):
    """Raised internally when ``cancel_event`` is set mid-stream."""


def _pump_stdout(
    *,
    proc: "_ProcessProtocol",
    progress_cb: Optional[Callable[[AnalyzeProgress], None]],
    cancel_event: Optional[threading.Event],
    log_lines: list[str],
) -> None:
    """Read every stdout line, emit progress events, honour cancel.

    Split out of :func:`run_analyze` so the same pump is used by the
    real subprocess and the test fake without duplication.
    """
    stdout = proc.stdout
    if stdout is None:  # pragma: no cover - Popen always sets this for PIPE
        return
    for raw in stdout:
        if cancel_event is not None and cancel_event.is_set():
            try:
                proc.terminate()
            except Exception:  # pragma: no cover - best-effort cancel
                pass
            raise _CancelledError()

        line = raw.rstrip()
        log_lines.append(line)

        parsed = _parse_stamp(line)
        if parsed is not None:
            stage, elapsed = parsed
            if progress_cb:
                progress_cb(
                    AnalyzeProgress(stage=stage, message=line, elapsed_s=elapsed)
                )
            continue
        if progress_cb and line:
            progress_cb(AnalyzeProgress(stage=STAGE_LINE, message=line))
