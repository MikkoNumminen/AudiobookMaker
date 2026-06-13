"""Docs-integrity gate: AI-facing instructions must not contain dead references.

CLAUDE.md and the committed skills are load-bearing interfaces: agents OBEY
them, so a reference to a file or module that no longer exists is a defect of
the same class as a broken import — except nothing used to catch it. A
2026-06-10 audit found ~15 dead references that had silently accumulated
(e.g. a mandatory voice-pipeline entry point pointing at a module deleted in
a refactor). This test makes that whole category fail at commit time, the
same way the source-guard tests do for code.

Scope: CLAUDE.md, docs/AI_FIRST_GUIDE.md, docs/CONVENTIONS.md, and every
.claude/skills/*/SKILL.md. Historical records (docs/audits/, RELEASES.md)
are deliberately NOT scanned — rewriting history to fix a reference would be
worse than the dead link.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Share the catalog-drift logic with the pre-commit checker rather than
# re-implementing it: the script runs unconditionally in the hook (even on
# docs-only commits), this test is the CI / full-suite backstop for the same
# invariant. One implementation, two enforcement points.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.check_skill_catalog import (  # noqa: E402
    actual_skill_slugs,
    check as check_skill_catalog,
)

SCANNED_DOCS = [
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "AI_FIRST_GUIDE.md",
    REPO_ROOT / "docs" / "AGENT_QUICKSTART.md",
    REPO_ROOT / "docs" / "MEMORY_MIGRATIONS.md",
    REPO_ROOT / "docs" / "CONVENTIONS.md",
    *sorted((REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")),
]

# Repo-relative path prefixes we validate. Anything else backticked (shell
# fragments, Windows paths, UI strings) is ignored rather than guessed at.
_CHECKED_PREFIXES = (
    "src/", "scripts/", "docs/", "tests/", "data/", "installer/",
    ".claude/", ".github/", "assets/",
)

# Bare backticked tokens (no slash) that clearly name a repo-root file.
# Deliberately NOT included: shorthand module names like `cleanup.py` that
# docs use for src/cleanup.py — bare names are human shorthand, not paths.
_ROOT_FILES_WHITELIST = {
    "CLAUDE.md", "README.md", "requirements.txt", "pyproject.toml",
    "audiobookmaker.spec", "pytest.ini", "LICENSE.txt",
}

# Placeholder paths used in teaching examples ("src/foo.py:42").
_PLACEHOLDER_PATHS = {"src/foo.py", "src/bar.py", "src/x.py"}

# References that are correct DESPITE not existing in a fresh checkout:
# gitignored by design, created lazily at runtime, or per-clone.
_ALLOWED_MISSING = {
    "TODO.md",                                   # local-only, gitignored
    "docs/pronunciation_corpus_fi.md",           # created by the skill on first report
    # Created per parallel session. _iter_path_refs strips a trailing slash, so
    # a doc writing `.claude/worktrees/` arrives here as `.claude/worktrees`;
    # list the stripped form or the exact-match never fires.
    ".claude/worktrees",
}
_ALLOWED_MISSING_PREFIXES = (
    ".local/",          # the gitignored local I/O tree
    ".git/",            # per-clone
    ".claude/worktrees/",
)

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\]\(([^)#\s]+)\)")
_DOTTED_SRC_RE = re.compile(r"`(src\.[A-Za-z_][\w.]*)`")


def _looks_like_repo_path(token: str) -> bool:
    if any(ch in token for ch in "*<>{}|$%~ \t") or "..." in token:
        return False
    if token.startswith(("http://", "https://")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", token):  # Windows drive paths are examples
        return False
    if "\\" in token:
        return False
    if token.startswith(_CHECKED_PREFIXES):
        return True
    return token in _ROOT_FILES_WHITELIST


def _is_allowed_missing(token: str) -> bool:
    if token in _ALLOWED_MISSING:
        return True
    return token.startswith(_ALLOWED_MISSING_PREFIXES)


def _iter_path_refs(text: str):
    for m in _BACKTICK_RE.finditer(text):
        token = m.group(1).strip().rstrip("/").rstrip(":,.")
        # Docs cite locations as path:123, path:symbol, or path::symbol —
        # validate the path part only.
        token = token.split("::")[0].split(":")[0]
        if token in _PLACEHOLDER_PATHS:
            continue
        if _looks_like_repo_path(token):
            yield token
    for m in _MD_LINK_RE.finditer(text):
        token = m.group(1).strip()
        # Markdown links are doc-relative; normalize ../ against docs/.
        token = token.removeprefix("./")
        while token.startswith("../"):
            token = token[3:]
        if _looks_like_repo_path(token):
            yield token


@pytest.mark.parametrize(
    "doc", SCANNED_DOCS, ids=[str(p.relative_to(REPO_ROOT)) for p in SCANNED_DOCS]
)
def test_no_dead_file_references(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    dead: list[str] = []
    for token in _iter_path_refs(text):
        if _is_allowed_missing(token):
            continue
        if not (REPO_ROOT / token).exists():
            dead.append(token)
    assert not dead, (
        f"{doc.relative_to(REPO_ROOT)} references files that do not exist: "
        f"{sorted(set(dead))}. Fix the reference (or, for a path that is "
        f"legitimately gitignored/lazily-created, add it to _ALLOWED_MISSING "
        f"in {Path(__file__).name})."
    )


@pytest.mark.parametrize(
    "doc", SCANNED_DOCS, ids=[str(p.relative_to(REPO_ROOT)) for p in SCANNED_DOCS]
)
def test_no_dead_module_references(doc: Path) -> None:
    """Dotted `src.*` references must resolve to a real module or package.

    Only the first segment after `src.` is checked (deeper segments are
    often attributes/functions, which a filesystem check can't validate
    without importing heavy modules).
    """
    text = doc.read_text(encoding="utf-8")
    dead: list[str] = []
    for m in _DOTTED_SRC_RE.finditer(text):
        dotted = m.group(1)
        top = dotted.split(".")[1]
        if not (
            (REPO_ROOT / "src" / f"{top}.py").exists()
            or (REPO_ROOT / "src" / top).is_dir()
        ):
            dead.append(dotted)
    assert not dead, (
        f"{doc.relative_to(REPO_ROOT)} references src modules that do not "
        f"exist: {sorted(set(dead))}"
    )


def test_scanned_docs_all_exist() -> None:
    """The scan list itself must not rot."""
    missing = [str(p) for p in SCANNED_DOCS if not p.exists()]
    assert not missing, f"docs-integrity scan list is stale: {missing}"
    # And it must actually cover every committed skill.
    skills = list((REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    assert len(skills) >= 10, "skill glob found suspiciously few skills"


# ── Skill-catalog completeness ──────────────────────────────────────────────
# test_no_dead_file_references catches a catalog row that points at a deleted
# skill (the link 404s). It does NOT catch the opposite, more common drift: a
# skill added to .claude/skills/ that nobody added to the catalog, or a count
# claim left stale. README.md is not even in SCANNED_DOCS. Both gaps let the
# "10 skills vs 11 committed" drift land twice. These tests close the loop via
# the same checker the pre-commit hook runs.


def test_skill_catalog_in_sync() -> None:
    """README and AI_FIRST_GUIDE skill catalogs must match .claude/skills/."""
    problems = check_skill_catalog()
    assert not problems, "Skill catalog drift:\n" + "\n".join(
        f"  - {p}" for p in problems
    )


def test_skill_catalog_checker_sees_the_skills() -> None:
    """Guard against the checker silently globbing nothing (path rot)."""
    assert len(actual_skill_slugs()) >= 10, (
        "check_skill_catalog found suspiciously few skills — the source-of-truth "
        "glob may be pointed at the wrong directory"
    )
