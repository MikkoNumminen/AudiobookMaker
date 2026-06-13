#!/usr/bin/env python3
"""Run the GUI test suite and hard-exit to dodge the Windows Tk teardown hang.

On headless GHA Windows a module-level ``import tkinter`` starts a Tcl notifier
thread that ordinary interpreter shutdown cannot join, so a plain
``pytest tests/test_gui_*.py`` hangs AFTER the summary prints — the tests
themselves run to completion, only process exit stalls, and the job ends only
at the runner timeout. That is exactly why the GUI files are excluded from the
Windows "Build and Release" run.

Running pytest in-process and then calling ``os._exit()`` skips the atexit /
thread-join phase entirely, so the process exits immediately with pytest's real
status code. Used by ``.github/workflows/gui-tests-windows.yml``. It is
platform-agnostic and harmless elsewhere (on macOS/Linux there is no hang to
dodge; the hard exit just skips a no-op teardown).

Usage:
    python scripts/run_gui_tests.py            # all tests/test_gui_*.py
    python scripts/run_gui_tests.py tests/test_gui_unified.py [...]  # a subset
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _targets(argv: list[str]) -> list[str]:
    if argv:
        return argv
    return [str(p) for p in sorted((REPO_ROOT / "tests").glob("test_gui_*.py"))]


def main(argv: list[str]) -> int:
    targets = _targets(argv)
    if not targets:
        print("run_gui_tests: no GUI test files found", file=sys.stderr)
        return 1
    # -p no:timeout: pytest-timeout's thread method cannot cleanly cancel its
    # timer on Windows and can crash the run at shutdown; the workflow's
    # timeout-minutes is the runaway guard instead.
    args = [*targets, "-q", "--tb=short", "-p", "no:timeout"]
    return int(pytest.main(args))


if __name__ == "__main__":
    rc = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # Bypass interpreter shutdown — and with it the un-joinable Tcl notifier
    # thread that otherwise hangs the process after the summary prints.
    os._exit(rc)
