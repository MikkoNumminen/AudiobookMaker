"""Subprocess bridge between the Tkinter launcher and the Chatterbox runner.

The Chatterbox full-book runner (``scripts/generate_chatterbox_audiobook.py``)
is a separate Python script so we can keep the heavy dependencies
(``torch``, ``chatterbox-tts``, ``silero-vad``) out of the launcher's import
graph. When the user picks Chatterbox in the launcher GUI, the launcher
spawns the runner as a subprocess and streams its stdout through the parser
in this module to drive the progress bar.

Edge-TTS and Piper engines don't need this bridge — they run in-process via
``src.tts_engine.text_to_speech`` on a background thread. See ``launcher.py``
for the dispatch logic.

Why a parser instead of a ``--json-progress`` flag: the runner already uses
``print(..., flush=True)`` for every meaningful event, stdout is line-
buffered, and there is no ``tqdm`` bleeding ``\\r`` carriage returns into the
stream. Regex parsing is cheap and avoids churn on the runner script.
"""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass
class ProgressEvent:
    """One progress event parsed from the runner's stdout."""

    kind: str
    """One of: setup_total, setup_cached, chapter_start, chunk, chapter_done,
    full_done, done, error, signal, log, exit."""

    chapter_idx: int = 0  # 1-based
    chapter_total: int = 0
    chunk_idx: int = 0  # 1-based, within current chapter
    chunk_total: int = 0  # total chunks in current chapter
    total_done: int = 0  # cumulative chunks finished across all chapters
    total_chunks: int = 0  # cumulative total
    elapsed_s: float = 0.0
    eta_s: float = 0.0
    rtf: float = 0.0
    output_path: str = ""
    returncode: int = 0
    raw_line: str = ""


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------


class ChatterboxLineParser:
    """Parse ``generate_chatterbox_audiobook.py`` stdout into ``ProgressEvent``s.

    The runner currently emits lines in these shapes:

        [setup] out=...
        [setup] total chunks to synthesize: 1043
        [setup] cached chunks found: 215/1043
        [chapter 3/8] idx=... title=... chunks=126
        [chapter 3/8] chunk 42/126 (215/1043 total) - 12m30s elapsed,
            ~65m00s remaining, RTF 0.17x
        [chapter 3/8] assembling MP3...
        [chapter 3/8] wrote 03_foo.mp3 (1820.3s)
        [full] concatenating 8 chapters
        [full] wrote /abs/path/00_full.mp3 (12345.6s)
        [done] 1043/1043 chunks, 3h05m wall-clock
        [error] ...
        [signal] Ctrl-C received...

    Unmatched non-empty lines still produce a ``log`` event so nothing
    disappears from the UI's log panel.
    """

    _CHUNK_RE = re.compile(
        r"^\[chapter (\d+)/(\d+)\] chunk (\d+)/(\d+) \((\d+)/(\d+) total\) "
        r"- (\S+) elapsed, ~(\S+) remaining, RTF ([\d.]+)x"
    )
    _SETUP_TOTAL_RE = re.compile(r"^\[setup\] total chunks to synthesize: (\d+)")
    _SETUP_CACHED_RE = re.compile(r"^\[setup\] cached chunks found: (\d+)/(\d+)")
    _CHAPTER_START_RE = re.compile(
        r"^\[chapter (\d+)/(\d+)\] idx=(\d+) title=(.+?) chunks=(\d+)"
    )
    _CHAPTER_WROTE_RE = re.compile(
        r"^\[chapter (\d+)/(\d+)\] wrote (\S+\.mp3) \(([\d.]+)s\)"
    )
    _FULL_WROTE_RE = re.compile(r"^\[full\] wrote (\S+\.mp3) \(([\d.]+)s\)")
    _DONE_RE = re.compile(r"^\[done\] (\d+)/(\d+) chunks")
    _ERROR_RE = re.compile(r"^\[error\]")
    _SIGNAL_RE = re.compile(r"^\[signal\]")

    _HMS_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")

    @classmethod
    def parse_hms(cls, s: str) -> float:
        """Parse ``"12m30s"`` / ``"1h23m"`` / ``"45s"`` into seconds."""
        m = cls._HMS_RE.match(s.strip())
        if not m:
            return 0.0
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        se = int(m.group(3) or 0)
        return h * 3600 + mi * 60 + se

    def parse(self, line: str) -> ProgressEvent:
        """Classify one line into a ``ProgressEvent``."""
        line = line.rstrip("\r\n")

        m = self._CHUNK_RE.match(line)
        if m:
            return ProgressEvent(
                kind="chunk",
                chapter_idx=int(m.group(1)),
                chapter_total=int(m.group(2)),
                chunk_idx=int(m.group(3)),
                chunk_total=int(m.group(4)),
                total_done=int(m.group(5)),
                total_chunks=int(m.group(6)),
                elapsed_s=self.parse_hms(m.group(7)),
                eta_s=self.parse_hms(m.group(8)),
                rtf=float(m.group(9)),
                raw_line=line,
            )

        m = self._SETUP_TOTAL_RE.match(line)
        if m:
            return ProgressEvent(
                kind="setup_total", total_chunks=int(m.group(1)), raw_line=line
            )

        m = self._SETUP_CACHED_RE.match(line)
        if m:
            return ProgressEvent(
                kind="setup_cached",
                total_done=int(m.group(1)),
                total_chunks=int(m.group(2)),
                raw_line=line,
            )

        m = self._CHAPTER_START_RE.match(line)
        if m:
            return ProgressEvent(
                kind="chapter_start",
                chapter_idx=int(m.group(1)),
                chapter_total=int(m.group(2)),
                chunk_total=int(m.group(5)),
                raw_line=line,
            )

        m = self._CHAPTER_WROTE_RE.match(line)
        if m:
            return ProgressEvent(
                kind="chapter_done",
                chapter_idx=int(m.group(1)),
                chapter_total=int(m.group(2)),
                output_path=m.group(3),
                raw_line=line,
            )

        m = self._FULL_WROTE_RE.match(line)
        if m:
            return ProgressEvent(
                kind="full_done", output_path=m.group(1), raw_line=line
            )

        m = self._DONE_RE.match(line)
        if m:
            return ProgressEvent(
                kind="done",
                total_done=int(m.group(1)),
                total_chunks=int(m.group(2)),
                raw_line=line,
            )

        if self._ERROR_RE.match(line):
            return ProgressEvent(kind="error", raw_line=line)
        if self._SIGNAL_RE.match(line):
            return ProgressEvent(kind="signal", raw_line=line)

        return ProgressEvent(kind="log", raw_line=line)


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------


EventCallback = Callable[[ProgressEvent], None]


@dataclass
class _RunnerState:
    """Mutable state held by a ``ChatterboxRunner`` instance."""

    proc: Optional[subprocess.Popen] = None
    reader: Optional[threading.Thread] = None
    waiter: Optional[threading.Thread] = None
    event_queue: queue.Queue = field(default_factory=queue.Queue)
    tail: deque = field(default_factory=lambda: deque(maxlen=500))
    done: threading.Event = field(default_factory=threading.Event)


class ChatterboxRunner:
    """Spawn the Chatterbox runner script as a subprocess and stream events.

    Usage::

        runner = ChatterboxRunner(
            python_exe="/path/to/.venv-chatterbox/bin/python",
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path="/path/to/book.pdf",
            out_dir="/path/to/dist/audiobook",
        )
        runner.start()
        while not runner.finished:
            ev = runner.poll_event(timeout=0.1)
            if ev is not None:
                ...  # update UI
        runner.join()

    Call ``cancel()`` at any time to send SIGINT — the runner script catches
    it and saves partial progress before exiting.
    """

    def __init__(
        self,
        python_exe: str,
        script_path: str,
        pdf_path: str,
        out_dir: str,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.python_exe = python_exe
        self.script_path = script_path
        self.pdf_path = pdf_path
        self.out_dir = out_dir
        self.extra_args = extra_args or []
        self._state = _RunnerState()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the subprocess and begin streaming events."""
        if self._state.proc is not None:
            raise RuntimeError("runner already started")

        parser = ChatterboxLineParser()
        argv = [
            self.python_exe,
            "-u",
            self.script_path,
            "--pdf",
            self.pdf_path,
            "--out",
            self.out_dir,
            "--device",
            "auto",
            *self.extra_args,
        ]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Silence tqdm progress bars from HuggingFace downloads — their
        # carriage returns would pollute the line-based parser.
        env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        env["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
        env["TQDM_DISABLE"] = "1"

        creationflags = 0
        if sys.platform == "win32":
            # Needed so we can deliver CTRL_C_EVENT to the child without
            # killing the launcher process too. See cancel() below.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        self._state.proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )

        self._state.reader = threading.Thread(
            target=self._reader_loop,
            args=(parser,),
            daemon=True,
            name="chatterbox-reader",
        )
        self._state.reader.start()

        self._state.waiter = threading.Thread(
            target=self._waiter_loop,
            daemon=True,
            name="chatterbox-waiter",
        )
        self._state.waiter.start()

    def cancel(self) -> None:
        """Send a clean cancel signal. The runner finishes the current chunk
        then exits with code 0 and a ``[signal]`` marker."""
        proc = self._state.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                # CTRL_C_EVENT is the only way to raise SIGINT in the child
                # process group on Windows.
                proc.send_signal(signal.CTRL_C_EVENT)  # type: ignore[attr-defined]
            else:
                proc.send_signal(signal.SIGINT)
        except (OSError, ProcessLookupError):
            pass

    def poll_event(self, timeout: float = 0.0) -> Optional[ProgressEvent]:
        """Return the next queued event or ``None`` if nothing available."""
        try:
            return self._state.event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def finished(self) -> bool:
        """True once the subprocess has exited AND the reader has drained."""
        return self._state.done.is_set() and self._state.event_queue.empty()

    def tail_lines(self, n: int = 20) -> list[str]:
        """Return the last ``n`` raw stdout lines for error dialogs."""
        return list(self._state.tail)[-n:]

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for reader + waiter threads to finish."""
        if self._state.reader is not None:
            self._state.reader.join(timeout=timeout)
        if self._state.waiter is not None:
            self._state.waiter.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal threads
    # ------------------------------------------------------------------

    def _reader_loop(self, parser: ChatterboxLineParser) -> None:
        proc = self._state.proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                self._state.tail.append(line)
                ev = parser.parse(line)
                self._state.event_queue.put(ev)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _waiter_loop(self) -> None:
        proc = self._state.proc
        assert proc is not None
        rc = proc.wait()
        # Reader finishes on stdout EOF, which happens as the child exits.
        if self._state.reader is not None:
            self._state.reader.join(timeout=5.0)
        self._state.event_queue.put(
            ProgressEvent(kind="exit", returncode=rc)
        )
        self._state.done.set()


# ---------------------------------------------------------------------------
# Convenience: resolve the Chatterbox runner's Python interpreter.
# ---------------------------------------------------------------------------


def resolve_chatterbox_python() -> Optional[Path]:
    """Return the path to the Python that should run the Chatterbox script.

    Preference order:
        1. ``CHATTERBOX_PYTHON`` environment variable (escape hatch for tests)
        2. ``.venv-chatterbox`` next to the repo/app root
        3. ``C:\\AudiobookMaker\\.venv-chatterbox`` (default install path)
        4. ``None`` if no Chatterbox venv is detected

    The launcher should show a friendly "Chatterbox not installed" message if
    this returns ``None``.
    """
    override = os.environ.get("CHATTERBOX_PYTHON")
    if override:
        p = Path(override)
        if p.exists():
            return p

    suffix = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")

    # Check relative to the repo/app root.
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / ".venv-chatterbox" / suffix[0] / suffix[1]
    if candidate.exists():
        return candidate

    # Check the default install location used by the in-app installer and
    # the old launcher installer.
    if sys.platform == "win32":
        default = Path(r"C:\AudiobookMaker\.venv-chatterbox") / suffix[0] / suffix[1]
        if default.exists():
            return default

    return None
