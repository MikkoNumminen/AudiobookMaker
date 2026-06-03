---
name: copyright-scan
description: Scan a git diff (staged by default, or a named revision range / file set) for accidental third-party copyright leaks before they land on origin. Use this skill whenever the user says "scan the diff", "copyright check", "scan for leaks", "is this safe to push", "check this before I commit", "did I leak anything?", or whenever you are about to create a commit or push in this repo. CLAUDE.md marks copyright leakage as a P0 — this skill turns the manual scan ritual into one pass. Produces a structured pass/fail report with file:line citations and, on fail, the exact remediation path (edit vs. un-stage vs. P0 scrub-from-origin).
---

# copyright-scan

Audit a diff for third-party copyright leaks before it leaves the
working tree. Returns pass/fail with precise citations. Opinionated
about what counts as a leak — the rules are codified in
[CLAUDE.md](../../../CLAUDE.md) and this skill is the executable
version of that section.

## Why this skill exists

Voice-cloning and audiobook R&D uses copyrighted books, audiobooks, and
other third-party material as local testing inputs. Nothing that is
itself a copyrighted source — or that identifies a specific copyrighted
source — is allowed anywhere that gets pushed to GitHub (code,
docs, `TODO.md`, commit messages, PR titles/bodies, release notes,
issues, wiki).

The repo has been burned by this before. The memory index flags it as a
standing rule (`feedback_no_copyright_in_repo.md`,
`feedback_no_installing_copyright_derived_packs.md`). Scanning the
staged diff manually every time is error-prone; a checklist skill is
not.

## When to run it

Run this **before** every commit that touches files other than pure
source code, and always before `git push`. Specifically:

- The user says any of the trigger phrases in the frontmatter
  description.
- You are about to run `git commit` and the staged diff touches
  `.md`, `TODO.md`, any file under `.local/*` that somehow got added,
  any test fixture, any release-notes template, any script output.
- You are about to run `git push`.
- You are about to run `gh pr create`, `gh issue create`, or any
  `gh release` command (PR body / issue body / release notes are all
  public surfaces).
- The user has just generated audio output or dropped a source file
  into the repo and you are tidying up before a commit.

When in doubt, run it. False positives cost seconds; a real leak
costs a history rewrite + force-push.

## What counts as a leak

### Forbidden (anywhere pushed)

1. **Source material files themselves.** Never commit the actual
   text, audio, EPUB, PDF, or other copyrighted content, even if
   small, even if "just for a quick regression test", even if in
   Finnish forum content, even if the author "probably wouldn't mind".
   Source goes in `.local/` (gitignored). Test fixtures use synthetic
   or public-domain material only.
2. **Book, audiobook, or series titles** identifying a specific
   copyrighted work (e.g. `<Book Title>: <Subtitle>`,
   `<Series Name>`).
3. **Real author or narrator names** tied to a specific copyrighted
   work (e.g. `<Author Name>`, `<Narrator Name> narrator`).
4. **Source-file paths that identify a work**
   (`D:/.../<book_title>_<author>_<year>_<publisher>.epub`,
   `~/Downloads/<book_title>_<author>.m4b`).
5. **URLs** pointing at third-party copyrighted content (audible.com
   titles, goodreads book pages, specific epub download URLs, etc.).
6. **Character / proper-noun names** drawn from copyrighted works
   used as identifiers in code or examples (e.g. a test that names a
   voice `<copyrighted_character>_narrator`).
7. **Voice-pack directories registered into
   `~/.audiobookmaker/voice_packs/`** if the pack was trained on
   copyrighted source audio. Those packs stay in
   `.local/voice_packs/` only. See
   `feedback_no_installing_copyright_derived_packs.md` memory.

### Explicitly allowed (do NOT flag these)

- **Capability claims:** "AudiobookMaker reads EPUB, PDF, and TXT",
  "supports Finnish narration", "can clone a voice from a reference
  WAV".
- **Generic placeholders:** `source_audio.wav`, `book.epub`,
  `test_book.pdf`, `reference.wav`, `1h voice-pack sample`,
  "two-narrator audiobook".
- **Technical IDs:** `SPEAKER_00`, `CHAR_A`, `Narrator A`,
  `Character X`, `reader_00`, `reader_01`.
- **Library/engine names:** Chatterbox, pyannote, ECAPA, Whisper,
  Edge-TTS, Piper, VoxCPM2, pydub.
- **Public-domain demo clips already in the repo**: Aleksis Kivi's
  *Seitsemän veljestä* (1870), Edward Gibbon's *Decline and Fall of
  the Roman Empire* (1776). The README's Hear-it-first section
  references these deliberately; those authors died well over 70
  years ago so the works are public domain. A new reference to Kivi
  or Gibbon by name in new README copy is NOT a leak.
- **"Grandmom"** — the project's own canonical Chatterbox voice
  persona. `feedback_voice_pack_framing.md` and
  `project_grandmom_voice.md` establish this as project nomenclature,
  not a real narrator's identity.

## How to run it

### Default: scan the staged diff

```bash
git diff --staged --stat      # file list first, to bound scope
git diff --staged              # full content
git diff --staged --name-only  # path-only, for the filename check
```

### Alternative scopes (when the user asks explicitly)

- **Before push, scan everything since origin:**
  `git diff origin/master...HEAD`
- **A specific commit range:** `git diff A..B`
- **A specific path set:** `git diff --staged -- <paths>`
- **Unstaged too (paranoid check before `git add -A`):**
  `git diff HEAD`
- **A specific file at HEAD** (not a diff, full content): `cat <path>`
  — useful for release notes or a new README section.

### The seven checks

Run each against the diff content. Use the dedicated tools —
`Grep` for regex, `Read` for a whole file (use `Read(limit=…)` for
anything large; full reads are only for short release-notes / README
sections) — never `bash grep`.

**Fan out by default.** For diffs touching more than ~10 files, or
any pre-push scan against `origin/master...HEAD`, dispatch parallel
`Agent` calls with `subagent_type: "Explore"` — one agent per check
(or two adjacent checks per agent for very small diffs) — in a
single message. Each agent reports `PASS` / `WARN` / `FAIL` plus
the specific hits for its assigned check(s). The main run merges
verdicts and runs the **decision tree** below sequentially after
every agent returns — never parallelise the decision tree, the
verdict logic depends on the full finding set. For a tiny staged
diff (one file, < 200 lines), serial per-check is fine.

| # | Check | How |
|---|-------|-----|
| 1 | **Source-file extensions in the tree** | `git diff --staged --name-only` and flag any path matching `*.epub`, `*.m4b`, `*.mobi`, `*.azw3`. Also `*.pdf`, `*.wav`, `*.mp3`, `*.flac`, `*.m4a`, `*.ogg`, `*.txt` over ~20 KB, `*.csv` over ~50 KB. (Small, clearly synthetic fixtures under `tests/` are fine; large ones in the repo root or `data/` are suspect.) |
| 2 | **Identifying source paths in changed text** | Grep diff content for path patterns that look like `<Title>_<author>_<year>.<ext>` or long title-case filenames with underscores. Finnish variants too (e.g. `Kolme_muskettisoturia.epub`). |
| 3 | **Book / audiobook titles** | Grep for known copyrighted-era signals: quoted English phrases in title case, subtitle patterns (`: The X of Y`), series markers (`Book One`, `Volume III`), ISBN patterns (`\b97[89]\d{10}\b`). |
| 4 | **Real author / narrator names** | Harder — scan for `Firstname Lastname` patterns adjacent to words like `narrated by`, `author`, `narrator`, `read by`, `by <Name>`. Flag for user review; don't hard-fail on bare first+last name pairs since false-positive rate is high. |
| 5 | **URLs to copyrighted content** | Grep diff content for URL hosts likely to point at copyrighted material: `audible.com`, `audible.co.`, `goodreads.com/book/`, `amazon.*/dp/`, `libgen`, `z-lib`, storefront pages for specific titles. |
| 6 | **TODO.md accidental staging** | `git diff --staged --name-only` must not include `TODO.md`. Per CLAUDE.md, that file is gitignored; if it shows up staged, something is wrong with `.gitignore` or `git add -A` accidentally pulled it in. |
| 7 | **Forbidden AI-origin strings in commit message** | If a commit message is being drafted (via the user or via `git log -1` on a just-made commit), grep it for `Claude`, `Anthropic`, `AI`, `agent`, `session`, `Co-Authored-By` (case-insensitive). Any match = P0 violation; `feedback_no_ai_in_git.md`. |

Check 7 is orthogonal to copyright but shares the same blast-radius
profile and the same "must never land" property, so folding it into
the pre-push scan keeps the ritual to one pass.

## What to report

Structure the output as a single block with one section per check.
Keep it skimmable; the user will glance at it while staging the next
piece.

```
## Copyright scan — <scope, e.g. `git diff --staged`>

**Verdict:** PASS | FAIL | WARN (review required)

### Check 1 — Source-file extensions
<PASS with short reason, or FAIL with file paths>

### Check 2 — Identifying source paths
<PASS, or FAIL with file:line citations>

### Check 3 — Book / audiobook titles
<PASS, or WARN with file:line + the matched phrase>

### Check 4 — Real author / narrator names
<PASS, or WARN + what triggered>

### Check 5 — URLs to copyrighted content
<PASS, or FAIL with file:line + URL>

### Check 6 — TODO.md accidentally staged
<PASS, or FAIL>

### Check 7 — AI-origin strings in commit message
<PASS, or FAIL + offending tokens>

### Action required
<exact next step, per the decision tree below>
```

Every citation uses the markdown link format
`[filename.md:42](path/to/filename.md#L42)` — the VSCode runner
renders these as clickable jumps.

## Decision tree

### All checks PASS

Report PASS. Proceed with the commit / push. If the user invoked this
as part of a commit flow, continue to the commit step unassisted.

### Any WARN

Surface the warnings and ask the user to confirm each one is a
false positive **before** committing / pushing. Do not auto-proceed.
WARNs are common — many ambiguous phrases legitimately resemble leak
patterns — so don't treat WARN as failure, just as "the human has to
look at this."

### Any FAIL, not yet pushed

1. **Stop.** Do not commit. Do not push.
2. List the offending files and patterns exactly.
3. Recommend the fix shape:
   - Source file extensions (Check 1): move the file under `.local/`
     and un-stage. If it is already tracked (previously committed
     elsewhere), that's a P0 history-rewrite problem — escalate.
   - Path / title / URL references (Checks 2, 3, 5): edit the file
     to use the allowed generic placeholder.
   - TODO.md (Check 6): `git reset HEAD TODO.md` and verify
     `.gitignore` still covers it.
   - AI strings in message (Check 7): rewrite the commit message.
4. After the fix, re-run the scan to confirm PASS.

### Any FAIL, already pushed to origin

This is the P0 path. Do not "just fix forward" — the leak is already
public.

1. Stop all other work.
2. Ask the user before doing anything destructive. Rewriting history
   or force-pushing requires explicit approval
   (`feedback_ask_before_rebase.md`).
3. For a single-file leak with a clean fix, the preferred first
   move is the `gh api` Contents DELETE / PUT pattern — it does not
   require a local worktree and can run even while another Claude
   owns the main worktree (see `feedback_gh_api_merge_pattern.md` in
   memory).
4. History rewrite + force-push is a last resort and only with
   explicit approval for that specific rewrite.
5. After the scrub, scan again to confirm origin is clean.

## Running the scan

Concrete call order:

```bash
# 1. Scope
git status --short                      # what's actually staged
git diff --staged --stat                # file list + sizes

# 2. Filenames
git diff --staged --name-only
```

Then, for each check that reads diff content, call the `Grep` tool
with the diff as input rather than re-invoking bash grep. The
cleanest pattern is to pipe the diff to a temp file under
`.local/` (gitignored) and run `Grep` against it — but usually the
diff is short enough to read once via `git diff --staged` and
reason about directly.

Do not waste tool calls piping `git diff` through `bash grep` — use
the dedicated tools.

## Things NOT to do

- **Do not flag Kivi, Gibbon, or Grandmom.** These are
  project-canonical PD demos and nomenclature. See the "Explicitly
  allowed" list.
- **Do not scrub origin unilaterally.** Even on a confirmed FAIL
  that landed on origin, ask the user before any destructive action.
- **Do not widen scope beyond what the user asked for.** If they
  said "scan the staged diff", do that; do not also scan unstaged
  changes or the whole history.
- **Do not try to auto-fix a FAIL.** Report the fix *shape*; the
  user drives the edit. Auto-editing removes the sanity check that
  "this is actually a leak, not a false positive."
- **Do not skip Check 7 on docs-only commits.** AI-origin strings
  have landed in release notes before — same severity, same rule.
