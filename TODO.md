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

### mikkonumminen.dev — voice-first web identity
- [ ] Record a Chatterbox-clonable voice sample for mikkonumminen.dev. Same quality bar as the v7 Finnish audiobook run — 12–20 s, well-lit room, no clipping, Input volume ~85%, loudness −25…−15 dBFS, SNR 40+ dB. Produce a clean WAV at 22050 Hz for the site. 🟢 ⚡ Sonnet
- [ ] Wire that cloned voice into the site so the primary experience is audio-first, with visuals as support: every section/page loads with a short TTS narration in the user's voice, transcript visible below, play/pause control. Think podcast landing page, not a portfolio card gallery. 🔴 🧠 Opus

### Next session — three candidate paths (pick one)

The Finnish normalizer is at a natural stopping point: 16 passes shipped, 419 tests green, Tier 1 mechanical layer complete. These are the three realistic ways to push further, in order of "user impact vs effort":

1. **🛑 Stop and wait for audio feedback.** Ship the current normalizer to the GPU test machine, listen to a chapter, collect specific failures. This is the only path that gets us ground-truth audio validation — all other work is speculation until we hear a real run. Opus judgment call when the time comes.
2. **🔴 🧠 Long compound word seam splitter (Pass P).** The biggest remaining unhandled class from `docs/finnish_tts_failure_inventory.md` — 576 unique compounds (≥20 chars) in the test book. Two implementation strategies to decide between before coding: (a) manual seam lexicon of ~300-500 compound boundaries like `oikeus-`, `rangaistus-`, `menettely-`, or (b) libvoikko integration (heavy C-library dependency, installer complexity). Full session of its own.
3. **🟡 🧠 Heuristic acronym fallback for unknown all-caps.** Smaller than (2) but higher false-positive risk. Current Pass N is whitelist-only — adding a heuristic for unknown ALL-CAPS tokens ≥2 letters needs a small-caps-heading guard (`KESKI JA AJALLA` is a heading, not an acronym). Needs careful design.

### Voice cloning — real-world end-to-end validation
- [ ] Test `scripts/record_voice_sample.py` live with a real 12 s recording of the user's own voice — raise the macOS **System Settings → Sound → Input → Input volume** slider to ~85% first (work calls like Zoom/Teams leave it at ~5–10% which fails preflight at ~−47 dBFS loudness despite 40+ dB SNR). Then re-run with `--synthesize "Terve. Tämä on minun ääneni testi. Kohta keitetään kahvit."`, confirm the preflight passes with loudness in −25…−15 dBFS, verify Chatterbox finishes and the playback sounds like v7-quality Finnish in the user's cloned voice. Log the final MP3 path + per-check numbers for future reference. 🟢 ⚡ Sonnet
- [ ] If the first clip passes but cloning quality is below v7, iterate: (a) re-record longer (20 s), (b) re-record with more varied prosody, (c) experiment with an explicit `--ref-audio` path and compare vs auto-detected flow. 🟡 🧠 Opus
- [ ] Document the "input volume gotcha" in the README `record_voice_sample.py` section as a troubleshooting note so future users don't hit the same dead end. 🟢 ⚡ Sonnet

### Finnish normalizer — Tier 1 follow-ups (Passes A/B/J1/J2/J3/K/L/N/M/C/D/E/F/G/I/H all shipped)
- [ ] Pass I + Pass L audio validation on GPU — listen to a test-book chapter with the new passes on vs off on the NVIDIA machine. Confirm that `humanismi`/`konsiliarismissa`/`instituutio`/Latin phrases/Roman numerals actually sound better with the respelling and ordinal expansion. If they sound worse, revisit the respelling format per-category. 🟡 🧠 Opus
- [ ] Pass I lexicon extensions as new failure classes surface in other Finnish books beyond the test corpus — the current 42 `ismi_stems` + 53 `tio_stems` cover the test corpus; other books will need more. 🟡 ⚡ Sonnet
- [ ] Long compound word seam splitter (Pass P?) for `oikeudenkäyntimenettely`-style 20+ char compounds. The inventory flags 576 unique compounds as a MEDIUM-severity failure mode. Tier 2 work — needs either a seam lexicon or libvoikko integration. 🔴 🧠 Opus
- [ ] Heuristic acronym letter-by-letter fallback for unknown all-caps tokens. Current Pass N is whitelist-only for safety. A heuristic fallback needs a small-caps-heading guard to avoid false positives. 🟡 🧠 Opus
- [ ] Governor table expansion for other Finnish books — current table targets legal-history prose. Kitchen-sink additions (`rivi` → `riveiltä`, `kappale` → `kappaleissa`, `luku` → `luvuissa` plural forms) as corpora reveal them. 🟢 ⚡ Sonnet

### Local disk cleanup (deferred — Mac still in use)
- [ ] After the fast-track audiobook run succeeds AND no more local Chatterbox dev is needed, delete `.venv-chatterbox/` (symlink) + `.venv-qwen/` (1.4 GB real venv — originally named `.venv-qwen` from the dead Qwen investigation, later repurposed for Chatterbox), `~/.cache/huggingface/hub/models--ResembleAI--chatterbox` (6.0 GB), `~/.cache/huggingface/hub/models--Finnish-NLP--Chatterbox-Finnish` (2.0 GB). Total ~9.4 GB reclaimable. Do NOT delete while the Mac is still being used for dev. 🟢 ⚡ Sonnet

### Chatterbox-Finnish — upstream contribution (ready to ship)
- [ ] Submit the bug report + patch in `docs/upstream/chatterbox/BUG_REPORT.md` and `docs/upstream/chatterbox/hook_leak_fix.patch` as a GitHub issue + PR to `resemble-ai/chatterbox`. Reproducer is at `docs/upstream/chatterbox/repro_hook_leak.py`. 🟡 🧠 Opus

### Chatterbox-Finnish — tester feedback loop
- [ ] After a tester runs the fast-track bundle on their PC, collect any errors/friction and fix them in `scripts/setup_chatterbox_windows.ps1` or `scripts/run_audiobook.bat` 🟡 ⚡ Sonnet
- [ ] If a tester hits the "open PowerShell" wall: write a `setup.bat` wrapper that launches the .ps1 with `ExecutionPolicy Bypass` already (check whether current `setup_chatterbox_windows.bat` already does this) 🟢 ⚡ Sonnet

### VoxCPM2 — GPU testing (requires NVIDIA machine)
- [ ] Run `pip install voxcpm` on a GPU machine and verify the GUI sees the engine as available 🟢 ⚡ Sonnet
- [ ] Synthesize the first ~5000 characters of the test book with VoxCPM2 in Finnish, compare against v3 (Noora Edge-TTS) 🟡 🧠 Opus
- [ ] Try `voice_description="warm baritone elderly male"` on an English sample and judge whether the output sounds right 🟡 🧠 Opus
- [ ] Try `reference_audio` cloning with a 10 s clip (e.g. a Morgan Freeman-style sample) and evaluate the result 🟡 🧠 Opus
- [ ] If VoxCPM2's Finnish is not clearly better than Noora: decide whether to keep the engine in the codebase or remove it 🟢 🧠 Opus

### Qwen3-TTS — DROPPED
Investigated and ruled out. Qwen3-TTS officially supports only 10 languages (Finnish not
included), is CUDA-only (flash-attn3), hits a hard `Output channels > 65536` convolution
limit on MPS, and CPU inference is slower than realtime even on an RTX 4090. The
`dev_qwen_tts.py` script stays as a feasibility probe for any future developer who wonders
"why not Qwen" but is not a viable engine for this project. No further action planned.

### Requires a Windows machine
- [ ] Add an application icon (assets/icon.ico) 🟢 ⚡ Sonnet
- [ ] Test the .exe against multiple PDF files 🟡 ⚡ Sonnet
- [ ] Test the installer on a clean Windows environment 🟡 ⚡ Sonnet

## Post-Audit Tasks

### TTS Output Quality
- [ ] Create a comparison script that runs the same Finnish + English text samples through all three engines and outputs labeled MP3s for manual A/B review
- [ ] Verify silence trimming between chunks — check for gaps or over-trimming
- [ ] Test sentence splitter output against edge cases before TTS (URLs, decimals, Finnish abbreviations like "esim.", "ns.", "mm.")

### PDF Parser Stress Testing
- [ ] Collect 10-15 diverse test PDFs: scanned, two-column layout, academic with footnotes, e-book with TOC and page numbers, Finnish book with hyphenation, PDFs with tables and inline images
- [ ] Run parser against all test PDFs and manually review extracted text for accuracy
- [ ] Document which PDF types break the current heuristics

### GUI Threading
- [ ] During a long conversion, test UI responsiveness: drag window, change settings, press buttons — document any freezes
- [ ] Identify and fix any blocking operations running on the main thread

### Clean Install Testing
- [ ] Test AudiobookMaker-Setup.exe on a fresh Windows VM/sandbox with no dev tools installed
- [ ] Verify full flow: install → open → load PDF → select engine → convert → save MP3
- [ ] Document any missing bundled dependencies or PATH issues

### Memory Profiling
- [ ] Profile memory usage during conversion of a large PDF (300+ pages) using tracemalloc or memory_profiler
- [ ] Check that pydub audio chunks are released properly and not accumulating in memory
- [ ] Set a baseline for peak memory usage per book size

### Dependency Security
- [ ] Run pip audit or safety check against requirements.txt
- [ ] Pin all dependency versions if not already pinned
- [ ] Update any packages with known vulnerabilities

### Sentence Splitter Edge Case Tests
- [ ] URLs embedded in text
- [ ] Decimal numbers ("3.14 was the result")
- [ ] Finnish abbreviations ("esim.", "ns.", "mm.", "jne.")
- [ ] Periods inside quotation marks
- [ ] Strings with only punctuation or whitespace
- [ ] Mixed Finnish/English paragraphs
- [ ] Initials ("J.R.R. Tolkien wrote...")
