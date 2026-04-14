"""Single-instance guard for AudiobookMaker.

Prevents multiple copies of the app from running simultaneously using a
named mutex on Windows or a lock file on other platforms. Offers the
user a choice to proceed anyway (useful for power users running
Edge-TTS on one PDF and Chatterbox on another).
"""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import messagebox
from typing import Optional


_mutex_handle = None  # Windows: keep the handle alive for the process lifetime
_lock_file: Optional[Path] = None


_STRINGS = {
    "fi": {
        "title": "AudiobookMaker",
        "already_running": (
            "AudiobookMaker on jo käynnissä.\n\n"
            "Haluatko avata uuden ikkunan silti?\n"
            "(Useampi ikkuna voi aiheuttaa ongelmia GPU-moottoreiden kanssa.)"
        ),
    },
    "en": {
        "title": "AudiobookMaker",
        "already_running": (
            "AudiobookMaker is already running.\n\n"
            "Do you want to open a new window anyway?\n"
            "(Multiple windows may cause issues with GPU engines.)"
        ),
    },
}


def _s(key: str, ui_lang: str) -> str:
    """Look up a user-facing string. Falls back to Finnish on unknown language."""
    table = _STRINGS.get(ui_lang, _STRINGS["fi"])
    return table.get(key, _STRINGS["fi"][key])


def _acquire_windows_mutex() -> bool:
    """Try to acquire a named mutex. Returns True if we got it (no other instance)."""
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ERROR_ALREADY_EXISTS = 183

        _mutex_handle = kernel32.CreateMutexW(None, False, "AudiobookMaker_SingleInstance")
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True  # If mutex fails, allow the app to run.


def _acquire_lock_file() -> bool:
    """Try to create a lock file. Returns True if no other instance holds it."""
    global _lock_file
    import os
    import tempfile

    _lock_file = Path(tempfile.gettempdir()) / "audiobookmaker.lock"
    try:
        if _lock_file.exists():
            # Check if the PID in the lock file is still alive.
            try:
                pid = int(_lock_file.read_text().strip())
                # os.kill(pid, 0) raises OSError if process doesn't exist.
                os.kill(pid, 0)
                return False  # Process is still running.
            except (ValueError, OSError, PermissionError):
                pass  # Stale lock file — process is gone.

        _lock_file.write_text(str(os.getpid()))
        return True
    except OSError:
        return True  # If lock file fails, allow the app to run.


def release() -> None:
    """Release the instance lock on exit."""
    global _mutex_handle, _lock_file
    if sys.platform == "win32" and _mutex_handle is not None:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)  # type: ignore[attr-defined]
        except Exception:
            pass
        _mutex_handle = None

    if _lock_file is not None and _lock_file.exists():
        try:
            _lock_file.unlink()
        except OSError:
            pass
        _lock_file = None


def check_single_instance(ui_lang: str = "fi") -> bool:
    """Check if another instance is running. Returns True if we should proceed.

    If another instance is detected, shows a dialog asking the user
    whether to open a new window anyway. Returns False if the user
    declines (app should exit).
    """
    if sys.platform == "win32":
        is_first = _acquire_windows_mutex()
    else:
        is_first = _acquire_lock_file()

    if is_first:
        return True

    # Another instance is running — ask the user.
    title = _s("title", ui_lang)
    msg = _s("already_running", ui_lang)

    result = messagebox.askyesno(title, msg)
    if result:
        # User wants to proceed — acquire our own lock.
        if sys.platform != "win32":
            _acquire_lock_file()  # Overwrite with our PID.
        return True
    return False
