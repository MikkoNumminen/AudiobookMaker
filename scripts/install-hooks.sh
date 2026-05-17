#!/usr/bin/env bash
# Install the project's git hooks into .git/hooks/.
#
# Git hooks live outside the tracked tree (.git/ is per-clone), so a
# fresh clone has no project hooks until this script runs. Without
# hooks, commits skip the CLI doc sync check, the AI-mention and
# copyright scans, and the test suite — all of which the project
# treats as load-bearing per CLAUDE.md.
#
# Usage:
#   bash scripts/install-hooks.sh
#
# Idempotent. Re-running re-points the hook at the current script
# version, so updating ``scripts/pre-commit`` and re-running this
# is the canonical way to roll a hook change to the local clone.
#
# Works on Linux, macOS, and Windows under Git Bash. PowerShell-only
# users can either call this through Git Bash (which Git for Windows
# ships) or copy ``scripts/pre-commit`` to ``.git/hooks/pre-commit``
# manually.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "Error: not inside a git repository." >&2
    exit 1
}

SRC="$ROOT/scripts/pre-commit"
DEST="$ROOT/.git/hooks/pre-commit"

if [ ! -f "$SRC" ]; then
    echo "Error: source hook not found at $SRC" >&2
    exit 1
fi

mkdir -p "$(dirname "$DEST")"

# Prefer a symlink so future ``git pull`` updates flow into the live
# hook without re-running this script. Fall back to a copy on
# filesystems where symlinks are restricted (older Windows without
# Developer Mode + unprivileged shell).
if ln -sf "$SRC" "$DEST" 2>/dev/null; then
    method="symlink"
else
    cp "$SRC" "$DEST"
    method="copy"
fi

chmod +x "$DEST"

echo "Installed pre-commit hook ($method) at:"
echo "  $DEST"
echo ""
echo "What the hook does on every commit:"
echo "  1. Checks docs/CLI.md is in sync with the CLI parsers"
echo "     (runs scripts/render_cli_help.py --check)."
echo "  2. Runs the test suite — skipped on pure docs commits to"
echo "     save the ~12 s startup."
echo ""
echo "To bypass the hook for a single commit (rarely correct):"
echo "  git commit --no-verify"
echo ""
echo "If you change scripts/pre-commit, re-run this script to refresh"
echo "the installed copy."
