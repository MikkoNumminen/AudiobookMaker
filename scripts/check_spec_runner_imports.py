"""check_spec_runner_imports.py — CI guard for runner/spec consistency.

AudiobookMaker bundles ``scripts/generate_chatterbox_audiobook.py`` inside
the frozen installer so the Chatterbox subprocess can be launched from the
installed app.  The runner is executed by the separate ``.venv-chatterbox``
interpreter and imports ``from src.<module> import ...`` from the bundled
``_internal/src/`` tree — so EVERY ``src`` module it can reach, directly or
transitively, must be present in the ``datas`` list of the specs that build
shipped artifacts.  PyInstaller's automatic dependency analysis does NOT help
here: that interpreter never consults the frozen PYZ, it reads ``src/*.py``
files straight off disk.

Four times in the project's history a ``from src.X import …`` was added
WITHOUT a matching ``(os.path.join('src', 'X.py'), 'src')`` datas entry,
bricking the runner with a silent ``ModuleNotFoundError``.  The most recent
(``tts_normalizer_fi_legal``, shipped broken in 3.15.0) was reachable only
*transitively* — runner → ``tts_normalizer`` → ``tts_normalizer_fi`` →
``tts_normalizer_fi_legal`` — so the old DIRECT-imports-only guard passed
green while the installer was broken.

This guard now walks the FULL TRANSITIVE CLOSURE of the runner's ``src``
imports to a fixpoint and checks it against the datas list of every spec that
ships a runner-bearing artifact.  A new ``src`` dependency anywhere in the
chain can no longer slip through.

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
_SRC_DIR = _REPO_ROOT / "src"
# Every spec that bundles the runner script + a hand-curated src datas list.
# Both ship to end users (GUI installer + CLI zip) and both read src/*.py off
# disk, so both must carry the runner's full transitive src closure.
_SPECS = [
    _REPO_ROOT / "audiobookmaker.spec",
    _REPO_ROOT / "audiobookmaker_cli.spec",
]


def _src_imports_in_text(text: str) -> set[str]:
    """Return every ``src.<module>`` name imported by a chunk of source text.

    Handles ``from src.<module> import …`` (the dominant form — the module
    name is on the import line regardless of how the imported names wrap),
    ``import src.<module>``, and the single-line ``from src import a, b`` re-
    export form.
    """
    modules: set[str] = set()

    # from src.<module> import ...
    for m in re.finditer(r"^\s*from\s+src\.(\w+)\s+import\b", text, re.MULTILINE):
        modules.add(m.group(1))

    # import src.<module>
    for m in re.finditer(r"^\s*import\s+src\.(\w+)\b", text, re.MULTILINE):
        modules.add(m.group(1))

    # from src import name1[, name2 [as alias] …]  (each name -> src/<name>.py)
    for m in re.finditer(r"^\s*from\s+src\s+import\s+(.+)$", text, re.MULTILINE):
        names_text = m.group(1).split("#", 1)[0]
        for chunk in names_text.replace("(", " ").replace(")", " ").split(","):
            name = chunk.strip().split(" as ", 1)[0].strip()
            if name and name.replace("_", "").isalnum():
                modules.add(name)

    return modules


def _transitive_src_closure(seed_path: Path) -> set[str]:
    """All ``src.<module>`` reachable transitively from ``seed_path``'s imports.

    Walks each imported ``src/<module>.py`` for its own ``src`` imports to a
    fixpoint, so a module pulled in only via a chain (runner ->
    tts_normalizer -> tts_normalizer_fi -> tts_normalizer_fi_legal) is caught.
    A ``src`` name with no ``.py`` on disk may be a subpackage
    (``src/<name>/__init__.py``); recurse into it but it isn't itself a datas
    ``.py`` entry.
    """
    closure: set[str] = set()
    worklist = list(_src_imports_in_text(seed_path.read_text(encoding="utf-8")))
    while worklist:
        mod = worklist.pop()
        if mod in closure:
            continue
        closure.add(mod)
        mod_file = _SRC_DIR / f"{mod}.py"
        if not mod_file.exists():
            pkg_init = _SRC_DIR / mod / "__init__.py"
            if not pkg_init.exists():
                continue
            mod_file = pkg_init
        for nxt in _src_imports_in_text(mod_file.read_text(encoding="utf-8")):
            if nxt not in closure:
                worklist.append(nxt)
    return closure


def _collect_spec_bundled(path: Path) -> set[str]:
    """Return the module stems bundled as ``src/*.py`` in a spec's datas block.

    Matches the filename literal inside ``os.path.join('src', '<name>.py')``,
    tolerating single or double quotes.
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"""os\.path\.join\s*\(\s*['"]src['"]\s*,\s*['"](\w+)\.py['"]\s*\)"""
    )
    return {m.group(1) for m in pattern.finditer(text)}


def main() -> int:
    if not _RUNNER.exists():
        print(f"error: runner script not found: {_RUNNER}", file=sys.stderr)
        return 1

    closure = _transitive_src_closure(_RUNNER)
    # Only flat src/<module>.py modules need a datas entry; subpackages and
    # names without a .py on disk are handled by other bundling mechanisms.
    required = sorted(m for m in closure if (_SRC_DIR / f"{m}.py").exists())

    failed = False
    for spec in _SPECS:
        if not spec.exists():
            print(f"error: spec file not found: {spec}", file=sys.stderr)
            failed = True
            continue
        bundled = _collect_spec_bundled(spec)
        missing = sorted(m for m in required if m not in bundled)
        if not missing:
            continue
        failed = True
        for name in missing:
            print(
                f"error: {spec.name}: the Chatterbox runner reaches src.{name} "
                f"(directly or transitively) but {name}.py is not bundled in "
                f"its datas list",
                file=sys.stderr,
            )
        print(f"\nFix: add to the datas block in {spec.name}:", file=sys.stderr)
        for name in missing:
            print(f"    (os.path.join('src', '{name}.py'), 'src'),", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
