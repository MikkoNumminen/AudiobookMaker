#!/usr/bin/env python3
"""Verify the human-readable skill catalogs match .claude/skills/.

The repo advertises its committed skills in two AI-facing docs — the
docs/skill_catalog.md table and the docs/AI_FIRST_GUIDE.md skill index.
Both must stay in sync with the actual .claude/skills/ directory: a skill
added to the tree but left out of the catalog (or removed from the tree but
left in the catalog) is silent drift. It has bitten this repo twice — the
catalogs said "10 skills" while 11 were committed, and engine-venv-triage
was missing from the index table.

Nothing caught it because (a) the catalog page was not in the docs-integrity
test's scan list, and (b) a catalog edit — or adding a new SKILL.md — is a
pure-markdown change, so the pre-commit hook's docs-only shortcut skips the
test suite for exactly these commits.

This check is pure stdlib (no project deps) so it can run UNCONDITIONALLY in
the pre-commit hook, the same way the docs/CLI.md sync check does — blocking
the drift at commit time even on a docs-only commit and even on an
interpreter without the project installed. The full test suite enforces the
same invariant via tests/test_docs_integrity.py (the CI backstop).

Usage:
    python scripts/check_skill_catalog.py            # exit 1 on drift
    python scripts/check_skill_catalog.py --check     # alias, same behaviour
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# Docs that maintain a human-readable catalog of the committed skills.
# The catalog table used to live in README.md and moved to docs/skill_catalog.md
# — it is a maintenance record, not something a user needs to install the app.
# The README now links to it and carries no rows, so scanning README here would
# fail on a missing count claim rather than catch anything.
CATALOG_DOCS = [
    REPO_ROOT / "docs" / "skill_catalog.md",
    REPO_ROOT / "docs" / "AI_FIRST_GUIDE.md",
]

# A catalog entry links a skill by its full path: `.claude/skills/<slug>/SKILL.md`.
# Bare backticked names (e.g. the "retired skills" sentence's `audit-followup`)
# deliberately do NOT match — prose mentions of retired or hypothetical skills
# must not register as catalog rows, or removing a skill cleanly would be
# impossible without scrubbing every narrative reference to it.
_SKILL_LINK_RE = re.compile(r"\.claude/skills/([A-Za-z0-9_-]+)/SKILL\.md")

# The catalogs each state a count in prose ("11 in-repo skills"). That number
# is exactly what drifted (README said 10), so verify it independently of the
# row set — a stale count with a correct row set is still a defect.
_COUNT_CLAIM_RE = re.compile(r"(\d+)\s+in-repo skills")


def actual_skill_slugs() -> set[str]:
    """The source of truth: every directory under .claude/skills/ with a SKILL.md."""
    return {p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")}


def check() -> list[str]:
    """Return a list of human-readable drift problems; empty means in sync."""
    actual = actual_skill_slugs()
    problems: list[str] = []
    if not actual:
        rel = SKILLS_DIR.relative_to(REPO_ROOT)
        return [f"no skills found under {rel} — is the path right?"]

    for doc in CATALOG_DOCS:
        rel = doc.relative_to(REPO_ROOT)
        if not doc.exists():
            problems.append(f"{rel}: catalog doc is missing")
            continue
        text = doc.read_text(encoding="utf-8")
        listed = set(_SKILL_LINK_RE.findall(text))

        for slug in sorted(actual - listed):
            problems.append(
                f"{rel}: skill '{slug}' exists under .claude/skills/ but is "
                f"not linked in the catalog"
            )
        for slug in sorted(listed - actual):
            problems.append(
                f"{rel}: catalog links skill '{slug}' but "
                f".claude/skills/{slug}/ does not exist"
            )

        claims = [int(n) for n in _COUNT_CLAIM_RE.findall(text)]
        if not claims:
            problems.append(
                f"{rel}: no \"N in-repo skills\" count claim found to verify "
                f"(expected one stating {len(actual)})"
            )
        for claimed in claims:
            if claimed != len(actual):
                problems.append(
                    f"{rel}: count claim says {claimed} in-repo skills but "
                    f"{len(actual)} exist"
                )
    return problems


def main(argv: list[str]) -> int:
    problems = check()
    if problems:
        print("Skill catalog is out of sync with .claude/skills/:")
        for p in problems:
            print(f"  - {p}")
        print()
        print("Fix the skill rows and the 'N in-repo skills' count in")
        print("docs/skill_catalog.md and docs/AI_FIRST_GUIDE.md to match the directory.")
        return 1
    print(f"Skill catalog is in sync ({len(actual_skill_slugs())} skills).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
