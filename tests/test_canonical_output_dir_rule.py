"""Repo-hygiene tests for the canonical output-directory rule.

The rule (CLAUDE.md "One canonical output directory"): every piece of
generated material — MP3s, synthesis logs, diagnostic CSVs, stress-test
outputs, scratch files from scripts — lands in exactly one place:

- Dev mode: ``./out/`` under the current working directory (gitignored).
- Frozen mode: next to the running ``.exe`` (install root).

The rule has been broken before: ``default_output_dir()`` used to
return ``~/Documents/AudiobookMaker``; several scripts defaulted
``--out`` to ``dist/audiobook`` or ``dist/stress_test``; diagnostic
scripts wrote CSVs at the repo root.

These tests scan the tracked source tree for those specific
anti-patterns. They are deliberately narrow string matches — the goal
is to catch regressions of the exact violations we just fixed, not to
police every possible path-construction style. Loosen only if a real
false positive shows up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SRC_DIR = REPO_ROOT / "src"


# Files that legitimately mention a forbidden token for reasons other
# than defaulting output there (e.g. legacy-rescue paths in cleanup.py,
# the rule itself being spelled out in synthesis_orchestrator docstrings).
_DOCUMENTS_ALLOWLIST = {
    # Uninstall-time rescue destination for MP3s produced by pre-v3.3
    # installs that wrote to the user's Documents folder. Not a write
    # target for new runs.
    SRC_DIR / "cleanup.py",
}


def _tracked_py_files(root: Path) -> list[Path]:
    """Return every .py file under ``root``, recursively."""
    return sorted(root.rglob("*.py"))


@pytest.mark.parametrize(
    "forbidden",
    [
        '"dist/audiobook"',
        "'dist/audiobook'",
        '"dist/stress_test"',
        "'dist/stress_test'",
    ],
)
def test_no_dist_defaults_in_scripts(forbidden: str) -> None:
    """dist/ is reserved for the PyInstaller build pipeline.

    Runtime output must never default to a dist/ path; use out/
    instead. This caught three defaults in 2026-04 (diagnose_*,
    stress_test_*, generate_chatterbox_audiobook).
    """
    offenders: list[str] = []
    for py in _tracked_py_files(SCRIPTS_DIR):
        text = py.read_text(encoding="utf-8")
        if forbidden in text:
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"{forbidden} appears as a default in: {offenders}. "
        f"Use out/ instead — dist/ is for the PyInstaller build "
        f"pipeline only."
    )


def test_no_documents_audiobookmaker_write_defaults() -> None:
    """No source file should default output into ~/Documents/AudiobookMaker.

    That was the legacy dev default for ``default_output_dir()`` and
    it violated the canonical-output-dir rule. The only permitted
    occurrences are in the allowlist above (cleanup.py's legacy
    uninstall rescue) and docstrings/comments describing the rule
    itself.
    """
    # Written without embedded slashes so this test file itself doesn't
    # trip the scan when the offending string appears inline in another
    # test or comment elsewhere in the repo.
    forbidden_windows = "Documents" + "\\\\" + "AudiobookMaker"
    forbidden_posix = "Documents" + "/" + "AudiobookMaker"
    offenders: list[str] = []
    for root in (SRC_DIR, SCRIPTS_DIR):
        for py in _tracked_py_files(root):
            if py in _DOCUMENTS_ALLOWLIST:
                continue
            text = py.read_text(encoding="utf-8")
            if forbidden_posix in text or forbidden_windows in text:
                offenders.append(str(py.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"~/Documents/AudiobookMaker appears in: {offenders}. "
        f"Route output through src.synthesis_orchestrator.default_output_dir() "
        f"instead."
    )


def test_default_output_dir_dev_mode_is_cwd_out() -> None:
    """The canonical dev-mode output dir is ``<cwd>/out`` — nothing else.

    This is the behavioral anchor for the whole rule. If someone
    changes it, this test forces them to read the CLAUDE.md policy
    and update it consciously.
    """
    import sys

    from src.synthesis_orchestrator import default_output_dir

    # Guard against a stray sys.frozen attribute set by another test.
    was_frozen = getattr(sys, "frozen", False)
    try:
        if was_frozen:
            del sys.frozen  # type: ignore[attr-defined]
        result = default_output_dir()
    finally:
        if was_frozen:
            sys.frozen = True  # type: ignore[attr-defined]

    assert result == Path.cwd() / "out"
