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

# Pinned dependency chain for the Chatterbox engine venv. This MUST stay
# identical to installer/requirements-chatterbox.txt and the matching list
# in installer/post_install_chatterbox.py — tests/test_chatterbox_requirements.py
# enforces parity so the two install paths can never drift apart again.
#
# Every entry is pinned on purpose: a floating transformers (or any other
# link in this chain) is what produced the silent "Could not import module
# 'LlamaModel'" engine-load failures. See the requirements file's header for
# the full rationale and bump policy. torch / torchaudio install separately
# via TORCH_CUDA_INDEX so chatterbox-tts's resolver does not pull the CPU
# wheel — keep them out of this list.
PIP_PACKAGES_MAIN = [
    "chatterbox-tts==0.1.7",
    # The load-bearing chain (the LlamaModel failure lives here).
    "transformers==5.2.0",
    "tokenizers==0.22.2",
    # chatterbox-tts direct dependencies that otherwise float.
    "numpy==1.26.4",
    "diffusers==0.29.0",
    "librosa==0.11.0",
    "conformer==0.3.2",
    "s3tokenizer==0.3.0",
    "omegaconf==2.3.0",
    "resemble-perth==1.0.1",
    "pykakasi==2.3.0",
    "pyloudnorm==0.2.0",
    "safetensors==0.5.3",
    "huggingface_hub==1.10.1",
    # Runner companions.
    "silero-vad==6.2.1",
    "pydub==0.25.1",
    "num2words==0.5.14",
    "PyMuPDF==1.27.2.2",
    # Voice-pack LoRA training (scripts/voice_pack_train.py). Adds ~6 MB
    # on top of the ~5 GB Chatterbox stack; listed here so training does
    # not fail with NotImplementedError on a fresh install.
    "peft==0.19.1",
    "accelerate==1.13.0",
]

# (repo_id, allow_patterns, revision). The revision is an immutable commit
# SHA so the prefetch resolves the same model files every install — an
# upstream rename/move/force-push can't silently change what we cache. The
# Finnish SHA matches FINNISH_REVISION in the synthesis runner so the prefetch
# populates exactly the revision the runner later requests.
HF_REPOS = [
    ("ResembleAI/chatterbox", None, "ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd"),
    (
        "Finnish-NLP/Chatterbox-Finnish",
        [
            "models/best_finnish_multilingual_cp986.safetensors",
            "samples/reference_finnish.wav",
        ],
        "d15775e1788055e67f49dfc6da402021e51bd0f0",
    ),
]

# The ResembleAI/chatterbox base model is loaded by the library's
# ChatterboxMultilingualTTS.from_pretrained, which hardcodes revision="main".
# We can't pass a revision through that call, so the install step patches the
# library source to pin it to this SHA (matching the prefetch revision in
# HF_REPOS). MUST equal the ResembleAI/chatterbox revision above.
CHATTERBOX_BASE_REVISION = "ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd"

DEFAULT_VENV_PATH = Path(r"C:\AudiobookMaker\.venv-chatterbox")

# Sentinel written inside the engine venv while an install or repair is in
# progress, and removed only after the post-install smoke test passes. A venv
# that has its python.exe but still carries this marker is INCOMPLETE — an
# install still running, or one interrupted/corrupted (e.g. a Convert was
# launched against the half-built venv and pip could not finish overwriting
# files the synth subprocess had locked). Treating such a venv as "not
# installed" stops the engine from looking usable before it actually is.
# Installs built before this marker existed never carry it, so they keep
# reading as installed — the check is backward-compatible.
INSTALL_INCOMPLETE_MARKER = ".install-incomplete"


def is_install_incomplete(venv_python) -> bool:
    """True if the venv that owns ``venv_python`` carries the incomplete marker.

    ``venv_python`` is the interpreter path (``<venv>/Scripts/python.exe`` or
    ``<venv>/bin/python``); the marker lives at the venv root. Used by the
    Chatterbox engine's ``check_status`` to refuse a venv that is mid-install
    or was interrupted.
    """
    try:
        venv_root = Path(venv_python).resolve(strict=False).parent.parent
        return (venv_root / INSTALL_INCOMPLETE_MARKER).exists()
    except (OSError, ValueError):
        return False


# Smoke-test error substrings that mean missing/broken PACKAGES — the failure a
# clean rebuild can actually fix. Anything else (e.g. a CUDA-runtime error from
# torch.zeros(1).cuda() on a machine whose driver is down) is environmental, so
# a repair does NOT escalate to a ~30-min rebuild that would re-fail the same way.
_CORRUPTION_SMOKE_SIGNATURES = (
    "importerror",
    "modulenotfounderror",
    "no module named",
    "dll load failed",
    "cannot import",
    "failed to import",
    # Windows: a missing/half-written dependency DLL (e.g. torch's fbgemm.dll)
    # surfaces as OSError [WinError 126/127] "Error loading ..." rather than an
    # ImportError — still a corruption a clean rebuild fixes. A CUDA-availability
    # failure reads differently ("Found no NVIDIA driver" / "CUDA error"), so
    # these don't re-introduce the wasted-rebuild-on-CUDA-down case.
    "winerror 126",
    "winerror 127",
    "error loading",
    # A CPU-only torch wheel (wrong BUILD, not missing hardware) reports this —
    # e.g. after a bad force-reinstall clobbered the cu124 wheel. A clean
    # rebuild reinstalls the cu124 CUDA wheel and fixes it. Distinct from a
    # genuine "Found no NVIDIA driver" / "CUDA error", which a rebuild can't fix
    # and which therefore is NOT in this list.
    "torch not compiled with cuda enabled",
)


def _smoke_error_looks_like_corruption(err) -> bool:
    """True when a smoke-test error looks like broken packages (rebuild fixes it)
    rather than an environmental problem like CUDA being unavailable."""
    low = (err or "").lower()
    return any(sig in low for sig in _CORRUPTION_SMOKE_SIGNATURES)


# Upper bound on the smoke-test subprocess. Long enough to cover a cold
# torch import on a slow machine; short enough that a hung process does
# not freeze the install dialog indefinitely.
_SMOKE_TEST_TIMEOUT_S = 120


def _allowed_venv_roots() -> list[Path]:
    """Directories under which a Chatterbox venv is allowed to live.

    A venv_path that resolves outside every one of these roots is rejected
    before it gets passed to ``subprocess.run(... -m venv ...)`` — otherwise
    a malicious or corrupt config value ("../../Windows/System32") would
    drop a Python environment anywhere on disk.

    The roots cover the three legitimate locations:

    * The canonical install root ``C:\\AudiobookMaker\\`` (Inno Setup default
      and ``DEFAULT_VENV_PATH`` parent).
    * The repo / app root — ``.venv-chatterbox`` next to a dev checkout or
      next to the frozen executable.
    * ``%LOCALAPPDATA%`` — future fallback when the install root is
      read-only.
    """
    roots: list[Path] = []

    # 1. DEFAULT_VENV_PATH's parent: C:\AudiobookMaker\
    roots.append(DEFAULT_VENV_PATH.parent.resolve(strict=False))

    # 2. Dev / frozen app root. In dev the engine_installer module lives in
    #    <repo>/src/, so repo root is two parents up. In frozen mode the
    #    resolved root is where the .exe lives.
    try:
        roots.append(Path(__file__).resolve().parent.parent)
    except (OSError, ValueError):
        pass

    # 3. LOCALAPPDATA (Windows per-user root).
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        try:
            roots.append(Path(localappdata).resolve(strict=False))
        except (OSError, ValueError):
            pass

    # 4. TMP / TEMP — pytest's tmp_path lives here. Valid in tests only,
    #    but the cost of allowing it is zero in production because no
    #    real config points a venv at TEMP.
    for var in ("TEMP", "TMP"):
        tmp = os.environ.get(var)
        if tmp:
            try:
                roots.append(Path(tmp).resolve(strict=False))
            except (OSError, ValueError):
                pass

    return roots


def _canonicalize_venv_path(venv_path: Path) -> Path:
    """Resolve ``venv_path`` and verify it sits under an allowed root.

    Raises ``ValueError`` on a path-traversal or a path that escapes every
    allowed root. The resolved Path is returned so every downstream caller
    (mkdir, subprocess.run ``-m venv``) operates on the canonical form and
    cannot be fooled by symlinks or ``..`` segments sneaking through.
    """
    resolved = Path(venv_path).resolve(strict=False)
    roots = _allowed_venv_roots()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise ValueError(
        f"venv_path {venv_path!r} (resolved to {resolved}) is outside every "
        f"allowed root: {[str(r) for r in roots]}"
    )


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

    def force_reinstall(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Reinstall the engine to repair a broken/partial install.

        Default is a plain reinstall — adequate for engines whose assets are
        self-contained. Engines whose pip dependency tree can drift (e.g.
        Chatterbox, where a too-new transformers breaks model loading)
        override this to force the pinned versions back into place.
        """
        self.install(progress_cb, cancel_event)


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


# 30 minutes. Covers the worst case we have in production: a fresh torch
# + CUDA wheel install on a slow residential connection. Anything longer
# is almost certainly a hung process, not real progress — freezing the
# install modal forever (the old behaviour) is worse than surfacing the
# hang to the caller as a TimeoutExpired.
_DEFAULT_SUBPROCESS_TIMEOUT_S = 1800


def _run_subprocess(
    cmd: list[str],
    progress_cb: Optional[ProgressCallback] = None,
    step: int = 0,
    total_steps: int = 1,
    step_label: str = "",
    cancel_event: Optional[threading.Event] = None,
    env: Optional[dict] = None,
    timeout: float = _DEFAULT_SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess:
    """Run a subprocess and stream its output to progress_cb.

    Raises ``subprocess.TimeoutExpired`` if the child does not finish
    within ``timeout`` seconds. Callers that expect legitimately long
    installs (pip install torch) can raise the timeout; everyone else
    gets a safe default so a hung pip never freezes the install modal
    indefinitely.
    """
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=merged_env,
    )

    output_lines: list[str] = []
    # Wrap the stdout iteration in try/finally: if the progress_cb
    # raises, or if we hit the TimeoutExpired path below, the pipe still
    # gets closed. Without this the read end would linger until GC and
    # the child could block on a full PIPE buffer.
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
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
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    # After kill() the OS will reap quickly; 5s is a
                    # safety net against a kernel-level zombie window
                    # on shared CI machines.
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                raise InterruptedError("Cancelled")

        proc.wait(timeout=timeout)
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
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
    # Pinned to an immutable commit SHA (was floating `main`): an upstream
    # reorg of the voice tree can no longer change what this downloads.
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "b710b0ba0740da88dc36e1ab8fa6b310d43a3a48/"
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

    def remove(self) -> bool:
        """Delete the installed voice files. Returns True if anything was removed."""
        import shutil as _shutil
        if self._voice_dir.exists():
            _shutil.rmtree(self._voice_dir, ignore_errors=False)
            return True
        return False

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

    engine_id = "chatterbox_grandmom"
    # Single Chatterbox install handles BOTH the Finnish Isoäiti voice
    # (T3 finetune) and the English Grandmom voice (base multilingual
    # model + bundled reference clip). Same persona, two pipelines —
    # see memory/project_isoaiti_finnish_grandmom.md. Label reflects
    # that the install covers both languages so users do not look for
    # a separate "Chatterbox English" entry. The canonical engine id
    # was renamed from ``chatterbox_fi`` to ``chatterbox_grandmom`` on
    # 2026-05-17 because the ``_fi`` suffix misled users into thinking
    # the engine was Finnish-only — the alias in tts_chatterbox_bridge.py
    # keeps the old id working for back-compat.
    display_name = "Chatterbox (Isoäiti + Grandmom)"

    def __init__(self, venv_path: Optional[Path] = None) -> None:
        # Canonicalize before storing: every downstream use — mkdir,
        # subprocess.run for `python -m venv`, is_installed() — operates
        # on the resolved path. A traversal value ("C:/AudiobookMaker/
        # ../Windows/System32/foo") now fails fast at construction
        # instead of causing subprocess.run to write outside the intended
        # install root.
        candidate = venv_path or DEFAULT_VENV_PATH
        self._venv_path = _canonicalize_venv_path(candidate)

    @property
    def _venv_python(self) -> Path:
        if sys.platform == "win32":
            return self._venv_path / "Scripts" / "python.exe"
        return self._venv_path / "bin" / "python"

    @property
    def _install_marker(self) -> Path:
        """In-progress/incomplete sentinel inside the venv (see module docstring)."""
        return self._venv_path / INSTALL_INCOMPLETE_MARKER

    def _mark_incomplete(self) -> None:
        """Mark the venv as not-yet-usable (best-effort)."""
        try:
            self._install_marker.write_text("installing\n", encoding="utf-8")
        except OSError:
            pass

    def _clear_incomplete(self) -> None:
        """Clear the incomplete marker once the install is verified."""
        try:
            self._install_marker.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

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
        # A venv counts as installed only if its python exists AND it is not
        # mid-install or interrupted — i.e. the .install-incomplete marker is
        # absent. Pre-marker installs never carry it, so they still read as
        # installed (backward-compatible).
        if self._venv_python.exists():
            return not self._install_marker.exists()
        try:
            from src.launcher_bridge import resolve_chatterbox_python
            resolved = resolve_chatterbox_python()
        except Exception:
            return False
        if resolved is None:
            return False
        return not is_install_incomplete(resolved)

    def remove(self) -> bool:
        """Delete the Chatterbox venv. Returns True if anything was removed.

        Symmetric with :meth:`is_installed`: try the default location
        first, then fall back to ``resolve_chatterbox_python()`` so a
        venv at a non-default path (D-drive dev install, sibling-of-exe
        bundle, ``CHATTERBOX_PYTHON`` override) is still removable via
        the CLI. Without this, the engines list would say "available"
        and ``engines remove chatterbox_grandmom`` would refuse with
        "not installed" — a confusing UX gap.
        """
        import shutil as _shutil
        if self._venv_path.exists():
            _shutil.rmtree(self._venv_path, ignore_errors=False)
            return True
        try:
            from src.launcher_bridge import resolve_chatterbox_python
            resolved = resolve_chatterbox_python()
        except Exception:
            return False
        if resolved is None:
            return False
        # resolve_chatterbox_python() returns the path to python.exe
        # inside the venv (<venv>/Scripts/python.exe on Windows,
        # <venv>/bin/python on POSIX). Walk two levels up to get the
        # venv root.
        venv_root = Path(resolved).parent.parent
        # Defense against a misconfigured CHATTERBOX_PYTHON env var
        # pointing at a system python (e.g. /usr/bin/python3): in that
        # case parent.parent is /usr, and rmtree(/usr) is catastrophic.
        # pyvenv.cfg is created by `python -m venv` at the venv root
        # and exists in every legitimate venv; system directories never
        # carry one. Refuse to delete anything that does not look like
        # an actual venv.
        if not (venv_root / "pyvenv.cfg").is_file():
            return False
        if venv_root.exists() and venv_root.is_dir():
            _shutil.rmtree(venv_root, ignore_errors=False)
            return True
        return False

    def force_reinstall(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Repair a drifted Chatterbox venv by force-reinstalling the pins.

        The venv is reused (not deleted) but the main pip step runs with
        ``--force-reinstall`` so a transformers that drifted past the pin is
        replaced, and the smoke test re-verifies the engine loads.
        """
        self.install(progress_cb, cancel_event, force=True)

    def install(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
        force: bool = False,
        _allow_clean_rebuild: bool = True,
    ) -> None:
        """Install the Chatterbox engine.

        ``force`` re-runs the main pip step with ``--force-reinstall`` so an
        existing, drifted venv is pulled back to the pinned versions even when
        pip would otherwise consider a package "already satisfied". Used by
        :meth:`force_reinstall` (the ``engines repair`` path).

        ``_allow_clean_rebuild`` (internal) lets a repair that still fails its
        smoke test escalate ONCE to deleting the venv and rebuilding from
        scratch — the only way to fix a corrupt torch, which the in-place
        ``--force-reinstall`` does not touch. The recursive rebuild passes
        ``False`` to stop further escalation.
        """
        total = 6

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
        # The venv exists now but its packages do not. Mark it incomplete so
        # is_installed()/check_status() refuse to treat it as usable until the
        # smoke test passes — this is what blocks a Convert against the
        # half-built venv from looking like a working engine.
        self._mark_incomplete()

        if cancel_event.is_set():
            return

        # Step 3: pip install
        progress_cb(
            InstallProgress(
                3, total, "Asennetaan torch + chatterbox-tts",
                message="Tämä voi kestää 15-30 minuuttia...",
            )
        )
        self._pip_install(venv_py, progress_cb, cancel_event, force=force)

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

        if cancel_event.is_set():
            return

        # Step 6: Smoke test — actually load torch + Chatterbox so any
        # broken-venv failure surfaces here, while the user is still
        # watching the install dialog, instead of much later mid-Convert.
        progress_cb(
            InstallProgress(
                6, total, "Tarkistetaan asennus",
                message="Yritetään ladata torch ja Chatterbox...",
            )
        )
        smoke_error = self._smoke_test(venv_py, cancel_event)
        if smoke_error is not None:
            if (
                force and _allow_clean_rebuild
                and not cancel_event.is_set()
                and _smoke_error_looks_like_corruption(smoke_error)
            ):
                # A reused venv that still fails the smoke test after a
                # force-reinstall, with a package/import-shaped error, is
                # corrupt beyond repair-in-place: the main --force-reinstall
                # step does not touch torch, so a broken torch survives it.
                # Delete the venv and rebuild from scratch once — a fresh venv
                # reinstalls torch too. (A non-corruption failure such as CUDA
                # being unavailable is NOT rebuilt — see
                # _smoke_error_looks_like_corruption.)
                progress_cb(
                    InstallProgress(
                        6, total, "Rakennetaan ympäristö uudelleen",
                        message=(
                            "Korjaus ei riittänyt — asennetaan puhtaalta "
                            "pöydältä."
                        ),
                    )
                )
                # Do NOT clear the marker first: let remove() delete it with the
                # venv. If remove() fails (locked files — the very corruption
                # case), the broken venv keeps its marker and still reads as
                # incomplete; the rebuild's _mark_incomplete re-writes it.
                try:
                    self.remove()
                except OSError:
                    pass
                self.install(
                    progress_cb, cancel_event,
                    force=False, _allow_clean_rebuild=False,
                )
                return
            progress_cb(
                InstallProgress(
                    6, total, "Asennus ei toimi",
                    error=smoke_error,
                    message=(
                        "Asennus valmistui mutta tarkistus epäonnistui. "
                        "Lähetä alla oleva virhe kehittäjälle."
                    ),
                )
            )
            return

        # Re-check cancel: if the user cancelled during the smoke test,
        # _smoke_test may have returned None (the early-return sentinel)
        # rather than a real error, which would cause us to declare
        # "installed and working" on a cancelled run.
        if cancel_event.is_set():
            return

        # Install verified — clear the incomplete marker so the engine reports
        # ready.
        self._clear_incomplete()
        progress_cb(
            InstallProgress(
                6, total, "Valmis",
                done=True,
                message="Chatterbox (Isoäiti + Grandmom) asennettu ja toimii.",
            )
        )

    def _smoke_test(
        self,
        venv_python: Path,
        cancel_event: threading.Event,
    ) -> Optional[str]:
        """Verify the freshly-installed venv can actually load Chatterbox.

        Returns ``None`` on success, or a captured error string on
        failure. The probe:
          1. Imports torch and exercises CUDA (allocates a real tensor so
             a broken CUDA DLL surfaces now, not at first synthesis).
          2. Imports every package installed by PIP_PACKAGES_MAIN that the
             runner depends on, so a partial pip install is caught here.

        Cancellation is honoured mid-execution: the subprocess is
        terminated when ``cancel_event`` fires, matching the pattern used
        by ``_pip_install`` / ``_run_subprocess``. The caller is
        responsible for checking ``cancel_event`` after this returns to
        avoid treating a cancelled run as a success (see ``install()``).
        """
        if cancel_event.is_set():
            return None

        probe = (
            "import torch\n"
            # Exercise CUDA: allocate a real tensor on the device so a
            # broken cu124 wheel fails here rather than silently continuing.
            "_ = torch.zeros(1).cuda()\n"
            "from chatterbox.mtl_tts import ChatterboxMultilingualTTS\n"
            "import silero_vad\n"
            "import pydub\n"
            "import huggingface_hub\n"
            "import safetensors\n"
            "import peft\n"
            "import accelerate\n"
            "print('OK')\n"
        )

        output_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                [str(venv_python), "-c", probe],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            return f"Smoke test could not run: {exc}"

        try:
            deadline = _SMOKE_TEST_TIMEOUT_S
            import time
            start = time.monotonic()
            for line in proc.stdout:  # type: ignore[union-attr]
                output_lines.append(line.rstrip())
                if cancel_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    return None  # caller re-checks cancel_event
                elapsed = time.monotonic() - start
                if elapsed >= deadline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        # After kill() the OS reaps quickly; bound so a
                        # zombie window cannot freeze the install flow.
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                    return f"Smoke test timed out after {_SMOKE_TEST_TIMEOUT_S}s"
            proc.wait(timeout=10)
        except Exception as exc:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            return f"Smoke test could not run: {exc}"
        finally:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

        if proc.returncode != 0:
            captured = "\n".join(output_lines).strip()
            return captured or f"Smoke test exited with code {proc.returncode}"
        return None

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
        force: bool = False,
    ) -> None:
        """Install torch + chatterbox packages.

        ``force`` adds ``--force-reinstall --no-deps`` to the main package step
        so a drifted venv is repaired (a too-new transformers is pulled back to
        the pin) WITHOUT re-resolving dependencies. ``--no-deps`` is essential:
        plain ``--force-reinstall`` also reinstalls chatterbox-tts's ``torch``
        dependency from PyPI (a CPU wheel), clobbering the cu124 CUDA torch
        installed just above — which then fails synth/smoke with "Torch not
        compiled with CUDA enabled". Every runtime dependency is already pinned
        in PIP_PACKAGES_MAIN, so re-pinning just the listed packages is the
        correct, torch-safe repair.
        """
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
        main_cmd = [str(venv_py), "-m", "pip", "install"]
        if force:
            # Repair: re-pin the whole set even if pip thinks it is satisfied,
            # so a drifted transformers is replaced by the pinned version.
            # --no-deps is REQUIRED alongside --force-reinstall: without it pip
            # also reinstalls chatterbox-tts's torch dependency from PyPI (a CPU
            # wheel), clobbering the cu124 CUDA torch and breaking synth with
            # "Torch not compiled with CUDA enabled". The whole runtime chain is
            # already pinned in PIP_PACKAGES_MAIN, so deps need no resolution.
            main_cmd += ["--force-reinstall", "--no-deps"]
        main_cmd += PIP_PACKAGES_MAIN
        result = _run_subprocess(
            main_cmd,
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
        for repo_id, allow, revision in HF_REPOS:
            if allow is None:
                code_lines.append(
                    f"snapshot_download({repo_id!r}, repo_type='model', "
                    f"revision={revision!r})"
                )
            else:
                allow_str = repr(list(allow))
                code_lines.append(
                    f"snapshot_download({repo_id!r}, repo_type='model', "
                    f"allow_patterns={allow_str}, revision={revision!r})"
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
        self._pin_base_model_revision()

    def _pin_base_model_revision(self) -> None:
        """Pin the base-model download revision in the chatterbox library.

        ChatterboxMultilingualTTS.from_pretrained hardcodes revision="main"
        for the ResembleAI/chatterbox download, so an upstream rename of the
        weight files on main would break loading. Rewrite it to the validated
        SHA (matching the prefetch). Fully graceful — every miss is a no-op
        skip, so this can never break the install (worst case the base model
        stays on main, exactly as before this patch existed).
        """
        candidates = [
            self._venv_path / "Lib" / "site-packages" / "chatterbox" / "mtl_tts.py",
            self._venv_path / "lib" / "python3.11" / "site-packages"
            / "chatterbox" / "mtl_tts.py",
        ]
        path = next((c for c in candidates if c.exists()), None)
        if path is None:
            return
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            return
        new = f'revision="{CHATTERBOX_BASE_REVISION}"'
        if new in original:
            return  # already pinned
        old = 'revision="main"'
        # Only patch when there is exactly one occurrence — more than one means
        # the source shape changed and a blind replace could mis-pin a
        # different repo's download.
        if original.count(old) != 1:
            return
        path.write_text(original.replace(old, new), encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def get_installer(engine_id: str) -> Optional[EngineInstaller]:
    """Return an installer for the given engine (resolving aliases), or None.

    Engine ids are canonicalised through the registry alias map before
    lookup, so legacy ids like ``chatterbox_fi`` still resolve to the
    current ChatterboxInstaller. Add new installers under their
    canonical id only; alias entries are surfaced automatically.
    """
    from src.tts_base import canonical_engine_id

    installers = {
        "piper": PiperInstaller,
        "chatterbox_grandmom": ChatterboxInstaller,
    }
    cls = installers.get(canonical_engine_id(engine_id))
    return cls() if cls else None


def list_installable() -> list[EngineInstaller]:
    """Return all available engine installers."""
    return [PiperInstaller(), ChatterboxInstaller()]
