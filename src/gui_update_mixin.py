"""Auto-update mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from src.auto_updater import UpdateInfo, check_for_update, download_update, apply_update, APP_VERSION
from src.launcher_bridge import ProgressEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    class _UpdateHost(Protocol):
        """Static contract describing host attributes UpdateMixin reads/writes."""

        _update_queue: "queue.Queue[UpdateInfo]"
        _pending_update: Optional[UpdateInfo]
        _event_queue: "queue.Queue[ProgressEvent]"

        # Widgets (typed as Any to avoid heavy stub deps)
        _update_label: Any
        _update_btn: Any
        _update_banner: Any
        _progress_bar: Any

        POLL_INTERVAL_MS: int

        def _s(self, key: str) -> str: ...
        def after(self, ms: int, func: Optional[Callable[..., Any]] = ...) -> str: ...

    _Base = _UpdateHost
else:
    _Base = object


class UpdateMixin(_Base):
    """Mixin providing auto-update check, download, and install UI logic.

    Expects the host class to provide:
    - self._update_queue: queue.Queue[UpdateInfo]
    - self._pending_update: Optional[UpdateInfo]
    - self._event_queue: queue.Queue[ProgressEvent]
    - self._update_label, self._update_btn, self._update_banner (CTk widgets)
    - self._progress_bar (CTk widget)
    - self._s(key) -> str  (i18n helper)
    - self.after(ms, callback)  (Tk scheduling)
    - self.POLL_INTERVAL_MS: int
    """

    # How often to re-check for updates after a "no update" result (4 hours).
    _UPDATE_RECHECK_MS = 4 * 60 * 60 * 1000

    def _check_update_worker(self) -> None:
        """Background thread: check GitHub for a newer version."""
        try:
            info = check_for_update(APP_VERSION)
            self._update_queue.put(info)
        except Exception:
            # Keep the no-banner behaviour — we don't want to scare the
            # user with a popup just because GitHub was unreachable — but
            # log the traceback so flaky auto-update checks show up in
            # diagnostics instead of disappearing silently.
            logger.debug("Update check failed", exc_info=True)

    def _poll_update_check(self) -> None:
        """Tk main-thread poller: pick up the update-check result."""
        try:
            info = self._update_queue.get_nowait()
        except queue.Empty:
            self.after(500, self._poll_update_check)
            return

        if info.available:
            self._pending_update = info
            self._update_label.configure(
                text=self._s("update_available").format(
                    version=info.latest_version
                )
            )
            self._update_btn.configure(text=self._s("update_now"))
            self._update_banner.grid()
        else:
            # No update now — re-check after a delay.
            self.after(self._UPDATE_RECHECK_MS, self._schedule_update_recheck)

    def _schedule_update_recheck(self) -> None:
        """Launch another background update check."""
        import threading
        threading.Thread(
            target=self._check_update_worker, daemon=True,
            name="update-recheck",
        ).start()
        self.after(500, self._poll_update_check)

    def _on_update_click(self) -> None:
        """User clicked the update button — download and install."""
        if self._pending_update is None:
            return

        self._update_btn.configure(
            state="disabled",
            text=self._s("update_downloading"),
        )

        threading.Thread(
            target=self._download_update_worker, daemon=True,
            name="update-download",
        ).start()
        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)

    def _download_update_worker(self) -> None:
        """Background thread: download the installer."""
        assert self._pending_update is not None
        try:
            def progress_cb(done: int, total: int) -> None:
                if total > 0:
                    self._event_queue.put(
                        ProgressEvent(
                            kind="chunk",
                            total_done=done,
                            total_chunks=total,
                            raw_line=self._s("update_downloading"),
                        )
                    )

            installer_path = download_update(self._pending_update, progress_cb)
            self._event_queue.put(
                ProgressEvent(
                    kind="update_done",
                    raw_line=str(installer_path),
                )
            )
        except Exception as exc:
            self._event_queue.put(
                ProgressEvent(kind="update_failed", raw_line=str(exc))
            )

    def _pump_update_download(self) -> None:
        """Tk main-thread pump for update download progress."""
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    self._progress_bar.set(ev.total_done / ev.total_chunks)
            elif ev.kind == "update_done":
                self._progress_bar.set(1.0)
                self._update_btn.configure(text=self._s("update_installing"))
                installer_path = Path(ev.raw_line)
                expected = (
                    self._pending_update.latest_version
                    if self._pending_update else ""
                )
                self.after(200, lambda: apply_update(installer_path, expected))
                return
            elif ev.kind == "update_failed":
                self._update_btn.configure(
                    state="normal",
                    text=self._s("update_now"),
                )
                self._progress_bar.set(0)
                from tkinter import messagebox
                messagebox.showerror(
                    self._s("error"),
                    self._s("update_error_detail").format(error=ev.raw_line),
                )
                return

        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)
