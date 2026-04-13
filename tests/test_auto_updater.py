"""Tests for auto_updater module."""

from __future__ import annotations

from src.auto_updater import _extract_sha256, UpdateInfo


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
