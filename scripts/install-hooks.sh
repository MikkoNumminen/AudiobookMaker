#!/usr/bin/env bash
# Activate the project's git hooks for this clone — and every worktree.
#
# Git hooks live outside the tracked tree (.git/ is per-clone), so a fresh
# clone runs no project hooks until this is set up. Without hooks, commits
# skip the TODO.md/.local staging gates, the vendor-branding commit-message
# scan, the docs/CLI.md + skill-catalog sync checks, and the test suite — all
# load-bearing per CLAUDE.md.
#
# Mechanism: point core.hooksPath at the MAIN checkout's scripts/ directory,
# as an absolute path. The hooks there (scripts/pre-commit, scripts/commit-msg)
# are tracked executable (100755), so git can exec them directly. Versus the
# old symlink-into-.git/hooks/ approach this means:
#   * no copy/symlink step and no drift — editing scripts/pre-commit takes
#     effect immediately, on every platform (the old copy fallback on
#     symlink-less Windows went stale until re-run);
#   * EVERY git worktree runs the hooks. core.hooksPath lives in the shared
#     config, so every linked worktree under the parallel-session tree picks
#     it up with no per-tree install.
#
# Why absolute-to-main rather than the relative path "scripts": a RELATIVE
# core.hooksPath is resolved against each worktree's own root, so a worktree
# that does not have scripts/ checked out (created with --no-checkout, or
# sitting on a commit that predates scripts/) would make git find no hook and
# SILENTLY skip every gate. Pointing at the main checkout's scripts/ keeps the
# hooks running for all worktrees regardless of what they checked out. The one
# tradeoff: the path is absolute, so if you move/rename the clone directory you
# must re-run this script — test_project_git_hooks_are_active fails loudly if
# you forget.
#
# Usage:
#   bash scripts/install-hooks.sh
#
# Idempotent. Re-running just re-asserts the config.
#
# Works on Linux, macOS, and Windows under Git Bash.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "Error: not inside a git repository." >&2
    exit 1
}

# Resolve the MAIN worktree (first entry of `git worktree list`) so the hooks
# path is the same whether this script is run from the main clone or from a
# linked worktree, and so it never points at a linked worktree that could later
# be removed.
MAIN_ROOT="$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "$MAIN_ROOT" ] || MAIN_ROOT="$ROOT"   # fallback for git too old for --porcelain

for name in pre-commit commit-msg; do
    src="$MAIN_ROOT/scripts/$name"
    if [ ! -f "$src" ]; then
        echo "Error: source hook not found at $src" >&2
        exit 1
    fi
    # core.hooksPath execs the file directly, so it must be executable. Git
    # tracks the bit (100755), but restore it defensively in case a checkout
    # on a no-exec filesystem dropped it.
    chmod +x "$src" 2>/dev/null || true
done

git config core.hooksPath "$MAIN_ROOT/scripts"
echo "Set core.hooksPath = $MAIN_ROOT/scripts (absolute — shared by this clone"
echo "and every worktree). Active hooks: scripts/pre-commit, scripts/commit-msg."

# Tidy up hooks from the previous symlink-based installer. With core.hooksPath
# set, anything under .git/hooks/ is ignored, so a leftover symlink is dead
# weight. Only remove symlinks (our old install) — never a regular file, which
# could be a developer's own custom hook.
for name in pre-commit commit-msg; do
    legacy="$ROOT/.git/hooks/$name"
    if [ -L "$legacy" ]; then
        rm -f "$legacy" && \
            echo "Removed stale .git/hooks/$name symlink (superseded by core.hooksPath)."
    fi
done

echo ""
echo "What the hooks do on every commit:"
echo "  1. pre-commit: blocks staged TODO.md and .local/ files"
echo "     (local-only / copyright P0 gates, pure shell)."
echo "  2. pre-commit: checks docs/CLI.md is in sync with the CLI parsers."
echo "  3. pre-commit: checks the skill catalogs match the skills directory."
echo "  4. pre-commit: runs the test suite — skipped on pure-docs commits."
echo "  5. commit-msg: blocks vendor-branding tokens in the message"
echo "     (CLAUDE.md P0)."
echo ""
echo "Python resolution: ABM_TEST_PYTHON > py -3 > .venv > python3."
echo "Without a usable interpreter the python gates warn and skip;"
echo "the shell gates always run."
echo ""
echo "To bypass the hook for a single commit (rarely correct):"
echo "  git commit --no-verify"
