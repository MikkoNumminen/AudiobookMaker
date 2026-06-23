"""Regression tests for ``UnifiedApp._poll_update_check`` re-arming.

The startup update check schedules a single poll 500 ms after launching the
background check thread. A GitHub round-trip routinely takes longer than that,
so the poll must RE-ARM itself while the queue is still empty — otherwise the
result strands in the queue and the update banner never appears until the next
5-minute periodic check (frozen) or never (dev). That banner-delay bug came
from this method shadowing ``UpdateMixin._poll_update_check`` (which does
re-arm) with a copy that did ``except queue.Empty: pass`` and gave up.

These tests bypass Tk via ``__new__`` so they run headlessly in CI (no display,
no event loop) and live outside every GUI-test ignore list, so the fix is
actually CI-gated.
"""

from __future__ import annotations

import queue
import sys
from unittest.mock import MagicMock, patch

from src.auto_updater import UpdateInfo
from src.gui_unified import UnifiedApp


def _make_app() -> UnifiedApp:
    """Bare UnifiedApp with only the attributes ``_poll_update_check`` reads."""
    app = UnifiedApp.__new__(UnifiedApp)
    app._update_queue = queue.Queue()
    app._pending_update = None
    app.after = MagicMock()
    app._show_update_banner = MagicMock()
    return app


def _info(
    available: bool = True, version: str = "9.9.9", sha256: str = "a" * 64
) -> UpdateInfo:
    # A real release carries a SHA-256; the banner is only shown when one is
    # present (an update with no SHA-256 can't be installed). Tests that expect
    # the banner use the default; the no-SHA case passes sha256="".
    return UpdateInfo(
        available=available,
        current_version="3.0.0",
        latest_version=version,
        download_url="https://example.invalid/AudiobookMaker-Setup.exe",
        release_notes="notes",
        asset_size_bytes=1234,
        sha256=sha256,
    )


class TestPollUpdateCheckRearm:
    def test_rearms_when_result_not_ready(self):
        """Empty queue → poll reschedules itself in 500 ms, shows no banner."""
        app = _make_app()

        app._poll_update_check()

        app._show_update_banner.assert_not_called()
        app.after.assert_called_once_with(500, app._poll_update_check)

    def test_shows_banner_when_update_available(self):
        app = _make_app()
        info = _info(available=True)
        app._update_queue.put(info)

        app._poll_update_check()

        app._show_update_banner.assert_called_once_with(info)
        assert app._pending_update is info

    def test_no_banner_when_no_update(self):
        app = _make_app()
        app._update_queue.put(_info(available=False))

        app._poll_update_check()

        app._show_update_banner.assert_not_called()
        assert app._pending_update is None

    def test_no_banner_when_available_but_no_sha256(self):
        """A release with no SHA-256 can't be auto-installed — don't offer it,
        or the button fails with 'No SHA-256 published' and misleads the user."""
        app = _make_app()
        app._update_queue.put(_info(available=True, sha256=""))

        app._poll_update_check()

        app._show_update_banner.assert_not_called()
        assert app._pending_update is None

    def test_result_arriving_after_first_poll_is_consumed(self):
        """The race: the check returns AFTER the first poll. The re-armed
        poll must still pick the result up and show the banner."""
        app = _make_app()

        # First poll: the worker thread hasn't returned yet.
        app._poll_update_check()
        app._show_update_banner.assert_not_called()
        app.after.assert_called_once_with(500, app._poll_update_check)

        # Worker result lands now — after the first poll already ran.
        info = _info(available=True)
        app._update_queue.put(info)

        # The re-armed poll fires and consumes the stranded result.
        app._poll_update_check()

        app._show_update_banner.assert_called_once_with(info)
        assert app._pending_update is info

    def test_frozen_schedules_periodic_check_after_consume(self):
        """Frozen install: after a result is consumed, the next periodic
        check is scheduled (the branch the dev-mode tests don't exercise)."""
        app = _make_app()
        app._update_queue.put(_info(available=True))

        with patch.object(sys, "frozen", True, create=True):
            app._poll_update_check()

        app._show_update_banner.assert_called_once()
        app.after.assert_called_once_with(
            app.UPDATE_CHECK_INTERVAL_MS, app._schedule_update_check
        )


class TestCheckUpdateWorkerAlwaysEnqueues:
    """The worker must enqueue exactly one result so the poller stops re-arming
    — even if check_for_update's no-raise contract is ever violated."""

    def test_enqueues_the_check_result(self):
        app = _make_app()
        info = _info(available=True)
        with patch("src.gui_unified.check_for_update", return_value=info):
            app._check_update_worker()

        assert app._update_queue.get_nowait() is info

    def test_enqueues_no_update_on_unexpected_error(self):
        app = _make_app()
        with patch(
            "src.gui_unified.check_for_update", side_effect=RuntimeError("boom")
        ):
            app._check_update_worker()

        result = app._update_queue.get_nowait()
        assert result.available is False
