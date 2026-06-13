# Memory-to-docs migration log

The AI-first pattern in [`AI_FIRST_GUIDE.md`](AI_FIRST_GUIDE.md) has a
rule: when a per-user auto-memory note matures into stable project
knowledge, it moves into tracked docs so every contributor — human or
AI — can see it, not just the machine where the memory file lives.

This file is the tracked log of those migrations. It answers two
questions the memory files themselves cannot (they are deliberately
untracked): *what has already migrated* (so a session doesn't
re-derive or re-migrate it) and *what is still memory-only* (so
whoever touches that area next knows there is knowledge worth
pulling in).

## How to use this log

1. When you migrate a memory note into a tracked doc or skill, set its
   row to **migrated** and name the destination.
2. When you write a memory note that smells like future project
   knowledge (a technical finding, not a personal preference), add a
   row with status **pending** so the finding is discoverable before
   it matures.
3. Personal preferences (commit style, tone, language) never migrate
   and are never listed here — they stay per-user by design.

## Log

| Knowledge | Destination | Status |
|---|---|---|
| English Grandmom reads a bare `"up."` sentence unreliably (failure-word observation) | [`english_grandmom.md`](english_grandmom.md) | migrated |
| pyannote conflates similar-timbre Finnish speakers; ECAPA fallback rescues; always validate ref clips by transcript | [`voice-pack-finnish` skill](../.claude/skills/voice-pack-finnish/SKILL.md) + `CLAUDE.md` voice-extraction rules | migrated |
| `"Could not import module 'LlamaModel'"` = transformers drift inside the engine venv (not torch/GPU); provenance-first triage ladder | [`engine-venv-triage` skill](../.claude/skills/engine-venv-triage/SKILL.md) | migrated |
| Chatterbox emits rare ~1 s acoustic burst artifacts the duration band-guard cannot catch; needs an acoustic-anomaly detector tuned on real artifact audio | — | pending (memory-only) |
| Engine install should be GPU-vendor-aware (AMD ROCm + CPU fallback, not just NVIDIA cu124); blocked on real Radeon-on-Windows hardware to validate | — | pending (memory-only) |
