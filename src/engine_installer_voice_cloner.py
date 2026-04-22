"""Voice Cloner — installable capability.

Not a TTS engine. A sibling installer that lives next to the engine
installers and adds voice-cloning analysis (ASR + diarization) to the
app. Installs ``faster-whisper`` and ``pyannote.audio`` into the
existing ``.venv-chatterbox/`` so we do not bloat the main GUI venv
with torch+ctranslate2. Users see this in the Engine Manager under an
"Extras" row, separate from the Chatterbox / Piper entries.

The install walks the user through a five-step flow, explained
Barney-style in the log:

1. Disk-space check (~500 MB).
2. Pip install ``faster-whisper`` + ``pyannote.audio`` into the
   Chatterbox venv.
3. Warm the Whisper small model (one-time ~500 MB HF download).
4. **Hugging Face setup** — pyannote's diarization model is gated;
   the user needs a free HF account + access key. This step opens a
   dedicated modal owned by the GUI (injected via
   ``hf_token_prompt_fn``). Key persists at
   ``~/.cache/huggingface/token``.
5. Smoke-test imports in the Chatterbox venv to catch missing DLLs
   before the user ever hits "Clone voice from file".

Everything I/O-heavy is injectable so unit tests never touch the
network, the pip cache, or the user's token file.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.engine_installer import (
    EngineInstaller,
    InstallProgress,
    InstallStep,
    ProgressCallback,
)
from src.system_checks import check_disk_space


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VOICE_CLONER_ID: str = "voice_cloner"
VOICE_CLONER_DISPLAY_NAME: str = "Voice Cloner"

# ~500 MB pyannote model + ~500 MB Whisper small + ~100 MB wheels.
VOICE_CLONER_DISK_REQ_GB: int = 2

VOICE_CLONER_PIP_PACKAGES: tuple[str, ...] = (
    "faster-whisper",
    "pyannote.audio",
)

# Public URLs the HF setup modal opens in the user's browser. All three
# are the canonical pages under huggingface.co — no keys, no secrets.
HF_SIGNUP_URL: str = "https://huggingface.co/join"
HF_PYANNOTE_MODEL_URL: str = "https://huggingface.co/pyannote/speaker-diarization-3.1"
HF_TOKENS_URL: str = "https://huggingface.co/settings/tokens"

# Model id used in the HF HEAD-request verify step. Matches the pyannote
# model voice_pack_analyze pins.
HF_VERIFY_MODEL_ID: str = "pyannote/speaker-diarization-3.1"


# ---------------------------------------------------------------------------
# Injectable strategy types
# ---------------------------------------------------------------------------

# Modal callback. Returns the pasted token string, or None if the user
# cancelled the modal without entering one. The GUI owns the modal; the
# installer only asks for a value. Tests pass a fake that returns a
# canned string or ``None``.
HfTokenPromptFn = Callable[[], Optional[str]]


@dataclass(frozen=True)
class HfVerifyResult:
    """Outcome of verifying a token against HF.

    ``ok`` is True iff the credentials work and the model terms have
    been accepted. ``reason`` is a short machine tag: one of ``"ok"``,
    ``"unauthorised"``, ``"forbidden"``, ``"network"``, ``"other"``.
    ``detail`` is optional human-readable extra context.
    """

    ok: bool
    reason: str
    detail: Optional[str] = None


# Verify a token by hitting HF (HEAD on the gated model). Injected so
# tests do not touch the network. Default implementation does a HEAD
# request via ``urllib``.
HfVerifyFn = Callable[[str], HfVerifyResult]

# Pip + smoke-test runners. Tests inject fakes.
PipRunnerFn = Callable[[Path, tuple[str, ...], ProgressCallback, threading.Event], int]
SmokeTestFn = Callable[[Path, ProgressCallback, threading.Event], bool]


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


def _default_hf_verify(token: str) -> HfVerifyResult:
    """HEAD request to the pyannote model. No download, no side effects.

    Kept in this module so tests can monkeypatch it but its contract is
    identical to the injectable ``HfVerifyFn``.
    """
    import urllib.error
    import urllib.request

    url = f"https://huggingface.co/api/models/{HF_VERIFY_MODEL_ID}"
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                return HfVerifyResult(ok=True, reason="ok")
            return HfVerifyResult(
                ok=False, reason="other", detail=f"HTTP {resp.status}"
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return HfVerifyResult(ok=False, reason="unauthorised")
        if exc.code == 403:
            return HfVerifyResult(ok=False, reason="forbidden")
        return HfVerifyResult(ok=False, reason="other", detail=f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return HfVerifyResult(ok=False, reason="network", detail=str(exc.reason))
    except Exception as exc:  # pragma: no cover - defensive
        return HfVerifyResult(ok=False, reason="other", detail=str(exc))


# Same ceiling as src.engine_installer._DEFAULT_SUBPROCESS_TIMEOUT_S:
# 30 minutes covers a full faster-whisper + pyannote install on a slow
# residential connection. Anything longer is a hung pip, not a real
# install — propagate TimeoutExpired to the caller so the install modal
# surfaces a recoverable error instead of freezing indefinitely.
_PIP_RUNNER_TIMEOUT_S = 1800


def _default_pip_runner(
    venv_python: Path,
    packages: tuple[str, ...],
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> int:
    """Run ``pip install`` inside the Chatterbox venv, stream to progress_cb.

    Raises ``subprocess.TimeoutExpired`` if pip does not finish within
    :data:`_PIP_RUNNER_TIMEOUT_S` seconds.
    """
    cmd = [str(venv_python), "-m", "pip", "install", *packages]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            if cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return -1
            line = raw.rstrip()
            progress_cb(InstallProgress(message=line))
        return proc.wait(timeout=_PIP_RUNNER_TIMEOUT_S)
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass


def _default_smoke_test(
    venv_python: Path,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> bool:
    """Import faster-whisper + pyannote in a subprocess to catch missing DLLs."""
    if cancel_event.is_set():
        return False
    probe = (
        "import importlib, sys\n"
        "for mod in ('faster_whisper', 'pyannote.audio'):\n"
        "    importlib.import_module(mod)\n"
        "print('OK')\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python), "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        progress_cb(
            InstallProgress(message=f"Smoke test could not run: {exc}", error=str(exc))
        )
        return False
    if result.returncode != 0:
        progress_cb(
            InstallProgress(
                message=f"Smoke test failed: {result.stderr.strip() or result.stdout.strip()}",
                error=result.stderr.strip() or result.stdout.strip(),
            )
        )
        return False
    progress_cb(InstallProgress(message="Quick check: everything loads [OK]"))
    return True


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


class VoiceClonerInstaller(EngineInstaller):
    """Installs the Voice Cloner capability into ``.venv-chatterbox/``.

    Not a :class:`TTSEngine` — no entry in the engine registry. The
    Engine Manager renders this below the TTS engines under an "Extras"
    header.
    """

    engine_id = VOICE_CLONER_ID
    display_name = VOICE_CLONER_DISPLAY_NAME

    def __init__(
        self,
        *,
        venv_python: Optional[Path] = None,
        hf_token_prompt_fn: Optional[HfTokenPromptFn] = None,
        hf_verify_fn: Optional[HfVerifyFn] = None,
        pip_runner: Optional[PipRunnerFn] = None,
        smoke_test_fn: Optional[SmokeTestFn] = None,
        token_path: Optional[Path] = None,
    ) -> None:
        self._venv_python_override = venv_python
        # Default to "raise because no GUI attached" — the installer
        # can still check_prerequisites and get_steps without one, but
        # install() needs a prompt fn.
        self._hf_token_prompt_fn = hf_token_prompt_fn
        self._hf_verify_fn = hf_verify_fn or _default_hf_verify
        self._pip_runner = pip_runner or _default_pip_runner
        self._smoke_test_fn = smoke_test_fn or _default_smoke_test
        self._token_path = token_path or (
            Path.home() / ".cache" / "huggingface" / "token"
        )

    # -- venv lookup --------------------------------------------------------

    @property
    def venv_python(self) -> Optional[Path]:
        """Resolve the Chatterbox venv Python, or None if not installed."""
        if self._venv_python_override is not None:
            return self._venv_python_override
        try:
            from src.launcher_bridge import resolve_chatterbox_python
        except Exception:
            return None
        return resolve_chatterbox_python()

    # -- EngineInstaller contract ------------------------------------------

    def check_prerequisites(self, ui_lang: str = "fi") -> list[str]:
        issues: list[str] = []
        venv_python = self.venv_python
        if venv_python is None or not venv_python.exists():
            issues.append(
                "Chatterbox is not installed. Install Chatterbox first; "
                "Voice Cloner lives in the same Python environment."
            )
        else:
            disk = check_disk_space(str(venv_python.parent.parent))
            if disk.free_gb < VOICE_CLONER_DISK_REQ_GB:
                issues.append(
                    f"Only {disk.free_gb} GB free (Voice Cloner needs "
                    f"{VOICE_CLONER_DISK_REQ_GB} GB for libraries + models)."
                )
        return issues

    def get_steps(self) -> list[InstallStep]:
        return [
            InstallStep("disk", "Checking disk space", 0, 1),
            InstallStep("pip", "Installing Whisper + pyannote", 100, 4),
            InstallStep("whisper_warm", "Downloading Whisper model", 500, 4),
            InstallStep("hf_setup", "Hugging Face key", 0, 3),
            InstallStep("smoke", "Quick compatibility check", 0, 1),
        ]

    def is_installed(self) -> bool:
        """True if the smoke test would currently pass."""
        venv_python = self.venv_python
        if venv_python is None or not venv_python.exists():
            return False
        probe = "import faster_whisper, pyannote.audio; print('OK')"
        try:
            result = subprocess.run(
                [str(venv_python), "-c", probe],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
        # Token file must exist too — without it diarization fails at
        # load time.
        return self._token_path.exists()

    def install(
        self,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Run the five-step install flow. Call from a background thread."""
        total = 5
        venv_python = self.venv_python
        if venv_python is None or not venv_python.exists():
            progress_cb(
                InstallProgress(
                    step=0,
                    total_steps=total,
                    step_label="error",
                    error="Chatterbox venv not found.",
                    message=(
                        "Voice Cloner installs into the Chatterbox venv. "
                        "Install Chatterbox from the engine list first, "
                        "then come back here."
                    ),
                )
            )
            return

        # ----- Step 1: disk ------------------------------------------------
        progress_cb(
            InstallProgress(
                step=1,
                total_steps=total,
                step_label="Checking disk space",
                message=(
                    "We need about 2 GB free for the libraries and AI "
                    "models. Checking your disk…"
                ),
            )
        )
        disk = check_disk_space(str(venv_python.parent.parent))
        if disk.free_gb < VOICE_CLONER_DISK_REQ_GB:
            progress_cb(
                InstallProgress(
                    step=1,
                    total_steps=total,
                    step_label="Not enough disk space",
                    error=f"Only {disk.free_gb} GB free, need {VOICE_CLONER_DISK_REQ_GB} GB.",
                )
            )
            return

        if cancel_event.is_set():
            return

        # ----- Step 2: pip -------------------------------------------------
        progress_cb(
            InstallProgress(
                step=2,
                total_steps=total,
                step_label="Installing Whisper + pyannote",
                message=(
                    "We're downloading two helper libraries. Whisper listens "
                    "to your file and writes down what was said. Pyannote "
                    "figures out which voice said which part. They go into "
                    "the same Python folder as Chatterbox so we don't bloat "
                    "the main app."
                ),
            )
        )
        rc = self._pip_runner(
            venv_python, VOICE_CLONER_PIP_PACKAGES, progress_cb, cancel_event
        )
        if rc != 0:
            progress_cb(
                InstallProgress(
                    step=2,
                    total_steps=total,
                    step_label="Install failed",
                    error=(
                        "pip install of Whisper + pyannote did not finish "
                        f"cleanly (exit {rc}). Scroll up in the log to see "
                        "what pip complained about."
                    ),
                )
            )
            return

        if cancel_event.is_set():
            return

        # ----- Step 3: whisper warm-up -------------------------------------
        # Whisper auto-downloads its model on first import. We do not
        # force-warm here because the ASR stage does it lazily, but we
        # log a friendly heads-up so the user isn't surprised when the
        # first real analyze run pauses.
        progress_cb(
            InstallProgress(
                step=3,
                total_steps=total,
                step_label="Whisper model",
                message=(
                    "Whisper will download its listening model (~500 MB) "
                    "the first time you clone a voice. One-time download; "
                    "nothing you need to do right now."
                ),
            )
        )

        if cancel_event.is_set():
            return

        # ----- Step 4: HF setup -------------------------------------------
        hf_ok = self._ensure_hf_token(progress_cb, total, cancel_event)
        if not hf_ok:
            return

        if cancel_event.is_set():
            return

        # ----- Step 5: smoke test -----------------------------------------
        progress_cb(
            InstallProgress(
                step=5,
                total_steps=total,
                step_label="Quick compatibility check",
                message="Making sure everything loads on your machine…",
            )
        )
        if not self._smoke_test_fn(venv_python, progress_cb, cancel_event):
            progress_cb(
                InstallProgress(
                    step=5,
                    total_steps=total,
                    step_label="Smoke test failed",
                    error=(
                        "Libraries installed but did not import cleanly. "
                        "Usually a missing system DLL — scroll up for the "
                        "Python error and copy it into the Report a bug "
                        "button."
                    ),
                )
            )
            return

        progress_cb(
            InstallProgress(
                step=5,
                total_steps=total,
                step_label="Ready",
                message="Voice Cloner is installed. You can close this dialog.",
                done=True,
            )
        )

    # -- HF token flow -----------------------------------------------------

    def _ensure_hf_token(
        self,
        progress_cb: ProgressCallback,
        total_steps: int,
        cancel_event: threading.Event,
    ) -> bool:
        """Ensure a working HF token exists on disk. Return True on success.

        If the token file is already present and verify passes, reuse
        it. Otherwise ask the GUI via ``hf_token_prompt_fn`` for a
        pasted key, verify it, and write it to ``~/.cache/huggingface/token``.
        """
        step = 4
        if self._token_path.exists():
            token = self._token_path.read_text(encoding="utf-8").strip()
            if token:
                progress_cb(
                    InstallProgress(
                        step=step,
                        total_steps=total_steps,
                        step_label="Hugging Face key",
                        message=(
                            f"Found your Hugging Face key at {self._token_path} "
                            f"— reusing it."
                        ),
                    )
                )
                verify = self._hf_verify_fn(token)
                if verify.ok:
                    return True
                progress_cb(
                    InstallProgress(
                        step=step,
                        total_steps=total_steps,
                        step_label="Hugging Face key",
                        message=(
                            "Existing key didn't work (" + verify.reason + "). "
                            "Let's get a new one."
                        ),
                    )
                )

        if self._hf_token_prompt_fn is None:
            progress_cb(
                InstallProgress(
                    step=step,
                    total_steps=total_steps,
                    step_label="Hugging Face key",
                    error=(
                        "No Hugging Face setup modal available. Run install "
                        "from the GUI, not a script."
                    ),
                )
            )
            return False

        # Up to two tries: first attempt, then one retry after a clear
        # explanation of why the first key didn't work.
        for attempt in range(2):
            progress_cb(
                InstallProgress(
                    step=step,
                    total_steps=total_steps,
                    step_label="Hugging Face key",
                    message=(
                        "Opening the Hugging Face setup window. Follow the "
                        "steps there — it's a three-minute thing you only "
                        "do once."
                        if attempt == 0
                        else "Let's try that one more time."
                    ),
                )
            )
            if cancel_event.is_set():
                return False
            token = self._hf_token_prompt_fn()
            if token is None:
                progress_cb(
                    InstallProgress(
                        step=step,
                        total_steps=total_steps,
                        step_label="Hugging Face key",
                        error=(
                            "Setup was cancelled. Voice Cloner needs a "
                            "Hugging Face key to run."
                        ),
                    )
                )
                return False
            verify = self._hf_verify_fn(token)
            if verify.ok:
                self._save_token(token)
                progress_cb(
                    InstallProgress(
                        step=step,
                        total_steps=total_steps,
                        step_label="Hugging Face key",
                        message=(
                            "Hugging Face says hi back. Voice Cloner is "
                            "ready. You only have to do that once."
                        ),
                    )
                )
                return True
            progress_cb(
                InstallProgress(
                    step=step,
                    total_steps=total_steps,
                    step_label="Hugging Face key",
                    message=_verify_failure_copy(verify),
                )
            )

        progress_cb(
            InstallProgress(
                step=step,
                total_steps=total_steps,
                step_label="Hugging Face key",
                error=(
                    "Couldn't set up Hugging Face after two tries. "
                    "Cancel this, double-check the pyannote model page, "
                    "and retry."
                ),
            )
        )
        return False

    def _save_token(self, token: str) -> None:
        """Write ``token`` to ``~/.cache/huggingface/token``, restrictive perms.

        Creates the parent directory if it doesn't exist. On Unix we set
        0600; on Windows we still try ``chmod`` since ``huggingface_hub``
        reads the file and having the perms bit set doesn't hurt.
        """
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(token.strip() + "\n", encoding="utf-8")
        try:
            os.chmod(self._token_path, 0o600)
        except OSError:
            # Windows sometimes refuses chmod; ACLs on the user profile
            # protect the file in practice.
            pass


def _verify_failure_copy(verify: HfVerifyResult) -> str:
    """Return the Barney-style explanation for a failed verify.

    Public-ish so tests can assert the string per reason code.
    """
    if verify.reason == "unauthorised":
        return (
            "Hugging Face refused the key — it looks wrong. Double-check "
            "you copied the whole thing (starts with hf_)."
        )
    if verify.reason == "forbidden":
        return (
            "Hugging Face accepted the key but said you haven't agreed to "
            "the pyannote model terms yet. Open the pyannote model page "
            "and click 'Agree and access repository', then try again."
        )
    if verify.reason == "network":
        return (
            "Couldn't reach Hugging Face. Check your internet and try "
            "again — nothing is broken on your side."
        )
    return (
        "Hugging Face returned an unexpected error"
        + (f" ({verify.detail})" if verify.detail else "")
        + ". Try again in a minute."
    )


# ---------------------------------------------------------------------------
# Capability registry
# ---------------------------------------------------------------------------


def list_capability_installers() -> list[EngineInstaller]:
    """Return the list of non-engine capability installers.

    Sibling to :func:`src.engine_installer.list_installable`. The Engine
    Manager GUI renders the two lists separately — engines first, then
    an "Extras" header for these.
    """
    return [VoiceClonerInstaller()]


def get_capability_installer(capability_id: str) -> Optional[EngineInstaller]:
    """Look up a capability installer by id. None if not found."""
    if capability_id == VOICE_CLONER_ID:
        return VoiceClonerInstaller()
    return None
