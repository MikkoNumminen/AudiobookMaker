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
  `<Book_Title>_<Author>_<Year>_<Publisher>.epub`).
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

## One canonical local tree — `.local/` (dev) and next-to-exe (frozen)

All dev-machine I/O lives under a single gitignored root: `./.local/`.
Source material and generated output share that root but are split into
clear subdirs so the layout stays readable:

- **`.local/sources/`** — input source material (PDFs, EPUBs, audio
  inputs, reference clips you brought in by hand). Never push.
- **`.local/audiobooks/`** — synthesized MP3 output (the canonical dev
  output dir; `synthesis_orchestrator.default_output_dir()` resolves
  here in dev mode).
- **`.local/voice_runs/`** — voice-clone analyze / diarize / pack runs.
- **`.local/voice_packs/`** — trained LoRA voice packs.
- **`.local/scratch/`** — one-off log files, intermediate `.txt`,
  prompt files, anything ephemeral that doesn't fit above.
- **`.local/clone_scratch/`** — voice-clone-from-file pipeline scratch
  (path is fixed in `src/gui_clone_voice.py` and a hygiene test;
  do not redirect).
- **`.local/archive/`** — old run dirs from past sessions that don't
  fit the canonical layout. Read-only by convention; new code never
  writes here.

Frozen mode (installed `.exe`) keeps writing output next to the running
`.exe` (install root), not into a `.local/` folder — end users expect
to find their audiobooks next to the application icon, not in a hidden
folder. Same `synthesis_orchestrator.default_output_dir()` function
handles both modes; do not bypass it.

**Do not** write generated files to:
- The repo root — no more `*.log`, `diagnostic_*.csv`, or ad-hoc
  `*_input.txt` scratch files scattered next to `README.md`.
- `out/` — the old dev output dir is gone; everything moved into
  `.local/audiobooks/`. Don't recreate it.
- `dist/` — reserved for the PyInstaller build pipeline (ffmpeg.exe
  input + frozen-exe output consumed by the installer). Never a runtime
  target. If you find a leaked scratch dir under `dist/`, move it to
  `.local/audiobooks/` and fix the write site.
- Sibling-to-input paths — don't auto-name an MP3 next to the source
  PDF just because the PDF was at the repo root.
- `~/Documents/AudiobookMaker/` — the old dev default. Replace with
  `default_output_dir()` when you touch that code next.

If code today writes somewhere else (e.g.
`synthesis_orchestrator.default_output_dir` returning
`~/Documents/AudiobookMaker`), that's a bug — fix it at the write
site, don't add a second output root to work around it.

`.local/` is the single dev-machine I/O root. `dist/` is for the
PyInstaller build pipeline. Frozen-mode output is next-to-exe at
runtime. Never blur the three.

## Auto-update is critical

The in-app auto-update button is the lifeline to existing users. A broken
update path is P0 — same severity as data loss. Fix the user's immediate
pain first, then build structural prevention. See `docs/CONVENTIONS.md`
"Auto-update is critical" section for the full policy.

## Resource discipline — never run two heavy ML pipelines at once

The voice-pack pipeline subprocesses (analyze, clone-voice, train,
synthesize) each load a stack of native models — faster-whisper-large-v3
+ pyannote-3.1 + Chatterbox = ~6 GB VRAM and ~2 GB RAM **per process**.
A 12 GB GPU has room for exactly one. Two concurrent runs swap-thrash
the GPU allocator into system RAM, peg the page file, and freeze the OS
(observed 2026-05-10 — multiple parallel Claude agents triggered
bisection probes simultaneously and brought the box down).

Hard rules for any session in this repo:

- **Only one** voice-pack analyze / synthesize / clone-voice subprocess
  per machine at a time. The chunked-analyze orchestrator
  (`src/voice_pack_chunked_subproc.py`) defaults to one CUDA worker for
  exactly this reason; do not raise the default without a CUDA semaphore.
- When spawning parallel Claude `Agent`s on this project, only one may
  run a voice-pack subprocess at a time. Either serialise the work
  inside one agent, or restrict GPU-using work to a single agent and
  give the others read-only / non-GPU tasks.
- Bisection / probe runs across multiple slice lengths MUST be
  sequential, not parallel — even if it takes longer.
- If a session starts and the GPU shows residual VRAM from a prior run
  that's stuck, surface the PIDs to the user and wait for the
  user's call before killing anything (per
  `feedback_never_kill_processes.md` in memory).

## Voice-extraction default — assume multiple speakers

When the user hands an audio file to "copy the voices" / "extract
voices" / "clone the speaker(s)" / similar phrasing:

1. **Default to multi-speaker.** Audio sources are typically podcasts,
   interviews, or conversations — treat single-speaker as the
   exception, not the rule. Always run diarization, even when the user
   only needs one voice; the count of detected speakers is information
   they want.
2. **Always go through the chunked analyzer**
   (`src.voice_pack_chunked_subproc.run_chunked_analyze`). It handles
   long sources, runs diarization, picks ref clips per speaker, and
   produces the canonical artefacts.
3. **Validate refs by transcript before declaring done.** For each
   picked ref clip, read the chunk's transcript text and confirm it
   matches the expected speaker role — interviewer-questions in the
   host clip, guest-answers in the guest clip, etc. pyannote returns
   N labels but does **not** guarantee per-chunk consistency on
   similar-timbre voices. Two ref clips whose transcripts both read as
   questions (or both as the same person's content) mean the diarizer
   conflated labels and the refs are unusable as-is. Observed
   2026-05-10 on a Finnish podcast: pyannote returned 2 labels, both
   labels held mixed audio, both ref clips landed on host-voice
   chunks.
4. **On bad labels, retry with `--diarizer ecapa`.** speechbrain
   ECAPA-TDNN + agglomerative clustering rescues runs where pyannote
   conflated similar-timbre speakers. If ECAPA also fails to separate,
   surface the issue and offer manual ref-segment selection from a
   transcript timestamp the operator picks by ear.
5. **Never assume the picker's pick is the right speaker.** The
   reference picker scores chunks by duration / position / RMS — it
   does not verify the chunk's diarized label is correct. Read the
   transcript text every time.

## Worktree isolation is a hint, not a guarantee

`Agent({isolation: "worktree"})` is supposed to give the agent its own
working tree. Observed 2026-05-10: a worktree-isolated agent's edits
showed up unstaged in the **main** checkout's `git status`, including a
revert of CI fixes another session had pushed. Always verify after the
agent runs (or is stopped):

1. `git status` in main MUST be clean of files the agent touched.
2. If it isn't, the isolation leaked. Inspect the diff before
   the next session does anything that might commit those changes.
3. Targeted `git checkout HEAD -- <leaked-file>` is safer than a blanket
   stash when other sessions are working in parallel — surgical reverts
   don't disturb their unrelated WIP.

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

## Fresh-clone setup for AI sessions

If a session is operating on a freshly-cloned repo (no `.git/hooks/pre-commit`
yet), run `bash scripts/install-hooks.sh` once. This wires the project's
pre-commit hook into `.git/hooks/` — without it, commits skip the
`docs/CLI.md` sync check and the test suite. The hook content lives at
`scripts/pre-commit` and is version-controlled; the install script
symlinks (or copies, on filesystems without symlink support) it into
place.

## AI-first surface (contributor-facing)

The contributor-facing explanation of how this project uses Claude Code
lives in `docs/AI_FIRST_GUIDE.md`. It walks through the four pillars
(this CLAUDE.md, auto-memory, skills, tests), the memory-to-docs
migration pattern, and the minimum viable shape for adopting the same
approach in another project. When a memory note matures into stable
project knowledge, migrate it into tracked docs (`docs/<topic>.md`)
rather than leaving it as per-user memory.
