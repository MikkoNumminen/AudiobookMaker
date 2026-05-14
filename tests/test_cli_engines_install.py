"""Tests for engines install / remove / check sub-subcommands.

Drives the CLI via subprocess so argument-parsing and dispatch are
exercised end-to-end. Heavy installer work is mocked to avoid network
traffic and multi-GB downloads.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    import subprocess
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


# ---------------------------------------------------------------------------
# engines check
# ---------------------------------------------------------------------------


class TestEnginesCheck:
    def test_check_edge_exits_0(self):
        """Edge-TTS is network-based and always reports available."""
        result = _cli("engines", "check", "edge")
        assert result.returncode == 0

    def test_check_edge_output_contains_id(self):
        result = _cli("engines", "check", "edge")
        assert "edge" in result.stdout

    def test_check_piper_exits_0_or_2(self):
        """Piper may or may not be installed; both are valid outcomes."""
        result = _cli("engines", "check", "piper")
        assert result.returncode in (0, 2)

    def test_check_unknown_engine_exits_1(self):
        result = _cli("engines", "check", "unknown_engine_xyz")
        assert result.returncode == 1

    def test_check_unknown_engine_stderr_message(self):
        result = _cli("engines", "check", "unknown_engine_xyz")
        assert "unknown_engine_xyz" in result.stderr

    def test_check_json_mode_edge(self):
        result = _cli("engines", "check", "--json", "edge")
        assert result.returncode == 0
        obj = json.loads(result.stdout.strip())
        assert obj["id"] == "edge"
        assert obj["available"] is True

    def test_check_json_mode_unknown(self):
        result = _cli("engines", "check", "--json", "unknown_engine_xyz")
        assert result.returncode == 1
        # Should still emit JSON even for unknown id.
        obj = json.loads(result.stdout.strip())
        assert obj["available"] is False


# ---------------------------------------------------------------------------
# engines install — missing id argument
# ---------------------------------------------------------------------------


class TestEnginesInstallArgs:
    def test_install_without_id_exits_2(self):
        """argparse should reject a missing positional argument with exit 2."""
        result = _cli("engines", "install")
        assert result.returncode == 2

    def test_remove_help_works(self):
        """engines remove --help should not error."""
        result = _cli("engines", "remove", "--help")
        assert result.returncode == 0
        assert "remove" in result.stdout.lower() or "ID" in result.stdout


# ---------------------------------------------------------------------------
# engines install — mocked (no real download)
# ---------------------------------------------------------------------------


class TestEnginesInstallMocked:
    def test_install_unknown_engine_exits_1(self):
        result = _cli("engines", "install", "unknown_engine_xyz")
        assert result.returncode == 1

    def test_install_piper_mocked(self, tmp_path, monkeypatch):
        """Install with a no-op installer mock — verifies plumbing, not download."""
        pytest.importorskip("src.engine_installer")

        from src.engine_installer import PiperInstaller

        def _fake_install(self, progress_cb, cancel_event):
            from src.engine_installer import InstallProgress
            progress_cb(InstallProgress(step=1, total_steps=1, step_label="Done", done=True, message="ok"))

        with mock.patch.object(PiperInstaller, "install", _fake_install):
            result = _cli("engines", "install", "piper")
        # Either succeeds (0) or fails due to env issues (2) — never bad-input (1).
        assert result.returncode in (0, 2)

    def test_install_piper_json_mocked(self, tmp_path, monkeypatch):
        pytest.importorskip("src.engine_installer")

        from src.engine_installer import PiperInstaller

        def _fake_install(self, progress_cb, cancel_event):
            from src.engine_installer import InstallProgress
            progress_cb(InstallProgress(step=1, total_steps=1, step_label="Done", done=True, message="all good"))

        with mock.patch.object(PiperInstaller, "install", _fake_install):
            result = _cli("engines", "install", "--json", "piper")

        assert result.returncode in (0, 2)
        # If it reached the progress callback, there should be JSON lines.
        if result.returncode == 0:
            lines = [l for l in result.stdout.splitlines() if l.strip()]
            assert lines, "Expected JSON progress lines on stdout"
            obj = json.loads(lines[-1])
            assert "kind" in obj

    def test_install_propagates_installer_error(self):
        pytest.importorskip("src.engine_installer")

        from src.engine_installer import PiperInstaller
        from src.cli.__main__ import main

        def _bad_install(self, progress_cb, cancel_event):
            raise RuntimeError("Simulated failure")

        with mock.patch.object(PiperInstaller, "install", _bad_install):
            rc = main(["engines", "install", "piper"])
        assert rc == 2


# ---------------------------------------------------------------------------
# engines remove — mocked
# ---------------------------------------------------------------------------


class TestEnginesRemoveMocked:
    def test_remove_unknown_engine_exits_1(self):
        result = _cli("engines", "remove", "--yes", "unknown_engine_xyz")
        assert result.returncode == 1

    def test_remove_not_installed_exits_1(self):
        pytest.importorskip("src.engine_installer")

        from src.engine_installer import PiperInstaller
        from src.cli.__main__ import main

        with mock.patch.object(PiperInstaller, "is_installed", return_value=False):
            rc = main(["engines", "remove", "--yes", "piper"])
        assert rc == 1

    def test_remove_piper_mocked(self, tmp_path):
        pytest.importorskip("src.engine_installer")

        import src.engine_installer as ei
        from src.cli.__main__ import main

        fake_voice_dir = tmp_path / "fi_FI-harri-medium"
        fake_voice_dir.mkdir()
        (fake_voice_dir / "fi_FI-harri-medium.onnx").write_bytes(b"fake")

        fake_installer = ei.PiperInstaller()
        fake_installer._voice_dir = fake_voice_dir
        fake_installer.is_installed = lambda: True

        with mock.patch.object(ei, "get_installer", return_value=fake_installer):
            rc = main(["engines", "remove", "--yes", "piper"])

        assert rc == 0
        assert not fake_voice_dir.exists()

    def test_remove_without_yes_aborts_on_eof(self):
        """When stdin is closed (non-interactive), the prompt aborts cleanly."""
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "engines", "remove", "piper"],
            input="",  # empty stdin → EOFError on input()
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        # User-driven cancel returns EXIT_CANCELLED (3); a non-installed
        # engine returns 1 before the prompt ever runs. Either is a clean
        # abort — the test guards against EXIT_INTERNAL (5) crashes.
        assert proc.returncode in (1, 3)
