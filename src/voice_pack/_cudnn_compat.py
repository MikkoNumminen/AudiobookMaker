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
this module exists to detect the state at runtime and *quietly fix it*
without making the developer remember a shell command.

Call :func:`ensure_no_duplicate_cudnn` once, early, from any entry point
that pulls in both ``ctranslate2`` (usually via ``faster_whisper``) and
``torch``. The function is idempotent, logs one short info line when it
actually performs a rename, and falls back to a loud stderr warning only
when the automatic rename can't proceed (e.g. the file is locked by
another process).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CUDNN_DLL = "cudnn64_9.dll"
_DISABLED_SUFFIX = ".disabled"


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


def _emit_manual_fallback(ct2_dll: Path, torch_dll: Path, reason: str) -> None:
    """Print the old-style actionable warning when auto-fix failed.

    Used when something (permissions, file locks, read-only FS) blocks
    the automatic rename. The developer still gets a copy-paste command
    so they can unblock by hand.
    """
    sidelined = ct2_dll.with_name(_CUDNN_DLL + _DISABLED_SUFFIX)
    lines = [
        "",
        "=" * 72,
        "voice_pack: duplicate cuDNN DLL detected — auto-fix FAILED",
        "=" * 72,
        "",
        f"Reason: {reason}",
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
        f'  mv "{ct2_dll}" "{sidelined}"',
        f'  powershell Rename-Item -LiteralPath "{ct2_dll}" -NewName "{_CUDNN_DLL}{_DISABLED_SUFFIX}"',
        "",
        "See docs/CONVENTIONS.md, section 'cuDNN duplicate DLL', for the",
        "full background.",
        "=" * 72,
        "",
    ]
    print("\n".join(lines), file=sys.stderr, flush=True)


def ensure_no_duplicate_cudnn() -> None:
    """Auto-rename the ctranslate2 cuDNN DLL when torch's copy is present.

    Windows-only. When both ``ctranslate2/cudnn64_9.dll`` and
    ``torch/lib/cudnn64_9.dll`` exist on disk, this renames the
    ctranslate2 one to ``cudnn64_9.dll.disabled`` so torch's full cuDNN 9
    suite wins the loader race. If a ``.disabled`` sidecar is already
    there from a previous run, the fresh duplicate (left behind by a
    ``pip install --upgrade ctranslate2``) is just deleted.

    Safety rules:

    * Only runs on Windows — the DLL name is Windows-only.
    * Only renames when BOTH files exist. If torch's copy is missing we
      leave ctranslate2's copy alone, because taking it away would
      orphan ctranslate2 with no cuDNN at all.
    * If the rename or delete fails (file locked, permissions, read-only
      FS) we fall back to the old stderr warning with the manual command
      so the developer can unblock by hand.
    * The whole thing is wrapped in try/except — a failure here must
      never crash the voice-pack pipeline.

    Safe to call from any number of entry points; after the first
    successful run the duplicate is gone and subsequent calls are
    no-ops.
    """
    try:
        # Not a Windows problem — the DLL name itself is Windows-only.
        if not sys.platform.startswith("win"):
            return

        ct2_dir = _package_dir("ctranslate2")
        torch_dir = _package_dir("torch")
        if ct2_dir is None or torch_dir is None:
            # One of the libraries isn't installed in this environment.
            # The user has bigger problems than a cuDNN collision; stay
            # silent.
            return

        ct2_dll = ct2_dir / _CUDNN_DLL
        torch_dll = torch_dir / "lib" / _CUDNN_DLL

        # Only act when BOTH copies exist. If torch's copy is missing,
        # removing ctranslate2's copy would leave the environment with
        # no cuDNN at all — worse than the original problem.
        if not (ct2_dll.exists() and torch_dll.exists()):
            return

        sidelined = ct2_dll.with_name(_CUDNN_DLL + _DISABLED_SUFFIX)

        if sidelined.exists():
            # A previous run already sidelined a copy. pip put a fresh
            # duplicate back; just drop it silently.
            try:
                ct2_dll.unlink()
            except OSError as exc:
                _emit_manual_fallback(
                    ct2_dll, torch_dll,
                    f"could not delete fresh duplicate ({exc!s})",
                )
            return

        # First time seeing the conflict — rename ctranslate2's copy
        # aside so torch's full suite wins.
        try:
            ct2_dll.rename(sidelined)
        except OSError as exc:
            _emit_manual_fallback(
                ct2_dll, torch_dll,
                f"rename failed ({exc!s})",
            )
            return

        print(
            f"voice_pack: sidelined duplicate cuDNN DLL -> {sidelined} "
            "(torch's cuDNN suite will be used)",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:  # pragma: no cover - defensive catch-all
        # Absolute last line of defense: never let this guard crash the
        # caller. If something truly unexpected happens, log it and move
        # on.
        print(
            f"voice_pack: cuDNN duplicate guard hit unexpected error: {exc!r}",
            file=sys.stderr,
            flush=True,
        )


__all__ = ["ensure_no_duplicate_cudnn"]
