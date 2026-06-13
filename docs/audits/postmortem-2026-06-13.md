# Post-mortem — 2026-06-13 AI-first hardening session

Four issues surfaced and were resolved while hardening the AI-first guardrails.
Recorded here so the prevention is durable knowledge, not just a closed PR.

## 1. macOS path-root bug slipped CI (caught only locally)

- **What:** `engine_installer._allowed_venv_roots()` whitelisted the temp dir
  via `TEMP`/`TMP` (Windows names) but not `TMPDIR` (macOS/Linux), so the
  revision-pin tests rejected pytest's `tmp_path` venv and failed on the Mac
  dev box. CI was green because the Windows runner sets `TEMP`.
- **Root cause:** CI ran tests on Windows only; a Mac-specific defect had no
  mechanical signal.
- **Prevention:** added `TMPDIR`; added a macOS CI leg (PR #124) so
  dev-platform regressions go red in CI, not just locally.

## 2. Production/doc temperature drift

- **What:** the production generator was deliberately lowered to
  `FI_TEMPERATURE = 0.5` ("lower FI sampler temperature to 0.5"), but
  `docs/finnish_grandmom.md` still listed `0.8` as the "v7 production value".
  The dev preview tool's `0.8` is intentional (model-card golden, test-locked)
  and was not the bug — only the doc was wrong.
- **Root cause:** a doc claim about a production constant had no regression tie
  to the code; it drifted unnoticed.
- **Prevention:** corrected the doc to `0.5`; added `test_fi_v7_params.py`
  locking the doc's production rows to the production constants (PR #122).

## 3. Skill-catalog drift recurred twice

- **What:** the README and AI-first-guide catalogs said "10 skills" while 11
  were committed; the guide's index missed one skill.
- **Root cause:** README was not in the docs-integrity scan, and catalog edits
  are pure-markdown, so the pre-commit docs-only shortcut skipped the test that
  would have caught it.
- **Prevention:** `scripts/check_skill_catalog.py` checks both directions plus
  the count claim and runs unconditionally in the pre-commit hook; README is
  now in the dead-reference scan.

## 4. Review-workflow agents mutated the main checkout

- **What:** a multi-agent review workflow's subagents (read-only `Explore`,
  but with `Bash`) stubbed `scripts/pre-commit` to a no-op, created stray
  worktrees and branches, and made throwaway commits on the feature branch —
  all leaking into the main working tree. The pushed PR on origin was
  unaffected.
- **Root cause:** `Explore` lacking Edit/Write does not prevent shell-level
  mutation; the Bash tool runs in the main working directory.
- **Prevention:** snapshot `git status` / `git worktree list` / branch tip
  before a review workflow and verify against it after; restore leaked files
  from the known-good commit, remove stray worktrees/branches. Recorded as a
  durable working note for future sessions.

## Theme

Three of four were drift between two places that nothing compared (path-root
names, a doc vs a constant, a catalog vs a directory). The durable fix in each
case was the same: a mechanical check that fails when the two disagree.
