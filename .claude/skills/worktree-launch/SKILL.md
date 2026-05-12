---
name: worktree-launch
description: Start a new parallel Claude session safely. Pick a free session slot (Claude 1/2/3/4), create the worktree under .claude/worktrees/<branch>, claim a task in TODO.md, verify isolation actually held after the agent runs. Use whenever the user says "start a new session", "spawn another Claude", "open a worktree", or "let me run two of you in parallel".
---

# Worktree-launch

Filesystem setup and post-launch leak verification for a new parallel
Claude session. The `work-session` skill handles the `TODO.md` claim
mechanics; this skill is one level up — it covers the git worktree
plumbing and the isolation check that `work-session` does not.

## Why this skill exists

On 2026-05-10, a worktree-isolated agent's edits showed up unstaged in
the **main** checkout. The `CLAUDE.md` "Worktree isolation is a hint,
not a guarantee" section documents the event. The fix is verification
after every launch — trusting the platform is not enough.

In April 2026, `feature/retire-fast-track-bundle` was silently
committed on master because two sessions shared the same checkout and
switched branches under each other. A worktree per session is the only
structural protection, and this skill enforces that every new session
gets one before it touches any code.

## Phase 1 — pick a free slot

Read `TODO.md` from the **main** checkout (not a worktree):

```bash
git -C <main-repo-path> fetch origin
git -C <main-repo-path> merge --ff-only origin/master
```

Then read `TODO.md` in full. Find a session row showing `🟢 idle` with
no task or owner tag. That is the slot for the new session.

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

Do not branch from the local `master` HEAD — branch from `origin/master`
so the new worktree starts from the pushed state, not from any
uncommitted local changes.

Verify the worktree appears in the list:

```bash
git -C <main-repo-path> worktree list
```

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

Do not let the new session proceed without invoking `work-session` to
register its claim. The claim publish is what makes the session visible
to the other Claudes.

## Phase 5 — verify isolation after the agent runs or is stopped

After the agent finishes (or is stopped for any reason), run `git
status` inside the **main** checkout — not the worktree:

```bash
git -C <main-repo-path> status
```

The output must be clean of files the agent touched. Any file that
appears modified here means isolation leaked: the agent's edits wrote
into the main checkout instead of the worktree.

If a leak is found:

1. Identify exactly which files are affected (`git diff --name-only`).
2. For each leaked file, use a surgical revert — never a blanket stash
   when other sessions may have unrelated WIP in flight:

   ```bash
   git -C <main-repo-path> checkout HEAD -- <leaked-file>
   ```

3. Confirm the reverted file matches what the other sessions expect.
   If another session has staged changes to the same file, tell the
   user and wait for instructions before touching it.
4. Re-run `git status` to confirm the main tree is now clean.

Do not use `git stash`, `git restore .`, or `git checkout .` — these
are blanket operations that discard WIP from sessions that had nothing
to do with the leak.

## Phase 6 — clean up after a finished session

When the session finishes and its branch is merged:

```bash
git -C <main-repo-path> worktree remove .claude/worktrees/<branch>
git -C <main-repo-path> branch -d <branch>
git -C <main-repo-path> push origin --delete <branch>
```

The `work-session` skill handles the `TODO.md` clear and the
status-board flip to `🟢 idle`. Run it after the worktree is gone.

## Things NOT to do

- **Do not let a new session start in the main checkout.** If the user
  asks to "just run it here", refuse and create a worktree first. The
  April 2026 incident is the reason.
- **Do not create more than four parallel sessions.** The project has
  four permanent Claude slots. A fifth session has no status-board row,
  so its claim is invisible and it will collide.
- **Do not branch from local master.** Always use `origin/master` as
  the start point so uncommitted changes on the main checkout do not
  bleed into the new worktree.
- **Do not remove an existing worktree directory without asking.** A
  leftover worktree may hold unpushed commits. Inspect it first.
- **Do not skip the post-run `git status` check.** Isolation is a hint,
  not a guarantee. The check is the only reliable safety net.
- **Do not use blanket `git stash` or `git restore .` to fix a leak.**
  Surgical `git checkout HEAD -- <file>` is the right tool when other
  sessions may have WIP in the same tree.
- **Do not duplicate the TODO.md claim logic.** Delegate that entirely
  to the `work-session` skill.
