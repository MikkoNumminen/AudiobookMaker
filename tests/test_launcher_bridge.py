"""Unit tests for src/launcher_bridge.py.

Focuses on the pure ``ChatterboxLineParser`` — the subprocess runner is a
thin shell around ``subprocess.Popen`` and is exercised by manual smoke
tests in Phase 2 (cross-platform, needs CUDA on a real machine). The parser
is the brain that turns raw runner stdout into structured events the GUI
can consume, so it gets full coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.launcher_bridge import (  # noqa: E402
    ChatterboxLineParser,
    ChatterboxRunner,
    ProgressEvent,
    resolve_chatterbox_python,
)


# ---------------------------------------------------------------------------
# parse_hms helper
# ---------------------------------------------------------------------------


class TestParseHms:
    def test_seconds_only(self) -> None:
        assert ChatterboxLineParser.parse_hms("45s") == 45

    def test_minutes_seconds(self) -> None:
        assert ChatterboxLineParser.parse_hms("12m30s") == 12 * 60 + 30

    def test_hours_minutes(self) -> None:
        assert ChatterboxLineParser.parse_hms("1h23m") == 3600 + 23 * 60

    def test_hours_only(self) -> None:
        assert ChatterboxLineParser.parse_hms("3h") == 3 * 3600

    def test_zero(self) -> None:
        assert ChatterboxLineParser.parse_hms("0s") == 0

    def test_malformed_returns_zero(self) -> None:
        assert ChatterboxLineParser.parse_hms("not-a-duration") == 0

    def test_empty(self) -> None:
        assert ChatterboxLineParser.parse_hms("") == 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> ChatterboxLineParser:
    return ChatterboxLineParser()


class TestParseChunkLine:
    """The per-chunk progress line is the primary event driving the UI."""

    def test_basic_chunk_line(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse(
            "[chapter 3/8] chunk 42/126 (215/1043 total) - "
            "12m30s elapsed, ~65m00s remaining, RTF 0.17x"
        )
        assert ev.kind == "chunk"
        assert ev.chapter_idx == 3
        assert ev.chapter_total == 8
        assert ev.chunk_idx == 42
        assert ev.chunk_total == 126
        assert ev.total_done == 215
        assert ev.total_chunks == 1043
        assert ev.elapsed_s == 12 * 60 + 30
        assert ev.eta_s == 65 * 60
        assert ev.rtf == 0.17

    def test_first_chunk_of_first_chapter(
        self, parser: ChatterboxLineParser
    ) -> None:
        ev = parser.parse(
            "[chapter 1/1] chunk 1/3 (1/3 total) - 0m05s elapsed, "
            "~0m10s remaining, RTF 6.50x"
        )
        assert ev.kind == "chunk"
        assert ev.chapter_idx == 1
        assert ev.chunk_idx == 1
        assert ev.total_done == 1
        assert ev.total_chunks == 3
        assert ev.rtf == 6.50

    def test_chunk_with_hours_in_elapsed(
        self, parser: ChatterboxLineParser
    ) -> None:
        ev = parser.parse(
            "[chapter 5/8] chunk 80/126 (400/1043 total) - "
            "1h23m elapsed, ~1h05m remaining, RTF 0.20x"
        )
        assert ev.elapsed_s == 3600 + 23 * 60
        assert ev.eta_s == 3600 + 5 * 60


class TestParseSetupLines:
    def test_setup_total(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse("[setup] total chunks to synthesize: 1043")
        assert ev.kind == "setup_total"
        assert ev.total_chunks == 1043

    def test_setup_cached(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse("[setup] cached chunks found: 215/1043")
        assert ev.kind == "setup_cached"
        assert ev.total_done == 215
        assert ev.total_chunks == 1043

    def test_setup_cached_zero(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse("[setup] cached chunks found: 0/1043")
        assert ev.kind == "setup_cached"
        assert ev.total_done == 0
        assert ev.total_chunks == 1043


class TestParseChapterLines:
    def test_chapter_start(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse(
            "[chapter 3/8] idx=14 title=Uudella ajalla chunks=126"
        )
        assert ev.kind == "chapter_start"
        assert ev.chapter_idx == 3
        assert ev.chapter_total == 8
        assert ev.chunk_total == 126

    def test_chapter_done(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse(
            "[chapter 3/8] wrote 03_uudella_ajalla.mp3 (1820.3s)"
        )
        assert ev.kind == "chapter_done"
        assert ev.chapter_idx == 3
        assert ev.chapter_total == 8
        assert ev.output_path == "03_uudella_ajalla.mp3"


class TestParseFullAndDone:
    def test_full_wrote(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse(
            "[full] wrote /abs/path/00_full.mp3 (12345.6s)"
        )
        assert ev.kind == "full_done"
        assert ev.output_path == "/abs/path/00_full.mp3"

    def test_done_summary(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse("[done] 1043/1043 chunks, 3h05m wall-clock")
        assert ev.kind == "done"
        assert ev.total_done == 1043
        assert ev.total_chunks == 1043


class TestParseSignals:
    def test_error_line(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse("[error] something exploded")
        assert ev.kind == "error"

    def test_signal_line(self, parser: ChatterboxLineParser) -> None:
        ev = parser.parse(
            "[signal] Ctrl-C received, finishing current chunk"
        )
        assert ev.kind == "signal"


class TestParseUnknownLines:
    def test_random_text_becomes_log(
        self, parser: ChatterboxLineParser
    ) -> None:
        ev = parser.parse("WARNING: some deprecation notice")
        assert ev.kind == "log"
        assert ev.raw_line == "WARNING: some deprecation notice"

    def test_empty_lines_also_parse_as_log(
        self, parser: ChatterboxLineParser
    ) -> None:
        # Bridge's reader loop filters empty lines before this runs, but the
        # parser must still handle them gracefully.
        ev = parser.parse("")
        assert ev.kind == "log"

    def test_strips_trailing_newlines(
        self, parser: ChatterboxLineParser
    ) -> None:
        ev = parser.parse(
            "[chapter 1/1] chunk 1/1 (1/1 total) - 0m05s elapsed, "
            "~0m00s remaining, RTF 1.00x\r\n"
        )
        assert ev.kind == "chunk"
        assert ev.rtf == 1.00


# ---------------------------------------------------------------------------
# Full-run parse — feed a realistic transcript and assert the event sequence.
# ---------------------------------------------------------------------------


REALISTIC_TRANSCRIPT = """\
[setup] out=/abs/dist/audiobook/book
[setup] total chunks to synthesize: 3
[setup] cached chunks found: 0/3
[chapter 1/1] idx=14 title=Uudella ajalla chunks=3
[chapter 1/1] chunk 1/3 (1/3 total) - 0m20s elapsed, ~0m40s remaining, RTF 0.15x
[chapter 1/1] chunk 2/3 (2/3 total) - 0m40s elapsed, ~0m20s remaining, RTF 0.15x
[chapter 1/1] chunk 3/3 (3/3 total) - 1m00s elapsed, ~0m00s remaining, RTF 0.15x
[chapter 1/1] assembling MP3...
[chapter 1/1] wrote 01_uudella_ajalla.mp3 (57.2s)
[full] wrote /abs/dist/audiobook/book/00_full.mp3 (57.2s)
[done] 3/3 chunks, 1m00s wall-clock
"""


class TestFullTranscriptParse:
    def test_realistic_run_produces_expected_event_kinds(
        self, parser: ChatterboxLineParser
    ) -> None:
        events = [parser.parse(line) for line in REALISTIC_TRANSCRIPT.splitlines()]
        kinds = [ev.kind for ev in events]
        assert kinds == [
            "log",  # [setup] out=...
            "setup_total",
            "setup_cached",
            "chapter_start",
            "chunk",
            "chunk",
            "chunk",
            "log",  # assembling
            "chapter_done",
            "full_done",
            "done",
        ]

    def test_final_full_path_is_captured(
        self, parser: ChatterboxLineParser
    ) -> None:
        events = [parser.parse(line) for line in REALISTIC_TRANSCRIPT.splitlines()]
        full = [ev for ev in events if ev.kind == "full_done"]
        assert len(full) == 1
        assert full[0].output_path == "/abs/dist/audiobook/book/00_full.mp3"

    def test_total_chunks_progresses_monotonically(
        self, parser: ChatterboxLineParser
    ) -> None:
        events = [parser.parse(line) for line in REALISTIC_TRANSCRIPT.splitlines()]
        chunks = [ev for ev in events if ev.kind == "chunk"]
        totals = [ev.total_done for ev in chunks]
        assert totals == [1, 2, 3]


# ---------------------------------------------------------------------------
# resolve_chatterbox_python()
# ---------------------------------------------------------------------------


class TestResolveChatterboxPython:
    def test_env_override_wins(self, tmp_path, monkeypatch) -> None:
        fake = tmp_path / "python3"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CHATTERBOX_PYTHON", str(fake))
        assert resolve_chatterbox_python() == fake

    def test_env_override_missing_falls_back(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "CHATTERBOX_PYTHON", str(tmp_path / "nonexistent")
        )
        # Falls through to venv detection; may or may not find one.
        result = resolve_chatterbox_python()
        assert result is None or result.exists()


# ---------------------------------------------------------------------------
# ChatterboxRunner — constructor only (full spawn needs a real subprocess).
# ---------------------------------------------------------------------------


class TestRunnerConstruction:
    def test_constructor_stores_args(self, tmp_path) -> None:
        runner = ChatterboxRunner(
            python_exe=str(tmp_path / "python"),
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path=str(tmp_path / "book.pdf"),
            out_dir=str(tmp_path / "out"),
            extra_args=["--chapters", "1"],
        )
        assert runner.pdf_path == str(tmp_path / "book.pdf")
        assert runner.extra_args == ["--chapters", "1"]

    def test_language_defaults_to_fi(self, tmp_path) -> None:
        runner = ChatterboxRunner(
            python_exe=str(tmp_path / "python"),
            script_path="s.py",
            pdf_path="/tmp/x.pdf",
            out_dir="/tmp/out",
        )
        assert runner.language == "fi"

    def test_language_kwarg_stored(self, tmp_path) -> None:
        runner = ChatterboxRunner(
            python_exe=str(tmp_path / "python"),
            script_path="s.py",
            pdf_path="/tmp/x.pdf",
            out_dir="/tmp/out",
            language="en",
        )
        assert runner.language == "en"

    def test_finished_is_false_before_start(self, tmp_path) -> None:
        runner = ChatterboxRunner(
            python_exe=str(tmp_path / "python"),
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path="/tmp/x.pdf",
            out_dir="/tmp/out",
        )
        assert not runner.finished
        assert runner.tail_lines() == []

    def test_cancel_before_start_is_noop(self, tmp_path) -> None:
        runner = ChatterboxRunner(
            python_exe=str(tmp_path / "python"),
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path="/tmp/x.pdf",
            out_dir="/tmp/out",
        )
        # Must not raise.
        runner.cancel()

    def test_double_start_raises(self, tmp_path, monkeypatch) -> None:
        runner = ChatterboxRunner(
            python_exe=sys.executable,  # any real python, even if the script
            script_path="scripts/generate_chatterbox_audiobook.py",
            pdf_path="/tmp/definitely-missing.pdf",
            out_dir="/tmp/out",
        )
        # Prevent the subprocess from actually spawning.
        class _FakeProc:
            def poll(self) -> int:
                return 0

            stdout = None

            def wait(self, timeout=None) -> int:
                return 0

        import subprocess as _sp

        monkeypatch.setattr(
            _sp, "Popen", lambda *a, **kw: _FakeProc()
        )
        runner._state.proc = _FakeProc()  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="already started"):
            runner.start()


# ---------------------------------------------------------------------------
# ProgressEvent dataclass sanity
# ---------------------------------------------------------------------------


class TestAlignmentNoiseRewrite:
    """Upstream chatterbox's AlignmentStreamAnalyzer prints two scary-
    sounding lines whenever our gemination fix forces an EOS. Reframe them
    into a single neutral ``[info]`` line so the GUI log doesn't go yellow
    for a working fix.
    """

    def test_warning_line_becomes_neutral_info(
        self, parser: ChatterboxLineParser
    ) -> None:
        out = ChatterboxLineParser.rewrite_alignment_noise(
            "WARNING: Detected 2x repetition of token 123 at position 45, "
            "forcing EOS"
        )
        assert out is not None
        assert "[info]" in out
        assert "alignment fix applied" in out
        assert "token 123" in out
        assert "position 45" in out
        # Must NOT contain severity keywords that would route the line to
        # the warning-yellow or error-red GUI color.
        upper = out.upper()
        assert "WARNING" not in upper
        assert "ERROR" not in upper

    def test_warning_without_position_still_rewrites(
        self, parser: ChatterboxLineParser
    ) -> None:
        out = ChatterboxLineParser.rewrite_alignment_noise(
            "WARNING: Detected 3x repetition of token 42"
        )
        assert out is not None
        assert "token 42" in out
        assert "WARNING" not in out.upper()

    def test_forcing_eos_line_is_suppressed(self) -> None:
        out = ChatterboxLineParser.rewrite_alignment_noise(
            "Forcing EOS generation to prevent loop."
        )
        assert out is None

    def test_unrelated_line_passes_through(self) -> None:
        line = "[setup] total chunks to synthesize: 10"
        assert ChatterboxLineParser.rewrite_alignment_noise(line) == line

    def test_rewritten_line_parses_as_plain_log(
        self, parser: ChatterboxLineParser
    ) -> None:
        rewritten = ChatterboxLineParser.rewrite_alignment_noise(
            "WARNING: Detected 2x repetition of token 7 at position 3"
        )
        assert rewritten is not None
        ev = parser.parse(rewritten)
        # A plain log event, not an error/warning kind — severity routing
        # in the GUI is purely keyword-based on raw_line, and the rewritten
        # line contains neither "WARNING" nor "ERROR".
        assert ev.kind == "log"
        assert "WARNING" not in ev.raw_line.upper()
        assert "ERROR" not in ev.raw_line.upper()


class TestProgressEvent:
    def test_default_construction(self) -> None:
        ev = ProgressEvent(kind="log")
        assert ev.kind == "log"
        assert ev.chapter_idx == 0
        assert ev.total_chunks == 0
        assert ev.output_path == ""
        assert ev.raw_line == ""

    def test_kind_is_required(self) -> None:
        with pytest.raises(TypeError):
            ProgressEvent()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# _reader_loop invariants (stdout is always closed)
# ---------------------------------------------------------------------------


class _FakeReadline:
    """Minimal stand-in for proc.stdout that exposes the readline() +
    close() pair the reader thread expects."""

    def __init__(self, lines):
        self._lines = list(lines)
        self._i = 0
        self.closed = False

    def readline(self):
        if self._i >= len(self._lines):
            return ""
        line = self._lines[self._i]
        self._i += 1
        return line

    def close(self):
        self.closed = True


class _FakeState:
    """_RunnerState stand-in: only the fields _reader_loop actually
    touches need to exist."""

    def __init__(self, stdout):
        from collections import deque
        from queue import Queue as _Queue

        self.proc = type("P", (), {"stdout": stdout})()
        self.tail = deque(maxlen=20)
        self.event_queue = _Queue()


class TestReaderLoopFinallyClose:
    """Regression tests: _reader_loop must close its stdout pipe on
    every exit path. A leaked pipe lets the child block on a full PIPE
    buffer and turns a graceful exit into a hang."""

    def _run_reader(self, lines, parser_override=None):
        pipe = _FakeReadline(lines)
        state = _FakeState(pipe)
        runner = ChatterboxRunner.__new__(ChatterboxRunner)
        runner._state = state
        parser = parser_override if parser_override is not None else ChatterboxLineParser()
        return runner, pipe, parser

    def test_happy_path_closes_pipe(self) -> None:
        """Normal exit (EOF) must leave the pipe closed."""
        runner, pipe, parser = self._run_reader(
            ["[setup] out=foo\n", "[setup] total chunks to synthesize: 5\n"]
        )
        runner._reader_loop(parser)
        assert pipe.closed is True

    def test_exception_in_loop_body_still_closes_pipe(self) -> None:
        """If the parser raises inside the for-loop the pipe must still
        be closed by the finally block, the exception must be stashed
        on `state.reader_error` so `join()` can surface it, and a
        synthetic error event must be queued so the GUI poll loop sees
        something instead of starving.

        The reader runs in a daemon thread — re-raising into nothing
        would silently kill the thread and hang the GUI forever. The
        2026-05-19 audit caught that and the fix moved the contract
        to: never raise, but stash + signal."""

        class _ExplodingParser:
            @staticmethod
            def rewrite_alignment_noise(line):
                return line  # pass-through

            def parse(self, line):
                raise RuntimeError("parser blew up")

        runner, pipe, _ = self._run_reader(
            ["[setup] total chunks to synthesize: 5\n"],
            parser_override=_ExplodingParser(),
        )
        # No longer raises into the (daemon) caller — must return
        # normally and stash the exception.
        runner._reader_loop(_ExplodingParser())
        assert pipe.closed is True
        assert isinstance(runner._state.reader_error, RuntimeError)
        assert "parser blew up" in str(runner._state.reader_error)
        # The synthetic error event lets the GUI poll loop unstuck.
        queued = []
        while not runner._state.event_queue.empty():
            queued.append(runner._state.event_queue.get_nowait())
        assert any(
            ev.kind == "error" and "reader thread crashed" in ev.raw_line
            for ev in queued
        )


# ---------------------------------------------------------------------------
# _waiter_loop bounded shutdown
# ---------------------------------------------------------------------------


class _WaiterState:
    """Minimal state for _waiter_loop: proc + reader + event_queue + done.

    Mirrors the fields of ``_RunnerState`` that ``_waiter_loop`` and its
    helpers touch — including ``cancel_requested``, which gates whether the
    waiter escalates to terminate/kill or just keeps waiting for the runner
    to exit on its own.
    """

    def __init__(self, proc):
        from queue import Queue as _Queue
        from threading import Event as _Event

        self.proc = proc
        self.reader = None
        self.event_queue = _Queue()
        self.done = _Event()
        self.cancel_requested = _Event()
        self.reader_error = None
        self.waiter_error = None


def _make_runner_with_proc(proc, *, cancel: bool = False):
    runner = ChatterboxRunner.__new__(ChatterboxRunner)
    runner._state = _WaiterState(proc)
    if cancel:
        runner._state.cancel_requested.set()
    return runner


class TestWaiterLoopNaturalCompletion:
    """A normal, un-cancelled run must wait for the runner to exit on its
    own — for as long as it takes. The waiter polls in short slices but
    NEVER escalates to terminate/kill while waiting for natural completion.

    Regression guard: a previous version bounded the *initial* wait at the
    60s shutdown budget and terminated the runner the moment it ran longer
    — which killed every synthesis >60s and broke the final MP3-assembly
    phase on long books."""

    def test_happy_path_reports_clean_rc(self) -> None:
        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0

        runner = _make_runner_with_proc(mock_proc)
        runner._waiter_loop()

        # The poll wait is bounded (so a cancel can be honoured), but small.
        first_call = mock_proc.wait.call_args_list[0]
        assert first_call.kwargs.get("timeout") is not None
        assert first_call.kwargs["timeout"] > 0
        mock_proc.terminate.assert_not_called()
        mock_proc.kill.assert_not_called()
        ev = runner._state.event_queue.get_nowait()
        assert ev.kind == "exit"
        assert ev.returncode == 0

    def test_long_run_is_never_terminated_without_cancel(self) -> None:
        """The runner stays busy across many poll slices (simulating a
        multi-minute synthesis + assembly) and finally exits cleanly. With
        no cancel request, the waiter must never terminate() or kill() it."""
        import subprocess as _sp

        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        # Five poll slices time out (still running), then a clean exit.
        mock_proc.wait.side_effect = [
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            0,
        ]

        runner = _make_runner_with_proc(mock_proc)
        runner._waiter_loop()

        mock_proc.terminate.assert_not_called()
        mock_proc.kill.assert_not_called()
        ev = runner._state.event_queue.get_nowait()
        assert ev.kind == "exit"
        assert ev.returncode == 0


class TestWaiterLoopCancelEscalation:
    """Once cancel() has set ``cancel_requested``, the waiter gives the
    runner the shutdown budget to finish its current chunk, then SIGTERM +
    short grace, then SIGKILL — so a stuck runner can never pin shutdown."""

    def test_cancel_escalates_to_terminate_then_kill(self) -> None:
        import subprocess as _sp

        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        # poll slice (still running) -> cancel seen -> shutdown budget hangs
        # -> terminate grace hangs -> kill returns.
        mock_proc.wait.side_effect = [
            _sp.TimeoutExpired(cmd="runner", timeout=1),
            _sp.TimeoutExpired(cmd="runner", timeout=60),
            _sp.TimeoutExpired(cmd="runner", timeout=5),
            -9,
        ]

        runner = _make_runner_with_proc(mock_proc, cancel=True)
        runner._waiter_loop()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        ev = runner._state.event_queue.get_nowait()
        assert ev.kind == "exit"
        assert ev.returncode == -9

    def test_cancel_clean_exit_within_budget_skips_terminate(self) -> None:
        """If the runner honours SIGINT and exits inside the shutdown
        budget, neither terminate() nor kill() is called."""
        import subprocess as _sp

        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = [
            _sp.TimeoutExpired(cmd="runner", timeout=1),  # poll slice
            0,  # clean exit within the shutdown budget
        ]

        runner = _make_runner_with_proc(mock_proc, cancel=True)
        runner._waiter_loop()

        mock_proc.terminate.assert_not_called()
        mock_proc.kill.assert_not_called()
        ev = runner._state.event_queue.get_nowait()
        assert ev.returncode == 0

    def test_cancel_terminate_grace_skips_kill(self) -> None:
        """terminate() is enough — the child exits within the grace window —
        so kill() must not be called."""
        import subprocess as _sp

        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = [
            _sp.TimeoutExpired(cmd="runner", timeout=1),   # poll slice
            _sp.TimeoutExpired(cmd="runner", timeout=60),  # shutdown budget
            0,  # terminate grace: child exits cleanly
        ]

        runner = _make_runner_with_proc(mock_proc, cancel=True)
        runner._waiter_loop()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()
        ev = runner._state.event_queue.get_nowait()
        assert ev.returncode == 0
