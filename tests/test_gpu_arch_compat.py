"""GPU / torch-build compatibility: installer choice, runner preflight, fallback.

A torch build only has kernels for the GPUs in torch.cuda.get_arch_list().
These tests drive the installer's build choice and the runner's startup
check with the GPU and torch mocked, so they need neither.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

import pytest

import src.engine_installer as ei
import src.system_checks as system_checks
from src.engine_installer import ChatterboxInstaller
from src.system_checks import GpuInfo
from src.torch_builds import CPU_BUILD, GPU_BUILDS

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402

CU124, CU128 = GPU_BUILDS


# ---------------------------------------------------------------------------
# GPU probe
# ---------------------------------------------------------------------------


class TestDetectComputeCapabilities:
    def test_one_entry_per_gpu(self) -> None:
        out = MagicMock(returncode=0, stdout="12.0\n8.6\n")
        with mock.patch.object(system_checks.subprocess, "run", return_value=out):
            assert system_checks.detect_compute_capabilities() == [(12, 0), (8, 6)]

    def test_old_nvidia_smi_without_the_field(self) -> None:
        out = MagicMock(returncode=2, stdout="")
        with mock.patch.object(system_checks.subprocess, "run", return_value=out):
            assert system_checks.detect_compute_capabilities() == []

    def test_no_nvidia_smi(self) -> None:
        with mock.patch.object(
            system_checks.subprocess, "run", side_effect=FileNotFoundError()
        ):
            assert system_checks.detect_compute_capabilities() == []


# ---------------------------------------------------------------------------
# Installer: which build, and how it is installed
# ---------------------------------------------------------------------------


def _machine(has_nvidia: bool, caps: list):
    return (
        mock.patch.object(ei, "detect_gpu", return_value=GpuInfo(has_nvidia=has_nvidia)),
        mock.patch.object(ei, "detect_compute_capabilities", return_value=caps),
    )


class TestInstallerChoosesBuild:
    @pytest.mark.parametrize(
        "has_nvidia, caps, build, needle",
        [
            (True, [(8, 9)], CU124, "2.6.0+cu124"),
            (True, [(12, 0)], CU128, "2.8.0+cu128"),
            (True, [(13, 0)], CPU_BUILD, "13.0"),       # no build for it yet
            (False, [], CPU_BUILD, "NVIDIA"),
        ],
    )
    def test_build_and_user_message(self, tmp_path, has_nvidia, caps, build, needle):
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        inst.ui_lang = "en"
        gpu_patch, caps_patch = _machine(has_nvidia, caps)
        with gpu_patch, caps_patch:
            chosen, message = inst._select_torch_build()
        assert chosen == build
        assert needle in message
        assert build.local_version in message


def _pip_argvs(build, *, force=False, installed=None, tmp_path):
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    events = []
    with mock.patch.object(inst, "_select_torch_build", return_value=(build, "msg")), \
         mock.patch.object(inst, "_installed_torch_version", return_value=installed), \
         mock.patch.object(
             ei, "_run_subprocess", return_value=MagicMock(returncode=0)
         ) as run:
        inst._pip_install(tmp_path / "py.exe", events.append, threading.Event(),
                          force=force)
    return [c.args[0] for c in run.call_args_list], events


def _torch_argv(argvs):
    return next(a for a in argvs if any(str(t).startswith("torch==") for t in a))


class TestInstallerPipSteps:
    def test_validated_build_installs_as_before(self, tmp_path) -> None:
        argvs, _ = _pip_argvs(CU124, tmp_path=tmp_path)
        torch_argv = _torch_argv(argvs)
        assert "torch==2.6.0" in torch_argv
        assert CU124.index_url in torch_argv
        main = [a for a in argvs if "chatterbox-tts==0.1.7" in a]
        assert len(main) == 1
        assert "--no-deps" not in main[0]

    def test_blackwell_build_installs_chatterbox_without_deps(self, tmp_path) -> None:
        argvs, events = _pip_argvs(CU128, tmp_path=tmp_path)
        torch_argv = _torch_argv(argvs)
        assert "torch==2.8.0" in torch_argv
        assert "torchaudio==2.8.0" in torch_argv
        assert CU128.index_url in torch_argv
        # chatterbox-tts declares torch==2.6.0: resolved with deps it would
        # swap the cu128 build for PyPI's CPU torch 2.6.0.
        with_chatterbox = [a for a in argvs if "chatterbox-tts==0.1.7" in a]
        assert len(with_chatterbox) == 1
        assert "--no-deps" in with_chatterbox[0]
        assert argvs[-1] is with_chatterbox[0], "chatterbox goes in last"
        rest = [a for a in argvs if "transformers==5.2.0" in a]
        assert rest and "chatterbox-tts==0.1.7" not in rest[0]
        # The user sees which build was chosen.
        assert any(e.message == "msg" for e in events)

    def test_old_build_is_replaced_on_plain_install(self, tmp_path) -> None:
        # The field case: a venv built with cu124, now on an RTX 50 card. The
        # version differs, so pip upgrades on its own: no forced re-download
        # of torch's dependencies.
        argvs, _ = _pip_argvs(CU128, installed="2.6.0+cu124", tmp_path=tmp_path)
        torch_argv = _torch_argv(argvs)
        assert "torch==2.8.0" in torch_argv and CU128.index_url in torch_argv
        assert "--force-reinstall" not in torch_argv

    @pytest.mark.parametrize("installed", ["2.6.0+cpu", "2.6.0"])
    def test_same_version_other_build_is_forced(self, tmp_path, installed) -> None:
        # pip calls 2.6.0+cpu (or PyPI's untagged CPU 2.6.0) "already
        # satisfied" for torch==2.6.0, so only a forced reinstall swaps it.
        argvs, _ = _pip_argvs(CU124, installed=installed, tmp_path=tmp_path)
        assert "--force-reinstall" in _torch_argv(argvs)

    def test_matching_build_is_not_redownloaded(self, tmp_path) -> None:
        argvs, _ = _pip_argvs(CU128, installed="2.8.0+cu128", tmp_path=tmp_path)
        assert "--force-reinstall" not in _torch_argv(argvs)

    def test_repair_keeps_a_working_build_when_the_probe_fails(self, tmp_path) -> None:
        # Through the real selector: nvidia-smi gives no capability this once,
        # and the venv already holds the cu128 build. Repair must not take it
        # back to cu124.
        inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
        gpu_patch, caps_patch = _machine(True, [])
        with gpu_patch, caps_patch, \
             mock.patch.object(inst, "_installed_torch_version",
                               return_value=CU128.local_version), \
             mock.patch.object(
                 ei, "_run_subprocess", return_value=MagicMock(returncode=0)
             ) as run:
            inst._pip_install(tmp_path / "py.exe", lambda _p: None,
                              threading.Event(), force=True)
        torch_argv = _torch_argv([c.args[0] for c in run.call_args_list])
        assert "torch==2.8.0" in torch_argv and CU128.index_url in torch_argv
        assert "--force-reinstall" not in torch_argv

    def test_repair_keeps_no_deps_for_every_build(self, tmp_path) -> None:
        for build in (CU124, CU128):
            argvs, _ = _pip_argvs(build, force=True, tmp_path=tmp_path)
            main = [a for a in argvs if "chatterbox-tts==0.1.7" in a]
            assert len(main) == 1
            assert "--force-reinstall" in main[0] and "--no-deps" in main[0]

    def test_cpu_build_comes_from_the_cpu_index(self, tmp_path) -> None:
        argvs, _ = _pip_argvs(CPU_BUILD, tmp_path=tmp_path)
        assert CPU_BUILD.index_url in _torch_argv(argvs)


class TestStandaloneInstaller:
    """installer/post_install_chatterbox.py shares the table."""

    @pytest.fixture()
    def post_install(self):
        path = _REPO_ROOT / "installer" / "post_install_chatterbox.py"
        spec = importlib.util.spec_from_file_location("_abm_post_install", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize("caps, build", [([(8, 6)], CU124), ([(12, 0)], CU128)])
    def test_pip_steps(self, post_install, caps, build) -> None:
        with mock.patch.object(post_install, "detect_gpu",
                               return_value=GpuInfo(has_nvidia=True)), \
             mock.patch.object(post_install, "detect_compute_capabilities",
                               return_value=caps), \
             mock.patch.object(post_install, "pip_install") as pip:
            post_install.install_python_packages(Path("py.exe"))
        argvs = [c.args[1] for c in pip.call_args_list]
        torch_argv = next(a for a in argvs if build.index_url in a)
        # The exact local version, so a same-version other build is replaced.
        assert f"torch=={build.local_version}" in torch_argv
        assert f"torchaudio=={build.local_version}" in torch_argv
        with_chatterbox = [a for a in argvs if "chatterbox-tts==0.1.7" in a]
        assert len(with_chatterbox) == 1
        assert ("--no-deps" in with_chatterbox[0]) is build.overrides_chatterbox_pin


# ---------------------------------------------------------------------------
# Runner: startup preflight and CPU fallback
# ---------------------------------------------------------------------------


def _props(name, capability, sm_count=48, memory_gb=12):
    return SimpleNamespace(name=name, major=capability[0], minor=capability[1],
                           multi_processor_count=sm_count,
                           total_memory=memory_gb * 2**30)


def _fake_torch(*, available=True, capability=(12, 0), build=CU124,
                name="NVIDIA GeForce RTX 5070", gpus=None):
    """torch with CUDA mocked. ``gpus`` lists extra devices as _props()."""
    torch = MagicMock()
    torch.__version__ = build.local_version
    torch.version = SimpleNamespace(cuda="12.x" if build.is_cuda else None)
    torch.cuda.is_available.return_value = available
    torch.cuda.current_device.return_value = 0
    torch.cuda.get_device_capability.return_value = capability
    torch.cuda.get_device_name.return_value = name
    torch.cuda.get_arch_list.return_value = list(build.arch_list)
    devices = gpus if gpus is not None else [_props(name, capability)]
    torch.cuda.device_count.return_value = len(devices) if available else 0
    torch.cuda.get_device_properties.side_effect = lambda index: devices[index]
    return torch


class TestPreflightDecision:
    def test_compatible_gpu_uses_cuda_quietly(self, capsys) -> None:
        with mock.patch.dict(sys.modules, {"torch": _fake_torch(capability=(8, 9))}):
            assert gca._resolve_device("auto") == "cuda"
        assert "[warn]" not in capsys.readouterr().out

    def test_blackwell_on_the_new_build_uses_cuda(self) -> None:
        torch = _fake_torch(capability=(12, 0), build=CU128)
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("auto") == "cuda"

    @pytest.mark.parametrize("requested", ["auto", "cuda"])
    def test_incompatible_gpu_falls_back_to_cpu(self, capsys, requested) -> None:
        with mock.patch.dict(sys.modules, {"torch": _fake_torch()}):
            assert gca._resolve_device(requested) == "cpu"
        out = capsys.readouterr().out
        assert "NVIDIA GeForce RTX 5070" in out
        assert "12.0" in out
        assert "2.6.0+cu124" in out
        assert "sm_90" in out
        assert "Install engines" in out and "Repair" in out
        assert "CPU" in out
        # The GUI turns this one line into a dialog; everything else is a
        # warning in the log.
        assert ("[setup] gpu fallback: gpu=NVIDIA GeForce RTX 5070; "
                "capability=12.0; torch=2.6.0+cu124") in out.splitlines()
        assert all(line.startswith(("[warn]", "[setup] gpu fallback:"))
                   for line in out.splitlines())

    def test_names_the_gpu_in_use(self, capsys) -> None:
        torch = _fake_torch(capability=(12, 0), build=CU128,
                            name="NVIDIA GeForce RTX 5060 Ti")
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("auto") == "cuda"
        out = capsys.readouterr().out
        assert "[setup] GPU in use: NVIDIA GeForce RTX 5060 Ti" in out
        assert "found GPU" not in out  # one card: no inventory
        torch.cuda.set_device.assert_called_once_with(0)

    def test_new_card_is_used_when_the_old_one_is_still_installed(self, capsys) -> None:
        # GTX 1080 still in slot 0 next to the new RTX 5060 Ti; cu128 runs on
        # both. The run must land on the new card, and the log says so.
        torch = _fake_torch(build=CU128, gpus=[
            _props("NVIDIA GeForce GTX 1080", (6, 1), sm_count=20, memory_gb=8),
            _props("NVIDIA GeForce RTX 5060 Ti", (12, 0), sm_count=36, memory_gb=16),
        ])
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("auto") == "cuda"
        torch.cuda.set_device.assert_called_once_with(1)
        out = capsys.readouterr().out
        assert "[setup] found GPU 0: NVIDIA GeForce GTX 1080" in out
        assert "[setup] found GPU 1: NVIDIA GeForce RTX 5060 Ti" in out
        assert "[setup] GPU in use: NVIDIA GeForce RTX 5060 Ti" in out
        assert "[warn]" not in out

    def test_unsupported_first_card_does_not_force_the_cpu(self) -> None:
        # cu124 cannot run the 5060 Ti in slot 0 but can run the 3080 in
        # slot 1: use the 3080 instead of falling back to the CPU.
        torch = _fake_torch(build=CU124, gpus=[
            _props("NVIDIA GeForce RTX 5060 Ti", (12, 0)),
            _props("NVIDIA GeForce RTX 3080", (8, 6)),
        ])
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("auto") == "cuda"
        torch.cuda.set_device.assert_called_once_with(1)

    def test_mismatch_is_not_mistaken_for_a_broken_venv(self, capsys) -> None:
        # The installer rebuilds the venv (~30 min) on corruption-shaped smoke
        # failures. A rebuild with the same build cannot fix a GPU mismatch.
        with mock.patch.dict(sys.modules, {"torch": _fake_torch()}):
            gca._resolve_device("auto")
        assert not ei._smoke_error_looks_like_corruption(capsys.readouterr().out)

    def test_no_gpu(self, capsys) -> None:
        with mock.patch.dict(sys.modules, {"torch": _fake_torch(available=False)}):
            assert gca._resolve_device("auto") == "cpu"
        out = capsys.readouterr().out
        assert "CUDA not available" in out
        assert "CPU-only build" not in out

    def test_cpu_only_build_explains_itself(self, capsys) -> None:
        torch = _fake_torch(available=False, build=CPU_BUILD)
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("auto") == "cpu"
        out = capsys.readouterr().out
        assert "2.6.0+cpu" in out and "CPU-only build" in out
        assert "Install engines" in out

    def test_explicit_cpu_never_touches_the_gpu(self) -> None:
        torch = _fake_torch()
        with mock.patch.dict(sys.modules, {"torch": torch}):
            assert gca._resolve_device("cpu") == "cpu"
        torch.cuda.get_device_capability.assert_not_called()


_SELFTEST_MODULES = (
    "torchaudio", "chatterbox", "chatterbox.mtl_tts", "silero_vad", "pydub",
    "huggingface_hub", "safetensors", "peft", "accelerate",
)


def _selftest_with(torch):
    modules = {name: MagicMock() for name in _SELFTEST_MODULES}
    modules["torch"] = torch
    with mock.patch.dict(sys.modules, modules), \
         mock.patch.object(gca, "_verify_base_revision_pin", return_value=0):
        return gca._selftest()


class TestSelftest:
    def test_incompatible_gpu_fails_with_the_reason(self, capsys) -> None:
        torch = _fake_torch()
        assert _selftest_with(torch) == 2
        out = capsys.readouterr().out
        assert "[error] GPU NVIDIA GeForce RTX 5070" in out
        assert "Install engines" in out
        torch.ones.assert_not_called()

    def test_compatible_gpu_runs_a_real_kernel(self, capsys) -> None:
        torch = _fake_torch(capability=(8, 6))
        assert _selftest_with(torch) == 0
        torch.ones.assert_called_once()
        assert "OK" in capsys.readouterr().out

    def test_kernel_runs_on_the_card_synthesis_will_use(self) -> None:
        torch = _fake_torch(build=CU124, gpus=[
            _props("NVIDIA GeForce RTX 5060 Ti", (12, 0)),
            _props("NVIDIA GeForce RTX 3080", (8, 6)),
        ])
        assert _selftest_with(torch) == 0
        torch.cuda.set_device.assert_called_once_with(1)
        torch.ones.assert_called_once()

    def test_cpu_build_skips_the_gpu(self, capsys) -> None:
        torch = _fake_torch(available=False, build=CPU_BUILD)
        assert _selftest_with(torch) == 0
        torch.ones.assert_not_called()
        torch.cuda.get_device_capability.assert_not_called()


class TestFallbackRunsTheConversionOnCpu:
    def test_main_loads_the_engine_on_cpu(self, tmp_path, monkeypatch, capsys) -> None:
        """End to end through main(): an incompatible GPU must not stop the
        conversion; the engine is loaded on the CPU instead."""
        text = tmp_path / "book.txt"
        text.write_text("Tämä on lyhyt testilause.", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "runner", "--text-file", str(text), "--out", str(tmp_path / "out"),
            "--device", "auto", "--language", "fi",
        ])

        class _Loaded(Exception):
            pass

        devices = []

        def fake_load_engine(device, *args, **kwargs):
            devices.append(device)
            raise _Loaded

        modules = {"torch": _fake_torch(), "torchaudio": MagicMock(),
                   "chatterbox": MagicMock()}
        with mock.patch.dict(sys.modules, modules), \
             mock.patch.object(gca, "_ensure_flow_token_clamp"), \
             mock.patch.object(gca, "_load_engine", side_effect=fake_load_engine), \
             mock.patch.object(gca.signal, "signal"):
            with pytest.raises(_Loaded):
                gca.main()
        assert devices == ["cpu"]
        out = capsys.readouterr().out
        assert "[setup] device=cpu" in out
        assert "cannot run the installed torch 2.6.0+cu124" in out


class TestLegacyVoicePackOnCpu:
    def test_pickled_adapter_is_loaded_onto_the_cpu(self, tmp_path) -> None:
        """A legacy adapter.pt saved from a CUDA run must not restore onto CUDA
        when this run fell back to the CPU."""
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "adapter.pt").write_bytes(b"pickled")
        torch = _fake_torch()
        safetensors_torch = MagicMock()
        safetensors_torch.load_file.side_effect = ValueError("not safetensors")
        peft = MagicMock()
        modules = {
            "torch": torch, "peft": peft, "peft.utils": peft.utils,
            "safetensors": MagicMock(torch=safetensors_torch),
            "safetensors.torch": safetensors_torch,
        }
        with mock.patch.dict(sys.modules, modules):
            gca._apply_lora_adapter(MagicMock(), pack)
        assert torch.load.call_args.kwargs["map_location"] == "cpu"
