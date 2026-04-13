"""
ffmpeg_path.py
--------------
Resolves the correct ffmpeg executable path depending on whether the app is
running as a PyInstaller frozen bundle or as a normal Python process.

Call setup_ffmpeg_path() early in main.py before any pydub import.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def get_ffmpeg_dir() -> Optional[str]:
    """Return the directory containing ffmpeg.exe, or None if not found."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return None


def get_ffmpeg_exe() -> Optional[str]:
    """Return the full path to ffmpeg.exe, searching multiple locations."""
    # 1. PyInstaller bundle root
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidate = os.path.join(sys._MEIPASS, 'ffmpeg.exe')
        if os.path.isfile(candidate):
            return candidate

    # 2. Same directory as the running executable
    exe_dir = str(Path(sys.executable).parent)
    candidate = os.path.join(exe_dir, 'ffmpeg.exe')
    if os.path.isfile(candidate):
        return candidate

    # 3. dist/ffmpeg/ in the repo (dev builds)
    repo_root = str(Path(__file__).resolve().parent.parent)
    candidate = os.path.join(repo_root, 'dist', 'ffmpeg', 'ffmpeg.exe')
    if os.path.isfile(candidate):
        return candidate

    # 4. System PATH
    found = shutil.which('ffmpeg')
    if found:
        return found

    return None


def setup_ffmpeg_path() -> None:
    """Configure pydub to use the bundled ffmpeg.

    Sets both os.environ['PATH'] and pydub's AudioSegment.converter
    so that ffmpeg is found regardless of import order.
    """
    ffmpeg_exe = get_ffmpeg_exe()
    if ffmpeg_exe is None:
        return

    ffmpeg_dir = str(Path(ffmpeg_exe).parent)

    # Update PATH for subprocess calls.
    current_path = os.environ.get('PATH', '')
    if ffmpeg_dir not in current_path:
        os.environ['PATH'] = ffmpeg_dir + os.pathsep + current_path

    # Explicitly tell pydub where ffmpeg is (bypasses its own PATH lookup).
    try:
        from pydub import AudioSegment
        AudioSegment.converter = ffmpeg_exe
        # Also set ffprobe if it exists alongside ffmpeg.
        ffprobe = os.path.join(ffmpeg_dir, 'ffprobe.exe')
        if os.path.isfile(ffprobe):
            AudioSegment.ffprobe = ffprobe
    except ImportError:
        pass
