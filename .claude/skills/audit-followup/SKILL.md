---
name: audit-followup
description: Translate an audit report (docs/audits/audit-<date>.md) into one fix branch per area (resource lifecycle, data integrity, concurrency, error paths, external boundaries), with parallel sub-agents doing the actual fixes, then a merge sweep. Use whenever the user says "land the audit fixes", "fix the audit findings", "start the audit follow-up", or "let's burn down audit-<date>".
---

# audit-followup

Picks up where the `audit` skill stops. The `audit` skill produces a
`docs/audits/audit-<YYYY-MM-DD>.md` report grouped by the five Phase 2
areas. This skill turns that report into merged, tested code.

The precedent: the 2026-04-23 audit found 66 issues. The user manually
orchestrated five parallel branches, landed 26 `fix(*)` commits, and
merged everything in one sweep at commit `50b170a`. That orchestration
is now this skill.

## Why this skill exists

Without a written procedure, every audit follow-up reinvents how to
split work, name branches, decide which findings are P0, and coordinate
the final merge. When the user manually ran this for the 2026-04-23
audit it took a full session of coordination just to get the topology
right. The topology is always the same; only the findings change.

## When to invoke

- "Land the audit fixes"
- "Fix the audit findings"
- "Start the audit follow-up"
- "Let's burn down audit-<date>"
- Any time the user points at a `docs/audits/audit-*.md` and says "do
  this one"

## Workflow

### Step 1 — Read the report

Find the latest `docs/audits/audit-<YYYY-MM-DD>.md`. If the user named
a specific date, use that file. If multiple files exist for the same
date, prefer the highest suffix (`-v2` over plain, etc.).

Read it in full. Note:

- The commit SHA under "## Summary" (this is what was audited).
- Every finding under each of the five area headings.
- Any findings already struck through (`~~…~~`) by the user — those are
  false positives, skip them.

### Step 2 — Bucket by area

Split findings into five lists, one per `audit` Phase 2 scope:

| Area | Branch suffix |
|---|---|
| Resource lifecycle | `resource-lifecycle` |
| Data integrity | `data-integrity` |
| Concurrency | `concurrency` |
| Error paths | `error-paths` |
| External boundaries | `external-boundaries` |

Branch name pattern: `fix/audit-<YYYY-MM-DD>-<area-suffix>`.

If a bucket is empty after removing false positives, skip that branch.
State which buckets were skipped and why before spawning any sub-agents.

Escalate any `critical` finding to P0: fix it immediately on the
current branch before spawning sub-agents, then note it in the final
merge commit.

### Step 3 — Propose and confirm

Present the user with:

- The list of branches you intend to open, each with a count of
  findings.
- The estimated commit count per branch (one commit per logical fix
  group is the target; never batch unrelated fixes).
- Any P0 escalations that will block spawning.

Wait for user confirmation before step 4. If the user strikes more
items or moves findings between buckets, update before proceeding.

### Step 4 — Spawn sub-agents

Spawn one sub-agent per non-empty bucket. Use the `worktree-launch`
skill procedure for each — do not duplicate that procedure here. Each
sub-agent receives:

- Its bucket's finding list (file:line + one-line description).
- The audited commit SHA as the base for comparison.
- The branch name it must work on.
- Instruction to use `commit-then-scan` before every commit.
- Instruction to open a PR when its branch is ready.

**GPU lock rule** (from `CLAUDE.md` resource-discipline): only one
sub-agent at a time may run a voice-pack subprocess (analyze, synth,
train). All other sub-agents may run fully in parallel. If no bucket
involves voice-pack code, all five can run concurrently.

Each sub-agent's fix commits must use Conventional Commits:

```
fix(<area>): <what changed and why>
```

where `<area>` matches the bucket name (e.g. `fix(concurrency):`).

### Step 5 — Monitor and merge

After all sub-agent PRs are open:

1. Run `gh pr list` to confirm all branches have open PRs.
2. Verify CI is green on each PR before merging.
3. Merge with a single sweep commit on master:

```
merge: land audit <YYYY-MM-DD> fixes across N parallel branches

PRs: #N1 (resource-lifecycle), #N2 (data-integrity), ...
```

Do not squash individual fix commits — keep the history readable per
area. Use `--no-ff` for each branch merge so the branch topology is
visible in `git log --graph`.

### Step 6 — Update the audit report

After the merge commit lands, open `docs/audits/audit-<YYYY-MM-DD>.md`
and append a "Follow-up status" section at the bottom:

```markdown
## Follow-up status

Fixes landed: merge commit `<SHA>` on `<YYYY-MM-DD>`.
Branches: fix/audit-<date>-resource-lifecycle (#N), ...
Skipped buckets: <area> (0 findings after false-positive removal).
```

Commit this update as:

```
docs(audits): record follow-up merge SHA for audit-<date>
```

## Things NOT to do

- **Do not duplicate the `worktree-launch`, `audit`, or
  `commit-then-scan` skill procedures.** Reference them by name and
  delegate. This skill composes the others.
- **Do not batch findings from different areas into one branch.** The
  audit's five scopes are non-overlapping by design; mixing them makes
  per-area revert impossible.
- **Do not start spawning sub-agents before the user has confirmed the
  bucket split.** One misclassified finding can send a sub-agent into
  the wrong scope.
- **Do not run two voice-pack sub-agents in parallel.** GPU memory is
  finite; two concurrent model loads have crashed the machine (observed
  2026-05-10). Serialise GPU work even if it means the overall run
  takes longer.
- **Do not merge branches out of order when a P0 finding exists.** P0
  fixes go first, are verified independently, and are noted in the
  merge commit before the rest land.
- **Do not close false positives silently.** If you decide a finding is
  a false positive, state the reason in the audit report (strike it
  through with a note) before skipping it.
- **Do not invent new area names.** The five names are fixed by the
  `audit` skill. Introducing a sixth bucket breaks the assumption that
  branches are non-overlapping.
