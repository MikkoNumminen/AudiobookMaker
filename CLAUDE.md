# Claude Code instructions for AudiobookMaker

These instructions are loaded automatically at the start of every session,
including worktrees. They override default behavior.

## Task tracking — TODO.md is the ONLY task list

When the user says "todo":
1. `git pull --rebase origin master`
2. Read `TODO.md` in full
3. Report: status board, in-progress items, complete backlog summary

**Do NOT use the internal TodoWrite tool.** It is invisible to other Claude
sessions and creates confusion. ALL tasks — planned, in progress, blocked,
speculative — live in `TODO.md` on git.

When starting work: move the item to "In Progress" in `TODO.md`, update the
status board, commit + push.

When finishing: remove the item, set status to idle, commit + push.

Read `TODO.md`'s full rules section before your first action in any session.

## Auto-update is critical

The in-app auto-update button is the lifeline to existing users. A broken
update path is P0 — same severity as data loss. Fix the user's immediate
pain first, then build structural prevention. See `docs/CONVENTIONS.md`
"Auto-update is critical" section for the full policy.

## Commit style

- Small commits — one logical change each
- No AI mentions in commits (no Co-Authored-By, no "Claude", no "AI")
- Run tests before every commit (pre-commit hook handles this)
- Re-read TODO.md before every commit (`git pull` first)

## Communication

- Use English GUI label names in prose (Language, Engine, Voice, Convert),
  not the Finnish in-app strings (Kieli, Moottori, Ääni, Muunna)
- Barney-style educational tone in docs — plain language, no jargon
- Always ask before doing work outside the AudiobookMaker repo

## Key conventions

Read `docs/CONVENTIONS.md` for the full list. Highlights:
- Keep `cleanup.py` current when changing install/output paths
- Auto-update guarantees: SHA-256 in release notes + sidecar asset
- Maximize test coverage, enforce in CI, no shipping without passing tests
