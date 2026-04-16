# TODO

Shared task list across all Claude Code sessions. Remove completed tasks immediately — no "Recently Completed" section.
Every item must have a size estimate: 🟢 small, 🟡 medium, 🔴 large. LLM marker: ⚡ Sonnet, 🧠 Opus.
In Progress items must show the owner: `[Claude 1, main]`, `[Claude 2, worktree-name]`, etc.
4 permanent Claude instances: **Claude 1, Claude 2, Claude 3, Claude 4.**

**🚨 MANDATORY RULES — NO EXCEPTIONS:**

1. **Re-read this file BEFORE starting ANY work** — every single session, every single task.
2. **NEVER start a task without FIRST moving it to "In Progress" with your name tag.** If you skip this, you are causing collisions.
3. **If you pause or stop mid-task, your entry MUST stay in "In Progress" until the work is committed and pushed.** Do not remove it just because you stopped — other Claudes need to see it.
4. **If a task already has an owner tag, do NOT touch it.** Pick something else or wait.
5. **Violating these rules breaks the shared workflow for all instances.**

## In Progress

## Backlog

### Listen to demo clips and catalogue audible defects
- [ ] Play `assets/demos/finnish_grandmom_kivi.mp3` and `assets/demos/english_grandmom_gibbon.mp3`. Write down every word that sounds off — mispronunciations, wrong stress, swallowed words, weird prosody, garbled numbers. Group by root cause: normalizer issue vs model issue vs reference-clip issue vs chunking-boundary issue. Output: a ranked list that drives the next normalizer/audio sprint. **This is the top priority — all quality work is speculation until we hear a real run.** 🟡 🧠 Opus.

### "Report a bug" button in the GUI
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
- [ ] Add an application icon (assets/icon.ico). 🟢 ⚡ Sonnet
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

### Sentence Splitter Edge Cases
- [ ] URLs, decimals, Finnish abbreviations, periods inside quotes, whitespace-only strings, mixed Finnish/English, initials ("J.R.R. Tolkien")

### Qwen3-TTS — DROPPED
Investigated and ruled out. Finnish not supported, CUDA-only, too slow. No further action.
