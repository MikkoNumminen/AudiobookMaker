"""Post-install setup for the Chatterbox-Finnish engine.

Invoked by the Launcher Inno Setup wizard (``installer/launcher.iss``) only
when the user ticked the "Chatterbox Finnish (GPU)" component. Does the
same work as ``scripts/setup_chatterbox_windows.ps1`` but as a pure Python
script so it can be driven by the installer without hitting Windows'
PowerShell execution-policy wall.

Responsibilities (in order):

1. Find a system Python 3.11 interpreter via ``py -3.11`` or ``python3.11``.
2. Create or reuse a dedicated venv at ``--venv-path`` (default:
   ``C:\\AudiobookMaker\\.venv-chatterbox``). Short path avoids
   Windows 260-char MAX_PATH hazards when torch's deeply-nested wheel
   files land on disk.
3. pip-install CUDA torch + chatterbox-tts + runtime deps. Idempotent —
   re-running is cheap after the first successful install.
4. Pre-download the Chatterbox multilingual base model and the Finnish-NLP
   T3 finetune into ``%USERPROFILE%\\.cache\\huggingface\\hub``.
5. Apply the Finnish gemination patch to
   ``alignment_stream_analyzer.py`` inside the venv. Idempotent — checks
   whether the patch is already applied and skips if so.

Every step prints a line with the prefix ``[step N/5]`` so Inno's log
window gives the user something to watch. Failures raise ``SystemExit``
with an exit code and a Finnish error message.

Can also be run standalone for manual testing::

    python installer/post_install_chatterbox.py \\
        --venv-path C:/AudiobookMaker/.venv-chatterbox \\
        --verbose
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Versions pinned to what we verified working on dev_chatterbox_fi.py /
# setup_chatterbox_windows.ps1. Raising these requires a smoke-test.
TORCH_WHEEL_VERSION = "2.6.0"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"

PIP_PACKAGES_MAIN = [
    # CUDA torch is installed separately via --index-url below so that
    # chatterbox-tts's dep resolver treats torch as already satisfied and
    # does not pull in the CPU wheel.
    "chatterbox-tts",
    "safetensors",
    "num2words",
    "silero-vad",
    "pydub",
    "PyMuPDF",
    "huggingface_hub",
]

# HuggingFace assets to prefetch so the first real synthesis run does not
# have to wait on a 7 GB download.
HF_REPOS = [
    # (repo_id, allow_patterns)
    (
        "ResembleAI/chatterbox",
        None,  # whole multilingual repo, ~5.3 GB
    ),
    (
        "Finnish-NLP/Chatterbox-Finnish",
        ["models/best_finnish_multilingual_cp986.safetensors",
         "samples/reference_finnish.wav"],
    ),
]


def log(step: int, total: int, msg: str) -> None:
    print(f"[step {step}/{total}] {msg}", flush=True)


def err(msg: str, exit_code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[error] {msg}", flush=True, file=sys.stderr)
    raise SystemExit(exit_code)


# ---------------------------------------------------------------------------
# Step 1 — locate a system Python 3.11
# ---------------------------------------------------------------------------


def find_system_python311() -> Path:
    """Return a path to a Python 3.11 executable on this system.

    Tries in order:
      1. ``py -3.11 -c "import sys; print(sys.executable)"`` (Windows py
         launcher — the most reliable).
      2. ``where python3.11`` / ``which python3.11``.
      3. ``python.exe`` on PATH, and checks its ``sys.version_info`` matches.

    Raises SystemExit with a Finnish error message on failure.
    """
    # 1. Windows py launcher.
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["py", "-3.11", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                p = Path(result.stdout.strip())
                if p.exists():
                    return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 2. python3.11 on PATH.
    which = shutil.which("python3.11")
    if which:
        return Path(which)

    # 3. Bare python/python3 on PATH — check version.
    for candidate in ("python", "python3"):
        which = shutil.which(candidate)
        if not which:
            continue
        try:
            result = subprocess.run(
                [which, "-c",
                 "import sys; "
                 "print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip() == "3.11":
                return Path(which)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    err(
        "Python 3.11 ei löytynyt. Asenna Python 3.11 osoitteesta "
        "https://www.python.org/downloads/release/python-3119/ "
        "ja muista rastittaa 'Add python.exe to PATH'."
    )


# ---------------------------------------------------------------------------
# Step 2 — create venv
# ---------------------------------------------------------------------------


def create_or_reuse_venv(python_exe: Path, venv_path: Path) -> Path:
    """Create ``venv_path`` if missing, return the venv python executable."""
    venv_py = (
        venv_path / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv_path / "bin" / "python"
    )
    if venv_py.exists():
        return venv_py

    venv_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(python_exe), "-m", "venv", str(venv_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err(
            f"venv-ympäristön luonti epäonnistui: {result.stderr.strip()}",
            exit_code=2,
        )
    if not venv_py.exists():
        err(f"venv pythonia ei löytynyt luonnin jälkeen: {venv_py}", exit_code=2)
    return venv_py


# ---------------------------------------------------------------------------
# Step 3 — pip install
# ---------------------------------------------------------------------------


def pip_install(venv_py: Path, args: list[str], label: str) -> None:
    """Run ``venv_py -m pip install ...`` and stream output."""
    cmd = [str(venv_py), "-m", "pip", "install", *args]
    print(f"  $ {' '.join(cmd[:6])} ...", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        err(
            f"pip install {label} epäonnistui (exit {result.returncode}). "
            "Tarkista internet-yhteys ja yritä uudelleen.",
            exit_code=3,
        )


def install_python_packages(venv_py: Path) -> None:
    # Upgrade pip first — old pip sometimes can't resolve the large CUDA
    # wheels correctly.
    pip_install(venv_py, ["--upgrade", "pip"], label="pip")

    pip_install(
        venv_py,
        [
            f"torch=={TORCH_WHEEL_VERSION}",
            f"torchaudio=={TORCH_WHEEL_VERSION}",
            "--index-url",
            TORCH_CUDA_INDEX,
        ],
        label="torch (CUDA cu124)",
    )

    pip_install(venv_py, PIP_PACKAGES_MAIN, label="chatterbox + deps")


# ---------------------------------------------------------------------------
# Step 4 — prefetch HuggingFace weights
# ---------------------------------------------------------------------------


def prefetch_models(venv_py: Path) -> None:
    """Call ``huggingface_hub.snapshot_download`` inside the venv.

    We shell out rather than importing huggingface_hub directly because
    the installer script itself is run by the *system* Python, not the
    venv Python, and the venv is the only place the package is
    installed.
    """
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

    result = subprocess.run([str(venv_py), "-c", code])
    if result.returncode != 0:
        err(
            "HuggingFace-mallien lataus epäonnistui. Tarkista internet-"
            "yhteys ja yritä uudelleen. Lataus jatkuu siitä mihin jäi.",
            exit_code=4,
        )


# ---------------------------------------------------------------------------
# Step 5 — Finnish gemination patch
# ---------------------------------------------------------------------------


def _find_analyzer_path(venv_path: Path) -> Optional[Path]:
    """Return the path to chatterbox's alignment_stream_analyzer.py inside
    the venv, or None if not found (upstream may rename someday)."""
    candidates = [
        venv_path / "Lib" / "site-packages" / "chatterbox" / "models" / "t3"
        / "inference" / "alignment_stream_analyzer.py",  # Windows
        venv_path / "lib" / "python3.11" / "site-packages" / "chatterbox"
        / "models" / "t3" / "inference" / "alignment_stream_analyzer.py",
        # macOS / Linux
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def apply_gemination_patch(venv_path: Path) -> None:
    """Raise the AlignmentStreamAnalyzer token-repetition threshold from 2
    to 10 tokens so Finnish gemination stops triggering premature EOS.

    Idempotent: detects whether the patched form is already in place and
    skips in that case.
    """
    path = _find_analyzer_path(venv_path)
    if path is None:
        print(
            "  skipping: alignment_stream_analyzer.py not found (upstream "
            "may have renamed it)",
            flush=True,
        )
        return

    original = path.read_text(encoding="utf-8")
    # Fingerprints of the pre-patched (buggy) form.
    old_window = "len(set(self.generated_tokens[-2:])) == 1"
    old_guard = "len(self.generated_tokens) >= 3 and"
    old_buffer = "if len(self.generated_tokens) > 8:"
    new_window = "len(set(self.generated_tokens[-10:])) == 1"
    new_guard = "len(self.generated_tokens) >= 10 and"
    new_buffer = "if len(self.generated_tokens) > 10:"

    if new_window in original and new_guard in original:
        print("  already patched — skipping", flush=True)
        return

    if old_window not in original or old_guard not in original:
        print(
            "  upstream source has changed (neither old nor new fingerprint "
            "found); skipping patch. Expect Finnish gemination cuts.",
            flush=True,
        )
        return

    patched = original.replace(old_window, new_window)
    patched = patched.replace(old_guard, new_guard)
    if old_buffer in patched:
        patched = patched.replace(old_buffer, new_buffer)
    elif "self.generated_tokens[-8:]" in patched:
        patched = patched.replace(
            "self.generated_tokens[-8:]", "self.generated_tokens[-10:]"
        )

    # Sanity check — make sure we actually changed something.
    if patched == original:
        err(
            "gemination-korjauksen applying epäonnistui: tiedosto ei "
            "muuttunut odotusten mukaisesti.",
            exit_code=5,
        )

    path.write_text(patched, encoding="utf-8")
    print(f"  patched {path}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Post-install setup for Chatterbox-Finnish.",
    )
    p.add_argument(
        "--venv-path",
        type=Path,
        default=Path(r"C:\AudiobookMaker\.venv-chatterbox"),
        help="Where to create the Chatterbox venv. Default keeps the "
             "path short to avoid Windows MAX_PATH issues with torch "
             "wheels.",
    )
    p.add_argument(
        "--skip-models",
        action="store_true",
        help="Skip the HuggingFace model prefetch step (for debugging).",
    )
    p.add_argument(
        "--skip-patch",
        action="store_true",
        help="Skip the Finnish gemination patch (for debugging).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    total = 5

    log(1, total, "Etsitään Python 3.11…")
    python_exe = find_system_python311()
    print(f"  found {python_exe}", flush=True)

    log(2, total, f"Luodaan venv: {args.venv_path}")
    venv_py = create_or_reuse_venv(python_exe, args.venv_path)
    print(f"  venv python: {venv_py}", flush=True)

    log(3, total, "Asennetaan torch + chatterbox-tts + riippuvuudet (~5 GB)")
    install_python_packages(venv_py)

    if args.skip_models:
        log(4, total, "HuggingFace-mallien lataus ohitettu (--skip-models)")
    else:
        log(4, total, "Ladataan Chatterbox-mallit HuggingFacesta (~7 GB)")
        prefetch_models(venv_py)

    if args.skip_patch:
        log(5, total, "Gemination-korjaus ohitettu (--skip-patch)")
    else:
        log(5, total, "Sovelletaan suomen gemination-korjaus")
        apply_gemination_patch(args.venv_path)

    print("[done] Chatterbox-asennus valmis.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
