"""Unit tests for src.engine_installer module."""

from __future__ import annotations

import io
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.engine_installer import (
    ChatterboxInstaller,
    EngineInstaller,
    InstallProgress,
    InstallStep,
    PiperInstaller,
    _download_file,
    _run_subprocess,
    get_installer,
    list_installable,
)
from src.system_checks import DiskInfo, GpuInfo


# ---------------------------------------------------------------------------
# PiperInstaller
# ---------------------------------------------------------------------------


class TestPiperInstaller:
    def test_engine_metadata(self) -> None:
        inst = PiperInstaller()
        assert inst.engine_id == "piper"
        assert inst.display_name == "Piper (offline)"

    def test_check_prerequisites_ok_when_disk_space_sufficient(self) -> None:
        disk = DiskInfo(path="/home", free_gb=10.0, total_gb=100.0)
        with patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = PiperInstaller().check_prerequisites()

        assert issues == []

    def test_check_prerequisites_low_disk(self) -> None:
        disk = DiskInfo(path="/home", free_gb=0.1, total_gb=100.0)
        with patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = PiperInstaller().check_prerequisites()

        assert len(issues) == 1
        assert "200 MB" in issues[0]

    def test_get_steps_returns_one_step(self) -> None:
        steps = PiperInstaller().get_steps()
        assert len(steps) == 1
        assert steps[0].name == "download_voice"

    def test_is_installed_true_when_all_files_exist(self, tmp_path) -> None:
        inst = PiperInstaller()
        inst._voice_dir = tmp_path
        (tmp_path / "fi_FI-harri-medium.onnx").write_bytes(b"model")
        (tmp_path / "fi_FI-harri-medium.onnx.json").write_text("{}")

        assert inst.is_installed() is True

    def test_is_installed_false_when_files_missing(self, tmp_path) -> None:
        inst = PiperInstaller()
        inst._voice_dir = tmp_path
        # Only one file present
        (tmp_path / "fi_FI-harri-medium.onnx").write_bytes(b"model")

        assert inst.is_installed() is False

    def test_install_calls_download(self, tmp_path) -> None:
        inst = PiperInstaller()
        inst._voice_dir = tmp_path

        progress_events: list[InstallProgress] = []
        cancel = threading.Event()

        with patch("src.engine_installer._download_file", autospec=True) as mock_dl:
            inst.install(progress_events.append, cancel)

        # Should attempt to download both voice files
        assert mock_dl.call_count == 2

    def test_install_skips_existing_files(self, tmp_path) -> None:
        inst = PiperInstaller()
        inst._voice_dir = tmp_path
        # Create both files so downloads are skipped
        (tmp_path / "fi_FI-harri-medium.onnx").write_bytes(b"x")
        (tmp_path / "fi_FI-harri-medium.onnx.json").write_text("{}")

        progress_events: list[InstallProgress] = []
        cancel = threading.Event()

        with patch("src.engine_installer._download_file", autospec=True) as mock_dl:
            inst.install(progress_events.append, cancel)

        mock_dl.assert_not_called()
        # Should still get the final "done" event
        assert any(e.done for e in progress_events)


# ---------------------------------------------------------------------------
# ChatterboxInstaller
# ---------------------------------------------------------------------------


class TestChatterboxInstaller:
    def test_engine_metadata(self) -> None:
        inst = ChatterboxInstaller()
        assert inst.engine_id == "chatterbox_fi"
        assert inst.display_name == "Chatterbox Finnish"

    def test_get_steps_returns_five(self) -> None:
        steps = ChatterboxInstaller().get_steps()
        assert len(steps) == 5

    def test_step_names(self) -> None:
        names = [s.name for s in ChatterboxInstaller().get_steps()]
        assert names == ["python311", "venv", "torch", "models", "patch"]

    def test_check_prerequisites_no_gpu(self) -> None:
        gpu = GpuInfo(has_nvidia=False)
        disk = DiskInfo(path="C:\\", free_gb=50.0, total_gb=500.0)
        with patch("src.engine_installer.detect_gpu", autospec=True, return_value=gpu), \
             patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = ChatterboxInstaller().check_prerequisites()

        assert any("NVIDIA" in i for i in issues)

    def test_check_prerequisites_low_vram(self) -> None:
        gpu = GpuInfo(has_nvidia=True, gpu_name="GTX 1650", vram_mb=4096)
        disk = DiskInfo(path="C:\\", free_gb=50.0, total_gb=500.0)
        with patch("src.engine_installer.detect_gpu", autospec=True, return_value=gpu), \
             patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = ChatterboxInstaller().check_prerequisites()

        assert any("4096 MB" in i for i in issues)

    def test_check_prerequisites_low_disk(self) -> None:
        gpu = GpuInfo(has_nvidia=True, gpu_name="RTX 3080", vram_mb=10240)
        disk = DiskInfo(path="C:\\", free_gb=5.0, total_gb=500.0)
        with patch("src.engine_installer.detect_gpu", autospec=True, return_value=gpu), \
             patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = ChatterboxInstaller().check_prerequisites()

        assert any("16 GB" in i for i in issues)

    def test_check_prerequisites_all_ok(self) -> None:
        gpu = GpuInfo(has_nvidia=True, gpu_name="RTX 4090", vram_mb=24576)
        disk = DiskInfo(path="C:\\", free_gb=100.0, total_gb=500.0)
        with patch("src.engine_installer.detect_gpu", autospec=True, return_value=gpu), \
             patch("src.engine_installer.check_disk_space", autospec=True, return_value=disk):
            issues = ChatterboxInstaller().check_prerequisites()

        assert issues == []

    def test_is_installed_checks_venv_python(self, tmp_path) -> None:
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        # Patch the bridge fallback so a real dev venv on the host doesn't
        # leak into the unit test result.
        with patch(
            "src.launcher_bridge.resolve_chatterbox_python", autospec=True, return_value=None,
        ):
            assert inst.is_installed() is False

            # Create the expected python exe path
            scripts = tmp_path / "venv" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").write_bytes(b"fake")

            with patch("src.engine_installer.sys") as mock_sys:
                mock_sys.platform = "win32"
                inst2 = ChatterboxInstaller(venv_path=tmp_path / "venv")
                assert inst2.is_installed() is True

    def test_is_installed_falls_back_to_bridge_resolver(self, tmp_path) -> None:
        """is_installed() returns True when the bridge locates a venv
        at any known location — not just the default C:\\AudiobookMaker path.
        """
        inst = ChatterboxInstaller(venv_path=tmp_path / "missing_venv")
        with patch(
            "src.launcher_bridge.resolve_chatterbox_python",
            autospec=True,
            return_value="D:/koodaamista/AudiobookMaker/.venv-chatterbox/Scripts/python.exe",
        ):
            assert inst.is_installed() is True

    def test_cancel_event_stops_installation(self) -> None:
        inst = ChatterboxInstaller()
        cancel = threading.Event()
        cancel.set()  # Pre-cancelled

        progress_events: list[InstallProgress] = []

        with patch.object(inst, "_ensure_python311", return_value=Path("/fake/python")):
            inst.install(progress_events.append, cancel)

        # Should not reach step 2 because cancel is set after step 1
        assert not any(e.done for e in progress_events)


# ---------------------------------------------------------------------------
# _download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_writes_file_and_reports_progress(self, tmp_path) -> None:
        dest = tmp_path / "downloaded.bin"
        content = b"A" * 1024

        # Mock urlopen to return a response-like object usable as a context
        # manager: __enter__ must return the same response object.
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        mock_response.headers.get.return_value = str(len(content))
        mock_response.read.side_effect = [content, b""]

        progress_events: list[InstallProgress] = []

        with patch("src.engine_installer.urllib.request.urlopen", return_value=mock_response):
            _download_file(
                url="https://example.com/file.bin",
                dest=dest,
                progress_cb=progress_events.append,
                step=1,
                total_steps=3,
                step_label="Downloading",
            )

        assert dest.exists()
        assert dest.read_bytes() == content
        assert len(progress_events) >= 1
        assert progress_events[0].step == 1
        assert progress_events[0].total_steps == 3
        # __exit__ on the context manager must have fired so the socket is
        # released even on the happy path.
        mock_response.__exit__.assert_called()

    def test_cancel_during_download(self, tmp_path) -> None:
        dest = tmp_path / "cancelled.bin"
        cancel = threading.Event()

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        mock_response.headers.get.return_value = "10000"

        def read_and_cancel(size):
            cancel.set()
            return b"X" * 256

        mock_response.read.side_effect = read_and_cancel

        with patch("src.engine_installer.urllib.request.urlopen", return_value=mock_response):
            with pytest.raises(InterruptedError):
                _download_file(
                    url="https://example.com/file.bin",
                    dest=dest,
                    cancel_event=cancel,
                )

        # Temp file should have been cleaned up
        assert not dest.with_suffix(".bin.tmp").exists()
        # The urlopen response must have been closed via __exit__ so the
        # socket does not leak when the download is cancelled.
        mock_response.__exit__.assert_called()

    def test_urlopen_called_with_timeout(self, tmp_path) -> None:
        """Regression test: urlopen must be invoked with a finite timeout so a
        stalled Python 3.11 download does not freeze the installer modal."""
        dest = tmp_path / "file.bin"

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        mock_response.headers.get.return_value = "4"
        mock_response.read.side_effect = [b"data", b""]

        with patch(
            "src.engine_installer.urllib.request.urlopen",
            return_value=mock_response,
        ) as mock_urlopen:
            _download_file(url="https://example.com/file.bin", dest=dest)

        _, kwargs = mock_urlopen.call_args
        assert "timeout" in kwargs
        assert 0 < kwargs["timeout"] <= 60

    def test_cleans_up_temp_on_error(self, tmp_path) -> None:
        dest = tmp_path / "error.bin"

        with patch(
            "src.engine_installer.urllib.request.urlopen",
            side_effect=ConnectionError("network down"),
        ):
            with pytest.raises(ConnectionError):
                _download_file(url="https://example.com/bad", dest=dest)

        assert not dest.exists()
        assert not dest.with_suffix(".bin.tmp").exists()

    def test_creates_parent_dirs(self, tmp_path) -> None:
        dest = tmp_path / "sub" / "dir" / "file.bin"

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        mock_response.headers.get.return_value = "4"
        mock_response.read.side_effect = [b"data", b""]

        with patch("src.engine_installer.urllib.request.urlopen", return_value=mock_response):
            _download_file(url="https://example.com/file.bin", dest=dest)

        assert dest.exists()
        assert dest.read_bytes() == b"data"


# ---------------------------------------------------------------------------
# _run_subprocess
# ---------------------------------------------------------------------------


class _FakePipe:
    """Test helper: an iterable with a close() the production code can
    call. MagicMock's default ``iter(...)`` replacement loses the
    .close() attribute that the finally-close now depends on."""

    def __init__(self, lines):
        self._iter = iter(lines)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        self.closed = True


class TestRunSubprocess:
    def test_streams_output_to_callback(self) -> None:
        events: list[InstallProgress] = []

        mock_proc = MagicMock()
        pipe = _FakePipe(["line 1\n", "line 2\n"])
        mock_proc.stdout = pipe
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        with patch("src.engine_installer.subprocess.Popen", return_value=mock_proc):
            result = _run_subprocess(
                ["echo", "hello"],
                progress_cb=events.append,
                step=2,
                total_steps=5,
                step_label="Running",
            )

        assert result.returncode == 0
        assert len(events) == 2
        assert events[0].message == "line 1"
        assert events[1].message == "line 2"
        # finally-close must run on the happy path too.
        assert pipe.closed is True

    def test_cancel_terminates_process(self) -> None:
        cancel = threading.Event()

        mock_proc = MagicMock()

        def lines():
            yield "line 1\n"
            cancel.set()
            yield "line 2\n"

        pipe = _FakePipe(lines())
        mock_proc.stdout = pipe
        mock_proc.returncode = -1
        mock_proc.wait.return_value = None

        with patch("src.engine_installer.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(InterruptedError):
                _run_subprocess(["cmd"], cancel_event=cancel)

        mock_proc.terminate.assert_called_once()
        # Pipe must be closed even when cancel raises mid-stream.
        assert pipe.closed is True

    def test_wait_timeout_is_passed_through(self) -> None:
        """_run_subprocess must call proc.wait(timeout=...) with a finite,
        positive timeout. A missing timeout is the exact bug that froze the
        install modal on a hung pip."""
        mock_proc = MagicMock()
        pipe = _FakePipe([])
        mock_proc.stdout = pipe
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        with patch("src.engine_installer.subprocess.Popen", return_value=mock_proc):
            _run_subprocess(["cmd"], timeout=42.0)

        # The only wait() call in the happy path is the final one.
        mock_proc.wait.assert_called_once()
        _, kwargs = mock_proc.wait.call_args
        assert kwargs.get("timeout") == 42.0

    def test_wait_timeout_propagates(self) -> None:
        """If proc.wait() raises TimeoutExpired, the caller sees it so the
        install dialog can surface the hang instead of spinning forever."""
        mock_proc = MagicMock()
        pipe = _FakePipe([])
        mock_proc.stdout = pipe
        mock_proc.returncode = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=1.0)

        with patch("src.engine_installer.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(subprocess.TimeoutExpired):
                _run_subprocess(["cmd"], timeout=1.0)

        # Even on timeout, the pipe must be closed.
        assert pipe.closed is True

    def test_pipe_closed_when_progress_cb_raises(self) -> None:
        """If the progress callback itself raises, the finally must still
        close the stdout pipe so the child can exit cleanly."""
        mock_proc = MagicMock()
        pipe = _FakePipe(["boom\n"])
        mock_proc.stdout = pipe
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        def angry_cb(_evt):
            raise RuntimeError("ui exploded")

        with patch("src.engine_installer.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="ui exploded"):
                _run_subprocess(["cmd"], progress_cb=angry_cb)

        assert pipe.closed is True


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_installer_piper(self) -> None:
        inst = get_installer("piper")
        assert isinstance(inst, PiperInstaller)

    def test_get_installer_chatterbox(self) -> None:
        inst = get_installer("chatterbox_fi")
        assert isinstance(inst, ChatterboxInstaller)

    def test_get_installer_unknown(self) -> None:
        assert get_installer("nonexistent") is None

    def test_list_installable(self) -> None:
        installers = list_installable()
        assert len(installers) == 2
        engine_ids = {i.engine_id for i in installers}
        assert engine_ids == {"piper", "chatterbox_fi"}
