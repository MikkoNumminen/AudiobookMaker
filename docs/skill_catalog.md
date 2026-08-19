# Skill catalog

Every skill committed under [`.claude/skills/`](../.claude/skills/), what it
costs, and whether the last audit said to keep it.

This lives here rather than in the README because it is a maintenance record
for people working ON the project, not something a user or a first-time
contributor needs in order to use AudiobookMaker. The README links here.

`scripts/check_skill_catalog.py` runs on every commit and fails if the rows or
the count below drift from the directory, so this page cannot quietly go stale
the way a hand-maintained list normally does. If you add or remove a skill,
update the table and the count in the same commit.

For how skills fit alongside the other pillars of this project, see
[AI_FIRST_GUIDE.md](AI_FIRST_GUIDE.md).

## The catalog (audited 2026-05-19; 4 skills retired)

12 in-repo skills under `.claude/skills/`. Per-session cost when not invoked: ~30 tokens each (catalog entry only). Per-invocation cost: the SKILL.md body loads. The 2026-05-19 audit retired four skills that either duplicated CLAUDE.md rules (which auto-load every session) or had zero recorded invocations and trivially-rederivable workflows: `audit-followup`, `commit-then-scan`, `pre-push-scan`, `scanned-pdf-to-audiobook`. Two skills (`engine-venv-triage`, `narrate-texts`) landed after the audit and have not yet been through an audit pass.

**Body size** is the per-invocation load. **Saves/inv** is the rough order-of-magnitude tokens saved versus an agent re-deriving the workflow from first principles. **Usage** is rough 90-day evidence (artefacts on disk, commit log, tool runs).

| Skill | Body | Saves/inv | 90-day usage | Verdict |
|---|---|---|---|---|
| [`ai-codegen-smell-audit`](../.claude/skills/ai-codegen-smell-audit/SKILL.md) | ~7.3k | ~5–6k | 2 audits + heavy iteration | **KEEP** — load-bearing, non-derivable taxonomy |
| [`audit`](../.claude/skills/audit/SKILL.md) | ~4.8k | ~3–4k | 3 reports landed | **TRIMMED + CALIBRATED 2026-05-20** — Python-only Phase 1; added a Calibration section listing 8 recurring FP patterns (Tkinter single-threaded, `stderr=STDOUT`, CTkImage materialization, etc.) baked from the 2026-05-19 audit's 18 FPs; calibration directive embedded inside each of the 5 subagent template fences so subagents actually receive it |
| [`ci-failure-triage`](../.claude/skills/ci-failure-triage/SKILL.md) | ~1.9k | ~1k | 22 `fix(ci):` commits | **KEEP** — high recurrence; ordering is non-obvious |
| [`copyright-scan`](../.claude/skills/copyright-scan/SKILL.md) | ~3.1k | ~3k | 0 invocations | **TRIM** to ~600t — keep allow-list + decision tree, drop runbook |
| [`engine-venv-triage`](../.claude/skills/engine-venv-triage/SKILL.md) | ~1.4k | ~2–3k | born from the v3.16.0–v3.17.3 field saga (PRs #107–#113) | **KEEP (post-audit addition)** — provenance-first diagnosis ladder for end-user engine failures, built from a saga where every plausible first guess was wrong; not yet through an audit pass |
| [`narrate-texts`](../.claude/skills/narrate-texts/SKILL.md) | ~1.6k | ~8–10k | born from a 14-file blog batch that shipped three separate truncation bugs | **KEEP (post-audit addition)** — bundles the batch runner and the verifier as scripts so neither is rewritten inline, and encodes the verification discipline (transcript, not duration) that four normalizer bugs were only caught by; not yet through an audit pass |
| [`pronunciation-corpus-add`](../.claude/skills/pronunciation-corpus-add/SKILL.md) | ~1.8k | ~1.5k → 0 | corpus file empty today | **KEEP provisional** — re-audit after 10 entries land; corpus format then self-documents |
| [`release-bundle-audit`](../.claude/skills/release-bundle-audit/SKILL.md) | ~3.8k | ~3k | 1 use (its own birth) | **TRIMMED 2026-05-20** — cut verbose prose; kept exclude list verbatim, gotchas, and decision criteria |
| [`release-cut`](../.claude/skills/release-cut/SKILL.md) | ~1.7k | load-bearing | 20 releases in 90d | **KEEP** — auto-update is P0; ritual ordering not in CLAUDE.md |
| [`voice-pack-finnish`](../.claude/skills/voice-pack-finnish/SKILL.md) | ~6.2k | ~5–8k | 2 packs + ~60 probe runs | **KEEP** — encodes empirical scar tissue (pyannote/ECAPA fallback) not in any CLI `--help` |
| [`work-session`](../.claude/skills/work-session/SKILL.md) | ~1.7k | ~2k | TODO.md actively used | **KEEP** — coordinates the 4-session parallel-Claude protocol |
| [`worktree-launch`](../.claude/skills/worktree-launch/SKILL.md) | ~1.3k | ~0.8k | 63 active worktrees | **TRIMMED 2026-05-20** — deduped Why/incident-context vs CLAUDE.md; kept slot-picking, worktree creation, surgical-revert procedure (not in CLAUDE.md), cleanup |

**Net after retirement + trims + calibration:** 6 KEEP, 3 TRIMMED 2026-05-20, 1 TRIM deferred (`copyright-scan` — trim not yet applied; the skill is tracked in-repo like the rest). Skill surface: ~45k tokens (14 skills) → ~34k (10 skills after PR #73 retirements) → ~32.9k (PR #79 trims) → **~33.6k tokens** (this PR added a ~0.6k Calibration section plus a ~0.1k per-template calibration directive to `audit/SKILL.md`; net savings vs origin/master-pre-PR-79 are still −0.4k across these three skills, just smaller than the headline trim because the calibration is load-bearing knowledge that needs to reach every subagent). The post-audit `engine-venv-triage` adds ~1.4k on top of those audited-ten figures. Acting on the deferred `copyright-scan` trim would shrink further to ~31k tokens. Audit verdicts are from a single pass on 2026-05-19; prior PR cycles in this repo have shown even adversarial sub-reviewers miss things on the first try, so verdicts on the surviving skills remain input, not final policy.
