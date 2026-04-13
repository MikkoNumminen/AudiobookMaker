"""Entry point for AudiobookMaker."""

import atexit
import sys

from src import app_config
from src.ffmpeg_path import setup_ffmpeg_path
from src.single_instance import check_single_instance, release


def main() -> None:
    # Must be called before any pydub import (e.g. tts_engine.py) so that
    # ffmpeg.exe is on PATH when pydub initialises its converter lookup.
    setup_ffmpeg_path()

    cfg = app_config.load()
    if not check_single_instance(ui_lang=cfg.ui_language):
        sys.exit(0)
    atexit.register(release)

    from src.gui_unified import run
    run()


if __name__ == "__main__":
    main()
