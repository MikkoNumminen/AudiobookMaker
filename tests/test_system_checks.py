"""Unit tests for src.system_checks module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.system_checks import (
    DiskInfo,
    GpuInfo,
    PythonInfo,
    SystemReport,
    check_disk_space,
    check_output_disk_space,
    estimate_synthesis_size_mb,
    detect_gpu,
    find_python311,
    run_full_check,
)


# ---------------------------------------------------------------------------
# detect_gpu
# ---------------------------------------------------------------------------


class TestDetectGpu:
    def test_nvidia_smi_csv_parsed(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="NVIDIA GeForce RTX 3080 Ti, 591.74, 12288\n",
        )
        with patch("src.system_checks.subprocess.run", return_value=fake_result):
            info = detect_gpu()

        assert info.has_nvidia is True
        assert info.gpu_name == "NVIDIA GeForce RTX 3080 Ti"
        assert info.driver_version == "591.74"
        assert info.vram_mb == 12288

    def test_nvidia_smi_not_found_returns_no_gpu(self) -> None:
        with patch(
            "src.system_checks.subprocess.run",
            side_effect=FileNotFoundError("nvidia-smi not found"),
        ), patch("src.system_checks.sys") as mock_sys:
            mock_sys.platform = "linux"
            info = detect_gpu()

        assert info.has_nvidia is False
        assert info.gpu_name == ""
        assert info.vram_mb == 0

    def test_nvidia_smi_nonzero_returncode(self) -> None:
        bad_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
        )
        with patch("src.system_checks.subprocess.run", return_value=bad_result), \
             patch("src.system_checks.sys") as mock_sys:
            mock_sys.platform = "linux"
            info = detect_gpu()

        assert info.has_nvidia is False

    def test_nvidia_smi_timeout(self) -> None:
        with patch(
            "src.system_checks.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10),
        ), patch("src.system_checks.sys") as mock_sys:
            mock_sys.platform = "linux"
            info = detect_gpu()

        assert info.has_nvidia is False

    def test_driver_version_float_property(self) -> None:
        info = GpuInfo(has_nvidia=True, driver_version="591.74")
        assert info.driver_version_float == 591.74

    def test_driver_version_float_empty(self) -> None:
        info = GpuInfo()
        assert info.driver_version_float == 0.0

    def test_multi_line_csv_takes_first(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="NVIDIA RTX 4090, 550.00, 24576\nNVIDIA RTX 3060, 550.00, 12288\n",
        )
        with patch("src.system_checks.subprocess.run", return_value=fake_result):
            info = detect_gpu()

        assert info.gpu_name == "NVIDIA RTX 4090"
        assert info.vram_mb == 24576

    def test_wmi_fallback_on_windows(self) -> None:
        """When nvidia-smi fails on Windows, the WMI/PowerShell path runs."""
        import json

        wmi_json = json.dumps({
            "Name": "NVIDIA GeForce GTX 1660",
            "DriverVersion": "31.0.15.5000",
            "AdapterRAM": 6442450944,
        })
        smi_fail = FileNotFoundError("nvidia-smi")
        wmi_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout=wmi_json)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise smi_fail
            return wmi_ok

        with patch("src.system_checks.subprocess.run", side_effect=side_effect), \
             patch("src.system_checks.sys") as mock_sys:
            mock_sys.platform = "win32"
            info = detect_gpu()

        assert info.has_nvidia is True
        assert "1660" in info.gpu_name
        assert info.vram_mb == 6144  # 6 GB


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------


class TestCheckDiskSpace:
    def test_returns_correct_free_gb(self) -> None:
        # 500 GB total, 200 GB free
        fake_usage = MagicMock()
        fake_usage.total = 500 * 1024**3
        fake_usage.free = 200 * 1024**3
        fake_usage.used = 300 * 1024**3

        with patch("src.system_checks.shutil.disk_usage", return_value=fake_usage):
            info = check_disk_space("/some/path")

        assert info.path == "/some/path"
        assert info.free_gb == 200.0
        assert info.total_gb == 500.0

    def test_empty_path_uses_home(self) -> None:
        fake_usage = MagicMock()
        fake_usage.total = 1000 * 1024**3
        fake_usage.free = 400 * 1024**3

        with patch("src.system_checks.shutil.disk_usage", return_value=fake_usage) as mock_du:
            info = check_disk_space("")

        # Should have been called with Path.home()
        call_path = mock_du.call_args[0][0]
        assert call_path  # non-empty

    def test_os_error_returns_empty(self) -> None:
        with patch("src.system_checks.shutil.disk_usage", side_effect=OSError("nope")):
            info = check_disk_space("/bad/path")

        assert info.path == "/bad/path"
        assert info.free_gb == 0.0
        assert info.total_gb == 0.0

    def test_walks_up_to_existing_parent(self, tmp_path) -> None:
        """Checking a non-existent directory walks up to its nearest ancestor.

        Use case: ChatterboxInstaller checks disk space at the venv target
        path (e.g. C:\\AudiobookMaker\\.venv-chatterbox) BEFORE the install
        has created it. The check must report the drive's free space, not
        0 GB.
        """
        fake_usage = MagicMock(total=1000 * 1024**3, free=500 * 1024**3)
        probe_path = tmp_path / "does" / "not" / "exist" / "yet"

        with patch("src.system_checks.shutil.disk_usage",
                   return_value=fake_usage) as mock_du:
            info = check_disk_space(str(probe_path))

        # disk_usage should be called with tmp_path (the nearest existing parent)
        called_with = mock_du.call_args[0][0]
        assert Path(called_with) == tmp_path
        # free_gb should be reported from the parent
        assert info.free_gb == 500.0
        # But the path field keeps the original
        assert info.path == str(probe_path)


# ---------------------------------------------------------------------------
# estimate_synthesis_size_mb
# ---------------------------------------------------------------------------


class TestEstimateSynthesisSize:
    def test_zero_chars_is_zero(self) -> None:
        assert estimate_synthesis_size_mb(0, "edge") == 0.0

    def test_negative_chars_is_zero(self) -> None:
        assert estimate_synthesis_size_mb(-100, "edge") == 0.0

    def test_edge_tts_smaller_than_chatterbox(self) -> None:
        edge = estimate_synthesis_size_mb(10_000, "edge")
        cbox = estimate_synthesis_size_mb(10_000, "chatterbox_fi")
        assert cbox > edge

    def test_full_book_chatterbox_is_hundreds_of_mb(self) -> None:
        # 65k chars ~= 4h Finnish audiobook
        mb = estimate_synthesis_size_mb(65_000, "chatterbox_fi")
        assert 400 < mb < 1000

    def test_scales_linearly_with_chars(self) -> None:
        small = estimate_synthesis_size_mb(1_000, "edge")
        big = estimate_synthesis_size_mb(10_000, "edge")
        assert abs(big / small - 10.0) < 0.01

    def test_unknown_engine_uses_default(self) -> None:
        # Should not crash, returns some positive estimate
        assert estimate_synthesis_size_mb(1_000, "made_up") > 0


# ---------------------------------------------------------------------------
# check_output_disk_space
# ---------------------------------------------------------------------------


class TestCheckOutputDiskSpace:
    def test_enough_space_returns_true(self) -> None:
        fake_usage = MagicMock(total=1000 * 1024**3, free=100 * 1024**3)
        with patch("src.system_checks.shutil.disk_usage", return_value=fake_usage):
            ok, free_mb, need_mb = check_output_disk_space(
                "/some/path", 10_000, "edge",
            )
        assert ok is True
        assert free_mb > need_mb

    def test_insufficient_space_returns_false(self) -> None:
        # 1 MB free, try to make 500 MB chatterbox
        fake_usage = MagicMock(total=1000 * 1024**3, free=1 * 1024**2)
        with patch("src.system_checks.shutil.disk_usage", return_value=fake_usage):
            ok, free_mb, need_mb = check_output_disk_space(
                "/some/path", 65_000, "chatterbox_fi",
            )
        assert ok is False
        assert need_mb > free_mb

    def test_zero_text_always_fits(self) -> None:
        fake_usage = MagicMock(total=1 * 1024**3, free=1 * 1024**2)
        with patch("src.system_checks.shutil.disk_usage", return_value=fake_usage):
            ok, _free, need = check_output_disk_space("/x", 0, "edge")
        assert ok is True
        assert need == 0.0


# ---------------------------------------------------------------------------
# find_python311
# ---------------------------------------------------------------------------


class TestFindPython311:
    def test_py_launcher_found(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="C:\\Python311\\python.exe\n",
        )
        with patch("src.system_checks.subprocess.run", return_value=fake_result), \
             patch("src.system_checks.sys") as mock_sys, \
             patch("pathlib.Path.exists", return_value=True):
            mock_sys.platform = "win32"
            info = find_python311()

        assert info.found is True
        assert info.version == "3.11"

    def test_py_launcher_not_found(self) -> None:
        with patch(
            "src.system_checks.subprocess.run",
            side_effect=FileNotFoundError("py not found"),
        ), patch("src.system_checks.sys") as mock_sys, \
             patch("pathlib.Path.exists", return_value=False), \
             patch("src.system_checks.shutil.which", return_value=None):
            mock_sys.platform = "win32"
            info = find_python311()

        assert info.found is False
        assert info.path is None

    def test_python311_on_path(self) -> None:
        with patch("src.system_checks.subprocess.run", side_effect=FileNotFoundError), \
             patch("src.system_checks.sys") as mock_sys, \
             patch("pathlib.Path.exists", return_value=False), \
             patch("src.system_checks.shutil.which") as mock_which:
            mock_sys.platform = "linux"
            mock_which.return_value = "/usr/bin/python3.11"
            info = find_python311()

        assert info.found is True
        assert info.path == Path("/usr/bin/python3.11")

    def test_default_python_info(self) -> None:
        info = PythonInfo()
        assert info.found is False
        assert info.path is None
        assert info.version == ""


# ---------------------------------------------------------------------------
# run_full_check
# ---------------------------------------------------------------------------


class TestRunFullCheck:
    def test_returns_system_report(self) -> None:
        gpu = GpuInfo(has_nvidia=True, gpu_name="Test GPU", vram_mb=8192)
        disk = DiskInfo(path="/", free_gb=100.0, total_gb=500.0)
        py = PythonInfo(found=True, path=Path("/usr/bin/python3.11"), version="3.11")

        with patch("src.system_checks.detect_gpu", return_value=gpu), \
             patch("src.system_checks.check_disk_space", return_value=disk), \
             patch("src.system_checks.find_python311", return_value=py):
            report = run_full_check("/")

        assert report.gpu.has_nvidia is True
        assert report.gpu.gpu_name == "Test GPU"
        assert report.disk.free_gb == 100.0
        assert report.python311.found is True

    def test_report_defaults(self) -> None:
        report = SystemReport()
        assert report.gpu.has_nvidia is False
        assert report.disk.free_gb == 0.0
        assert report.python311.found is False
