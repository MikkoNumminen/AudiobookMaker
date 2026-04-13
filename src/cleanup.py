"""Detect and remove orphan AudiobookMaker installs and shortcuts.

When the user has installed multiple versions over time (e.g. one in
Program Files, one in LocalAppData, one in a custom directory), Windows
keeps stale shortcuts and registry entries pointing to the old copies.
The user often launches the wrong one — getting old code, broken
auto-updates, or stale icons.

This module:
  - Detects old installs in known locations
  - Detects orphan .lnk shortcuts that point to deleted exes
  - Provides safe cleanup (uninstaller-first, then directory removal)
  - Never touches the currently running install

Designed to be called on app startup so the user is offered a one-click
cleanup dialog whenever stale stuff is found.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class OldInstall:
    """A previous AudiobookMaker installation found on disk."""

    path: Path                # Install directory (contains AudiobookMaker.exe)
    exe_path: Path            # Full path to AudiobookMaker.exe
    has_uninstaller: bool     # Whether unins000.exe is present
    size_mb: float            # Approximate size on disk (MB)


@dataclass
class OrphanShortcut:
    """A .lnk shortcut pointing to a non-existent AudiobookMaker.exe."""

    shortcut_path: Path       # Path to the .lnk file
    target_path: str          # Target path the shortcut points to (broken)


# ---------------------------------------------------------------------------
# Detection — old installs
# ---------------------------------------------------------------------------


def _candidate_install_dirs() -> list[Path]:
    """Return install locations to scan for old AudiobookMaker copies."""
    candidates: list[Path] = []

    # Per-user (current default)
    if local := os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(local) / "Programs" / "AudiobookMaker")

    # Old admin install path (Program Files)
    if pf := os.environ.get("PROGRAMFILES"):
        candidates.append(Path(pf) / "AudiobookMaker")
    if pf86 := os.environ.get("PROGRAMFILES(X86)"):
        candidates.append(Path(pf86) / "AudiobookMaker")

    # Common dev/custom locations
    candidates.append(Path("C:/AudiobookMaker"))
    candidates.append(Path("D:/AudiobookMaker"))
    candidates.append(Path("D:/koodaamista/AudiobookMakerApp"))

    return candidates


def _is_audiobook_install(directory: Path) -> bool:
    """Verify a directory is an actual AudiobookMaker install."""
    if not directory.is_dir():
        return False
    exe = directory / "AudiobookMaker.exe"
    if not exe.is_file():
        return False
    # Sanity check: PyInstaller bundles include _internal/ next to the exe.
    return (directory / "_internal").is_dir() or (directory / "ffmpeg.exe").is_file()


def _dir_size_mb(directory: Path) -> float:
    """Approximate size of a directory in MB. Best-effort, ignores errors."""
    total = 0
    try:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total / (1024 * 1024)


def find_old_installs(current_exe: Optional[Path] = None) -> list[OldInstall]:
    """Return all AudiobookMaker installs that are NOT the currently running one.

    Args:
        current_exe: Path to the running AudiobookMaker.exe. Detected from
            sys.executable when omitted. Used to exclude self from the result.
    """
    if current_exe is None:
        current_exe = Path(sys.executable).resolve()

    try:
        current_dir = current_exe.parent.resolve()
    except OSError:
        current_dir = None

    found: list[OldInstall] = []
    seen: set[Path] = set()

    for cand in _candidate_install_dirs():
        try:
            real = cand.resolve()
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        if current_dir and real == current_dir:
            continue
        if not _is_audiobook_install(real):
            continue

        exe = real / "AudiobookMaker.exe"
        found.append(OldInstall(
            path=real,
            exe_path=exe,
            has_uninstaller=(real / "unins000.exe").is_file(),
            size_mb=_dir_size_mb(real),
        ))

    return found


# ---------------------------------------------------------------------------
# Detection — orphan shortcuts
# ---------------------------------------------------------------------------


def _candidate_shortcut_dirs() -> list[Path]:
    """Return locations where AudiobookMaker shortcuts could live."""
    dirs: list[Path] = []
    if appdata := os.environ.get("APPDATA"):
        dirs.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        dirs.append(Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch")
        dirs.append(
            Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch"
            / "User Pinned" / "TaskBar"
        )
    if userprofile := os.environ.get("USERPROFILE"):
        dirs.append(Path(userprofile) / "Desktop")
    if public := os.environ.get("PUBLIC"):
        dirs.append(Path(public) / "Desktop")
    if pd := os.environ.get("PROGRAMDATA"):
        dirs.append(Path(pd) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def _read_shortcut_target(lnk_path: Path) -> Optional[str]:
    """Parse a Windows .lnk file and return the target path, or None on error.

    Uses minimal binary parsing of the Shell Link format so we don't need
    pywin32 as a dependency.  Best-effort: returns None if parsing fails
    or the target can't be resolved.
    """
    try:
        with open(lnk_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    # Shell Link Header is 0x4C bytes; check magic.
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return None

    flags = struct.unpack("<I", data[20:24])[0]
    HAS_LINK_TARGET_ID_LIST = 0x01
    HAS_LINK_INFO = 0x02

    offset = 0x4C
    if flags & HAS_LINK_TARGET_ID_LIST:
        try:
            id_list_size = struct.unpack("<H", data[offset:offset + 2])[0]
            offset += 2 + id_list_size
        except struct.error:
            return None

    if not (flags & HAS_LINK_INFO):
        return None

    try:
        link_info_size = struct.unpack("<I", data[offset:offset + 4])[0]
        link_info = data[offset:offset + link_info_size]
    except struct.error:
        return None

    if len(link_info) < 28:
        return None

    try:
        local_base_path_offset = struct.unpack("<I", link_info[16:20])[0]
        common_path_suffix_offset = struct.unpack("<I", link_info[24:28])[0]
    except struct.error:
        return None

    if local_base_path_offset == 0:
        return None

    # Read null-terminated ANSI strings
    def _read_cstr(buf: bytes, off: int) -> str:
        end = buf.find(b"\x00", off)
        if end == -1:
            end = len(buf)
        try:
            return buf[off:end].decode("mbcs", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return buf[off:end].decode("latin-1", errors="replace")

    base = _read_cstr(link_info, local_base_path_offset)
    suffix = _read_cstr(link_info, common_path_suffix_offset) if common_path_suffix_offset else ""
    return base + suffix


def find_orphan_shortcuts() -> list[OrphanShortcut]:
    """Return AudiobookMaker .lnk shortcuts whose targets no longer exist."""
    orphans: list[OrphanShortcut] = []
    seen: set[Path] = set()

    for d in _candidate_shortcut_dirs():
        if not d.is_dir():
            continue
        try:
            entries = list(d.rglob("AudiobookMaker*.lnk"))
        except OSError:
            continue
        for lnk in entries:
            try:
                real = lnk.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)

            target = _read_shortcut_target(lnk)
            if target is None:
                continue
            if not os.path.isfile(target):
                orphans.append(OrphanShortcut(
                    shortcut_path=lnk,
                    target_path=target,
                ))

    return orphans


# ---------------------------------------------------------------------------
# Cleanup actions
# ---------------------------------------------------------------------------


def remove_old_install(install: OldInstall, timeout: int = 60) -> tuple[bool, str]:
    """Remove an old install. Tries the uninstaller first, falls back to rmtree.

    Returns (success, message). Never raises.
    """
    if install.has_uninstaller:
        uninstaller = install.path / "unins000.exe"
        try:
            # Inno Setup silent flags: /SILENT shows progress, /VERYSILENT hides it
            proc = subprocess.run(
                [str(uninstaller), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            # Inno Setup may leave the directory behind even after success.
            if install.path.exists():
                shutil.rmtree(install.path, ignore_errors=True)
            return (True, f"Uninstalled via {uninstaller.name}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Fall through to rmtree
            pass

    try:
        shutil.rmtree(install.path, ignore_errors=False)
        return (True, "Directory removed")
    except OSError as exc:
        # Best-effort second pass with ignore_errors
        shutil.rmtree(install.path, ignore_errors=True)
        if install.path.exists():
            return (False, f"Could not fully remove: {exc}")
        return (True, "Directory removed (some files locked)")


def remove_orphan_shortcut(shortcut: OrphanShortcut) -> tuple[bool, str]:
    """Delete an orphan .lnk file. Returns (success, message)."""
    try:
        shortcut.shortcut_path.unlink()
        return (True, "Removed")
    except OSError as exc:
        return (False, str(exc))
