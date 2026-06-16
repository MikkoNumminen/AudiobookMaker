"""Entry point for AudiobookMaker."""

import atexit
import sys

from src import app_config
from src.ffmpeg_path import setup_ffmpeg_path
from src.ocr_path import setup_ocr_path
from src.single_instance import check_single_instance, release


def _close_splash() -> None:
    """Hide the PyInstaller bootloader splash image once the app is loaded.

    pyi_splash only exists in frozen builds shipped by PyInstaller with a
    Splash() directive in the .spec file. In dev mode / when the spec has
    no splash, the import silently fails and this is a no-op.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def main() -> None:
    # Install diagnostic logging FIRST so any failure from here on — including
    # the ffmpeg/OCR setup and the GUI import — lands in a file the user can
    # send us. Best-effort: install() never raises.
    import logging

    from src import error_log

    log_path = error_log.install()
    logging.getLogger("audiobookmaker").info(
        "AudiobookMaker starting (log: %s)", log_path
    )

    # Must be called before any pydub import (e.g. tts_engine.py) so that
    # ffmpeg.exe is on PATH when pydub initialises its converter lookup.
    setup_ffmpeg_path()

    # Same shape: make bundled tesseract.exe + gswin64c.exe discoverable
    # to ocrmypdf, and export TESSDATA_PREFIX. Safe to call when nothing
    # is bundled — no-ops cleanly in that case (dev installs that haven't
    # set up OCR locally just see is_ocr_available() return False later).
    setup_ocr_path()

    cfg = app_config.load()
    if not check_single_instance(ui_lang=cfg.ui_language):
        _close_splash()  # Don't leave the splash hanging if we bail out.
        sys.exit(0)
    atexit.register(release)

    # Close the splash right before we hand control to the GUI so the
    # main window replaces it without a visible gap.
    _close_splash()

    from src.gui_unified import run
    run()


if __name__ == "__main__":
    main()
