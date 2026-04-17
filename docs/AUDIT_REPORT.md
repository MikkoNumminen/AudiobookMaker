# AudiobookMaker — Full Codebase Audit

Comprehensive read-only audit of the entire AudiobookMaker codebase. No code
was modified; this report is findings-only.

Scope: every file under `src/`, `tests/`, `scripts/`, `.github/workflows/`,
`installer/`, and root-level config files (`requirements.txt`,
`audiobookmaker.spec`, `audiobookmaker_launcher.spec`, `pytest.ini`,
`README.md`, `BUILDING.md`, `DEVELOPMENT.md`, `CLAUDE.md`, `TODO.md`,
`docs/*.md`).

Severity legend: 🔴 critical · 🟡 warning · 🟢 suggestion.

---

## 1. Architecture & Modularity

**Summary: 2 critical, 5 warnings, 1 suggestion.**

### 🔴 UnifiedApp is a god-object
- **File/lines:** [src/gui_unified.py:445-2916](../src/gui_unified.py#L445-L2916)
- **Issue:** 2,471-line class with ~95 private methods. Multiple single methods exceed 80 lines: `_build_engine_bar` [src/gui_unified.py:798-941](../src/gui_unified.py#L798-L941) (143 lines), `_build_header_bar` [src/gui_unified.py:941-1035](../src/gui_unified.py#L941-L1035) (94), `_build_action_row` [src/gui_unified.py:1045-1144](../src/gui_unified.py#L1045-L1144) (99), `_build_settings_frame` [src/gui_unified.py:1412-1547](../src/gui_unified.py#L1412-L1547) (135), `_on_convert_click` [src/gui_unified.py:2766-2870](../src/gui_unified.py#L2766-L2870) (104), `_pump_events` [src/gui_unified.py:3008-3092](../src/gui_unified.py#L3008-L3092) (84).
- **Fix:** Extract UI builders to helper modules (`gui_widgets_engine_bar.py`, `gui_widgets_header.py`, etc.). Move orchestration into a new `synthesis_orchestrator.py` (see next finding). Aim for a shell class with a handful of `_build_*` helpers that delegate.

### 🔴 Business logic living inside the GUI module
- **File/lines:** [src/gui_unified.py:403-437](../src/gui_unified.py#L403-L437) (parse_book dispatch), [src/gui_unified.py:2402-2566](../src/gui_unified.py#L2402-L2566) (`_on_listen_click`, 164 lines of parsing + subprocess management), [src/gui_unified.py:2766-2870](../src/gui_unified.py#L2766-L2870) (`_on_convert_click`)
- **Issue:** PDF/EPUB parsing dispatch, output-path derivation, subprocess orchestration, and progress-event routing all live in the GUI class. TTS engines correctly do not know about the GUI (good), but the GUI knows too much about synthesis plumbing.
- **Fix:** Introduce `src/synthesis_orchestrator.py` that owns: book loading, engine dispatch, output-path management, progress relaying. The GUI becomes a thin adapter that subscribes to orchestrator events.

### 🟡 Hidden registry coupling — adding an engine requires edits in 3+ files
- **Files/lines:**
  - [src/gui.py:32-34](../src/gui.py#L32-L34)
  - [src/gui_unified.py:56-65](../src/gui_unified.py#L56-L65)
  - [src/launcher.py:62-68](../src/launcher.py#L62-L68)
  - [src/engine_installer.py](../src/engine_installer.py) — hardcodes `"chatterbox_fi"` engine id
  - [src/duration_estimate.py](../src/duration_estimate.py) — `_GPU_ENGINES = {"chatterbox_fi", "chatterbox", "voxcpm"}`
- **Issue:** `@register_engine` is clean, but three entrypoints must import the engine module for its registration side-effect, and metadata like GPU-ness / install hints / subprocess-vs-in-process is scattered.
- **Fix:** Create `src/engine_registry.py` that imports every engine module in one place. Promote metadata (display_name, is_gpu, uses_subprocess) into `TTSEngine` class variables so the GUI no longer needs `== "chatterbox_fi"` branches.

### 🟡 Hardcoded engine-id checks in UI layer
- **File/lines:** [src/gui_unified.py:1657-1658](../src/gui_unified.py#L1657) and ~10 other `== "chatterbox_fi"` sites; [src/launcher.py:286-289](../src/launcher.py#L286-L289) branches on label prefix `"Chatterbox"`.
- **Issue:** UI hardcodes engine IDs instead of consulting engine metadata.
- **Fix:** Expose `engine.uses_subprocess` / `engine.requires_bridge_runner` in `tts_base.TTSEngine` and branch on that.

### 🟡 `tts_engine.py` mixes generic utilities with Edge-TTS-specific code
- **File/lines:** [src/tts_engine.py:168-198](../src/tts_engine.py#L168-L198) (`TTSConfig` only meaningful for Edge), [src/tts_engine.py:215-256](../src/tts_engine.py#L215-L256) (async helpers + Windows socketpair workaround), [src/tts_engine.py:315-380](../src/tts_engine.py#L315-L380) (`text_to_speech` — Edge pipeline)
- **Issue:** Piper and VoxCPM manually re-implement chunking + combine inline ([src/tts_piper.py:540-559](../src/tts_piper.py#L540-L559), [src/tts_voxcpm.py:213-242](../src/tts_voxcpm.py#L213-L242)) because the "generic" orchestrator is Edge-shaped. The module straddles two roles.
- **Fix:** Split into `src/tts_shared.py` (pure re-export hub of `split_text_into_chunks`, `combine_audio_files`, normalizers) and `src/tts_edge_orchestrator.py` (Edge async machinery, `TTSConfig`, `text_to_speech`). Consider an engine-agnostic `synthesize_with_chunking(engine, text, out, cb)` helper so all three engines share the chunk/combine path.

### 🟡 VoxCPM2 model caching is undocumented
- **File/lines:** [src/tts_voxcpm.py:88-91](../src/tts_voxcpm.py#L88-L91)
- **Issue:** Engine keeps `self._model` alive across calls while Edge/Piper are stateless. Not documented in `TTSEngine` contract or the module docstring.
- **Fix:** Document the caching lifetime in the class docstring and/or push lifecycle hooks (`load()` / `unload()`) into the `TTSEngine` base.

### 🟡 Mixin order + implicit dependency in UnifiedApp
- **File/lines:** [src/gui_unified.py:445](../src/gui_unified.py#L445)
- **Issue:** `UnifiedApp(SynthMixin, UpdateMixin, ctk.CTk)` — the mixins read attributes (`self._log_text`, `self._chatterbox_runner`) that only exist after `_build_ui`. This works but is fragile.
- **Fix:** Define the attributes with `Optional` defaults on the class body, or convert mixins to composition.

### 🟢 Data flow is clean in one direction
- **Files:** [src/pdf_parser.py](../src/pdf_parser.py), [src/epub_parser.py](../src/epub_parser.py), [src/tts_normalizer*.py](../src/tts_normalizer.py), [src/tts_chunking.py](../src/tts_chunking.py), [src/tts_audio.py](../src/tts_audio.py)
- **Observation:** No circular imports. PDF/EPUB → normalized text → chunks → audio flows in one direction. TTS engines do not import anything from `src/gui*.py`. Keep this invariant.

---

## 2. Code Quality

**Summary: 0 critical, 7 warnings, 4 suggestions.**

### 🟡 Docstring coverage drops below 35% in three legacy modules
- **Files/lines:**
  - [src/gui.py:81-731](../src/gui.py#L81-L731) — ~32% (10 docstrings / 31 methods). Missing on `_build_ui`, `_apply_loaded_config`, `_on_pdf_selected`, `_on_convert_complete`.
  - [src/voice_recorder.py:312-680](../src/voice_recorder.py#L312-L680) — ~33%. Missing on `_build_dialog`, `_populate_devices`, `_toggle_record`.
  - [src/launcher.py:129-680](../src/launcher.py#L129-L680) — ~34%. Missing on `_start_synthesis`, `_start_chatterbox_subprocess`, `_start_inprocess_engine`.
- **Fix:** Add one-line docstrings to each public / critical private method. Priority: `launcher.py` (critical synthesis methods).

### 🟡 Type-hint coverage gaps in the same modules
- **Files/lines:** [src/gui.py](../src/gui.py) ~65%, [src/voice_recorder.py](../src/voice_recorder.py) ~60%, [src/launcher.py](../src/launcher.py) ~70%.
- **Fix:** Add return-type annotations; mypy-style coverage reporting in CI would prevent regression.

### 🟡 String-table i18n pattern duplicated across four files
- **Files/lines:**
  - [src/launcher.py:70-120](../src/launcher.py#L70-L120) `_STRINGS`
  - [src/voice_recorder.py:48-99](../src/voice_recorder.py#L48-L99) `_STRINGS`
  - [src/gui_engine_dialog.py:29-82](../src/gui_engine_dialog.py#L29-L82) `_ENGINE_MGR_STRINGS`
  - [src/gui_unified.py](../src/gui_unified.py) — inline `_s()` method with its own table
- **Issue:** Identical structure (`{"fi": {...}, "en": {...}}`) + lookup function repeated.
- **Fix:** Promote to a shared `src/i18n.py` with `t(key, lang)`; each module registers its own table.

### 🟡 Overly broad `except Exception: pass` without logging
- **Files/lines:** [src/gui_unified.py:1608](../src/gui_unified.py#L1608), 1964, 1994, 2093; [src/engine_installer.py:231, 428](../src/engine_installer.py#L231); [src/gui.py](../src/gui.py) / [src/launcher.py](../src/launcher.py) UI failure paths.
- **Issue:** Most are intentional (UI responsiveness), but the silent swallow makes diagnostics hard. The comment-documented ones (e.g. [src/gui_unified.py:51,64,105](../src/gui_unified.py#L51)) are fine.
- **Fix:** Log at `logging.DEBUG` level with the exception so postmortems have a trail.

### 🟡 `import re` inside function in `auto_updater.py`
- **File/lines:** [src/auto_updater.py:128](../src/auto_updater.py#L128)
- **Issue:** `_fetch_sidecar_sha256()` does `import re` inside the function body. Everywhere else imports are top-level.
- **Fix:** Hoist to module top.

### 🟡 Redundant top-level imports in `gui_unified.py`
- **File/lines:** [src/gui_unified.py:23](../src/gui_unified.py#L23) (`shutil` imported but unused), [src/gui_unified.py:28](../src/gui_unified.py#L28) (`webbrowser` imported at module level then re-imported inside `_open_browser`).
- **Fix:** Drop the unused import; remove the duplicate.

### 🟡 Lines > 120 chars in `gui_unified.py`
- **File/lines:** [src/gui_unified.py:269,271,286,287,368,370,385,386](../src/gui_unified.py#L269) (max 163 chars).
- **Fix:** Wrap long string literals in tuples for implicit concatenation.

### 🟢 Magic `1024*1024` byte-to-MB conversions
- **File/lines:** [src/cleanup.py:104](../src/cleanup.py#L104), [src/system_checks.py:125](../src/system_checks.py#L125).
- **Fix:** Extract a module-level `BYTES_PER_MB = 1024 * 1024`.

### 🟢 Private-class `_PascalCase` names
- **Files/lines:** [src/launcher_bridge.py:248](../src/launcher_bridge.py#L248) `_RunnerState`, [src/tts_piper.py:41](../src/tts_piper.py#L41) `_PiperVoiceSpec`, [src/voice_recorder.py:185](../src/voice_recorder.py#L185) `_CheckResult`.
- **Observation:** PEP 8 allows this; acceptable for private helpers.

### 🟢 Side-effect engine imports flagged `noqa: F401` are intentional
- **Files/lines:** [src/gui_unified.py:57-58](../src/gui_unified.py#L57), [src/launcher.py:62-63](../src/launcher.py#L62).
- **Observation:** Registration side-effect only; not dead code. Noted for future auditors.

### 🟢 Variable naming is generally clear
- **Observation:** No egregious `x`/`tmp`/`data` in non-trivial contexts. Short names (`m`, `f`, `e`) follow Python idioms. `_fi_detect_case()` in [src/tts_normalizer_fi.py:707-749](../src/tts_normalizer_fi.py#L707-L749) is documented where brevity might otherwise confuse.

---

## 3. Finnish & English Text Normalizer

**Summary: 0 critical, 5 warnings, 3 suggestions.**

### 🟡 Finnish passes B, D, E, F, J1/J2/J3, K, L, M, N have no standalone unit tests
- **Files/lines:** [src/tts_normalizer_fi.py:823-926](../src/tts_normalizer_fi.py#L823-L926) (pipeline) vs. [tests/test_tts_normalizer_fi.py](../tests/test_tts_normalizer_fi.py).
- **Issue:** Only Pass A (citations), C (centuries), G (governors), H (morpheme split), and I (loanwords via `test_fi_loanwords.py`) have dedicated test classes. The other ten passes are covered *only* through end-to-end integration checks — any regression inside them won't pinpoint the broken pass.
- **Fix:** Mirror the English pattern: one `TestPass<LETTER>` class per pass with ≥10 cases per edge (empty, single char, whitespace, cross-language text).

### 🟡 English Pass N (time of day) has no test file
- **File/lines:** [src/tts_normalizer_en.py:648-677](../src/tts_normalizer_en.py#L648-L677). No `tests/test_tts_normalizer_en_time.py`.
- **Fix:** Add test file covering 12h vs 24h, minute teens (oh-one..oh-nine), noon/midnight, minutes 0–59.

### 🟡 Implicit pass-ordering dependencies not documented in docstrings
- **Files/lines:** [src/tts_normalizer_fi.py:852-890](../src/tts_normalizer_fi.py#L852-L890) (M→D→F→G chain), [src/tts_normalizer_en.py:838-849](../src/tts_normalizer_en.py#L838-L849) (O before F; P/I/H before G).
- **Issue:** The governor-aware case inflection silently breaks if Pass M and Pass G are reordered — unit expansion must survive through to Pass G so the governor lookup (`5 prosenttia` → case) fires. There's no docstring or comment warning against reordering.
- **Fix:** Add a "Pass ordering invariants" section to the main `normalize_finnish_text` / `normalize_english_text` docstrings listing the dependencies and why.

### 🟡 Per-call regex compilation in English abbreviation loop and Finnish whitespace tail
- **Files/lines:**
  - [src/tts_normalizer_en.py:168-176](../src/tts_normalizer_en.py#L168-L176) — ~15 `re.escape(abbr)` → `re.sub(pattern, ...)` per call.
  - [src/tts_normalizer_fi.py:929-930](../src/tts_normalizer_fi.py#L929-L930) — `re.sub(r"[ \t]+", " ", text)` and `re.sub(r" +([.,;:!?])", r"\1", text)` recompiled every call.
  - [src/fi_loanwords.py:163-167](../src/fi_loanwords.py#L163-L167) — one regex per Latin phrase (~10–20) per normalization call.
- **Fix:** Pre-compile module-level: `_FI_WHITESPACE_RE = re.compile(...)`, etc. Pre-build per-abbreviation regexes at import time.

### 🟡 `docs/plans/english_normalizer_plan.md` §3 does not list Pass R (URLs/emails)
- **Files/lines:** [docs/plans/english_normalizer_plan.md](plans/english_normalizer_plan.md) vs. [src/tts_normalizer_en.py:820-824](../src/tts_normalizer_en.py#L820-L824).
- **Issue:** Plan table stops at Phase 1 A–K. Code implements R/L/M/N/O/P/S and relies on specific placement (R before C). Readers using the plan as a reference will miss the full pipeline.
- **Fix:** Update §3 to list all 17 English passes in execution order and link each to its source location.

### 🟢 Lookup tables hardcoded in source
- **Files/lines:**
  - [src/tts_normalizer_fi.py:77-110](../src/tts_normalizer_fi.py#L77) `_FI_ABBREV_MAP` (34 entries)
  - [src/tts_normalizer_fi.py:157-184](../src/tts_normalizer_fi.py#L157) `_FI_ACRONYM_LOOKUP` (24)
  - [src/tts_normalizer_fi.py:221-250](../src/tts_normalizer_fi.py#L221) `_FI_UNIT_MAP` (19)
  - [src/tts_normalizer_fi.py:349-455](../src/tts_normalizer_fi.py#L349) governor tables (100+)
  - [src/tts_normalizer_en.py:117-155](../src/tts_normalizer_en.py#L117) abbreviations (22)
  - [src/tts_normalizer_en.py:530-572](../src/tts_normalizer_en.py#L530) units (30+)
  - [src/_en_pass_o_dates.py:28-53](../src/_en_pass_o_dates.py#L28) months
  - [src/_en_pass_s_acronyms.py:31-45](../src/_en_pass_s_acronyms.py#L31) acronym whitelist
- **Fix:** Extract to YAML like `data/fi_loanwords.yaml` already does in `fi_loanwords.py`. Enables user customization, simplifies localization, and allows non-developer updates.

### 🟢 No `None` / single-character defensive tests
- **Observation:** API contract is `str → str`. Tests cover empty string, whitespace-only, and cross-language inputs, but not `None` or one-character pathological cases. Acceptable per contract; flag for awareness.

### 🟢 Dispatcher cross-language tests already cover mixed input
- **File/lines:** [tests/test_tts_normalizer_dispatcher.py:102-140](../tests/test_tts_normalizer_dispatcher.py#L102-L140).
- **Observation:** Good coverage — no action needed here.

---

## 4. Security

**Summary: 1 critical, 4 warnings, rest clean.**

### 🔴 `build-launcher.yml` downloads ffmpeg without SHA-256 verification
- **File/lines:** [.github/workflows/build-launcher.yml:43-48](../.github/workflows/build-launcher.yml#L43-L48)
- **Issue:**
  ```yaml
  curl -L -o ffmpeg.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip
  7z x ffmpeg.zip -offmpeg_tmp
  ```
  Unlike [build-release.yml:33-46](../.github/workflows/build-release.yml#L33-L46), the launcher build does not pin the release or check a SHA-256. A compromised BtbN release or MITM on the unpinned `latest` redirect would ship a trojaned ffmpeg inside the launcher installer.
- **Fix:** Pin `FFMPEG_RELEASE` to the same autobuild date used by the release workflow, hardcode the SHA-256, and run `sha256sum -c -` before extraction.

### 🟡 `Pillow>=10.0.0` is not pinned
- **File/lines:** [requirements.txt:26](../requirements.txt#L26)
- **Issue:** Every other package is `==` pinned; Pillow uses `>=`. pip will silently upgrade to any newer major release, bypassing the "tested versions only" invariant.
- **Fix:** Pin to a specific tested version (e.g. `Pillow==10.1.0`).

### 🟡 Auto-update BAT script uses f-string substitution of paths
- **File/lines:** [src/auto_updater.py:509-530](../src/auto_updater.py#L509-L530)
- **Issue:** `f'set "INSTALLER={installer_path}"'` — if `installer_path` ever contains a `"` or `%`, the batch file is malformed. Currently paths come from controlled sources (`Path.home() / ".audiobookmaker"`), so risk is low.
- **Fix:** Pass paths via environment variables to the subprocess instead of embedding in the script body; or assert that the path contains none of `" % ^ &`.

### 🟡 Python installer downloaded from python.org without SHA-256
- **File/lines:** [src/engine_installer.py:33-36](../src/engine_installer.py#L33-L36)
- **Issue:** HTTPS prevents opportunistic tampering, but no hash compare. python.org publishes hashes; we should use them.
- **Fix:** Verify against python.org's published SHA-256 before launching the installer.

### 🟡 Choco install in CI not version-pinned
- **File/lines:** [.github/workflows/build-launcher.yml:89](../.github/workflows/build-launcher.yml#L89) — `choco install innosetup --yes`.
- **Fix:** Pin a version or add a post-install version assertion.

### 🟢 Update marker location (TOCTOU)
- **File/lines:** [src/auto_updater.py:30,35-36](../src/auto_updater.py#L30)
- **Observation:** `UPDATE_DIR = Path(tempfile.gettempdir()) / "audiobookmaker-update"` could be world-writable, but SHA-256 verification before execution ([src/auto_updater.py:252-258](../src/auto_updater.py#L252-L258), [src/auto_updater.py:295-302](../src/auto_updater.py#L295-L302)) neutralises the risk. Consider moving to `Path.home() / ".audiobookmaker" / "update"` for defense in depth, consistent with the pending marker.

### 🟢 Subprocess, deserialization, path handling all clean
- **Observation:**
  - No `shell=True`, `os.system`, `eval`, `exec`, `pickle.load`, `yaml.load` (all YAML via `yaml.safe_load`).
  - All subprocess args are list-form.
  - Path traversal: input paths come from `tkinter.filedialog` (absolute, user-chosen) and outputs are derived next to inputs; no user-controlled path components in `os.path.join`.
  - Config loading ([src/app_config.py:79-92](../src/app_config.py#L79-L92)) catches `OSError` + `JSONDecodeError`, validates dataclass fields and types.
  - Auto-updater mandates SHA-256 before install; verified both from release-notes block and sidecar `.exe.sha256`; CI guards the release notes ([build-release.yml:148-182](../.github/workflows/build-release.yml#L148-L182)).

---

## 5. Efficiency & Performance

**Summary: 3 critical, 4 warnings, 3 suggestions.**

### 🔴 `combine_audio_files` accumulates the entire book in RAM
- **File/lines:** [src/tts_audio.py:102-111](../src/tts_audio.py#L102-L111)
- **Issue:** `combined += segment` in pydub is an immutable operation: each `+=` allocates a new `AudioSegment` holding **all prior PCM data** plus the new chunk. For a 1000-chunk audiobook (~8h at 30s/chunk), peak working set grows to gigabytes; Python GC thrashes and OOM becomes possible on 8GB machines.
- **Fix:** Skip pydub for final concat. Write each chunk to a temporary WAV, then call `ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp3` (or `-c:a libmp3lame -b:a 128k` if the chunks are WAV). Zero extra RAM, faster, and deterministic.

### 🔴 Same pattern in Chatterbox chapter assembly
- **File/lines:** [scripts/generate_chatterbox_audiobook.py:930-942](../scripts/generate_chatterbox_audiobook.py#L930-L942) (per-chapter), [scripts/generate_chatterbox_audiobook.py:978-991](../scripts/generate_chatterbox_audiobook.py#L978-L991) (full-book).
- **Issue:** Two additional in-memory `AudioSegment` accumulations per run. A long chapter alone can peak several GB.
- **Fix:** Same ffmpeg concat demuxer approach; reuse a helper.

### 🔴 Inline `re.sub` in Finnish final-cleanup pass
- **File/lines:** [src/tts_normalizer_fi.py:929-930](../src/tts_normalizer_fi.py#L929-L930)
- **Issue:** Two `re.sub(r"...", ...)` calls compile fresh on every chunk. On a full audiobook that's tens of thousands of recompilations for patterns as trivial as `[ \t]+`.
- **Fix:** Module-level `_FI_WHITESPACE_RE` and `_FI_PUNCT_RE`, then `.sub()` on the compiled objects. Same micro-optimization already applied in English (see `_EN_MULTI_WS_RE`, `_EN_MULTI_NL_RE`).

### 🟡 Chatterbox `--chunk-chars` not exposed in GUI
- **File/lines:** [scripts/generate_chatterbox_audiobook.py:244](../scripts/generate_chatterbox_audiobook.py#L244) (CLI default 300), [src/gui_synth_mixin.py](../src/gui_synth_mixin.py) (no UI control).
- **Fix:** Expose a slider/combobox on the settings panel; plumb through to subprocess args.

### 🟡 Redundant `Path.exists()` before `AudioSegment.from_file()`
- **File/lines:** [scripts/generate_chatterbox_audiobook.py:834-835,863-865,983](../scripts/generate_chatterbox_audiobook.py#L834).
- **Issue:** Every chunk pays two `stat()` calls (exists check + open). At 10k chunks that's 20k syscalls.
- **Fix:** Use `try: … except FileNotFoundError:` or pass the collected list of paths directly into the concat step.

### 🟡 Chatterbox subprocess cleanup on init failure
- **File/lines:** [src/gui_synth_mixin.py:199-225](../src/gui_synth_mixin.py#L199-L225)
- **Issue:** If construction succeeds but `start()` fails, `self._chatterbox_runner` may retain a half-started object referencing a child process.
- **Fix:** Wrap `construct + start` in try/except and explicitly terminate + null the runner on failure.

### 🟡 MP3 export bitrate not tuned for speech
- **File/lines:** [src/tts_audio.py:111](../src/tts_audio.py#L111)
- **Issue:** `combined.export(path, format="mp3")` uses pydub's default (~192 kbps). For speech, 96-128 kbps is transparent — 30-50% smaller files.
- **Fix:** `export(..., bitrate="128k")`; optionally expose as a setting.

### 🟢 Startup imports are already lazy where it matters
- **Files:** [src/main.py](../src/main.py), [src/tts_edge.py](../src/tts_edge.py), [src/tts_piper.py](../src/tts_piper.py), [src/tts_voxcpm.py](../src/tts_voxcpm.py).
- **Observation:** `edge_tts`, `piper`, `torch`, `chatterbox` are imported inside methods, not at module load. VoxCPM is conditionally imported only when not frozen ([src/gui_unified.py:59-65](../src/gui_unified.py#L59-L65)).

### 🟢 asyncio concurrency in the parallel CLI is correct
- **File/lines:** [scripts/generate_audiobook_parallel.py:69-78](../scripts/generate_audiobook_parallel.py#L69-L78).
- **Observation:** Semaphore-guarded, no shared mutable state, default 8-way concurrency sensible.

### 🟢 Most normalizer regexes are pre-compiled module-level
- **Observation:** ~47 module-level `re.compile` across normalizer modules; exceptions are listed above. No catastrophic-backtracking risks detected in the alternation patterns.

---

## 6. Test Coverage & Quality

**Summary: 1 critical, 4 warnings, 3 suggestions.**

### 🔴 No end-to-end synthesis test with a real engine
- **File/lines:** [tests/test_integration.py:126-174](../tests/test_integration.py#L126-L174).
- **Issue:** The integration test uses a `_StubEngine` and is gated by `@pytest.mark.skipif(not _FFMPEG_AVAILABLE)`. There is no coverage verifying that Edge, Piper, or VoxCPM actually produce a playable MP3. This is the single largest quality gap: a silent break in any engine's synthesis path would ship.
- **Fix:** Add an offline, no-GPU integration test using Piper (bundled, deterministic) that converts a 2-sentence PDF to MP3 and asserts duration > 0 and header is MP3. Mark as `@pytest.mark.slow` so the fast suite is unaffected.

### 🟡 Five source modules have no dedicated test
- **Files:** [src/__init__.py](../src/__init__.py), [src/launcher.py](../src/launcher.py), [src/main.py](../src/main.py), [src/single_instance.py](../src/single_instance.py), [src/gui_update_mixin.py](../src/gui_update_mixin.py).
- **Issue:** `single_instance.py` is safety-critical (mutex prevents two concurrent conversions stomping outputs). `launcher.py` is labelled legacy but still shipped.
- **Fix:** Add at least `tests/test_single_instance.py` (lock acquisition, second-instance detection). Mark `launcher.py` tests optional if it is truly deprecated.

### 🟡 Network-requiring tests are not marked
- **Files:** [tests/conftest.py](../tests/conftest.py), [tests/test_auto_updater.py](../tests/test_auto_updater.py), [tests/test_engine_installer.py](../tests/test_engine_installer.py).
- **Issue:** No `@pytest.mark.network` decorator. Each file mocks network locally, but nothing prevents a new test from silently hitting the internet.
- **Fix:** Add a conftest autouse fixture that patches `urllib.request.urlopen` / `socket.socket` to raise unless `@pytest.mark.network` is set. Also add `@pytest.mark.gpu` for future CUDA tests.

### 🟡 `test_tts_edge.py` error-path test may pass vacuously
- **File/lines:** [tests/test_tts_edge.py:87-93](../tests/test_tts_edge.py#L87-L93).
- **Issue:** Tests `synthesize()` with empty text raises, but if `edge_tts` is missing the test short-circuits without verifying the specific exception. Dependent behaviour is implicit.
- **Fix:** Use `pytest.raises(SpecificError)` and patch the `edge_tts` module so the test behaves identically with/without the package installed.

### 🟡 Mocks don't validate signatures
- **File/lines:** [tests/test_tts_engine.py:109-123](../tests/test_tts_engine.py#L109-L123).
- **Issue:** `patch("src.tts_engine._synthesize_chunk")` replaces the function with a loose `MagicMock`. If the real signature changes, tests still pass while production breaks.
- **Fix:** Prefer `autospec=True` on `patch()` so signature drift trips tests.

### 🟢 GUI tests use sophisticated per-test state reset
- **File/lines:** [tests/test_gui_e2e.py:28-100](../tests/test_gui_e2e.py#L28-L100).
- **Observation:** Module-scoped Tk instance with per-test reset is a good pattern for the 934-line suite — well done.

### 🟢 Pytest timeout guards hangs
- **File/lines:** [pytest.ini:9](../pytest.ini#L9) (`timeout = 60, timeout_method = thread`).
- **Observation:** Threaded timeouts work on Windows where signal.alarm doesn't. Good.

### 🟢 Fixtures use `tmp_path` correctly
- **Observation:** No tests write to real user dirs. System-check tests intentionally hit real `subprocess` for ffmpeg/python/disk — this is correct.

---

## 7. CI/CD & Build

**Summary: 1 critical, 4 warnings, 3 suggestions.**

### 🔴 `setup.iss` has hardcoded version `1.0.0` — drifts from `APP_VERSION`
- **File/lines:** [installer/setup.iss:35,70,225](../installer/setup.iss#L35) vs. [src/auto_updater.py:27](../src/auto_updater.py#L27) (`APP_VERSION = "3.7.1"`).
- **Issue:** [build-release.yml:81](../.github/workflows/build-release.yml#L81) rewrites setup.iss at build time, so CI releases are correct. But any local build, or any CI job variant that doesn't hit the rewrite step, emits an installer branded `1.0.0`. That would corrupt the upgrade graph — auto-update is existential (see `docs/CONVENTIONS.md` + `feedback_autoupdate_is_existential.md`).
- **Fix:** Convert to `#define MyAppVersion` like [installer/launcher.iss](../installer/launcher.iss). Add a CI assertion step `python -c "assert auto_updater.APP_VERSION == inno_version"` before artifacts are signed.

### 🟡 Launcher CI uses unpinned ffmpeg `latest`
- **File/lines:** [.github/workflows/build-launcher.yml:43-48](../.github/workflows/build-launcher.yml#L43-L48).
- **Issue:** See security finding. Also a reproducibility issue — launcher and main app can drift onto different ffmpeg builds.
- **Fix:** Pin to the same `FFMPEG_RELEASE` + SHA-256 as `build-release.yml`.

### 🟡 Runner images use `-latest` labels
- **File/lines:** [build-release.yml:13](../.github/workflows/build-release.yml#L13), [build-launcher.yml:20](../.github/workflows/build-launcher.yml#L20), [monitor-ci.yml:24](../.github/workflows/monitor-ci.yml#L24).
- **Fix:** Pin to `windows-2022`, `ubuntu-24.04`. Bump deliberately.

### 🟡 Hardcoded Chatterbox venv path `C:\AudiobookMaker\.venv-chatterbox`
- **Files/lines:** [installer/launcher.iss:176](../installer/launcher.iss#L176), [src/engine_installer.py:63](../src/engine_installer.py#L63), [src/launcher_bridge.py:503-506](../src/launcher_bridge.py#L503-L506).
- **Issue:** Users with system drive other than C: can't find the venv.
- **Fix:** Enumerate drives dynamically (`string.ascii_uppercase`); store the discovered path in the user config for subsequent runs.

### 🟡 `cleanup.py` hardcodes a developer-specific path
- **File/lines:** [src/cleanup.py:74-76](../src/cleanup.py#L74-L76) contains `Path("D:/koodaamista/AudiobookMakerApp")`.
- **Fix:** Remove the dev-only candidate; `%LOCALAPPDATA%` + `C:/AudiobookMaker` + `D:/AudiobookMaker` covers real users.

### 🟢 PyInstaller spec bundles all current `src/*.py`
- **File/lines:** [audiobookmaker.spec:116-130](../audiobookmaker.spec#L116-L130). ffmpeg bundled at [audiobookmaker.spec:94-98](../audiobookmaker.spec#L94-L98). No stale references detected.
- **Suggestion:** Add a post-build verification that every datas/binaries source path still exists; today a missing file would ship and fail at runtime.

### 🟢 Release workflow (tag → build → SHA-256 → publish → re-verify) is excellent
- **File/lines:** [.github/workflows/build-release.yml:1-207](../.github/workflows/build-release.yml).
- **Observation:** SHA-256 sidecar, post-publish verification, release-notes check are model behaviour. Do not regress.

### 🟢 `requirements.txt` otherwise fully pinned
- **File/lines:** [requirements.txt](../requirements.txt) — all packages (except Pillow, noted in §4) are `==`-pinned. Good.

---

## 8. Documentation Accuracy

**Summary: 0 critical, 3 warnings, 2 suggestions.**

### 🟡 TODO.md backlog contains items that appear already done
- **File/lines:** [TODO.md:91-94](../TODO.md#L91-L94) "Add an application icon (assets/icon.ico)" — file exists at [assets/](../assets/). Similar for "Test the .exe against multiple PDF files" (ongoing QA, unclear completion bar).
- **Fix:** Sweep TODO.md. Move completed items to a `DONE.md` or simply delete. Per CLAUDE.md, TODO.md is the single source of truth, so drift is costly.

### 🟡 README.md language claims need verification
- **File/lines:** [README.md:15-16](../README.md#L15-L16) — claims "English, German, Swedish, French, and Spanish are also supported".
- **Issue:** Edge-TTS supports many languages; Piper only a subset; Chatterbox-Finnish only Finnish (though a Route-B English recipe exists per memory). The per-engine reality is more nuanced than the header suggests.
- **Fix:** Clarify a per-engine support matrix in README.

### 🟡 `docs/plans/english_normalizer_plan.md` §3 missing Pass R
- Cross-listed from §3 (Normalizer). Update the plan table to match the code.

### 🟢 `CLAUDE.md`, `docs/CONVENTIONS.md`, `docs/ARCHITECTURE.md` are accurate
- **Observation:** All three match current repo structure, workflows, and architecture. No correction needed.

### 🟢 `BUILDING.md` overlap with CI
- **File/lines:** [BUILDING.md:83-89](../BUILDING.md#L83-L89) walks through manual ffmpeg download; CI does it automatically.
- **Fix:** Add a one-line note that this step is only needed for local builds; CI handles ffmpeg.

### 🟢 No `TODO`/`FIXME`/`HACK`/`XXX` comments found in `src/` / `scripts/`
- **Observation:** Clean. Bookkeeping lives in TODO.md as intended.

---

## 9. Configuration & Portability

**Summary: 0 critical, 2 warnings, 3 suggestions.**

### 🟡 Drive-letter enumeration limited to C:, D:
- Cross-listed from §7. [src/cleanup.py:74-76](../src/cleanup.py#L74-L76), [src/launcher_bridge.py:505-506](../src/launcher_bridge.py#L505-L506).

### 🟡 Some docstrings / comments mix Finnish and English
- **File/lines:** [src/tts_normalizer_fi.py:301](../src/tts_normalizer_fi.py#L301) and scattered Finnish-labelled passes in English-docstring modules.
- **Fix:** Per CLAUDE.md + `feedback_english_in_communication.md`, code comments and docstrings should use English GUI label names. Finnish is fine only for in-app user strings.

### 🟢 `ffmpeg_path.py` handles all resolution cases
- **File/lines:** [src/ffmpeg_path.py:1-131](../src/ffmpeg_path.py#L1-L131). Bundle root, exe dir, dev `dist/ffmpeg/`, parent-of-src, system PATH, and Windows-only `CREATE_NO_WINDOW` monkeypatch for pydub.
- **Observation:** Model implementation. No change needed.

### 🟢 Platform checks gate Windows-only code correctly
- **Files/lines:** [src/auto_updater.py:543](../src/auto_updater.py#L543), [src/cleanup.py:344](../src/cleanup.py#L344), [src/launcher_bridge.py:501](../src/launcher_bridge.py#L501), [src/ffmpeg_path.py:129](../src/ffmpeg_path.py#L129). All use `sys.platform == "win32"` guards. If the app ever targets macOS/Linux, the branches are ready.

### 🟢 Path separators use `Path` / `os.path.join` consistently
- **Observation:** No hardcoded `\\` / `/` in logic (only in example comments). `src/cleanup.py` uses `Path(local) / "Programs" / "AudiobookMaker"` idiomatically.

### 🟢 User-facing strings centralised via `_STRINGS`
- **Files/lines:** [src/gui_unified.py](../src/gui_unified.py) inline table, [src/launcher.py:70-120](../src/launcher.py#L70-L120), [src/voice_recorder.py:48-99](../src/voice_recorder.py#L48-L99), [src/gui_engine_dialog.py:29-82](../src/gui_engine_dialog.py#L29-L82).
- **Observation:** No orphan Finnish strings in GUI code. `print()` / `logging` use English (developer-facing), which is correct.

---

## Executive Summary — Top 10 Impactful Findings

Ranked by a combination of blast radius, user-visible impact, and remediation leverage.

1. **🔴 Auto-updater version drift in `setup.iss` (§7)** — [installer/setup.iss:35](../installer/setup.iss#L35) hardcodes `1.0.0`. Any build path that skips the CI rewrite ships a mis-branded installer, corrupting the upgrade graph. Auto-update is existential; this is the highest-priority fix.
2. **🔴 `combine_audio_files` accumulates the whole book in RAM (§5)** — [src/tts_audio.py:102-111](../src/tts_audio.py#L102-L111). Causes gigabyte memory spikes and OOM on long books. Switch to ffmpeg concat demuxer.
3. **🔴 Same in-memory accumulation in Chatterbox chapter + full-book assembly (§5)** — [scripts/generate_chatterbox_audiobook.py:930-942,978-991](../scripts/generate_chatterbox_audiobook.py#L930).
4. **🔴 Launcher CI pulls ffmpeg `latest` without SHA-256 (§4, §7)** — [.github/workflows/build-launcher.yml:43-48](../.github/workflows/build-launcher.yml#L43-L48). Supply-chain risk and reproducibility gap; `build-release.yml` already has the correct pattern to mirror.
5. **🔴 No end-to-end synthesis test against a real engine (§6)** — [tests/test_integration.py:126-174](../tests/test_integration.py#L126-L174). Silent synthesis regressions would ship. Piper can anchor an offline, no-GPU E2E test.
6. **🔴 `UnifiedApp` god-object with business logic inside GUI (§1)** — [src/gui_unified.py:445-2916](../src/gui_unified.py#L445). Extract UI builders + create `synthesis_orchestrator.py`. Unlocks further refactoring and testability.
7. **🔴 Finnish final-cleanup regex recompiled per chunk (§3, §5)** — [src/tts_normalizer_fi.py:929-930](../src/tts_normalizer_fi.py#L929-L930). Trivial one-line fix; saves tens of thousands of recompilations per audiobook.
8. **🟡 Engine registry coupling (§1)** — adding a new engine requires imports in three files plus metadata scattered across `engine_installer.py` / `duration_estimate.py` / GUI `== "chatterbox_fi"` branches. Consolidate into `engine_registry.py` + `TTSEngine` metadata.
9. **🟡 Finnish normalizer passes B/D/E/F/J/K/L/M/N have no standalone unit tests (§3)** — regressions will be hard to localize. Mirror the English pass-by-pass `TestPass<LETTER>` pattern.
10. **🟡 Implicit pass-ordering dependencies are undocumented (§3)** — the Finnish M → D → F → G chain produces silently-wrong case inflection if reordered. Add a "Pass ordering invariants" section to each normalizer's main docstring.

**Not listed but worth keeping close**: TODO.md drift (done items still in backlog), `Pillow>=10.0.0` unpinned, hardcoded drive letters (C:/D: only), the BAT-script f-string substitution in `auto_updater.py`, and docstring coverage < 35% in `gui.py` / `voice_recorder.py` / `launcher.py`.
