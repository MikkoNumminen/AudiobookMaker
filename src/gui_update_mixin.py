"""Auto-update mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.auto_updater import UpdateInfo, check_for_update, download_update, apply_update, APP_VERSION
from src.launcher_bridge import ProgressEvent

if TYPE_CHECKING:
    pass


class UpdateMixin:
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

    def _check_update_worker(self) -> None:
        """Background thread: check GitHub for a newer version."""
        try:
            info = check_for_update(APP_VERSION)
            self._update_queue.put(info)
        except Exception:
            pass  # Silently ignore — no banner shown.

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
                self.after(200, lambda: apply_update(installer_path))
                return
            elif ev.kind == "update_failed":
                self._update_btn.configure(
                    state="normal",
                    text=self._s("update_failed"),
                )
                self._progress_bar.set(0)
                return

        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)
