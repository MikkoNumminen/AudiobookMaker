# TODO

Shared task list across all Claude Code sessions. Remove completed tasks immediately — no "Recently Completed" section.
Every item must have a size estimate: 🟢 small, 🟡 medium, 🔴 large. LLM marker: ⚡ Sonnet, 🧠 Opus.
In Progress items must show the owner: `[Claude 1, main]`, `[Claude 2, worktree-name]`, etc.
4 permanent Claude instances: **Claude 1, Claude 2, Claude 3, Claude 4.**

## Status board

Update your line when you start a session, pick a task, finish, or go idle.
Any Claude can read this section to know instantly what every other Claude is doing.

| Claude | Status | Current task | Since |
|--------|--------|-------------|-------|
| Claude 1 | 🟡 blocked | Second-narrator recovery (listening confirmation needed) | 2026-04-19 |
| Claude 2 | 🟢 idle | — | — |
| Claude 3 | 🟢 idle | — | — |
| Claude 4 | 🟢 idle | — | — |

Status values: 🟢 idle · 🔵 working · 🟡 blocked · 🔴 error · ⚫ offline

**🚨 MANDATORY RULES — NO EXCEPTIONS:**

1. **Re-read this file BEFORE starting ANY work AND before every commit.** Other Claudes may have pushed changes since you last looked. `git pull` + re-read TODO.md is a single atomic action — do both every time.
2. **Pick before you start.** Move the task to "In Progress" with your name tag AND update your Status Board line to 🔵 working. BEFORE touching any code. No tag = no work.
3. **Update on every state change.** When you start, move the item to "In Progress" and set your status to 🔵. When you finish, remove the item from the list, set your status to 🟢 idle, commit + push TODO.md. When you pause mid-task, the item stays in "In Progress" and your status stays 🔵. When you're blocked, add `[BLOCKED: reason]` and set your status to 🟡.
4. **Clear completed work immediately.** Don't batch. The moment your commit is pushed, remove the item from this file and push the updated TODO.md. Stale items mislead other Claudes into thinking work is still pending.
5. **If a task already has an owner tag, do NOT touch it.** Pick something else or wait.
6. **This file is the single source of truth.** If it's not in this file, it's not being worked on. If it's still in this file, it's not done. No exceptions — the other Claudes have no other way to know what you're doing.
7. **No private task lists.** Do NOT use the internal TodoWrite tool for tracking work. ALL tasks — planned, in progress, blocked, or speculative — go in THIS file. When the user says "todo", pull this file from git and report its full contents: status board, in-progress items, and the complete backlog. The user expects one place with everything, not a split between an ephemeral in-session list and this file.

## In Progress

### 🚨 PRIORITY: Second narrator missed by 1h voice-pack run [Claude 1, main] [BLOCKED: user listening confirmation]
- [ ] The Dual Class 3 1h sample has **two narrators (Christopher Boucher + Jessica Threat)**. Pyannote collapsed them into SPEAKER_00 (54.7 min vs SPEAKER_01 0.8 min of scraps). ECAPA character clustering then split SPEAKER_00 into CHAR_A (44.4 min) + CHAR_B (10.2 min). We only trained + packaged CHAR_A. Sample clips cut to `d:/tmp/char_clips/CHAR_{A,B}_{1,2,3}.wav` for listening. Awaiting user confirmation that CHAR_A and CHAR_B are different voices (Christopher vs Jessica) before spending ~2 h GPU on the second training run. Structural follow-up (auto-package every above-floor (speaker, character) bucket) tracked separately once the first retrain confirms the approach. 🔴 🧠 Opus.

### Windows speechbrain symlink failure (port into productised ECAPA diarizer)
- [ ] When the pyannote-free diarizer is productised into `src/voice_pack/diarize_ecapa.py`, it MUST pass `local_strategy=LocalStrategy.COPY` to `EncoderClassifier.from_hparams`, or first-run fails on Windows with `OSError: [WinError 1314]`. Prototype in `d:/tmp/analyze_ecapa.py` already carries this fix — copy the same argument over. 🟢 ⚡ Sonnet.

### Chatterbox-Finnish: collect pronunciation failure corpus (seeded — keep appending)
- [ ] Corpus file lives at `docs/pronunciation_corpus_fi.md` with 5 seeded entries across 5 failure categories. Keep appending each new failing word Turo or other testers report. Target: 20 concrete entries across ≥3 categories before attempting a targeted Pass I fix. 🟡 🧠 Opus.

### Finnish voice mispronounces "s" as "sch"
- [ ] Finnish Grandmom occasionally pronounces plain `s` as `sch` (German-like sibilant). Likely candidates: normalizer context around certain `s` positions, loanword respelling, compound-seam insertion, or specific clusters (`st`, `sk`, `sp`, word-final `s`). Next step: collect 5–10 concrete failing words from a test chapter, then decide on a targeted normalization pass or adjustment. 🟡 🧠 Opus.

### Add more Chatterbox voice presets (BLOCKED — needs voice recordings)
- [ ] Only "Grandmom" exists. Adding a new preset needs a clean reference WAV first (10–20 s, 22050 Hz mono, SNR 40+ dB). Code changes in 5 locations once a sample exists. 🟡 🧠 Opus.

### NeMo text-processing for English (future quality upgrade)
- [ ] If the hand-rolled English normalizer ever shows quality gaps that warrant industrial-grade coverage, consider adopting `nemo-text-processing` directly for English. Blockers: `pynini` has no PyPI wheel for Windows; NeMo doesn't support Finnish. Only worth it if the gap is audible. 🔴 🧠 Opus.

### Finnish normalizer — Tier 1 follow-ups
- [ ] Pass I + Pass L audio validation on GPU — listen to a test-book chapter with the new passes on vs off. 🟡 🧠 Opus
- [ ] Pass I lexicon extensions as new failure classes surface in other Finnish books. 🟡 ⚡ Sonnet
- [ ] Long compound word seam splitter (Pass P) for 20+ char compounds (576 unique in test book). Needs seam lexicon or libvoikko integration. 🔴 🧠 Opus
- [ ] Governor table expansion for other Finnish books. 🟢 ⚡ Sonnet

### Voice cloning — real-world end-to-end validation
- [ ] Test `scripts/record_voice_sample.py` live with a real 12 s recording. Raise input volume to ~85% first (Zoom/Teams leaves it at ~5–10%). 🟢 ⚡ Sonnet
- [ ] If cloning quality is below v7, iterate: longer recording, more varied prosody, explicit `--ref-audio`. 🟡 🧠 Opus

### Voice pack pipeline — remaining slices (Slices 1–5 scaffolding landed)
- [ ] **Pyannote-free diarization path:** the 1h run bypassed pyannote's HF-gated model using an ECAPA-TDNN + agglomerative-clustering diarizer (prototype in `d:/tmp/analyze_ecapa.py`). Productise into `src/voice_pack/diarize_ecapa.py` + `--diarizer ecapa` flag on `voice_pack_analyze.py` so future users aren't blocked by HF account gates. 🟡 🧠 Opus.
- [ ] **Expression markup wire-up (Slice 5 inference-path integration):** consume the `ExpressionPlan` produced by `src.voice_pack.expression.parse_markup` inside `scripts/generate_chatterbox_audiobook.py` so per-sentence `exaggeration` / `cfg_weight` overrides take effect during synthesis. Optional lightweight emotion-prefix token during training. 🟡 🧠 Opus.
- [ ] **XTTS v2 bake-off (Slice 5a, research lane):** run the same source audio through Coqui XTTS v2 finetune, listen side-by-side vs Chatterbox LoRA. If XTTS clearly wins on emotional range / accent, ship as a second engine slot (private-use builds only — XTTS is CPML non-commercial). 🟡 🧠 Opus.
- [ ] **Architecture write-up (`docs/voice_pack_design.md`):** capture the design rationale — Chatterbox LoRA primary (MIT, shared inference path), <200 MB/speaker adapters, emotional range via training-data balance + inference knobs, ~5 h source is the quality ceiling. Internal dev doc; may reference the audiobook source use case. 🟢 ⚡ Sonnet.
- [ ] **License/ethics guardrail note:** voice packs stay local by default (no cloud upload, no sharing button). Capability-framed README note ("voice cloning of third-party voices is your own responsibility, keep local, don't redistribute"). 🟢 ⚡ Sonnet.

### VoxCPM2 — GPU testing (requires NVIDIA machine)
- [ ] Run `pip install voxcpm` and verify the GUI sees the engine. 🟢 ⚡ Sonnet
- [ ] Synthesize ~5000 chars in Finnish, compare against Edge-TTS Noora. 🟡 🧠 Opus
- [ ] Try `voice_description` and `reference_audio` cloning. 🟡 🧠 Opus
- [ ] If VoxCPM2's Finnish is not clearly better than Noora: decide whether to keep it or remove it. 🟢 🧠 Opus

### Rallienglanti-mode preset
- [ ] Fun preset that routes English text through the FI T3 finetune with English→Finnish-phonetic text normalizations (`computer` → `kompuutteri`, `th` → `t`/`d`, `w` → `v`, etc.). Low priority — for after English Grandmom is fully validated. 🟡 🧠 Opus.

### Requires a Windows machine
- [ ] Test the .exe against multiple PDF files. 🟡 ⚡ Sonnet
- [ ] Test the installer on a clean Windows environment. 🟡 ⚡ Sonnet

### mikkonumminen.dev — voice-first web identity
- [ ] Record a Chatterbox-clonable voice sample. 🟢 ⚡ Sonnet
- [ ] Wire that cloned voice into the site as an audio-first experience. 🔴 🧠 Opus

### Local disk cleanup (deferred — Mac still in use)
- [ ] Delete `.venv-chatterbox/`, `.venv-qwen/`, HuggingFace model caches (~9.4 GB reclaimable). Do NOT delete while Mac is still used for dev. 🟢 ⚡ Sonnet

### Make a Finnish audiobook of "Testoman ja Sauro"
- [ ] Convert the two Testo Mani / Raudo gym-story texts to a single Finnish audiobook. Sources attached at repo root: `testoman_ja_sauro_part1.txt` (~64 KB, from https://users.aalto.fi/~jlinnosa/massaa/massaa.txt) and `testoman_ja_sauro_part2.txt` (~10 KB, from https://users.aalto.fi/~jlinnosa/massaa/massaa2.txt). Both are UTF-8, Finnish. Plan: (1) concatenate part1 + part2 in order, (2) strip email/forum metadata headers ("Subject:", "Mega Man kirjoitti 3.7 13:39", "ilmianna asiaton viesti", "vastaa", etc.) so only the story prose reads aloud, (3) synth via GUI or `scripts/generate_chatterbox_audiobook.py`. **Voice/engine still TBD — wait for user.** Do NOT start synth without explicit go-ahead. 🟡 ⚡ Sonnet

## Post-Audit Tasks

### TTS Output Quality
- [ ] Create a comparison script that runs the same text through all three engines for manual A/B review
- [ ] Verify silence trimming between chunks — check for gaps or over-trimming

### PDF Parser Stress Testing
- [ ] Collect 10-15 diverse test PDFs (scanned, two-column, academic, Finnish hyphenation, tables)
- [ ] Run parser against all and manually review extracted text

### GUI Threading
- [ ] During a long conversion, test UI responsiveness — document any freezes
- [ ] Identify and fix any blocking operations on the main thread

### Clean Install Testing
- [ ] Test on a fresh Windows VM/sandbox with no dev tools
- [ ] Verify full flow: install → open → load PDF → select engine → convert → save MP3

### Memory Profiling
- [ ] Profile memory during conversion of a 300+ page PDF
- [ ] Check pydub audio chunks are released properly

### Qwen3-TTS — DROPPED
Investigated and ruled out. Finnish not supported, CUDA-only, too slow. No further action.
