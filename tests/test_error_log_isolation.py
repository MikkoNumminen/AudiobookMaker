"""Regression: the test suite must never write to the real diagnostic log.

``src.error_log.install()`` attaches a ``RotatingFileHandler`` to the ROOT
logger at ``~/.audiobookmaker/logs/audiobookmaker.log`` — the file the GUI's
"Save error log" button exports for field support. Before the session-scoped
``_isolate_diagnostic_log`` fixture (in ``conftest.py``), every test that
constructed the GUI leaked its fixture-induced ERROR/WARNING records into that
real file, so an exported field log read as mostly test noise.

These tests pin the redirect so a future change can't silently re-point the
handler back at the user's real export target.
"""

from __future__ import annotations

import logging

import pytest

from src import app_config, error_log


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Snapshot/restore root-logger handlers + ``error_log``'s install latch.

    The tests below call ``error_log.install()``, which attaches a handler
    named ``error_log._HANDLER_NAME`` to the root logger and latches
    ``_installed``. ``install()`` is idempotent *by handler name*, so a leaked
    handler from one test makes a later ``temp_log``-based test's ``install()``
    silently no-op — its per-test log file is then never written and
    ``read_text()`` raises ``FileNotFoundError`` (order-dependent breakage).
    Remove any handler these tests added and restore the latch on teardown so
    the tests are self-contained.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_installed = error_log._installed
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if (handler not in saved_handlers
                    and getattr(handler, "name", None) == error_log._HANDLER_NAME):
                root.removeHandler(handler)
                handler.close()
        error_log._installed = saved_installed


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 — flushing is best-effort
            pass


def test_log_file_is_redirected_off_the_user_dir() -> None:
    """The session fixture must move LOG_FILE off ``~/.audiobookmaker/logs``."""
    real_log = app_config.CONFIG_DIR / "logs" / "audiobookmaker.log"
    assert error_log.LOG_FILE != real_log
    # And not merely a sibling: nothing under the redirected path may resolve
    # back into the real per-user config dir.
    assert app_config.CONFIG_DIR not in error_log.LOG_FILE.parents


def test_install_writes_records_to_the_redirected_file() -> None:
    """A fresh ``install()`` + log call lands in the temp file, proving the
    root handler points at the redirect (so the real user file is untouched).
    """
    error_log._installed = False
    error_log.install()
    logging.getLogger("audiobookmaker.gui").error("isolation-regression-marker")
    _flush_root_handlers()

    assert error_log.LOG_FILE.exists()
    assert "isolation-regression-marker" in error_log.LOG_FILE.read_text(
        encoding="utf-8"
    )


def test_real_user_log_is_never_written_during_tests() -> None:
    """The actual safety boundary: logging an error during a test must not
    grow (or create) the real ``~/.audiobookmaker/logs/audiobookmaker.log``.

    This is the property the redirect exists for — asserting it directly,
    rather than only the proxy "LOG_FILE points elsewhere", so a regression
    that re-attaches a handler to the real path (in addition to the redirect)
    is still caught.
    """
    real_log = app_config.CONFIG_DIR / "logs" / "audiobookmaker.log"
    before = real_log.stat().st_size if real_log.exists() else None

    error_log._installed = False
    error_log.install()
    marker = "real-log-untouched-marker"
    logging.getLogger("audiobookmaker.gui").error(marker)
    _flush_root_handlers()

    after = real_log.stat().st_size if real_log.exists() else None
    assert after == before
    if real_log.exists():
        assert marker not in real_log.read_text(encoding="utf-8", errors="replace")
