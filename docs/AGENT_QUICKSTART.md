# Agent quick-start — the one-screen version

A scannable index for an AI session (or new contributor) starting work here.
The authoritative rules live in [`CLAUDE.md`](../CLAUDE.md); this is the fast
path, not a replacement. When in doubt, the linked file wins.

## First 60 seconds on a fresh clone

1. Python 3.11+, ffmpeg, deps — see [`DEVELOPER_SETUP.md`](DEVELOPER_SETUP.md).
2. `bash scripts/install-hooks.sh` — wires the commit gates (and every
   worktree). Skipping it means your commits bypass the guardrails.
3. `pytest tests/` — the suite. `test_project_git_hooks_are_active` fails loud
   if step 2 was skipped.

## The hard rules you cannot break (full text in `CLAUDE.md`)

- **No third-party copyrighted material** anywhere pushed — code, docs, tests,
  commit messages, PR text. Source inputs live in gitignored `.local/`. P0.
- **No vendor branding in git history** — the AI tool's product/company names
  must not appear in any commit subject, body, trailer, tag, or PR text. The
  `commit-msg` hook enforces it (and a CI re-scan catches `--no-verify`).
- **`TODO.md` is local-only** — gitignored, never staged, never referenced in
  a commit. The pre-commit hook blocks it.
- **All dev I/O goes under `.local/`** — never the repo root, `out/`, or
  `dist/`. See the `.local/` layout in `CLAUDE.md`.
- **One heavy ML subprocess at a time** — analyze / clone / train / synthesize
  each load ~6 GB VRAM; two concurrent runs freeze the box.
- **AI-readable files stay neutral about the user** — record preferences and
  behaviour, never emotional state. Enforced by
  [`test_no_subjective_user_state.py`](../tests/test_no_subjective_user_state.py).

## Where things live

| Need | Look in |
|---|---|
| Project rules | [`CLAUDE.md`](../CLAUDE.md) |
| How the AI-first setup works | [`AI_FIRST_GUIDE.md`](AI_FIRST_GUIDE.md) |
| Coding / commit / release conventions | [`CONVENTIONS.md`](CONVENTIONS.md) |
| Runbooks for repeated work | [`.claude/skills/`](../.claude/skills/) |
| CLI reference (auto-generated) | [`CLI.md`](CLI.md) |
| Which memory notes migrated to docs | [`MEMORY_MIGRATIONS.md`](MEMORY_MIGRATIONS.md) |
| Runtime architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) |

## Running tests

- `pytest tests/` runs everything; a module's tests mirror its `src/` name
  (`pytest tests/test_tts_audio.py`).
- `slow`-marked tests run a real engine; the pre-commit hook skips them.
- `tests/conftest.py` blocks outbound network — a hung "downloading" test means
  a missing mock, not a slow link.
- GUI tests need a display: they run in CI on Linux (xvfb) and Windows, and
  locally on a Mac with a display.

## Before you commit

The pre-commit hook does this for you, but the checklist is: tests green ·
`docs/CLI.md` in sync (`python scripts/render_cli_help.py` if not) · skill
catalogs match the skills directory · no `TODO.md` / `.local/` staged · commit
message free of vendor branding · diff scanned for copyright leaks.

## Running a sub-agent / worktree safely

`Agent({isolation: "worktree"})` isolation is a hint, not a guarantee, and
read-only `Explore` agents still have `Bash` — they can mutate the main
checkout. Before trusting a multi-agent run and after it finishes:

1. Snapshot first: `git status`, `git worktree list`, current branch + tip.
2. After: re-check all three. The main tree must be clean of files the agents
   touched; the branch tip must equal what you pushed.
3. If it leaked: restore from the known-good commit
   (`git checkout <sha> -- <file>` / `git reset --hard <sha>`), remove stray
   worktrees (`git worktree remove --force`) and branches, `git worktree prune`.

Full rule: `CLAUDE.md` → "Worktree isolation is a hint, not a guarantee".

## See also

- [`CLAUDE.md`](../CLAUDE.md) — the authoritative rules.
- [`AI_FIRST_GUIDE.md`](AI_FIRST_GUIDE.md) — the four pillars in depth.
