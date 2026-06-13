"""A local clone must have the project git hooks active.

Fresh clones don't get hooks automatically — git won't run code on clone, by
design. Without them, commits skip the staging gates, the vendor-branding
scan, the CLI/skill-catalog sync checks, and the test gate; CI is the only
backstop. This test makes a missing install loud the first time the suite runs
locally and points at the one-command fix, converting the "I forgot to install
the hooks" bypass from silent to caught.

Skipped where hooks are neither present nor required:
  * CI — never commits, so hooks aren't installed and shouldn't be demanded.
  * No working .git (source export / tarball) — hooks don't apply.
  * git not on PATH — nothing to check.

Accepts either activation mechanism so it doesn't false-fail across the
core.hooksPath switch or on older symlink-based installs:
  * core.hooksPath points at a directory containing pre-commit + commit-msg
    (the current install-hooks.sh sets this to scripts/), or
  * the default .git/hooks/ holds them (legacy symlink/copy install).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK_NAMES = ("pre-commit", "commit-msg")


def _in_ci() -> bool:
    return any(os.environ.get(v) for v in ("CI", "GITHUB_ACTIONS"))


def _hooks_dir_has_all(d: Path) -> bool:
    return all((d / name).exists() for name in _HOOK_NAMES)


@pytest.mark.skipif(_in_ci(), reason="CI never commits; hooks not required there")
def test_project_git_hooks_are_active() -> None:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no .git (source export / tarball) — hooks not applicable")

    try:
        configured = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except FileNotFoundError:
        pytest.skip("git not on PATH")

    candidates: list[Path] = []
    if configured:
        p = Path(configured)
        # A relative core.hooksPath is resolved per working tree; for this
        # checkout that anchors at the repo root.
        candidates.append(p if p.is_absolute() else REPO_ROOT / p)
    candidates.append(REPO_ROOT / ".git" / "hooks")  # git's default location

    if any(_hooks_dir_has_all(d) for d in candidates):
        return

    pytest.fail(
        "Project git hooks are not active — commits would skip the staging "
        "gates, vendor-branding scan, and CLI/skill-catalog sync checks. "
        "Fix: bash scripts/install-hooks.sh"
    )
