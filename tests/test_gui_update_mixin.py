"""Tests for the UpdateMixin GUI helper.

These tests exercise the mixin's background thread / Tk queue plumbing
without spinning up a real CTk window. The mixin declares a soft
contract in its docstring (attributes + methods the host must provide);
we build a minimal fake host that satisfies it.
"""

from __future__ import annotations

import logging
import queue
from typing import Any
from unittest.mock import patch

from src.auto_updater import UpdateInfo
from src.gui_update_mixin import UpdateMixin
from src.launcher_bridge import ProgressEvent


class _FakeWidget:
    """Minimal stand-in for CTk widgets the mixin pokes at."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def configure(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def grid(self) -> None:
        pass

    def set(self, value: float) -> None:
        self.calls.append({"set": value})


class _FakeHost(UpdateMixin):
    """Minimal test host satisfying UpdateMixin's expected attributes."""

    POLL_INTERVAL_MS = 10

    def __init__(self) -> None:
        self._update_queue: queue.Queue[UpdateInfo] = queue.Queue()
        self._event_queue: queue.Queue[ProgressEvent] = queue.Queue()
        self._pending_update: UpdateInfo | None = None
        self._update_label = _FakeWidget()
        self._update_btn = _FakeWidget()
        self._update_banner = _FakeWidget()
        self._progress_bar = _FakeWidget()
        self._after_calls: list[tuple[int, Any]] = []

    def _s(self, key: str) -> str:
        return key

    def after(self, ms: int, func: Any = None) -> str:
        self._after_calls.append((ms, func))
        return "after-id"


def _make_update_info(available: bool = True) -> UpdateInfo:
    return UpdateInfo(
        available=available,
        current_version="2.0.0",
        latest_version="3.0.0",
        download_url="https://example.com/dl.exe",
        release_notes="",
        asset_size_bytes=1000,
        sha256="a" * 64,
    )


class TestCheckUpdateWorker:
    """Background update-check worker must never bubble exceptions,
    but also must not swallow them silently — they have to show up
    in `logger.debug` so diagnostics can catch flaky update checks."""

    def test_success_enqueues_info(self) -> None:
        host = _FakeHost()
        with patch(
            "src.gui_update_mixin.check_for_update",
            return_value=_make_update_info(),
        ):
            host._check_update_worker()
        result = host._update_queue.get_nowait()
        assert result.available is True
        assert result.latest_version == "3.0.0"

    def test_exception_logs_but_does_not_raise(
        self, caplog: Any
    ) -> None:
        host = _FakeHost()
        with patch(
            "src.gui_update_mixin.check_for_update",
            side_effect=RuntimeError("network down"),
        ), caplog.at_level(logging.DEBUG, logger="src.gui_update_mixin"):
            # Must not raise.
            host._check_update_worker()

        # Queue is empty (no info to put).
        assert host._update_queue.empty()
        # The debug log records the failure for diagnostics.
        assert any(
            "Update check failed" in rec.message for rec in caplog.records
        ), "Expected 'Update check failed' log record (exc_info=True)"


