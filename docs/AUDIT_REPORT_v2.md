# Audit Report v2

Generated 2026-04-16. Branch `audit-v3-and-followups` off master at `0873bc5`. Suite at audit time: **1001 passed, 29 skipped**.

Four parallel read-only Explore agents covered four lenses: sequencing, modularity, security, test quality. This report is the synthesis. Severity: 🔴 real bug / exploitable / data loss · 🟡 should fix soon · 🟢 nit / informational.

---

## 1. Sequencing & Initialization

### 1.1 Engine list populated twice on startup
- 🟡 `src/gui_unified.py:796` (now removed in commit `e8ed8bf`)
- During widget construction `_populate_engine_list()` ran with default Language "Suomi"; `_apply_loaded_config()` then resolved the saved Language and called `_populate_engine_list()` again. Net effect was harmless re-work, but cargo-cult risk for future widgets.
- **Fixed in this branch.** Engine combobox now stays empty until config is applied.

### 1.2 Defensive try/except in language-change callback
- 🟡 `src/gui_unified.py:1496-1499`
- `_on_language_changed` calls `self._save_current_config()` inside a bare `try/except: pass` because the callback fires during `_lang_cb.set()` while widgets are still being built. Symptom of timing fragility: callbacks registered before all widgets exist.
- **Fix path:** delay callback wiring until after `_apply_loaded_config()`, OR use `_lang_cb.configure(command=...)` AFTER the initial `.set()` call.
- Out-of-scope this round; documented for the followup pass.

### 1.3 Update banner polling can fire before window is realized
- 🟢 `src/gui_unified.py:494-499`
- Update worker thread starts inside `__init__` while widgets are still being constructed. Race is theoretical because the network round-trip is slower than the rest of `__init__`, but moving the schedule to after the window's `<Map>` event would be cleaner.

### 1.4 Pre-commit retry-on-failure
- 🟢 `scripts/pre-commit:40-50`
- Tests are re-run once if the first run fails. Documented as deliberate workaround for Windows asyncio ProactorEventLoop teardown flakes. Leave as-is.

---

## 2. Modularity & Layering

### 2.1 No backward import edges
- 🟢 Pipeline `Parser → Normalizer → Chunker → Engine → Audio` is one-way. Engines import `tts_engine` only as a utility module, not for orchestration. Clean.

### 2.2 Mixin Protocols match actual usage
- 🟢 `_SynthHost` and `_UpdateHost` Protocols accurately enumerate what the mixins read off `self`. No drift.

### 2.3 Plugin contract intact
- 🟢 Every concrete `TTSEngine` subclass (Edge, Piper, VoxCPM) implements all abstract methods with consistent signatures. `supported_languages()` overrides are uniform.

### 2.4 Chatterbox special-casing in `gui_unified.py`
- 🟡 ~39 occurrences of `"chatterbox_fi"` string check. Justified today (subprocess-only design, separate venv). Will become removable noise if Chatterbox ever joins the registry as a Voice.
- **Effort:** L. Defer until somebody refactors Chatterbox to fit the Voice contract.

### 2.5 `engine_installer.py` mixes Python install + pip + GPU + venv
- 🟡 793 LoC, several concerns under one roof.
- **Suggested split:** abstract `Installer` base + `Python311Installer` / `ChatterboxInstaller` / `PipPackageInstaller` subclasses.
- **Effort:** L. Low priority — code works.

### 2.6 `tts_engine.py` re-exports private helpers
- 🟡 Re-exports of internal symbols like `_expand_abbreviations`, `_fi_detect_case`, `_split_sentences` exist purely so old test files can import them from the legacy path. Real callers don't use them.
- **Suggested action:** prune unused re-exports; have tests import directly from `tts_normalizer_fi` / `tts_chunking`.
- **Effort:** M.

### 2.7 No god-files needing decomposition
- 🟢 `gui_unified.py` (3159 LoC) is large but stabilized — SynthMixin and UpdateMixin already extracted. `tts_normalizer_fi.py` (931 LoC) and `tts_normalizer_en.py` (852 LoC) are cohesive single-responsibility files; the English normalizer already extracted Pass O/P/R/S into `_en_pass_*` modules, which is the right pattern.

### 2.8 No dead code, no shadowed duplicates
- 🟢 Audit found nothing. The earlier `_start_inprocess_engine` / `_run_inprocess` duplicates are gone (commit `60e2a4f`).

---

## 3. Security

**Headline finding: no exploitable vulnerabilities.** Codebase demonstrates mature security practice for a single-user Windows desktop app.

### 3.1 Auto-updater
- 🟢 SHA-256 verification mandatory before download. HTTPS-only to GitHub Releases. Pending marker is JSON, no untrusted deserialization. Installer relaunch uses list-form subprocess with quoted arguments.

### 3.2 No `shell=True` anywhere
- 🟢 All ~40 subprocess invocations use list form. Malicious filenames cannot inject shell metacharacters. Verified with grep.

### 3.3 Path handling
- 🟢 Output paths resolved via `Path.resolve()` before mkdir/write. No string concatenation of user input into paths.

### 3.4 PENDING_MARKER tampering by other local users
- 🟡 `src/auto_updater.py:241-295` writes `audiobookmaker_update_pending.json` to system-wide temp dir. A second local user could write a fake marker. Worst-case impact: false-positive "update failed" dialog (the marker only triggers a re-download of a hash-verified installer; no RCE).
- **Fix:** move marker to per-user `~/.audiobookmaker/`.
- **Effort:** S.

### 3.5 Soft-pinned dependencies
- 🟡 `requirements.txt` has loose pins for `PyYAML>=6.0`, `ebooklib>=0.18`, `beautifulsoup4>=4.12`. For a bundled desktop app this is acceptable but brittle.
- **Fix:** pin to exact versions.
- **Effort:** S (~20 min).

### 3.6 PyMuPDF version
- 🟡 v1.24.5 is recent and patched. Set quarterly reminder to re-check upstream CVE feed.

### 3.7 CI pipeline
- 🟢 Triggers on `v*` tag push only — no `pull_request_target`, no fork-injection risk. ffmpeg download SHA-256-verified. No secrets exposed in unsafe contexts.

### 3.8 No code signing on installer
- 🟢 Documented. SmartScreen warning expected. Defer until certificate funding exists.

---

## 4. Test Quality

**1001 passing tests, but the audit found three structural gaps.**

### 4.1 Critical coverage holes
- 🔴 `src/tts_normalizer.py` (the language dispatcher) — **0 tests**. Routing is the new joint between Finnish and English; if it picks the wrong normalizer, every Chatterbox audiobook silently produces garbage.
  - **Effort:** S. ~5 tests covering: dispatch by language code, fallback for unknown language, unicode language code, empty text, behaviour identical to direct fi/en normalizer call.
- 🔴 `src/tts_audio.py` — only **2 tests**. Most file-handling failure modes (corrupt MP3, full disk, permission denied) untested.
  - **Effort:** M. ~6 new tests covering corruption, missing input, permission errors.
- 🔴 `src/gui_synth_mixin.py` (355 LoC) — **0 unit tests**. Cancellation, progress callback, language-switch-mid-run, subprocess exit codes all untested. Currently covered only via `test_gui_e2e.py` which exercises happy paths.
  - **Effort:** L. ~10 new tests. Some require mocking the engine + the event queue.
- 🟡 `src/gui_unified.py` validation logic — only e2e coverage. Direct unit tests of `_on_convert_click` / `_on_sample_click` validation paths would be cheaper to maintain.
  - **Effort:** M.

### 4.2 Tautological tests to rewrite (concrete examples)
- `tests/test_sample_helpers.py:14-16` — `assert extract_sample_text("Hei maailma.") == "Hei maailma."` — pure identity test. Should assert sentence-integrity behaviour instead.
- `tests/test_sample_helpers.py:62-63` — `assert DEFAULT_SAMPLE_CHARS == 500` — tests a constant assignment, no behaviour.
- `tests/test_ffmpeg_and_engines.py:600-603` — Finnish normalizer passthrough on already-normal text.
- `tests/test_auto_updater.py:58-71` — verifies the dataclass stores what was passed in.
- `tests/test_tts_audio.py:31-33` — `pytest.raises(ValueError)` on empty list with no message check.

### 4.3 Missing behavioural test cases (top 10 of 23)
1. PDF with 0 chapters shows a sensible error, not a traceback.
2. Click Convert twice in quick succession — second click is no-op.
3. User changes Language while synthesis is running — synthesis completes in old Language, new Language stored for next run.
4. Edge-TTS network failure mid-chunk surfaces as a user-facing error, not silent file truncation.
5. Chatterbox subprocess exits with code != 0 → error logged + UI re-armed.
6. Auto-updater downloaded file with mismatched SHA-256 is deleted, error raised, no install attempted.
7. Whitespace-only / emoji-only / mixed-language input doesn't crash the chunker.
8. Sample mode actually produces 10-60 second audio (ffprobe duration check).
9. Two `AudiobookMaker.exe` instances starting simultaneously — single-instance guard wins exactly one.
10. Saved config round-trips: write every UserConfig field, reload, verify all fields match.

### 4.4 Parametrize the normalizer test files
- `tests/test_tts_normalizer_fi.py` (135 tests) and `tests/test_tts_normalizer_en.py` (142 tests) are mostly copy-paste case methods. Converting to `@pytest.mark.parametrize(...)` per-pass would shrink the file ~40% and make adding cases trivially cheap.
- **Effort:** M.

### 4.5 Test infrastructure issues
- 🟡 `tests/test_gui_e2e.py` `_shared_app` fixture is `scope="module"` — every test inherits the previous test's app state. Cross-contamination has not bitten yet but will once tests start mutating settings/engine selection.
- 🟡 `tests/test_integration.py` uses `tempfile.gettempdir()` + manual `unlink` — should use `tmp_path` like the rest.

---

## Top-10 prioritized action list

Ranked by `(impact / effort)`:

| # | Action | Severity | Effort | Bucket |
|---|--------|----------|--------|--------|
| 1 | **Add tests for `tts_normalizer.py` dispatcher** | 🔴 | S | Test-quality |
| 2 | **Add failure-path tests for `tts_audio.py`** (corrupt MP3, disk full, permissions) | 🔴 | M | Test-quality |
| 3 | **Move PENDING_MARKER to `~/.audiobookmaker/`** | 🟡 | S | Security |
| 4 | **Pin `PyYAML`, `ebooklib`, `beautifulsoup4` exactly in `requirements.txt`** | 🟡 | S | Security |
| 5 | **Decouple `_on_language_changed` from init-time fragility** (drop the bare `try/except: pass`) | 🟡 | S | Sequencing |
| 6 | **Behavioural-test the synth orchestration** (cancellation, language switch, progress) — 5–10 unit tests for `gui_synth_mixin.py` | 🔴 | L | Test-quality |
| 7 | **Parametrize `test_tts_normalizer_fi.py` + `test_tts_normalizer_en.py`** | 🟡 | M | Test-quality |
| 8 | **Add 5–10 unit tests for `gui_unified.py` validation paths** (no PDF, no text, both wrong) — direct, not e2e | 🟡 | M | Test-quality |
| 9 | **Per-test app instance OR explicit reset in `test_gui_e2e.py`** to remove cross-contamination risk | 🟡 | M | Test-infra |
| 10 | **Prune unused private re-exports from `tts_engine.py`**; update tests to import from the real module | 🟡 | M | Modularity |

---

## What landed in this branch

- ✅ Commit `e8ed8bf` — drop redundant `_populate_engine_list()` call during engine bar build (item 1.1).

## Followups not in scope this branch

- Items 1–10 above. Suggest tackling 1–4 as a quick batch (all 🟡/🔴 small to medium), then 5–8 as a separate "test-coverage push" branch.

## Followups deferred from earlier audit (still open)

- Rallienglanti-mode preset (TODO backlog).
- Phase 2 follow-ups noted in the prior `docs/AUDIT_REPORT.md`.
