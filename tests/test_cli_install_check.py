"""Unit tests for src.cli.install_check — CLI command reachability diagnostics.

These pin the three failure modes the diagnostics exist to surface:
shim-not-on-PATH, GUI-shadow on the bare name, and package-not-installed.
All PATH facts are monkeypatched so the tests are hermetic and run identically
on Windows and POSIX CI (the path-parsing is separator-aware, not host-aware).
"""

from __future__ import annotations

import pytest

from src.cli import install_check as ic


class TestLooksLikeGui:
    @pytest.mark.parametrize("path", [
        r"C:\Program Files\AudiobookMaker\AudiobookMaker.exe",
        r"C:\Users\x\AppData\Local\AudiobookMaker\audiobookmaker.exe",
        "/opt/AudiobookMaker/audiobookmaker",
    ])
    def test_install_root_binary_is_gui(self, path: str) -> None:
        assert ic._looks_like_gui(path) is True

    @pytest.mark.parametrize("path", [
        r"C:\venv\Scripts\audiobookmaker.exe",
        r"C:\Python\Scripts\audiobookmaker-cli.exe",
        "/home/x/.venv/bin/audiobookmaker",
    ])
    def test_scripts_or_bin_shim_is_not_gui(self, path: str) -> None:
        assert ic._looks_like_gui(path) is False

    def test_console_shim_in_python_prefix_is_not_gui(self) -> None:
        # A console script that landed directly in a Python prefix dir (not a
        # Scripts subdir) is still the package's own CLI shim, not the GUI.
        # Regression: the first heuristic ("not in Scripts/bin") false-flagged
        # this real-world layout as a GUI shadow.
        assert ic._looks_like_gui(r"C:\Program Files\Python311\audiobookmaker.exe") is False

    def test_unrelated_binary_is_not_gui(self) -> None:
        assert ic._looks_like_gui("/usr/bin/ffmpeg") is False


def _which_map(mapping: dict[str, str]):
    """Build a fake shutil.which that resolves only the given names."""
    def fake_which(name: str, *args, **kwargs):
        return mapping.get(name)
    return fake_which


def _row(checks: list[dict], name: str) -> dict:
    return next(c for c in checks if c["name"] == name)


def test_shim_on_path_is_ok(monkeypatch):
    monkeypatch.setattr(ic.shutil, "which", _which_map({
        ic.CANONICAL: r"C:\venv\Scripts\audiobookmaker-cli.exe",
        ic.BACKCOMPAT: r"C:\venv\Scripts\audiobookmaker.exe",
    }))
    checks = ic.diagnose()
    assert _row(checks, "cli:shim")["status"] == "ok"
    assert _row(checks, "cli:gui_shadow")["status"] == "ok"


def test_shim_absent_is_warning_with_remediation(monkeypatch):
    monkeypatch.setattr(ic.shutil, "which", _which_map({}))
    shim = _row(ic.diagnose(), "cli:shim")
    assert shim["status"] == "warning"
    assert "pip install -e ." in shim["detail"]


def test_gui_shadow_detected(monkeypatch):
    # bare name resolves to the GUI in an install root, not a Scripts dir
    monkeypatch.setattr(ic.shutil, "which", _which_map({
        ic.CANONICAL: r"C:\venv\Scripts\audiobookmaker-cli.exe",
        ic.BACKCOMPAT: r"C:\Program Files\AudiobookMaker\AudiobookMaker.exe",
    }))
    shadow = _row(ic.diagnose(), "cli:gui_shadow")
    assert shadow["status"] == "warning"
    assert ic.CANONICAL in shadow["detail"]


def test_no_false_shadow_when_bare_is_a_real_shim(monkeypatch):
    monkeypatch.setattr(ic.shutil, "which", _which_map({
        ic.BACKCOMPAT: "/home/x/.venv/bin/audiobookmaker",
    }))
    assert _row(ic.diagnose(), "cli:gui_shadow")["status"] == "ok"


def test_shim_resolvable_helper(monkeypatch):
    monkeypatch.setattr(ic.shutil, "which",
                        _which_map({ic.CANONICAL: "/x/bin/audiobookmaker-cli"}))
    assert ic.shim_resolvable() is True
    monkeypatch.setattr(ic.shutil, "which", _which_map({}))
    assert ic.shim_resolvable() is False


def test_all_rows_are_advisory_and_well_formed(monkeypatch):
    monkeypatch.setattr(ic.shutil, "which", _which_map({}))
    rows = ic.diagnose()
    names = {r["name"] for r in rows}
    assert {"cli:shim", "cli:python", "cli:gui_shadow", "cli:package"} <= names
    for c in rows:
        assert c["required"] is False
        assert set(c) == {"name", "status", "required", "detail"}
