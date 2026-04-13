"""Tests for src.cleanup — old install + orphan shortcut detection."""

from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cleanup import (
    OldInstall,
    OrphanShortcut,
    _is_audiobook_install,
    find_old_installs,
    find_orphan_shortcuts,
    remove_old_install,
    remove_orphan_shortcut,
)


# ---------------------------------------------------------------------------
# _is_audiobook_install
# ---------------------------------------------------------------------------


class TestIsAudiobookInstall:
    def test_recognises_pyinstaller_layout(self, tmp_path: Path) -> None:
        (tmp_path / "AudiobookMaker.exe").write_bytes(b"fake")
        (tmp_path / "_internal").mkdir()
        assert _is_audiobook_install(tmp_path) is True

    def test_recognises_old_layout_with_ffmpeg(self, tmp_path: Path) -> None:
        (tmp_path / "AudiobookMaker.exe").write_bytes(b"fake")
        (tmp_path / "ffmpeg.exe").write_bytes(b"fake")
        assert _is_audiobook_install(tmp_path) is True

    def test_rejects_directory_without_exe(self, tmp_path: Path) -> None:
        (tmp_path / "_internal").mkdir()
        assert _is_audiobook_install(tmp_path) is False

    def test_rejects_exe_only_no_internal(self, tmp_path: Path) -> None:
        (tmp_path / "AudiobookMaker.exe").write_bytes(b"fake")
        assert _is_audiobook_install(tmp_path) is False

    def test_rejects_nonexistent_path(self, tmp_path: Path) -> None:
        assert _is_audiobook_install(tmp_path / "nope") is False


# ---------------------------------------------------------------------------
# find_old_installs
# ---------------------------------------------------------------------------


class TestFindOldInstalls:
    def _make_install(self, root: Path, name: str = "Install") -> Path:
        d = root / name
        d.mkdir(parents=True)
        (d / "AudiobookMaker.exe").write_bytes(b"x" * 1024)
        (d / "_internal").mkdir()
        (d / "_internal" / "data.bin").write_bytes(b"x" * 4096)
        return d

    def test_excludes_current_install(self, tmp_path: Path) -> None:
        running = self._make_install(tmp_path, "Running")
        stale = self._make_install(tmp_path, "Stale")

        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[running, stale]):
            results = find_old_installs(current_exe=running / "AudiobookMaker.exe")

        paths = {r.path for r in results}
        assert stale.resolve() in paths
        assert running.resolve() not in paths

    def test_finds_uninstaller(self, tmp_path: Path) -> None:
        stale = self._make_install(tmp_path, "Stale")
        (stale / "unins000.exe").write_bytes(b"fake")

        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[stale]):
            results = find_old_installs(current_exe=tmp_path / "OtherApp" / "x.exe")

        assert len(results) == 1
        assert results[0].has_uninstaller is True

    def test_no_uninstaller(self, tmp_path: Path) -> None:
        stale = self._make_install(tmp_path, "Stale")
        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[stale]):
            results = find_old_installs(current_exe=tmp_path / "OtherApp" / "x.exe")
        assert results[0].has_uninstaller is False

    def test_reports_size(self, tmp_path: Path) -> None:
        stale = self._make_install(tmp_path, "Stale")
        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[stale]):
            results = find_old_installs(current_exe=tmp_path / "OtherApp" / "x.exe")
        assert results[0].size_mb > 0

    def test_skips_non_install_directories(self, tmp_path: Path) -> None:
        empty = tmp_path / "Empty"
        empty.mkdir()
        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[empty]):
            results = find_old_installs(current_exe=tmp_path / "x.exe")
        assert results == []

    def test_deduplicates_same_path(self, tmp_path: Path) -> None:
        stale = self._make_install(tmp_path, "Stale")
        with patch("src.cleanup._candidate_install_dirs",
                   return_value=[stale, stale]):
            results = find_old_installs(current_exe=tmp_path / "x.exe")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Shortcut parsing
# ---------------------------------------------------------------------------


def _build_minimal_lnk(target_path: str) -> bytes:
    """Build a minimal Windows .lnk pointing to target_path.

    Just enough structure for _read_shortcut_target to parse — a real
    Shell Link header (0x4C bytes) with HasLinkInfo flag, followed by
    a LinkInfo section with LocalBasePath = target.
    """
    # Header: 0x4C 0x00 0x00 0x00 + GUID + flags + ... (mostly zeros)
    header = bytearray(0x4C)
    header[0:4] = b"\x4c\x00\x00\x00"  # HeaderSize
    header[20:24] = struct.pack("<I", 0x02)  # flags = HAS_LINK_INFO

    # LinkInfo: size + header_size + flags + offsets + path (ANSI null-terminated)
    target_bytes = target_path.encode("mbcs", errors="replace") + b"\x00"
    # Layout: [size:4][header_size:4][flags:4][volume_id_offset:4]
    #         [local_base_path_offset:4][common_network_offset:4]
    #         [common_path_suffix_offset:4][...path data...]
    li_header_size = 28
    local_base_path_offset = li_header_size
    common_path_suffix_offset = local_base_path_offset + len(target_bytes)
    # The terminating empty suffix is just one null byte
    suffix = b"\x00"
    li_size = common_path_suffix_offset + len(suffix)

    li = bytearray(li_size)
    li[0:4] = struct.pack("<I", li_size)
    li[4:8] = struct.pack("<I", li_header_size)
    li[8:12] = struct.pack("<I", 0x01)  # VolumeIDAndLocalBasePath
    li[16:20] = struct.pack("<I", local_base_path_offset)
    li[24:28] = struct.pack("<I", common_path_suffix_offset)
    li[local_base_path_offset:local_base_path_offset + len(target_bytes)] = target_bytes
    li[common_path_suffix_offset:common_path_suffix_offset + len(suffix)] = suffix

    return bytes(header) + bytes(li)


class TestFindOrphanShortcuts:
    def test_finds_shortcut_pointing_to_missing_exe(self, tmp_path: Path) -> None:
        shortcut_dir = tmp_path / "shortcuts"
        shortcut_dir.mkdir()
        lnk = shortcut_dir / "AudiobookMaker.lnk"
        missing_target = str(tmp_path / "DoesNotExist" / "AudiobookMaker.exe")
        lnk.write_bytes(_build_minimal_lnk(missing_target))

        with patch("src.cleanup._candidate_shortcut_dirs",
                   return_value=[shortcut_dir]):
            results = find_orphan_shortcuts()

        assert len(results) == 1
        assert results[0].shortcut_path == lnk
        assert "DoesNotExist" in results[0].target_path

    def test_skips_shortcut_with_existing_target(self, tmp_path: Path) -> None:
        shortcut_dir = tmp_path / "shortcuts"
        shortcut_dir.mkdir()
        # Create the target file so it exists
        target_dir = tmp_path / "Real"
        target_dir.mkdir()
        target = target_dir / "AudiobookMaker.exe"
        target.write_bytes(b"fake")

        lnk = shortcut_dir / "AudiobookMaker.lnk"
        lnk.write_bytes(_build_minimal_lnk(str(target)))

        with patch("src.cleanup._candidate_shortcut_dirs",
                   return_value=[shortcut_dir]):
            results = find_orphan_shortcuts()

        assert results == []

    def test_handles_corrupt_lnk_gracefully(self, tmp_path: Path) -> None:
        shortcut_dir = tmp_path / "shortcuts"
        shortcut_dir.mkdir()
        bad = shortcut_dir / "AudiobookMaker.lnk"
        bad.write_bytes(b"not a real lnk file")

        with patch("src.cleanup._candidate_shortcut_dirs",
                   return_value=[shortcut_dir]):
            results = find_orphan_shortcuts()

        # Should not crash; corrupt files are silently skipped
        assert results == []

    def test_ignores_non_audiobook_shortcuts(self, tmp_path: Path) -> None:
        shortcut_dir = tmp_path / "shortcuts"
        shortcut_dir.mkdir()
        # A shortcut with a different name should be ignored
        (shortcut_dir / "OtherApp.lnk").write_bytes(
            _build_minimal_lnk("C:/missing.exe")
        )
        with patch("src.cleanup._candidate_shortcut_dirs",
                   return_value=[shortcut_dir]):
            results = find_orphan_shortcuts()
        assert results == []


# ---------------------------------------------------------------------------
# Cleanup actions
# ---------------------------------------------------------------------------


class TestRemoveOldInstall:
    def test_rmtree_when_no_uninstaller(self, tmp_path: Path) -> None:
        d = tmp_path / "Stale"
        d.mkdir()
        (d / "AudiobookMaker.exe").write_bytes(b"x")
        (d / "_internal").mkdir()
        (d / "_internal" / "x.bin").write_bytes(b"x")

        install = OldInstall(
            path=d, exe_path=d / "AudiobookMaker.exe",
            has_uninstaller=False, size_mb=1.0,
        )
        ok, _msg = remove_old_install(install)
        assert ok is True
        assert not d.exists()

    def test_handles_already_removed(self, tmp_path: Path) -> None:
        d = tmp_path / "Gone"
        install = OldInstall(
            path=d, exe_path=d / "AudiobookMaker.exe",
            has_uninstaller=False, size_mb=0.0,
        )
        # Should not raise; rmtree on missing path is fine
        ok, _msg = remove_old_install(install)
        # Removing a non-existent dir is "successful" (idempotent)
        assert ok is True


class TestRemoveOrphanShortcut:
    def test_deletes_lnk_file(self, tmp_path: Path) -> None:
        lnk = tmp_path / "AudiobookMaker.lnk"
        lnk.write_bytes(b"fake")
        short = OrphanShortcut(shortcut_path=lnk, target_path="C:/missing.exe")
        ok, _msg = remove_orphan_shortcut(short)
        assert ok is True
        assert not lnk.exists()

    def test_returns_false_on_missing(self, tmp_path: Path) -> None:
        lnk = tmp_path / "Missing.lnk"
        short = OrphanShortcut(shortcut_path=lnk, target_path="C:/missing.exe")
        ok, _msg = remove_orphan_shortcut(short)
        assert ok is False
