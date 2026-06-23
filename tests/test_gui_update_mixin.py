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
        self.calls.append({"grid": True})

    def grid_remove(self) -> None:
        self.calls.append({"grid_remove": True})

    def start(self) -> None:
        self.calls.append({"start": True})

    def stop(self) -> None:
        self.calls.append({"stop": True})

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
        self._update_progress = _FakeWidget()
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


class TestNoShadowedUpdaterMethods:
    """The update check / poll / banner moved to the host (UnifiedApp). Guard
    that the mixin no longer ALSO defines them — a re-introduced copy would be
    silently shadowed, so a fix could land on dead code (the bug this collapse
    removed). The check worker is verified end-to-end on the host in
    tests/test_update_banner_poll.py."""

    def test_mixin_does_not_redefine_host_owned_methods(self) -> None:
        for name in (
            "_check_update_worker",
            "_poll_update_check",
            "_schedule_update_recheck",
        ):
            assert name not in vars(UpdateMixin), (
                f"{name} was re-added to UpdateMixin; it belongs to the host "
                "(UnifiedApp) only. A mixin copy is silently shadowed by MRO."
            )

    def test_mixin_still_owns_download_install_flow(self) -> None:
        for name in (
            "_on_update_click",
            "_download_update_worker",
            "_pump_update_download",
            "_apply_update_and_recover",
        ):
            assert name in vars(UpdateMixin), (
                f"{name} must stay on UpdateMixin (the download/install hand-off)."
            )


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

    def test_update_failed_hides_dead_banner(self) -> None:
        # With the pending handle cleared, the Update button is a no-op, so the
        # banner must be hidden — otherwise the user is left with a dead
        # "Update now" button until the next periodic check.
        host = _FakeHost()
        host._pending_update = _make_update_info()
        host._event_queue.put(
            ProgressEvent(kind="update_failed", raw_line="boom")
        )
        with patch.dict(
            "sys.modules", {"tkinter": MagicMock(messagebox=MagicMock())}
        ):
            host._pump_update_download()
        assert {"grid_remove": True} in host._update_banner.calls

    def test_download_failure_logs_for_the_diagnostic_file(
        self, caplog: Any
    ) -> None:
        # Auto-update is P0: a download/verify failure must land in the log
        # file (via the root handler) so it's in what the user sends us, not
        # only in the transient error dialog.
        host = _FakeHost()
        host._pending_update = _make_update_info()
        with patch(
            "src.gui_update_mixin.download_update",
            side_effect=RuntimeError("sha mismatch"),
        ), caplog.at_level(logging.ERROR, logger="src.gui_update_mixin"):
            host._download_update_worker()  # must not raise

        ev = host._event_queue.get_nowait()
        assert ev.kind == "update_failed"
        assert any(
            "Update download/verify failed" in rec.message
            for rec in caplog.records
        ), "download failure must be logged with a traceback"



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


class TestBannerProgress:
    """The banner shows its OWN download progress (motion where the user
    clicked), not just the synthesis bar elsewhere in the window — the
    'no indicator / looks frozen' report. It animates immediately
    (indeterminate) and becomes a real percentage bar once size is known."""

    def test_begin_shows_and_animates_indeterminate(self) -> None:
        host = _FakeHost()
        host._begin_update_progress()
        assert {"mode": "indeterminate"} in host._update_progress.calls
        assert {"grid": True} in host._update_progress.calls
        assert {"start": True} in host._update_progress.calls

    def test_render_switches_to_determinate_and_sets_fraction(self) -> None:
        host = _FakeHost()
        host._begin_update_progress()
        host._render_update_progress(25, 100)
        assert {"stop": True} in host._update_progress.calls
        assert {"mode": "determinate"} in host._update_progress.calls
        assert {"set": 0.25} in host._update_progress.calls
        # Button carries the numeric percentage too (key returned verbatim
        # by the fake _s, .format leaves it unchanged).
        assert any(
            c.get("text") == "update_downloading_pct"
            for c in host._update_btn.calls
        )

    def test_render_at_completion_shows_verifying(self) -> None:
        host = _FakeHost()
        host._begin_update_progress()
        host._render_update_progress(100, 100)
        assert {"set": 1.0} in host._update_progress.calls
        assert any(
            c.get("text") == "update_verifying" for c in host._update_btn.calls
        )

    def test_pump_renders_latest_chunk_only(self) -> None:
        # Many chunk events in one drain → only the latest fraction applied
        # (no 0.1/0.3 churn), so a 170 MB download doesn't poke the widget
        # hundreds of times per tick.
        host = _FakeHost()
        host._pending_update = _make_update_info()
        host._begin_update_progress()
        for done in (10, 30, 70):
            host._event_queue.put(
                ProgressEvent(kind="chunk", total_done=done, total_chunks=100)
            )
        host._pump_update_download()
        set_calls = [c["set"] for c in host._update_progress.calls if "set" in c]
        assert 0.7 in set_calls
        assert 0.1 not in set_calls
        assert 0.3 not in set_calls

    def test_pump_failure_hides_banner_progress(self) -> None:
        host = _FakeHost()
        host._pending_update = _make_update_info()
        host._begin_update_progress()
        host._event_queue.put(
            ProgressEvent(kind="update_failed", raw_line="boom")
        )
        with patch.dict(
            "sys.modules", {"tkinter": MagicMock(messagebox=MagicMock())}
        ):
            host._pump_update_download()
        assert {"stop": True} in host._update_progress.calls
        assert {"grid_remove": True} in host._update_progress.calls
