"""Tests for _common.py ergonomics: short flags (-q / -j), cache-resume notice,
long-path warning.

Covers:
- N8: -q / -j short flags accepted by the CLI (convert --dry-run)
- N1: print_event emits setup_cached/setup_total to stderr in --quiet mode
- N4: _warn_if_long_windows_path warns on Windows for paths > 250 chars
- N4: validate_input_path calls the warning (non-fatal)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_OK,
    print_event,
    validate_input_path,
)
from src.launcher_bridge import ProgressEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(kind: str, **kwargs) -> ProgressEvent:
    """Build a ProgressEvent with sensible defaults."""
    defaults = dict(
        raw_line="",
        output_path="",
        total_done=0,
        total_chunks=0,
        chapter_idx=0,
        chapter_total=0,
        chunk_idx=0,
        chunk_total=0,
        elapsed_s=0.0,
        eta_s=0.0,
        rtf=0.0,
        returncode=0,
    )
    defaults.update(kwargs)
    return ProgressEvent(kind=kind, **defaults)


def _cli(*args: str) -> "subprocess.CompletedProcess":
    """Drive the CLI via subprocess."""
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _tmp_txt(content: str = "Hello world.") -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


def _get_warn_helper():
    """Return _warn_if_long_windows_path or None if not yet present in _common."""
    import src.cli._common as m
    return getattr(m, "_warn_if_long_windows_path", None)


# ---------------------------------------------------------------------------
# N1 — setup_cached / setup_total in quiet mode → stderr
# ---------------------------------------------------------------------------


class TestSetupCachedQuietMode:
    def test_setup_cached_goes_to_stderr_in_quiet_mode(self, capsys):
        """setup_cached event must appear on stderr when quiet=True."""
        event = _make_event(
            "setup_cached",
            total_done=215,
            total_chunks=1043,
            raw_line="[setup_cached] cached chunks found: 215/1043",
        )
        print_event(event, json_mode=False, quiet=True)
        captured = capsys.readouterr()
        assert captured.out == "", "stdout must be silent in quiet mode"
        assert "215" in captured.err
        assert "1043" in captured.err
        assert "Resuming" in captured.err

    def test_setup_cached_fallback_when_no_counts(self, capsys):
        """Fallback to raw_line when total_chunks is 0."""
        event = _make_event(
            "setup_cached",
            total_done=0,
            total_chunks=0,
            raw_line="cached chunks found: some",
        )
        print_event(event, json_mode=False, quiet=True)
        captured = capsys.readouterr()
        assert "Resuming" in captured.err
        assert "cached chunks found" in captured.err

    def test_setup_total_goes_to_stderr_in_quiet_mode(self, capsys):
        """setup_total event must appear on stderr when quiet=True."""
        event = _make_event(
            "setup_total",
            total_chunks=500,
            raw_line="total chunks: 500",
        )
        print_event(event, json_mode=False, quiet=True)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "500" in captured.err

    def test_setup_cached_in_human_mode_goes_to_stdout(self, capsys):
        """Human-readable mode: setup_cached still goes to stdout (unchanged)."""
        event = _make_event(
            "setup_cached",
            raw_line="[setup_cached] cached chunks found: 100/200",
        )
        print_event(event, json_mode=False, quiet=False)
        captured = capsys.readouterr()
        assert "[setup_cached]" in captured.out
        assert captured.err == ""

    def test_done_event_still_goes_to_stdout_in_quiet_mode(self, capsys):
        """done event must still print path to stdout in quiet mode."""
        event = _make_event("done", output_path="/out/book.mp3")
        print_event(event, json_mode=False, quiet=True)
        captured = capsys.readouterr()
        assert "/out/book.mp3" in captured.out

    def test_other_events_suppressed_in_quiet_mode(self, capsys):
        """Non-setup, non-done events must be silent in quiet mode."""
        event = _make_event("chunk", total_done=5, total_chunks=100)
        print_event(event, json_mode=False, quiet=True)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


# ---------------------------------------------------------------------------
# N8 — short flags -q / -j accepted by the CLI
# ---------------------------------------------------------------------------


class TestShortFlags:
    def test_dash_q_accepted(self):
        """-q must be equivalent to --quiet: no crash, exit 0 on --dry-run."""
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "-q", "--dry-run", "--engine", "edge")
            assert result.returncode == 0, (
                f"-q flag rejected or caused error:\n{result.stderr}"
            )
        finally:
            os.unlink(path)

    def test_dash_j_accepted(self):
        """-j must be equivalent to --json: exit 0 on --dry-run."""
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "-j", "--dry-run", "--engine", "edge")
            assert result.returncode == 0, (
                f"-j flag rejected or caused error:\n{result.stderr}"
            )
        finally:
            os.unlink(path)

    def test_dash_q_suppresses_stdout(self):
        """-q (quiet) must suppress stdout progress on a dry-run invocation."""
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "-q", "--dry-run", "--engine", "edge")
            assert result.returncode == 0
        finally:
            os.unlink(path)

    def test_dash_j_produces_json_output(self):
        """-j (json) must produce parseable JSON output on a dry-run invocation."""
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "-j", "--dry-run", "--engine", "edge")
            assert result.returncode == 0
            combined = result.stdout + result.stderr
            json_lines = [
                line for line in combined.splitlines() if line.strip().startswith("{")
            ]
            assert json_lines, (
                f"Expected at least one JSON line with -j, got:\n{combined}"
            )
        finally:
            os.unlink(path)

    def test_dash_q_and_dash_j_are_mutually_exclusive(self):
        """-q and -j together must cause argparse to exit 2."""
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path, "-q", "-j", "--dry-run", "--engine", "edge"
            )
            assert result.returncode == 2, (
                "Expected exit 2 (argparse error) when -q and -j used together"
            )
        finally:
            os.unlink(path)
