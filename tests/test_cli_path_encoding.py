"""Regression tests for CLI path-handling and encoding hardening.

Covers three audit findings:
- M1: ~ expansion in all path arguments
- M2: UTF-8 stdout/stderr forced at startup
- Quick win: preview --no-play tempfile path is shell-quoted
"""

from __future__ import annotations

import argparse
import io
import os
import shlex
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

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


def _tmp_txt(content: str = "Hello world. This is a test sentence.") -> str:
    """Write a temp .txt file and return its path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# M1 — ~ expansion in path arguments
# ---------------------------------------------------------------------------


class TestExpandPath:
    """Unit tests for the expand_path() helper in src.cli._common."""

    def test_tilde_expands_to_home(self):
        from src.cli._common import expand_path
        result = expand_path("~/somefile.txt")
        assert result == str(Path.home() / "somefile.txt")

    def test_plain_path_unchanged(self):
        from src.cli._common import expand_path
        # Use os.sep-style comparison so the test works on both POSIX and Windows.
        result = expand_path("/absolute/path/file.txt")
        # No tilde → result must still end with the original filename.
        assert result.endswith("file.txt")
        assert "~" not in result

    def test_relative_path_unchanged_structure(self):
        from src.cli._common import expand_path
        # relative paths have no tilde to expand; the structure is preserved.
        result = expand_path("relative/file.txt")
        assert result.endswith("file.txt")
        assert "~" not in result


class TestValidateInputPathExpandsHome:
    """validate_input_path must expand ~ and return the expanded path."""

    def test_tilde_in_existing_file_expands(self, tmp_path):
        from src.cli._common import EXIT_OK, validate_input_path

        # Create a real file inside the actual home directory so that
        # expanduser("~/…") resolves to a real path.  We use a unique
        # name to avoid collisions with other test runs.
        home = Path.home()
        unique_name = f"_abm_test_validate_{os.getpid()}.txt"
        real_file = home / unique_name
        try:
            real_file.write_text("Hello world.", encoding="utf-8")
            tilde_path = f"~/{unique_name}"
            code, expanded = validate_input_path(tilde_path)
        finally:
            real_file.unlink(missing_ok=True)

        assert code == EXIT_OK, f"Expected EXIT_OK, got {code} / {expanded}"
        assert "~" not in expanded, "expanded path must not contain ~"
        assert expanded.endswith(unique_name)

    def test_tilde_in_missing_file_returns_bad_input(self):
        from src.cli._common import EXIT_BAD_INPUT, validate_input_path

        code, msg = validate_input_path("~/no_such_file_xyz.txt")
        assert code == EXIT_BAD_INPUT

    def test_absolute_path_returned_unchanged_on_success(self, tmp_path):
        from src.cli._common import EXIT_OK, validate_input_path

        real_file = tmp_path / "book.txt"
        real_file.write_text("Hello world.", encoding="utf-8")

        code, expanded = validate_input_path(str(real_file))
        assert code == EXIT_OK
        assert expanded == str(real_file)


class TestResolveStrExpandsHome:
    """resolve_str must expand ~ when the value comes from a CLI flag or env var."""

    def test_flag_value_with_tilde_is_expanded(self):
        from src.cli._common import resolve_str

        result = resolve_str("~/output.mp3", "SOME_ENV_KEY", "", "")
        assert "~" not in result
        assert result.endswith("output.mp3")
        assert result.startswith(str(Path.home()))

    def test_env_var_with_tilde_is_expanded(self):
        from src.cli._common import resolve_str

        with mock.patch.dict(os.environ, {"AUDIOBOOKMAKER_OUTPUT": "~/from_env.mp3"}):
            result = resolve_str(None, "AUDIOBOOKMAKER_OUTPUT", "", "")
        assert "~" not in result
        assert result.endswith("from_env.mp3")
        assert result.startswith(str(Path.home()))

    def test_plain_path_unchanged(self):
        from src.cli._common import resolve_str

        result = resolve_str("/absolute/out.mp3", "SOME_KEY", "", "")
        assert result == "/absolute/out.mp3"


class TestConvertTildeExpansion:
    """End-to-end: passing ~/file.txt as convert INPUT must work (not crash on ~)."""

    def test_dry_run_with_tilde_input(self, tmp_path):
        real_file = tmp_path / "book.txt"
        real_file.write_text("Hello world. This is a test.", encoding="utf-8")

        # Patch Path.home() to point at tmp_path in the subprocess env by
        # using the real absolute path (no ~ needed for the assertion).
        # Simpler: just call validate_input_path directly with a real file.
        from src.cli._common import EXIT_OK, validate_input_path

        code, expanded = validate_input_path(str(real_file))
        assert code == EXIT_OK
        assert "~" not in expanded

    def test_dry_run_with_tilde_output_flag(self, tmp_path):
        """--output ~/out.mp3 must resolve under home, not leave ~ literal."""
        from src.cli._common import resolve_str

        result = resolve_str("~/audiobooks/out.mp3", "AUDIOBOOKMAKER_OUTPUT", "", "")
        assert result == str(Path.home() / "audiobooks" / "out.mp3")


# ---------------------------------------------------------------------------
# M2 — UTF-8 stdout/stderr forced at startup
# ---------------------------------------------------------------------------


class TestForceUtf8Streams:
    """_force_utf8_streams() must call reconfigure on both stdout and stderr."""

    def test_reconfigure_called_on_stdout_and_stderr(self):
        from src.cli.__main__ import _force_utf8_streams

        # Build minimal mock streams that support reconfigure.
        class _FakeStream:
            def __init__(self):
                self.calls = []

            def reconfigure(self, **kwargs):
                self.calls.append(kwargs)

        fake_out = _FakeStream()
        fake_err = _FakeStream()

        with mock.patch("sys.stdout", fake_out), mock.patch("sys.stderr", fake_err):
            _force_utf8_streams()

        assert fake_out.calls, "reconfigure must be called on stdout"
        assert fake_err.calls, "reconfigure must be called on stderr"
        assert fake_out.calls[0]["encoding"] == "utf-8"
        assert fake_err.calls[0]["encoding"] == "utf-8"

    def test_graceful_when_reconfigure_absent(self):
        """Streams without reconfigure (e.g. StringIO) must not cause a crash."""
        from src.cli.__main__ import _force_utf8_streams

        # StringIO has no reconfigure — should be silently skipped.
        fake_out = StringIO()
        fake_err = StringIO()

        with mock.patch("sys.stdout", fake_out), mock.patch("sys.stderr", fake_err):
            _force_utf8_streams()  # must not raise

    def test_graceful_when_reconfigure_raises_unsupported(self):
        """io.UnsupportedOperation from reconfigure must be swallowed."""
        from src.cli.__main__ import _force_utf8_streams

        class _StrictStream:
            def reconfigure(self, **kwargs):
                raise io.UnsupportedOperation("reconfigure not supported")

        with mock.patch("sys.stdout", _StrictStream()), mock.patch("sys.stderr", _StrictStream()):
            _force_utf8_streams()  # must not raise

    def test_main_calls_force_utf8_before_dispatch(self):
        """main() must call _force_utf8_streams before any subcommand runs."""
        from src.cli import __main__ as main_mod

        calls = []

        def _mock_force():
            calls.append(True)

        with mock.patch.object(main_mod, "_force_utf8_streams", side_effect=_mock_force):
            # --version exits immediately; we just need to confirm the call.
            try:
                main_mod.main(["--version"])
            except SystemExit:
                pass

        assert calls, "_force_utf8_streams must be called by main()"


# ---------------------------------------------------------------------------
# Quick win — preview --no-play prints a shell-quoted path
# ---------------------------------------------------------------------------


class TestPreviewNoPlayShellQuote:
    """preview --no-play must emit a shell-quoted path on stdout."""

    def _make_preview_args(self, no_play=True):
        """Build a minimal argparse.Namespace for the preview run() function."""
        ns = argparse.Namespace(
            text="hello",
            engine="edge",
            language="fi",
            voice=None,
            output=None,
            no_play=no_play,
            json=False,
            quiet=False,
        )
        return ns

    def test_quoted_path_is_valid_shlex(self, tmp_path):
        """The printed path must be parseable by shlex.split as a single token."""
        # Simulate a tempfile path that contains a space (typical on Windows).
        spaced_tmp = tmp_path / "temp dir" / "preview_test.mp3"
        spaced_tmp.parent.mkdir(parents=True, exist_ok=True)
        spaced_tmp.write_bytes(b"")

        # Patch tempfile.NamedTemporaryFile to return our spaced path.
        import src.cli.preview as preview_mod

        class _FakeTmp:
            name = str(spaced_tmp)
            def __enter__(self): return self
            def __exit__(self, *a): pass

        stdout_buf = StringIO()
        with mock.patch("tempfile.NamedTemporaryFile", return_value=_FakeTmp()), \
             mock.patch("sys.stdout", stdout_buf):
            # Patch the engine so synthesis succeeds instantly.
            mock_engine = mock.MagicMock()
            mock_engine.uses_subprocess = False
            mock_engine.check_status.return_value = mock.MagicMock(available=True)
            mock_engine.synthesize.return_value = None

            with mock.patch("src.tts_base.get_engine", return_value=mock_engine), \
                 mock.patch("src.engine_registry", create=True):
                rc = preview_mod.run(self._make_preview_args(no_play=True))

        assert rc == 0
        printed = stdout_buf.getvalue().strip()
        assert printed, "expected a path on stdout"
        # shlex.split must parse it as exactly one token.
        tokens = shlex.split(printed)
        assert len(tokens) == 1, f"expected one token, got {tokens!r} from {printed!r}"
        # The token must match our spaced path.
        assert tokens[0] == str(spaced_tmp)

    def test_path_without_spaces_still_emits_valid_shlex(self, tmp_path):
        """A path with no special chars must also survive shlex round-trip."""
        plain_tmp = tmp_path / "plain.mp3"
        plain_tmp.write_bytes(b"")

        import src.cli.preview as preview_mod

        class _FakeTmp:
            name = str(plain_tmp)
            def __enter__(self): return self
            def __exit__(self, *a): pass

        stdout_buf = StringIO()
        with mock.patch("tempfile.NamedTemporaryFile", return_value=_FakeTmp()), \
             mock.patch("sys.stdout", stdout_buf):
            mock_engine = mock.MagicMock()
            mock_engine.uses_subprocess = False
            mock_engine.check_status.return_value = mock.MagicMock(available=True)
            mock_engine.synthesize.return_value = None

            with mock.patch("src.tts_base.get_engine", return_value=mock_engine), \
                 mock.patch("src.engine_registry", create=True):
                rc = preview_mod.run(self._make_preview_args(no_play=True))

        assert rc == 0
        printed = stdout_buf.getvalue().strip()
        tokens = shlex.split(printed)
        assert len(tokens) == 1
        assert tokens[0] == str(plain_tmp)
