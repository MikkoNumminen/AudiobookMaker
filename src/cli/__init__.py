"""AudiobookMaker command-line interface.

Thin presentation layer over the existing backend modules.
No business logic lives here — all real work is delegated to the
same functions the GUI uses.

Entry point:
    python -m src.cli [subcommand] [flags]
"""

from src.auto_updater import APP_VERSION

__version__ = APP_VERSION

__all__ = ["__version__"]
