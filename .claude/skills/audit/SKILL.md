---
name: audit
description: Run a multi-phase robustness audit of the current codebase. Phase 1 tries language-appropriate static-analysis tools, skipping cleanly if absent. Phase 2 spawns five parallel subagents across non-overlapping scopes — resource lifecycle, data integrity, concurrency, error paths, external boundaries — each returning findings with file:line citations and severity. Phase 3 aggregates into docs/audits/audit-YYYY-MM-DD.md with a severity tally (critical / high / medium / low) and a branch topology recommendation for the fix follow-up. Use whenever the user says "audit this codebase", "find bugs", "robustness review", "review for races / leaks / error swallows", "check for hidden bugs", "comprehensive review", "shake out issues before release", or asks for a defect list. Not for style / readability reviews — see the "When NOT to invoke" section below.
---

# audit

Runs a comprehensive robustness audit in three phases. Produces a
structured report at [`docs/audits/audit-<YYYY-MM-DD>.md`](../../../docs/audits/)
plus a severity-ranked list of findings suitable for translating into
one fix branch per area.

## Why this skill exists

A single-agent code review tends to produce 5–10 high-level notes and
miss concrete cross-cutting bugs. A single model's attention is
finite; by the time it has thought about concurrency it has forgotten
the resource-lifecycle patterns it noticed earlier. Splitting the
review into five parallel subagents with non-overlapping scopes
appears to produce substantially more findings with very little
duplication, in roughly the same wall-clock time as a single pass.

On one ~150-file Python desktop app (2026-04-23) this skill produced
66 findings and landed 26 `fix(*)` commits across 7 parallel branches
— compared with ~8 findings from a free-form single-agent pass on the
same codebase the day before. That is a single observation, not a
controlled study; expect the delta to vary with codebase size,
language, and prior audit coverage.

## When to invoke

- "audit this codebase", "find bugs", "robustness review"
- Before a major release to shake out hidden races and leaks
- After a big refactor to catch cleanup-path regressions
- On unfamiliar code to produce a prioritised defect list
- Before a security review (the two overlap but are distinct — this
  skill finds robustness bugs, `/security-review` finds exploitable
  ones)

## When NOT to invoke

- Style, formatting, or readability reviews — dilutes the defect list
  and is a separate pass. Use a dedicated linter or a plain review.
- Performance profiling — this skill does not measure hotspots.
- Architectural / design review — patterns across modules, not
  per-line bugs.

## Workflow

### Phase 1 — static analysis (best effort)

1. Detect primary language(s) from repo root:
   - `package.json` or `tsconfig.json` → JS/TS (TS if `tsconfig.json` present or `.ts`/`.tsx` files exist)
   - `Cargo.toml` → Rust
   - `go.mod` → Go
   - `pyproject.toml`, `requirements.txt`, `setup.py`, or `*.py` files → Python
   - Multi-language repos: run the tool set for every detected language.
   - No known language detected: note it in the report and skip Phase 1 entirely. Do not guess.

2. For each detected language, run the tools below in order. For every tool: try it, capture stdout+stderr+exit code. If the binary is not on PATH or not installed, skip it cleanly and record it under "Skipped" with a one-line reason (`not on PATH`, `not installed`, or `detected but no config`). Never fabricate output for a skipped tool.

   **Python**
   1. `ruff check .`
   2. `mypy .`
   3. `bandit -r <src-root>/`
   4. `vulture <src-root>/`

   **JS/TS**
   1. `npx eslint .`
   2. `npx tsc --noEmit` (TS projects only)
   3. `npm audit`

   **Rust**
   1. `cargo clippy --all-targets --all-features -- -D warnings`
   2. `cargo audit`

   **Go**
   1. `golangci-lint run ./...`
   2. `staticcheck ./...`
   3. `govulncheck ./...`

3. Summarise results per language:
   - **Ran:** tool → pass / N findings (top categories)
   - **Skipped:** tool → reason
   - Quote at most the 5 highest-signal findings per tool; link to file:line.

4. Do not fix anything in this phase. Phase 1 is read-only triage — fixes land in a later phase once the full picture is in hand.

### Phase 2 — five parallel subagents

Spawn all five subagents **in parallel** (one message, five `Agent`
tool calls) — not sequentially. They do not depend on each other.
Scopes are non-overlapping by design; if an issue falls between two,
the most appropriate subagent claims it.

1. **Resource lifecycle** — file handles, subprocesses, tempfiles,
   network connections, GUI widgets. Patterns to grep:
   - `NamedTemporaryFile(delete=False)` without matching cleanup
   - `urlopen(` / `requests.get(` without `with` or explicit `.close()`
   - `Popen(` where the handle is stored on `self.` without a
     matching `__enter__` / `__exit__` or explicit `finally: .kill()`
   - `open(` outside a `with` block
   - Stored handles released only via a `_cleanup` method that is
     only called on the happy path

2. **Data integrity** — format assumptions, silent conversions,
   silent fallbacks, anchor-on-first-element patterns. Patterns:
   - Float equality (`a == b`, `total == 0.0`) where epsilon or
     `math.isclose` is required; accumulated rounding error assumed
     negligible
   - Integer narrowing / overflow — `int(x)` on a large float, 32-bit
     containers holding 64-bit ids, timestamp truncation to seconds
   - String encoding drift — implicit UTF-8/Latin-1/CP1252 conversion,
     locale-dependent `str()`, filename bytes decoded with the wrong
     codec
   - `if X is None: X = <default>` that masks an upstream load/parse
     failure instead of raising
   - Loop-first-element-sets-schema patterns — first row's columns,
     keys, or dtype silently coerce every subsequent element
   - Serialization round-trip corruption — numeric precision lost
     through JSON, datetime timezone dropped, `Decimal` → `float`,
     `dict.get(k, 0)` where "missing" is not "zero"
   - (audio/media projects) hardcoded sample rates / bit depths /
     MIME types; silent `resample` / `downmix` / re-encode without a
     mismatch check against the input

3. **Concurrency** — shared state between threads, TOCTOU windows,
   daemon threads that die silently, check-then-act races. Patterns:
   - Plain `bool` / `int` shared between worker thread and main
     thread with no `Lock` / `Event`
   - `path.exists()` followed by `open(path)` / `mkdir` / `unlink`
   - Daemon thread target where the outer `try` only covers the
     inner loop body; an exception above it kills the thread
     silently
   - Operations on a Tk / Qt queue after the window may have been
     destroyed
   - `check_something()` followed by an action that assumes the
     check is still valid (not atomic)

4. **Error paths** — swallowed exceptions, pre-append errors,
   missing try/finally, cleanup that only runs on success. Patterns:
   - `except Exception: pass` (and `except Exception: return`) with
     no log call above the return
   - State writes (`self.running = True`, `self._pending = X`)
     **before** a fallible operation, with no compensating state
     rollback on exception
   - `try` blocks that only wrap the inner call and leak resources
     on outer failures
   - `.join(timeout=…)` without a re-raise of the worker's stored
     exception

5. **External boundaries** — timeouts, path traversal, shell
   interpolation, untrusted input, SSRF-adjacent patterns. Patterns:
   - `urlopen(` / `requests.get(` with no `timeout=` argument
   - `subprocess.run` / `Popen` whose argv includes a config-derived
     path without `Path.resolve()` or explicit canonicalisation
   - String interpolation into a PowerShell here-string, shell
     command, or `system()` call without escape / quote handling
   - File path construction from user input without a containment
     check against an expected root
   - `Popen.wait()` without `timeout=` on a process that could hang

Each subagent returns findings in this exact format so Phase 3 can
aggregate without transformation:

```
- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description
```

severity ∈ {critical, high, medium, low}.

### Subagent prompt templates

Paste the corresponding block below into each `Agent` tool call. They
are intentionally terse so the subagent stays focused on grepping,
citing, and returning. Each caps the reply at ~400 words to keep the
aggregate digestible.

#### 1. Resource lifecycle
```
Your task is to audit the codebase for resource lifecycle bugs — file handles, subprocesses, tempfiles, network sockets, and GUI widgets that are created but not reliably released.

Grep for these patterns (see SKILL.md Phase 1 pattern list for the full set):
- `open(` without `with` / missing `.close()`
- `subprocess.Popen(` without `.wait()`, `.terminate()`, or context manager
- `tempfile.NamedTemporaryFile(delete=False)` without matching `os.unlink`
- `socket(`, `requests.get(stream=True)` without `.close()`
- `tk.Toplevel`, `.after(` callbacks that outlive the widget
- `threading.Thread(` / `Timer(` with no join or cancel path

Output format — one line per finding, exact template:
`- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description`
severity ∈ {critical, high, medium, low}.

Do not fabricate. Every finding must cite a real file:line you can point at in the working tree.

Cap your report at ~400 words.
```

#### 2. Data integrity
```
Your task is to audit the codebase for data integrity bugs — format assumptions, silent conversions, and silent fallbacks that corrupt user data or mask errors.

Grep for these patterns (see SKILL.md Phase 1 pattern list for the full set):
- `.encode(` / `.decode(` without explicit encoding or with `errors="ignore"` / `"replace"`
- `json.loads` / `yaml.safe_load` without schema or key-presence checks
- `int(` / `float(` on external strings without `try/except`
- `except ... : return None` or `return default` that hides a conversion failure
- sample-rate, channel-count, bit-depth assumed rather than read from the file
- `dict.get(key)` where downstream code requires the key to exist

Output format — one line per finding, exact template:
`- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description`
severity ∈ {critical, high, medium, low}.

Do not fabricate. Every finding must cite a real file:line you can point at in the working tree.

Cap your report at ~400 words.
```

#### 3. Concurrency
```
Your task is to audit the codebase for concurrency bugs — shared mutable state, TOCTOU windows, daemon-thread death, and check-then-act races.

Grep for these patterns (see SKILL.md Phase 1 pattern list for the full set):
- module-level mutable globals read/written from threads
- `os.path.exists(x)` immediately followed by `open(x)` / `os.remove(x)`
- `Thread(..., daemon=True)` doing work that must finish (writes, flushes)
- `queue.Queue` consumers with no shutdown sentinel
- GUI callbacks that mutate state touched by a worker thread without a lock
- `if self._running: ...` with no lock around the flag

Output format — one line per finding, exact template:
`- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description`
severity ∈ {critical, high, medium, low}.

Do not fabricate. Every finding must cite a real file:line you can point at in the working tree.

Cap your report at ~400 words.
```

#### 4. Error paths
```
Your task is to audit the codebase for broken error paths — swallowed exceptions, errors raised after partial writes, and missing try/finally cleanup.

Grep for these patterns (see SKILL.md Phase 1 pattern list for the full set):
- `except Exception: pass` / `except: pass` / `except ... : continue`
- `except ... as e: logger.debug(e)` with no re-raise and no recovery
- writes to an output file before validation can raise
- `open(...)` followed by work that can raise, with no `finally` and no `with`
- bare `raise` inside `except` that has already partially mutated state
- retry loops that catch and discard the final failure

Output format — one line per finding, exact template:
`- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description`
severity ∈ {critical, high, medium, low}.

Do not fabricate. Every finding must cite a real file:line you can point at in the working tree.

Cap your report at ~400 words.
```

#### 5. External boundaries
```
Your task is to audit the codebase for unsafe external boundaries — missing timeouts, path traversal, shell interpolation, and unvalidated untrusted input.

Grep for these patterns (see SKILL.md Phase 1 pattern list for the full set):
- `requests.get(` / `requests.post(` / `urlopen(` without `timeout=`
- `subprocess.run(..., shell=True)` or f-strings built into shell commands
- `os.path.join(base, user_input)` with no `os.path.realpath` containment check
- `open(user_supplied_path)` without normalising or allow-listing the root
- `zipfile.extractall` / `tarfile.extractall` without member-path checks
- `eval(`, `exec(`, `pickle.loads(` on data crossing a trust boundary

Output format — one line per finding, exact template:
`- [path/file.py:NN](path/file.py#LNN) [severity] — one-line description`
severity ∈ {critical, high, medium, low}.

Do not fabricate. Every finding must cite a real file:line you can point at in the working tree.

Cap your report at ~400 words.
```

### Phase 3 — aggregated report

Write `docs/audits/audit-<YYYY-MM-DD>.md`. If a file for the same
date already exists, suffix with `-v2`, `-v3`, etc. — never
overwrite.

Report structure (exact headings, in order):

```markdown
# Audit — <YYYY-MM-DD>

## Summary
- Commit audited: `<git rev-parse HEAD>` on branch `<git branch --show-current>`
- Coverage: Phase 1 <ran|skipped: reason>; Phase 2 ran all five
  subagents (resource lifecycle, data integrity, concurrency, error
  paths, external boundaries); Phase 3 report below.
- Total findings: N (critical: N · high: N · medium: N · low: N)
- Skipped: <tools not installed, or "none">

## Static analysis
<Phase 1 per-tool output, or a short note explaining which tools
were unavailable and why ("not on PATH" / "no config detected" /
"no recognised language in this repo")>

## Findings by area

### Resource lifecycle
<bulleted findings from subagent 1>

### Data integrity
<bulleted findings from subagent 2>

### Concurrency
<bulleted findings from subagent 3>

### Error paths
<bulleted findings from subagent 4>

### External boundaries
<bulleted findings from subagent 5>

## Recommended next steps
<grouped by severity — critical first>
<for each group: suggest the branch. Two naming conventions work:
  area-named (`audit-resources`, `audit-concurrency`) when each
  area has enough findings to warrant its own branch; or numbered
  batches (`audit-batch-1`, `audit-batch-2`, …) when batching
  mixed-area work is simpler. Note the rough commit count per branch.>
<close with: "Work in parallel across branches, merge with a single
`merge:` commit that summarises what landed.">
```

## Output discipline

- **Never fabricate.** Every finding must cite a real `file:line`
  you can point at in the working tree. If in doubt, leave it out.
- **Do not suggest fixes in the audit report.** The audit is a
  *defect list*, not a patch list. Fixes happen on follow-up
  branches so each can be reviewed independently.
- **Preserve the codebase's own naming.** Identifiers in the
  project's own language stay as-is — do not anglicise or rename
  them in quotes.
- **Severity tally must match the findings list.** If the summary
  says "critical: 7" the body must contain exactly seven findings
  tagged `critical`. Recount before writing the summary line.

## Follow-up workflow (short version)

After the report lands, the recommended loop is: user strikes any
false positives with `~~…~~` and a reason; user creates one branch
per area (or batch), critical/high first; each commit cites the
area with Conventional Commits (e.g. `fix(<area>): <what>`); merge
everything with one `merge:` commit summarising the landings.
Parallel branches are safe because the audit's five scopes are
non-overlapping by construction.

## Known limitations

- Static-analysis tools usually are not installed in application
  venvs (they live in dev venvs). Expect Phase 1 to be partial or
  skipped.
- Subagents have context limits. On repos larger than ~300 source
  files, pass each subagent a `--scope <glob>` hint so it can fit
  the relevant slice.
- Pattern-matching is heuristic. Some findings will be false
  positives; the user's sanity-check before translating to fixes is
  part of the workflow, not a weakness.
- The skill does **not** find performance hotspots (profiling is a
  different tool) and does **not** grade architectural decisions
  (that is a design review, not an audit).
