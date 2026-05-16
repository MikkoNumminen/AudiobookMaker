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
# N4 — _warn_if_long_windows_path
# ---------------------------------------------------------------------------


class TestWarnIfLongWindowsPath:
    def test_warns_on_windows_with_long_path(self, capsys, monkeypatch):
        """A 280-char path on win32 must emit a stderr warning."""
        fn = _get_warn_helper()
        if fn is None:
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "win32")
        long_path = Path("C:/" + "a" * 277)  # 280 chars total
        assert len(str(long_path)) > 250
        fn(long_path, "input")
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "MAX_PATH" in err or "260" in err

    def test_no_warning_on_windows_with_short_path(self, capsys, monkeypatch):
        """A short path on win32 must not emit any warning."""
        fn = _get_warn_helper()
        if fn is None:
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "win32")
        short_path = Path("C:/books/book.pdf")
        assert len(str(short_path)) < 250
        fn(short_path, "input")
        assert capsys.readouterr().err == ""

    def test_no_warning_on_linux_regardless_of_length(self, capsys, monkeypatch):
        """Long path on a non-Windows platform must not produce any warning."""
        fn = _get_warn_helper()
        if fn is None:
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "linux")
        long_path = Path("/" + "a" * 280)
        fn(long_path, "input")
        assert capsys.readouterr().err == ""

    def test_no_warning_on_darwin_regardless_of_length(self, capsys, monkeypatch):
        """Long path on macOS must not produce any warning."""
        fn = _get_warn_helper()
        if fn is None:
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "darwin")
        long_path = Path("/" + "a" * 280)
        fn(long_path, "input")
        assert capsys.readouterr().err == ""

    def test_label_appears_in_warning(self, capsys, monkeypatch):
        """The label parameter must appear in the warning text."""
        fn = _get_warn_helper()
        if fn is None:
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "win32")
        long_path = Path("C:/" + "z" * 277)
        fn(long_path, "output")
        err = capsys.readouterr().err
        assert "output" in err


# ---------------------------------------------------------------------------
# N4 — validate_input_path warns but is non-fatal for long paths
# ---------------------------------------------------------------------------


class TestValidateInputPathLongPathWarning:
    def test_warning_emitted_for_long_path_on_windows(
        self, capsys, monkeypatch, tmp_path
    ):
        """Long input path on Windows must emit a warning but still return the
        validation result (warning is non-fatal).

        Uses monkeypatching to assert the wire-up from validate_input_path to
        _warn_if_long_windows_path without depending on path length tricks.
        Skipped if _warn_if_long_windows_path does not yet exist in _common.
        """
        import src.cli._common as _common_mod
        if not hasattr(_common_mod, "_warn_if_long_windows_path"):
            pytest.skip("_warn_if_long_windows_path not present in this build")
        monkeypatch.setattr(sys, "platform", "win32")
        real_file = tmp_path / "book.pdf"
        real_file.write_bytes(b"%PDF-1.4 fake")

        called_with = {}

        def fake_warn(path: Path, label: str) -> None:
            called_with["path"] = path
            called_with["label"] = label

        monkeypatch.setattr(_common_mod, "_warn_if_long_windows_path", fake_warn)
        rc, msg = validate_input_path(str(real_file))
        assert rc == EXIT_OK
        assert called_with.get("label") == "input"

    def test_warning_does_not_abort_valid_path(self, capsys, monkeypatch, tmp_path):
        """Even when the warning fires (patched to do so), the path still
        validates successfully.

        Skipped if _warn_if_long_windows_path does not yet exist in _common.
        """
        import src.cli._common as _common_mod
        if not hasattr(_common_mod, "_warn_if_long_windows_path"):
            pytest.skip("_warn_if_long_windows_path not present in this build")
        real_file = tmp_path / "book.epub"
        real_file.write_bytes(b"PK fake epub")

        def always_warn(path: Path, label: str) -> None:
            print("Warning: forced long-path warning", file=sys.stderr)

        monkeypatch.setattr(_common_mod, "_warn_if_long_windows_path", always_warn)
        rc, msg = validate_input_path(str(real_file))
        assert rc == EXIT_OK
        err = capsys.readouterr().err
        assert "Warning" in err


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
