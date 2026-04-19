---
name: work-session
description: Start, pause, or finish a TODO.md work session as one of the 4 permanent Claude sessions in AudiobookMaker. Use this whenever the user says "claim task X", "take task Y", "start working on Z", "pick something", "I'm done", "finish up", "pause", "I'm blocked", or "go idle". Also use before touching any code on a fresh session to make sure the status board and in-progress list are honest. Parallel Claudes collide without this discipline; the shared TODO.md protocol is the only way four sessions stay coherent.
---

# Work-session

Atomic start / pause / finish for the shared [TODO.md](../../../TODO.md)
protocol. Four Claude sessions (Claude 1–4) run in parallel against the
same repo; `TODO.md` on master is the only way any one session knows what
the others are doing. Getting the mechanics right is the difference
between parallel productivity and silent clobbering.

## Why this skill exists

Three separate failure modes keep recurring without this discipline:

1. **Stale view** — you read `TODO.md` at the start of a long session,
   pick a task another Claude already owns, and work in parallel on the
   same item.
2. **Forgotten claim** — you start editing code before adding your owner
   tag. If another Claude pulls, they see the task un-owned and pick it.
3. **Shared workdir** — two Claude sessions with the same checkout
   switch branches under each other. `feature/retire-fast-track-bundle`
   got its commit landed on master in April 2026 exactly this way.

The fix for all three is the same: **pull → read → claim → branch →
work → finish** as atomic phases, each committed before the next starts.

## Session identity

This project has **four permanent Claude sessions**: `Claude 1`,
`Claude 2`, `Claude 3`, `Claude 4`. Figure out which one you are by
reading the status board in `TODO.md`:

- If the user told you ("you're Claude 2 today"), use that.
- Otherwise pick the slot showing 🟢 idle with no owner tag anywhere
  else in the file.
- If unsure, ask the user. Do not invent new names ("Claude 5", "Opus
  2", etc.).

## Starting work — claim a task

Run these in order; do not skip the pull.

### 1. Pull and re-read

```bash
git fetch origin
git checkout master
git merge --ff-only origin/master
```

Do not `git pull --rebase` — rebase is never done in this repo without
explicit per-operation approval from the user. A fast-forward merge is
safe and loud.

Then read `TODO.md` **in full**, not just the backlog. Another Claude
may have just pushed a status change.

### 2. Pick

Pick a task from the "In Progress" list only if it has no owner tag, or
from the backlog. Do **not** touch a task already tagged by another
Claude.

If the user named a specific task, honour that even if the size marker
suggests it's outside your usual. Ask only if the task already has an
owner.

### 3. Claim on master

Edit `TODO.md`:

- Move the task into the "In Progress" section if it isn't there.
- Append your tag to the header: `[Claude N, worktree-<branch-slug>]`.
- Update the status-board row: `🔵 working`, task title, today's date
  (`YYYY-MM-DD`).

Commit with a conventional message:

```
chore(todo): claim <short task description>
```

Push immediately:

```bash
git push origin master
```

This publishes your claim. Every other Claude sees it on their next
pull. **Do not touch any other files in this commit** — the claim is one
logical change, and keeping it alone makes it trivial to revert if the
session aborts.

### 4. Branch into a worktree

Shared workdir is forbidden. Create your own worktree:

```bash
git worktree add .claude/worktrees/<branch-slug> -b feature/<branch-slug> master
```

Where `<branch-slug>` is a short kebab-case description of the task
(e.g. `audioplayer`, `upstream-pr`, `fi-normalizer-pass-p`). All
subsequent file edits, commits, and tests happen inside that worktree:

```bash
cd .claude/worktrees/<branch-slug>
```

Or, if you prefer not to `cd`, use absolute paths that include the
worktree directory and `git -C <worktree-path> ...` for git ops.

### 5. Work

Normal dev loop inside the worktree. Re-read `TODO.md` before **every
commit** (via `git -C <main-repo-path> pull --ff-only origin master`
piped into a fresh read) so you notice when another Claude finishes
something that affects your task.

## Pausing mid-task

If you need to stop before the work is done:

- The item **stays** in "In Progress" with your tag still on it.
- Your status stays `🔵 working` — pausing is not idling.
- Commit and push whatever is safe to commit (tests still passing).
- Leave a brief note in the task's bullet if the next session pick-up
  needs context ("halted at: ref-audio integration; see branch
  `feature/voice-pack-slice-3` last commit").

## Blocked

If external input is needed:

- Append `[BLOCKED: <short reason>]` to the task line.
- Update status-board row to `🟡 blocked`.
- Commit + push the TODO.md change.

Examples of real blocks: "waiting on voice recording from Turo", "needs
NVIDIA hardware", "needs upstream merge".

## Finishing

Once the work is merged to master and verified:

### 1. Merge the feature branch

```bash
cd <main-repo-path>
git checkout master
git pull --ff-only origin master
git merge --no-ff feature/<branch-slug>
git push origin master
```

Fast-forward merges are fine for single-commit branches; `--no-ff`
preserves history for multi-commit branches where the branch tells a
coherent story.

### 2. Delete the branch and worktree

```bash
git worktree remove .claude/worktrees/<branch-slug>
git branch -d feature/<branch-slug>
git push origin --delete feature/<branch-slug>
```

### 3. Clear your claim

Edit `TODO.md`:

- **Remove the task entirely** — no "Recently Completed" section.
  `git log` is the history.
- Update your status-board row to `🟢 idle`, `—`, `—`.

Commit:

```
chore(todo): clear <short task description>
```

Push.

## Going idle without finishing

If the user dismisses you mid-session but you haven't finished:

- Do not clear the task. Leave the claim.
- Update status-board row to `🟢 idle` only if you truly are idle; if
  there is pushed WIP still owned by you, stay `🔵` so the next Claude
  doesn't pick it up.

## Things NOT to do

- **Do not use the internal TodoWrite tool for task tracking.** It is
  invisible to other Claudes. All task state lives in `TODO.md`.
- **Do not commit to master from a shared workdir.** Create a worktree.
- **Do not squash or rebase a published branch** without explicit
  per-operation user approval.
- **Do not remove a task someone else owns** — even if you think it's
  stale. Ping the user.
- **Do not batch multiple TODO.md edits** (claim, status flip, clear)
  into one commit. Each transition is its own commit; they read like a
  timeline.
