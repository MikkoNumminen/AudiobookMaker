"""Runtime guard for the ctranslate2 / torch cuDNN DLL collision on Windows.

``ctranslate2`` ships a single-file ``cudnn64_9.dll`` (~266 KB) inside its
package directory. ``torch`` ships the full modular cuDNN 9 suite (eight
DLLs including the same ``cudnn64_9.dll``) under ``torch/lib/``. When both
exist on ``PATH`` / in the loader search order, Windows picks one of them
and the other's symbols disappear. The symptom is a faster-whisper / torch
call failing with::

    Could not load symbol cudnnGetLibConfig. Error code 127.

The fix is to rename ``ctranslate2``'s bundled copy aside so torch's full
suite wins. ``pip install`` reinstates the duplicate on every upgrade, so
this module exists to detect the state at runtime and tell the developer
what to do.

Call :func:`ensure_no_duplicate_cudnn` once, early, from any entry point
that pulls in both ``ctranslate2`` (usually via ``faster_whisper``) and
``torch``. The function is idempotent, has no side effects beyond a
``stderr`` print when the conflict is real, and returns silently if either
package isn't importable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CUDNN_DLL = "cudnn64_9.dll"


def _package_dir(name: str) -> Path | None:
    """Return the on-disk directory holding ``name``'s package, if any.

    Uses ``importlib.util.find_spec`` so we don't trigger the package's
    own import side effects. Returns ``None`` when the package isn't
    installed or its spec has no filesystem origin (namespace packages,
    zipimports, etc.).
    """
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parent


def ensure_no_duplicate_cudnn() -> None:
    """Warn loudly on stderr when both ctranslate2 and torch ship cuDNN.

    Detects the Windows-only state where ``ctranslate2/cudnn64_9.dll`` and
    ``torch/lib/cudnn64_9.dll`` are both present on disk. Emits an
    actionable message (paths of both DLL copies + the exact rename
    command) and returns. Never raises.

    Safe to call from any number of entry points; repeated calls produce
    repeated warnings, which is fine — the warning is stderr noise, not
    state.
    """
    # Not a Windows problem — the DLL name itself is Windows-only.
    if not sys.platform.startswith("win"):
        return

    ct2_dir = _package_dir("ctranslate2")
    torch_dir = _package_dir("torch")
    if ct2_dir is None or torch_dir is None:
        # One of the libraries isn't installed in this environment. The
        # user has bigger problems than a cuDNN collision; stay silent.
        return

    ct2_dll = ct2_dir / _CUDNN_DLL
    torch_dll = torch_dir / "lib" / _CUDNN_DLL
    if not (ct2_dll.exists() and torch_dll.exists()):
        return

    sidelined = ct2_dll.with_name(_CUDNN_DLL + ".disabled")
    lines = [
        "",
        "=" * 72,
        "voice_pack: duplicate cuDNN DLL detected",
        "=" * 72,
        "",
        "Both ctranslate2 and torch ship their own cudnn64_9.dll. Windows",
        "will load one of them and break the other, usually with:",
        "    Could not load symbol cudnnGetLibConfig. Error code 127.",
        "",
        f"  ctranslate2 copy: {ct2_dll}",
        f"  torch copy:      {torch_dll}",
        "",
        "Fix: rename the ctranslate2 copy aside so torch's full cuDNN 9",
        "suite wins. From a shell in the repo root, one of:",
        "",
        f"  mv \"{ct2_dll}\" \"{sidelined}\"",
        f"  powershell Rename-Item -LiteralPath \"{ct2_dll}\" -NewName \"{_CUDNN_DLL}.disabled\"",
        "",
        "pip will reinstate the duplicate the next time ctranslate2 is",
        "reinstalled or upgraded, so re-run the rename after any env",
        "rebuild. See docs/CONVENTIONS.md, section 'cuDNN duplicate",
        "resolution', for the full background.",
        "=" * 72,
        "",
    ]
    print("\n".join(lines), file=sys.stderr, flush=True)


__all__ = ["ensure_no_duplicate_cudnn"]
