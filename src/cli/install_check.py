"""CLI install diagnostics — is `audiobookmaker-cli` actually reachable?

The single most-reported dev-setup failure is "the CLI command doesn't work",
and it has three distinct root causes that all look identical to the user:

1. `pip install -e .` was never run, so no console-script shim exists.
2. The shim landed in a Python Scripts/bin dir that isn't on PATH.
3. On Windows the installed GUI's `AudiobookMaker.exe` case-insensitively
   shadows a bare `audiobookmaker`, so the command silently launches the GUI.

These are PATH/packaging facts, not runtime exceptions, so nothing surfaces
them today. `diagnose()` returns check rows in the same shape `doctor` uses
(name/status/required/detail) so the `doctor` subcommand can fold them in and
`scripts/check_cli_install.py` can run them standalone right after an editable
install. Every row is advisory (required=False): if this code is running the
CLI clearly works in *this* shell — the point is whether it will work in a
fresh one.
"""

from __future__ import annotations

import json
import shutil
import sys
from importlib import metadata
from pathlib import PurePosixPath, PureWindowsPath

#: Canonical CLI command. The hyphen is load-bearing — see pyproject.toml.
CANONICAL = "audiobookmaker-cli"
#: Back-compat alias; the name that collides with the GUI on Windows.
BACKCOMPAT = "audiobookmaker"


def _in_venv() -> bool:
    """True when running inside a virtualenv / venv, not the base interpreter."""
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _looks_like_gui(resolved: str) -> bool:
    """True if `resolved` is the installed GUI app rather than a Python shim.

    The installer puts the GUI in its own ``AudiobookMaker\\`` directory, so
    the resolved bare ``audiobookmaker[.exe]`` is the GUI only when its parent
    directory is the app's own install folder. A pip console-script shim, by
    contrast, lives in a ``Scripts``/``bin`` dir *or* directly under a Python
    prefix (e.g. ``...\\Python311\\``) — using "not in Scripts/bin" as the
    test would false-positive on a shim that landed in the prefix root, which
    still dispatches to the CLI, not the GUI.

    Parse with the flavour that matches the path's separators rather than the
    host OS, so a Windows-style path is read correctly even on POSIX CI.
    """
    p = PureWindowsPath(resolved) if "\\" in resolved else PurePosixPath(resolved)
    if p.stem.lower() != "audiobookmaker":
        return False
    return "audiobookmaker" in p.parent.name.lower()


def _is_editable(dist: metadata.Distribution) -> bool:
    """True if `dist` was installed with `pip install -e .` (PEP 610)."""
    try:
        raw = dist.read_text("direct_url.json")
        if not raw:
            return False
        return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except Exception:  # noqa: BLE001 — absence/garbage just means "unknown"
        return False


def _row(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "required": False, "detail": detail}


def diagnose() -> list[dict]:
    """Return advisory check rows about CLI command reachability."""
    checks: list[dict] = []

    # 1. Is the canonical shim on PATH?
    cli_path = shutil.which(CANONICAL)
    checks.append(_row(
        "cli:shim",
        "ok" if cli_path else "warning",
        cli_path or (
            f"`{CANONICAL}` is not on PATH. Run `pip install -e .` in the repo, "
            "then make sure the printed Scripts/bin dir is on PATH."
        ),
    ))

    # 2. Which interpreter / venv is this? ("wrong Python" is a common cause.)
    checks.append(_row(
        "cli:python",
        "ok",
        f"{sys.executable} ({'venv' if _in_venv() else 'base/system'})",
    ))

    # 3. GUI shadow: does the bare name resolve to the GUI instead of the CLI?
    bare = shutil.which(BACKCOMPAT)
    if bare and _looks_like_gui(bare):
        checks.append(_row(
            "cli:gui_shadow",
            "warning",
            f"`{BACKCOMPAT}` resolves to the GUI app ({bare}), not the CLI. "
            f"Use `{CANONICAL}` — the hyphenated name cannot be shadowed.",
        ))
    else:
        checks.append(_row(
            "cli:gui_shadow",
            "ok",
            f"`{BACKCOMPAT}` -> {bare}" if bare
            else f"`{BACKCOMPAT}` is not on PATH (fine — use `{CANONICAL}`).",
        ))

    # 4. Is the package installed at all, and is it editable?
    try:
        dist = metadata.distribution("audiobookmaker")
    except metadata.PackageNotFoundError:
        checks.append(_row(
            "cli:package",
            "warning",
            "`audiobookmaker` is not installed in this interpreter. "
            "Run `pip install -e .` from the repo root.",
        ))
    else:
        kind = "editable" if _is_editable(dist) else "regular"
        checks.append(_row(
            "cli:package", "ok", f"audiobookmaker {dist.version} ({kind} install)",
        ))

    return checks


def shim_resolvable() -> bool:
    """True if the canonical CLI command is resolvable on PATH right now."""
    return shutil.which(CANONICAL) is not None
