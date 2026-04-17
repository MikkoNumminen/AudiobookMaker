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
| Claude 1 | 🔵 working | Tier 2 tail re-synth of Turo's audiobook (ch 5-8, ~4h GPU, delivers TURO_tail_fixed.mp3) | 2026-04-17 |
| Claude 2 | 🔵 working | Tier 1 picks: pronunciation corpus + Report-a-bug button (parallel worktrees) | 2026-04-18 |
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

### Verify Chatterbox long-run hardening [Claude 1, main]
- [ ] **Tier 1 PASSED** on 2026-04-17 — 500 `engine.generate()` calls in one process. `hook_count` stayed at 0 after call #1 (was 30 residual from load), `allocated_mb` drifted only +2.6 MiB end-to-end, `reserved_mb` +45 MiB (noise). Memory hygiene fix confirmed. Summary at `dist/stress_test/20260417_030630/summary.txt`.
- [ ] **Tier 2 first pass DONE 2026-04-17** — tail re-synth of Turo's audiobook (chapters 5-8 = ~4h-onward stretch) completed 979/979 chunks with `FI_TEMPERATURE=0.5` + normalizer Pass H + `_clear_chatterbox_state` memory hygiene. Memory trend clean (`hook_count=0`, `allocated_mb` flat). **BUT** Turo reported the swallowed-sentence symptom is still present (e.g. `asianosaisaloitteinen menettely` → `…menet`). Telemetry confirmed 261 of 979 chunks (27%) had `s_per_char < 0.040` — far below the ~0.06 s/char Finnish baseline = T3 sampler early-stop. Root-caused to three bugs in upstream `AlignmentStreamAnalyzer` (see chatterbox commit `fix(t3): tighten EOS suppression and token-repetition heuristic`). Also added audiobook-side retry guard in `scripts/generate_chatterbox_audiobook.py` (commit `cb7e13a`).
- [ ] **Tier 2 second pass in progress** — 261 bad WAVs deleted, re-running generator with `--resume` + fixed analyzer + retry-on-short-audio guard. Same output dir `dist/audiobook_tail_fix/test_book/`. ETA ~30-60 min GPU. On completion, rename `00_full.mp3` → `TURO_tail_fixed.mp3` and verify via stats scan that <1% of chunks fall below 0.040 s/char. 🔴 🧠 Opus.

### Chatterbox: 1-in-500 stochastic early-stop glitch
- [ ] **Root cause identified and fixed 2026-04-17** — three bugs in `AlignmentStreamAnalyzer`: (a) `complete` flag fired at `text_position >= S - 3` instead of `S - 1`, letting `long_tail`/`alignment_repetition` heuristics force EOS mid-sentence; (b) EOS suppression stopped at the same `S - 3` line, so noisy attention argmax could briefly cross into that zone and let T3 naturally sample EOS; (c) `token_repetition` checked only 2 identical adjacent tokens despite comment claiming "3x" — 2x is extremely common in normal speech and fired constantly. Patched in chatterbox fork + added audio-ratio retry guard in audiobook generator (`MIN_AUDIO_S_PER_CHAR = 0.040`, up to 2 retries). Validated on Turo's tail re-synth (Tier 2 second pass). Keep item open until second pass confirms <1% of chunks below threshold. 🟡 🧠 Opus.

### Chatterbox-Finnish: collect pronunciation failure corpus [Claude 2, worktree-corpus]
- [ ] User reported 4 mispronunciations in the `turo_stressitesti_tulokset_fi` sample: `löysimme` → `löys imme` (mid-word pause), `lopetti` → `loopetti` (vowel-length hallucination), `ennen vain` → `ennenvän` (word-boundary collapse), `äänikirja` → `aanikirja` (ää → aa substitution). Lowering `FI_TEMPERATURE` to 0.5 cleared the length/boundary symptoms in a fresh A/B sweep, but the `ää → aa` umlaut drop and the mid-word pause pattern are likely in-weights. Keep collecting: each new failing word adds a data point for Pass I lexicon respelling (try `ää` → `ä ä` or a hyphenated form) and for the known `s → sch` bucket. Target: 20 concrete words across ≥3 failure categories before attempting a targeted fix. 🟡 🧠 Opus.

### "Report a bug" button in the GUI [Claude 2, worktree-report-bug]
- [ ] Add a "Report a bug" / "Ilmoita virheestä" link or button (Settings panel or Help menu) that opens the GitHub Issues page (`https://github.com/MikkoNumminen/AudiobookMaker/issues/new`) in the browser. Optionally pre-fill the issue body with the app version, OS version, installed engines, and the last ~20 log lines. 🟢 ⚡ Sonnet.

### Inline audio player in the GUI
- [ ] Replace the external-player shell-out with a minimal in-GUI play/stop widget. Scope: play/stop only, no seek bar, no volume slider. Library choice: `pygame.mixer` (~5 MB) or `miniaudio` (lighter). Must stop on window close and stop the previous clip before starting a new one. ~1 h for samples-only, ~2 h if it also plays the final book MP3. 🟡 ⚡ Sonnet.

### Mixin dedup: merge diverged SynthMixin / UnifiedApp overrides
- [ ] `SynthMixin._pump_events`, `_set_running_state`, `_set_idle_state` diverged from the `UnifiedApp` overrides. Merge into one canonical implementation. Code health, not urgent. 🟡 ⚡ Sonnet.

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
- [ ] Heuristic acronym letter-by-letter fallback for unknown all-caps tokens. Current Pass N is whitelist-only. 🟡 🧠 Opus
- [ ] Governor table expansion for other Finnish books. 🟢 ⚡ Sonnet

### Voice cloning — real-world end-to-end validation
- [ ] Test `scripts/record_voice_sample.py` live with a real 12 s recording. Raise input volume to ~85% first (Zoom/Teams leaves it at ~5–10%). 🟢 ⚡ Sonnet
- [ ] If cloning quality is below v7, iterate: longer recording, more varied prosody, explicit `--ref-audio`. 🟡 🧠 Opus
- [ ] Document the "input volume gotcha" in README. 🟢 ⚡ Sonnet

### Voice pack pipeline — remaining slices (Slices 1–5 scaffolding landed)
- [ ] **GPU training loop (Slice 3 inner):** fill in the `NotImplementedError` seam in `scripts/voice_pack_train.py::_run_training` with the actual Chatterbox LoRA fine-tune loop. Borrow from the existing Finnish finetune. Low LR + early stopping to preserve accent/dialect. Needs a GPU host to validate. 🔴 🧠 Opus.
- [ ] **Expression markup wire-up (Slice 5 inference-path integration):** consume the `ExpressionPlan` produced by `src.voice_pack.expression.parse_markup` inside `scripts/generate_chatterbox_audiobook.py` so per-sentence `exaggeration` / `cfg_weight` overrides take effect during synthesis. Optional lightweight emotion-prefix token during training. 🟡 🧠 Opus.
- [ ] **XTTS v2 bake-off (Slice 5a, research lane):** run the same source audio through Coqui XTTS v2 finetune, listen side-by-side vs Chatterbox LoRA. If XTTS clearly wins on emotional range / accent, ship as a second engine slot (private-use builds only — XTTS is CPML non-commercial). 🟡 🧠 Opus.
- [ ] **Architecture write-up (`docs/voice_pack_design.md`):** capture the design rationale — Chatterbox LoRA primary (MIT, shared inference path), <200 MB/speaker adapters, emotional range via training-data balance + inference knobs, ~5 h source is the quality ceiling. Internal dev doc; may reference the audiobook source use case. 🟢 ⚡ Sonnet.
- [ ] **License/ethics guardrail note:** voice packs stay local by default (no cloud upload, no sharing button). Capability-framed README note ("voice cloning of third-party voices is your own responsibility, keep local, don't redistribute"). 🟢 ⚡ Sonnet.

### Chatterbox-Finnish — upstream contribution
- [ ] Submit bug report + patch (`docs/upstream/chatterbox/BUG_REPORT.md` + `hook_leak_fix.patch`) as a GitHub issue + PR to `resemble-ai/chatterbox`. 🟡 🧠 Opus

### Chatterbox-Finnish — tester feedback loop
- [ ] Collect errors/friction from testers running the fast-track bundle. 🟡 ⚡ Sonnet
- [ ] If a tester hits the "open PowerShell" wall: write a `setup.bat` wrapper. 🟢 ⚡ Sonnet

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

## Post-Audit Tasks

### TTS Output Quality
- [ ] Create a comparison script that runs the same text through all three engines for manual A/B review
- [ ] Verify silence trimming between chunks — check for gaps or over-trimming
- [ ] Test sentence splitter against edge cases (URLs, decimals, Finnish abbreviations)

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

### Dependency Security
- [ ] Run pip audit against requirements.txt
- [ ] Update any packages with known vulnerabilities

### Qwen3-TTS — DROPPED
Investigated and ruled out. Finnish not supported, CUDA-only, too slow. No further action.
