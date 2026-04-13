"""Entry point for AudiobookMaker."""

import atexit
import sys

from src import app_config
from src.single_instance import check_single_instance, release


def main() -> None:
    cfg = app_config.load()
    if not check_single_instance(ui_lang=cfg.ui_language):
        sys.exit(0)
    atexit.register(release)

    from src.gui_unified import run
    run()


if __name__ == "__main__":
    main()
