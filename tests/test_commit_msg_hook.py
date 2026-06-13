"""Behavioural tests for the commit-msg hook (scripts/commit-msg).

The hook enforces two commit-message rules mechanically: no vendor branding,
and no co-author trailers. The hook itself was never tested, so a regression in
its regex would silently disarm the gate. These tests run the actual script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "commit-msg"

# The hook is a bash script. On Windows it runs through Git's bundled bash (the
# real hook path), but a plain `bash` from a pytest subprocess resolves to the
# WSL launcher stub (no distro -> exit 1), so we can't exercise it that way.
# The logic is platform-independent; Linux and macOS cover it.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="commit-msg is bash; invoking it via a plain `bash` subprocess is "
    "unreliable on Windows runners (WSL launcher stub). Covered on Linux/macOS.",
)


def _run(tmp_path: Path, message: str) -> int:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["bash", str(HOOK), str(msg_file)],
        capture_output=True,
        text=True,
    ).returncode


def test_clean_message_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, "feat(cli): add a doctor subcommand\n\nWith a body.\n") == 0


def test_vendor_token_blocked(tmp_path: Path) -> None:
    # 'claude' / 'anthropic' substrings are banned anywhere in the message.
    assert _run(tmp_path, "docs: note the assistant vendor Claude here\n") == 1
    assert _run(tmp_path, "chore: mention Anthropic in passing\n") == 1


def test_co_authored_by_trailer_blocked(tmp_path: Path) -> None:
    # A human co-author trailer must be blocked too (not just the vendor case).
    msg = "fix(audio): trim trailing silence\n\nCo-authored-by: Jane Dev <jane@example.com>\n"
    assert _run(tmp_path, msg) == 1


def test_comment_lines_are_ignored(tmp_path: Path) -> None:
    # git's commented help lines (and the diff in verbose mode) are not part of
    # the recorded message — a banned token there must not block the commit.
    msg = "feat: real subject\n\n# Co-authored-by: someone (this is a comment)\n# Claude\n"
    assert _run(tmp_path, msg) == 0
