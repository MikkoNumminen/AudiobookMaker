"""Tests for :mod:`src.voice_pack._cudnn_compat`.

The detector is pure filesystem + importlib introspection, so these tests
fake the ctranslate2 / torch installs by writing tiny placeholder DLL
files into ``tmp_path`` and monkeypatching ``importlib.util.find_spec`` to
point the module at them. No real CUDA / cuDNN / faster-whisper / torch
install is required.
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


def test_warns_when_both_dlls_present(force_windows, fake_environments, capsys):
    paths = fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate cuDNN DLL detected" in captured.err
    assert str(paths["ct2_dll"]) in captured.err
    assert str(paths["torch_dll"]) in captured.err
    # The actionable rename command should mention the sidelined name.
    assert "cudnn64_9.dll.disabled" in captured.err
    # Pointer to docs is part of the contract.
    assert "docs/CONVENTIONS.md" in captured.err


def test_silent_when_only_ctranslate2_has_dll(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=True, torch_has_dll=False)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_silent_when_only_torch_has_dll(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=False, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

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
    fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_idempotent_safe_to_call_twice(force_windows, fake_environments, capsys):
    fake_environments(ct2_has_dll=True, torch_has_dll=True)

    _cudnn_compat.ensure_no_duplicate_cudnn()
    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    # Two calls = two warnings. Point of the test is that no state is
    # stashed and no exception is raised.
    assert captured.err.count("duplicate cuDNN DLL detected") == 2


def test_find_spec_raising_is_swallowed(monkeypatch, force_windows, capsys):
    def _boom(name):
        raise ImportError(f"no module named {name!r}")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    _cudnn_compat.ensure_no_duplicate_cudnn()

    captured = capsys.readouterr()
    assert captured.err == ""
