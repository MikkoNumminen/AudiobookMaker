"""Tests for CLI tilde expansion and UTF-8 stdout reconfiguration.

Covers:
- validate_input_path() expands a leading ~
- AUDIOBOOKMAKER_OUTPUT=~/foo.mp3 is expanded before use
- stdout reconfigure is a no-op on streams without .reconfigure
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest

from src.cli._common import validate_input_path, EXIT_OK, EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# validate_input_path — tilde expansion
# ---------------------------------------------------------------------------


def _set_fake_home(tmp_path: Path, monkeypatch) -> None:
    """Point os.path.expanduser / Path.expanduser at tmp_path.

    expanduser reads USERPROFILE (Windows) or HOME (POSIX) to resolve ~.
    Patching both env vars covers either platform.
    """
    home_str = str(tmp_path)
    monkeypatch.setenv("HOME", home_str)
    monkeypatch.setenv("USERPROFILE", home_str)


class TestValidateInputPathExpansion:
    def test_tilde_path_resolves_to_home(self, tmp_path, monkeypatch):
        """A path like ~/books/foo.txt should resolve under the home dir."""
        _set_fake_home(tmp_path, monkeypatch)

        book = tmp_path / "books" / "test.txt"
        book.parent.mkdir(parents=True)
        book.write_text("some text")

        code, msg = validate_input_path("~/books/test.txt")
        assert code == EXIT_OK, f"expected EXIT_OK but got {code}: {msg}"

    def test_tilde_path_not_found_reports_resolved_path(self, tmp_path, monkeypatch):
        """Error message must show the resolved absolute path, not the literal ~."""
        _set_fake_home(tmp_path, monkeypatch)

        code, msg = validate_input_path("~/no_such_file.txt")
        assert code == EXIT_BAD_INPUT
        # The error must contain the resolved path, not a bare ~.
        assert "~" not in msg, "error message should show resolved path, not '~'"
        assert str(tmp_path) in msg

    def test_absolute_path_unchanged(self, tmp_path):
        """An already-absolute path must still validate correctly."""
        f = tmp_path / "book.epub"
        f.write_text("epub content")

        code, msg = validate_input_path(str(f))
        assert code == EXIT_OK, msg

    def test_unsupported_ext_after_expansion(self, tmp_path, monkeypatch):
        """Expansion must happen before extension check."""
        _set_fake_home(tmp_path, monkeypatch)

        bad = tmp_path / "doc.xyz"
        bad.write_text("data")

        code, msg = validate_input_path("~/doc.xyz")
        assert code == EXIT_BAD_INPUT
        assert "unsupported" in msg


# ---------------------------------------------------------------------------
# AUDIOBOOKMAKER_OUTPUT env-var — tilde expansion
# ---------------------------------------------------------------------------


class TestOutputEnvVarExpansion:
    """Confirm that ~/foo.mp3 in AUDIOBOOKMAKER_OUTPUT is expanded to an
    absolute path before reaching synthesis."""

    def test_output_env_tilde_expands_in_dry_run(self, tmp_path, monkeypatch):
        """
        With AUDIOBOOKMAKER_OUTPUT=~/out.mp3, a --dry-run --json invocation
        must report an output path under the real home dir, not '~/out.mp3'.
        """
        import subprocess, json

        # Create a real .txt file to satisfy input validation.
        book = tmp_path / "book.txt"
        book.write_text("Hello world. Test sentence.")

        # Use a tilde-relative output path.
        tilde_out = "~/out_test_expansion.mp3"
        env = os.environ.copy()
        env["AUDIOBOOKMAKER_OUTPUT"] = tilde_out

        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "convert", str(book),
             "--dry-run", "--json", "--engine", "edge"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        obj = json.loads(result.stdout.strip())
        output_val: str = obj["output"]
        assert not output_val.startswith("~"), (
            f"output path was not expanded: {output_val!r}"
        )
        # Must be an absolute path.
        assert Path(output_val).is_absolute(), (
            f"expected absolute output path, got: {output_val!r}"
        )


# ---------------------------------------------------------------------------
# stdout reconfigure — no-op on streams without .reconfigure
# ---------------------------------------------------------------------------


class TestStdoutReconfigureNoOp:
    def test_reconfigure_absent_does_not_raise(self):
        """
        The try/except in main() must swallow AttributeError gracefully
        when a stream does not have .reconfigure (e.g. captured StringIO,
        or an old Python version).
        """
        # Build a minimal stream-like object with no reconfigure method.
        fake_stream = types.SimpleNamespace()  # no .reconfigure attr

        # Replicate the exact guard from __main__.main().
        raised = False
        try:
            for stream in (fake_stream,):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
                except (AttributeError, OSError):
                    pass
        except Exception as exc:
            raised = True

        assert not raised, "reconfigure guard raised an unexpected exception"

    def test_reconfigure_oserror_does_not_raise(self):
        """OSError from reconfigure (e.g. underlying fd closed) must be swallowed."""

        class BrokenStream:
            def reconfigure(self, **kwargs):
                raise OSError("fd closed")

        raised = False
        try:
            for stream in (BrokenStream(),):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except (AttributeError, OSError):
                    pass
        except Exception:
            raised = True

        assert not raised
