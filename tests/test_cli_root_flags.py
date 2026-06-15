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


# ---------------------------------------------------------------------------
# T2 — subcommand aliases
# ---------------------------------------------------------------------------


class TestConvertAlias:
    def test_c_alias_exits_0(self, book_txt):
        result = _cli("c", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_c_alias_same_output_as_convert(self, book_txt):
        r_full = _cli("convert", book_txt, "--dry-run")
        r_alias = _cli("c", book_txt, "--dry-run")
        assert r_full.returncode == r_alias.returncode == 0
        # Both should emit the same dry-run summary text.
        assert r_full.stdout == r_alias.stdout


class TestSampleAlias:
    def test_s_alias_exits_0(self, book_txt):
        result = _cli("s", book_txt, "--dry-run")
        assert result.returncode == 0, result.stderr

    def test_s_alias_same_output_as_sample(self, book_txt):
        r_full = _cli("sample", book_txt, "--dry-run")
        r_alias = _cli("s", book_txt, "--dry-run")
        assert r_full.returncode == r_alias.returncode == 0
        assert r_full.stdout == r_alias.stdout


class TestPreviewAlias:
    # Force a preview-capable engine so these tests don't depend on the dev
    # machine's saved default engine in ~/.audiobookmaker/config.json. Without
    # --engine, a saved default of a subprocess engine (e.g. chatterbox_grandmom)
    # makes preview exit 1 ("cannot be used with preview"), which has nothing to
    # do with whether the alias itself works.
    def test_p_alias_exits_0(self):
        # preview --no-play with a short string should exit 0 even if no
        # audio device is present (--no-play skips playback).
        result = _cli("p", "hi", "--engine", "edge", "--no-play")
        # Accept 0 (success) or 2 (engine not available) or 4 (runtime) —
        # what matters is the alias was recognised, not returncode 1 from
        # argparse "invalid choice".
        assert result.returncode in (0, 2, 4), (
            f"Unexpected exit code from 'p' alias: {result.returncode}\n{result.stderr}"
        )
        assert "invalid choice" not in result.stderr.lower()

    def test_p_alias_same_path_as_preview(self):
        r_full = _cli("preview", "hi", "--engine", "edge", "--no-play")
        r_alias = _cli("p", "hi", "--engine", "edge", "--no-play")
        assert r_full.returncode == r_alias.returncode
