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

Voice-cloning and audiobook R&D uses copyrighted books, audiobooks, and
other third-party content as local testing inputs. **Nothing that is
itself a copyrighted source — or that identifies a specific copyrighted
source — ever gets pushed to GitHub.** Public attribution of which
audiobook, author, or narrator was used creates legal exposure that does
not exist if the repo only describes the pipeline generically. Treat a
leak the same severity as leaked secrets.

### What's allowed (the app's capability surface)

- **Capability claims are fine:** "AudiobookMaker reads EPUB, PDF, and
  TXT", "supports Finnish narration via Chatterbox-Finnish", "can clone
  a voice from a reference WAV". These describe what the tool does, not
  what third-party material you personally tested it with.
- **Generic placeholders in examples, tests, and docs:**
  `source_audio.m4b`, `book.epub`, "1h voice-pack sample",
  "user-supplied Finnish text", "two-narrator audiobook (male + female)".
- **Technical IDs:** `SPEAKER_00`, `CHAR_A`, "Narrator A", "Character X".
- **Library/engine names:** Chatterbox, pyannote, ECAPA, Whisper,
  Edge-TTS, Piper.

### What's forbidden (anywhere pushed — code, docs, tests, `TODO.md`, commit messages, PR titles/bodies, release notes, GitHub issues, wiki)

- **Source material files themselves.** Never commit the actual text,
  audio, EPUB, PDF, or any other copyrighted content you're testing
  with — even if it's small, even if it's "just for a quick regression
  test", even if the file is in Finnish forum content, even if the
  author "probably wouldn't mind". Fixture files in `tests/` use
  synthetic or public-domain text only.
- **Book, audiobook, or series titles** identifying a specific
  copyrighted work.
- **Real author or narrator names.**
- **Source-file paths that identify a work** (`D:/.../Some_Book.m4b`,
  `Rubicon_..._Holland,_Tom_2003_Anchor.epub`).
- **URLs** pointing at third-party copyrighted content.
- **Character / proper-noun names** drawn from copyrighted works.

### Workflow rules

- **Keep source material in untracked local paths only:** worktree root
  (already gitignored for new files you don't `git add`), `d:/tmp/`, or
  a path added to `.gitignore`. If you find yourself typing `git add
  some_book.epub`, stop.
- **Before every commit and every push, scan the diff** for book/
  audiobook titles, author/narrator names, identifying paths, third-party
  URLs, and any file that looks like source content by size or extension.
- **If a leak already landed on origin:** P0 — stop other work, scrub
  the tree via `gh api` Contents PUT/DELETE (works even when another
  Claude owns the main worktree — see `feedback_gh_api_merge_pattern.md`
  in memory), and ask the user before any history rewrite (destructive).

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
