"""The installer undoes the garbled /DIR that the 3.24.0-and-older updater passes.

Those updaters wrote the install folder into their relaunch .bat as UTF-8,
and cmd.exe read it in the OEM codepage, so an app under "Äänikirjat" was
updated into "├ä├ñnikirjat". They keep doing it on the way to a fixed version,
because the .bat comes from the version being replaced. Only the installer is
new code, so installer/keep_install_dir.iss recognises the garbled folder and
installs into the real one.

These tests compile that exact file into a throwaway installer (its own AppId,
so an installed AudiobookMaker is never touched) and run it for real. They
need Windows and Inno Setup 6, and skip without them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "installer" / "tests" / "keep_install_dir_test.iss"
APP_ID_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{6F1D2E3C-9A4B-4C5D-8E7F-0A1B2C3D4E5F}_is1"
)


def _iscc() -> Path | None:
    found = shutil.which("ISCC")
    candidates = [Path(found)] if found else []
    for root in (
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ):
        if root:
            candidates.append(Path(root) / "Programs" / "Inno Setup 6" / "ISCC.exe")
            candidates.append(Path(root) / "Inno Setup 6" / "ISCC.exe")
    return next((c for c in candidates if c.is_file()), None)


@pytest.fixture(scope="module")
def setup_exe(tmp_path_factory) -> Path:
    iscc = _iscc()
    if iscc is None:
        if os.environ.get("ABM_REQUIRE_ISCC"):
            pytest.fail("ABM_REQUIRE_ISCC is set but ISCC.exe was not found")
        pytest.skip("Inno Setup 6 (ISCC.exe) not installed")
    out = tmp_path_factory.mktemp("iss")
    subprocess.run([str(iscc), "/Q", f"/O{out}", str(HARNESS)], check=True, timeout=300)
    return out / "keep_dir_test_setup.exe"


def _recorded_dir() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY) as key:
            return winreg.QueryValueEx(key, "Inno Setup: App Path")[0]
    except OSError:
        return None


@pytest.fixture
def clean_install():
    """No leftover test install before, and none after (files or registry)."""
    import winreg

    def wipe() -> None:
        recorded = _recorded_dir()
        if recorded:
            uninstaller = Path(recorded) / "unins000.exe"
            if uninstaller.is_file():
                subprocess.run(
                    [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    timeout=120,
                )
                time.sleep(2)  # the uninstaller finishes from a temp copy
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY)
        except OSError:
            pass

    wipe()
    yield
    wipe()


def _install(setup_exe: Path, target: str, log: Path) -> None:
    subprocess.run(
        [str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/DIR={target}", f"/LOG={log}"],
        check=True, timeout=120,
    )


def _wait_for(path: Path, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.2)
    return path.exists()


def _garbled(path: Path) -> str:
    """What the old relaunch .bat turned the path into on cp850 Windows."""
    return str(path).encode("utf-8").decode("cp850")


def test_garbled_dir_installs_into_the_previous_folder(setup_exe, clean_install, tmp_path):
    real = tmp_path / "Äänikirjat" / "KeepDirTest"
    _install(setup_exe, str(real), tmp_path / "first.log")
    assert (real / "probe.bat").is_file()

    garbled = _garbled(real)
    assert "├ä├ñnikirjat" in garbled
    _install(setup_exe, garbled, tmp_path / "update.log")

    assert not Path(garbled).exists(), "installed into the garbled folder"
    assert _recorded_dir() == str(real)
    # The old .bat relaunches from the garbled path, so the installer has to.
    assert _wait_for(real / "started.txt"), "the installer did not start the app"


def test_the_old_updater_script_end_to_end(setup_exe, clean_install, tmp_path):
    # The real field path: an old-style UTF-8 .bat run by a hidden cmd.exe,
    # which starts in the OEM codepage.
    real = tmp_path / "Äänikirjat" / "KeepDirTest"
    _install(setup_exe, str(real), tmp_path / "first.log")

    bat = tmp_path / "old_relaunch.bat"
    bat.write_bytes(
        (
            "@echo off\r\n"
            f'set "INSTALLER={setup_exe}"\r\n'
            f'set "APPDIR={real}"\r\n'
            '"%INSTALLER%" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /DIR="%APPDIR%"\r\n'
        ).encode("utf-8")
    )
    subprocess.run(
        ["cmd.exe", "/c", str(bat)], timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    assert not Path(_garbled(real)).exists(), "installed into the garbled folder"
    assert _recorded_dir() == str(real)
    assert (real / "probe.bat").is_file()


def test_an_already_garbled_install_is_not_garbled_further(setup_exe, clean_install, tmp_path):
    # A tester's app already sits in "├ä├ñnikirjat" after one bad update. The
    # next old-updater run garbles that name again; the app must stay put.
    already = Path(_garbled(tmp_path / "Äänikirjat")) / "KeepDirTest"
    _install(setup_exe, str(already), tmp_path / "first.log")

    twice = _garbled(already)
    assert twice != str(already)
    _install(setup_exe, twice, tmp_path / "update.log")

    assert not Path(twice).exists(), "installed into the twice-garbled folder"
    assert _recorded_dir() == str(already)


def test_a_rename_to_another_non_ascii_name_is_left_alone(setup_exe, clean_install, tmp_path):
    # Same length, different letters: a person's choice, not garbling.
    _install(setup_exe, str(tmp_path / "Ääni" / "KeepDirTest"), tmp_path / "first.log")

    moved = tmp_path / "Ööni" / "KeepDirTest"
    _install(setup_exe, str(moved), tmp_path / "move.log")

    assert (moved / "probe.bat").is_file()
    assert _recorded_dir() == str(moved)


def test_a_real_choice_of_folder_is_left_alone(setup_exe, clean_install, tmp_path):
    # A different ASCII folder is not a garbled copy: install exactly there,
    # and do not start anything (the normal updater .bat does that).
    first = tmp_path / "Äänikirjat" / "KeepDirTest"
    _install(setup_exe, str(first), tmp_path / "first.log")

    moved = tmp_path / "Elsewhere" / "KeepDirTest"
    _install(setup_exe, str(moved), tmp_path / "move.log")

    assert (moved / "probe.bat").is_file()
    assert _recorded_dir() == str(moved)
    assert not _wait_for(moved / "started.txt", seconds=2)


def test_updating_in_place_starts_nothing(setup_exe, clean_install, tmp_path):
    # The normal update: /DIR is the folder the app is already in.
    real = tmp_path / "Äänikirjat" / "KeepDirTest"
    _install(setup_exe, str(real), tmp_path / "first.log")
    _install(setup_exe, str(real), tmp_path / "update.log")

    assert _recorded_dir() == str(real)
    assert not _wait_for(real / "started.txt", seconds=2)
