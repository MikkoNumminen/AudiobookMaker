"""Pin the CLI console-script registration in pyproject.toml.

The installed Windows app ships `AudiobookMaker.exe`, which on the case-
insensitive Windows filesystem case-insensitively shadows a bare
`audiobookmaker.exe` console script registered by `pip install -e .`. The
hyphenated `audiobookmaker-cli` alias was added so the cheatsheet command
always lands on the CLI even on machines that have both installed.

This test reads pyproject.toml directly (instead of `importlib.metadata
.entry_points()`, which only sees the live install of the current
interpreter and would be a no-op in CI when the package isn't installed).
A regression here — someone deleting either alias — fails immediately
in pre-commit and CI, not silently months later when a Windows user
follows the cheatsheet.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires py>=3.11
    import tomli as tomllib  # type: ignore[no-redef]


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
_CLI_ENTRY = "src.cli.__main__:main"


def _scripts() -> dict[str, str]:
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["scripts"]


def test_both_console_scripts_point_at_cli_main():
    scripts = _scripts()
    assert scripts.get("audiobookmaker-cli") == _CLI_ENTRY, (
        "audiobookmaker-cli is the canonical CLI command (hyphen avoids the "
        "case-insensitive collision with the installed app's AudiobookMaker.exe "
        "on Windows). Do not remove this entry."
    )
    assert scripts.get("audiobookmaker") == _CLI_ENTRY, (
        "audiobookmaker is kept as a back-compat alias. Do not remove this entry."
    )


def test_no_other_console_script_shadows_the_cli_main():
    """Guard against a third entry pointing at the same callable under a
    name that could be mistaken for the canonical command."""
    scripts = _scripts()
    matching = {name for name, target in scripts.items() if target == _CLI_ENTRY}
    assert matching == {"audiobookmaker", "audiobookmaker-cli"}, (
        f"Expected exactly two console scripts pointing at {_CLI_ENTRY}; "
        f"got {sorted(matching)}"
    )
