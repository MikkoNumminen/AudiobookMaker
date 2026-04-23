"""Tests for the legacy ``src.gui.App`` temp-sample cleanup path.

The "Test voice" flow in the legacy GUI writes each sample MP3 to a
``tempfile.NamedTemporaryFile(delete=False)``. The external audio player
reads those files asynchronously, so the worker thread can't unlink
immediately after handing off to ``startfile``/``Popen`` — the player
would open nothing. Instead we track every path and sweep them on
window close.

These tests exercise ``_cleanup_temp_samples`` directly, without
instantiating Tk. Creating a full ``App`` under pytest is expensive and
flaky on headless CI; since the cleanup logic is a self-contained
method, we call it as an unbound function bound to a stub instance.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.gui import App


class _StubApp:
    """Minimal instance surface the cleanup method reads."""

    def __init__(self, paths):
        self._temp_sample_paths = list(paths)


class TestCleanupTempSamples:
    def test_unlinks_every_tracked_path(self, tmp_path: Path) -> None:
        # Two real temp files on disk; cleanup must remove both.
        a = tmp_path / "sample_a.mp3"
        b = tmp_path / "sample_b.mp3"
        a.write_bytes(b"fake mp3")
        b.write_bytes(b"fake mp3")
        stub = _StubApp([str(a), str(b)])
        App._cleanup_temp_samples(stub)  # type: ignore[arg-type]
        assert not a.exists()
        assert not b.exists()
        # The tracking list must be emptied so a second close doesn't
        # try to unlink the same paths twice.
        assert stub._temp_sample_paths == []

    def test_missing_file_is_swallowed(self, tmp_path: Path) -> None:
        # If the user already moved/deleted the file, cleanup must not
        # raise — we still want to sweep the remaining paths.
        missing = tmp_path / "already_gone.mp3"
        present = tmp_path / "still_here.mp3"
        present.write_bytes(b"fake mp3")
        stub = _StubApp([str(missing), str(present)])
        App._cleanup_temp_samples(stub)  # type: ignore[arg-type]
        assert not present.exists()
        assert stub._temp_sample_paths == []

    def test_os_error_is_logged_not_raised(self, tmp_path: Path) -> None:
        # Windows can hold the file if the audio player still has an
        # open handle. Cleanup must log the failure and move on — we
        # can't block shutdown over one stuck MP3.
        path = tmp_path / "locked.mp3"
        path.write_bytes(b"fake mp3")
        stub = _StubApp([str(path)])

        def _raise(_p):
            raise PermissionError("file in use")

        with patch("src.gui.os.unlink", side_effect=_raise):
            # Must not raise.
            App._cleanup_temp_samples(stub)  # type: ignore[arg-type]
        # Even on failure the tracking list is cleared — we already
        # tried, there's nothing to retry on the next close.
        assert stub._temp_sample_paths == []

    def test_empty_list_is_noop(self) -> None:
        stub = _StubApp([])
        App._cleanup_temp_samples(stub)  # type: ignore[arg-type]
        assert stub._temp_sample_paths == []


class TestOnClose:
    def test_on_close_sweeps_then_destroys(self) -> None:
        # _on_close calls _cleanup_temp_samples then self.destroy().
        # Both must fire, in that order, even if destroy never returns
        # (a real tearing-down Tk root).
        calls: list[str] = []

        class _Stub:
            _temp_sample_paths = ["whatever"]

            def _cleanup_temp_samples(self):
                calls.append("cleanup")

            def destroy(self):
                calls.append("destroy")

        App._on_close(_Stub())  # type: ignore[arg-type]
        assert calls == ["cleanup", "destroy"]
