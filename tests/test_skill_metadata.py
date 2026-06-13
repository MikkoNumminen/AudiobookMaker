"""Every committed skill's SKILL.md must carry valid, load-bearing frontmatter.

A skill's frontmatter is an interface, not decoration: the harness reads
`name` to address the skill and `description` to decide when to trigger it.
`tests/test_skill_evals.py` already validates each skill's `evals/evals.json`
strictly, but nothing checked the SKILL.md frontmatter itself — so a corrupted
`name`, a slug that drifts from its directory, or an emptied `description`
would pass CI silently and quietly break discovery/invocation.

This test closes that gap with the same per-skill parametrization the rest of
the docs-integrity suite uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))

# Match a leading YAML frontmatter block: --- ... --- at the very top.
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    assert m, (
        f"{skill_md.relative_to(REPO_ROOT)} has no leading YAML frontmatter "
        f"(must start with a '---' block)"
    )
    data = yaml.safe_load(m.group(1))
    assert isinstance(data, dict), (
        f"{skill_md.relative_to(REPO_ROOT)} frontmatter is not a YAML mapping"
    )
    return data


def test_skill_files_found() -> None:
    """Guard against the glob silently matching nothing (path rot)."""
    assert len(SKILL_FILES) >= 10, (
        f"found only {len(SKILL_FILES)} SKILL.md files under "
        f"{SKILLS_DIR.relative_to(REPO_ROOT)} — is the path right?"
    )


@pytest.mark.parametrize(
    "skill_md", SKILL_FILES, ids=[p.parent.name for p in SKILL_FILES]
)
def test_skill_name_matches_directory(skill_md: Path) -> None:
    """The frontmatter `name` is the skill's address — it must equal the dir."""
    fm = _frontmatter(skill_md)
    slug = skill_md.parent.name
    name = fm.get("name")
    assert name == slug, (
        f"{skill_md.relative_to(REPO_ROOT)}: frontmatter name {name!r} does "
        f"not match its directory {slug!r}. The harness addresses a skill by "
        f"its slug; a mismatch breaks invocation."
    )


@pytest.mark.parametrize(
    "skill_md", SKILL_FILES, ids=[p.parent.name for p in SKILL_FILES]
)
def test_skill_has_nonempty_description(skill_md: Path) -> None:
    """`description` is what the harness reads to decide when to trigger."""
    fm = _frontmatter(skill_md)
    desc = fm.get("description")
    assert isinstance(desc, str) and desc.strip(), (
        f"{skill_md.relative_to(REPO_ROOT)}: frontmatter is missing a "
        f"non-empty 'description'. Without it the skill cannot be triggered "
        f"on intent."
    )


@pytest.mark.parametrize(
    "skill_md", SKILL_FILES, ids=[p.parent.name for p in SKILL_FILES]
)
def test_skill_has_evals(skill_md: Path) -> None:
    """Every skill must ship an evals/evals.json — its only behavioural spec.

    test_skill_evals.py validates the *content* of an evals.json when present,
    but used to silently accept a skill that shipped none, so a skill could go
    permanently unspecced. Require the file here; test_skill_evals.py then
    checks it parses to the schema.
    """
    evals = skill_md.parent / "evals" / "evals.json"
    assert evals.exists(), (
        f"skill '{skill_md.parent.name}' has no evals/evals.json. Every skill "
        f"needs one (its behavioural spec); add it next to {skill_md.name}."
    )
