#!/usr/bin/env bash
# Install the project's git hooks into .git/hooks/.
#
# Git hooks live outside the tracked tree (.git/ is per-clone), so a
# fresh clone has no project hooks until this script runs. Without
# hooks, commits skip the TODO.md/.local staging gates, the
# vendor-branding commit-message scan, the CLI doc sync check, and the
# test suite — all of which the project treats as load-bearing per
# CLAUDE.md.
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

mkdir -p "$ROOT/.git/hooks"

install_hook() {
    local name="$1"
    local src="$ROOT/scripts/$name"
    local dest="$ROOT/.git/hooks/$name"

    if [ ! -f "$src" ]; then
        echo "Error: source hook not found at $src" >&2
        exit 1
    fi

    # Prefer a symlink so future ``git pull`` updates flow into the live
    # hook without re-running this script. Fall back to a copy on
    # filesystems where symlinks are restricted (older Windows without
    # Developer Mode + unprivileged shell).
    local method
    if ln -sf "$src" "$dest" 2>/dev/null; then
        method="symlink"
    else
        cp "$src" "$dest"
        method="copy"
    fi
    chmod +x "$dest"
    echo "Installed $name hook ($method) at: $dest"
}

install_hook pre-commit
install_hook commit-msg

echo ""
echo "What the hooks do on every commit:"
echo "  1. pre-commit: blocks staged TODO.md and .local/ files"
echo "     (local-only / copyright P0 gates, pure shell)."
echo "  2. pre-commit: checks docs/CLI.md is in sync with the CLI"
echo "     parsers (runs scripts/render_cli_help.py --check)."
echo "  3. pre-commit: runs the test suite — skipped on pure docs"
echo "     commits to save the ~12 s startup."
echo "  4. commit-msg: blocks vendor-branding tokens in the commit"
echo "     message (CLAUDE.md P0)."
echo ""
echo "Python resolution: ABM_TEST_PYTHON > py -3 > .venv > python3."
echo "Without a usable interpreter the python gates warn and skip;"
echo "the shell gates always run."
echo ""
echo "To bypass the hook for a single commit (rarely correct):"
echo "  git commit --no-verify"
echo ""
echo "If you change scripts/pre-commit, re-run this script to refresh"
echo "the installed copy."
