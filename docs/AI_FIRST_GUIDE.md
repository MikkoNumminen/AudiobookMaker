# AI-first development — how this project works with Claude Code

AudiobookMaker is set up to be productive when an AI coding assistant
(Claude Code, primarily) is part of the development loop. This doc
explains the pattern so a new contributor — human or AI — can read it
once and know how to extend the project without re-deriving the
conventions.

If you have never used Claude Code on this repo, start here. If you
are a returning Claude session, your `CLAUDE.md` already loaded and
this file is the contributor-facing version of the same ideas.

## The four pillars

```
            ┌─────────────────┐
            │   CLAUDE.md     │  ← project rules (committed, loaded
            │  (project-wide  │     automatically at session start)
            │   instructions) │
            └────────┬────────┘
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
  ┌─────────┐   ┌─────────┐   ┌─────────┐
  │  Auto-  │   │ Skills  │   │  Tests  │
  │ memory  │   │(runbook │   │ (regr.  │
  │ (per-   │   │ scripts │   │ guard-  │
  │ user)   │   │ for AI) │   │  rails) │
  └─────────┘   └─────────┘   └─────────┘
```

Each pillar has a specific job. The pattern is the value, not any
single pillar in isolation.

### 1. `CLAUDE.md` — committed project rules

[`CLAUDE.md`](../CLAUDE.md) at the repo root encodes the rules every
session inherits: things you do not want to relearn in turn 30 of
turn 1's conversation, and things that would cost real money/time if
forgotten.

What lives there:

- **Hard rules** that must never be violated (no AI mentions in
  commits, no copyrighted material in the repo, `TODO.md` is
  gitignored, auto-update integrity is P0).
- **Resource discipline** that prevents the box from freezing (one
  voice-pack ML subprocess at a time on a 12 GB GPU).
- **Output discipline** (`.local/` for all dev I/O, never next to
  source, never in `dist/`).
- **Communication tone** (English in user-facing prose, GUI label
  names, Barney-style educational tone).

Loaded into every Claude Code session automatically. **Edit
`CLAUDE.md` when a rule is genuinely project-wide and stable.** If a
rule is one user's preference (commit message style, tone of voice),
it belongs in that user's auto-memory, not here.

### 2. Auto-memory — per-user, not in the repo

Auto-memory lives under
`~/.claude/projects/<repo-slug>/memory/` on each developer's machine
and is **deliberately not tracked**. Each session reads `MEMORY.md`
into context at start, so the next session knows what the last
session learned.

Why not in the repo:

- **Personal preferences don't generalize.** *"Reply in English even
  when I write Finnish"* is one user's rule, not a project rule.
- **Memory drifts.** A note written on 2026-04-15 may be wrong by
  2026-05-17 — publishing it would freeze stale assumptions in public
  history.
- **Copyright-leak vector.** Memory captures conversational context,
  which can include copyrighted-source attribution by accident. The
  project has had to scrub one such leak before (commit `6747cac`).
- **Privacy.** Memories sometimes reference testers by name or
  machine-specific paths.

The migration pattern: **when a memory note matures into stable
project knowledge, move it into tracked docs.** Example already
landed: the English Grandmom *"up." failure-word* observation was
originally a per-session memory note; it now lives in
[`docs/english_grandmom.md`](english_grandmom.md) where every
contributor can see it without needing the original author's memory
file.

Which notes have migrated — and which technical findings are still
memory-only and waiting to mature — is tracked in
[`docs/MEMORY_MIGRATIONS.md`](MEMORY_MIGRATIONS.md), so a new session
can see the state of the pipeline without access to anyone's memory
files.

### 3. Skills — committed runbooks for repeated multi-step work

[`.claude/skills/`](../.claude/skills/) holds skill packages. Each
skill is a `SKILL.md` describing a multi-step workflow + (optionally)
an `evals/evals.json` with eval prompts.

A skill earns its keep when:

- The operation has **multi-step orchestration** the LLM should not
  re-derive every time.
- The operation has **hidden gotchas** (e.g., pyannote conflates
  similar-timbre Finnish speakers; always prefer ECAPA on short
  clips).
- The cost of forgetting a step is real (a wasted 2-hour synth run,
  a force-push to scrub a leak, a stuck GPU).

A skill does **not** earn its keep when the workflow is one CLI
command + `--help`, or when it restates CLAUDE.md rules that
auto-load every session. Of the current 11 in-repo skills (plus a
handful of Claude Code builtins like `simplify`, `loop`, `schedule`),
ten are the survivors of a 2026-05-19 audit that retired four skills
for those exact failure modes (see `README.md` "Skill catalog" for
the audit verdicts); `engine-venv-triage` landed after that audit,
encoding the v3.16.0–v3.17.3 field saga. Resist adding more without
a real failure pattern to encode.

Skill index (in-repo):

| Category | Skills |
|---|---|
| Code quality | [`audit`](../.claude/skills/audit/SKILL.md), [`ai-codegen-smell-audit`](../.claude/skills/ai-codegen-smell-audit/SKILL.md) |
| Git hygiene | [`copyright-scan`](../.claude/skills/copyright-scan/SKILL.md) |
| Release / CI | [`release-cut`](../.claude/skills/release-cut/SKILL.md), [`release-bundle-audit`](../.claude/skills/release-bundle-audit/SKILL.md), [`ci-failure-triage`](../.claude/skills/ci-failure-triage/SKILL.md) |
| Voice / TTS | [`voice-pack-finnish`](../.claude/skills/voice-pack-finnish/SKILL.md), [`pronunciation-corpus-add`](../.claude/skills/pronunciation-corpus-add/SKILL.md) |
| Multi-session | [`work-session`](../.claude/skills/work-session/SKILL.md), [`worktree-launch`](../.claude/skills/worktree-launch/SKILL.md) |
| End-user support | [`engine-venv-triage`](../.claude/skills/engine-venv-triage/SKILL.md) |

### 4. Tests — regression guardrails the next session can't sidestep

The pre-commit hook runs the suite on every commit (with a docs-only
shortcut). 2400+ tests today, all green. The pattern worth copying:

- **Contract tests for AI-generated artefacts.** Example: every
  CLI subcommand must have a registered example, enforced by
  [`test_every_subcommand_has_example`](../tests/test_render_cli_help.py).
  Adding a subcommand without an example fails the suite — so the
  next agent who extends the CLI is forced to extend the docs too.
- **Network guards in tests.** `tests/conftest.py` blocks outbound
  connections so a test can't accidentally pull from a real HF
  endpoint. Forces every external dependency to be mocked
  explicitly.
- **Pre-commit hook runs them automatically.** No relying on the
  developer to remember to run `pytest`.

## How the pillars interlock

A real example from this codebase:

1. A user (Turo) reported that the Chatterbox-Finnish model
   mispronounces a Finnish word.
2. The reporter's session called the **`pronunciation-corpus-add`
   skill** to append the entry to `docs/pronunciation_corpus_fi.md`
   (created by the skill on the first report) in a structured form.
3. The skill referenced an **auto-memory** note about Turo as the
   canonical Finnish tester.
4. The corpus entry was committed under **`feat(normalizer):`** or
   `fix(normalizer):` per the **CLAUDE.md** Conventional Commits
   rule.
5. The commit went through the **git hooks**: tests passed,
   docs/CLI.md sync verified, the staged diff clear of TODO.md and
   `.local/` paths, and the commit message clear of vendor-branding
   tokens (the `commit-msg` hook).
6. A future normalizer fix references the corpus entry to verify it
   does not regress, locked in by a **test**.

That's the loop. Memory tells the LLM who Turo is. Skill tells it
what to do with his report. Project rules tell it how to phrase the
commit. Tests prevent the same bug from coming back. Every pillar
plays its part.

## Setting up a fresh clone

1. Clone the repo.
2. Install Python 3.11+, ffmpeg, and the project requirements per
   [`docs/DEVELOPER_SETUP.md`](DEVELOPER_SETUP.md).
3. **Install git hooks:**

   ```bash
   bash scripts/install-hooks.sh
   ```

   This installs two hooks: `pre-commit` (blocks staged TODO.md /
   `.local/` files, checks `docs/CLI.md` is in sync with the parsers,
   checks the skill catalogs match `.claude/skills/`, runs the test
   suite — skipped on pure-docs commits) and `commit-msg` (blocks
   vendor-branding tokens in the message).
   Without this step, your local commits skip the project's
   guardrails — they will still land but they will break things.

4. Read [`CLAUDE.md`](../CLAUDE.md) once. The rules are short.
5. If you use Claude Code, point it at this repo. `CLAUDE.md` loads
   automatically; the skill list is auto-discovered from
   `.claude/skills/`.

## Extending the AI-first surface

When you add code that the next session should know about:

- **Stable project knowledge** → tracked docs (`docs/<topic>.md`).
- **Recurring multi-step workflow** → new skill in
  `.claude/skills/`.
- **Permanent project rule** → update `CLAUDE.md`.
- **Cross-session learning the user wants remembered** → auto-memory
  (per-user, not tracked).

Resist the urge to add a skill for everything. The current 11 are the
ones that pay rent (ten survived the 2026-05-19 audit;
`engine-venv-triage` earned its place in the v3.16.0–v3.17.3 field
saga).

## Where this pattern came from / could go

The pattern emerged organically — there was no upfront design doc
saying "we will have four pillars." Each piece landed when a real
problem demanded it (e.g., the worktree-isolation skill landed after
sessions collided in the main checkout; the copyright-scan skill
landed after a leak). The shape is reusable for any project that
runs AI sessions seriously.

If you adopt this pattern for your own project, the minimum viable
shape is:

1. **`CLAUDE.md`** at the repo root with your project's hard rules.
2. A **pre-commit hook** that runs your tests + any quality scans.
3. A few **skills** for your one or two most expensive failure modes.
4. The discipline to **migrate stable memory → tracked docs** as
   project knowledge matures.

Everything else (worktree coordination, multi-Claude TODO.md,
auto-generated CLI docs) is opportunity, not requirement.

## See also

- [`CLAUDE.md`](../CLAUDE.md) — the project's hard rules.
- [`docs/MEMORY_MIGRATIONS.md`](MEMORY_MIGRATIONS.md) — the tracked
  log of memory-to-docs migrations (done and pending).
- [`docs/CONVENTIONS.md`](CONVENTIONS.md) — coding / commit /
  release conventions referenced from `CLAUDE.md`.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — how the runtime fits
  together (engines, registry, GUI / CLI dispatch).
- [`docs/CLI.md`](CLI.md) — auto-generated CLI reference; the
  generator is [`scripts/render_cli_help.py`](../scripts/render_cli_help.py).
- [`scripts/pre-commit`](../scripts/pre-commit) — the hook installed
  by `scripts/install-hooks.sh`.
