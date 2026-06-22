"""Tests for src/error_log.py — persistent diagnostic logging + crash capture.

The GUI never wrote logs to disk, so a user could not hand a developer a record
of a failed run. error_log installs a rotating file handler on the root logger
plus sys/threading excepthooks, all best-effort (must never crash startup). The
log lives under ~/.audiobookmaker/logs/ (same root as config.json), identical in
dev and frozen mode.
"""
from __future__ import annotations

import logging
import sys
import threading

import pytest

from src import error_log


@pytest.fixture
def temp_log(monkeypatch, tmp_path):
    """Redirect the log to a tmp dir and fully restore logging state after.

    install() mutates process-global state (a root-logger handler, the root
    level, sys.excepthook, threading.excepthook); without restoration these
    would leak into other test modules.
    """
    log_dir = tmp_path / "logs"
    log_file = log_dir / "audiobookmaker.log"
    monkeypatch.setattr(error_log, "LOG_DIR", log_dir)
    monkeypatch.setattr(error_log, "LOG_FILE", log_file)
    monkeypatch.setattr(error_log, "_installed", False)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_excepthook = sys.excepthook
    saved_thread_hook = threading.excepthook

    yield log_file

    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(saved_level)
    sys.excepthook = saved_excepthook
    threading.excepthook = saved_thread_hook
    error_log._installed = False


def _handler_count() -> int:
    root = logging.getLogger()
    return sum(
        1 for h in root.handlers
        if getattr(h, "name", None) == error_log._HANDLER_NAME
    )


def test_log_file_path_under_config_dir():
    from src.app_config import CONFIG_DIR

    # Production invariant: the diagnostic log lives under the per-user config
    # dir. The test session redirects the live LOG_DIR/LOG_FILE globals to a
    # throwaway dir for isolation (see conftest _isolate_diagnostic_log), so
    # assert the default derivation rather than the redirected global.
    assert error_log._default_log_dir() == CONFIG_DIR / "logs"
    assert error_log.log_file_path().name == "audiobookmaker.log"


def test_install_creates_file_and_captures_module_logging(temp_log):
    error_log.install()
    # A plain module logger (the kind sprinkled across src/) must now land
    # on disk — that's the whole point: those calls vanished before.
    logging.getLogger("some.module").error("boom-marker")
    text = temp_log.read_text(encoding="utf-8")
    assert "boom-marker" in text
    assert "ERROR" in text


def test_install_is_idempotent(temp_log):
    error_log.install()
    assert _handler_count() == 1
    error_log.install()
    error_log.install()
    assert _handler_count() == 1  # never stacks duplicate file handlers


def test_tee_line_writes_with_severity(temp_log):
    error_log.install()
    error_log.tee_line("transcript line 42", "info")
    error_log.tee_line("a warning happened", "warning")
    error_log.tee_line("it broke", "error")
    text = temp_log.read_text(encoding="utf-8")
    assert "transcript line 42" in text
    assert "a warning happened" in text and "WARNING" in text
    assert "it broke" in text and "ERROR" in text


def test_read_log_text_missing_returns_empty(temp_log):
    # Nothing installed / no file yet -> empty string, never an exception.
    assert error_log.read_log_text() == ""


def test_install_never_raises_when_handler_fails(temp_log, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full / read-only profile")

    monkeypatch.setattr(error_log, "RotatingFileHandler", boom)
    # Must swallow the failure, add no handler, and still return the path.
    assert error_log.install() == temp_log
    assert _handler_count() == 0
    assert error_log._installed is False


def test_excepthook_captures_uncaught_exception(temp_log, monkeypatch):
    # Silence the chained default hook so the test doesn't spam stderr.
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    error_log.install()
    try:
        raise ValueError("kaboom-uncaught")
    except ValueError:
        sys.excepthook(*sys.exc_info())  # what the interpreter would call
    text = temp_log.read_text(encoding="utf-8")
    assert "kaboom-uncaught" in text
    assert "Traceback" in text  # exc_info rendered the full traceback


def test_thread_excepthook_captures_and_chains(temp_log, monkeypatch):
    # Install a no-op predecessor so we can assert chaining without tripping
    # pytest's own thread-exception warning hook.
    chained = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: chained.append(args.exc_type)
    )
    error_log.install()

    def crash():
        raise RuntimeError("worker-thread-marker")

    t = threading.Thread(target=crash, name="crasher")
    t.start()
    t.join()
    text = temp_log.read_text(encoding="utf-8")
    assert "worker-thread-marker" in text  # captured to the file
    assert RuntimeError in chained  # AND chained to the previous hook (stderr)


def test_read_log_text_includes_rotated_backups(temp_log):
    # RotatingFileHandler rolls older content into .1/.2; read_log_text must
    # stitch them back oldest -> newest so an early traceback isn't dropped.
    temp_log.parent.mkdir(parents=True, exist_ok=True)
    (temp_log.parent / (temp_log.name + ".2")).write_text(
        "oldest-rotated\n", encoding="utf-8"
    )
    (temp_log.parent / (temp_log.name + ".1")).write_text(
        "middle-rotated\n", encoding="utf-8"
    )
    temp_log.write_text("live-line\n", encoding="utf-8")
    text = error_log.read_log_text()
    assert "oldest-rotated" in text
    assert "middle-rotated" in text
    assert "live-line" in text
    assert (
        text.index("oldest-rotated")
        < text.index("middle-rotated")
        < text.index("live-line")
    )
