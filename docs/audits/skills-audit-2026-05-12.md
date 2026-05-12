# Skills audit — what we have and what we still need

This file is a one-time audit. It looks at every recurring AudiobookMaker
workflow that benefits from a fixed, written procedure, and asks two
questions:

1. Do we already have a skill for it under `.claude/skills/`?
2. If not, should we write one — and what would the trigger, value, and
   shape look like?

The point is reliability. When the user hands a task to a Claude
session, the session should be able to look up a skill and walk through
it the same way every time, with the same guards and the same failure
modes. Right now the project already has eight skills; this audit
proposes the next batch.

## Inventory — skills already in `.claude/skills/`

These are the procedures that have been formalised so far. Each has a
`SKILL.md` file with a YAML frontmatter `description` field that lists
the trigger phrases.

| Skill | One-line summary |
|---|---|
| `release-cut` | Bumps `APP_VERSION` and the installer version together, tags `vX.Y.Z`, pushes, watches CI, and verifies the live release carries a SHA-256 in the notes **and** as a sidecar asset (auto-update breaks otherwise). |
| `release-bundle-audit` | Trims dead-code data files and unused dependency trees out of the two PyInstaller `.spec` files on the `chore/release-bundle-size` branch, so the installer stays small enough that users actually click "update". |
| `copyright-scan` | Scans a staged diff (or named range) for third-party source-material leaks — book titles, author names, narrator names, identifying paths, copyrighted URLs — and returns pass / fail with file:line citations. |
| `audit` | Three-phase robustness audit: static analysis + five parallel sub-agents (resource lifecycle, data integrity, concurrency, error paths, external boundaries) + aggregated `docs/audits/audit-<date>.md` report with a severity tally. |
| `voice-clone-finnish` | End-to-end Finnish multi-speaker clone: chunked analyze + ECAPA diarization + per-speaker transcript validation + LoRA training (or few-shot fallback) + ear-check by synth. |
| `scanned-pdf-to-audiobook` | Converts a scanned / image-only PDF into an audiobook through the `ocrmypdf` + Tesseract fallback added in PR #26. Handles the language choice, sample-first ritual, and long-run cancellation behaviour. |
| `pronunciation-corpus-add` | Appends a Finnish mispronunciation report from a tester (Turo is the canonical one) to `docs/pronunciation_corpus_fi.md` in the project's structured format. |
| `work-session` | Atomic claim / pause / finish protocol for `TODO.md` so the four parallel Claude sessions never collide. |

Eight skills today. Most of them are heavy procedures that touch
multi-file flows (releases, bundles, voice cloning, audits). The newer
ones (`scanned-pdf-to-audiobook`, `pronunciation-corpus-add`,
`voice-clone-finnish`, `release-bundle-audit`) show the project moving
toward formalising every workflow that has bitten a session at least
once.

## Top proposed new skills

The list below is ordered by the pain a missing skill is causing today.
Each entry has a name, the user phrase that should trigger it, the
pain point we are paying without it, a rough outline of the steps, and
a priority.

### 1. `pre-push-scan` — P0

**Purpose.** Run every pre-push safety check in one pass before any
`git push`: scan the staged diff (and any commits about to leave the
local clone) for AI-origin mentions, copyright leaks, accidental
`TODO.md` content, and non-conventional commit subjects.

**Trigger.** "Is this safe to push", "scan before push", "ready to
push", "push it", "let's ship this", or any time you are about to call
`git push` in this repo.

**Why it matters.** Today four separate rules in `CLAUDE.md` all have
to be remembered at push time:

- No `Claude`, `Anthropic`, `AI`, `agent`, `session`, `Co-Authored-By`
  anywhere in commit messages or PR bodies (zero tolerance, force-push
  required to scrub).
- No third-party copyrighted material — book titles, author names,
  narrator names, identifying paths, copyrighted URLs.
- No `TODO.md` content in the diff (the file is gitignored and must
  stay that way).
- Conventional Commits — every commit subject is `type(scope): desc`,
  no invented prefixes like "todo:" or bare "gui:".

The `copyright-scan` skill already exists for the second one. The
other three are remembered manually each time and have all been
violated before. A `pre-push-scan` skill bundles all four into one
deterministic pass, so the session cannot push until it has run them.

**Pain point.** Every AI-mention violation requires a history rewrite
and force-push. The repo has hit this multiple times. Once a leak
lands on origin, the cleanup is destructive and needs explicit user
approval.

**Outline.**

1. Check the working tree is clean (no unstaged changes that will
   slip past the scan).
2. Determine the push range: `git log @{u}..HEAD` for tracked
   branches, or `git log master..HEAD` for new branches.
3. For each commit in range:
   - Grep the subject + body for forbidden AI strings.
   - Validate the subject matches the Conventional Commits regex.
   - Diff scan for `TODO.md` content, identifying paths, book / author
     / narrator names.
4. Invoke `copyright-scan` as a sub-step for the deep content scan
   (re-use, don't duplicate).
5. Report pass / fail with file:line citations and the exact
   remediation: which commit to rewrite, which file to un-stage, or
   if it already landed, escalate to P0.

### 2. `commit-then-scan` — P0

**Purpose.** Before any commit, run a small ritual: re-read `TODO.md`,
draft a Conventional Commit subject, scan the staged diff for AI
mentions and copyright leaks, then commit.

**Trigger.** "Commit this", "make a commit", "let's commit", or any
time the next action is `git commit`.

**Why it matters.** `CLAUDE.md` lists three pre-commit obligations:

- Re-read `TODO.md` (it is shared by four parallel sessions; another
  session may have just changed it).
- Re-read the staged diff for AI-origin strings.
- Re-read the staged diff for copyright leaks.

These three checks are currently inlined in every session's habit. A
skill makes them part of one named ritual instead of three rules a
session has to remember individually. Pairs naturally with
`pre-push-scan`: this one runs before `git commit`, the other runs
before `git push`.

**Pain point.** When a session is mid-flow and the user says "commit
that", the session sometimes skips one of the three checks. The
`feedback_no_ai_in_git` and `feedback_no_copyright_in_repo` memory
entries both flag this as recurrent.

**Outline.**

1. `git status` and `git diff --cached` — confirm staged scope.
2. Re-read `TODO.md`. If the staged diff touches files referenced in
   "In Progress", confirm the owner tag belongs to this session.
3. Run `copyright-scan` over the staged diff.
4. Grep the staged diff and the draft commit message for AI mentions.
5. Verify the subject is `type(scope): desc` with no period, lower
   case, present-tense imperative.
6. Only then call `git commit`.

### 3. `worktree-launch` — P1

**Purpose.** Start a new parallel Claude session safely: pick a free
session slot (Claude 1 / 2 / 3 / 4), create the worktree under
`.claude/worktrees/<branch>`, claim a task in `TODO.md`, verify
isolation actually held.

**Trigger.** "Start a new session", "spawn another Claude", "open a
worktree", "let me run two of you in parallel".

**Why it matters.** `feedback_worktree_isolation.md` in memory and the
`CLAUDE.md` "Worktree isolation is a hint, not a guarantee" section
both document an observed failure: a worktree-isolated agent's edits
showed up unstaged in the **main** checkout. The fix is verification,
not trust. The skill encodes the verification.

The `work-session` skill already covers the `TODO.md` claim mechanics
once a session exists. This new skill is one level up: it covers the
filesystem setup and the post-launch leak check.

**Pain point.** Without it, a session forgets `--worktree`, two
sessions land in the same checkout, branches switch under each
other, and committed work disappears. Observed in April 2026 on
`feature/retire-fast-track-bundle`.

**Outline.**

1. Read `TODO.md` and find a 🟢 idle session slot.
2. Pick a branch name in kebab-case matching the task scope.
3. `git fetch origin && git worktree add .claude/worktrees/<branch>
   -b <branch> origin/master`.
4. Hand the worktree path to the new Claude with the session ID baked
   in.
5. After the agent runs (or is stopped), `git status` in main — must
   be clean. If not, surgical `git checkout HEAD -- <leaked-file>`
   per the `feedback_worktree_isolation.md` recipe.

### 4. `audit-followup` — P1

**Purpose.** Translate an audit report (`docs/audits/audit-<date>.md`)
into one fix branch per area (resource lifecycle, data integrity,
concurrency, error paths, external boundaries), with parallel
sub-agents doing the actual fixes.

**Trigger.** "Land the audit fixes", "fix the audit findings",
"start the audit follow-up", "let's burn down audit-<date>".

**Why it matters.** The `audit` skill produces the report but stops
there. The 2026-04-23 audit found 66 issues; the user manually
orchestrated five parallel branches to fix them, then merged seven
PRs in one sweep (commit `50b170a`). That orchestration is rerunnable
every audit, and right now it lives only in the user's head.

**Pain point.** Without a skill, each audit follow-up reinvents how
to split the work, name the branches, decide which findings escalate
to P0, and coordinate the merge. The skill encodes the topology so
the next audit's fixes land just as cleanly.

**Outline.**

1. Read the latest `docs/audits/audit-<date>.md`.
2. Bucket findings by area (the five Phase 2 categories).
3. For each non-empty bucket, propose a branch name
   (`fix/audit-<date>-<area>`).
4. Spawn one sub-agent per branch with the bucket's findings as input.
   Hard rule: only one sub-agent at a time may touch a voice-pack
   subprocess (GPU lock, per `CLAUDE.md` resource-discipline).
5. After all branches land, write the merge commit `merge: land audit
   <date> fixes across N parallel branches` and link the PRs.
6. Update the audit report's "Follow-up status" section with the
   merge commit SHA.

### 5. `voice-pack-from-audio-short` — P1

**Purpose.** Few-shot voice cloning for short audio sources (< 5
minutes) that are too short to train a LoRA adapter on. Goes through
the chunked analyzer, picks a ref clip per detected speaker, validates
each ref by transcript, and packages a few-shot voice pack.

**Trigger.** "Copy this voice quickly", "I have a short clip", "make
a fast pack", or any audio source under five minutes that the user
hands over with a clone request.

**Why it matters.** The `voice-clone-finnish` skill covers the full
LoRA path. For short sources, training is the wrong tool — the pack
must be few-shot. The decision rule is currently informal. A skill
documents the threshold (~5 minutes), the path divergence, and the
guardrails:

- Multi-speaker default applies even for short clips.
- ECAPA diarizer fallback if pyannote conflates speakers.
- Pack tier is `few-shot`, not `reduced` or `full`.
- The pack stays in `.local/voice_packs/` and is **not** installed
  into `~/.audiobookmaker/` if the source is copyrighted (per
  `feedback_no_installing_copyright_derived_packs.md`).

**Pain point.** Without it, a session reaches for the heavy LoRA
pipeline on a 90-second clip and either crashes (too little data) or
produces a low-quality adapter. The few-shot path is correct here and
should be the obvious default at that source length.

**Outline.**

1. `ffprobe` the source for duration; confirm under five minutes.
2. Run `voice_pack_analyze --diarizer ecapa`.
3. Validate ref-clip transcripts per speaker.
4. Run `voice_pack_package --tier few-shot --reference <ref>.wav`.
5. Synth a 30 s sample for ear check before declaring done.
6. Output the pack to `.local/voice_packs/<slug>/`; never install if
   source is copyrighted.

### 6. `ci-failure-triage` — P1

**Purpose.** When CI fails on a release tag or PR, walk the failure
modes in the right order: version drift → Tk-thread hang → test
flake → coverage timeout → SHA guard → ffmpeg pin → spec import
drift. Each has a known fix recipe in the recent commit log.

**Trigger.** "CI is red", "the build failed", "tag failed CI",
"release-build broke", or any time `gh run list` shows a recent
failure on master or a tag.

**Why it matters.** Look at the last 20 `fix(ci):` commits:

- "disable pytest-timeout to avoid late-firing timer crash"
- "restore 13-file ignore list — module-level tkinter imports defeat
  destroy() refactor"
- "exclude all test_gui_*.py from CI — customtkinter import spawns Tk
  threads"
- "bump ffmpeg to n7.1.4 — old build is gone"
- "catch `from src import name` form in runner-imports guard"

Same shapes recur. A triage skill maps `gh run view --log-failed`
output to the canonical fix, so future CI failures don't restart the
investigation from scratch.

**Pain point.** Without it, every CI break starts with a fresh dive
into the workflow file, the ignore list, and the spec runner imports.
A skill turns "CI is red" into "checked symptom X, recipe Y, fix
landed in commit Z" inside ten minutes.

**Outline.**

1. `gh run list --limit 5` and pick the failing run.
2. `gh run view <id> --log-failed` and pull the first failing step.
3. Match the output against the recipe library:
   - Version drift between `APP_VERSION` and `setup.iss` → see
     `release-cut` skill, re-tag.
   - `STATUS_STACK_BUFFER_OVERRUN` in voice-pack-analyze → CPU
     fallback in chunked orchestrator.
   - pytest-timeout late fire → drop `--cov` and `-p no:timeout`.
   - Tk-thread hang in `test_gui_*` → confirm CI ignore list still
     covers those files.
   - "runner imports a src/ file not in the spec" → run
     `scripts/check_spec_runner_imports.py` locally, add the
     missing entry.
4. Apply the fix on a `fix(ci): …` commit and re-tag if needed.

### 7. `engine-add` — P2

**Purpose.** Add a new TTS engine to the registry end-to-end: write
the engine module, register it in `engine_registry.py`, hook it into
the GUI's Language → Engine → Voice cascade, add tests, update the
spec `datas=` list, update `cleanup.py` if the engine writes anywhere
new, update `docs/CLI.md` and `docs/DEVELOPER_SETUP.md`.

**Trigger.** "Add engine X", "wire up <new TTS library>", "register a
new TTS engine".

**Why it matters.** Engines are the project's main extension point
and adding one touches at least eight files. Today the steps are
spread across `docs/CONVENTIONS.md`, `docs/ARCHITECTURE.md`, and the
GUI builder docstrings. A skill is the single source of truth so a
new engine ships with the spec, the tests, the cleanup hook, and the
docs all updated in one logical pass.

**Pain point.** Half-wired engines bite later: ship-tests pass but
the frozen build crashes because the engine's runner script imports
a `src/` file not in the spec — see commit `8c3f91e fix(ci): catch
from src import name form in runner-imports guard` for one example.

**Outline.**

1. Decide the engine's contract — in-process vs subprocess
   (`uses_subprocess`).
2. Write `src/tts_<engine>.py` implementing the `TTSEngine` protocol.
3. Register it in `src/engine_registry.py` under the right Language
   keys.
4. Wire the Voice dropdown rebuilder (`gui_unified.py`).
5. Add `tests/test_tts_<engine>.py`.
6. Update `audiobookmaker.spec` `datas=` if the runner imports any
   non-default `src/` modules.
7. Update `src/cleanup.py` if the engine writes anywhere new.
8. Update `docs/ARCHITECTURE.md` mermaid diagram + the engine table.
9. Update `docs/DEVELOPER_SETUP.md` if credentials are needed.
10. Run the full test suite before commit (pre-commit hook does this).

### 8. `cli-subcommand-add` — P2

**Purpose.** Add a new subcommand to the planned `audiobookmaker-cli`
following the design in `docs/CLI.md`. Wires the argparse, routes to
the right backend module, adds tests, regenerates the help reference,
and adds a CLI entry to `docs/CLI.md`.

**Trigger.** "Add CLI command X", "wire up audiobookmaker <verb>",
"expose <feature> to the CLI".

**Why it matters.** The CLI design is documented but not yet
implemented. Once implementation starts, each subcommand follows the
same pattern (thin wrapper over an existing orchestrator function,
matching exit codes, optional `--json` mode). A skill keeps every
new subcommand consistent with that pattern instead of evolving its
own conventions.

**Pain point.** The biggest danger with a CLI is drift from the GUI.
If the CLI and GUI ever disagree on what "convert with Piper at
speed +25" means, the bug is invisible until a user reports it. The
skill enforces the design rule: "no business logic in the CLI layer;
both call the same backend function."

**Outline.**

1. Pick the subcommand from the design in `docs/CLI.md`.
2. Identify the backend module / function it wraps.
3. Add `src/cli/<name>.py` with the argparse subparser + dispatcher.
4. Add `tests/test_cli_<name>.py`.
5. Run `scripts/render_cli_help.py` (or the pre-commit hook that
   does) to regenerate the reference section in `docs/CLI.md`.
6. Confirm exit codes follow the standard table (0 success, 1 bad
   input, 2 missing dependency, 3 user cancelled, 4 transient, 5
   internal).

### 9. `gh-api-merge-from-locked-main` — P2

**Purpose.** Merge a PR to master and update tracked files when
another Claude session owns the main worktree. Uses `gh api`
Contents PUT/DELETE so the other session's uncommitted work is never
clobbered.

**Trigger.** "Land this PR but main is busy", "merge PR #N from the
worktree", "another session is working in main".

**Why it matters.** `feedback_gh_api_merge_pattern.md` in memory
documents the exact pattern. It is not obvious — the natural
instinct is to `git checkout master && git pull && git merge`, which
silently overwrites the other session's WIP. The skill replaces that
instinct with `gh api repos/.../pulls/N/merge` + Contents API for any
follow-up file edits.

**Pain point.** Without it, the other session's uncommitted edits
vanish from main and they discover it ten minutes later. Observed
behaviour from parallel-session experiments.

**Outline.**

1. Confirm the main worktree is busy (someone has uncommitted edits
   or is on a non-master branch).
2. `gh pr merge <N> --squash --delete-branch` via the API endpoint,
   not via the local checkout.
3. For any post-merge edits (release-notes, docs update), use
   `gh api repos/<owner>/<repo>/contents/<path>` PUT with the right
   `sha`, not a local `git add && git push`.
4. Verify the merge with `gh pr view <N>` and `git fetch origin` (do
   not check out the new master in the busy worktree).

### 10. `docs-sync-after-feature` — P2

**Purpose.** After a feature lands, walk the documentation set and
update everything that references the changed surface: README quality
table, `docs/ARCHITECTURE.md` diagrams, `docs/CONVENTIONS.md` if the
process changed, `docs/CLI.md` if a CLI knob changed,
`docs/DEVELOPER_SETUP.md` if a new credential is needed.

**Trigger.** "Update the docs", "sync the README", "what docs does
this feature touch", or after merging any `feat(...)` commit larger
than a single file.

**Why it matters.** Doc drift kills doc usefulness. Look at the
commit log: feature commits ship, and then a follow-up
`docs(readme):` commit lands one to three days later to catch up.
The pattern is repeatable enough to deserve a procedure.

Examples in the recent log:

- `feat(ocr)` series → `docs(readme): add OCR fallback`.
- `feat(gui): remove voice-cloning dialog` → `docs(readme): drop GUI
  voice-cloning sections`.
- `release: bump to 3.12.0` → `docs(readme): update download links
  to v3.12.0`.

**Pain point.** Without it, the docs drift by one or two releases
behind the code, and new contributors read stale instructions.

**Outline.**

1. Determine the feature scope from the merged commit / PR.
2. For each doc file, ask the question listed in `docs/CONVENTIONS.md`
   "Docs" section: "when you change a boundary the docs describe,
   update the doc in the same commit."
3. Update the README test count if tests changed
   (`pytest --collect-only -q | tail -1`).
4. Update download links if the version bumped (already part of
   `release-cut`, but worth checking).
5. Update the architecture mermaid diagram if a module was added or
   removed.
6. One commit per doc file, conventional prefix `docs(<scope>):`.

## Workflows that should NOT become skills

Not every recurring task is a skill candidate. The bar is: does the
procedure have load-bearing guards a session will forget, or does it
have ordering rules that have bitten before? If neither, a skill adds
ceremony without saving anything.

Things that look like skill candidates but probably are not:

- **"Run the test suite."** It is one command (`python -m pytest tests/
  -x -q --tb=short`), the pre-commit hook already runs it, and there
  is no decision tree. A skill here is overhead.
- **"Open a PR."** `gh pr create` with a HEREDOC body is already in
  every session's muscle memory and in `CLAUDE.md`. No hidden traps.
- **"Generate icons."** `scripts/generate_icons.py` runs once after a
  design change and has no arguments. Document the trigger in the
  script docstring, not in a skill.
- **"Add a normalizer pass."** The `docs/CONVENTIONS.md` "Finnish text
  normalizer — lexicon vs. new pass" section already encodes the
  decision rule (most fixes are lexicon edits, not new passes). A
  skill would duplicate that doc.
- **"Set up `.venv-chatterbox`."** One-shot per machine, well
  documented in `docs/DEVELOPER_SETUP.md`, and the `_cudnn_compat.py`
  guard handles the only repeating gotcha automatically.
- **"Write a release note."** CI generates the body from a template in
  `build-release.yml`; the user does not hand-author it.
- **"Find a flaky test."** Investigation tasks resist
  proceduralisation. Each flake has its own root cause; a skill would
  pretend a tree exists where there isn't one.
- **"Add a unit test."** Already encoded in `docs/CONVENTIONS.md`
  ("Tests" section). Pattern is uniform enough that explicit
  procedure does not add value.

The pattern: things with one canonical command, no ordering rules,
and no failure mode that has cost the user time, stay as inline
instructions. Skills are reserved for workflows where forgetting a
step costs hours or causes a P0.

## Implementation order

If you write these in the order below, each next skill builds on the
muscle memory of the previous one, and the highest-leverage P0s land
first.

1. **`commit-then-scan`** (P0). Every other workflow goes through
   commits eventually. Getting the commit ritual right protects every
   later change. Smallest scope, biggest reach.
2. **`pre-push-scan`** (P0). Pairs with the commit ritual; closes the
   "leak landed on origin" failure mode that the user has fixed by
   force-push in the past. Re-uses the existing `copyright-scan`
   skill, so the work is mostly composition.
3. **`ci-failure-triage`** (P1). The recent commit log shows CI
   failures are the most common interruption to release work. Coding
   the recipes into a skill turns minutes of investigation into
   pattern-match-then-fix.
4. **`worktree-launch`** (P1). Once parallel sessions become routine,
   the worktree-isolation failure mode becomes high-impact. Write
   the skill before the next time four sessions run together.
5. **`audit-followup`** (P1). Pairs with the existing `audit` skill;
   the user has done this orchestration manually once, so the
   procedure is already proven.
6. **`voice-pack-from-audio-short`** (P1). Closes the short-source
   gap in `voice-clone-finnish` so the multi-speaker default works
   on any input length.
7. **`engine-add`** (P2). Write this when the next TTS engine is on
   the roadmap. Premature otherwise.
8. **`cli-subcommand-add`** (P2). Write this when CLI implementation
   starts, not before. Today the CLI is design-only.
9. **`gh-api-merge-from-locked-main`** (P2). Low frequency, but the
   pattern is sharp and easy to forget. Cheap to write.
10. **`docs-sync-after-feature`** (P2). Marginal — most doc updates
    are obvious from the diff. Write this last, or skip if the
    pattern stops showing up after a few releases.

The two P0 skills are the foundation. They protect every commit and
every push in the repo and they directly close the failure modes
flagged in memory (`feedback_no_ai_in_git`,
`feedback_no_copyright_in_repo`, `feedback_conventional_commits`).
Land those first, then build out the P1 set as the workflows they
cover come up naturally in the next few weeks of work.
