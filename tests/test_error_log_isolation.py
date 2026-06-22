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

from src import app_config, error_log


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

    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 — flushing is best-effort
            pass

    assert error_log.LOG_FILE.exists()
    assert "isolation-regression-marker" in error_log.LOG_FILE.read_text(
        encoding="utf-8"
    )
