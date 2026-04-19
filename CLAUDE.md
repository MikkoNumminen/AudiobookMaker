# Claude Code instructions for AudiobookMaker

These instructions are loaded automatically at the start of every session,
including worktrees. They override default behavior.

## Task tracking — `TODO.md` is local-only, never tracked

`TODO.md` lives at the repo root and is gitignored. It is a per-machine
scratch list, not a shared multi-session coordination surface. **Never**
`git add` it, never reference it in a commit message, never let its
contents leak into a diff or a PR body.

When the user says "todo":
1. Read the local `TODO.md` if it exists.
2. Report its contents: current tasks, notes, backlog.
3. Do not attempt to sync with remote — there is no remote copy.

**Do not use the internal TodoWrite tool.** Keep all task notes in the
local `TODO.md` file. If the user wants tasks tracked, it goes there.

Before every commit, scan the staged diff for any accidental `TODO.md`
entry (e.g. if it was briefly un-ignored, or if a script wrote content
that mirrors task-list text). If found, stop and remove it.

## Commit messages — zero tolerance for AI-origin mentions

Never put `Claude`, `Anthropic`, `AI`, `agent`, `session`, `Co-Authored-By`,
or any variant of these in a commit subject, body, trailer, tag, or PR
description. The repo's public history must not reveal that automation
produced the changes. This rule has been broken before; every violation
requires a force-push to scrub. Before every `git commit`:

1. Draft the message.
2. Re-read it slowly, looking specifically for any of the forbidden
   strings above.
3. Only then commit.

If a violation already landed, treat as P0: rewrite history and
force-push immediately, then ask the user before doing anything else.

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

- **Keep source material in `.local/`.** The repo root has `.local/` as
  the one canonical directory for third-party source inputs (EPUBs,
  audiobooks, test texts, reference clips). Everything there is
  gitignored. Never add new source material outside `.local/`; never
  scatter copyrighted inputs at the repo root.
- **Before every commit and every push, scan the diff** for book/
  audiobook titles, author/narrator names, identifying paths, third-party
  URLs, and any file that looks like source content by size or extension.
- **If a leak already landed on origin:** P0 — stop other work, scrub
  the tree via `gh api` Contents PUT/DELETE (works even when another
  Claude owns the main worktree — see `feedback_gh_api_merge_pattern.md`
  in memory), and ask the user before any history rewrite (destructive).

## One canonical output directory — `out/` (dev) and next-to-exe (frozen)

All generated material — audiobook MP3s, synthesis logs, diagnostic
CSVs, stress-test outputs, scratch files from scripts — goes to **one
place, always**:

- **Dev mode:** `./out/` in the repo root. Gitignored.
- **Frozen mode (installed .exe):** next to the running `.exe` (install
  root). Users expect their files there.

**Do not** write generated files to:
- The repo root — no more `*.log`, `diagnostic_*.csv`, or ad-hoc
  `*_input.txt` scratch files scattered next to `README.md`.
- `dist/` — reserved for the PyInstaller build pipeline (ffmpeg.exe
  input + frozen-exe output consumed by the installer). Never a runtime
  target. If you find a leaked scratch dir under `dist/`, move it to
  `out/` and fix the write site.
- Sibling-to-input paths — don't auto-name an MP3 next to the source
  PDF just because the PDF was at the repo root.
- `~/Documents/AudiobookMaker/` — the old dev default. Replace with
  `./out/` when you touch that code next.

If code today writes somewhere else (e.g. `synthesis_orchestrator.default_output_dir`
returning `~/Documents/AudiobookMaker`), that's a bug — fix it at the
write site, don't add a second output root to work around it.

`out/` is for runtime and dev-work output. `.local/` is for local-only
input source material. `dist/` is for the PyInstaller build pipeline.
Never mix the three.

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
