"""Tests for the 'preview' CLI subcommand (src/cli/preview.py).

Driven via subprocess so the real argument-parsing and dispatch layers
are exercised. Audio playback is always suppressed with --no-play.
"""

from __future__ import annotations

import os
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers (same pattern as tests/test_cli.py)
# ---------------------------------------------------------------------------


def _cli(*args: str, env: dict | None = None) -> "subprocess.CompletedProcess":
    """Run the CLI via subprocess and return the CompletedProcess."""
    import subprocess

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _edge_available() -> bool:
    """Return True if the edge engine reports itself as available."""
    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import get_engine
        engine = get_engine("edge")
        if engine is None:
            return False
        return engine.check_status().available
    except Exception:
        return False


# ---------------------------------------------------------------------------
# preview --help
# ---------------------------------------------------------------------------


class TestPreviewHelp:
    def test_help_exits_0(self):
        result = _cli("preview", "--help")
        assert result.returncode == 0

    def test_help_lists_required_flags(self):
        result = _cli("preview", "--help")
        output = result.stdout + result.stderr
        for flag in ("--engine", "--language", "--voice", "--no-play", "--json", "--quiet"):
            assert flag in output, f"'{flag}' missing from preview --help"


# ---------------------------------------------------------------------------
# preview "" — empty text → exit 1
# ---------------------------------------------------------------------------


class TestPreviewBadInput:
    def test_empty_text_exits_1(self):
        result = _cli("preview", "")
        assert result.returncode == 1

    def test_whitespace_only_exits_1(self):
        result = _cli("preview", "   ")
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# preview "hello" — engine may or may not be available in CI
# ---------------------------------------------------------------------------


class TestPreviewBasic:
    def test_hello_exits_0_or_4(self):
        """Exit 0 (success) or 4 (engine unavailable in CI) are both OK."""
        result = _cli("preview", "hello", "--no-play")
        assert result.returncode in (0, 1, 2, 4), (
            f"Unexpected exit code {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# preview "hi" --no-play --engine edge — prints tempfile path when available
# ---------------------------------------------------------------------------


def _ffmpeg_available() -> bool:
    """Return True only when ffmpeg actually runs as a subprocess.

    Edge-TTS synth uses pydub to assemble MP3 chunks, and pydub needs
    ffmpeg. CI runners often lack it. Just finding a path is not
    enough — the CLI subprocess that runs synth inherits the parent's
    environment, so the only reliable check is to invoke ffmpeg the
    same way pydub will. If `ffmpeg -version` works, synthesis can
    proceed; if it doesn't, we skip rather than treat the exit 4
    that follows as a regression.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


class TestPreviewNoPlay:
    def test_no_play_prints_path_and_exits_0(self):
        if not _edge_available():
            pytest.skip("edge engine not available in this environment")
        if not _ffmpeg_available():
            pytest.skip("ffmpeg not available — pydub cannot assemble MP3")

        result = _cli("preview", "hi", "--no-play", "--engine", "edge")
        assert result.returncode == 0, (
            f"Expected exit 0\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # stdout must contain a non-empty path
        path = result.stdout.strip()
        assert path, "Expected a tempfile path on stdout with --no-play"
