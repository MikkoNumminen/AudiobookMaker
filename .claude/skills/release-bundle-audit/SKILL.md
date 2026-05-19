---
name: release-bundle-audit
description: Audit the AudiobookMaker PyInstaller release bundle for unused dependencies, dead-code data files, and accidental ML-stack pollution; then propose spec-only fixes on a `chore/release-bundle-size` branch. Use whenever the user says "the installer is huge", "why is the bundle so big", "shake out unused deps before release", "shrink the .exe", "audit the .spec", or asks why a frozen build ships some specific package. Encodes the empirical finding that ~28% of the v3.11.x bundle was dead-code data files (ffplay.exe, AVIF support, Arabic phonemizer, ONNX training tools) plus a defense-in-depth exclude set that prevents future regressions when a contributor accidentally pip-installs torch on the build machine.
---

# release-bundle-audit

Audit `audiobookmaker.spec` and `audiobookmaker_launcher.spec` for unused
dependency trees, dead-code data files, and missing defense-in-depth
excludes. Apply spec-only fixes on a `chore/release-bundle-size` branch.

## Why this skill exists

Installers above ~200 MB get skipped by users on slow connections, which
strands them on a broken release. Bundles accrete dead weight silently:
ffplay.exe surviving a Listen-button rewrite to pygame (~195 MB), the
full Pillow ImagePlugin set including `_avif`/`_webp` (~10 MB) when the
app only renders PNG and ICO, Piper's Arabic/Chinese phonemizers, the
ONNX training toolchains. The 2026-05-11 audit cut **786 MB → 568 MB
(−27.7%)** with `.spec`-only edits.

## When to invoke

"Why is the installer so big?" / "audit the .spec files" / "shrink the
.exe" / "is `<package>` actually needed at runtime?" — and routinely
before any release tagged on a new minor version, since new bloat
tends to land in minor bumps.

Do not invoke for: ffmpeg.exe size complaints (CI-workflow change, not
.spec), auto-update SHA-256 issues (see
[release-cut](../release-cut/SKILL.md)), or splash-speed UX work.

## Constraints (hard rules)

- **Spec-only.** No code changes outside the two `.spec` files. If a
  missing `sys.frozen` guard would land a feature in frozen that
  shouldn't be there, surface it — do not fix here.
- **One commit per spec file**, never squashed (revert granularity).
- **Conventional Commits:** `build(spec): …` — not `chore`, since the
  change rebuilds packaging output.
- **Branch name:** `chore/release-bundle-size`. Suffix `-2`, `-rc2` if
  the branch is already on origin.

## Workflow

### Phase 0 — static spec inventory (report-only, no edits)

**Fan out by default.** Dispatch one `Agent` call with
`subagent_type: "Explore"` per spec file (`audiobookmaker.spec` and
`audiobookmaker_launcher.spec`) in a single message. Each agent
enumerates the same checklist below for its assigned spec
independently. Merge the per-spec tables into one Phase-0 report
before stopping for the user. If either agent fails, re-run that
spec serially rather than reporting a half-table.

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

**Fan out by default.** Dispatch parallel `Agent` calls
(`subagent_type: "Explore"`) by *shared reachability tree* so two
agents don't read the same files:

- `{torch, transformers, chatterbox, faster-whisper, pyannote}` —
  one bundle (in this repo, `src/voice_pack/asr.py:74` imports torch
  for a CUDA probe, pulling faster-whisper into the torch tree).
- `onnxruntime`, `piper`, `PIL` — one bundle each (distinct DLL
  trees).
- catch-all — remaining engines (`espeak`, `kokoro`, etc.).

Each agent traces reachability for its assigned packages from
`src/main.py` under `sys.frozen is True` and reports one row of the
reachability table. Merge before stopping. Re-run failing bundles
serially. Phase 3 (build + smoke test) is strictly sequential.

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
rm -rf dist/AudiobookMaker build/audiobookmaker
python -m PyInstaller --clean --noconfirm audiobookmaker.spec
du -sh dist/AudiobookMaker
```

PyInstaller takes 5–15 minutes per build on Windows. Run in background
and watch for `dist/AudiobookMaker/AudiobookMaker.exe` to appear.

Then run the full smoke test (all steps required to pass):

1. Launch `AudiobookMaker.exe` — main window renders, no error popup.
2. Load any PDF; `.local/` (gitignored) usually has dev samples.
3. Edge-TTS + Finnish voice (Noora) + default speed → click Listen,
   audio plays.
4. Convert PDF → MP3 to a temp dir.
5. Repeat 3–4 with Piper.
6. Engine dropdown shows no VoxCPM2 or Chatterbox cloning (Chatterbox
   SYNTHESIS is OK — post-install venv engine).
7. Quit cleanly via window close.

A 10-second alive check is *not* a smoke test substitute. Be explicit
in the PR description about ran vs needs-a-human. If any step fails,
revert the last exclude and re-test.

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
- **`_drop_path` is duplicated in both `audiobookmaker.spec` and
  `audiobookmaker_launcher.spec`.** PyInstaller `.spec` files have no
  clean way to share helpers (you would have to add a sibling
  `spec_helpers.py` and play `pathex` tricks). When you fix a bug in
  one copy or add a needle, mirror the change to the other spec in the
  same audit — the two filters drift silently otherwise.
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

The audit is expensive — two full PyInstaller builds, 15–20 minutes —
so skip it when the bundle inputs haven't moved.

The bundle inputs are: `requirements.txt`, anything under `src/`,
anything under `scripts/`, and the two `.spec` files. The reference
point is the last bundle-audit merge on master — find it with:

```bash
LAST_AUDIT=$(git log --grep='build(spec).*bundle' \
                     --format='%H' -n 1 origin/master)
```

(Falls back to the commit that introduced this skill if no prior trim
exists yet — `git log --grep='feat(skills): add release-bundle-audit'
--format='%H' -n 1 origin/master`.)

Then:

```bash
git log --oneline "${LAST_AUDIT}..origin/master" -- \
    requirements.txt 'src/**/*.py' 'scripts/**/*.py' 'audiobookmaker*.spec'
```

If the output is empty, **do not re-run** the audit. Tell the user the
bundle inputs haven't changed since `<short SHA of LAST_AUDIT>` and
point them at the prior PR / docs entry. Do not anchor against
`master..HEAD` — when this skill is invoked from `master`, that range
is empty by construction and the check trivially passes "no changes"
even when an audit is actually warranted.
