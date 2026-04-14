# Conventions

Code style, commit format, branch hygiene, and the TODO.md protocol.
Read this once; it shouldn't change often.

## Python style

- Python 3.11+. Use modern syntax: `X | None`, `list[str]`, `dict[K, V]`,
  `match` statements where they help.
- PEP 8, 4-space indent, ~88-char soft line length (Black-ish, but no
  formatter is enforced — match the surrounding style).
- Type hints on every public function and method, every dataclass field,
  every public class attribute. Internal helpers should be hinted too if
  the types aren't obvious.
- Use `from __future__ import annotations` at the top of files where it
  buys you cleaner forward references.
- Docstrings: short, imperative ("Return …", "Raise … if …"). One line is
  often enough. No multi-paragraph essays.
- Comments: only when the *why* is non-obvious — a hidden constraint, a
  workaround, a subtle invariant. Don't restate the code.
- Naming: `snake_case` for functions and variables, `PascalCase` for
  classes, `_leading_underscore` for module-private. Test classes are
  `class TestX:` (pytest finds them).

## Imports

- Group: stdlib, third-party, `src.*`. Blank line between groups.
- Avoid wildcard imports.
- Heavy optional dependencies (`torch`, `piper`, `voxcpm`, `edge_tts`)
  go inside the function or method that uses them — keeps app startup
  fast and lets `check_status()` report missing-dep errors cleanly.
- `from src.X import Y` form preferred over `import src.X as X`.

## Error handling

- Validate at boundaries (user input, external APIs, file I/O). Trust
  internal code.
- Catch specific exceptions, not `except Exception:` — exceptions are
  load-bearing; broad catches hide regressions.
- For user-facing errors, route through `_STRINGS` so the message is
  bilingual. Never hardcode Finnish or English literals in
  `messagebox.showerror(...)` or `_fail(...)`.
- For developer-facing log lines, plain English in `_append_log(...)`
  is fine.

## Tests

- Every new feature ships with tests. Bug fixes ship with a regression
  test that fails on the old code.
- One file per `src/` module: `tests/test_<module>.py`.
- Test naming: `def test_<thing>_<expected_behavior>(...)`. Class
  groupings (`class TestSomething:`) for related tests are encouraged.
- Use `pytest` fixtures for shared setup. Common fixtures live in
  `tests/conftest.py` (`clean_registry`, etc.). Don't duplicate.
- Mock at the module boundary, not inside the unit under test —
  patch `src.tts_engine.normalize_finnish_text`, not `tts_engine`'s
  internal implementation details.
- GUI tests share one Tk root via `tests/test_gui_e2e.py::_shared_app`.
  Don't create new `Tk()` instances — Tkinter crashes the interpreter.
- Run before every commit: `python -m pytest tests/ -x -q --tb=short`
  (the pre-commit hook does this automatically).

## Commits

- One logical change per commit. If the diff has two unrelated edits,
  split them.
- Conventional Commits format:
  ```
  <type>(<scope>): <subject>
  ```
  Types in use: `feat`, `fix`, `refactor`, `test`, `docs`, `ci`,
  `chore`, `security`, `i18n`. Scope is the affected module or area
  (`gui`, `updater`, `normalizer`, `engine-installer`, `ci`, etc.).
- Subject in present-tense imperative, no trailing period, no capital:
  ```
  fix(updater): require SHA-256 in release notes before installing
  ```
- Body (optional) explains *why* the change was needed, not what — the
  diff already shows what.
- **No co-authors, no AI/Claude/assistant attribution, no tool
  signatures.** Anywhere. Not in commit messages, not in PR bodies, not
  in code comments. The repo's contributors list stays clean.
- **No `--no-verify`** unless you have an explicit reason to bypass
  the pre-commit test run, and even then write down why in the commit
  body.

## Branches

- Branch off `master`. Branch name: `kebab-case`, descriptive
  (`audit-fixes`, `dev-docs`, `fix-edge-timeout`).
- Worktrees go under `.claude/worktrees/<name>` so multiple developers
  (or agents) can work in parallel without stepping on each other's
  checkouts.
- Merge to master via fast-forward when possible; merge commit
  (`--no-ff`) when the branch tells a story worth preserving.
- Delete the remote branch after merge:
  `git push origin --delete <branch>`.

## Pull requests (when used)

- Title follows the same Conventional Commits format as commits.
- Body: bullet-list summary of what changed, "Test plan" section with
  a checklist of what was verified.
- Link the TODO.md item if any.

## TODO.md protocol

[TODO.md](../TODO.md) is the shared task list across all parallel
developers and agents. The file's own header lists the mandatory
rules — re-read it before starting any task. Highlights:

- **Move your task to "In Progress" with your name tag *before* you
  touch any files.** `[mikko, master]`, `[Claude 1, audit-fixes]`,
  etc. This is how parallel workers avoid collisions.
- **Don't pick up an item that already has an owner tag.** Pick
  something else or wait.
- **Don't remove your tag mid-task.** It stays in In Progress until
  the work is committed and pushed.
- **Every item has a size estimate** — 🟢 small, 🟡 medium, 🔴 large.
- **Items destined for an LLM use a model marker** — ⚡ Sonnet for
  mechanical tasks, 🧠 Opus for ones needing judgement.
- TODO.md is mixed Finnish/English — match the language the existing
  section uses.
- Remove items as soon as they're done. No "Recently Completed"
  archive — `git log` is the history.

## Docs

- Keep `DEVELOPMENT.md` short. It points to other docs; it doesn't
  duplicate them.
- `docs/ARCHITECTURE.md` owns the module map and Mermaid diagrams.
- `docs/CONVENTIONS.md` (this file) owns style and process.
- `docs/tts_text_normalization_cases.md` owns the normalizer test
  inventory.
- When you change a boundary the docs describe (a new engine, a new
  mixin, a renamed module), update the doc in the same commit. Drift
  is what kills doc usefulness.

## When to ask first

- Anything that touches the auto-update flow (`auto_updater.py`,
  `installer/setup.iss`, `cleanup.py`) — these run on every user's
  machine on every release. Mistakes are expensive.
- Anything that affects `master` directly without a branch.
- Anything that would create a `CLAUDE.md`, `.claude/` directory in
  the repo, or a co-authored commit. Don't.
