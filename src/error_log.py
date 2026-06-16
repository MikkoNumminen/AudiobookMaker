"""Persistent diagnostic logging + crash capture for the GUI.

The GUI historically configured no Python logging, so every
``logging.getLogger(__name__)`` call vanished in the frozen windowed ``.exe``
and the on-screen log was lost the moment the window closed. There was no way
for a user to hand a developer a record of a failed run.

This installs a rotating file handler on the root logger (so every module
logger lands on disk) plus ``sys.excepthook`` / ``threading.excepthook`` so
uncaught crashes are captured too. The log lives in the per-user data dir
(``~/.audiobookmaker/logs/``) — the same root as ``config.json`` — so it is
writable and stable in BOTH dev and frozen mode, with no ``sys._MEIPASS`` trap
and no install-dir pollution. The "Save error log" button exports it.

Everything here is best-effort: diagnostics must never crash app startup, so a
failure to set up logging degrades to a no-op.
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.app_config import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "logs"
LOG_FILE = LOG_DIR / "audiobookmaker.log"

_HANDLER_NAME = "audiobookmaker-file"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3

# Module logger the GUI tee + crash hooks write through. Lines mirrored from the
# on-screen log land here; the root file handler renders them to disk.
_gui_log = logging.getLogger("audiobookmaker.gui")
_crash_log = logging.getLogger("audiobookmaker.crash")

_LEVELS = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "success": logging.INFO,
    "info": logging.INFO,
}

_installed = False


def log_file_path() -> Path:
    """Absolute path of the diagnostic log file (it may not exist yet)."""
    return LOG_FILE


def read_log_text() -> str:
    """Best-effort read of the current log file; ``""`` if missing/unreadable."""
    try:
        return LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def install() -> Path:
    """Install file logging + crash hooks (idempotent). Return the log path.

    Safe to call from multiple entry points (``src/main.py`` and the GUI
    constructor); only the first call installs handlers. Wrapped so a
    filesystem hiccup (read-only profile, exotic permissions) degrades to a
    no-op instead of taking down app startup.
    """
    global _installed
    if _installed:
        return LOG_FILE
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        if not any(getattr(h, "name", None) == _HANDLER_NAME
                   for h in root.handlers):
            handler = RotatingFileHandler(
                LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.set_name(_HANDLER_NAME)
            handler.setFormatter(logging.Formatter(_LOG_FORMAT))
            handler.setLevel(logging.INFO)
            root.addHandler(handler)
        # NOTSET (0) means "no level set" -> default WARNING for the root, which
        # would drop our INFO transcript lines. Only raise the floor, never
        # lower a more permissive level a host may have configured.
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
        _install_excepthooks()
        _installed = True
    except Exception:  # noqa: BLE001 — diagnostics must never break startup
        pass
    return LOG_FILE


def tee_line(line: str, severity: str = "info") -> None:
    """Mirror one on-screen log line into the diagnostic file.

    Called from the GUI's log-append helpers so the full run transcript — the
    context around any failure, including Chatterbox subprocess output — is
    captured on disk and survives the window closing.
    """
    try:
        _gui_log.log(_LEVELS.get(severity, logging.INFO), "%s", line)
    except Exception:  # noqa: BLE001 — never let a log tee break the UI
        pass


def _install_excepthooks() -> None:
    prev_excepthook = sys.excepthook

    def _hook(exc_type, exc, tb):
        _crash_log.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        prev_excepthook(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        _crash_log.error(
            "Uncaught exception in thread %r",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook
