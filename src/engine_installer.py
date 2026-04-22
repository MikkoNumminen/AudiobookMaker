"""In-app engine installation for AudiobookMaker.

Provides installer classes for each TTS engine that can be driven from
the GUI with progress callbacks. Replaces the old PowerShell/Inno Setup
post-install scripts with pure Python so everything runs inside the app
window — no console popups.

Each installer is idempotent: re-running after a partial install resumes
from where it left off.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.system_checks import find_python311, detect_gpu, check_disk_space

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

PYTHON_VERSION = "3.11.9"
PYTHON_INSTALLER_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-amd64.exe"
)
PYTHON_INSTALLER_SIZE_MB = 25

TORCH_WHEEL_VERSION = "2.6.0"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"

PIP_PACKAGES_MAIN = [
    "chatterbox-tts",
    "safetensors",
    "num2words",
    "silero-vad",
    "pydub",
    "PyMuPDF",
    "huggingface_hub",
    # Voice-pack LoRA training (scripts/voice_pack_train.py). Adds ~6 MB
    # on top of the ~5 GB Chatterbox stack; listed here so training does
    # not fail with NotImplementedError on a fresh install.
    "peft",
    "accelerate",
]

HF_REPOS = [
    ("ResembleAI/chatterbox", None),
    (
        "Finnish-NLP/Chatterbox-Finnish",
        [
            "models/best_finnish_multilingual_cp986.safetensors",
            "samples/reference_finnish.wav",
        ],
    ),
]

DEFAULT_VENV_PATH = Path(r"C:\AudiobookMaker\.venv-chatterbox")


# ---------------------------------------------------------------------------
# User-facing strings (bilingual)
# ---------------------------------------------------------------------------

_STRINGS = {
    "fi": {
        "disk_under_200mb": "Levytilaa alle 200 MB",
        "no_nvidia_gpu": "NVIDIA-näytönohjainta ei löytynyt",
        "low_vram": "Näytönohjaimessa vain {vram} MB muistia (suositus 8 GB+)",
        "low_disk": "Levytilaa vain {free} GB (tarvitaan vähintään 16 GB)",
        "python_install_failed": "Python-asennus epäonnistui (koodi {code})",
        "venv_create_failed": "Ympäristön luonti epäonnistui: {err}",
        "torch_install_failed": "torch-asennus epäonnistui",
        "chatterbox_install_failed": "chatterbox-asennus epäonnistui",
    },
    "en": {
        "disk_under_200mb": "Less than 200 MB of disk space",
        "no_nvidia_gpu": "No NVIDIA graphics card found",
        "low_vram": "Graphics card has only {vram} MB of memory (8 GB+ recommended)",
        "low_disk": "Only {free} GB of disk space (at least 16 GB required)",
        "python_install_failed": "Python install failed (code {code})",
        "venv_create_failed": "Virtualenv creation failed: {err}",
        "torch_install_failed": "torch install failed",
        "chatterbox_install_failed": "chatterbox install failed",
    },
}


def _s(key: str, ui_lang: str = "fi", **fmt) -> str:
    """Look up a user-facing string. Falls back to Finnish on unknown language."""
    table = _STRINGS.get(ui_lang, _STRINGS["fi"])
    text = table.get(key, _STRINGS["fi"][key])
    if fmt:
        return text.format(**fmt)
    return text


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


@dataclass
class InstallStep:
    """Description of one install step for display."""

    name: str
    label: str
    estimated_size_mb: int = 0
    estimated_minutes: int = 0


@dataclass
class InstallProgress:
    """Progress event pushed to the GUI queue during install."""

    step: int = 0
    total_steps: int = 0
    step_label: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    percent: float = 0.0
    message: str = ""
    error: str = ""
    done: bool = False


ProgressCallback = Callable[[InstallProgress], None]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class EngineInstaller(ABC):
    """Abstract base for engine installers."""

    engine_id: str = ""
    display_name: str = ""

    # UI language for user-facing error strings. Callers may override this
    # (e.g. the engine dialog sets it from the app config) before invoking
    # check_prerequisites() or install().
    ui_lang: str = "fi"

    @abstractmethod
    def check_prerequisites(self, ui_lang: str = "fi") -> list[str]:
        """Return list of unmet prerequisites (empty = all OK)."""

    @abstractmethod
    def get_steps(self) -> list[InstallStep]:
        """Return the planned install steps for display."""

    @abstractmethod
    def install(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Run the full install. Call from a background thread.

        Must push InstallProgress events via progress_cb. Must check
        cancel_event between steps and abort cleanly if set.
        """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _download_file(
    url: str,
    dest: Path,
    progress_cb: Optional[ProgressCallback] = None,
    step: int = 0,
    total_steps: int = 1,
    step_label: str = "",
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Download a file with progress reporting."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    try:
        # Context manager guarantees the HTTP response handle is closed on
        # exception or cancellation, not just on the happy path. Without this
        # a cancelled download leaks the underlying socket until GC runs.
        with urllib.request.urlopen(url, timeout=30) as response:
            total_bytes = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 256 * 1024  # 256 KB

            with open(tmp, "wb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError("Cancelled")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(
                            InstallProgress(
                                step=step,
                                total_steps=total_steps,
                                step_label=step_label,
                                bytes_done=downloaded,
                                bytes_total=total_bytes,
                                percent=(downloaded / total_bytes * 100)
                                if total_bytes
                                else 0,
                                message=f"{downloaded // (1024 * 1024)}"
                                f" / {total_bytes // (1024 * 1024)} MB",
                            )
                        )

        # Atomic rename.
        if dest.exists():
            dest.unlink()
        tmp.rename(dest)

    except InterruptedError:
        if tmp.exists():
            tmp.unlink()
        raise
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def _run_subprocess(
    cmd: list[str],
    progress_cb: Optional[ProgressCallback] = None,
    step: int = 0,
    total_steps: int = 1,
    step_label: str = "",
    cancel_event: Optional[threading.Event] = None,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess and stream its output to progress_cb."""
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=merged_env,
    )

    output_lines = []
    for line in proc.stdout:  # type: ignore
        line = line.rstrip()
        output_lines.append(line)
        if progress_cb:
            progress_cb(
                InstallProgress(
                    step=step,
                    total_steps=total_steps,
                    step_label=step_label,
                    message=line,
                )
            )
        if cancel_event and cancel_event.is_set():
            proc.terminate()
            proc.wait()
            raise InterruptedError("Cancelled")

    proc.wait()
    result = subprocess.CompletedProcess(
        cmd, proc.returncode, "\n".join(output_lines), ""
    )
    return result


# ---------------------------------------------------------------------------
# Piper installer
# ---------------------------------------------------------------------------


PIPER_VOICE_FILES = [
    "fi_FI-harri-medium.onnx",
    "fi_FI-harri-medium.onnx.json",
]
PIPER_VOICE_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "fi/fi_FI/harri/medium/"
)
PIPER_VOICE_DIR_NAME = "fi_FI-harri-medium"


class PiperInstaller(EngineInstaller):
    """Downloads Piper Finnish voice files (~60 MB)."""

    engine_id = "piper"
    display_name = "Piper (offline)"

    def __init__(self) -> None:
        self._voice_dir = (
            Path.home()
            / ".audiobookmaker"
            / "piper_voices"
            / PIPER_VOICE_DIR_NAME
        )

    def check_prerequisites(self, ui_lang: str = "fi") -> list[str]:
        issues = []
        disk = check_disk_space(str(Path.home()))
        if disk.free_gb < 0.2:
            issues.append(_s("disk_under_200mb", ui_lang))
        return issues

    def get_steps(self) -> list[InstallStep]:
        return [
            InstallStep(
                name="download_voice",
                label="Ladataan Piper Harri -ääni",
                estimated_size_mb=60,
                estimated_minutes=2,
            )
        ]

    def is_installed(self) -> bool:
        return all(
            (self._voice_dir / f).exists() for f in PIPER_VOICE_FILES
        )

    def install(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        total = 1
        step = 1

        for i, filename in enumerate(PIPER_VOICE_FILES):
            dest = self._voice_dir / filename
            if dest.exists():
                continue

            url = PIPER_VOICE_BASE_URL + filename
            label = f"Ladataan {filename}"
            progress_cb(
                InstallProgress(
                    step=step,
                    total_steps=total,
                    step_label=label,
                    message=f"Ladataan {filename}...",
                )
            )
            _download_file(
                url,
                dest,
                progress_cb=progress_cb,
                step=step,
                total_steps=total,
                step_label=label,
                cancel_event=cancel_event,
            )

        progress_cb(
            InstallProgress(
                step=step,
                total_steps=total,
                step_label="Valmis",
                done=True,
                message="Piper-ääni asennettu.",
            )
        )


# ---------------------------------------------------------------------------
# Chatterbox installer
# ---------------------------------------------------------------------------


class ChatterboxInstaller(EngineInstaller):
    """Installs Chatterbox-Finnish: Python 3.11 + venv + torch + models + patch."""

    engine_id = "chatterbox_fi"
    display_name = "Chatterbox Finnish"

    def __init__(self, venv_path: Optional[Path] = None) -> None:
        self._venv_path = venv_path or DEFAULT_VENV_PATH

    @property
    def _venv_python(self) -> Path:
        if sys.platform == "win32":
            return self._venv_path / "Scripts" / "python.exe"
        return self._venv_path / "bin" / "python"

    def check_prerequisites(self, ui_lang: str = "fi") -> list[str]:
        issues = []
        gpu = detect_gpu()
        if not gpu.has_nvidia:
            issues.append(_s("no_nvidia_gpu", ui_lang))
        elif gpu.vram_mb < 6000:
            issues.append(_s("low_vram", ui_lang, vram=gpu.vram_mb))
        disk = check_disk_space(str(self._venv_path.parent))
        if disk.free_gb < 16:
            issues.append(_s("low_disk", ui_lang, free=disk.free_gb))
        return issues

    def get_steps(self) -> list[InstallStep]:
        return [
            InstallStep("python311", "Varmistetaan Python 3.11", 25, 3),
            InstallStep("venv", "Luodaan Python-ympäristö", 0, 1),
            InstallStep("torch", "Asennetaan torch + CUDA", 5000, 20),
            InstallStep("models", "Ladataan AI-mallit", 7000, 30),
            InstallStep("patch", "Sovelletaan korjaukset", 0, 1),
        ]

    def is_installed(self) -> bool:
        # Check the default path first, then fall back to the bridge resolver
        # which searches every location we've ever used (repo root, D: drive
        # dev setup, common C:\AudiobookMaker\, sibling of the running exe…).
        if self._venv_python.exists():
            return True
        try:
            from src.launcher_bridge import resolve_chatterbox_python
            return resolve_chatterbox_python() is not None
        except Exception:
            return False

    def install(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        total = 5

        # Step 1: Python 3.11
        progress_cb(
            InstallProgress(1, total, "Varmistetaan Python 3.11")
        )
        python_exe = self._ensure_python311(progress_cb, cancel_event)

        if cancel_event.is_set():
            return

        # Step 2: Create venv
        progress_cb(
            InstallProgress(
                2, total, "Luodaan Python-ympäristö",
                message=f"Kohde: {self._venv_path}",
            )
        )
        venv_py = self._create_venv(python_exe, progress_cb)

        if cancel_event.is_set():
            return

        # Step 3: pip install
        progress_cb(
            InstallProgress(
                3, total, "Asennetaan torch + chatterbox-tts",
                message="Tämä voi kestää 15-30 minuuttia...",
            )
        )
        self._pip_install(venv_py, progress_cb, cancel_event)

        if cancel_event.is_set():
            return

        # Step 4: Prefetch models
        progress_cb(
            InstallProgress(
                4, total, "Ladataan AI-mallit",
                message="Ladataan ~7 GB mallitiedostoja...",
            )
        )
        self._prefetch_models(venv_py, progress_cb, cancel_event)

        if cancel_event.is_set():
            return

        # Step 5: Gemination patch
        progress_cb(
            InstallProgress(
                5, total, "Sovelletaan korjaukset",
                message="Korjataan suomen kielen gemination...",
            )
        )
        self._apply_patch(progress_cb)

        progress_cb(
            InstallProgress(
                5, total, "Valmis",
                done=True,
                message="Chatterbox Finnish asennettu.",
            )
        )

    def _ensure_python311(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> Path:
        """Find or install Python 3.11."""
        info = find_python311()
        if info.found and info.path:
            progress_cb(
                InstallProgress(
                    1, 5, "Python 3.11 löytyi",
                    message=f"Polku: {info.path}",
                )
            )
            return info.path

        if sys.platform != "win32":
            raise RuntimeError(
                "Python 3.11 ei löytynyt. Asenna se manuaalisesti."
            )

        # Download and install silently.
        progress_cb(
            InstallProgress(
                1, 5, "Ladataan Python 3.11",
                message=f"Ladataan python.org:sta (~{PYTHON_INSTALLER_SIZE_MB} MB)...",
            )
        )

        installer_dir = Path(os.environ.get("TEMP", "/tmp")) / "audiobookmaker-py311"
        installer_dir.mkdir(parents=True, exist_ok=True)
        installer_path = installer_dir / f"python-{PYTHON_VERSION}-amd64.exe"

        if not installer_path.exists():
            _download_file(
                PYTHON_INSTALLER_URL,
                installer_path,
                progress_cb=progress_cb,
                step=1,
                total_steps=5,
                step_label="Ladataan Python 3.11",
                cancel_event=cancel_event,
            )

        if cancel_event.is_set():
            raise InterruptedError("Cancelled")

        # Silent install (per-user, no UAC).
        progress_cb(
            InstallProgress(
                1, 5, "Asennetaan Python 3.11",
                message="Hiljainen asennus käynnissä...",
            )
        )
        result = subprocess.run(
            [
                str(installer_path),
                "/quiet",
                "InstallAllUsers=0",
                "PrependPath=1",
                "Include_launcher=1",
                "InstallLauncherAllUsers=0",
                "Include_doc=0",
                "Include_test=0",
                "Include_pip=1",
                "Include_tcltk=1",
                "SimpleInstall=1",
            ],
            capture_output=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise RuntimeError(
                _s("python_install_failed", self.ui_lang, code=result.returncode)
            )

        # Re-detect after install.
        info = find_python311()
        if not info.found or not info.path:
            # Check known path directly.
            known = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs" / "Python" / "Python311" / "python.exe"
            )
            if known.exists():
                return known
            raise RuntimeError(
                "Python 3.11 ei löytynyt asennuksen jälkeen"
            )
        return info.path

    def _create_venv(
        self,
        python_exe: Path,
        progress_cb: ProgressCallback,
    ) -> Path:
        """Create or reuse the Chatterbox venv."""
        if self._venv_python.exists():
            progress_cb(
                InstallProgress(
                    2, 5, "Python-ympäristö löytyi",
                    message=f"Käytetään olemassa olevaa: {self._venv_path}",
                )
            )
            return self._venv_python

        self._venv_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(python_exe), "-m", "venv", str(self._venv_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                _s("venv_create_failed", self.ui_lang, err=result.stderr.strip())
            )
        if not self._venv_python.exists():
            raise RuntimeError(
                f"Ympäristön python ei löytynyt: {self._venv_python}"
            )
        return self._venv_python

    def _pip_install(
        self,
        venv_py: Path,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Install torch + chatterbox packages."""
        # Upgrade pip.
        _run_subprocess(
            [str(venv_py), "-m", "pip", "install", "--upgrade", "pip"],
            progress_cb=progress_cb,
            step=3,
            total_steps=5,
            step_label="Päivitetään pip",
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            return

        # CUDA torch.
        result = _run_subprocess(
            [
                str(venv_py), "-m", "pip", "install",
                f"torch=={TORCH_WHEEL_VERSION}",
                f"torchaudio=={TORCH_WHEEL_VERSION}",
                "--index-url", TORCH_CUDA_INDEX,
            ],
            progress_cb=progress_cb,
            step=3,
            total_steps=5,
            step_label="Asennetaan torch (CUDA)",
            cancel_event=cancel_event,
        )
        if result.returncode != 0:
            raise RuntimeError(_s("torch_install_failed", self.ui_lang))

        if cancel_event.is_set():
            return

        # Main packages.
        result = _run_subprocess(
            [str(venv_py), "-m", "pip", "install", *PIP_PACKAGES_MAIN],
            progress_cb=progress_cb,
            step=3,
            total_steps=5,
            step_label="Asennetaan chatterbox + riippuvuudet",
            cancel_event=cancel_event,
        )
        if result.returncode != 0:
            raise RuntimeError(_s("chatterbox_install_failed", self.ui_lang))

    def _prefetch_models(
        self,
        venv_py: Path,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Download HuggingFace model weights."""
        code_lines = ["from huggingface_hub import snapshot_download"]
        for repo_id, allow in HF_REPOS:
            if allow is None:
                code_lines.append(
                    f"snapshot_download({repo_id!r}, repo_type='model')"
                )
            else:
                allow_str = repr(list(allow))
                code_lines.append(
                    f"snapshot_download({repo_id!r}, repo_type='model', "
                    f"allow_patterns={allow_str})"
                )
        code = "; ".join(code_lines)

        result = _run_subprocess(
            [str(venv_py), "-c", code],
            progress_cb=progress_cb,
            step=4,
            total_steps=5,
            step_label="Ladataan AI-mallit HuggingFacesta",
            cancel_event=cancel_event,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Mallien lataus epäonnistui. Tarkista internet-yhteys."
            )

    def _apply_patch(self, progress_cb: ProgressCallback) -> None:
        """Apply Finnish gemination patch to alignment_stream_analyzer.py."""
        # Reuse logic from post_install_chatterbox.py.
        candidates = [
            self._venv_path / "Lib" / "site-packages" / "chatterbox"
            / "models" / "t3" / "inference"
            / "alignment_stream_analyzer.py",
            self._venv_path / "lib" / "python3.11" / "site-packages"
            / "chatterbox" / "models" / "t3" / "inference"
            / "alignment_stream_analyzer.py",
        ]

        path = None
        for c in candidates:
            if c.exists():
                path = c
                break

        if path is None:
            progress_cb(
                InstallProgress(
                    5, 5, "Korjaus ohitettu",
                    message="alignment_stream_analyzer.py ei löytynyt",
                )
            )
            return

        original = path.read_text(encoding="utf-8")
        old_window = "len(set(self.generated_tokens[-2:])) == 1"
        new_window = "len(set(self.generated_tokens[-10:])) == 1"
        old_guard = "len(self.generated_tokens) >= 3 and"
        new_guard = "len(self.generated_tokens) >= 10 and"
        old_buffer = "if len(self.generated_tokens) > 8:"
        new_buffer = "if len(self.generated_tokens) > 10:"

        if new_window in original and new_guard in original:
            progress_cb(
                InstallProgress(
                    5, 5, "Korjaus jo sovellettu",
                    message="Gemination-korjaus on jo paikallaan.",
                )
            )
            return

        if old_window not in original:
            progress_cb(
                InstallProgress(
                    5, 5, "Korjaus ohitettu",
                    message="Lähdekoodia on muutettu upstreamissa.",
                )
            )
            return

        patched = original.replace(old_window, new_window)
        patched = patched.replace(old_guard, new_guard)
        if old_buffer in patched:
            patched = patched.replace(old_buffer, new_buffer)

        path.write_text(patched, encoding="utf-8")
        progress_cb(
            InstallProgress(
                5, 5, "Korjaus sovellettu",
                message="Gemination-korjaus asennettu.",
            )
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def get_installer(engine_id: str) -> Optional[EngineInstaller]:
    """Return an installer for the given engine, or None if none exists."""
    installers = {
        "piper": PiperInstaller,
        "chatterbox_fi": ChatterboxInstaller,
    }
    cls = installers.get(engine_id)
    return cls() if cls else None


def list_installable() -> list[EngineInstaller]:
    """Return all available engine installers."""
    return [PiperInstaller(), ChatterboxInstaller()]


# ---------------------------------------------------------------------------
# Capability installers — sibling to engine installers
# ---------------------------------------------------------------------------
#
# Capability installers are things that add an ability to the app but are
# NOT a TTSEngine (no entry in the engine registry, no synthesis endpoint).
# Voice Cloner is the first one: it lives inside the Chatterbox venv and
# adds ASR + diarization so users can clone voices from an audio file.
#
# Exposed through a separate list so the Engine Manager GUI can render
# them under a distinct "Extras" header. Importing here, not at module
# top, to avoid a circular import (the voice-cloner module imports
# ``InstallProgress`` from this file).


def list_capability_installers() -> list[EngineInstaller]:
    """Return the list of non-engine capability installers.

    Thin re-export of :func:`src.engine_installer_voice_cloner.list_capability_installers`
    so GUI code can import the two registry functions from the same module.
    """
    from src.engine_installer_voice_cloner import (
        list_capability_installers as _list,
    )

    return _list()


def get_capability_installer(capability_id: str) -> Optional[EngineInstaller]:
    """Return an installer for the given capability id, or None."""
    from src.engine_installer_voice_cloner import (
        get_capability_installer as _get,
    )

    return _get(capability_id)
