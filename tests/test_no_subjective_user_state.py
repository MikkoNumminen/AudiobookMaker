"""AI-readable files must not characterize the user's emotional state.

CLAUDE.md ("AI-readable files — no subjective user-state characterization")
forbids phrasings like "user was furious" or "user seemed annoyed" in any file
AI tooling reads (docs, SKILL.md, READMEs, audit reports). Neutral preference
and behavioural records are fine ("the user prefers small commits", "user
reported a bug"); subjective internal-state characterization is not. The rule
was documentation-only — nothing caught a violation, so one could land and
shape future AI behaviour undetected. This test mechanizes it.

CLAUDE.md itself is exempt: it is the policy document and quotes the forbidden
phrasings to define them (the same narrow exception the vendor-branding rule
gets). docs/audits/ and RELEASES.md are historical records and not rewritten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The forbidden shape: "user" + a state verb + an internal-state word, close
# together (same clause). Anchored on the state verb so neutral phrasings
# ("the user prefers", "user reported") never match — only attributions of
# feeling do. Matches every example CLAUDE.md lists as forbidden.
_FORBIDDEN = re.compile(
    r"\buser('?s)?\b[^.\n]{0,15}\b"
    r"(was|is|wasn'?t|got|gets|seem(s|ed)?|appear(s|ed)?|felt|feels|became|"
    r"becomes|looked|looks|sounded|sounds|grew|seemed to be)\b[^.\n]{0,25}\b"
    r"(furious|frustrat\w*|annoy\w*|angry|mad|upset|displeas\w*|irritat\w*|"
    r"enrag\w*|livid|irate|exasperat\w*|disappoint\w*|unhappy|distress\w*|"
    r"agitat\w*|fed up|pissed)\b",
    re.IGNORECASE,
)


def _scanned_files() -> list[Path]:
    """AI-readable markdown, minus the policy doc and historical records."""
    files: list[Path] = [REPO_ROOT / "README.md"]
    for md in sorted((REPO_ROOT / "docs").rglob("*.md")):
        rel = md.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("docs/audits/") or rel.startswith("docs/upstream/"):
            continue  # historical / third-party — not rewritten
        files.append(md)
    files.extend(sorted((REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")))
    return [p for p in files if p.exists()]


_FILES = _scanned_files()


def test_scanned_set_is_nonempty() -> None:
    assert len(_FILES) >= 10, "subjective-state scan list looks empty — path rot?"


@pytest.mark.parametrize(
    "doc", _FILES, ids=[str(p.relative_to(REPO_ROOT)) for p in _FILES]
)
def test_no_subjective_user_state(doc: Path) -> None:
    hits = []
    for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if _FORBIDDEN.search(line):
            hits.append(f"  {doc.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    assert not hits, (
        "AI-readable file characterizes the user's emotional state (forbidden "
        "by CLAUDE.md 'no subjective user-state characterization'). Use a "
        "neutral behavioural/preference record instead:\n" + "\n".join(hits)
    )
