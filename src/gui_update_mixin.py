"""Auto-update mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from src.auto_updater import UpdateInfo, download_update, apply_update
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
        _update_progress: Any  # banner-local download bar
        _progress_bar: Any

        POLL_INTERVAL_MS: int

        def _s(self, key: str) -> str: ...
        def after(self, ms: int, func: Optional[Callable[..., Any]] = ...) -> str: ...

    _Base = _UpdateHost
else:
    _Base = object


class UpdateMixin(_Base):
    """Mixin providing auto-update download + install UI logic.

    The update check / poll / banner live on the host (``UnifiedApp``); this
    mixin owns only the click -> download -> verify -> apply hand-off. (An
    earlier check/poll copy lived here too and was silently shadowed by the
    host's — it has been removed so a fix can't land on a dead copy.)

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

    # Whether the banner progress bar has switched from its initial
    # indeterminate animation to a real percentage bar. Class-level default
    # so the helpers are safe even if a caller skips _begin_update_progress.
    _update_progress_determinate: bool = False

    def _on_update_click(self) -> None:
        """User clicked the update button — download and install."""
        if self._pending_update is None:
            return

        self._update_btn.configure(
            state="disabled",
            text=self._s("update_downloading"),
        )
        # Show motion immediately, in the banner the user is looking at.
        # The synthesis progress bar lives elsewhere in the window; driving
        # only that made a real download look like a frozen, dead button.
        self._begin_update_progress()

        threading.Thread(
            target=self._download_update_worker, daemon=True,
            name="update-download",
        ).start()
        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)

    # ------------------------------------------------------------------
    # Banner-local progress indicator
    # ------------------------------------------------------------------

    def _begin_update_progress(self) -> None:
        """Reveal the banner progress bar and start it animating.

        Starts in *indeterminate* mode (an animated barber-pole) so the
        banner shows activity the instant the click lands — before the
        first byte, and even if the server never reports a content length.
        ``_render_update_progress`` swaps it to a real percentage bar as
        soon as a sized chunk arrives.
        """
        self._update_progress_determinate = False
        self._update_progress.configure(mode="indeterminate")
        self._update_progress.grid()
        self._update_progress.start()

    def _render_update_progress(self, done: int, total: int) -> None:
        """Show real download progress (``done``/``total`` bytes)."""
        if total <= 0:
            return
        if not self._update_progress_determinate:
            # First sized measurement — leave the animation for a true bar.
            self._update_progress.stop()
            self._update_progress.configure(mode="determinate")
            self._update_progress_determinate = True
        fraction = max(0.0, min(1.0, done / total))
        self._update_progress.set(fraction)
        if done >= total:
            # The bytes are in; download_update still has to SHA-256 the
            # ~170 MB file before it returns, so say so rather than sit at
            # a silent 100%.
            self._update_btn.configure(text=self._s("update_verifying"))
        else:
            self._update_btn.configure(
                text=self._s("update_downloading_pct").format(
                    pct=int(fraction * 100)
                )
            )

    def _end_update_progress(self) -> None:
        """Stop and hide the banner progress bar (download ended/failed)."""
        self._update_progress.stop()
        self._update_progress.set(0)
        self._update_progress.grid_remove()
        self._update_progress_determinate = False

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
            # Auto-update is P0: capture the full traceback (SHA mismatch, 404,
            # network blip) in the diagnostic log so a failed update is in the
            # file the user sends, not just the transient error dialog.
            logger.exception("Update download/verify failed")
            self._event_queue.put(
                ProgressEvent(kind="update_failed", raw_line=str(exc))
            )

    def _pump_update_download(self) -> None:
        """Tk main-thread pump for update download progress.

        Drains the whole event queue per tick and applies at most one
        progress render, so a 170 MB download's hundreds of 256 KB chunk
        events don't each poke the widget. A terminal event (done/failed)
        in the same drain wins over the intermediate chunks.
        """
        latest_chunk: Optional[tuple[int, int]] = None
        done_path: Optional[str] = None
        failure: Optional[str] = None

        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    latest_chunk = (ev.total_done, ev.total_chunks)
            elif ev.kind == "update_done":
                done_path = ev.raw_line
            elif ev.kind == "update_failed":
                failure = ev.raw_line

        if failure is not None:
            self._end_update_progress()
            self._update_btn.configure(
                state="normal",
                text=self._s("update_now"),
            )
            # Clear the pending-update handle so the banner doesn't keep
            # pointing at a release whose installer we couldn't fetch (bad
            # SHA, 404, network blip). The next scheduled update check will
            # re-populate if the release is still fine — this just prevents
            # a stale "update available" handle from silently re-triggering
            # a broken download.
            self._pending_update = None
            # Hide the banner too: with no pending handle the button is now a
            # no-op, so leaving the banner up presents a dead "Update now". The
            # periodic check re-shows it if the failure was transient.
            self._update_banner.grid_remove()
            from tkinter import messagebox
            messagebox.showerror(
                self._s("error"),
                self._s("update_error_detail").format(error=failure),
            )
            return

        if done_path is not None:
            self._update_progress.set(1.0)
            self._update_btn.configure(text=self._s("update_installing"))
            installer_path = Path(done_path)
            expected = (
                self._pending_update.latest_version
                if self._pending_update else ""
            )
            self.after(
                200,
                lambda: self._apply_update_and_recover(installer_path, expected),
            )
            return

        if latest_chunk is not None:
            self._render_update_progress(latest_chunk[0], latest_chunk[1])

        self.after(self.POLL_INTERVAL_MS, self._pump_update_download)

    def _apply_update_and_recover(self, installer_path: Path, expected: str) -> None:
        """Hand off to the installer; recover the banner if the launch fails.

        ``apply_update`` normally never returns — it spawns the installer via a
        detached helper script and ``os._exit``'s this process. If it raises
        (or returns) instead, the hand-off failed; without this the banner
        would sit frozen on "installing" and read as a hang (field-observed).
        Re-enable the button and surface the error so the user can retry,
        mirroring the download-failure path above.
        """
        try:
            apply_update(installer_path, expected)
        except Exception as exc:  # noqa: BLE001 — any hand-off failure must show
            logger.exception("apply_update failed to launch the installer")
            self._update_btn.configure(state="normal", text=self._s("update_now"))
            self._end_update_progress()
            self._progress_bar.set(0)
            from tkinter import messagebox
            messagebox.showerror(
                self._s("error"),
                self._s("update_error_detail").format(error=str(exc)),
            )
