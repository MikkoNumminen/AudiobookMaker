#!/usr/bin/env python3
"""Post-install sanity check for the `audiobookmaker-cli` command.

Run this right after `pip install -e .` to confirm the CLI shim is reachable
and not shadowed by the installed GUI. Exits non-zero (1) if the canonical
`audiobookmaker-cli` command cannot be found on PATH, so it can gate a
bootstrap script.

    python scripts/check_cli_install.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running straight from a clean checkout (before/without an install).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cli.install_check import CANONICAL, diagnose  # noqa: E402

_ICON = {"ok": "OK ", "warning": "WRN", "error": "ERR"}


def main() -> int:
    checks = diagnose()
    shim_ok = True
    for c in checks:
        print(f"[{_ICON.get(c['status'], '???')}] {c['name']:<16} {c['detail']}")
        if c["name"] == "cli:shim" and c["status"] != "ok":
            shim_ok = False

    print()
    if not shim_ok:
        print(
            f"FAIL: `{CANONICAL}` is not resolvable on PATH.\n"
            "See the Troubleshooting section of docs/CLI_CHEATSHEET.md."
        )
        return 1
    print(f"OK: `{CANONICAL}` is installed and resolvable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
