"""Tests for the install-incomplete marker + Repair clean-rebuild escalation.

The Chatterbox install writes a `.install-incomplete` sentinel inside the venv
the moment the venv is created and removes it only after the post-install smoke
test passes. While the marker is present the engine must read as NOT installed /
NOT available, so a Convert can't be launched against a half-built venv (which
fails and can corrupt the in-progress pip install). A repair (`force=True`) that
still fails its smoke test escalates once to deleting the venv and rebuilding
from scratch — the only way to fix a corrupt torch.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.engine_installer import (
    ChatterboxInstaller,
    INSTALL_INCOMPLETE_MARKER,
    is_install_incomplete,
)


def _mocked_install_steps(inst):
    """Patch every real install step except _smoke_test, which the caller
    controls. Returns the patch context managers as a list to enter."""
    return [
        patch.object(inst, "_ensure_python311", return_value=Path("/fake/py")),
        patch.object(inst, "_create_venv", return_value=Path("/fake/venv-py")),
        patch.object(inst, "_pip_install"),
        patch.object(inst, "_prefetch_models"),
        patch.object(inst, "_apply_patch"),
    ]


class TestIncompleteMarkerHelper:
    def test_marker_absent_then_present(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        venv_python = venv / "Scripts" / "python.exe"  # need not exist

        assert is_install_incomplete(venv_python) is False
        (venv / INSTALL_INCOMPLETE_MARKER).write_text("installing")
        assert is_install_incomplete(venv_python) is True

    def test_bad_path_is_not_incomplete(self):
        # A nonsense path must not raise — just report "not incomplete".
        assert is_install_incomplete("") is False


class TestIsInstalledMarkerAware:
    def test_present_venv_with_marker_reads_not_installed(self, tmp_path):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        py = inst._venv_python
        py.parent.mkdir(parents=True)
        py.write_text("")  # fake interpreter so the first branch is taken

        assert inst.is_installed() is True  # no marker yet
        inst._install_marker.write_text("installing")
        assert inst.is_installed() is False  # incomplete → not installed


class TestInstallMarkerLifecycle:
    def test_marker_present_during_install_cleared_on_success(self, tmp_path):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)  # so the marker can be written

        seen = {}

        def smoke(_venv_py, _cancel):
            seen["marked_during"] = inst._install_marker.exists()
            return None  # success

        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(inst, "_smoke_test", side_effect=smoke))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
            events = []
            inst.install(events.append, threading.Event())

        assert seen["marked_during"] is True        # marked while installing
        assert not inst._install_marker.exists()      # cleared on success
        assert any(e.done for e in events)

    def test_marker_left_on_smoke_failure(self, tmp_path):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)

        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(inst, "_smoke_test", return_value="boom"))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
            events = []
            inst.install(events.append, threading.Event())

        # A non-force install that fails smoke leaves the marker → the venv
        # correctly reads as incomplete and the user is told it failed.
        assert inst._install_marker.exists()
        assert any(e.error for e in events)


class TestRepairEscalation:
    def test_repair_escalates_to_clean_rebuild_on_corruption(self, tmp_path):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)

        # First (force-reinstall) smoke fails with a corruption-shaped error;
        # the rebuild's smoke passes.
        smoke_results = ["ImportError: DLL load failed while importing _C", None]

        def smoke(_venv_py, _cancel):
            return smoke_results.pop(0)

        remove_mock = MagicMock(return_value=True)
        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(inst, "_smoke_test", side_effect=smoke))
        ctxs.append(patch.object(inst, "remove", remove_mock))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
            events = []
            inst.force_reinstall(events.append, threading.Event())

        remove_mock.assert_called_once()        # the corrupt venv was deleted
        assert smoke_results == []               # force attempt + rebuild attempt
        assert any(e.done for e in events)        # rebuild succeeded
        assert not any(e.error for e in events)

    def test_repair_rebuild_failure_reports_error_without_looping(self, tmp_path):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)

        remove_mock = MagicMock(return_value=True)
        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(
            inst, "_smoke_test", return_value="ImportError: DLL load failed"
        ))
        ctxs.append(patch.object(inst, "remove", remove_mock))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
            events = []
            inst.force_reinstall(events.append, threading.Event())

        remove_mock.assert_called_once()        # escalated exactly once, no loop
        assert any(e.error for e in events)       # final failure surfaced
        assert not any(e.done for e in events)

    def test_repair_does_not_rebuild_on_environmental_failure(self, tmp_path):
        # A CUDA-runtime smoke failure is environmental, not corruption — a
        # clean rebuild can't fix it, so do NOT delete+rebuild; report directly.
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)

        remove_mock = MagicMock(return_value=True)
        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(
            inst, "_smoke_test",
            return_value="RuntimeError: Found no NVIDIA driver on your system",
        ))
        ctxs.append(patch.object(inst, "remove", remove_mock))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
            events = []
            inst.force_reinstall(events.append, threading.Event())

        remove_mock.assert_not_called()         # no wasted rebuild
        assert any(e.error for e in events)
        assert not any(e.done for e in events)

    def test_repair_rebuilds_on_winerror_126_dll_failure(self, tmp_path):
        # The common Windows corruption: a missing dependency DLL surfaces as
        # OSError [WinError 126] "Error loading ...", NOT an ImportError — it
        # must still trigger the clean rebuild (this is exactly a corrupt torch).
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst._venv_path.mkdir(parents=True)
        smoke_results = [
            "OSError: [WinError 126] The specified module could not be found. "
            'Error loading "...\\torch\\lib\\fbgemm.dll" or one of its dependencies.',
            None,
        ]

        def smoke(_venv_py, _cancel):
            return smoke_results.pop(0)

        remove_mock = MagicMock(return_value=True)
        ctxs = _mocked_install_steps(inst)
        ctxs.append(patch.object(inst, "_smoke_test", side_effect=smoke))
        ctxs.append(patch.object(inst, "remove", remove_mock))
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
            events = []
            inst.force_reinstall(events.append, threading.Event())

        remove_mock.assert_called_once()
        assert smoke_results == []
        assert any(e.done for e in events)
        assert not any(e.error for e in events)


class TestBridgeCheckStatusIncomplete:
    def test_check_status_unavailable_when_incomplete(self):
        from src.tts_chatterbox_bridge import ChatterboxEngine

        eng = ChatterboxEngine()
        with patch(
            "src.tts_chatterbox_bridge.resolve_chatterbox_python",
            return_value=Path("/fake/venv/bin/python"),
        ), patch("src.engine_installer.is_install_incomplete", return_value=True):
            status = eng.check_status()

        assert status.available is False
        low = status.reason.lower()
        assert "installing" in low or "kesken" in low

    def test_check_status_available_when_complete(self):
        from src.tts_chatterbox_bridge import ChatterboxEngine

        eng = ChatterboxEngine()
        with patch(
            "src.tts_chatterbox_bridge.resolve_chatterbox_python",
            return_value=Path("/fake/venv/bin/python"),
        ), patch("src.engine_installer.is_install_incomplete", return_value=False):
            status = eng.check_status()

        assert status.available is True
