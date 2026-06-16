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
from unittest.mock import MagicMock, patch

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


class TestDownloadFailureClearsPending:
    """If the download fails, _pending_update must be cleared so the
    banner doesn't keep pointing at a broken release handle."""

    def test_update_failed_event_clears_pending(self) -> None:
        host = _FakeHost()
        # Simulate a successful check that set the handle.
        host._pending_update = _make_update_info()

        # Worker surfaced a failure.
        host._event_queue.put(
            ProgressEvent(kind="update_failed", raw_line="Download failed: 404")
        )

        # Patch tkinter.messagebox so the error popup doesn't try to
        # open a real window in the test process.
        fake_messagebox = MagicMock()
        with patch.dict(
            "sys.modules", {"tkinter": MagicMock(messagebox=fake_messagebox)}
        ):
            host._pump_update_download()

        assert host._pending_update is None, (
            "Pending update handle must be cleared after download failure"
        )
        # User got the error dialog.
        fake_messagebox.showerror.assert_called_once()



class TestApplyUpdateRecovery:
    """A failed installer hand-off must recover the banner (re-enable the
    button + surface the error) instead of leaving it frozen on 'installing'
    — the silent freeze observed in the field when apply_update aborts."""

    def test_failed_handoff_reenables_button_and_shows_error(self) -> None:
        host = _FakeHost()
        with patch(
            "src.gui_update_mixin.apply_update",
            side_effect=RuntimeError("installer locked"),
        ), patch("tkinter.messagebox.showerror") as showerr:
            host._apply_update_and_recover("setup.exe", "3.0.0")
        assert {"state": "normal", "text": "update_now"} in host._update_btn.calls
        assert {"set": 0} in host._progress_bar.calls
        showerr.assert_called_once()

    def test_successful_handoff_leaves_banner_untouched(self) -> None:
        # apply_update normally os._exit's; mocked as a no-op here. The recovery
        # branch must NOT run on success (no spurious re-enable / error popup).
        host = _FakeHost()
        with patch("src.gui_update_mixin.apply_update", return_value=None) as au, \
             patch("tkinter.messagebox.showerror") as showerr:
            host._apply_update_and_recover("setup.exe", "3.0.0")
        au.assert_called_once()
        assert host._update_btn.calls == []
        showerr.assert_not_called()


class TestUpdateDoneWiring:
    """The update_done branch of _pump_update_download must hand off through
    _apply_update_and_recover (not raw apply_update) — otherwise a failed
    hand-off would freeze the banner. This proves the WIRING, not just the
    wrapper in isolation: reverting the lambda back to apply_update fails here."""

    def test_update_done_schedules_recovery_wrapper(self) -> None:
        host = _FakeHost()
        host._pending_update = _make_update_info()  # latest_version == "3.0.0"
        host._event_queue.put(
            ProgressEvent(kind="update_done", raw_line=r"C:\tmp\setup.exe")
        )

        # Record routing through the wrapper; guard module-level apply_update to
        # a no-op so a wrong wiring can't os._exit the test (it just won't route).
        routed: list[tuple[Any, Any]] = []
        host._apply_update_and_recover = (  # type: ignore[method-assign]
            lambda path, expected: routed.append((path, expected))
        )

        with patch("src.gui_update_mixin.apply_update", lambda *a, **k: None):
            host._pump_update_download()
            cbs = [func for ms, func in host._after_calls if ms == 200 and func]
            assert cbs, "update_done must schedule a 200ms hand-off callback"
            cbs[-1]()  # invoke the scheduled callback

        assert routed, "the scheduled callback must route through _apply_update_and_recover"
        path, expected = routed[0]
        assert str(path).endswith("setup.exe")
        assert expected == "3.0.0"
