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

## No third-party copyrighted material in the repo — P0

Voice-cloning R&D uses copyrighted audiobooks as local training inputs.
**Nothing identifying those sources is ever pushed to GitHub.** Public
attribution of which audiobook, author, or narrator was used for training
creates legal exposure that does not exist if the repo only describes the
pipeline generically. Treat a leak the same severity as leaked secrets.

Forbidden in any pushed artifact (`TODO.md`, code, docs, commit messages,
PR titles/bodies, release notes, GitHub issues, wiki):

- Book or audiobook titles (e.g. specific series names)
- Real author or narrator names
- Source-file paths that identify a specific work (`D:/.../Some_Book.m4b`)
- URLs pointing at third-party copyrighted content
- Character/proper-noun names drawn from copyrighted works

Allowed (these describe the pipeline, not the source):

- Generic placeholders: "1h voice-pack sample", "user-supplied Finnish
  text", "two-narrator audiobook (male + female)"
- Technical IDs: `SPEAKER_00`, `CHAR_A`, "Narrator A", "Character X"
- Library/engine names: Chatterbox, pyannote, ECAPA, Whisper

**Before every commit and every push, scan the diff.** If you spot any
forbidden item, scrub it before the push. If something already leaked
to origin, treat it as P0: stop other work, scrub via `gh api` Contents
PUT (or force-push a rewrite if the user approves rewriting history),
and document the leak class in a memory so the pattern isn't repeated.

Keep identifying details in **local-only** files: the untracked
worktree root, `d:/tmp/`, or a gitignored path. Never in a tracked file.

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
