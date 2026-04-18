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


def get_ffmpeg_dir() -> str | None:
    """Return the directory containing ffmpeg.exe, or None if not found."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return None


def get_ffmpeg_exe() -> str | None:
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

    # 4. One level above repo_root — covers the Chatterbox subprocess in
    # a frozen install, where this file lives at {app}\_internal\src\
    # and the bundled ffmpeg.exe sits at {app}\ffmpeg.exe.
    candidate = os.path.join(str(Path(repo_root).parent), 'ffmpeg.exe')
    if os.path.isfile(candidate):
        return candidate

    # 5. System PATH
    found = shutil.which('ffmpeg')
    if found:
        return found

    return None


_PYDUB_PATCHED = False


def _patch_pydub_no_window() -> None:
    """Monkey-patch pydub's subprocess calls to hide console windows.

    pydub calls subprocess.Popen without startupinfo or creationflags,
    causing ffmpeg/ffprobe to flash console windows on every call.
    We patch the Popen references in pydub's modules to use a wrapper
    that adds CREATE_NO_WINDOW on Windows.

    Idempotent — calling this more than once is a no-op. Without the
    guard, each call wrapped the previously-wrapped Popen, producing
    arbitrarily deep recursion (and a corresponding stack trace) on
    every spawned subprocess.
    """
    global _PYDUB_PATCHED
    if _PYDUB_PATCHED:
        return

    import subprocess as _sp

    _OrigPopen = _sp.Popen

    class _SilentPopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            if "creationflags" not in kwargs and "startupinfo" not in kwargs:
                kwargs["creationflags"] = _sp.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    # Patch the Popen reference in pydub's modules.
    try:
        import pydub.utils
        pydub.utils.Popen = _SilentPopen
    except (ImportError, AttributeError):
        pass
    try:
        import pydub.audio_segment
        pydub.audio_segment.subprocess.Popen = _SilentPopen
    except (ImportError, AttributeError):
        pass

    _PYDUB_PATCHED = True


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

    # Explicitly tell pydub where ffmpeg and ffprobe are.
    # Setting AudioSegment.converter handles ffmpeg for export/convert.
    # However, pydub's mediainfo_json() uses get_prober_name() which does
    # NOT check AudioSegment.ffprobe — it runs its own which() lookup.
    # We must also monkey-patch get_prober_name to return the full path.
    try:
        from pydub import AudioSegment
        AudioSegment.converter = ffmpeg_exe
        ffprobe = os.path.join(ffmpeg_dir, 'ffprobe.exe')
        if os.path.isfile(ffprobe):
            AudioSegment.ffprobe = ffprobe
            # Patch the prober lookup so mediainfo_json() uses our ffprobe.
            import pydub.utils
            pydub.utils.get_prober_name = lambda: ffprobe
    except ImportError:
        pass

    # On Windows, prevent ffmpeg/ffprobe subprocess calls from flashing
    # console windows.  Pydub uses bare subprocess.Popen() without
    # startupinfo, so we monkey-patch the Popen calls in pydub's modules
    # to pass CREATE_NO_WINDOW.
    if sys.platform == "win32":
        _patch_pydub_no_window()
