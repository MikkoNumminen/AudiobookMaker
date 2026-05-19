---
name: ai-codegen-smell-audit
description: Read-only audit for specific failure modes that recur in LLM-generated code — defensive guards on impossible cases, generic names in domain code, swallowed errors, single-use helpers, mirror tests, and so on. Each check has a concrete smell example and a concrete legitimate counter-example so the auditor (human + assistant) can tell signal from noise. Produces `docs/audits/ai-smell-<YYYY-MM-DD>.md` with severity-ranked findings. Use whenever the user says "smell audit", "check for AI-codegen smells", "review this branch for LLM-style sludge", "audit the code for generated-code patterns", or asks for a calibration pass before merging a large generated diff. NOT a witch hunt for "AI-written code" — every finding must be a specific testable pattern with a concrete example, not a vibe.
---

# ai-codegen-smell-audit

Reads the codebase (or a specified directory / branch diff) looking for
ten specific failure modes that LLM-generated code produces at higher
rates than careful human authors. Reports findings in a markdown table
under `docs/audits/`. **Does not modify code.** The human decides which
findings are real.

## Why this skill exists

LLM-generated code has a recognisable surface texture: defensive checks
on values the type system already guarantees, generic names like
`data` / `result` in code that has a clear domain vocabulary, mirror
tests that re-state the implementation instead of asserting behaviour,
single-use helpers extracted to look modular. None of these are wrong
on their own — every pattern in the list also has a legitimate use —
but at high density they make a codebase harder to read, harder to
refactor, and slower to onboard onto.

A free-form "review this for AI sludge" prompt produces a witch hunt:
the reviewer strips out useful patterns (justified type annotations,
defensive code at trust boundaries) because they "look generated". This
skill is the antidote — every check is a specific testable pattern
with a clear smell example and a clear legitimate example. If a
finding cannot meet that bar, it does not appear.

The skill is grounded against this repo (see "Calibration against
src/" below). On AudiobookMaker's `src/` tree, two checks turn up
verified hits — `stylistic-drift-within-file` and `generic-names-in-
domain-context`. The remaining eight either find nothing here
(this codebase has had careful human review) or are mixed/unclear
pending finer analysis. That is the expected baseline; the skill
becomes more useful on fresh AI-heavy diffs that have not yet been
hand-reviewed.

## When to invoke

- "smell audit", "check for AI-codegen smells", "review this branch for LLM-style sludge"
- Before merging a large generated diff (multiple files of fresh
  code from a single generation pass)
- On a directory the user is about to onboard onto and wants
  cleaned up
- After running `audit` (the robustness audit) — this skill is the
  *readability / fingerprint* pass, complementary to robustness
- On an individual PR before final review

## When NOT to invoke

- **Not** as a substitute for `audit`. This skill finds readability
  and habit smells, not robustness bugs. Run both if you want both.
- **Not** during initial code generation. The skill would chase its
  own tail — the model would adjust to its own checks. Use only after
  the diff exists and is ready for review.
- **Not** as a style linter. If your project has explicit style
  conventions (quote style, import order, line length), use the
  linter for those. This skill targets patterns no linter catches.
- **Not** a witch hunt. The skill never claims "this was written by
  an AI" — only that a specific concrete pattern appears at a given
  line and matches a documented smell shape.

## What this skill does NOT do

- **Does not detect "AI-written code" in a tribal sense.** Findings
  are about concrete patterns, not authorship.
- **Does not modify code.** Output is a markdown report. The human
  picks fixes.
- **Does not flag defensive programming with a documented reason.**
  A `# Defensive: caller in src/foo.py passes None on cache miss`
  comment immunises the next line.
- **Does not flag stylistic choices the codebase has explicitly
  adopted.** If `CLAUDE.md` or `docs/CONVENTIONS.md` documents a
  convention (e.g. "always use `Path`, never `os.path`"), it is the
  house style — not drift.
- **Does not flag patterns at trust boundaries.** Defensive checks
  on user input, network responses, filesystem reads, or third-party
  API responses are legitimate.

## The checklist

Each check has a `Pattern`, a `Why`, a `Smell example`, a `Legitimate
example` with reason, and a `Severity default`. The auditor can
upgrade or downgrade severity per finding when context warrants.

### 1. defensive-checks-for-impossible-cases

- **Pattern.** `if x is None:` or `isinstance(x, T)` guards on
  function parameters whose type annotation already excludes the
  guarded value. Grep (requires multiline mode — `rg -U` or
  `grep -Pz`): `def \w+\([^)]*: \w+[^=]*\)[^:]*:\s*\n\s+if\s+\w+\s+is\s+None`.
  **Known grep limitation:** this pattern only matches when the
  `is None` check is the literal first body line. A docstring or any
  other prefix statement defeats it — for the general case you need
  an AST parse, not grep. The CI script
  (`scripts/check_codegen_smells.py`) shares the same limitation.
- **Why.** Adds noise, suggests the contract is uncertain, and trains
  callers to pass `None` "just in case" because the function tolerates
  it. Type system loses its meaning.
- **Smell example.**
  ```python
  def render(text: str) -> str:
      if text is None:        # ← type says str; None is impossible
          return ""
      return text.upper()
  ```
- **Legitimate example.**
  ```python
  def render(text: str | None) -> str:
      if text is None:        # ← type explicitly allows None
          return ""
      return text.upper()
  ```
  Or: defensive guard at a trust boundary (config-loaded value, JSON
  field, subprocess stdout) with a comment naming the boundary.
- **Severity default.** Minor. Upgrade to Major when the guard
  masks a real upstream bug (the "impossible" case actually fires
  in production and the function silently returns garbage).

### 2. stylistic-drift-within-file

- **Pattern.** Multiple conventions for the same thing in one file:
  mixed single/double quotes for the same kind of string, mixed
  `Path()` and `os.path.join()`, mixed `f"..."` and `.format()`,
  inconsistent docstring style (some `"""one line"""`, some `"""
  multiline\n"""` for similar functions).
- **Why.** Suggests the file was edited in passes that did not
  read each other. Slows readers down and signals further drift
  elsewhere.
- **Smell example.** Same module: `path = os.path.join(root, "data")`
  on line 12, `out = Path(root) / "out"` on line 28, `tmp =
  f"{root}/tmp"` on line 41 — three idioms, same operation.
- **Legitimate example.** A "legacy section" with a clearly-marked
  comment (`# Legacy I/O path — kept for back-compat with v1.x`).
  Or a file deliberately bridging two APIs at a boundary.
- **Severity default.** Minor. Skip entirely if the project has no
  style guide on this axis.

### 3. paraphrase-comments

- **Pattern.** Comments that restate the next line of code in
  English without adding intent, constraint, or reasoning.
- **Why.** Doubles the surface area of every change (now the comment
  rots too) and tells the reader nothing the code did not already
  say.
- **Smell example.**
  ```python
  # increment the counter
  counter += 1

  # Loop over the list of items
  for item in items:
      ...
  ```
- **Legitimate example.**
  ```python
  # Increment by 2 to skip the parity bits — see RFC 4648 §5.
  counter += 2
  ```
  Comment carries *why*, not *what*.
- **Severity default.** Nit. Mass occurrences (>5 in one file)
  upgrade to Minor — that density is a fingerprint.

### 4. single-use-helpers

- **Pattern.** Private helpers (`_foo`) defined in a module and
  called exactly once in that module. Inlining would shorten the
  file and remove a layer of indirection.
- **Why.** Premature factoring. The "modular" shape was generated
  by reflex, not by need.
- **Smell example.** `_parse_int_safely(s)` defined as a 3-line
  function called once in the same file, when `int(s) if s.isdigit()
  else 0` inline would be just as clear.
- **Legitimate example.** Helper has a documenting name
  (`assert_never_happened`, `_emit_progress_safely`) that carries
  intent the inline form would lose. Or the helper is tested
  directly (called from tests, even if called once in src).
- **Severity default.** Nit. Upgrade if the helper hides a side
  effect or makes flow harder to follow.

### 5. generic-names-in-domain-context

- **Pattern.** Variables / parameters named `data`, `result`,
  `processed`, `temp`, `handle`, `obj`, `info`, `output` in a module
  where a concrete domain term obviously fits (`voice_pack`,
  `chunk`, `engine_status`, `release_metadata`).
- **Why.** Erases the domain. Readers re-derive what `data` is on
  every line. Refactors are riskier because rename-symbol catches
  nothing meaningful.
- **Smell example.**
  ```python
  data = json.loads(response.text)
  if data.get("tag_name"):           # ← it's a GitHub release
      result = data["assets"]        # ← `result` is the asset list
      for item in result: ...        # ← `item` is one asset
  ```
- **Legitimate example.** A truly generic utility (a JSON
  pretty-printer, a hash function) where the inputs are by
  definition arbitrary. Or a one-line lambda parameter.
- **Severity default.** Minor. Upgrade to Major in code that other
  humans will read often (public APIs, GUI handlers, anything in a
  "load-bearing" file named in CLAUDE.md or ARCHITECTURE.md).

### 6. swallowed-errors

- **Pattern.** `except Exception: pass`, `except: pass`, `except
  Exception: return None / return default` with no log call, no
  re-raise, no documented rationale in a comment.
- **Why.** Errors disappear silently. Debugging a downstream
  failure becomes archaeology.
- **Smell example.**
  ```python
  try:
      voice = load_voice(path)
  except Exception:
      pass     # ← what failed? what does the rest of the code do now?
  ```
- **Legitimate example.**
  ```python
  try:
      _emit_progress(percent)
  except Exception as e:
      # GUI may have been torn down mid-callback; emit is best-effort.
      log.debug("progress emit dropped: %s", e)
  ```
  Has a log call AND a comment naming the reason.
- **Severity default.** Major. Bare `except: pass` upgrades to
  Critical when it wraps a write or a state mutation.

### 7. mirror-tests

- **Pattern.** Tests whose body is structurally identical to the
  implementation — same control flow, same branches, same constant
  values. The test would pass for any function with the same shape,
  not for the specific behaviour.
- **Why.** Locks in the implementation, not the contract. Refactors
  must rewrite the tests in lockstep, and the tests no longer catch
  the behaviour they were meant to assert.
- **Smell example.**
  ```python
  # impl
  def even(n): return n % 2 == 0

  # test
  def test_even():
      assert (4 % 2 == 0) == even(4)   # ← restates the impl
  ```
- **Legitimate example.**
  ```python
  def test_even():
      assert even(4) is True
      assert even(5) is False
      assert even(0) is True           # ← edge case the impl could regress on
  ```
- **Severity default.** Minor. Upgrade if the test file is the
  primary safety net for a load-bearing module.

### 8. phantom-todos

- **Pattern.** `# TODO`, `# FIXME`, `# XXX`, `# HACK` comments with
  no owner, no issue link, and no specified condition for when the
  TODO can be resolved.
- **Why.** Permanently stale by design. Every reader has to decide
  whether the TODO is still relevant, and the cost compounds.
- **Smell example.** `# TODO: handle Unicode edge cases`
- **Legitimate example.** `# TODO(numminen, 2026-Q3): remove the
  legacy chatterbox_fi alias after two release cycles — issue #<n>`
- **Severity default.** Nit. Upgrade to Minor if there are more
  than 5 phantom TODOs in one file (signals a backlog disguised as
  code).

### 9. duplicated-helpers

- **Pattern.** Two functions in the same module (or in
  sister modules) with ≥80 % structural similarity — same arg
  shape, same control flow, only a constant or single line differs.
- **Why.** Diverging copies silently — bug fixes land in one, miss
  the other. A parameterised single function would catch the
  divergence at compile/import time.
- **Smell example.** `_save_voice_pack_v1(pack, path)` and
  `_save_voice_pack_v2(pack, path)` whose bodies differ only in a
  format-version constant and one field name. Both called from
  different places that should both have been migrated.
- **Legitimate example.** Two functions that look similar but have
  meaningfully different invariants (one writes atomically via a
  tempfile + rename; the other appends without a lock). Surface
  the invariant in the name or a docstring.
- **Severity default.** Minor. Upgrade if both copies are called
  from production code paths.

### 10. over-typed-primitives

- **Pattern.** `typing.NewType`, `Literal["x", "y"]`, `TypedDict`,
  branded types, `satisfies` (TS), `as const` (TS) on values where
  plain `str` / `int` carry the same effective safety. Heavy
  decorator stacks (`@dataclass(frozen=True, kw_only=True, slots=
  True)`) on data classes that hold two strings.
- **Why.** Type scaffolding without a payoff. Readers spend
  attention on the type, the runtime still treats the value as a
  string, and refactors get harder.
- **Smell example.**
  ```python
  EngineId = NewType("EngineId", str)
  def get_engine(id: EngineId) -> TTSEngine: ...
  # caller still writes:
  engine = get_engine("chatterbox_grandmom")   # ← plain str works
  ```
- **Legitimate example.** A `NewType` used to enforce that the
  argument came through a normalisation function (e.g.
  `canonical_engine_id`) — the type carries a real invariant the
  string alone cannot.
- **Severity default.** Minor. Skip in projects that have
  explicitly adopted heavy typing as a convention.

## Calibration rules

These rules are blocking — apply them before recording any finding.

- **Trust boundaries are immune.** Defensive checks on user input,
  filesystem reads, network responses, subprocess stdout, and third-
  party API responses are legitimate.
- **Documented intent immunises a line.** A comment on the line
  above (`# Defensive: ...`, `# Helper for readability...`,
  `# Mirror test — locks in v1 contract`) means the author considered
  the pattern and chose it. Do not flag.
- **House-style conventions are not drift.** If `CLAUDE.md`,
  `docs/CONVENTIONS.md`, or a top-of-file comment documents a
  convention, follow it instead of the generic check.
- **Generic-utility files are immune to generic-names.** A file
  named `utils.py`, `helpers.py`, `_internal.py` whose docstring
  says "generic helpers" can use `data`, `result`, etc.
- **Test fixtures get a free pass on most checks** — fixtures
  deliberately mimic shape, often duplicate helpers, often use
  generic names. Only flag fixtures for `swallowed-errors` and
  `phantom-todos`.
- **One occurrence is data; many is a fingerprint.** Single
  occurrences of any check are Nit by default. Density (≥5 in one
  file, ≥20 in a directory) upgrades severity by one level.

## Output format

Write `docs/audits/ai-smell-<YYYY-MM-DD>.md`. If a file for the same
date already exists, suffix with `-v2`, `-v3`, etc. — never
overwrite.

Report structure (exact headings, in order):

```markdown
# AI-codegen smell audit — <YYYY-MM-DD>

## Summary
- Commit audited: `<git rev-parse HEAD>` on branch `<git branch --show-current>`
- Scope: <files / directories / branch diff>
- Total findings: N (critical: N · major: N · minor: N · nit: N)
- False-positive log applied: <yes — N findings suppressed | no — first run>

## Findings

| Check | Severity | Location | Snippet | Suggested action |
|---|---|---|---|---|
| defensive-checks-for-impossible-cases | minor | `src/foo.py:42` | `if x is None: return ""` | Drop the guard; type already excludes None. |
| generic-names-in-domain-context | minor | `src/auto_updater.py:253` | `data = json.loads(...)` | Rename to `release_data`. |
| ... | | | | |

## Findings by severity

### Critical
- ...

### Major
- [src/foo.py:120](src/foo.py#L120) — `except Exception: pass` swallows write errors silently. (swallowed-errors)
- ...

### Minor
- ...

### Nit
- ...

## False-positive log

<!--
Append findings the auditor reviewed and dismissed. The next run
reads this section and skips any finding whose `Check + Location`
appears here, unless the line has changed since.

Format:
- `<check-name>` at `<file:line>` — reviewed YYYY-MM-DD by <name>: <why it's a false positive>
-->

(empty on first run)

## Patterns observed but not in the checklist

<Free-form notes — patterns that recurred during the audit but
don't match any of the ten checks. Useful input for evolving this
skill.>
```

## How to run the audit

1. **Determine scope.** Default to `src/` of the current repo. If the
   user names a directory or "this branch's diff", honour that —
   compute the file list via `git diff --name-only <base>...HEAD`.

2. **Per check, grep + read.** For each of the ten checks, run the
   pattern listed and read the surrounding context (5–10 lines)
   before recording. Pattern-matching is a starter; the read
   confirms the smell shape matches and applies calibration rules.

   **Fan out by default.** Dispatch parallel `Agent` calls with
   `subagent_type: "Explore"` — one agent per 2–3 checks — in a
   single message. Suggested bundles: `{1, 2}` (defensive +
   stylistic-drift), `{3, 4}` (paraphrase + single-use), `{5, 6, 7}`
   (generic names + swallowed + mirror tests), `{8, 9, 10}` (phantom
   TODOs + duplicated helpers + over-typed). Each agent reports
   findings in the standard table-row format and the main run
   merges them, then applies the false-positive log and severity
   recount **after** all agents return. Agents do not need to
   consult the false-positive log themselves; the main run filters
   merged findings in step 3. If any agent fails or returns an
   error, the main run re-attempts that bundle serially rather than
   dropping the missing checks silently. Only fall back to fully
   serial per-check grep when the scope is one small file
   (≤200 LOC) and the parallel overhead would dominate.

3. **Apply the false-positive log.** Before recording a finding, look
   at the previous report's "False-positive log" section. If a
   matching `<check-name>` at `<file:line>` was dismissed and the
   line at that location has not changed (verify with `git blame -L
   N,N <file>`), skip it silently.

4. **Recount severities.** Before writing the Summary line, count
   findings per severity level. Make the Summary match the body
   exactly.

5. **Write the report.** Do not suggest fixes inline. The
   "Suggested action" column is a one-line nudge, not a patch.

6. **Stop.** This skill is read-only. Fixes happen on a follow-up
   branch the user opens by hand.

## Lightweight CI companion

The full skill needs an LLM to apply the calibration rules (trust
boundaries, documented intent, density thresholds). CI cannot run a
model, so a deterministic subset runs on every push to `master` and
weekly via cron:

- **Workflow:** [`.github/workflows/codegen-smell-audit.yml`](../../../.github/workflows/codegen-smell-audit.yml)
- **Script:** [`scripts/check_codegen_smells.py`](../../../scripts/check_codegen_smells.py)

The CI subset covers four of the ten checks — the ones that survive
pure grep without semantic reasoning:

- `phantom-todos` and `swallowed-errors` are **gating** (job fails
  if `src/` grows any new hits — both are zero today, so the gate is
  green on day one).
- `defensive-checks-for-impossible-cases` and `over-typed-primitives`
  are **warnings** (reported in the step summary, do not fail the
  job — they have too many edge cases to gate on without a model).

The remaining six checks need the full LLM-driven skill to apply
calibration. Run it by hand (or from this skill) when you want the
full pass.

The CI gate is intentionally minimal — its job is to catch
regressions, not to grade code. False positives waste developer
attention; the script's docstring documents every exclusion (owner-
tagged TODOs, `typing` imports, `T | None` parameters, etc.) so the
reader can audit what's actually being checked.

## Failure modes of this skill

Honest list — the auditor should know these going in:

- **Under-detects in heavily abstracted codebases.** Single-use
  helpers, mirror tests, and paraphrase comments hide behind
  function-call indirection; the patterns are still there but the
  greppable surface is gone.
- **Over-flags in test fixtures.** Mitigated by the fixtures-immune
  rule above, but a fixture-shaped file outside `tests/` (a
  conftest-like helper module) can still attract noise.
- **Brittle against renamed checks.** The false-positive log keys
  by `<check-name> + <file:line>`. Renaming a check (e.g.
  splitting `defensive-checks-for-impossible-cases` into two finer
  checks) silently invalidates the log entries — they need a manual
  migration. Documented here so it does not silently happen.
- **Cannot grade architectural decisions.** "This module should not
  exist" is out of scope; the skill audits patterns, not whether
  the pattern *should* be there.
- **Cannot run on partial generations.** If a diff is half-written,
  the skill will flag the WIP scaffolding as smells. Run only after
  the diff is ready for review.
- **No language coverage beyond grep heuristics.** The skill works
  on any language but does best on Python and TypeScript where the
  smell shapes are most stable. Rust / Go / Swift findings will
  skew toward `generic-names` and `swallowed-errors` (the two most
  language-agnostic checks).
- **CI subset is necessarily narrow.** The 4-check CI gate (see
  "Lightweight CI companion" above) can only enforce patterns that
  don't need semantic judgement. Six of the ten checks are
  LLM-only — they will not catch regressions on their own; the
  human-invoked full pass remains the canonical check.

## Eval-schema test

The skill's `evals/evals.json` schema is validated by
[`tests/test_skill_evals.py`](../../../tests/test_skill_evals.py),
which runs as part of the project test suite (pre-commit + CI).
The test walks every `.claude/skills/*/evals/evals.json` and asserts:

- `skill_name` matches the parent directory name (catches copy-paste
  forks where the slug never got updated)
- `evals` is a non-empty list
- Every entry has `id` (unique int), `name` (unique kebab-case
  string), `prompt` (non-empty), `expected_output` (non-empty), and
  a `files` list

So a malformed eval file fails the test suite, not silently at
audit time. The schema is intentionally small — it catches drift,
it does not validate semantic content.

## Calibration against this repo (`src/`)

This calibration ran on AudiobookMaker's `src/` tree as part of the
skill's initial build (2026-05-17). It is the empirical evidence we
have on the checks against this repo today — not a claim that all
ten checks are grounded here. **Citations were verified by
spot-reading each cited line in the working tree on 2026-05-17**,
not by grep alone — any "GROUNDED" row below names a specific line
the auditor can open and see the pattern.

| Check | Verdict on this repo | Concrete hit (when grounded) |
|---|---|---|
| defensive-checks-for-impossible-cases | **NO HITS** | No function-parameter type guard that contradicts its annotation found in a sampled sweep — kept because the check is grounded in generated code generally and will fire on fresh AI diffs |
| stylistic-drift-within-file | **GROUNDED (fixed in PR #67)** | `src/cleanup.py:106` used `os.path.getsize(os.path.join(...))` while the rest of the same file built paths via `Path()`; rewritten to `(Path(root) / f).stat().st_size` and confirmed in the 2026-05-17-v2 second-run audit |
| paraphrase-comments | **NO HITS** | None found — codebase has been human-reviewed |
| single-use-helpers | **MIXED** | No clean false-positive, no clean true-positive in samples — needs full call-graph analysis to confirm either way |
| generic-names-in-domain-context | **GROUNDED (fixed in PR #67)** | `src/auto_updater.py:253` had `data = json.loads(...)` for a GitHub release response — subsequent code read `data.get("tag_name")`, `data.get("assets", [])`. Renamed to `release_data` across the six reads in `check_for_update`; confirmed in the 2026-05-17-v2 second-run audit |
| swallowed-errors | **PATTERN WIDESPREAD, SAMPLED CALIBRATION-IMMUNE** | `except Exception: pass` and `except Exception: return <default>` shapes appear at ~40 sites across ~20 files in `src/` (desktop GUI + optional-import + subprocess teardown make these common). Three sampled and confirmed calibration-immune: `src/engine_registry.py:36-37` (optional-import swallow, preceded by an explicit comment block); `src/engine_installer.py:795-796` (best-effort subprocess kill cleanup, inside an outer error handler); `src/app_config.py:29-30` (locale fallback, rationale in the function docstring). Bare `except:` (no exception type) is genuinely absent. A full enumeration was NOT done — the sample is illustrative, not exhaustive, and some unsampled sites may lack a rationale comment. The first-pass calibration claimed "NO HITS" outright — that was wrong on the literal pattern; this entry was rewritten 2026-05-19 after a post-merge skeptical review surfaced the misclaim, and tightened further the same day after a second adversarial pass revealed the original correction undercounted by ~10x |
| mirror-tests | **NO HITS** | Sampled `test_tts_normalizer_fi.py`, `test_tts_audio.py`, `test_cleanup.py`, `test_tts_chunking.py` — all assert real behaviour |
| phantom-todos | **NO HITS** | Zero `# TODO` / `# FIXME` in `src/` |
| duplicated-helpers | **UNCLEAR** | `tts_normalizer_fi.py` has many similar regex builders, but they are intentionally distinct passes — would need cross-module similarity analysis to confirm |
| over-typed-primitives | **NO HITS** | No `Literal` / `NewType` / `TypedDict` overuse — codebase uses plain dataclasses |

**Calibration verdict.** Only 2 of the 10 checks turn up verified
hits in `src/` today (stylistic-drift, generic-names). The other 8
either find nothing here (the codebase has been hand-reviewed) or
are mixed/unclear pending finer analysis. The "NO HITS" checks stay
in the skill because they are grounded against LLM-codegen
*generally* — they target patterns documented in independent reviews
of generated code and will fire on a fresh AI diff that has not
been hand-reviewed. This skill is most useful on those diffs, not on
a codebase that has already had careful human review.

**Honest caveat.** A more aggressive calibration sweep — full
call-graph analysis for single-use-helpers, structural similarity
analysis across modules for duplicated-helpers, function-signature
parsing for defensive-checks — would likely surface more grounded
hits. The 2/10 number is a floor, not a ceiling.

**Second-run verification (2026-05-17, post-PR #67).** A second
audit ran the same 4-parallel-sub-agent pattern against `src/` after
both first-run findings were fixed. Both citations now point at
clean code (`(Path(root) / f).stat().st_size` and `release_data =
json.loads(...)` respectively). The second-run sweep reported zero
new findings on the other nine checks. Two later skeptical
re-reviews on 2026-05-19 surfaced additional misses that neither
self-review caught: the `swallowed-errors` calibration row was wrong
on the literal pattern (now corrected, see table above), and the
first correction undercounted the affected sites ~10x. Honest
summary: the self-review loop did NOT catch its own blind spots —
**external skeptical re-review** is what surfaced both misclaims.
That review pattern (re-audit after merge by a different agent) is
now part of the AI-first cadence documented in
[`docs/AI_FIRST_GUIDE.md`](../../../docs/AI_FIRST_GUIDE.md). This is
one repo over a few days; the calibration table is one data point,
not validated empirics. Full second-run report at
[`docs/audits/ai-smell-2026-05-17-v2.md`](../../../docs/audits/ai-smell-2026-05-17-v2.md).

**One pattern observed in `src/` that is NOT in the ten checks** —
a candidate for a future check once it shows up across multiple
repos: `src/voice_pack/expression.py:72-78` has a `frozen=True`
dataclass whose `__post_init__` uses `object.__setattr__` to clamp
values rather than validate or raise. Signal of "I wanted validation
but did not want to write a custom `__init__`." A future check
`post-init-mutation-workaround` could target this, but a single
observation in a single repo is not enough evidence to promote it.
The "Patterns observed but not in the checklist" section in the
report exists exactly so future runs accumulate that evidence
before the skill grows.

## Things NOT to do

- **Never modify code.** This skill is read-only. Suggested actions
  are one-line nudges, not patches.
- **Never fabricate a finding.** Every entry must cite a real
  `file:line` you can point at in the working tree.
- **Never claim a finding proves the code was AI-generated.**
  Findings cite concrete patterns, not authorship.
- **Never re-flag a dismissed false positive on an unchanged line.**
  The false-positive log is load-bearing — read it first.
- **Never auto-fix.** A `--fix` flag would defeat the point: the
  human judgement that distinguishes signal from noise is the
  product, not an obstacle.
- **Never run during initial code generation.** The skill would
  chase its own tail. Run on finished diffs only.
