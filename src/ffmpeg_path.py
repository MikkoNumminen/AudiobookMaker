"""
ffmpeg_path.py
--------------
Resolves the correct ffmpeg executable path depending on whether the app is
running as a PyInstaller frozen bundle or as a normal Python process.

Usage in gui.py (or any entry-point module):

    from ffmpeg_path import setup_ffmpeg_path
    setup_ffmpeg_path()

Call this before any pydub import or audio operation so that pydub can
locate ffmpeg via PATH.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def get_ffmpeg_dir() -> Optional[str]:
    """
    Return the directory that contains ffmpeg.exe, or None if running in a
    regular (non-frozen) Python environment.

    When PyInstaller bundles the app in onedir mode the _MEIPASS attribute
    points to the folder that holds all collected files.  ffmpeg.exe is
    bundled into the package root (destination '.') in the spec file, so it
    lives directly inside _MEIPASS.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running inside a PyInstaller bundle
        return sys._MEIPASS
    return None


def setup_ffmpeg_path() -> None:
    """
    Prepend the bundled ffmpeg directory to the process PATH so that pydub
    (and any other subprocess-based library) can find ffmpeg.exe automatically.

    When NOT running as a frozen bundle this function is a no-op, relying on
    ffmpeg being available on the developer's system PATH instead.
    """
    ffmpeg_dir = get_ffmpeg_dir()
    if ffmpeg_dir is None:
        # Development environment — assume ffmpeg is already on PATH.
        return

    current_path = os.environ.get('PATH', '')
    if ffmpeg_dir not in current_path:
        os.environ['PATH'] = ffmpeg_dir + os.pathsep + current_path
