"""Tests for the runner watchdog: idle silence, not total runtime.

Field incident (2026-08-14): a conversion estimated at ~14 hours stopped
overnight. The waiter had an absolute 12-hour ceiling on total runtime, with a
comment asserting that "even a very long book finishes well inside this". A
book-length conversion on a mid-range GPU is a 14-hour job, so the ceiling
terminated a perfectly healthy run at the 12-hour mark.

Total elapsed time cannot distinguish "slow" from "stuck". Silence can: the
runner prints a line per chunk, so a long gap with nothing on stdout is the
real signal that it stopped working.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.launcher_bridge import ChatterboxRunner, _RunnerState


class _FakeProc:
    """A process that never exits on its own."""

    def __init__(self) -> None:
        self.terminated = False

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="runner", timeout=timeout)


@pytest.fixture
def runner():
    r = ChatterboxRunner.__new__(ChatterboxRunner)
    r._state = _RunnerState()
    r._state.proc = _FakeProc()
    return r


class TestCeilingIsNotTotalRuntime:
    def test_a_fourteen_hour_run_is_not_terminated_while_it_talks(self, runner):
        """The regression: 14 h of healthy work was killed at 12 h.

        Time is advanced past the old ceiling while stdout keeps ticking, so
        the only thing that could stop this run is a total-runtime rule.
        """
        clock = {"t": 0.0}
        escalated: list = []

        def fake_monotonic():
            clock["t"] += 60.0          # one minute per poll
            # The runner is talking: refresh the idle marker every tick.
            runner._state.last_output_at = clock["t"]
            if clock["t"] > 14 * 3600.0:
                raise _Done()
            return clock["t"]

        class _Done(Exception):
            pass

        with patch("src.launcher_bridge.time.monotonic", fake_monotonic), \
                patch.object(runner, "_escalate_shutdown",
                             side_effect=lambda p: escalated.append(p) or 0):
            with pytest.raises(_Done):
                runner._wait_for_runner_exit(runner._state.proc)

        assert escalated == [], "a talking runner was terminated on elapsed time"

    def test_absolute_backstop_is_far_beyond_any_real_book(self):
        assert ChatterboxRunner._MAX_RUN_S >= 48 * 3600.0


class TestIdleTimeout:
    def test_a_silent_runner_is_terminated(self, runner):
        """No stdout for longer than the idle budget means wedged."""
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 60.0
            return clock["t"]

        # last_output_at stays at 0.0: the runner never speaks again.
        runner._state.last_output_at = 0.0

        with patch("src.launcher_bridge.time.monotonic", fake_monotonic), \
                patch.object(runner, "_escalate_shutdown", return_value=7) as esc:
            rc = runner._wait_for_runner_exit(runner._state.proc)

        assert esc.call_count == 1
        assert rc == 7

    def test_idle_budget_is_generous_enough_for_assembly(self):
        """Final MP3 assembly runs for many minutes with no chunk lines.

        Too tight an idle budget would kill a run during its last step, which
        is the most expensive possible moment to lose.
        """
        assert ChatterboxRunner._MAX_IDLE_S >= 30 * 60.0

    def test_output_refreshes_the_idle_marker(self, runner):
        """The reader must stamp the marker, or every long run looks wedged."""
        import src.launcher_bridge as lb
        before = runner._state.last_output_at

        class _Parser:
            def rewrite_upstream_noise(self, line):
                return line

            def parse(self, line):
                return lb.ProgressEvent(kind="log", raw_line=line)

        class _Stdout:
            def __init__(self):
                self._lines = iter(["one line\n", ""])

            def readline(self):
                return next(self._lines)

            def close(self):
                pass

        runner._state.proc = type("P", (), {"stdout": _Stdout()})()
        with patch("src.launcher_bridge.time.monotonic", return_value=before + 500.0):
            runner._reader_loop(_Parser())

        assert runner._state.last_output_at == before + 500.0


class TestCancelStillWins:
    def test_cancel_escalates_immediately(self, runner):
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 1.0
            runner._state.last_output_at = clock["t"]
            return clock["t"]

        runner._state.cancel_requested.set()
        with patch("src.launcher_bridge.time.monotonic", fake_monotonic), \
                patch.object(runner, "_escalate_shutdown", return_value=0) as esc:
            runner._wait_for_runner_exit(runner._state.proc)

        assert esc.call_count == 1
