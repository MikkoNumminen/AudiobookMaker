"""Tests for auto_updater module."""

from __future__ import annotations

import hashlib
import json
import threading
from io import BytesIO
from unittest.mock import MagicMock, patch

from src.auto_updater import (
    UpdateInfo,
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

        update = self._make_update_info()

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            dest = download_update(update)
            assert dest.exists()
            assert dest.read_bytes() == content
            assert dest.name == "AudiobookMaker-Setup-3.0.0.exe"
            # cleanup
            dest.unlink(missing_ok=True)

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
        update = self._make_update_info()

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
        update = self._make_update_info()

        with patch("src.auto_updater.UPDATE_DIR", tmp_path):
            import pytest

            with pytest.raises(RuntimeError, match="Download failed"):
                download_update(update)

            expected_path = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
            assert not expected_path.exists()


# ---------------------------------------------------------------------------
# Pending update marker — self-heal for failed silent installs
# ---------------------------------------------------------------------------


class TestPendingMarker:
    """Tests for the marker file that lets the app detect a failed silent update."""

    def _patch_marker(self, tmp_path):
        """Context helper that redirects PENDING_MARKER to tmp_path."""
        from pathlib import Path
        marker = tmp_path / "marker.json"
        return patch("src.auto_updater.PENDING_MARKER", marker), marker

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
