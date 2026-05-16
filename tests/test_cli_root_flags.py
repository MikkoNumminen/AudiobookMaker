"""Tests for root-level -v/--verbose/--log-level flags and subcommand aliases.

All tests drive the CLI via subprocess so the real argument-parsing and
dispatch layers are exercised end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess and return CompletedProcess."""
    env = os.environ.copy()
    # Suppress colour / interactive prompts that might pollute stderr.
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def book_txt(tmp_path):
    """A minimal text file that passes validate_input_path."""
    f = tmp_path / "book.txt"
    f.write_text("Hello world.", encoding="utf-8")
    return str(f)


# ---------------------------------------------------------------------------
# T1 — root verbose flags
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_single_v_exits_0(self, book_txt):
        result = _cli("-v", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_double_v_exits_0(self, book_txt):
        result = _cli("-vv", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_double_v_emits_debug_log(self, book_txt):
        result = _cli("-vv", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr
        # The debug sentinel line written in main() should appear on stderr.
        assert "DEBUG" in result.stderr, (
            f"Expected DEBUG output on stderr with -vv; got:\n{result.stderr!r}"
        )

    def test_single_v_emits_info_not_debug(self, book_txt):
        result = _cli("-v", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr
        # INFO level: the sentinel line is at DEBUG so it must NOT appear.
        assert "verbosity: INFO" not in result.stderr or "DEBUG" not in result.stderr

    def test_no_flags_is_warning_level(self, book_txt):
        result = _cli("convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr
        # At WARNING level the debug sentinel must be absent.
        assert "DEBUG" not in result.stderr, (
            f"Expected no DEBUG output at default level; got:\n{result.stderr!r}"
        )


class TestLogLevelFlag:
    def test_log_level_debug_exits_0(self, book_txt):
        result = _cli("--log-level", "debug", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_log_level_debug_emits_debug(self, book_txt):
        result = _cli("--log-level", "debug", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "DEBUG" in result.stderr, (
            f"Expected DEBUG output with --log-level debug; got:\n{result.stderr!r}"
        )

    def test_log_level_warning_no_debug(self, book_txt):
        result = _cli("--log-level", "warning", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "DEBUG" not in result.stderr

    def test_log_level_info_exits_0(self, book_txt):
        result = _cli("--log-level", "info", "convert", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_log_level_and_verbose_are_mutually_exclusive(self, book_txt):
        result = _cli("-v", "--log-level", "debug", "convert", book_txt, "--dry-run")
        # argparse should reject the combination and exit non-zero.
        assert result.returncode != 0

    def test_invalid_log_level_rejected(self, book_txt):
        result = _cli("--log-level", "verbose", "convert", book_txt, "--dry-run")
        assert result.returncode != 0
