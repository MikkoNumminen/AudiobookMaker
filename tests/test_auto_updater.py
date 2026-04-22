"""Tests for auto_updater module."""

from __future__ import annotations

import hashlib
import json
import threading
from io import BytesIO
from unittest.mock import MagicMock, patch

from src.auto_updater import (
    UpdateInfo,
    _assert_bat_safe_path,
    _assert_ps_safe_path,
    _extract_sha256,
    check_for_update,
    clear_pending_marker,
    download_update,
    read_pending_marker,
    verify_pending_update,
    _write_pending_marker,
)


class TestExtractSha256:
    def test_standard_format(self) -> None:
        notes = "Release notes\nSHA-256: abcd1234" + "0" * 56
        result = _extract_sha256(notes)
        assert result == "abcd1234" + "0" * 56

    def test_lowercase_label(self) -> None:
        hash_val = "a" * 64
        result = _extract_sha256(f"sha256: {hash_val}")
        assert result == hash_val

    def test_backtick_wrapped(self) -> None:
        hash_val = "b" * 64
        result = _extract_sha256(f"SHA-256: `{hash_val}`")
        assert result == hash_val

    def test_no_hash_returns_none(self) -> None:
        assert _extract_sha256("Just release notes") is None
        assert _extract_sha256("") is None

    def test_short_hex_not_matched(self) -> None:
        assert _extract_sha256("SHA-256: abcd1234") is None


class TestUpdateInfoSha256Field:
    def test_dataclass_has_sha256_field(self) -> None:
        info = UpdateInfo(
            available=False,
            current_version="1.0.0",
            latest_version="1.0.0",
            download_url="",
            release_notes="",
            asset_size_bytes=0,
            sha256="",
        )
        assert info.sha256 == ""

    def test_sha256_stores_value(self) -> None:
        hash_val = "c" * 64
        info = UpdateInfo(
            available=True,
            current_version="1.0.0",
            latest_version="2.0.0",
            download_url="https://example.com/installer.exe",
            release_notes="notes",
            asset_size_bytes=1024,
            sha256=hash_val,
        )
        assert info.sha256 == hash_val


# ---------------------------------------------------------------------------
# Helpers for mocking
# ---------------------------------------------------------------------------


def _mock_github_response(
    tag="v3.0.0",
    asset_name="AudiobookMaker-Setup-3.0.0.exe",
    body="",
    assets=None,
):
    """Build a mock urllib response that returns a JSON GitHub release payload."""
    data = {
        "tag_name": tag,
        "body": body,
        "assets": assets
        if assets is not None
        else [
            {
                "name": asset_name,
                "browser_download_url": "https://example.com/dl.exe",
                "size": 1000,
            }
        ],
    }
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_download_response(content: bytes):
    """Build a mock urllib response that yields *content* as the download body."""
    buf = BytesIO(content)
    resp = MagicMock()
    resp.read = buf.read
    resp.headers = {"Content-Length": str(len(content))}
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# E2E: check_for_update
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    """End-to-end tests for check_for_update with mocked GitHub API."""

    @patch("src.auto_updater.urlopen")
    def test_newer_version_available(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_github_response(tag="v3.0.0")
        info = check_for_update("2.0.0")

        assert info.available is True
        assert info.latest_version == "3.0.0"
        assert info.current_version == "2.0.0"
        assert info.download_url == "https://example.com/dl.exe"
        assert info.asset_size_bytes == 1000

    @patch("src.auto_updater.urlopen")
    def test_same_version_not_available(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_github_response(tag="v2.0.0")
        info = check_for_update("2.0.0")

        assert info.available is False

    @patch("src.auto_updater.urlopen")
    def test_older_version_not_available(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_github_response(tag="v1.0.0")
        info = check_for_update("2.0.0")

        assert info.available is False

    @patch("src.auto_updater.urlopen")
    def test_network_error_returns_not_available(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("network down")
        info = check_for_update("2.0.0")

        assert info.available is False
        assert info.current_version == "2.0.0"

    @patch("src.auto_updater.urlopen")
    def test_no_exe_asset_returns_not_available(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_github_response(
            tag="v3.0.0",
            assets=[
                {
                    "name": "source.tar.gz",
                    "browser_download_url": "https://example.com/src.tar.gz",
                    "size": 500,
                }
            ],
        )
        info = check_for_update("2.0.0")

        assert info.available is False

    @patch("src.auto_updater.urlopen")
    def test_sha256_extracted_from_release_notes(self, mock_urlopen: MagicMock) -> None:
        sha = "a" * 64
        mock_urlopen.return_value = _mock_github_response(
            tag="v3.0.0", body=f"Release notes\nSHA-256: {sha}"
        )
        info = check_for_update("2.0.0")

        assert info.available is True
        assert info.sha256 == sha


class TestSidecarSha256Fallback:
    """When the release body lacks a SHA-256 line, fall back to the
    `.exe.sha256` sidecar asset uploaded by the release pipeline."""

    @patch("src.auto_updater.urlopen")
    def test_sidecar_used_when_body_lacks_sha(
        self, mock_urlopen: MagicMock
    ) -> None:
        sha = "b" * 64
        api_response = _mock_github_response(
            tag="v3.0.0",
            body="Release notes with no SHA line",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe.sha256",
                    "browser_download_url": (
                        "https://example.com/dl.exe.sha256"
                    ),
                    "size": 80,
                },
            ],
        )
        sidecar_response = _mock_download_response(
            f"{sha}  AudiobookMaker-Setup-3.0.0.exe\n".encode()
        )
        mock_urlopen.side_effect = [api_response, sidecar_response]

        info = check_for_update("2.0.0")

        assert info.available is True
        assert info.sha256 == sha

    @patch("src.auto_updater.urlopen")
    def test_body_sha_preferred_over_sidecar(
        self, mock_urlopen: MagicMock
    ) -> None:
        body_sha = "c" * 64
        api_response = _mock_github_response(
            tag="v3.0.0",
            body=f"SHA-256: {body_sha}",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe.sha256",
                    "browser_download_url": (
                        "https://example.com/dl.exe.sha256"
                    ),
                    "size": 80,
                },
            ],
        )
        # If the body parses successfully, we should never even fetch the
        # sidecar. Only one urlopen call should happen.
        mock_urlopen.return_value = api_response

        info = check_for_update("2.0.0")

        assert info.sha256 == body_sha
        assert mock_urlopen.call_count == 1

    @patch("src.auto_updater.urlopen")
    def test_no_sidecar_present_returns_empty_sha(
        self, mock_urlopen: MagicMock
    ) -> None:
        mock_urlopen.return_value = _mock_github_response(
            tag="v3.0.0",
            body="No SHA in body",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
            ],
        )

        info = check_for_update("2.0.0")

        # available=True (the .exe exists), but sha256 empty so the
        # downloader will refuse with the friendly browser-fallback message.
        assert info.available is True
        assert info.sha256 == ""

    @patch("src.auto_updater.urlopen")
    def test_sidecar_with_unparseable_payload_returns_empty(
        self, mock_urlopen: MagicMock
    ) -> None:
        api_response = _mock_github_response(
            tag="v3.0.0",
            body="No SHA in body",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe.sha256",
                    "browser_download_url": (
                        "https://example.com/dl.exe.sha256"
                    ),
                    "size": 80,
                },
            ],
        )
        garbage = _mock_download_response(b"<html>404 Not Found</html>")
        mock_urlopen.side_effect = [api_response, garbage]

        info = check_for_update("2.0.0")

        assert info.available is True
        assert info.sha256 == ""

    @patch("src.auto_updater.urlopen")
    def test_sidecar_network_error_returns_empty(
        self, mock_urlopen: MagicMock
    ) -> None:
        from urllib.error import URLError

        api_response = _mock_github_response(
            tag="v3.0.0",
            body="No SHA in body",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe.sha256",
                    "browser_download_url": (
                        "https://example.com/dl.exe.sha256"
                    ),
                    "size": 80,
                },
            ],
        )
        mock_urlopen.side_effect = [api_response, URLError("sidecar 404")]

        info = check_for_update("2.0.0")

        assert info.available is True
        assert info.sha256 == ""

    @patch("src.auto_updater.urlopen")
    def test_sidecar_fetch_uses_bounded_timeout(
        self, mock_urlopen: MagicMock
    ) -> None:
        """A hanging sidecar download must not stall the update flow.

        We only assert that the second urlopen call (the sidecar fetch)
        passes a ``timeout`` keyword — the exact value is a tuning knob.
        """
        sha = "e" * 64
        api_response = _mock_github_response(
            tag="v3.0.0",
            body="No SHA in body",
            assets=[
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe",
                    "browser_download_url": "https://example.com/dl.exe",
                    "size": 1000,
                },
                {
                    "name": "AudiobookMaker-Setup-3.0.0.exe.sha256",
                    "browser_download_url": (
                        "https://example.com/dl.exe.sha256"
                    ),
                    "size": 80,
                },
            ],
        )
        sidecar_response = _mock_download_response(
            f"{sha}  AudiobookMaker-Setup-3.0.0.exe\n".encode()
        )
        mock_urlopen.side_effect = [api_response, sidecar_response]

        check_for_update("2.0.0")

        # Both calls must have a timeout set; otherwise a slow endpoint
        # could hang the whole update pipeline.
        assert mock_urlopen.call_count == 2
        for call in mock_urlopen.call_args_list:
            timeout = call.kwargs.get("timeout")
            assert timeout is not None and timeout > 0


# ---------------------------------------------------------------------------
# E2E: download_update
# ---------------------------------------------------------------------------


class TestDownloadUpdate:
    """End-to-end tests for download_update with mocked urlopen."""

    def _make_update_info(self, sha256: str = "") -> UpdateInfo:
        return UpdateInfo(
            available=True,
            current_version="2.0.0",
            latest_version="3.0.0",
            download_url="https://example.com/dl.exe",
            release_notes="",
            asset_size_bytes=1000,
            sha256=sha256,
        )

    @patch("src.auto_updater.urlopen")
    def test_download_writes_file(self, mock_urlopen: MagicMock, tmp_path) -> None:
        content = b"fake-installer-bytes"
        mock_urlopen.return_value = _mock_download_response(content)

        update = self._make_update_info(sha256=hashlib.sha256(content).hexdigest())

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            dest = download_update(update)
            assert dest.exists()
            assert dest.read_bytes() == content
            assert dest.name == "AudiobookMaker-Setup-3.0.0.exe"
            # cleanup
            dest.unlink(missing_ok=True)

    @patch("src.auto_updater.urlopen")
    def test_missing_sha256_raises_before_download(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        """Without a published SHA-256 we must refuse to download at all."""
        mock_urlopen.return_value = _mock_download_response(b"anything")
        update = self._make_update_info(sha256="")

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            import pytest

            with pytest.raises(RuntimeError, match="No SHA-256"):
                download_update(update)

            # Network must not have been hit and no file should exist.
            mock_urlopen.assert_not_called()
            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()

    @patch("src.auto_updater.urlopen")
    def test_sha256_mismatch_raises_and_deletes(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        content = b"fake-installer-bytes"
        wrong_sha = "b" * 64
        mock_urlopen.return_value = _mock_download_response(content)

        update = self._make_update_info(sha256=wrong_sha)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            import pytest

            with pytest.raises(RuntimeError, match="Integrity check failed"):
                download_update(update)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()

    @patch("src.auto_updater.urlopen")
    def test_sha256_match_succeeds(self, mock_urlopen: MagicMock, tmp_path) -> None:
        content = b"fake-installer-bytes"
        correct_sha = hashlib.sha256(content).hexdigest()
        mock_urlopen.return_value = _mock_download_response(content)

        update = self._make_update_info(sha256=correct_sha)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            dest = download_update(update)
            assert dest.exists()
            assert dest.read_bytes() == content
            dest.unlink(missing_ok=True)

    @patch("src.auto_updater.urlopen")
    def test_cancel_event_stops_download(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        cancel = threading.Event()
        cancel.set()  # pre-cancelled

        mock_urlopen.return_value = _mock_download_response(b"x" * 10000)
        update = self._make_update_info(sha256="d" * 64)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            import pytest

            with pytest.raises(RuntimeError, match="cancelled"):
                download_update(update, cancel_event=cancel)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()

    @patch("src.auto_updater.urlopen")
    def test_network_error_raises_and_cleans_up(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("connection reset")
        update = self._make_update_info(sha256="e" * 64)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            import pytest

            with pytest.raises(RuntimeError, match="Download failed"):
                download_update(update)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()

    @patch("src.auto_updater.urlopen")
    def test_keyboard_interrupt_cleans_up_partial_file(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        """A KeyboardInterrupt mid-download must not leave a partial .exe.

        If we left it behind, a subsequent retry path (or worse, a naive
        launcher that sees the file) could execute a truncated installer.
        """
        import pytest

        class _KeyboardKillingResp:
            headers = {"Content-Length": "1000"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __init__(self):
                self._calls = 0

            def read(self, _size):
                self._calls += 1
                if self._calls == 1:
                    return b"x" * 256
                raise KeyboardInterrupt()

        mock_urlopen.return_value = _KeyboardKillingResp()
        update = self._make_update_info(sha256="f" * 64)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            with pytest.raises(KeyboardInterrupt):
                download_update(update)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists(), (
                "Partial .exe leaked after KeyboardInterrupt"
            )

    @patch("src.auto_updater.urlopen")
    def test_system_exit_cleans_up_partial_file(
        self, mock_urlopen: MagicMock, tmp_path
    ) -> None:
        """SystemExit (e.g. thread abort) must still delete the partial file."""
        import pytest

        class _SystemExitResp:
            headers = {"Content-Length": "1000"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __init__(self):
                self._calls = 0

            def read(self, _size):
                self._calls += 1
                if self._calls == 1:
                    return b"y" * 256
                raise SystemExit(1)

        mock_urlopen.return_value = _SystemExitResp()
        update = self._make_update_info(sha256="a" * 64)

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            with pytest.raises(SystemExit):
                download_update(update)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()


# ---------------------------------------------------------------------------
# Pending update marker — self-heal for failed silent installs
# ---------------------------------------------------------------------------


class TestPendingMarker:
    """Tests for the marker file that lets the app detect a failed silent update."""

    def _patch_marker(self, tmp_path):
        """Context helper that redirects PENDING_MARKER to tmp_path.

        Also stubs out the legacy-path migration so tests can't accidentally
        pick up a real stale marker sitting in the developer's %TEMP%.
        """
        from contextlib import ExitStack
        from pathlib import Path
        marker = tmp_path / "marker.json"
        legacy = tmp_path / "legacy_marker.json"  # does not exist
        stack = ExitStack()
        stack.enter_context(patch("src.auto_updater.PENDING_MARKER", marker))
        stack.enter_context(
            patch("src.auto_updater._LEGACY_PENDING_MARKER", legacy)
        )
        return stack, marker

    def test_write_and_read_roundtrip(self, tmp_path) -> None:
        from pathlib import Path
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            _write_pending_marker("3.0.0", Path("/fake/installer.exe"))
            result = read_pending_marker()
            assert result is not None
            assert result["expected_version"] == "3.0.0"
            assert "installer.exe" in result["installer_path"]

    def test_read_returns_none_when_missing(self, tmp_path) -> None:
        ctx, _ = self._patch_marker(tmp_path)
        with ctx:
            assert read_pending_marker() is None

    def test_clear_removes_marker(self, tmp_path) -> None:
        from pathlib import Path
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            _write_pending_marker("3.0.0", Path("/fake/installer.exe"))
            assert marker.exists()
            clear_pending_marker()
            assert not marker.exists()

    def test_verify_success_when_version_matches(self, tmp_path) -> None:
        """When running version >= expected, marker is cleared."""
        from pathlib import Path
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            _write_pending_marker("3.0.0", Path("/fake/installer.exe"))
            result = verify_pending_update("3.0.0")
            assert result is None
            assert not marker.exists()

    def test_verify_returns_marker_when_version_still_old(self, tmp_path) -> None:
        """When running version < expected, the silent install failed → return marker."""
        from pathlib import Path
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            _write_pending_marker("3.0.0", Path("/fake/installer.exe"))
            result = verify_pending_update("2.9.0")
            assert result is not None
            assert result["expected_version"] == "3.0.0"
            assert marker.exists()  # kept for the GUI to handle

    def test_verify_ignores_stale_marker_older_than_24h(self, tmp_path) -> None:
        import json
        import time
        from pathlib import Path
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            marker.write_text(json.dumps({
                "expected_version": "3.0.0",
                "installer_path": "/fake/installer.exe",
                "started_at": time.time() - 25 * 3600,  # > 24h ago
            }))
            result = verify_pending_update("2.9.0")
            assert result is None
            assert not marker.exists()

    def test_verify_returns_none_when_no_marker(self, tmp_path) -> None:
        ctx, _ = self._patch_marker(tmp_path)
        with ctx:
            assert verify_pending_update("2.0.0") is None

    def test_verify_handles_corrupt_marker(self, tmp_path) -> None:
        ctx, marker = self._patch_marker(tmp_path)
        with ctx:
            marker.write_text("not json")
            # Should return None (treats corrupt as "no marker")
            assert verify_pending_update("2.0.0") is None

    def test_legacy_marker_migrated_on_read(self, tmp_path) -> None:
        """A marker in the old system-temp location is honored once, then removed."""
        import json
        new_marker = tmp_path / "marker.json"
        legacy_marker = tmp_path / "legacy_marker.json"
        legacy_marker.write_text(json.dumps({
            "expected_version": "3.0.0",
            "installer_path": "/fake/installer.exe",
            "started_at": 0,
        }))
        with patch("src.auto_updater.PENDING_MARKER", new_marker), \
             patch("src.auto_updater._LEGACY_PENDING_MARKER", legacy_marker):
            result = read_pending_marker()
            assert result is not None
            assert result["expected_version"] == "3.0.0"
            # Legacy file removed after the one-time read.
            assert not legacy_marker.exists()
            # Subsequent read finds nothing.
            assert read_pending_marker() is None

    def test_write_creates_parent_dir(self, tmp_path) -> None:
        """write_pending_marker creates ~/.audiobookmaker/ if it doesn't exist."""
        from pathlib import Path
        new_dir = tmp_path / "subdir" / "nested"
        new_marker = new_dir / "marker.json"
        legacy = tmp_path / "legacy.json"
        with patch("src.auto_updater.PENDING_MARKER", new_marker), \
             patch("src.auto_updater._LEGACY_PENDING_MARKER", legacy):
            assert not new_dir.exists()
            _write_pending_marker("3.0.0", Path("/fake/installer.exe"))
            assert new_marker.exists()


# ---------------------------------------------------------------------------
# Batch-metacharacter guard for apply_update's relaunch .bat
# ---------------------------------------------------------------------------


class TestAssertBatSafePath:
    """_assert_bat_safe_path refuses paths that would corrupt the relaunch .bat."""

    def test_safe_windows_path_does_not_raise(self) -> None:
        from pathlib import Path
        # Representative of Path.home()/tempfile.gettempdir() output.
        _assert_bat_safe_path(
            Path("C:/Users/alice/Downloads/installer.exe"), "installer_path"
        )

    def test_safe_path_with_spaces_does_not_raise(self) -> None:
        from pathlib import Path
        # Spaces are fine — the .bat quotes every substitution.
        _assert_bat_safe_path(
            Path("C:/Users/Alice Smith/AppData/Local/Temp/x.exe"),
            "installer_path",
        )

    def test_double_quote_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="batch-unsafe"):
            _assert_bat_safe_path(
                Path('C:/evil"path/installer.exe'), "installer_path"
            )

    def test_percent_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="batch-unsafe"):
            _assert_bat_safe_path(
                Path("C:/%USERPROFILE%/installer.exe"), "installer_path"
            )

    def test_caret_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="batch-unsafe"):
            _assert_bat_safe_path(
                Path("C:/weird^path/installer.exe"), "installer_path"
            )

    def test_ampersand_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="batch-unsafe"):
            _assert_bat_safe_path(
                Path("C:/a&b/installer.exe"), "installer_path"
            )

    def test_newline_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="batch-unsafe"):
            _assert_bat_safe_path(
                Path("C:/a\nb/installer.exe"), "installer_path"
            )

    def test_error_message_includes_label(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="installer_path"):
            _assert_bat_safe_path(
                Path('C:/bad"path.exe'), "installer_path"
            )


class TestAssertPsSafePath:
    """_assert_ps_safe_path refuses paths that would corrupt the splash .ps1."""

    def test_safe_windows_path_does_not_raise(self) -> None:
        from pathlib import Path
        _assert_ps_safe_path(
            Path("C:/Program Files/AudiobookMaker/_internal/assets/icon.png"),
            "icon_png",
        )

    def test_safe_path_with_spaces_does_not_raise(self) -> None:
        from pathlib import Path
        # Spaces are fine — the .ps1 wraps the path in double-quotes.
        _assert_ps_safe_path(
            Path("C:/Users/Alice Smith/AppData/Local/AudiobookMaker/assets/icon.png"),
            "icon_png",
        )

    def test_double_quote_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="PowerShell-unsafe"):
            _assert_ps_safe_path(
                Path('C:/evil"path/icon.png'), "icon_png"
            )

    def test_backtick_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="PowerShell-unsafe"):
            _assert_ps_safe_path(
                Path("C:/weird`path/icon.png"), "icon_png"
            )

    def test_dollar_raises(self) -> None:
        """`$` starts PowerShell variable interpolation — must be rejected."""
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="PowerShell-unsafe"):
            _assert_ps_safe_path(
                Path("C:/$env:TEMP/icon.png"), "icon_png"
            )

    def test_newline_raises(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="PowerShell-unsafe"):
            _assert_ps_safe_path(
                Path("C:/a\nb/icon.png"), "icon_png"
            )

    def test_error_message_includes_label(self) -> None:
        import pytest
        from pathlib import Path
        with pytest.raises(ValueError, match="icon_png"):
            _assert_ps_safe_path(
                Path('C:/bad"path.png'), "icon_png"
            )
