"""check_spec_runner_imports.py — CI guard for runner/spec consistency.

AudiobookMaker bundles ``scripts/generate_chatterbox_audiobook.py`` inside
the frozen installer so the Chatterbox subprocess can be launched from the
installed app.  The script uses ``from src.<module> import ...`` at several
call sites, relying on the installer having placed the matching ``src/*.py``
source files into ``_internal/src/`` via the ``datas`` list in
``audiobookmaker.spec``.

Three times in the project's history a new ``from src.X import …`` line was
added to the runner WITHOUT a matching ``(os.path.join('src', 'X.py'), 'src')``
entry in the spec.  Each miss bricks the runner subprocess in frozen builds
with a silent ``ModuleNotFoundError``.

This script enforces the invariant automatically:

1. Parse ``scripts/generate_chatterbox_audiobook.py`` for every
   ``from src.<module> import`` and ``import src.<module>`` statement.
2. Parse ``audiobookmaker.spec`` for every ``src/*.py`` filename listed in
   the ``datas`` block.
3. Print any imported module that has no matching bundle entry and exit
   non-zero.  Exit 0 silently when everything is consistent.

Run it as a CI step BEFORE the PyInstaller build so a mismatch fails the
build rather than shipping a broken installer to users.
"""

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (resolved relative to this script's location so the script can be
# run from any working directory — as CI typically does).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNNER = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
_SPEC = _REPO_ROOT / "audiobookmaker.spec"


def _collect_runner_imports(path: Path) -> set[str]:
    """Return the set of ``src.<module>`` names imported by the runner script."""
    text = path.read_text(encoding="utf-8")
    modules: set[str] = set()

    # Match: from src.<module> import ...
    for m in re.finditer(r"^\s*from\s+src\.(\w+)\s+import\b", text, re.MULTILINE):
        modules.add(m.group(1))

    # Match: import src.<module>
    for m in re.finditer(r"^\s*import\s+src\.(\w+)\b", text, re.MULTILINE):
        modules.add(m.group(1))

    # Match: from src import name1[, name2[ as alias[, …]]]
    # Each name maps to src/<name>.py (or to a re-export from src/__init__.py,
    # which is bundled separately and intentionally does not trigger the
    # guard — see _collect_spec_bundled). The simple single-line form covers
    # everything the runner uses today; multi-line parenthesised import
    # blocks aren't currently used and aren't worth the regex complexity.
    for m in re.finditer(r"^\s*from\s+src\s+import\s+(.+)$", text, re.MULTILINE):
        names_text = m.group(1).split("#", 1)[0]  # strip trailing comment
        for chunk in names_text.split(","):
            # Take the imported name itself, before any "as alias" rename.
            name = chunk.strip().split(" as ", 1)[0]
            if name and name.replace("_", "").isalnum():
                modules.add(name)

    return modules


def _collect_spec_bundled(path: Path) -> set[str]:
    """Return the set of module names (stems) bundled as ``src/*.py`` in the spec.

    The spec contains lines of the form::

        (os.path.join('src', 'tts_engine.py'), 'src'),

    We extract the filename stem (``tts_engine``) from any such occurrence.
    The regex intentionally handles both single-quoted and double-quoted
    strings and ignores leading/trailing whitespace and comments.
    """
    text = path.read_text(encoding="utf-8")
    bundled: set[str] = set()

    # Match the filename literal inside os.path.join('src', '<name>.py')
    # Handles single and double quotes around both arguments.
    pattern = re.compile(
        r"""os\.path\.join\s*\(\s*['"]src['"]\s*,\s*['"](\w+)\.py['"]\s*\)"""
    )
    for m in pattern.finditer(text):
        bundled.add(m.group(1))

    return bundled


def main() -> int:
    if not _RUNNER.exists():
        print(f"error: runner script not found: {_RUNNER}", file=sys.stderr)
        return 1
    if not _SPEC.exists():
        print(f"error: spec file not found: {_SPEC}", file=sys.stderr)
        return 1

    imported = _collect_runner_imports(_RUNNER)
    bundled = _collect_spec_bundled(_SPEC)

    missing = sorted(imported - bundled)

    if not missing:
        # Silent success — keeps CI output clean on green builds.
        return 0

    for name in missing:
        print(
            f"error: scripts/generate_chatterbox_audiobook.py imports from "
            f"src.{name} but {name}.py is not bundled in audiobookmaker.spec",
            file=sys.stderr,
        )
    print(
        f"\nFix: add the following line(s) to the datas block in audiobookmaker.spec:",
        file=sys.stderr,
    )
    for name in missing:
        print(
            f"    (os.path.join('src', '{name}.py'), 'src'),",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
