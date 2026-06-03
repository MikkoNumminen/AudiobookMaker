---
name: worktree-launch
description: Start a new parallel Claude session safely. Pick a free session slot (Claude 1/2/3/4), create the worktree under .claude/worktrees/<branch>, claim a task in TODO.md, verify isolation actually held after the agent runs. Use whenever the user says "start a new session", "spawn another Claude", "open a worktree", or "let me run two of you in parallel".
---

# Worktree-launch

Filesystem setup for a new parallel Claude session. The `work-session`
skill handles the `TODO.md` claim mechanics; this skill is one level
up — it covers the git worktree plumbing.

For the WHY (isolation incidents, post-run verification, surgical
revert procedure on leak), see CLAUDE.md's
**"Worktree isolation is a hint, not a guarantee"** section. That is
the canonical reference; do not re-state it here.

## Phase 1 — pick a free slot

Read `TODO.md` from the **main** checkout (not a worktree):

```bash
git -C <main-repo-path> fetch origin
git -C <main-repo-path> merge --ff-only origin/master
```

Then read `TODO.md` (limit=60 lines; the session table is always near
the top). Find a session row showing `🟢 idle` with no task or owner
tag. That is the slot for the new session.

If no slot is idle, stop and tell the user. Do not invent a fifth
session name or reuse a slot showing `🔵 working`.

## Phase 2 — pick a branch name

Choose a short kebab-case branch name that describes the task scope
(e.g. `fi-normalizer`, `audioplayer`, `ci-timeout-fix`). The branch
will be created under `.claude/worktrees/<branch>`. Keep it under 30
characters.

## Phase 3 — create the worktree

```bash
git -C <main-repo-path> worktree add \
    .claude/worktrees/<branch> \
    -b <branch> \
    origin/master
```

Branch from `origin/master`, not local `master`, so uncommitted
changes in the main checkout do not bleed into the new worktree.

If the worktree path already exists (leftover from a prior session),
stop and ask the user before removing it. Never silently delete a
worktree that may hold unpushed work.

## Phase 4 — hand off to the new session

The new session needs three pieces of information baked into its launch:

1. **Worktree path** — the absolute path to `.claude/worktrees/<branch>`.
2. **Session ID** — which Claude slot it is (e.g. `Claude 3`).
3. **Task** — the specific task it is picking up from `TODO.md`.

Provide the session with this launch block:

```
You are Claude <N> in the AudiobookMaker parallel-session setup.
Your worktree is at: <absolute-path-to-worktree>
Your task: <task description from TODO.md>
All your file edits and commits happen inside that worktree path.
Before touching any code, invoke the work-session skill to claim the
task in TODO.md under your session slot.
```

The new session must invoke `work-session` to publish its claim before
touching any code; the claim is what makes the session visible to
the other Claudes.

## Phase 5 — verify isolation, then clean up

CLAUDE.md's "Worktree isolation is a hint" section covers the *why*
and the basic verification. This phase adds the operational
procedure for handling a leak when other sessions may have
unrelated WIP in the same tree.

After the agent finishes (or is stopped), run `git status` inside
the **main** checkout — not the worktree:

```bash
git -C <main-repo-path> status
```

If files the agent touched appear modified here, isolation leaked.
Diagnose and repair, **never** with a blanket operation:

1. List the leaked files:
   ```bash
   git -C <main-repo-path> diff --name-only
   ```
2. For each leaked file, check whether another session has staged
   or unstaged changes to the same path. If yes, **stop and ask
   the user** before touching it — a blanket revert would
   silently discard another Claude's WIP.
3. For files no other session is editing, use a surgical revert:
   ```bash
   git -C <main-repo-path> checkout HEAD -- <leaked-file>
   ```
4. Re-run `git status` to confirm the main tree is clean.

**Forbidden tools for fixing a leak:** `git stash`, `git restore .`,
`git checkout .`, `git reset --hard`. All four are blanket
operations that discard WIP from sessions that had nothing to do
with the leak.

Once `git status` in the main checkout is clean, proceed to
cleanup:

```bash
git -C <main-repo-path> worktree remove .claude/worktrees/<branch>
git -C <main-repo-path> branch -d <branch>
git -C <main-repo-path> push origin --delete <branch>
```

`work-session` clears the `TODO.md` claim and flips the slot back to
`🟢 idle`. Run it after the worktree is gone.

## Things NOT to do

- **Do not let a new session start in the main checkout.** Refuse and
  create a worktree first.
- **Do not create more than four parallel sessions.** Four permanent
  Claude slots exist; a fifth has no status-board row.
- **Do not branch from local master.** Always use `origin/master`.
- **Do not remove an existing worktree directory without asking** — it
  may hold unpushed commits.
- **Do not duplicate the `TODO.md` claim logic** here — delegate to
  the `work-session` skill.
