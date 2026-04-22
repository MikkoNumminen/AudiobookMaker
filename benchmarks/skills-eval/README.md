# Skills evaluation — iteration 1

Measured impact of the four project-local Claude Code skills in
[`.claude/skills/`](../../.claude/skills/). This directory is the raw
evidence behind the summary table in the main [README.md](../../README.md)
"Claude Code skills" section.

## What gets measured

For every skill we run the same set of realistic task prompts twice:

- **`with_skill`** — the session has the skill loaded. Claude loads the
  `SKILL.md` contents into its context before planning a response.
- **`without_skill`** — identical prompt, identical model, identical
  effort level — but no skill file available. Claude has to work out
  the same steps from scratch, using only the repo's `CLAUDE.md` and
  whatever it can discover by reading the tree.

Each config is run **`n = 3`** times. The numbers in each per-skill
table are means ± standard deviation across those three runs.

Each run produces a set of outputs (plan, commit messages, file edits),
which a graded rubric scores against expected behaviour for that
eval. A run "passes" when the rubric criteria are met. The pass-rate
column is the fraction passed. The token column is total tokens used
by the session (input + output + cache), captured from the session
logs.

## Why two metrics

Token delta alone is a lousy summary because some good skills **cost**
tokens rather than save them — they deliberately load extra
instructions so the output is more correct. The `fi-normalizer-pass`
skill is exactly that case: it uses 10.4% more tokens than going
without, but lifts the pass rate from 83% to 100%.

If you only looked at the token axis you'd delete the skill. If you
only looked at quality you might assume every skill is worth loading.
Showing both is the honest picture.

## Files here

- [`benchmark.json`](benchmark.json) — machine-readable aggregate of
  every number on this page, one record per skill, both configs, every
  eval.
- [`release-cut.md`](release-cut.md) — release-cut per-eval breakdown
- [`work-session.md`](work-session.md) — work-session per-eval breakdown
- [`pronunciation-corpus-add.md`](pronunciation-corpus-add.md) —
  pronunciation-corpus-add per-eval breakdown
- [`fi-normalizer-pass.md`](fi-normalizer-pass.md) — fi-normalizer-pass
  per-eval breakdown (prototype skill, not yet in
  [`.claude/skills/`](../../.claude/skills/) — the data is here because
  it is the cleanest example of the "costs tokens, gains quality"
  pattern)

## Caveats

- **Iteration 1.** Three runs per config is enough to smell cache luck
  vs. real signal, but it is not a publishable scientific result.
  Treat the absolute numbers as illustrative, the direction and
  relative ordering as reliable.
- **One developer, one project.** These numbers come from one
  codebase running in real conditions against real prompts. Your
  mileage on a different codebase will differ.
- **Model and effort are held constant** within each skill's three
  runs, but the choice of model/effort is recorded per skill in the
  aggregate JSON.
- **Per-run output artefacts were excluded on purpose.** Several evals
  reference a specific copyrighted book that is used locally for
  testing; the detailed per-run outputs mention it by name. Those
  artefacts stay outside the repository. The aggregates and pass-rate
  rubrics here are the abstracted, publishable form.

## Methodology — how to reproduce

Each run captured the full Claude Code session log as JSONL. Tokens
are taken from the log's usage records (`input_tokens`,
`output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`) summed across the session. Durations are
wall-clock from session start to session end.

Pass rates were graded by a second Claude session per run, given the
run's outputs plus the eval's written expectations. Rubrics are
boolean per expectation; aggregate pass rate is the mean.
