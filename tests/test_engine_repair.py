"""Tests for the engine repair path — force_reinstall + 'engines repair'.

A drifted Chatterbox venv (e.g. a transformers newer than chatterbox-tts
targets) is repaired by force-reinstalling the pinned set. These tests lock
in that the force flag reaches pip and that 'engines repair' dispatches to
force_reinstall rather than a plain install.
"""

from __future__ import annotations

import os
import sys
import threading
from unittest import mock
from unittest.mock import MagicMock

import pytest

import src.engine_installer as ei
from src.engine_installer import ChatterboxInstaller, PiperInstaller


def _noop_progress(_p) -> None:
    pass


def _cli(*args: str):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _main_pip_calls(run_mock):
    """Return pip calls whose argv installs the main package set."""
    out = []
    for call in run_mock.call_args_list:
        argv = call.args[0]
        if any("chatterbox-tts" in str(tok) for tok in argv):
            out.append(argv)
    return out


# ---------------------------------------------------------------------------
# _pip_install force flag
# ---------------------------------------------------------------------------


class TestPipInstallForce:
    def test_force_adds_force_reinstall_to_main_step(self, tmp_path) -> None:
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        with mock.patch.object(
            ei, "_run_subprocess", return_value=MagicMock(returncode=0)
        ) as run:
            inst._pip_install(
                tmp_path / "py.exe", _noop_progress, threading.Event(), force=True
            )
        main_calls = _main_pip_calls(run)
        assert main_calls, "expected a main-packages pip call"
        assert "--force-reinstall" in main_calls[-1]

    def test_no_force_omits_force_reinstall(self, tmp_path) -> None:
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        with mock.patch.object(
            ei, "_run_subprocess", return_value=MagicMock(returncode=0)
        ) as run:
            inst._pip_install(
                tmp_path / "py.exe", _noop_progress, threading.Event(), force=False
            )
        main_calls = _main_pip_calls(run)
        assert main_calls
        assert "--force-reinstall" not in main_calls[-1]

    def test_force_never_touches_the_torch_step(self, tmp_path) -> None:
        # torch installs via --index-url; a forced multi-GB re-download is
        # never what repair needs, so the torch call must stay un-forced.
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        with mock.patch.object(
            ei, "_run_subprocess", return_value=MagicMock(returncode=0)
        ) as run:
            inst._pip_install(
                tmp_path / "py.exe", _noop_progress, threading.Event(), force=True
            )
        torch_calls = [
            c.args[0] for c in run.call_args_list
            if any("torch==" in str(tok) for tok in c.args[0])
        ]
        assert torch_calls, "expected a torch pip call"
        for argv in torch_calls:
            assert "--force-reinstall" not in argv


# ---------------------------------------------------------------------------
# force_reinstall wiring
# ---------------------------------------------------------------------------


class TestForceReinstall:
    def test_chatterbox_force_reinstall_calls_install_with_force(self, tmp_path) -> None:
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        with mock.patch.object(ChatterboxInstaller, "install") as mock_install:
            inst.force_reinstall(_noop_progress, threading.Event())
        mock_install.assert_called_once()
        assert mock_install.call_args.kwargs.get("force") is True

    def test_base_force_reinstall_delegates_to_plain_install(self) -> None:
        inst = PiperInstaller()
        with mock.patch.object(PiperInstaller, "install") as mock_install:
            inst.force_reinstall(_noop_progress, threading.Event())
        mock_install.assert_called_once()
        # Base path passes no force kwarg — a plain reinstall.
        assert "force" not in mock_install.call_args.kwargs


# ---------------------------------------------------------------------------
# CLI: engines repair
# ---------------------------------------------------------------------------


class TestEnginesRepairCli:
    def test_repair_unknown_engine_exits_1(self) -> None:
        assert _cli("engines", "repair", "unknown_engine_xyz").returncode == 1

    def test_repair_without_id_exits_2(self) -> None:
        assert _cli("engines", "repair").returncode == 2

    def test_repair_help_works(self) -> None:
        result = _cli("engines", "repair", "--help")
        assert result.returncode == 0
        assert "repair" in result.stdout.lower()

    def test_repair_dispatches_to_force_reinstall(self) -> None:
        from src.cli.__main__ import main
        from src.engine_installer import InstallProgress

        called: dict[str, bool] = {}

        def _fake_force(self, progress_cb, cancel_event) -> None:
            called["force_reinstall"] = True
            progress_cb(
                InstallProgress(
                    step=1, total_steps=1, step_label="Done", done=True, message="ok"
                )
            )

        with (
            mock.patch.object(PiperInstaller, "check_prerequisites", return_value=[]),
            mock.patch.object(PiperInstaller, "force_reinstall", _fake_force),
            mock.patch.object(PiperInstaller, "install", side_effect=AssertionError(
                "repair must not call install() directly"
            )),
        ):
            rc = main(["engines", "repair", "piper"])
        assert called.get("force_reinstall") is True
        assert rc == 0
