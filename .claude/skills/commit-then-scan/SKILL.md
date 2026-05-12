---
name: commit-then-scan
description: Pre-commit ritual that re-reads TODO.md, scans the staged diff for AI-origin mentions and copyright leaks, validates a Conventional Commits subject, and only then runs git commit. Use this whenever the user says "commit this", "make a commit", "let's commit", "commit these changes", "stage and commit", or any time the next action is git commit in the AudiobookMaker repo. Exists because three separate CLAUDE.md obligations must all fire before every commit, and the project memory shows they have each been skipped in past sessions.
---

# Commit-then-scan

Run this ritual every time a commit is about to happen. It gates the
commit behind four checks that have each caused a P0 incident when
skipped: stale `TODO.md` view, AI-origin strings in the message,
copyright leaks in the diff, and a malformed commit subject.

## Why this skill exists

`CLAUDE.md` lists three pre-commit obligations that must happen before
every `git commit`:

1. Re-read `TODO.md` — it is shared by four parallel sessions, and
   another session may have just pushed a status change that affects
   what you are about to touch.
2. Scan the staged diff for AI-origin strings — `feedback_no_ai_in_git`
   in memory marks violations as P0 requiring a force-push scrub.
3. Scan the staged diff for copyright leaks — `feedback_no_copyright_in_repo`
   in memory treats a leak the same as a leaked secret.

A fourth obligation is implied by `feedback_conventional_commits`: the
commit subject must be a valid `type(scope): desc` before the commit is
written, not discovered malformed after push.

Without this skill, sessions under time pressure skip one of the four.
With it, the four checks are one named ritual that cannot be split.

### 1. Confirm staged scope

Run both commands and read the output before drafting anything:

```bash
git status
git diff --cached
```

If nothing is staged, stop and ask the user which files to stage.
Do not auto-stage with `git add .` or `git add -A` — that can
accidentally include `.env`, credentials, or binary test inputs.
Stage specific named files only.

### 2. Re-read TODO.md

Read the full `TODO.md` at the repo root. Look for two things:

- Any "In Progress" item that references a file present in the staged
  diff. If you find one, confirm the owner tag on that item belongs to
  this session before proceeding. If it belongs to another session,
  stop and surface the conflict to the user.
- Any new backlog item that changes the scope of what you are about to
  commit. If the staged changes now cover something already claimed
  elsewhere, surface that too.

### 3. Copyright scan

Invoke the `copyright-scan` skill over the staged diff. Do not
duplicate its logic here — call it as a sub-step.

The copyright-scan skill looks for book and audiobook titles, real
author and narrator names, source-file paths that identify a work,
and third-party URLs. If it returns fail, stop. Do not commit until
the leak is removed from the staged changes.

### 4. Scan for AI-origin strings

Grep the output of `git diff --cached` for each of the following
patterns. Match case-insensitively and match whole-word where the
pattern could appear as a substring.

Forbidden patterns to grep for (presented here as quoted strings so
this file does not trip its own scan):

- `"Claude"`
- `"Anthropic"`
- `" AI "` and `"AI-"` and `"-AI"`
- `"agent"` — flag any occurrence; context-read before blocking
- `"session"` — flag; context-read; technical uses like "HTTP session"
  are fine, attribution uses ("in this session") are not
- `"Co-Authored-By"` — forbidden in trailers
- `"Generated with"` — flags AI-origin boilerplate
- `"claude.ai"` — URL form

Also grep the draft commit message for the same list.

If any forbidden string appears, identify the exact file and line,
surface it to the user, and stop. The commit does not proceed until
the string is removed.

### 5. Validate the commit subject

The subject must satisfy all of the following before the commit runs:

- Starts with one of the allowed types: `feat`, `fix`, `docs`, `chore`,
  `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`,
  `release`.
- Followed immediately by an optional `(scope)` in parentheses, then
  a colon and a single space.
- The description after the colon is lowercase, present-tense
  imperative, and does not end with a period.
- The full subject line is 72 characters or fewer.

Forbidden subject forms:
- Bare `gui:` — not a valid type; use `feat(gui):` or `fix(gui):`.
- `todo:` — not a valid type.
- Any subject with "Claude", "AI", "agent", "Co-Authored-By", or
  similar in it.

If the subject fails validation, fix it and re-read the draft once
more before proceeding.

### 6. Commit

Only after all five steps above pass, run `git commit` with the
validated message. Use a HEREDOC to pass the message so PowerShell
and bash both handle multi-line bodies correctly:

```bash
git commit -m "$(cat <<'EOF'
type(scope): description here
EOF
)"
```

If the pre-commit hook fails, **do not** use `--amend` to retry.
Fix the underlying issue, re-stage the corrected files, and create a
new commit from scratch. `--amend` is only used when the user
explicitly asks for it.

## Things NOT to do

- **Do not skip any of the six steps**, even when the commit looks
  obviously safe. The checks that have been skipped before were all
  "obviously safe" at the time.
- **Do not use `git add .` or `git add -A`** to stage files. Stage
  named files only.
- **Do not duplicate copyright-scan logic.** Call the existing skill
  as a sub-step. If the skill is unavailable, surface that and wait
  for the user to resolve it before committing.
- **Do not amend an existing commit after a hook failure.** Hook
  failure means the commit did not happen — amending would rewrite
  the commit before it, which may destroy another session's work.
- **Do not put forbidden strings in the body or trailers** of the
  commit message. The ban applies to the full message, not only the
  subject line.
- **Do not self-approve a conflict in TODO.md.** If the staged diff
  touches files owned by another session's In Progress item, the
  decision belongs to the user, not to this skill.
