"""Guard: ``pip install -e .`` registers the CLI console-script shims.

The dev CLI is invoked as ``audiobookmaker-cli`` (hyphenated — the bare
``audiobookmaker`` name is a back-compat alias that the installed
``AudiobookMaker.exe`` GUI shadows on Windows). Both names are declared in
``pyproject.toml`` under ``[project.scripts]``. If that table is renamed,
deleted, or its module target drifts, an editable install silently stops
producing a runnable ``audiobookmaker-cli`` and every "run it from a fresh
clone" instruction breaks with no test catching it.

This test does a real editable install into a throwaway virtualenv and
asserts both console-script shims land in the venv's scripts dir. It is
marked ``slow`` because building the editable wheel + spinning up a venv
costs ~10-40 s; the pre-commit hook runs ``-m "not slow"`` so it stays out
of the fast inner loop while still running in full CI / manual ``pytest``.

What this test deliberately does NOT do:

- It installs with ``--no-deps`` — a full install would resolve torch and
  the rest of the ML stack (gigabytes, minutes) and hang CI. The shims are
  written by the ``[project.scripts]`` table regardless of whether runtime
  deps are present, so ``--no-deps`` is sufficient to prove they exist.
- It does NOT execute ``audiobookmaker-cli --version``. The shim imports
  ``src.cli.__main__``, which pulls ``src.auto_updater`` /
  ``src.ffmpeg_path`` and (transitively) heavier modules. Asserting the
  shim FILE exists on the venv's scripts path is the contract under test;
  running it would re-introduce the heavy-import cost ``--no-deps`` avoids.
"""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Generous: a cold pip HTTP cache may re-download the PEP 517 build backend
# (setuptools + wheel) before it can build the editable wheel. A warm cache
# finishes in well under 15 s; the ceiling only guards a genuine hang. Kept
# just under the @pytest.mark.timeout below so the subprocess-level skip
# ("install timed out") fires before pytest-timeout kills the whole test.
_INSTALL_TIMEOUT_S = 240
# Per-test override of pytest.ini's global 60 s timeout — a real venv build +
# editable install legitimately exceeds 60 s on a cold cache, and the global
# limit would otherwise kill this slow test mid-install.
_TEST_TIMEOUT_S = 300


def _venv_scripts_dir(venv_root: Path) -> Path:
    """Return the venv's executables dir (Scripts on Windows, bin on POSIX)."""
    return venv_root / ("Scripts" if sys.platform == "win32" else "bin")


def _venv_python(venv_root: Path) -> Path:
    """Return the venv's interpreter path, cross-platform."""
    scripts = _venv_scripts_dir(venv_root)
    return scripts / ("python.exe" if sys.platform == "win32" else "python")


def _shim_path(scripts_dir: Path, name: str) -> Path:
    """Console-script path for *name* (``.exe`` suffix on Windows)."""
    return scripts_dir / (f"{name}.exe" if sys.platform == "win32" else name)


@pytest.mark.slow
@pytest.mark.timeout(_TEST_TIMEOUT_S)
def test_editable_install_creates_cli_console_scripts(tmp_path) -> None:
    """A ``--no-deps`` editable install must register both CLI shims.

    Builds a fresh venv, installs THIS repo editable (no runtime deps), and
    asserts the ``audiobookmaker-cli`` and ``audiobookmaker`` console scripts
    land in the venv scripts dir and are resolvable files.
    """
    venv_root = tmp_path / "venv"
    try:
        venv.create(venv_root, with_pip=True)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"could not create a venv: {exc}")

    venv_python = _venv_python(venv_root)
    if not venv_python.exists():
        pytest.skip(f"venv has no interpreter at {venv_python}")

    # --no-deps is ESSENTIAL: a full resolve pulls torch and the rest of the
    # ML stack (gigabytes) and would hang CI. The console-script shims are
    # written from [project.scripts] independent of runtime deps.
    cmd = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "-e",
        ".",
        "--no-deps",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_S,
        )
    except FileNotFoundError as exc:  # pragma: no cover - no pip in the venv
        pytest.skip(f"pip not available in the venv: {exc}")
    except subprocess.TimeoutExpired:  # pragma: no cover - slow/hung network
        pytest.skip(
            f"editable install timed out after {_INSTALL_TIMEOUT_S}s "
            "(likely a cold build-backend cache with no network)"
        )

    if proc.returncode != 0:
        combined = f"{proc.stdout}\n{proc.stderr}"
        # A cold pip cache with no outbound network can't fetch the PEP 517
        # build backend (setuptools/wheel). That's an environment limitation,
        # not a regression in this repo's packaging — skip rather than fail.
        offline_markers = (
            "Failed to establish a new connection",
            "No matching distribution found",
            "Could not find a version",
            "Temporary failure in name resolution",
            "network access blocked",
        )
        if any(marker in combined for marker in offline_markers):
            pytest.skip(
                "editable install could not fetch the build backend offline; "
                f"environment-limited, not a packaging regression:\n{combined}"
            )
        pytest.fail(f"`pip install -e . --no-deps` failed:\n{combined}")

    scripts_dir = _venv_scripts_dir(venv_root)

    cli_shim = _shim_path(scripts_dir, "audiobookmaker-cli")
    assert cli_shim.is_file(), (
        f"canonical CLI shim {cli_shim.name!r} was not created in {scripts_dir}; "
        "check the [project.scripts] table in pyproject.toml. "
        f"Scripts present: {sorted(p.name for p in scripts_dir.iterdir())}"
    )

    bare_shim = _shim_path(scripts_dir, "audiobookmaker")
    assert bare_shim.is_file(), (
        f"back-compat shim {bare_shim.name!r} was not created in {scripts_dir}; "
        "both entry points must stay declared in [project.scripts]. "
        f"Scripts present: {sorted(p.name for p in scripts_dir.iterdir())}"
    )
