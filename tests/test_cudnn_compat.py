"""Tests for :mod:`src.voice_pack._cudnn_compat`.

The auto-fix is pure filesystem + importlib introspection, so these
tests fake the ctranslate2 / torch installs by writing tiny placeholder
DLL files into ``tmp_path`` and monkeypatching
``importlib.util.find_spec`` to point the module at them. No real CUDA
/ cuDNN / faster-whisper / torch install is required.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.voice_pack import _cudnn_compat


def _fake_spec(origin: Path) -> SimpleNamespace:
    """Build a stand-in for an ``importlib.machinery.ModuleSpec``."""
    return SimpleNamespace(origin=str(origin))


def _install_fake_package(root: Path, name: str, subpath: str | None = None) -> Path:
    """Create ``root/name/__init__.py`` and optionally a DLL inside.

    Returns the path of the DLL (whether it was created or not) so the
    caller can decide to ``.touch()`` it afterwards.
    """
    pkg_dir = root / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    if subpath is None:
        return pkg_dir / _cudnn_compat._CUDNN_DLL
    target = pkg_dir / subpath / _cudnn_compat._CUDNN_DLL
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture
def force_windows(monkeypatch):
    """Pretend to be Windows regardless of the host OS."""
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def fake_environments(tmp_path, monkeypatch):
    """Return a helper that stages ctranslate2 + torch packages on disk.

    The helper takes two booleans: whether each package should have its
    cudnn64_9.dll present. It wires ``importlib.util.find_spec`` so the
    detector finds the fake packages' ``__init__.py`` files.
    """

    def _setup(*, ct2_has_dll: bool, torch_has_dll: bool,
               ct2_installed: bool = True, torch_installed: bool = True) -> dict:
        ct2_dll = _install_fake_package(tmp_path, "ctranslate2")
        torch_dll = _install_fake_package(tmp_path, "torch", subpath="lib")
        if ct2_has_dll:
            ct2_dll.write_bytes(b"fake ct2 cudnn")
        if torch_has_dll:
            torch_dll.write_bytes(b"fake torch cudnn")

        specs = {}
        if ct2_installed:
            specs["ctranslate2"] = _fake_spec(tmp_path / "ctranslate2" / "__init__.py")
        if torch_installed:
            specs["torch"] = _fake_spec(tmp_path / "torch" / "__init__.py")

        def _find_spec(name: str):
            return specs.get(name)

        monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
        return {"ct2_dll": ct2_dll, "torch_dll": torch_dll}

    return _setup


def _sidelined(dll: Path) -> Path:
    """Return the ``.disabled`` sibling path for a given DLL."""
    return dll.with_name(dll.name + _cudnn_compat._DISABLED_SUFFIX)


def test_auto_renames_when_both_dlls_present(force_windows, fake_environments, capsys):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    ct2_dll = paths["ct2_dll"]
    sidelined = _sidelined(ct2_dll)
    # ctranslate2's copy is gone, renamed aside with .disabled suffix.
    assert not ct2_dll.exists()
    assert sidelined.exists()
    # torch's copy is untouched.
    assert paths["torch_dll"].exists()

    captured = capsys.readouterr()
    assert captured.out == ""
    # One concise info line, no multi-line warning banner.
    assert "sidelined duplicate cuDNN DLL" in captured.err
    assert "duplicate cuDNN DLL detected" not in captured.err
    assert str(sidelined) in captured.err


def test_silent_when_only_ctranslate2_has_dll(force_windows, fake_environments, capsys):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=False)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    # Never orphan ctranslate2: leave its DLL alone when torch's is absent.
    assert paths["ct2_dll"].exists()
    assert not _sidelined(paths["ct2_dll"]).exists()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_silent_when_only_torch_has_dll(force_windows, fake_environments, capsys):
    paths = fake_environments(ct2_has_dll=False, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    # No ctranslate2 copy to rename; torch's copy untouched.
    assert not paths["ct2_dll"].exists()
    assert paths["torch_dll"].exists()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_silent_when_neither_dll_present(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=False, torch_has_dll=False)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_silent_when_ctranslate2_not_installed(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=True, torch_has_dll=True, ct2_installed=False)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_when_torch_not_installed(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=True, torch_has_dll=True, torch_installed=False)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_silent_on_non_windows(monkeypatch, fake_environments, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    # Nothing touched on non-Windows.
    assert paths["ct2_dll"].exists()
    assert paths["torch_dll"].exists()
    assert not _sidelined(paths["ct2_dll"]).exists()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_existing_disabled_sidecar_triggers_silent_delete(
    force_windows, fake_environments, capsys
):
    """pip put the duplicate back; a .disabled file already exists.

    In that case we just delete the fresh duplicate — no rename, no
    stderr output.
    """
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)
    # Pre-seed the sideline: a previous run already renamed once.
    sidelined = _sidelined(paths["ct2_dll"])
    sidelined.write_bytes(b"old sidelined copy")

    _cudnn_compat.ensure_no_duplicate_cudnn()

    # Fresh duplicate is gone, old sideline preserved.
    assert not paths["ct2_dll"].exists()
    assert sidelined.exists()
    assert sidelined.read_bytes() == b"old sidelined copy"

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_permission_error_on_rename_falls_back_to_warning(
    force_windows, fake_environments, monkeypatch, capsys
):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    def _boom(self, *args, **kwargs):
        raise PermissionError("file locked by another process")

    monkeypatch.setattr(Path, "rename", _boom)

    # Must not raise.
    _cudnn_compat.ensure_no_duplicate_cudnn()

    # Files untouched because rename failed.
    assert paths["ct2_dll"].exists()
    assert not _sidelined(paths["ct2_dll"]).exists()

    captured = capsys.readouterr()
    # Fallback warning with the manual command.
    assert "duplicate cuDNN DLL detected" in captured.err
    assert "auto-fix FAILED" in captured.err
    assert "rename failed" in captured.err
    # Manual command should still be in there.
    assert "cudnn64_9.dll.disabled" in captured.err


def test_permission_error_on_delete_falls_back_to_warning(
    force_windows, fake_environments, monkeypatch, capsys
):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)
    _sidelined(paths["ct2_dll"]).write_bytes(b"old")

    def _boom(self, *args, **kwargs):
        raise PermissionError("file locked")

    monkeypatch.setattr(Path, "unlink", _boom)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert "auto-fix FAILED" in captured.err
    assert "could not delete fresh duplicate" in captured.err


def test_idempotent_safe_to_call_twice(force_windows, fake_environments, capsys):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()
    # Second call: nothing to do (no ct2 dll anymore), so no new output.
    _cudnn_compat.ensure_no_duplicate_cudnn()

    sidelined = _sidelined(paths["ct2_dll"])
    assert not paths["ct2_dll"].exists()
    assert sidelined.exists()

    captured = capsys.readouterr()
    # Only one info line from the first call.
    assert captured.err.count("sidelined duplicate cuDNN DLL") == 1


def test_idempotent_when_pip_puts_file_back(force_windows, fake_environments, capsys):
    """Simulate ``pip install --upgrade ctranslate2`` between calls.

    First call renames; pip puts a fresh duplicate back; second call
    silently deletes it because .disabled already exists.
    """
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()
    # pip reinstates the DLL:
    paths["ct2_dll"].write_bytes(b"fresh ct2 cudnn from pip")

    _cudnn_compat.ensure_no_duplicate_cudnn()

    # Fresh copy gone, .disabled preserved from first call.
    assert not paths["ct2_dll"].exists()
    assert _sidelined(paths["ct2_dll"]).exists()

    captured = capsys.readouterr()
    # One info from the first call; second call is silent.
    assert captured.err.count("sidelined duplicate cuDNN DLL") == 1


def test_find_spec_raising_is_swallowed(monkeypatch, force_windows, capsys):
    def _boom(name):
        raise ImportError(f"no module named {name!r}")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""
