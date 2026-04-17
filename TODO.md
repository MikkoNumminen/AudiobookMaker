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
| Claude 1 | 🔵 working | Tier 1 stress test (500-call Chatterbox long-run) | 2026-04-17 |
| Claude 2 | 🟢 idle | — | — |
| Claude 3 | 🔵 working | Audit quick-wins batch (CI pins, regex hoist, Pillow, MP3 bitrate, time tests) | 2026-04-17 |
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

### Audit quick-wins batch [Claude 3, worktree-audit-quick-wins]
- [ ] Pin CI runner images (`windows-2022`, `ubuntu-24.04`) across all three workflows. 🟢 ⚡ Sonnet.
- [ ] Pin ffmpeg in `build-launcher.yml` with SHA-256 + release tag, mirroring `build-release.yml`. 🟢 ⚡ Sonnet.
- [ ] Pin Pillow version in `requirements.txt` (currently `>=10.0.0`). 🟢 ⚡ Sonnet.
- [ ] Hoist per-call regex compilation in `tts_normalizer_fi.py:929-930`, `tts_normalizer_en.py:168-176`, `fi_loanwords.py:163-167`. 🟢 ⚡ Sonnet.
- [ ] MP3 export bitrate → 128k in `src/tts_audio.py` (and Chatterbox export sites). 🟢 ⚡ Sonnet.
- [ ] English normalizer Pass N (time of day) test file — `tests/test_tts_normalizer_en_time.py`. 🟢 ⚡ Sonnet.

### Verify Chatterbox long-run hardening [Claude 1, main]
- [ ] Commits 74018b0 + bb81f60 added GPU memory hygiene to `_clear_chatterbox_state` and per-chunk observability (`.chunk_stats.jsonl`). Tier 1 validation in progress: `scripts/stress_test_chatterbox_longrun.py` runs 500 `engine.generate()` calls in one process to catch drift fast before committing to a 5h re-synth. If Tier 1 passes, next is Tier 2 (regenerate the tail of the problem Finnish audiobook from ~hour 4 onward using existing `.chunks/` cache). Confirm (a) the swallowing is gone perceptually, and (b) `.chunk_stats.jsonl` shows `hook_count` stuck at ≤3 and `reserved_mb` not monotonically climbing. 🟡 🧠 Opus.

### Suppress HuggingFace unauthenticated-request warning in Chatterbox log
- [ ] When Chatterbox loads models, `huggingface_hub` prints "Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN..." in the log panel. Harmless (models still download fine) but looks alarming to users. Suppress it the same way we suppress other cosmetic upstream warnings — either via `logging.getLogger("huggingface_hub").setLevel(logging.ERROR)` before model load, or by adding it to the existing warning-filter block in `scripts/generate_chatterbox_audiobook.py`. 🟢 ⚡ Sonnet.

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

## Audit 2026-04-17 follow-ups

Findings from the full codebase audit (`docs/AUDIT_REPORT.md`). Ordered by priority.
The P0 streaming-assembly fix is claimed separately above; everything below is queued.

### Auto-update: pin setup.iss version to APP_VERSION
- [ ] `installer/setup.iss:35,70,225` hardcodes `1.0.0` while `src/auto_updater.py:27` is `3.7.1`. CI rewrites at build time (`build-release.yml:81`) so release artifacts are correct today, but any off-CI build ships a mis-branded installer → corrupts the upgrade graph. Auto-update is existential. Fix: convert to `#define MyAppVersion` like `launcher.iss`, and add a CI assertion step that fails the build if `auto_updater.APP_VERSION != inno_version`. 🟡 🧠 Opus.

### Engine registry consolidation
- [ ] Adding a new TTS engine currently needs import edits in `gui.py:32-34`, `gui_unified.py:56-65`, `launcher.py:62-68`, plus `_GPU_ENGINES` in `duration_estimate.py`, plus hardcoded `"chatterbox_fi"` checks scattered ~10× in `gui_unified.py`. Central `src/engine_registry.py` imports every engine module in one place; engine metadata (display_name, is_gpu, uses_subprocess, requires_bridge_runner) moves onto `TTSEngine` class variables so the GUI stops branching on engine id. 🔴 🧠 Opus.

### Synthesis orchestrator — extract business logic from UnifiedApp
- [ ] `src/gui_unified.py` is 3,482 lines with ~95 private methods on one class. `_on_convert_click` (104 lines), `_on_listen_click` (164 lines), `_build_engine_bar` (143 lines) all belong elsewhere. Introduce `src/synthesis_orchestrator.py` that owns book loading, engine dispatch, output paths, progress relay; GUI becomes a thin adapter subscribing to orchestrator events. UI builders (`_build_engine_bar`, `_build_header_bar`, `_build_action_row`, `_build_settings_frame`) extract to helper modules. 🔴 🧠 Opus.

### Finnish normalizer: per-pass unit tests for B, D, E, F, J, K, L, M, N
- [ ] Only passes A (citations), C (centuries), G (governors), H (morpheme split), and I (loanwords, via `test_fi_loanwords.py`) have standalone test classes. The other ten passes are covered only via end-to-end integration — a regression inside any of them won't pinpoint which pass broke. Mirror the English `TestPass<LETTER>` pattern with ≥10 cases per pass (empty, single char, whitespace, cross-language). 🟡 🧠 Opus.

### Normalizer: document pass-ordering invariants
- [ ] `normalize_finnish_text` has an implicit M → D → F → G dependency: if someone reorders "unit expansion" to run after the governor pass, the governor lookup (`5 prosenttia` → case inflection) silently breaks. Same pattern in English (O before F, P before G, I/H before G). Add a "Pass ordering invariants" section to each main normalizer's docstring listing the dependencies + why. 🟢 ⚡ Sonnet.

### End-to-end synthesis test with a real engine (Piper)
- [ ] `test_integration.py:126-174` uses `_StubEngine` and is gated on ffmpeg availability. No coverage verifying that Edge/Piper/VoxCPM actually produce a playable MP3. Add an offline, no-GPU E2E test using Piper (bundled, deterministic): 2-sentence PDF → MP3, assert duration > 0 + MP3 header + silence distribution within tolerance. Mark `@pytest.mark.slow`. 🟡 🧠 Opus.

### Test infrastructure: block network calls by default
- [ ] `tests/conftest.py` doesn't globally block network access; each file patches individually. Add autouse fixture that patches `urllib.request.urlopen` + `socket.socket` to raise unless `@pytest.mark.network` is set. Mark existing tests that genuinely need patched network with `network`. Also add `@pytest.mark.gpu` for future CUDA tests. 🟢 ⚡ Sonnet.

### Hardcoded drive letters (C:, D:) in install path candidates
- [ ] `cleanup.py:74-76`, `launcher_bridge.py:505-506`, `engine_installer.py:63` only consider C: and D:. Users with system drive E: or F: are invisible. Enumerate drives dynamically (`string.ascii_uppercase` + `os.path.exists`). Also remove the dev-path leak `D:/koodaamista/AudiobookMakerApp` from `cleanup.py:76`. 🟢 ⚡ Sonnet.

### Auto-updater: harden BAT script path handling
- [ ] `src/auto_updater.py:509-530` builds the relaunch .bat via f-string substitution of `installer_path`. If paths ever contain `"`, `%`, `^`, `&` the batch is malformed. Pass paths to the subprocess via environment variables instead, or assert the path is free of batch-metacharacters before writing. Low risk today (paths come from `Path.home()`), but the substitution pattern is a footgun. 🟢 ⚡ Sonnet.

### Chatterbox: expose --chunk-chars in GUI
- [ ] `scripts/generate_chatterbox_audiobook.py:244` accepts `--chunk-chars` (default 300) but the GUI hardcodes the CLI invocation in `src/gui_synth_mixin.py` without exposing it. Add a settings-panel control; plumb through the subprocess args. 🟢 ⚡ Sonnet.

### Docs: english_normalizer_plan.md §3 missing Pass R (URLs/emails)
- [ ] Plan table stops at Phase 1 A-K. Code implements R/L/M/N/O/P/S. Update §3 to list all 17 English passes in execution order with source links. 🟢 ⚡ Sonnet.

### Docs: README language-support matrix per engine
- [ ] `README.md:15-16` claims "English, German, Swedish, French, and Spanish are also supported". Reality: Edge-TTS many; Piper a subset; Chatterbox Finnish-only (+ Route-B English). Clarify as a per-engine matrix. 🟢 ⚡ Sonnet.

### Normalizer: extract lookup tables to YAML
- [ ] Abbreviations, acronyms, units, governor tables, month names, acronym whitelist are all hardcoded Python. `fi_loanwords.py` already shows the good pattern: YAML-driven with safe_load. Extract analogously; enables user customization and non-developer updates. 🟡 ⚡ Sonnet.

### Docstring + type-hint coverage bump
- [ ] `gui.py` (~32% docstrings / ~65% type hints), `voice_recorder.py` (~33% / ~60%), `launcher.py` (~34% / ~70%). Critical synthesis methods (`_start_synthesis`, `_start_chatterbox_subprocess`) lack docstrings. Priority: `launcher.py` first. 🟢 ⚡ Sonnet.

### Test quality: autospec on mocks to catch signature drift
- [ ] `tests/test_tts_engine.py:109-123` uses loose `patch(...)` without `autospec=True`. If real signatures change, mocks silently still pass while production breaks. Audit and add `autospec=True` where appropriate. 🟢 ⚡ Sonnet.

### Minor cleanups
- [ ] `src/auto_updater.py:128` — hoist `import re` to module top. 🟢 ⚡ Sonnet.
- [ ] `src/gui_unified.py:23,28` — drop unused `shutil` + redundant top-level `webbrowser` (re-imported in `_open_browser`). 🟢 ⚡ Sonnet.
- [ ] `src/cleanup.py:104`, `src/system_checks.py:125` — extract `BYTES_PER_MB = 1024 * 1024` constant. 🟢 ⚡ Sonnet.
- [ ] Broad `except Exception: pass` UI paths (`gui_unified.py:1608,1964,1994,2093`) should log at DEBUG so diagnostics survive. 🟢 ⚡ Sonnet.

### TODO.md sweep for completed items
- [ ] Items like "Add an application icon (assets/icon.ico)" under "Requires a Windows machine" appear to be already done (`assets/` has the icon). Audit and remove stale entries. 🟢 ⚡ Sonnet.

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
