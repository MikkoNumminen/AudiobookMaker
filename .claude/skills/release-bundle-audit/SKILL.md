---
name: release-bundle-audit
description: Audit the AudiobookMaker PyInstaller release bundle for unused dependencies, dead-code data files, and accidental ML-stack pollution; then propose spec-only fixes on a `chore/release-bundle-size` branch. Use whenever the user says "the installer is huge", "why is the bundle so big", "shake out unused deps before release", "shrink the .exe", "audit the .spec", or asks why a frozen build ships some specific package. Encodes the empirical finding that ~28% of the v3.11.x bundle was dead-code data files (ffplay.exe, AVIF support, Arabic phonemizer, ONNX training tools) plus a defense-in-depth exclude set that prevents future regressions when a contributor accidentally pip-installs torch on the build machine.
---

# release-bundle-audit

Audit `audiobookmaker.spec` and `audiobookmaker_launcher.spec` for unused
dependency trees, dead-code data files, and missing defense-in-depth
excludes. Apply spec-only fixes on a `chore/release-bundle-size` branch.

## Why this skill exists

Auto-update has to actually fit inside the user's patience. The
installer is the lifeline (see [release-cut](../release-cut/SKILL.md));
a 200 MB installer that takes 10 minutes to download will be skipped,
which means existing users stay on a broken version forever.

Over time the bundle accreted dead weight that nobody noticed because
the build "worked":

- A `_find_ffplay()` helper got merged with a Listen-button design that
  later switched to pygame. The helper survived as dead code; ffplay.exe
  (~195 MB raw) kept shipping for every release.
- Pillow's PyInstaller hook bundled ~46 ImagePlugin modules and their
  native sidecar DLLs — including `_avif.cp311-win_amd64.pyd` (7.6 MB) —
  even though the app only loads PNG (icons) and ICO (window icon).
- Piper shipped `tashkeel/` (4.6 MB Arabic diacritizer) and a Chinese
  phonemizer the engine never invokes.
- `onnxruntime/transformers|tools|quantization|backend|datasets` —
  training-time toolchains that Piper's inference path never touches.
- `ctranslate2/cudnn64_9.dll` had a hand-filter in the spec from a past
  incident, but the *rest* of ctranslate2 would have shipped if it ever
  landed in site-packages — there was no top-level exclude.

The 2026-05-11 audit cut the uncompressed bundle 786 MB → 568 MB
(−218 MB, −27.7%) with **only** `.spec` file edits.

## When to invoke

- "Why is the installer so big?"
- "Audit the .spec files"
- "Shrink the .exe / installer"
- "Shake out unused deps before release"
- "Is `<package>` actually needed at runtime?"
- After a contributor adds a new engine, GUI feature, or post-install
  venv — the reachability set may have shifted.
- Before any release tagged on a new minor version. Patch releases
  inherit prior audit findings; minor releases are where new accidental
  bloat tends to land.

## When NOT to invoke

- Bundle works fine, user reports a runtime crash — that's a debugging
  task, not a sizing audit.
- ffmpeg.exe binary itself is too big — that's a CI-workflow change
  (switch GPL build to LGPL, or build a custom audio-only ffmpeg), not
  a spec-only change. Flag for the user but don't try to fix here.
- Auto-update SHA-256 broken — see [release-cut](../release-cut/SKILL.md).
- "Make the splash faster" — that's a UX change, not a bundle change.

## Constraints (hard rules)

- **No code changes outside the two `.spec` files.** The audit relies on
  the existing `sys.frozen` guards as ground truth for reachability. If
  a guard is missing for a feature that should be dev-only, that's a
  separate code-change task — surface it to the user, don't fix it
  inside this skill.
- **Do not modify `sys.frozen` checks, the CI workflows, or any code
  under `src/`.** Spec-only.
- **One commit per spec file.** `audiobookmaker.spec` and
  `audiobookmaker_launcher.spec` change for the same reason but stay in
  separate commits so a revert can target just one if a regression
  surfaces. No squashing.
- **Conventional Commits.** `build(spec): …` is the right prefix (the
  change rebuilds packaging output). Avoid `chore:` — `chore(spec):`
  doesn't communicate the intent.
- **Branch name: `chore/release-bundle-size`.** Reused across audits; if
  the branch already exists on origin, append a suffix (`-2`, `-rc2`).

## Workflow

### Phase 0 — static spec inventory (report-only, no edits)

For both `audiobookmaker.spec` and `audiobookmaker_launcher.spec`,
enumerate:

- `collect_all('<package>')` — flag as highest-bloat candidates (pulls
  the entire wheel tree including tests/docs subdirs).
- `collect_submodules('<package>')` — usually fine but document so the
  reachability table has the full picture.
- `collect_data_files('<package>')` — same.
- `datas=` entries — classify each as **required-at-runtime** /
  **dev-only** / **unclear**. The most common dev-only sins:
  - Voice-cloning reference clips (`assets/voices/<voice>_reference.wav`)
    that ship for a Chatterbox SYNTHESIS feature reachable in frozen
    *or* for a Chatterbox CLONING feature gated behind a lazy import.
    Resolve by reading `src/gui_unified.py` for the actual reachability.
  - `scripts/<engine>_runner.py` files for subprocess engines.
  - `src/*.py` re-bundled into a `src/` data dir — these are usually
    required because the subprocess script can't import from the PYZ.
- `excludes=` entries — flag entries that look pre-existing vs.
  defense-in-depth and entries that are missing.
- `binaries=` entries.
- Post-Analysis filters (e.g. the existing `ctranslate2/cudnn64_9.dll`
  drop).

**Report as a table per spec. Stop and wait for the user to greenlight
Phase 1.**

### Phase 1 — build inventory (report-only)

If `dist/AudiobookMaker/` exists, run:

```bash
du -sh dist/AudiobookMaker
du -sh dist/AudiobookMaker/_internal/* | sort -rh | head -30
find dist/AudiobookMaker/_internal -maxdepth 1 -type d
du -sh dist/AudiobookMaker/_internal/PIL/* | sort -rh
du -sh dist/AudiobookMaker/_internal/onnxruntime/* | sort -rh
du -sh dist/AudiobookMaker/_internal/piper/* | sort -rh
```

If no build exists, say so and skip to Phase 2 noting the gap. **Do not
build during Phase 1** — Phase 1 is observation only. The Phase 3 build
will be the measurement point.

Report total size, top 30 size offenders, presence/absence of each
suspect package. **Stop and wait.**

### Phase 2 — reachability vs. presence (report-only)

Trace imports from `src/main.py` under the assumption `sys.frozen is
True`. For Audiobookmaker:

1. `src/main.py` → `src/app_config.py`, `src/ffmpeg_path.py`,
   `src/single_instance.py`, then `src/gui_unified.py`.
2. From `src/gui_unified.py`, identify every engine dispatch and check
   whether each engine module is imported at module-load or lazily on
   user action.
3. Find every `import torch | transformers | chatterbox | voxcpm |
   pyannote | silero_vad | safetensors | accelerate | bitsandbytes |
   peft | ctranslate2 | faster_whisper | whisper | speechbrain |
   soundfile | librosa | huggingface_hub | sklearn` under `src/`. For
   each, decide: reachable in frozen / subprocess-only / dev-only /
   sys.frozen-gated.

Cross-check against `requirements.txt` to flag packages that are
installed in the build env but never imported from reachable code.

Produce the reachability table:

| Package / dir | Size on disk | Status | Safe to exclude? | Risk if excluded |

"Safe to exclude" is **YES** only if (a) no reachable code imports it
*and* (b) it's not a known PyInstaller false negative. Otherwise
**NEEDS_TEST**.

**Stop and wait for the user to say "proceed".**

### Phase 3 — patch and verify

Branch:

```bash
git checkout master && git pull --ff-only origin master
git checkout -b chore/release-bundle-size
```

#### 3a. Apply the baseline excludes batch

The `audiobookmaker.spec` defense-in-depth exclude list that the
2026-05-11 audit settled on (paste verbatim; do not abridge):

```
# Heavy ML stack — never reaches the frozen .exe in any sanctioned build.
torch, torchaudio, torchvision, transformers, chatterbox, chatterbox_tts,
voxcpm, pyannote, pyannote.audio, pyannote.core, pyannote.metrics,
silero_vad, safetensors, accelerate, bitsandbytes, peft, ctranslate2,
faster_whisper, whisper, speechbrain, soundfile, librosa, sklearn,
scikit_learn, huggingface_hub,

# Test machinery.
pytest, pytest_asyncio, pytest_cov, pytest_timeout, _pytest, coverage,

# System-Python pollution from dev tooling.
cyclonedx, cyclonedx_python_lib, pip_audit, pip_api,
pip_requirements_parser, CacheControl, msgpack, rich, markdown_it,
mdurl, Pygments, boolean, docopt, license_expression, tabulate,
imageio_ffmpeg, psutil,

# Jupyter / notebook stack.
jupyter, jupyter_client, jupyter_core, jupyterlab, ipykernel,
ipywidgets, tornado, zmq,
```

#### 3b. PIL plugin trim (in `excludes`)

Keep **only** `BmpImagePlugin`, `JpegImagePlugin`, `PngImagePlugin`,
`IcoImagePlugin`. Exclude every other `PIL.<Name>ImagePlugin` by name.
Verify the current Pillow plugin list with:

```bash
python -c "import os, PIL; [print(f) for f in sorted(os.listdir(os.path.dirname(PIL.__file__))) if 'ImagePlugin' in f]"
```

Then construct the `PIL.<Name>` exclude list, minus the four to keep.

#### 3c. Post-Analysis path filter

Add to the `.spec` *after* the `Analysis(...)` call (or merge with the
existing `ctranslate2` filter):

```python
def _drop_path(item, *needles):
    """Match path-style (a.datas / a.binaries) AND module-name (a.pure) entries."""
    raw = item[0].lower().replace('\\', '/')
    as_path = raw.replace('.', '/')
    return any(n.lower() in raw or n.lower() in as_path for n in needles)

_unused_path_needles = (
    'piper/tashkeel/', 'piper/train/', 'piper/phonemize_chinese',
    'piper/http_server', 'piper/__main__', 'piper/download_voices',
    'onnxruntime/transformers/', 'onnxruntime/quantization/',
    'onnxruntime/tools/', 'onnxruntime/backend/', 'onnxruntime/datasets/',
)
_pil_unused_binaries = ('pil/_avif.', 'pil/_webp.', 'pil/_imagingcms.')

a.datas = [d for d in a.datas if not _drop_path(d, *_unused_path_needles)]
a.pure = [p for p in a.pure if not _drop_path(p, *_unused_path_needles)]
a.binaries = [b for b in a.binaries
              if not _drop_path(b, *_unused_path_needles)
              and not _drop_path(b, *_pil_unused_binaries)]
```

Why this filter shape: PyInstaller TOC entries use **paths** in
`a.datas` / `a.binaries` and **module names** (dot-separated) in
`a.pure`. A single needle works for both because we normalize dots → slashes.

#### 3d. ffplay decision

Search for actual callers:

```bash
rg -n "_find_ffplay\(|\.ffplay\b" src/
```

If `_find_ffplay` is defined but never called (the 2026-05-11 state),
drop `dist/ffmpeg/ffplay.exe` from `datas=`. Document the reasoning in
the spec comment block — future audits need to know why ffplay isn't
shipped, since the helper still exists in `gui_unified.py`.

If `_find_ffplay` IS called by a feature reachable in frozen, **do not
remove ffplay.exe**. Note it as NEEDS_TEST and stop.

#### 3e. ffprobe decision

ffprobe is required by pydub's `mediainfo_json()` whenever
`AudioSegment.from_file(path)` is called without a `format=` arg.
[tts_audio.py:85](../../../src/tts_audio.py) calls it without a format,
so the format is auto-detected — which uses ffprobe. **Keep ffprobe.exe
in `datas=`.** Removing it is a code-change task (pass an explicit
format to from_file), not a spec-only change.

#### 3f. Apply the same excludes + path filter to `audiobookmaker_launcher.spec`

The launcher already excludes more aggressively than the main spec (it
excludes PIL entirely because the launcher doesn't render images). Add
the missing ML stack entries to parity and the same `_drop_path`
filter. PIL plugins do not need a per-plugin exclude in the launcher
because PIL itself is already excluded.

#### 3g. Build, smoke-test, measure

```bash
# Bash (Git Bash on Windows):
rm -rf dist/AudiobookMaker build/audiobookmaker
python -m PyInstaller --clean --noconfirm audiobookmaker.spec
du -sh dist/AudiobookMaker
```

Or in PowerShell:

```powershell
Remove-Item -Recurse -Force dist/AudiobookMaker, build/audiobookmaker -ErrorAction SilentlyContinue
python -m PyInstaller --clean --noconfirm audiobookmaker.spec
"{0:N1} MB" -f ((Get-ChildItem -Recurse dist/AudiobookMaker | Measure-Object -Property Length -Sum).Sum / 1MB)
```

PyInstaller takes 5–15 minutes per build on a typical Windows dev
machine. Run in background and watch for `dist/AudiobookMaker/AudiobookMaker.exe`
to appear rather than tailing logs.

Then run the smoke test from the audit prompt, all steps required to
pass:

1. Launch `dist/AudiobookMaker/AudiobookMaker.exe` — main window
   renders, no error popup.
2. Load a small sample PDF. The repo's `.local/` directory (gitignored)
   typically contains user-supplied PDFs; if not, any PDF on disk works
   — the audit doesn't care about content, only that the parser path
   runs.
3. Select Edge-TTS, default Finnish voice (Noora), default speed.
4. Click the Listen / Preview button (label is "Esikuuntele" in Finnish
   UI, "Preview" in English) — audio plays.
5. Convert the loaded PDF to MP3, output to `./out/` (dev mode) or any
   temp dir.
6. Repeat steps 3–5 with Piper instead of Edge-TTS.
7. Confirm the engine dropdown does NOT show VoxCPM2 or Chatterbox
   cloning options (Chatterbox SYNTHESIS may still appear — that is
   the post-install venv engine, which is correct).
8. Quit cleanly via the window close button.

A 10-second alive-after-launch check is a useful sanity check, but
**does not substitute for the full smoke test**. Be explicit in the PR
description about which checks you ran vs. which need a human.

If any step fails, revert the last exclude and re-test.

#### 3h. Commit per spec, push, open PR

Two separate commits, never squashed:

```
build(spec): trim unused subtrees and add defense-in-depth excludes  (audiobookmaker.spec)
build(spec): trim launcher bundle subtrees and broaden excludes      (audiobookmaker_launcher.spec)
```

PR title format: `build(spec): trim release bundle (<before> -> <after> MB, -<X>%)`.

Body must include:

- Phase 2 reachability summary (one sentence per heavy dep, "reachable
  vs. subprocess-only vs. sys.frozen-gated").
- Before / after bundle sizes.
- What was NOT changed (ffmpeg.exe, ffprobe.exe, Chatterbox-related
  data files, etc.) and why.
- Test plan checklist (the audit-prompt smoke test, plus any
  measurement steps).

## Gotchas the audit has hit before

- **`_drop_path` filter using slash-only needles miss `a.pure`**, because
  pure-Python module entries are dot-separated module names. Normalize
  both forms (the filter above does this). Without normalization the
  drop is silent — `a.pure` keeps the modules, the .py files ship inside
  the PYZ, and the bundle size barely moves while you congratulate
  yourself.
- **pre-commit hook flake** on `test_gui_e2e.py::test_hero_tagline_…`:
  the test spawns a subprocess against `.venv-chatterbox` to probe
  installation status, and that subprocess can hang on Windows when the
  main pytest process is competing with a PyInstaller build for
  resources. Wait for any background build to finish before committing,
  and retry once if the hook fails for this specific test.
- **System Python's site-packages is the build env**, not a clean venv.
  Anything you've ever `pip install`-ed (pip_audit, cyclonedx, rich)
  becomes a potential transitive bundle source. The defense-in-depth
  exclude list is what makes the build environment-independent — never
  remove entries from it on the grounds that "we don't have that
  installed anyway".
- **The `ctranslate2/cudnn64_9.dll` filter is now belt-and-suspenders**
  because `ctranslate2` is in `excludes=`. Keep the filter — it
  defends dev-machine builds where someone has the chatterbox venv
  polluting site-packages despite the exclude.
- **`scripts/generate_chatterbox_audiobook.py` and the bundled
  `src/*.py` files in `datas=` are NOT removable.** The Chatterbox
  synthesis subprocess (separate Python interpreter from
  `.venv-chatterbox`) does `from src.tts_engine import …` and needs
  those files on disk in the frozen bundle. Leave them.

## Skipping the audit when nothing has changed

If `git log --oneline master..HEAD -- requirements.txt 'src/*.py'
'scripts/*.py' 'audiobookmaker*.spec'` shows no changes since the last
audit recorded under `docs/audits/`, **do not re-run**. Tell the user
the bundle hasn't changed and refer to the prior audit report. The
audit is expensive (two full PyInstaller builds, ~15–20 minutes); don't
burn that time for no signal.
