---
name: pre-push-scan
description: Run every pre-push safety check in one pass before any git push — AI mention scan, copyright leak scan, accidental TODO.md content, Conventional Commits subject validation, across every commit in the push range. Use whenever the user says "is this safe to push", "scan before push", "ready to push", "push it", "let's ship this", or any time the session is about to call git push.
---

# Pre-push scan

One deterministic pass over every commit about to leave the local clone.
Four rules in `CLAUDE.md` all have to hold at push time; this skill
makes the machine enforce them instead of your memory.

Pairs with `commit-then-scan` (the per-commit equivalent). That skill
runs before `git commit`; this one is the final gate before `git push`.

## Why this skill exists

Every violation of the four rules below has occurred in this repo and
required destructive remediation:

- **No forbidden strings in commit messages.** The strings `Claude`,
  `Anthropic`, `AI`, `agent`, `session`, and `Co-Authored-By` must never
  appear in commit subjects, bodies, trailers, tags, or PR descriptions.
  Once they land on origin a history rewrite and force-push are required
  — destructive operations that need explicit user approval.
- **No copyrighted material.** Book titles, author names, narrator names,
  identifying source paths (e.g. `.m4b` or `.epub` under a named work),
  and copyrighted URLs must not appear anywhere in the diff.
- **No `TODO.md` content in the diff.** The file is gitignored. If it
  appears in a diff, something went wrong with the gitignore or a script
  wrote it outside `.local/`.
- **Conventional Commits.** Every commit subject is `type(scope): desc`.
  Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`,
  `chore`, `perf`, `ci`, `build`. No invented prefixes like `todo:` or
  bare `gui:`.

Running all four manually every push is how violations happen. Running
this skill instead means they cannot slip through.

## Step 1 — confirm the working tree is clean

```bash
git status --short
```

If there are unstaged or untracked modifications to tracked files, stop.
The scan covers only committed content; unreviewed changes sitting in the
working tree mean the scan is incomplete. Tell the user what is dirty and
ask whether to stash or commit the remainder first.

Exception: untracked files (the `??` prefix) that match `.gitignore`
patterns (e.g. `.local/`, `__pycache__/`) are fine — they cannot enter
the diff.

## Step 2 — determine the push range

For a branch that already tracks a remote:

```bash
git log @{u}..HEAD --oneline
```

For a new branch with no upstream yet:

```bash
git log master..HEAD --oneline
```

If the output is empty there is nothing to push; tell the user and stop.
Record the list of commit SHAs for the per-commit checks below.

## Step 3 — per-commit checks

Run these for every commit SHA in the push range.

### 3a. Forbidden string scan

Fetch the full commit message (subject + body + trailers):

```bash
git log -1 --format="%B" <sha>
```

Grep for each of the forbidden strings listed below. Match
case-insensitively so deliberate capitalisation variations do not sneak
through.

Forbidden patterns (quoted here so this file does not trip its own scan):

- `"[Cc]laude"`
- `"[Aa]nthropic"`
- `"\bAI\b"` (word-bounded to avoid matching "SAID", "MAID", etc.)
- `"\bagent\b"`
- `"\bsession\b"` — flag only when it appears in a trailer line
  (`Session: …`) or a `Co-Authored` context, not in normal prose
  about e.g. audio sessions
- `"[Cc]o-[Aa]uthored-[Bb]y"`

Any match is a FAIL. Record the commit SHA, the matching line, and the
offending string. Do not push.

### 3b. Conventional Commits subject validation

Fetch the subject line:

```bash
git log -1 --format="%s" <sha>
```

Validate against the regex:

```
^(feat|fix|docs|style|refactor|test|chore|perf|ci|build)(\([a-z0-9-]+\))?: .+[^.]$
```

Rules derived from the regex:

- Subject must start with an allowed type.
- Optional `(scope)` in parentheses; scope is lowercase kebab-case.
- Colon + space before the description.
- Description is present-tense imperative, no trailing period.
- No uppercase in the type or scope.

Any mismatch is a FAIL. Record the SHA and show the subject alongside
the expected format.

### 3c. TODO.md diff scan

Fetch the diff for the commit:

```bash
git show <sha> -- TODO.md
```

If this produces any output at all (added or removed lines), that is a
FAIL. `TODO.md` is gitignored. Its presence in any commit diff means
something bypassed the gitignore. Record the SHA.

### 3d. Identifying path scan

Fetch the full diff:

```bash
git show <sha>
```

Scan for patterns that suggest copyrighted source material slipped in:

- File paths ending in `.epub`, `.m4b`, `.mp3`, `.wav`, `.pdf` that are
  not under `tests/fixtures/` (which uses synthetic / public-domain
  content only).
- Paths containing `.local/` (gitignored; should never appear).
- Any path that looks like a named audiobook work — a proper noun
  followed by an author surname and a year, or a series name not
  already in the codebase.

Flag any match as a potential copyright leak. Some matches will be false
positives (e.g. test fixtures referencing generic filenames like
`source_audio.m4b`). Use judgment; when uncertain, flag and let the user
decide.

## Step 4 — invoke copyright-scan for deep content scan

After the per-commit mechanical checks, invoke the `copyright-scan`
skill, pointing it at the full push range:

```
/copyright-scan <oldest-sha-in-range>..HEAD
```

`copyright-scan` does the heavy pattern matching for book titles, author
names, narrator names, and third-party URLs across the entire diff
content — not just commit messages. Do not duplicate that logic here;
compose instead.

If `copyright-scan` returns PASS, note it in the summary. If it returns
FAIL, incorporate its findings into the overall report.

## Step 5 — report and decide

Collect all findings from Steps 3 and 4. Then:

**If every check passed:**

Report PASS clearly. State how many commits were scanned and which checks
ran. The user may proceed with `git push`.

**If any check failed:**

Report FAIL. For each finding include:

- The commit SHA (short form is fine).
- The rule that was violated.
- The exact offending text or path.
- The remediation:
  - Forbidden string in a commit message → `git rebase -i` to reword
    that commit (requires explicit user approval before executing).
  - Non-conventional subject → same rebase + reword.
  - `TODO.md` in diff → identify the commit that introduced it, remove
    the file from that commit via interactive rebase.
  - Copyright leak identified by `copyright-scan` → follow
    `copyright-scan`'s remediation output.

**If a violation already landed on origin (i.e. the SHA is already
reachable from the remote):** escalate to P0. State clearly that
removing it requires a history rewrite and force-push, which are
destructive operations. Do not execute them. Wait for explicit user
approval before proceeding.

## Things NOT to do

- **Do not skip Step 1.** An unclean working tree means the scan does
  not cover everything the user intends to push. A dirty tree is not a
  minor paperwork problem — it is a gap in the safety net.
- **Do not invent a forbidden-string regex that matches normal prose.**
  The `\bsession\b` pattern applies to trailers and co-authorship lines,
  not to phrases like "audio session length" or "recording session".
- **Do not execute `git rebase -i` without explicit user approval.** The
  user must confirm each history rewrite. History rewrites on published
  branches are destructive.
- **Do not duplicate `copyright-scan` logic.** Step 4 delegates to the
  existing skill. If `copyright-scan` is missing or broken, report that
  gap rather than attempting a partial manual replacement.
- **Do not push after a FAIL.** Even a single failed check is a hard
  stop. The user decides what to fix and when; this skill enforces the
  gate, not the repair.
- **Do not treat a PASS as permanent.** If the user edits a commit after
  this scan runs (e.g. amends the last commit to fix a typo), run the
  scan again before pushing.
